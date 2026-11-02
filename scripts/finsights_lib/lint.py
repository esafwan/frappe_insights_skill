"""Semantic lint for finsights specs (spec section 3: L01..L28).

L01/L03/L04 come from spec.shape_findings; everything else lives here.
`lint_spec` never raises on malformed input: objects with shape errors are skipped by the
rules that depend on them, and each rule group is guarded.
"""

from __future__ import annotations

import re

from .common import Finding
from .schema import (
	AGGREGATIONS, ALL_FILTER_OPERATORS, CHART_ALIASES, CHART_TYPES, FILTER_COLUMN_TYPES, FILTER_OPERATORS,
	FILTER_TYPE_KIND, GRANULARITIES, GRID_COLS, JOIN_TYPES, ORDER_DIRS, column_kind, output_schema,
)
from .spec import shape_findings

V2_RE = re.compile(
	r"Insights (?:Query|Chart|Dashboard)\b(?! v3)|insights\.api\.(?:queries|public|home)\b|dashboards\.create_dashboard"
)
DEVELOP_KEYS = ("number_format", "window", "layouts", "default_user_key", "visible_to_roles")
WITHIN_RE = re.compile(
	r"^(?:(?:last|next)\s+\d+\s+(?:day|week|month|quarter|year|fiscal year)s?(?:\s+\(include current\))?"
	r"|current\s+(?:day|week|month|quarter|year|fiscal year))$",
	re.I,
)
NO_CATALOG_MSG = "no catalog; column checks skipped"
SQL_STR_RE = re.compile(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`|--[^\n]*|/\*.*?\*/", re.S)


def _p(base: str, *parts) -> str:
	return base + "".join("/" + str(x) for x in parts)


def _col(e):
	if isinstance(e, str):
		return e
	if isinstance(e, dict):
		c = e.get("column")
		return c if isinstance(c, str) else None
	return None


def _list(v):
	return v if isinstance(v, list) else []


def _dicts(v):
	return [(i, x) for i, x in enumerate(_list(v)) if isinstance(x, dict)]


class _Ctx:
	def __init__(self, spec, catalog):
		self.spec = spec
		self.catalog = catalog
		self.out: list = []
		self.bad: set = set()
		self.qmap: dict = {}  # id -> query dict
		self.qidx: dict = {}
		self.cmap: dict = {}  # id -> chart dict
		self.cidx: dict = {}
		self.cyclic: set = set()

	def add(self, rule, sev, path, msg, hint=""):
		self.out.append(Finding(rule, sev, path, msg, hint))

	def is_bad(self, ptr: str) -> bool:
		return any(b == ptr or b.startswith(ptr + "/") for b in self.bad)

	def ok_obj(self, ptr: str, obj) -> bool:
		return isinstance(obj, dict) and not self.is_bad(ptr)


# ---- schema helpers ---------------------------------------------------------------------------

def _chain_ok(ctx: _Ctx, q: dict):
	"""Walk the source_query chain; return the terminal base table query or None on cycle/unresolved."""
	seen = set()
	cur = q
	while isinstance(cur, dict):
		if cur.get("sql"):
			return None
		if cur.get("table"):
			return cur if cur["table"] in ctx.catalog else None
		sq = cur.get("source_query")
		if not isinstance(sq, str) or sq in seen or sq not in ctx.qmap:
			return None
		seen.add(sq)
		cur = ctx.qmap[sq]
	return None


def safe_schema(ctx: _Ctx, qid):
	"""Final output schema of query id, or None when unknowable (no catalog, sql, cycle, unresolved)."""
	if ctx.catalog is None or qid not in ctx.qmap:
		return None
	q = ctx.qmap[qid]
	if _chain_ok(ctx, q) is None:
		return None
	try:
		return output_schema(q, ctx.catalog, ctx.qmap)
	except Exception:
		return None


def _pre_select(ctx: _Ctx, q: dict):
	"""Columns visible at the select step (source + joins + mutate), or None when unknowable."""
	if q.get("sql"):
		return None
	if q.get("table"):
		if q["table"] not in ctx.catalog:
			return None
		cols = dict(ctx.catalog[q["table"]])
	elif isinstance(q.get("source_query"), str):
		cols = safe_schema(ctx, q["source_query"])
		if cols is None:
			return None
		cols = dict(cols)
	else:
		return None
	for j in _list(q.get("joins")):
		if isinstance(j, dict):
			jt = ctx.catalog.get(j.get("table"), {})
			for c in _list(j.get("columns")):
				n = _col(c)
				if n:
					cols[n] = jt.get(n, "String")
	for m in _list(q.get("mutate")):
		if isinstance(m, dict) and isinstance(m.get("as"), str):
			cols[m["as"]] = m.get("type", "String")
	return cols


def _pruned(ctx: _Ctx, q: dict) -> dict:
	"""{col: select-entry-index} of optional select columns missing from the pre-select columns."""
	res = {}
	if ctx.catalog is None:
		return res
	pre = _pre_select(ctx, q)
	if pre is None:
		return res
	for i, e in enumerate(_list(q.get("select"))):
		if isinstance(e, dict) and e.get("optional") and isinstance(e.get("column"), str) and e["column"] not in pre:
			res[e["column"]] = i
	return res


def _chart_cols(c: dict):
	"""[(pointer-suffix parts, column)] referenced by a chart (excluding expr measures)."""
	res = []
	x = c.get("x")
	if _col(x):
		res.append((("x",), _col(x)))
	for i, y in enumerate(_list(c.get("y"))):
		if isinstance(y, dict) and y.get("expr") and not y.get("column"):
			continue
		if _col(y):
			res.append((("y", i), _col(y)))
	if isinstance(c.get("split_by"), dict) and _col(c["split_by"]):
		res.append((("split_by",), _col(c["split_by"])))
	if isinstance(c.get("date"), dict) and _col(c["date"]):
		res.append((("date",), _col(c["date"])))
	for k in ("rows", "columns"):
		for i, e in enumerate(_list(c.get(k))):
			if _col(e):
				res.append(((k, i), _col(e)))
	return res


def _depends(ctx: _Ctx, qid, target) -> bool:
	"""True when query qid is target or (transitively) built on target."""
	seen = set()
	while isinstance(qid, str) and qid not in seen:
		if qid == target:
			return True
		seen.add(qid)
		q = ctx.qmap.get(qid)
		qid = q.get("source_query") if isinstance(q, dict) else None
	return False


# ---- rules ------------------------------------------------------------------------------------

def _l02(ctx):
	def walk(v, ptr):
		if isinstance(v, str):
			m = V2_RE.search(v)
			if m:
				ctx.add("L02", "error", ptr or "/", f"v2 trap: `{m.group(0)}` names a v2 doctype/API",
					"use the v3 doctypes (Insights Query v3 / Chart v3 / Dashboard v3) and insights.api.workbooks")
		elif isinstance(v, dict):
			for k, x in v.items():
				walk(x, _p(ptr, k))
		elif isinstance(v, list):
			for i, x in enumerate(v):
				walk(x, _p(ptr, i))
	walk(ctx.spec, "")


def _l19(ctx):
	def walk(v, ptr):
		if isinstance(v, dict):
			for k, x in v.items():
				if k in DEVELOP_KEYS:
					ctx.add("L19", "warn", _p(ptr, k), f"develop-only key `{k}` is ignored on 3.15.1",
						"remove it, or keep it only for the develop standard-workbook layout")
				walk(x, _p(ptr, k))
		elif isinstance(v, list):
			for i, x in enumerate(v):
				walk(x, _p(ptr, i))
	walk(ctx.spec, "")


def _no_catalog(ctx):
	if ctx.qmap:
		ctx.add("L05", "warn", "/catalog", NO_CATALOG_MSG, "pass --catalog FILE or create finsights_catalog.json")
		ctx.add("L06", "warn", "/catalog", NO_CATALOG_MSG, "pass --catalog FILE")
	if ctx.cmap:
		ctx.add("L08", "warn", "/catalog", NO_CATALOG_MSG, "pass --catalog FILE")


def _queries(ctx):
	cat = ctx.catalog
	for qi, q in _dicts(ctx.spec.get("queries")):
		p = _p("", "queries", qi)
		if ctx.is_bad(p):
			continue
		# L16 raw sql
		if "sql" in q:
			_l16(ctx, q["sql"], _p(p, "sql"))
		# L18 enums
		for j, jn in _dicts(q.get("joins")):
			if "type" in jn and jn["type"] not in JOIN_TYPES:
				ctx.add("L18", "error", _p(p, "joins", j, "type"), f"join type `{jn['type']}` outside enum",
					"one of " + ", ".join(JOIN_TYPES))
		for j, f in _dicts(q.get("filters")):
			if "op" in f and f["op"] not in ALL_FILTER_OPERATORS:
				ctx.add("L18", "error", _p(p, "filters", j, "op"), f"filter op `{f['op']}` outside enum",
					"one of " + ", ".join(ALL_FILTER_OPERATORS))
		sm = q.get("summarize")
		if isinstance(sm, dict):
			for j, b in _dicts(sm.get("by")):
				if "granularity" in b and b["granularity"] not in GRANULARITIES:
					ctx.add("L18", "error", _p(p, "summarize", "by", j, "granularity"),
						f"granularity `{b['granularity']}` outside enum", "one of " + ", ".join(GRANULARITIES))
			for j, m in _dicts(sm.get("measures")):
				if "agg" in m and m["agg"] not in AGGREGATIONS:
					ctx.add("L18", "error", _p(p, "summarize", "measures", j, "agg"),
						f"aggregation `{m['agg']}` outside enum", "one of " + ", ".join(AGGREGATIONS))
		for j, o in _dicts(q.get("order_by")):
			if "dir" in o and o["dir"] not in ORDER_DIRS:
				ctx.add("L18", "error", _p(p, "order_by", j, "dir"), f"order dir `{o['dir']}` outside enum", "asc or desc")
		if cat is None:
			continue
		_columns(ctx, q, p)


def _l16(ctx, sql, ptr):
	hint = "write one SELECT or WITH statement in `sql:`"
	if not isinstance(sql, str) or not sql.strip():
		ctx.add("L16", "error", ptr, "`sql` must be a non-empty string", hint)
		return
	body = SQL_STR_RE.sub(" ", sql).strip()
	if body.endswith(";"):
		body = body[:-1].rstrip()
	if ";" in body:
		ctx.add("L16", "error", ptr, "`sql` must be one statement (found `;`)", hint)
	elif not re.match(r"(?i)^\(*\s*(select|with)\b", body):
		ctx.add("L16", "error", ptr, "`sql` must start with SELECT or WITH", hint)


def _columns(ctx, q, p):
	"""L05 + L06 for one query (catalog present)."""
	cat = ctx.catalog
	cols = None
	if q.get("table"):
		t = q["table"]
		if t not in cat:
			ctx.add("L05", "error", _p(p, "table"), f"table `{t}` not in catalog / not a DocType",
				"check the DocType name or refresh the catalog snapshot")
			return
		cols = dict(cat[t])
	elif isinstance(q.get("source_query"), str):
		sq = q["source_query"]
		if sq not in ctx.qmap:
			ctx.add("L05", "error", _p(p, "source_query"), f"source_query `{sq}` does not resolve to a query", "use an existing query id")
			return
		cols = safe_schema(ctx, sq)
	else:
		return  # sql
	if cols is None:
		return
	cols = dict(cols)

	def need(c, ptr, where, have):
		if isinstance(c, str) and c not in have:
			ctx.add("L06", "error", ptr, f"column `{c}` not in {where}", "pick a column that exists in the catalog / query output")

	where = f"`{q.get('table') or q.get('source_query')}`"
	for j, jn in _dicts(q.get("joins")):
		jp = _p(p, "joins", j)
		jt = jn.get("table")
		if jt not in cat:
			ctx.add("L05", "error", _p(jp, "table"), f"table `{jt}` not in catalog / not a DocType", "check the DocType name")
			continue
		on = jn.get("on")
		if isinstance(on, list) and len(on) == 2:
			need(on[0], _p(jp, "on", 0), where, cols)
			need(on[1], _p(jp, "on", 1), f"`{jt}`", cat[jt])
		for k, c in enumerate(_list(jn.get("columns"))):
			n = _col(c)
			need(n, _p(jp, "columns", k), f"`{jt}`", cat[jt])
			if n:
				cols[n] = cat[jt].get(n, "String")
	for j, f in _dicts(q.get("filters")):
		if "expr" not in f:
			need(f.get("column"), _p(p, "filters", j, "column"), where, cols)
	for j, m in _dicts(q.get("mutate")):
		if isinstance(m.get("as"), str):
			cols[m["as"]] = m.get("type", "String")
	if q.get("select"):
		kept = {}
		for j, e in enumerate(_list(q["select"])):
			n = _col(e)
			if n is None:
				continue
			if n in cols:
				kept[n] = cols[n]
			elif not (isinstance(e, dict) and e.get("optional")):
				need(n, _p(p, "select", j, "column") if isinstance(e, dict) else _p(p, "select", j), where, cols)
		cols = kept
	sm = q.get("summarize")
	if isinstance(sm, dict):
		for j, b in _dicts(sm.get("by")):
			need(b.get("column"), _p(p, "summarize", "by", j, "column"), "the query columns", cols)
		for j, m in _dicts(sm.get("measures")):
			if m.get("column") and not m.get("expr"):
				need(m["column"], _p(p, "summarize", "measures", j, "column"), "the query columns", cols)
				if m.get("agg", "sum") in ("sum", "avg") and m["column"] in cols and column_kind(cols[m["column"]]) != "number":
					ctx.add("L20", "warn", _p(p, "summarize", "measures", j, "column"),
						f"`{m.get('agg', 'sum')}` on a non-numeric column `{m['column']}`", "use count/min/max, or a numeric column")
		final = safe_schema(ctx, q.get("id"))
		if final is not None:
			cols = final
	for j, o in _dicts(q.get("order_by")):
		need(o.get("column"), _p(p, "order_by", j, "column"), "the query output", cols)


def _charts(ctx):
	cat = ctx.catalog
	for ci, c in _dicts(ctx.spec.get("charts")):
		p = _p("", "charts", ci)
		if ctx.is_bad(p):
			continue
		ctype = None
		t = c.get("type")
		if isinstance(t, str) and t.strip().lower() in {x.lower() for x in CHART_TYPES}:
			ctype = next(x for x in CHART_TYPES if x.lower() == t.strip().lower())
		else:
			alias = CHART_ALIASES.get(str(t).strip().lower()) if isinstance(t, str) else None
			hint = f"use one of {', '.join(CHART_TYPES)}" + (f"; `{t}` -> {alias}" if alias else "")
			ctx.add("L09", "error", _p(p, "type"), f"chart_type `{t}` not in the 10-type enum", hint)
		q = c.get("query")
		qok = isinstance(q, str) and q in ctx.qmap
		if not qok:
			ctx.add("L07", "error", _p(p, "query"), f"chart `{c.get('id')}`: query `{q}` does not resolve", "use an existing query id")
		# L18 enums
		for k in ("x", "date"):
			v = c.get(k)
			if isinstance(v, dict) and "granularity" in v and v["granularity"] not in GRANULARITIES:
				ctx.add("L18", "error", _p(p, k, "granularity"), f"granularity `{v['granularity']}` outside enum",
					"one of " + ", ".join(GRANULARITIES))
		for j, y in _dicts(c.get("y")):
			if "agg" in y and y["agg"] not in AGGREGATIONS:
				ctx.add("L18", "error", _p(p, "y", j, "agg"), f"aggregation `{y['agg']}` outside enum", "one of " + ", ".join(AGGREGATIONS))
		for j, f in _dicts(c.get("filters")):
			if "op" in f and f["op"] not in ALL_FILTER_OPERATORS:
				ctx.add("L18", "error", _p(p, "filters", j, "op"), f"filter op `{f['op']}` outside enum", "one of " + ", ".join(ALL_FILTER_OPERATORS))
		for j, o in _dicts(c.get("order")):
			if "dir" in o and o["dir"] not in ORDER_DIRS:
				ctx.add("L18", "error", _p(p, "order", j, "dir"), f"order dir `{o['dir']}` outside enum", "asc or desc")
		# L10 required slots
		if ctype:
			_l10(ctx, c, ctype, p)
		# L22
		if ctype == "Number" and (c.get("comparison") or c.get("sparkline")) and not c.get("date"):
			ctx.add("L22", "warn", p, f"Number chart `{c.get('id')}` has comparison/sparkline without `date`",
				"add `date: {column, granularity}`")
		# L23
		_l23(ctx, c, p)
		# L08 / L20 need schema
		if cat is None or not qok:
			continue
		schema = safe_schema(ctx, q)
		if schema is None:
			continue
		pruned = _pruned(ctx, ctx.qmap[q])
		for parts, col in _chart_cols(c):
			if col not in schema and col not in pruned:
				ctx.add("L08", "error", _p(p, *parts), f"chart `{c.get('id')}`: column `{col}` not in output of query `{q}`",
					"add it to the query (select/summarize) or use an existing output column")
		for j, y in _dicts(c.get("y")):
			col = y.get("column")
			if col in schema and y.get("agg", "sum") in ("sum", "avg") and not y.get("expr") and column_kind(schema[col]) != "number":
				ctx.add("L20", "warn", _p(p, "y", j, "column"), f"`{y.get('agg', 'sum')}` on a non-numeric column `{col}`",
					"use count/min/max, or a numeric column")


def _l10(ctx, c, ctype, p):
	def miss(what):
		ctx.add("L10", "error", p, f"chart `{c.get('id')}` misses a required slot for `{ctype}`: {what}",
			"see R3 section 4 (validateConfig)")
	has_x = bool(_col(c.get("x")))
	ys = [y for y in _list(c.get("y")) if _col(y) or (isinstance(y, dict) and y.get("expr"))]
	if ctype in ("Bar", "Line", "Row"):
		if not has_x:
			miss("x")
		elif isinstance(c.get("split_by"), dict) and _col(c["split_by"]) == _col(c.get("x")):
			miss("x must differ from split_by")
	elif ctype == "Number":
		if not ys:
			miss("y (>=1 measure)")
	elif ctype in ("Donut", "Funnel", "Map"):
		if not has_x:
			miss("x (label/location column)")
		if not ys:
			miss("y (value measure)")
	elif ctype == "Table":
		if not [r for r in _list(c.get("rows")) if _col(r)]:
			miss("rows (>=1)")
	elif ctype == "Bubble":
		if not has_x:
			miss("x measure")
		if not ys:
			miss("y measure")


def _l23(ctx, c, p):
	order = [(i, o) for i, o in _dicts(c.get("order")) if isinstance(o.get("by"), str)]
	if not order:
		return
	aliases = set()
	for y in _list(c.get("y")):
		if isinstance(y, dict):
			a = y.get("as") or y.get("column")
			if a:
				aliases.add(a)
		elif isinstance(y, str):
			aliases.add(y)
	x = c.get("x")
	if isinstance(x, dict):
		aliases.add(x.get("as") or x.get("column"))
	elif isinstance(x, str):
		aliases.add(x)
	if isinstance(c.get("split_by"), dict):
		aliases.add(_col(c["split_by"]))
	for k in ("rows", "columns"):
		for e in _list(c.get(k)):
			aliases.add(_col(e))
	for i, o in order:
		if o["by"] not in aliases:
			ctx.add("L23", "error", _p(p, "order", i, "by"), f"chart `{c.get('id')}` order.by `{o['by']}` is not an output alias",
				"use a measure `as` / dimension name: " + ", ".join(sorted(a for a in aliases if a)))


def _l17(ctx):
	graph = {qid: q.get("source_query") for qid, q in ctx.qmap.items() if isinstance(q.get("source_query"), str)}
	reported = set()
	for start in sorted(graph):
		path, cur = [], start
		while cur in graph and cur not in path:
			path.append(cur)
			cur = graph[cur]
		if cur in path:
			cyc = path[path.index(cur):]
			ctx.cyclic.update(path)
			k = min(range(len(cyc)), key=lambda i: cyc[i])
			cyc = cyc[k:] + cyc[:k]
			key = tuple(cyc)
			if key in reported:
				continue
			reported.add(key)
			ctx.add("L17", "error", _p("", "queries", ctx.qidx[cyc[0]], "source_query"),
				"query reference cycle " + "->".join(cyc + [cyc[0]]), "break the source_query loop")


def _dashboards(ctx):
	cat = ctx.catalog
	for di, d in _dicts(ctx.spec.get("dashboards")):
		p = _p("", "dashboards", di)
		if ctx.is_bad(p):
			continue
		rects = []  # (label, pointer, x,y,w,h)
		placed = {}
		dash_charts = []
		for ii, it in _dicts(d.get("items")):
			ip = _p(p, "items", ii)
			cid = it.get("chart")
			label = None
			if "chart" in it:
				if cid not in ctx.cmap:
					ctx.add("L15", "error", _p(ip, "chart"), f"dashboard `{d.get('id')}`: chart `{cid}` undefined", "define it under charts")
				elif cid in placed:
					ctx.add("L15", "error", _p(ip, "chart"), f"dashboard `{d.get('id')}`: chart `{cid}` placed twice (first at {placed[cid]})",
						"place each chart once")
				else:
					placed[cid] = ip
					dash_charts.append(cid)
				label = f"chart-{cid}"
			elif "text" in it:
				txt = it["text"]
				if not isinstance(txt, str) or not re.sub(r"<[^>]*>|&nbsp;", "", txt).strip():
					ctx.add("L25", "error", _p(ip, "text"), "text item is empty", "give it content or remove it")
				label = f"text-{ii}"
			rects.append((label or f"item-{ii}", ip, it))
		auto = 0
		frects = []
		filters = [(fi, f) for fi, f in _dicts(d.get("filters")) if not ctx.is_bad(_p(p, "filters", fi))]
		for fi, f in filters:
			fp = _p(p, "filters", fi)
			lay = f.get("layout")
			if isinstance(lay, dict):
				frects.append((f"filter-{f.get('id')}", _p(fp, "layout"), lay))
			else:
				n = auto
				auto += 1
				frects.append((f"filter-{f.get('id')}", fp, {"x": (n * 4) % GRID_COLS, "y": (n * 4) // GRID_COLS, "w": 4, "h": 1}))
		_layout(ctx, d, rects + frects, p)
		for fi, f in filters:
			_filter(ctx, d, f, _p(p, "filters", fi), dash_charts, cat)


def _layout(ctx, d, rects, p):
	valid = []
	for label, ptr, r in rects:
		vals = [r.get(k) for k in ("x", "y", "w", "h")]
		if not all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
			ctx.add("L11", "error", ptr, f"layout of `{label}` needs integer x,y,w,h", "use integers")
			continue
		x, y, w, h = vals
		if x < 0 or y < 0 or w < 1 or h < 1 or x + w > GRID_COLS:
			ctx.add("L11", "error", ptr, f"layout out of bounds for `{label}`: x={x} y={y} w={w} h={h}",
				f"need x>=0,y>=0,w>=1,h>=1,x+w<={GRID_COLS}")
			continue
		valid.append((label, ptr, x, y, w, h))
	for i in range(len(valid)):
		for j in range(i + 1, len(valid)):
			a, b = valid[i], valid[j]
			if a[2] < b[2] + b[4] and b[2] < a[2] + a[4] and a[3] < b[3] + b[5] and b[3] < a[3] + a[5]:
				ctx.add("L12", "error", b[1], f"layout overlap `{a[0]}` / `{b[0]}` on dashboard `{d.get('id')}`",
					"move one item to a free grid cell")


def _filter(ctx, d, f, fp, dash_charts, cat):
	ftype_raw = f.get("type")
	ftype = ftype_raw.capitalize() if isinstance(ftype_raw, str) else None
	if ftype not in FILTER_TYPE_KIND:
		ctx.add("L18", "error", _p(fp, "type"), f"filter type `{ftype_raw}` outside enum", "date, string or number")
		ftype = None
	# L14
	df = f.get("default")
	if ftype and isinstance(df, dict):
		op = df.get("op")
		if op not in FILTER_OPERATORS[FILTER_TYPE_KIND[ftype]]:
			ctx.add("L14", "error", _p(fp, "default", "op"), f"filter `{f.get('id')}` default op `{op}` invalid for {ftype}",
				"allowed: " + ", ".join(FILTER_OPERATORS[FILTER_TYPE_KIND[ftype]]))
		elif op == "within" and not (isinstance(df.get("value"), str) and WITHIN_RE.match(df["value"].strip())):
			ctx.add("L14", "error", _p(fp, "default", "value"), f"bad `within` value `{df.get('value')}`",
				"use `Last N <Unit>` or `Current <Unit>` (Day|Week|Month|Quarter|Year|Fiscal Year)")
	# L13
	at = f.get("applies_to")
	if not isinstance(at, dict):
		return
	for qid, col in at.items():
		ap = _p(fp, "applies_to", qid)
		fid = f.get("id")
		if qid not in ctx.qmap:
			ctx.add("L13", "error", ap, f"filter `{fid}`: applies_to `{qid}.{col}`: query does not resolve", "use an existing query id")
			continue
		if not any(isinstance(ctx.cmap.get(c, {}).get("query"), str) and _depends(ctx, ctx.cmap[c]["query"], qid) for c in dash_charts):
			ctx.add("L13", "error", ap, f"filter `{fid}`: applies_to `{qid}.{col}`: query unused by any chart on the dashboard",
				"add a chart on that query to the dashboard, or drop the link")
			continue
		if cat is None:
			continue
		schema = safe_schema(ctx, qid)
		if schema is None:
			continue
		if col in _pruned(ctx, ctx.qmap[qid]):
			continue
		if col not in schema:
			ctx.add("L13", "error", ap, f"filter `{fid}`: applies_to `{qid}.{col}`: column absent from the query's FINAL output",
				"filter columns must survive select/summarize; keep the query row-level")
		elif ftype and schema[col] not in FILTER_COLUMN_TYPES[ftype]:
			ctx.add("L13", "error", ap, f"filter `{fid}`: applies_to `{qid}.{col}`: type mismatch ({ftype} <- {schema[col]})",
				"Date<-Date/Datetime, Number<-Integer/Decimal, String<-String/Text")


def _l21(ctx):
	if ctx.catalog is None:
		return
	for qi, q in _dicts(ctx.spec.get("queries")):
		qid = q.get("id")
		p = _p("", "queries", qi)
		if ctx.is_bad(p):
			continue
		for col, si in _pruned(ctx, q).items():
			n = 1
			for _, c in ctx.cmap.items():
				if c.get("query") == qid:
					n += sum(1 for _, cc in _chart_cols(c) if cc == col)
			for d in _list(ctx.spec.get("dashboards")):
				for f in _list(d.get("filters") if isinstance(d, dict) else None):
					if isinstance(f, dict) and isinstance(f.get("applies_to"), dict) and f["applies_to"].get(qid) == col:
						n += 1
			ctx.add("L21", "info", _p(p, "select", si), f"optional column `{col}` absent: pruned {n} refs",
				"add the column to the catalog if it should exist")


def _l24(ctx):
	used = {c.get("query") for c in ctx.cmap.values()}
	used |= {q.get("source_query") for q in ctx.qmap.values()}
	for qid, q in ctx.qmap.items():
		if qid not in used:
			ctx.add("L24", "warn", _p("", "queries", ctx.qidx[qid]), f"query `{qid}` used by no chart", "add a chart or remove the query")


def _l26(ctx):
	if "share" in ctx.spec:
		ctx.add("L26", "warn", "/share", "share is not applied",
			"`share:` is accepted but nothing applies it (finsights never grants access); grant access in the Insights UI "
			"or via insights.api.workbooks.update_share_permissions")


def _l27(ctx):
	for ci, c in _dicts(ctx.spec.get("charts")):
		p = _p("", "charts", ci)
		t = c.get("type")
		if ctx.is_bad(p) or not isinstance(t, str):
			continue
		name = next((x for x in ("Map", "Bubble", "Sankey") if x.lower() == t.strip().lower()), None)
		if name:
			ctx.add("L27", "warn", _p(p, "type"), f"chart `{c.get('id')}`: derived chart type `{name}`",
				"compiled config is derived from the Insights type definitions and unverified on 3.15.1; check the rendered chart")


def _l28(ctx):
	cat = ctx.catalog
	if cat is None:
		return
	for qi, q in _dicts(ctx.spec.get("queries")):
		p = _p("", "queries", qi)
		if ctx.is_bad(p) or not _list(q.get("joins")) or q.get("sql"):
			continue
		final = safe_schema(ctx, q.get("id"))
		if final is None:
			continue
		if q.get("table"):
			if q["table"] not in cat:
				continue
			have = set(cat[q["table"]])
			base = q["table"]
		else:
			src = safe_schema(ctx, q.get("source_query"))
			if src is None:
				continue
			have = set(src)
			base = q.get("source_query")
		for j, jn in _dicts(q.get("joins")):
			jt = jn.get("table")
			if jt not in cat:
				continue
			for k, c in enumerate(_list(jn.get("columns"))):
				n = _col(c)
				if n and n in have and n in final:
					ctx.add("L28", "warn", _p(p, "joins", j, "columns", k),
						f"join column collision: `{n}` exists in both `{base}` and joined `{jt}`",
						f"the join carries `{n}` from `{jt}` into the same output name as the base column; remove `{n}` from "
						"this join's `columns` (select cannot rename), or build a source_query on the joined table with a mutate alias")
			have |= {_col(c) for c in _list(jn.get("columns")) if _col(c)}


def _guard(fn, ctx):
	try:
		fn(ctx)
	except Exception:  # malformed specs must never crash lint
		pass


def lint_spec(spec, catalog, only=None) -> list:
	shape = shape_findings(spec)
	if not isinstance(spec, dict):
		return _finish(shape, only)
	ctx = _Ctx(spec, catalog)
	ctx.out.extend(shape)
	ctx.bad = {f.path for f in shape if f.rule == "L01" and f.severity == "error"}
	for i, q in _dicts(spec.get("queries")):
		if isinstance(q.get("id"), str) and q["id"] not in ctx.qmap:
			ctx.qmap[q["id"]] = q
			ctx.qidx[q["id"]] = i
	for i, c in _dicts(spec.get("charts")):
		if isinstance(c.get("id"), str) and c["id"] not in ctx.cmap:
			ctx.cmap[c["id"]] = c
			ctx.cidx[c["id"]] = i
	_guard(_l02, ctx)
	_guard(_l19, ctx)
	_guard(_l17, ctx)
	if catalog is None:
		_guard(_no_catalog, ctx)
	_guard(_queries, ctx)
	_guard(_charts, ctx)
	_guard(_dashboards, ctx)
	_guard(_l21, ctx)
	_guard(_l24, ctx)
	_guard(_l26, ctx)
	_guard(_l27, ctx)
	_guard(_l28, ctx)
	return _finish(ctx.out, only)


def _finish(findings, only):
	if only:
		keep = {s.strip().upper() for s in only}
		findings = [f for f in findings if f.rule in keep]
	# dedupe identical findings, then sort deterministically
	seen, res = set(), []
	for f in findings:
		k = (f.rule, f.path, f.message)
		if k not in seen:
			seen.add(k)
			res.append(f)
	return sorted(res, key=lambda f: (f.rule, f.path, f.message))


def exit_code_for(findings, strict: bool = False) -> int:
	for f in findings:
		if f.severity == "error" or (strict and f.severity == "warn"):
			return 1
	return 0
