"""Self-heal: turn lint findings into Fix objects (safe = deterministic + meaning-preserving) and apply them.

Fix = {"rule","path","safe","description","patch":[{"op","path","value"}],"alternatives":[{"description","patch"}]}
Safe fixes only rename identifiers/titles/layout/aliases; they never add, remove or re-point queries, charts or filters.
Unsafe fixes (unknown table/column, missing slot, ...) carry `alternatives` and are never applied automatically.
"""

from __future__ import annotations

import copy
import difflib
import re

from .common import Finding
from .lint import lint_spec
from .schema import GRID_COLS, norm_chart_type, output_schema
from .spec import G, ID_RE

CLOSE_CUTOFF = 0.8

# ---------------------------------------------------------------- JSON pointer / patch


def _split(ptr: str) -> list:
	if ptr in ("", "/"):
		return []
	if not ptr.startswith("/"):
		raise ValueError(f"bad JSON pointer {ptr!r}")
	return [p.replace("~1", "/").replace("~0", "~") for p in ptr[1:].split("/")]


def _esc(k) -> str:
	return str(k).replace("~", "~0").replace("/", "~1")


def _ptr(*parts) -> str:
	return "".join("/" + _esc(p) for p in parts)


def _index(lst: list, tok: str, allow_end=False) -> int:
	if tok == "-" and allow_end:
		return len(lst)
	if not re.fullmatch(r"0|[1-9]\d*", tok):
		raise ValueError(f"bad list index {tok!r}")
	i = int(tok)
	if i > len(lst) or (i == len(lst) and not allow_end):
		raise ValueError(f"list index {i} out of range")
	return i


def _parent(doc, parts: list, ptr: str):
	cur = doc
	for tok in parts[:-1]:
		if isinstance(cur, dict) and tok in cur:
			cur = cur[tok]
		elif isinstance(cur, list):
			cur = cur[_index(cur, tok)]
		else:
			raise ValueError(f"path not found: {ptr}")
	return cur


def get_at(doc, ptr: str, default=None):
	cur = doc
	try:
		for tok in _split(ptr):
			if isinstance(cur, dict):
				cur = cur[tok]
			elif isinstance(cur, list):
				cur = cur[_index(cur, tok)]
			else:
				return default
	except (KeyError, ValueError):
		return default
	return cur


def apply_patch(spec, ops) -> dict:
	"""Apply replace/remove/add ops (JSON pointers) to a deep copy of `spec`. ValueError on a bad op or path."""
	doc = copy.deepcopy(spec)
	for op in ops or []:
		kind = op.get("op")
		ptr = op.get("path")
		if kind not in ("replace", "remove", "add") or not isinstance(ptr, str):
			raise ValueError(f"bad patch op {op!r}")
		parts = _split(ptr)
		if not parts:
			if kind != "replace":
				raise ValueError("only replace is allowed on the document root")
			doc = copy.deepcopy(op.get("value"))
			continue
		par = _parent(doc, parts, ptr)
		last = parts[-1]
		if isinstance(par, dict):
			if kind in ("replace", "remove") and last not in par:
				raise ValueError(f"path not found: {ptr}")
			if kind == "remove":
				del par[last]
			else:
				par[last] = copy.deepcopy(op.get("value"))
		elif isinstance(par, list):
			if kind == "add":
				par.insert(_index(par, last, True), copy.deepcopy(op.get("value")))
			elif kind == "replace":
				par[_index(par, last)] = copy.deepcopy(op.get("value"))
			else:
				del par[_index(par, last)]
		else:
			raise ValueError(f"path not found: {ptr}")
	return doc


# ---------------------------------------------------------------- fix construction


def _fix(rule, path, safe, description, patch=None, alternatives=None) -> dict:
	return {"rule": rule, "path": path, "safe": safe, "description": description,
		"patch": patch or [], "alternatives": alternatives or []}


def _fd(f) -> dict:
	return f.to_dict() if isinstance(f, Finding) else dict(f)


def _lev(a: str, b: str) -> int:
	prev = list(range(len(b) + 1))
	for i, ca in enumerate(a, 1):
		cur = [i]
		for j, cb in enumerate(b, 1):
			cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
		prev = cur
	return prev[-1]


def _close(name, pool, n=3, cutoff=CLOSE_CUTOFF) -> list:
	if not isinstance(name, str):
		return []
	return difflib.get_close_matches(name, sorted(set(pool)), n=n, cutoff=cutoff)


_KIND_BY_SHAPE = {
	"": "top", "workbook": "workbook", "queries/#": "query", "queries/#/joins/#": "join",
	"queries/#/filters/#": "qfilter", "queries/#/mutate/#": "mutate", "queries/#/select/#": "select",
	"queries/#/summarize": "summarize", "queries/#/summarize/by/#": "by",
	"queries/#/summarize/measures/#": "measure", "queries/#/order_by/#": "order_by",
	"charts/#": "chart", "charts/#/x": "x", "charts/#/y/#": "y", "charts/#/split_by": "split_by",
	"charts/#/date": "date", "charts/#/order/#": "order", "charts/#/filters/#": "cfilter",
	"dashboards/#": "dashboard", "dashboards/#/filters/#": "dfilter", "dashboards/#/filters/#/default": "default",
	"dashboards/#/filters/#/layout": "layout", "dashboards/#/items/#": "item", "desk": "desk", "share": "share",
}


def _grammar_kind(parent_parts: list):
	shape = "/".join("#" if p.isdigit() else p for p in parent_parts)
	return _KIND_BY_SHAPE.get(shape)


# ---------------------------------------------------------------- safe fixes


def _slug(val, used: set) -> str:
	s = re.sub(r"[^a-z0-9_]+", "_", str(val).strip().lower())
	s = re.sub(r"_+", "_", s).strip("_")
	if not s or not s[0].isalpha():
		s = "x_" + s if s else "x_id"
	s = s.rstrip("_")
	if len(s) < 2:
		s = s + "_1"
	s = s[:40].rstrip("_") or "x_id"
	cand, n = s, 2
	while cand in used or not ID_RE.match(cand):
		suf = f"_{n}"
		cand = s[: 40 - len(suf)].rstrip("_") + suf
		n += 1
	return cand


def _ref_ops(spec: dict, kind: str, old: str, new: str) -> list:
	"""Patch ops that re-point every reference to id `old` (of `kind`: queries|charts) to `new`."""
	ops = []
	if kind == "queries":
		for i, q in enumerate(spec.get("queries") or []):
			if isinstance(q, dict) and q.get("source_query") == old:
				ops.append({"op": "replace", "path": _ptr("queries", i, "source_query"), "value": new})
		for i, c in enumerate(spec.get("charts") or []):
			if isinstance(c, dict) and c.get("query") == old:
				ops.append({"op": "replace", "path": _ptr("charts", i, "query"), "value": new})
		for di, d in enumerate(spec.get("dashboards") or []):
			for fi, f in enumerate(d.get("filters") or [] if isinstance(d, dict) else []):
				at = f.get("applies_to") if isinstance(f, dict) else None
				if isinstance(at, dict) and old in at:
					p = _ptr("dashboards", di, "filters", fi, "applies_to")
					ops.append({"op": "add", "path": p + "/" + _esc(new), "value": at[old]})
					ops.append({"op": "remove", "path": p + "/" + _esc(old)})
	elif kind == "charts":
		for di, d in enumerate(spec.get("dashboards") or []):
			for ii, it in enumerate(d.get("items") or [] if isinstance(d, dict) else []):
				if isinstance(it, dict) and it.get("chart") == old:
					ops.append({"op": "replace", "path": _ptr("dashboards", di, "items", ii, "chart"), "value": new})
	return ops


def _fix_l03(spec: dict, findings: list) -> list:
	used = set()
	for _, _, v in _all_ids(spec):
		if isinstance(v, str) and ID_RE.match(v):
			used.add(v)
	first_seen = {}  # (kind, old) -> True once references were claimed
	out = []
	for f in findings:
		path = f["path"]
		val = get_at(spec, path)
		if not isinstance(val, (str, int)) or isinstance(val, bool):
			continue
		new = _slug(val, used)
		dup = isinstance(val, str) and ID_RE.match(val) is not None
		used.add(new)
		parts = _split(path)
		kind = parts[0] if parts and parts[0] in ("queries", "charts") else None
		ops = [{"op": "replace", "path": path, "value": new}]
		if kind and not dup and (kind, val) not in first_seen:
			first_seen[(kind, val)] = True
			ops += _ref_ops(spec, kind, val, new)
		out.append(_fix("L03", path, True, f"rename id `{val}` -> `{new}`" + (" (references rewritten)" if len(ops) > 1 else ""), ops))
	return out


def _all_ids(spec):
	res = []
	wb = spec.get("workbook")
	if isinstance(wb, dict) and "id" in wb:
		res.append(("workbook", "/workbook/id", wb["id"]))
	for k in ("queries", "charts", "dashboards"):
		for i, o in enumerate(spec.get(k) or []):
			if isinstance(o, dict):
				res.append((k, _ptr(k, i, "id"), o.get("id")))
				for fi, f in enumerate(o.get("filters") or [] if k == "dashboards" else []):
					if isinstance(f, dict):
						res.append(("filters", _ptr(k, i, "filters", fi, "id"), f.get("id")))
	return res


def _fix_l04(spec: dict, findings: list) -> list:
	used = {}
	for kind in ("queries", "charts", "dashboards"):
		used[kind] = {o.get("title") for o in spec.get(kind) or [] if isinstance(o, dict)}
	out = []
	for f in findings:
		parts = _split(f["path"])
		title = get_at(spec, f["path"])
		if len(parts) != 3 or parts[0] not in used or not isinstance(title, str):
			continue
		n = 2
		while f"{title} ({n})" in used[parts[0]]:
			n += 1
		new = f"{title} ({n})"
		used[parts[0]].add(new)
		out.append(_fix("L04", f["path"], True, f"rename duplicate title `{title}` -> `{new}`",
			[{"op": "replace", "path": f["path"], "value": new}]))
	return out


def _fix_l09(spec: dict, f: dict):
	val = get_at(spec, f["path"])
	try:
		new = norm_chart_type(val)
	except Exception:
		return None
	if new == val:
		return None
	return _fix("L09", f["path"], True, f"chart type `{val}` -> `{new}`", [{"op": "replace", "path": f["path"], "value": new}])


def _isint(v) -> bool:
	return isinstance(v, int) and not isinstance(v, bool)


def _fix_l11(spec: dict, f: dict):
	r = get_at(spec, f["path"])
	if not isinstance(r, dict) or not all(_isint(r.get(k)) for k in "xywh"):
		return None
	w = max(1, min(r["w"], GRID_COLS))
	x = max(0, min(r["x"], GRID_COLS - w))
	y = max(0, r["y"])
	h = max(1, r["h"])
	ops = [{"op": "replace", "path": f["path"] + "/" + k, "value": v}
		for k, v in (("x", x), ("y", y), ("w", w), ("h", h)) if r[k] != v]
	if not ops:
		return None
	return _fix("L11", f["path"], True, f"clamp layout into the {GRID_COLS}-column grid: x={x} y={y} w={w} h={h}", ops)


def _hit(a, b) -> bool:
	return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _fix_l12(spec: dict, dpath: str):
	d = get_at(spec, dpath)
	if not isinstance(d, dict):
		return None
	placed = []  # (x, y, w, h)
	auto = 0
	for f in d.get("filters") or []:
		if not isinstance(f, dict):
			continue
		lay = f.get("layout")
		if isinstance(lay, dict) and all(_isint(lay.get(k)) for k in "xywh"):
			placed.append((lay["x"], lay["y"], lay["w"], lay["h"]))
		elif not isinstance(lay, dict):
			placed.append(((auto * 4) % GRID_COLS, (auto * 4) // GRID_COLS, 4, 1))
			auto += 1
	ops = []
	for ii, it in enumerate(d.get("items") or []):
		if not isinstance(it, dict) or not all(_isint(it.get(k)) for k in "xywh"):
			continue
		x, y, w, h = (it[k] for k in "xywh")
		if x < 0 or y < 0 or w < 1 or h < 1 or x + w > GRID_COLS:
			continue
		if any(_hit((x, y, w, h), p) for p in placed):
			ny, nx = y, x
			found = False
			while not found:
				for cx in range(0, GRID_COLS - w + 1):
					if not any(_hit((cx, ny, w, h), p) for p in placed):
						nx, found = cx, True
						break
				if not found:
					ny += 1
			x, y = nx, ny
			ip = dpath + "/items/" + str(ii)
			if it["x"] != x:
				ops.append({"op": "replace", "path": ip + "/x", "value": x})
			if it["y"] != y:
				ops.append({"op": "replace", "path": ip + "/y", "value": y})
		placed.append((x, y, w, h))
	if not ops:
		return None
	return _fix("L12", dpath, True, f"reflow overlapping items of dashboard `{d.get('id')}` top-down (order and sizes kept)", ops)


def _fix_l01(spec: dict, f: dict):
	if "(unknown)" not in f["message"]:
		return None
	parts = _split(f["path"])
	if not parts:
		return None
	kind = _grammar_kind(parts[:-1])
	obj = get_at(spec, _ptr(*parts[:-1]) if parts[:-1] else "")
	if kind not in G or not isinstance(obj, dict):
		return None
	req, opt = G[kind]
	bad = parts[-1]
	cands = [k for k in tuple(req) + tuple(opt) if _lev(bad, k) <= 2 and k != bad]
	if len(cands) != 1 or cands[0] in obj:
		return None
	new = cands[0]
	base = f["path"][: f["path"].rfind("/")]
	return _fix("L01", f["path"], True, f"rename unknown key `{bad}` -> `{new}`",
		[{"op": "add", "path": base + "/" + _esc(new), "value": obj[bad]}, {"op": "remove", "path": f["path"]}])


# ---------------------------------------------------------------- unsafe proposals


def _query_cols(spec: dict, catalog: dict, qi: int) -> list:
	q = (spec.get("queries") or [])[qi]
	qmap = {x.get("id"): x for x in spec.get("queries") or [] if isinstance(x, dict)}
	pool = []
	if q.get("table") in catalog:
		pool += list(catalog[q["table"]])
	if isinstance(q.get("source_query"), str) and q["source_query"] in qmap:
		try:
			pool += list(output_schema(qmap[q["source_query"]], catalog, qmap))
		except Exception:
			pass
	for j in q.get("joins") or []:
		if isinstance(j, dict) and j.get("table") in catalog:
			pool += list(catalog[j["table"]])
	return pool


def _alts_replace(path, names, what) -> list:
	return [{"description": f"use {what} `{n}`", "patch": [{"op": "replace", "path": path, "value": n}]} for n in names]


def _col_target(spec, path):
	"""(pointer of the string to replace, current column name) for a path that is a column str or a {column} map."""
	v = get_at(spec, path)
	if isinstance(v, str):
		return path, v
	if isinstance(v, dict) and isinstance(v.get("column"), str):
		return path + "/column", v["column"]
	return None, None


def _unsafe(spec: dict, catalog, f: dict):
	rule, path = f["rule"], f["path"]
	parts = _split(path)
	desc = f["message"]
	if rule in ("L05", "L06", "L08", "L07", "L13") and path == "/catalog":
		return None
	if rule == "L05":
		if catalog is None:
			return None
		v = get_at(spec, path)
		if parts and parts[-1] == "source_query":
			pool = [q.get("id") for q in spec.get("queries") or [] if isinstance(q, dict)]
			what = "query"
		else:
			pool, what = list(catalog), "table"
		return _fix("L05", path, False, desc, alternatives=_alts_replace(path, _close(v, pool), what))
	if rule == "L06":
		if catalog is None or len(parts) < 2 or parts[0] != "queries" or not parts[1].isdigit():
			return None
		tp, name = _col_target(spec, path)
		if tp is None:
			return None
		try:
			pool = _query_cols(spec, catalog, int(parts[1]))
		except Exception:
			pool = []
		return _fix("L06", path, False, desc, alternatives=_alts_replace(tp, _close(name, pool), "column"))
	if rule == "L08":
		if catalog is None or len(parts) < 2 or parts[0] != "charts" or not parts[1].isdigit():
			return None
		c = get_at(spec, _ptr(*parts[:2]))
		qmap = {x.get("id"): x for x in spec.get("queries") or [] if isinstance(x, dict)}
		pool = []
		if isinstance(c, dict) and c.get("query") in qmap:
			try:
				pool = list(output_schema(qmap[c["query"]], catalog, qmap))
			except Exception:
				pool = []
		tp, name = _col_target(spec, path)
		if tp is None:
			return None
		return _fix("L08", path, False, desc, alternatives=_alts_replace(tp, _close(name, pool), "column"))
	if rule == "L07":
		pool = [q.get("id") for q in spec.get("queries") or [] if isinstance(q, dict)]
		return _fix("L07", path, False, desc, alternatives=_alts_replace(path, _close(get_at(spec, path), pool), "query"))
	if rule == "L13":
		alts = []
		if len(parts) >= 2 and not (catalog is None):
			qid, col = parts[-1], get_at(spec, path)
			qmap = {x.get("id"): x for x in spec.get("queries") or [] if isinstance(x, dict)}
			if qid in qmap and isinstance(col, str):
				try:
					pool = list(output_schema(qmap[qid], catalog, qmap))
				except Exception:
					pool = []
				near = difflib.get_close_matches(col, sorted(pool), n=1, cutoff=0.6)
				alts += _alts_replace(path, near, "column")
		alts.append({"description": "remove this applies_to entry (filter no longer applies to that query)",
			"patch": [{"op": "remove", "path": path}]})
		return _fix("L13", path, False, desc, alternatives=alts)
	if rule == "L20":
		agg = path.rsplit("/", 1)[0] + "/agg"
		alts = [{"description": f"use agg `{a}` (changes meaning)", "patch": [{"op": "add", "path": agg, "value": a}]}
			for a in ("count", "count_distinct")]
		return _fix("L20", path, False, desc, alternatives=alts)
	if rule in ("L10", "L02", "L16"):
		return _fix(rule, path, False, desc)
	return None


# ---------------------------------------------------------------- public API


def suggest(findings, spec, catalog=None) -> list:
	"""Fix objects for `findings` (Finding or dict) against `spec`. Order: safe first, in finding order."""
	if not isinstance(spec, dict):
		return []
	fs = [_fd(f) for f in findings]
	fixes: list = []
	fixes += _fix_l03(spec, [f for f in fs if f["rule"] == "L03"])
	fixes += _fix_l04(spec, [f for f in fs if f["rule"] == "L04"])
	seen_dash = set()
	for f in fs:
		fx = None
		if f["rule"] == "L09":
			fx = _fix_l09(spec, f)
		elif f["rule"] == "L11":
			fx = _fix_l11(spec, f)
		elif f["rule"] == "L12":
			dp = _ptr(*_split(f["path"])[:2])
			if dp not in seen_dash:
				seen_dash.add(dp)
				fx = _fix_l12(spec, dp)
		elif f["rule"] == "L01":
			fx = _fix_l01(spec, f)
		if fx:
			fixes.append(fx)
	seen = set()
	for f in fs:
		fx = _unsafe(spec, catalog, f)
		if fx:
			k = (fx["rule"], fx["path"])
			if k not in seen:
				seen.add(k)
				fixes.append(fx)
	return fixes


def _lint(spec, catalog) -> list:
	findings = lint_spec(spec, catalog)
	if not any(f.severity == "error" for f in findings):
		try:
			from .compile import compile_spec
			_, cf = compile_spec(copy.deepcopy(spec), catalog)
			known = {(f.rule, f.path, f.message) for f in findings}
			findings += [f for f in cf if (f.rule, f.path, f.message) not in known]
		except Exception:
			pass
	return findings


def heal_spec(spec, catalog=None, *, max_rounds=3, allow="safe") -> dict:
	"""Offline lint -> apply safe fixes -> re-lint, at most `max_rounds` rounds; stops when a round changes nothing."""
	cur = copy.deepcopy(spec)
	applied: list = []
	rounds = 0
	if allow == "safe" and isinstance(cur, dict):
		for _ in range(max(0, int(max_rounds))):
			fixes = [f for f in suggest(_lint(cur, catalog), cur, catalog) if f["safe"]]
			changed = False
			for fx in fixes:
				try:
					nxt = apply_patch(cur, fx["patch"])
				except ValueError:
					continue
				if nxt != cur:
					cur, changed = nxt, True
					applied.append(fx)
			if not changed:
				break
			rounds += 1
	findings = _lint(cur, catalog)
	fixes = suggest(findings, cur, catalog)
	proposed = [f for f in fixes if not f["safe"]] if allow == "safe" else fixes
	remaining = [f.to_dict() for f in findings if f.severity == "error"]
	return {"spec": cur, "rounds": rounds, "applied": applied, "proposed": proposed, "remaining": remaining}
