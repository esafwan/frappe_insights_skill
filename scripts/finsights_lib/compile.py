"""Spec -> Insights workbook JSON (template envelope or standard flat doc). See design spec sections 1, 2, 6."""

from __future__ import annotations

import json
import os
import re

from .common import Finding
from .schema import norm_chart_type, output_schema

DEFAULT_DATA_SOURCE = "Site DB"


def data_source() -> str:
	"""Data source the compiled queries read. Default "Site DB" (Insights 3.15 / Frappe 16, tables == DocTypes).
	Set INSIGHTS_DATA_SOURCE to another Insights Data Source v3 name for sites where the URY tables have no
	DocType (Insights 3.50-dev refuses the Site DB for a table without a DocType; see
	references/develop-compat.md)."""
	return os.environ.get("INSIGHTS_DATA_SOURCE") or DEFAULT_DATA_SOURCE
DATE_TYPES = ("Date", "Datetime")
FILTER_TYPE_NAMES = {"date": "Date", "string": "String", "number": "Number"}


def dumps(obj) -> str:
	"""Byte-stable serialisation."""
	return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def kebab(s: str) -> str:
	return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def _col(name: str) -> dict:
	return {"type": "column", "column_name": name}


def _cname(e):
	return e if isinstance(e, str) else e.get("column")


def _expr(e: str) -> dict:
	return {"type": "expression", "expression": e}


# ---------------------------------------------------------------- queries

def _table_ref(table: str) -> dict:
	return {"type": "table", "data_source": data_source(), "table_name": "tab" + table}


def _filter_rule(f: dict) -> dict:
	# Falsy values (0, False, "") are passed through verbatim. In Insights 3.15.1 the chart path
	# (frontend charts/chart.ts:222 -> query.addFilterGroup) does not validate rows, and the backend
	# (ibis_utils.py:480-518, FILTER_OPERATORS "=" at :109) evaluates `col == 0` correctly. The UI
	# validator isFilterValid (query/components/filter_utils.ts:74-110) accepts 0 only for Number
	# columns; for String-typed columns it requires string / string[] values. No rewrite is provably safe.
	if "expr" in f:
		return {"expression": _expr(f["expr"])}
	return {"column": _col(f["column"]), "operator": f["op"], "value": f.get("value")}


def _dimension(d, schema: dict) -> dict:
	"""Dimension object from a column name or {column, granularity?, as?}."""
	if isinstance(d, str):
		d = {"column": d}
	col = d["column"]
	dt = schema.get(col, "String")
	if d.get("granularity") and dt not in DATE_TYPES:
		dt = "Date"
	out = {"column_name": col, "data_type": dt, "dimension_name": d.get("as") or col}
	if d.get("granularity"):
		out["granularity"] = d["granularity"]
	return out


def _measure(m, schema: dict) -> dict:
	"""Measure object from {column|expr, agg=sum, as?, format?, currency_column?}."""
	if isinstance(m, str):
		m = {"column": m}
	agg = m.get("agg", "sum")
	col = m.get("column")
	if m.get("expr") and not col:
		out = {"measure_name": m.get("as") or "measure", "expression": _expr(m["expr"]),
			"data_type": m.get("type", "Decimal")}
	else:
		if agg in ("count", "count_distinct"):
			dt = "Integer"
		elif agg in ("sum", "avg"):
			dt = "Decimal"
		else:
			dt = schema.get(col, "Decimal")
		cn = col or "count"
		out = {"aggregation": agg, "column_name": cn, "data_type": dt, "measure_name": m.get("as") or cn}
	if m.get("format"):
		out["format"] = m["format"]
	if m.get("currency_column"):
		out["currency_column"] = m["currency_column"]
	return out


def _query_ops(q: dict, wb_name: str, names: dict, catalog: dict, qmap: dict, pruned: set) -> list:
	"""Operations in the fixed order: source, joins, filter_group, mutate, select, summarize, order_by, limit."""
	if q.get("sql"):
		return [{"type": "sql", "data_source": data_source(), "raw_sql": q["sql"]}]
	ops = []
	if q.get("table"):
		ops.append({"type": "source", "table": _table_ref(q["table"])})
	else:
		ops.append({"type": "source", "table": {"type": "query", "workbook": wb_name, "query_name": names[q["source_query"]]}})
	for j in q.get("joins") or []:
		left, right = j["on"]
		ops.append({
			"type": "join", "join_type": j.get("type", "inner"), "table": _table_ref(j["table"]),
			"select_columns": [_col(_cname(c)) for c in j.get("columns") or []],
			"join_condition": {"left_column": _col(left), "right_column": _col(right)},
		})
	if q.get("filters"):
		logical = "Or" if any(f.get("any") for f in q["filters"]) else "And"
		ops.append({"type": "filter_group", "logical_operator": logical, "filters": [_filter_rule(f) for f in q["filters"]]})
	for m in q.get("mutate") or []:
		ops.append({"type": "mutate", "new_name": m["as"], "data_type": m.get("type", "String"), "expression": _expr(m["expr"])})
	if q.get("select"):
		cols = [_cname(e) for e in q["select"] if _cname(e) not in pruned]
		ops.append({"type": "select", "column_names": cols})
	s = q.get("summarize")
	if s:
		# schema before summarize = columns available to dimensions/measures
		pre = output_schema({k: v for k, v in q.items() if k != "summarize"}, catalog, qmap)
		ops.append({
			"type": "summarize",
			"measures": [_measure(m, pre) for m in s.get("measures") or []],
			"dimensions": [_dimension(d, pre) for d in s.get("by") or []],
		})
	for o in q.get("order_by") or []:
		ops.append({"type": "order_by", "column": _col(o["column"]), "direction": o.get("dir", "asc")})
	if q.get("limit"):
		ops.append({"type": "limit", "limit": q["limit"]})
	return ops


# ---------------------------------------------------------------- charts

def _chart_columns(c: dict) -> set:
	"""Every source-query column a chart references (for optional-column pruning)."""
	cols = set()

	def add(v):
		if isinstance(v, str):
			cols.add(v)
		elif isinstance(v, dict) and v.get("column"):
			cols.add(v["column"])

	add(c.get("x"))
	for k in ("y", "rows", "columns"):
		for e in c.get(k) or []:
			add(e)
	add(c.get("split_by"))
	add(c.get("date"))
	for f in c.get("filters") or []:
		add(f)
	return cols


def _chart_config(c: dict, ctype: str, schema: dict) -> dict:
	cfg: dict = {}
	ys = [y if isinstance(y, dict) else {"column": y} for y in c.get("y") or []]
	meas = [_measure(y, schema) for y in ys]
	x = c.get("x")
	dim = _dimension(x, schema) if x else None

	def date_sorted_order():
		return [{"column": _col(dim["dimension_name"]), "direction": "asc"}]

	order = None
	if c.get("order"):
		order = [{"column": _col(o["by"]), "direction": o.get("dir", "asc")} for o in c["order"]]

	if ctype in ("Bar", "Line", "Row"):
		if dim:
			cfg["x_axis"] = {"dimension": dim}
		y_axis = {"series": [{"measure": m} for m in meas], "show_data_labels": bool(c.get("show_data_labels", False))}
		if ctype == "Bar" and "stack" in c:
			y_axis["stack"] = bool(c["stack"])
		if ctype == "Line" and "smooth" in c:
			y_axis["smooth"] = bool(c["smooth"])
		cfg["y_axis"] = y_axis
		if c.get("split_by"):
			sb = c["split_by"]
			sp = {"dimension": _dimension(sb["column"], schema)}
			if sb.get("max"):
				sp["max_split_values"] = sb["max"]
			cfg["split_by"] = sp
		if order is None and dim and dim.get("granularity"):
			order = date_sorted_order()
	elif ctype == "Number":
		cfg["number_columns"] = meas
		if "comparison" in c:
			cfg["comparison"] = bool(c["comparison"])
		if "sparkline" in c:
			cfg["sparkline"] = bool(c["sparkline"])
		if c.get("date"):
			dc = _dimension(c["date"], schema)
			cfg["date_column"] = dc
			if order is None:
				order = [{"column": _col(dc["dimension_name"]), "direction": "asc"}]
	elif ctype in ("Donut", "Funnel"):
		if dim:
			cfg["label_column"] = dim
		if meas:
			cfg["value_column"] = meas[0]
		if ctype == "Donut":
			if c.get("legend"):
				cfg["legend_position"] = c["legend"]
			if c.get("max_slices"):
				cfg["max_slices"] = c["max_slices"]
	elif ctype == "Table":
		rows = c.get("rows") or ([x] if x else [])
		cfg["rows"] = [_dimension(r, schema) for r in rows]
		# Insights 3.15.1 chart.ts addTableChartOperation does config.rows/.columns/.values.filter(...)
		# unguarded and decides pivot iff columns is non-empty, so all three keys must ALWAYS exist
		# (shipped workbook_templates carry "columns": [] on flat tables).
		cfg["columns"] = [_dimension(r, schema) for r in c.get("columns") or []]
		cfg["values"] = meas
	# DERIVED/UNVERIFIED (R3 s4: no shipped fixture uses Map/Bubble/Sankey): minimal slots from chart.types.ts
	elif ctype == "Map":
		if dim:
			cfg["location_column"] = dim
		if meas:
			cfg["value_column"] = meas[0]
		if c.get("map_type"):
			cfg["map_type"] = c["map_type"]
	elif ctype == "Bubble":
		if meas:
			cfg["xAxis"] = meas[0]
		if len(meas) > 1:
			cfg["yAxis"] = meas[1]
		if len(meas) > 2:
			cfg["size_column"] = meas[2]
		if dim:
			cfg["dimension"] = dim
		if "show_data_labels" in c:
			cfg["show_data_labels"] = bool(c["show_data_labels"])
	elif ctype == "Sankey":
		# x = source, split_by = target, y[0] = value
		if dim:
			cfg["source_column"] = dim
		if c.get("split_by"):
			cfg["target_column"] = _dimension(c["split_by"]["column"], schema)
		if meas:
			cfg["value_column"] = meas[0]

	cfg["filters"] = {"filters": [_filter_rule(f) for f in c.get("filters") or []], "logical_operator": "And"}
	cfg["order_by"] = order or []
	cfg["limit"] = c.get("limit", 100)
	return cfg


# ---------------------------------------------------------------- dashboards

def _depends_on(qid: str, target: str, qmap: dict) -> bool:
	seen = set()
	while qid and qid not in seen:
		if qid == target:
			return True
		seen.add(qid)
		qid = (qmap.get(qid) or {}).get("source_query")
	return False


def _dashboard(d: dict, wb_name: str, names: dict, cnames: dict, charts_by_id: dict, qmap: dict,
		dead_charts: set, pruned_by_q: dict, findings: list, di: int) -> dict:
	items = []
	live = [it for it in d["items"] if "chart" not in it or it["chart"] not in dead_charts]
	chart_ids = [it["chart"] for it in live if "chart" in it]

	fx, fy = 0, 0
	for fi, f in enumerate(d.get("filters") or []):
		links = {}
		dropped = 0
		for qid, col in (f.get("applies_to") or {}).items():
			if col in pruned_by_q.get(qid, ()):
				dropped += 1
				continue
			for cid in chart_ids:
				cq = charts_by_id[cid]["query"]
				if _depends_on(cq, qid, qmap) and (cq == qid or cnames[cid] not in links):
					links[cnames[cid]] = f"`{names[qid]}`.`{col}`"
		if dropped and not links:
			findings.append(Finding("L21", "info", f"/dashboards/{di}/filters/{fi}",
				f"optional column absent: filter `{f['id']}` dropped ({dropped} link(s) pruned)", ""))
			continue
		lay = f.get("layout") or {"x": fx, "y": fy, "w": 4, "h": 1}
		if "layout" not in f:
			fx += 4
			if fx + 4 > 20:
				fx, fy = 0, fy + 1
		item = {"type": "filter", "filter_name": f["label"], "filter_type": FILTER_TYPE_NAMES[str(f["type"]).lower()],
			"links": links, "layout": {"i": "filter-" + kebab(f["id"]), "x": lay["x"], "y": lay["y"], "w": lay["w"], "h": lay["h"]}}
		if f.get("icon"):
			item["icon"] = f["icon"]
		dft = f.get("default")
		if dft and dft.get("value") is not None:
			item["default_operator"] = dft["op"]
			item["default_value"] = dft["value"]
		items.append(item)

	tn = 0
	for it in live:
		lay = {"x": it["x"], "y": it["y"], "w": it["w"], "h": it["h"]}
		if "chart" in it:
			lay["i"] = "chart-" + kebab(it["chart"])
			items.append({"type": "chart", "chart": cnames[it["chart"]], "layout": lay})
		else:
			lay["i"] = f"text-{tn}"
			tn += 1
			items.append({"type": "text", "text": it["text"], "layout": lay})
	compact_layout(items)
	return {"name": names[d["id"]], "title": d["title"], "workbook": wb_name, "items": items}


def _collides(a: dict, b: dict) -> bool:
	return not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"]
		or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])


def compact_layout(items: list) -> list:
	"""Vertical compaction, in place; x/w/h and item order are untouched, only layout.y changes.

	Port of the grid compaction the Insights dashboard (vue-grid-layout, verticalCompact=true,
	insights/frontend/src2/dashboard/VueGridLayout.vue) applies before saving, so the stored
	layout equals what we compile. Algorithm: process items sorted by (y, x) (stable); each one
	moves up (y -= 1) while y > 0 and it would not collide with an already-placed item, then
	moves down while it collides. Deterministic and idempotent.
	"""
	lays = [it["layout"] for it in items if isinstance(it.get("layout"), dict)]
	placed = []
	for lay in sorted(lays, key=lambda l: (l["y"], l["x"])):
		while lay["y"] > 0 and not any(_collides(lay, o) for o in placed):
			lay["y"] -= 1
		while any(_collides(lay, o) for o in placed):
			lay["y"] += 1
		placed.append(lay)
	return items


# ---------------------------------------------------------------- entry point

def compile_spec(spec: dict, catalog: dict | None, fmt: str = "template") -> tuple:
	"""Compile a validated spec. Returns (document, findings)."""
	findings: list = []
	wb = spec["workbook"]
	wb_name = kebab(wb["id"])
	cat = catalog or {}
	queries = spec.get("queries") or []
	charts = spec.get("charts") or []
	dashboards = spec.get("dashboards") or []
	qmap = {q["id"]: q for q in queries}
	charts_by_id = {c["id"]: c for c in charts}

	def nm(i):
		return f"{wb_name}-{kebab(i)}"

	qnames = {q["id"]: nm(q["id"]) for q in queries}
	cnames = {c["id"]: nm(c["id"]) for c in charts}
	names = {**qnames, **{d["id"]: nm(d["id"]) for d in dashboards}}

	# optional-column pruning: only when a catalog is available for the table
	pruned_by_q: dict = {}
	refs: dict = {}  # (qid, col) -> [count, pointer]

	def base_table(q):
		seen = set()
		while q and q.get("source_query") and q["id"] not in seen:
			seen.add(q["id"])
			q = qmap.get(q["source_query"])
		return (q or {}).get("table")

	for qi, q in enumerate(queries):
		tbl = base_table(q)
		cols = cat.get(tbl)
		for si, e in enumerate(q.get("select") or []):
			if isinstance(e, dict) and e.get("optional") and cols is not None and e["column"] not in cols:
				pruned_by_q.setdefault(q["id"], set()).add(e["column"])
				refs[(q["id"], e["column"])] = [1, f"/queries/{qi}/select/{si}"]
	# a downstream query inherits pruned columns
	for q in queries:
		src = q.get("source_query")
		if src:
			pruned_by_q.setdefault(q["id"], set()).update(pruned_by_q.get(src, set()))

	dead_charts = set()
	for c in charts:
		bad = _chart_columns(c) & pruned_by_q.get(c["query"], set())
		if bad:
			dead_charts.add(c["id"])
			for col in bad:
				refs.setdefault((c["query"], col), [0, ""])[0] += 1

	out_queries, out_charts, out_dash = {}, {}, {}
	for qi, q in enumerate(queries):
		native = bool(q.get("sql"))
		out_queries[qnames[q["id"]]] = {
			"name": qnames[q["id"]], "title": q["title"], "workbook": wb_name, "folder": None, "sort_order": qi,
			"use_live_connection": 1 if q.get("live", True) else 0,
			"is_builder_query": 0 if native else 1, "is_native_query": 1 if native else 0, "is_script_query": 0,
			"operations": _query_ops(q, wb_name, qnames, cat, qmap, pruned_by_q.get(q["id"], set())),
		}
	for ci, c in enumerate(charts):
		if c["id"] in dead_charts:
			continue
		q = qmap[c["query"]]
		schema = output_schema(q, cat, qmap) if cat else {}
		ctype = norm_chart_type(c["type"])
		out_charts[cnames[c["id"]]] = {
			"name": cnames[c["id"]], "title": c["title"], "workbook": wb_name, "folder": None, "sort_order": ci,
			"query": qnames[c["query"]], "chart_type": ctype, "config": _chart_config(c, ctype, schema),
		}
	for di, d in enumerate(dashboards):
		for fi, f in enumerate(d.get("filters") or []):
			for qid, col in (f.get("applies_to") or {}).items():
				if col in pruned_by_q.get(qid, ()):
					refs.setdefault((qid, col), [0, ""])[0] += 1
		out_dash[names[d["id"]]] = _dashboard(d, wb_name, names, cnames, charts_by_id, qmap, dead_charts,
			pruned_by_q, findings, di)

	for (qid, col), (n, ptr) in sorted(refs.items()):
		findings.append(Finding("L21", "info", ptr or f"/queries/{qid}",
			f"optional column `{col}` absent: pruned {n} refs", "remove `optional` or add the column to the catalog"))

	if fmt == "standard":
		for d in out_dash.values():
			d["vertical_compact_layout"] = 1
		doc = {
			"doctype": "Insights Workbook", "name": wb_name, "title": wb["title"], "is_standard": 1,
			"module": wb.get("module") or "Insights", "docstatus": 0, "folders": [],
			"queries": out_queries, "charts": out_charts, "dashboards": out_dash,
		}
		return doc, findings
	doc = {
		"version": "1.0", "type": "Workbook", "name": wb_name,
		"doc": {"name": wb_name, "title": wb["title"]},
		"dependencies": {"folders": [], "queries": out_queries, "charts": out_charts, "dashboards": out_dash},
	}
	return doc, findings
