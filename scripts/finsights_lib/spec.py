"""Spec loading and structural lint (L01 shape, L03 ids, L04 duplicate titles)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .common import Finding, FinsightsError, SpecParseError
from . import yamlish

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

# grammar: key -> (required, optional)
G = {
	"top": (("workbook", "queries"), ("catalog", "charts", "dashboards", "desk", "share")),
	"workbook": (("id", "title"), ("module", "description", "required_apps", "version")),
	"query": (("id", "title"), ("table", "source_query", "sql", "joins", "filters", "mutate", "select",
		"summarize", "order_by", "limit", "live")),
	"join": (("table", "on"), ("type", "columns")),
	"qfilter": ((), ("column", "op", "value", "expr", "any")),
	"mutate": (("as", "type", "expr"), ()),
	"select": (("column",), ("optional",)),
	"summarize": (("measures",), ("by",)),
	"by": (("column",), ("granularity", "as")),
	"measure": (("agg",), ("column", "expr", "as", "type", "format", "currency_column")),
	"order_by": (("column",), ("dir",)),
	"chart": (("id", "title", "query", "type"), ("x", "y", "split_by", "rows", "columns", "date", "comparison",
		"sparkline", "stack", "smooth", "show_data_labels", "legend", "max_slices", "map_type", "order",
		"limit", "filters")),
	"x": (("column",), ("granularity", "as")),
	"y": ((), ("column", "expr", "agg", "as", "format", "currency_column")),
	"split_by": (("column",), ("max",)),
	"date": (("column",), ("granularity",)),
	"order": (("by",), ("dir",)),
	"cfilter": (("column", "op"), ("value",)),
	"dashboard": (("id", "title", "items"), ("filters",)),
	"dfilter": (("id", "label", "type", "applies_to"), ("default", "icon", "layout")),
	"default": (("op",), ("value",)),
	"layout": (("x", "y", "w", "h"), ()),
	"item": (("x", "y", "w", "h"), ("chart", "text")),
	"desk": (("workspace", "label"), ("sidebar", "dock")),
	"share": (("team",), ()),
}


def load_spec(path) -> dict:
	"""Load a spec from .json or .yaml/.yml; SpecParseError on failure."""
	p = Path(path)
	try:
		text = p.read_text(encoding="utf-8")
	except OSError as e:
		raise FinsightsError(f"cannot read spec {path}: {e}", 2)
	ext = p.suffix.lower()
	if ext == ".json":
		try:
			data = json.loads(text)
		except json.JSONDecodeError as e:
			raise SpecParseError(e.msg, e.lineno, e.colno)
	elif ext in (".yaml", ".yml"):
		data = yamlish.loads(text)
	else:
		raise FinsightsError(f"unsupported spec extension {ext!r} (use .json, .yaml or .yml)", 2)
	if not isinstance(data, dict):
		raise SpecParseError("spec top level must be a mapping", 1, 1)
	return data


def _ptr(base: str, *parts) -> str:
	return base + "".join("/" + str(p) for p in parts)


def _check(obj, kind: str, path: str, out: list, extra_ok=()):
	"""L01 on a mapping against grammar `kind`. Returns True when obj is a dict."""
	if not isinstance(obj, dict):
		out.append(Finding("L01", "error", path or "/", f"expected a mapping at {path or '/'}", "use `key: value` entries"))
		return False
	req, opt = G[kind]
	for k in req:
		if k not in obj:
			out.append(Finding("L01", "error", _ptr(path, k), f"unknown or missing required key `{k}`", f"add `{k}`"))
	for k in obj:
		if k not in req and k not in opt and k not in extra_ok:
			out.append(Finding("L01", "error", _ptr(path, k), f"unknown or missing required key `{k}` (unknown)",
				"allowed: " + ", ".join(sorted(set(req) | set(opt)))))
	return True


def _list(obj, key, path, out):
	v = obj.get(key)
	if v is None:
		return []
	if not isinstance(v, list):
		out.append(Finding("L01", "error", _ptr(path, key), f"`{key}` must be a list", ""))
		return []
	return v


def _one_of(obj, keys, path, out):
	have = [k for k in keys if k in obj]
	if len(have) != 1:
		out.append(Finding("L01", "error", path, f"unknown or missing required key: exactly one of {'|'.join(keys)} (found {len(have)})", ""))


def _col_or_map(v, kind, path, out):
	if isinstance(v, str):
		return
	_check(v, kind, path, out)


def _filters(items, kind, path, out):
	for i, f in enumerate(items):
		p = _ptr(path, i)
		if _check(f, kind, p, out) and kind == "qfilter":
			if "expr" not in f:
				for k in ("column", "op"):
					if k not in f:
						out.append(Finding("L01", "error", _ptr(p, k), f"unknown or missing required key `{k}` (or use `expr`)", ""))


def shape_findings(spec) -> list:
	"""L01 + L03 + L04 over a parsed spec."""
	out: list = []
	if not _check(spec, "top", "", out):
		return out
	wb = spec.get("workbook")
	if "workbook" in spec:
		_check(wb, "workbook", "/workbook", out)
	ids = []  # (id, pointer)
	if isinstance(wb, dict) and "id" in wb:
		ids.append((wb["id"], "/workbook/id"))

	queries = _list(spec, "queries", "", out)
	for i, q in enumerate(queries):
		p = _ptr("", "queries", i)
		if not _check(q, "query", p, out):
			continue
		ids.append((q.get("id"), _ptr(p, "id")))
		_one_of(q, ("table", "source_query", "sql"), p, out)
		for j, jn in enumerate(_list(q, "joins", p, out)):
			_check(jn, "join", _ptr(p, "joins", j), out)
		_filters(_list(q, "filters", p, out), "qfilter", _ptr(p, "filters"), out)
		for j, m in enumerate(_list(q, "mutate", p, out)):
			_check(m, "mutate", _ptr(p, "mutate", j), out)
		for j, s in enumerate(_list(q, "select", p, out)):
			_col_or_map(s, "select", _ptr(p, "select", j), out)
		sm = q.get("summarize")
		if sm is not None and _check(sm, "summarize", _ptr(p, "summarize"), out):
			for j, b in enumerate(_list(sm, "by", _ptr(p, "summarize"), out)):
				_check(b, "by", _ptr(p, "summarize", "by", j), out)
			for j, m in enumerate(_list(sm, "measures", _ptr(p, "summarize"), out)):
				mp = _ptr(p, "summarize", "measures", j)
				if _check(m, "measure", mp, out) and "column" not in m and "expr" not in m and m.get("agg") != "count":
					out.append(Finding("L01", "error", mp, "unknown or missing required key: one of column|expr", ""))
		for j, o in enumerate(_list(q, "order_by", p, out)):
			_check(o, "order_by", _ptr(p, "order_by", j), out)

	charts = _list(spec, "charts", "", out)
	for i, c in enumerate(charts):
		p = _ptr("", "charts", i)
		if not _check(c, "chart", p, out):
			continue
		ids.append((c.get("id"), _ptr(p, "id")))
		if "x" in c:
			_col_or_map(c["x"], "x", _ptr(p, "x"), out)
		for j, y in enumerate(_list(c, "y", p, out)):
			if _check_y(y, _ptr(p, "y", j), out):
				pass
		if "split_by" in c:
			_check(c["split_by"], "split_by", _ptr(p, "split_by"), out)
		if "date" in c:
			_check(c["date"], "date", _ptr(p, "date"), out)
		for j, o in enumerate(_list(c, "order", p, out)):
			_check(o, "order", _ptr(p, "order", j), out)
		_filters(_list(c, "filters", p, out), "cfilter", _ptr(p, "filters"), out)

	dashboards = _list(spec, "dashboards", "", out)
	for i, d in enumerate(dashboards):
		p = _ptr("", "dashboards", i)
		if not _check(d, "dashboard", p, out):
			continue
		ids.append((d.get("id"), _ptr(p, "id")))
		for j, f in enumerate(_list(d, "filters", p, out)):
			fp = _ptr(p, "filters", j)
			if _check(f, "dfilter", fp, out):
				ids.append((f.get("id"), _ptr(fp, "id")))
				if "default" in f:
					_check(f["default"], "default", _ptr(fp, "default"), out)
				if "layout" in f:
					_check(f["layout"], "layout", _ptr(fp, "layout"), out)
				if "applies_to" in f and not isinstance(f["applies_to"], dict):
					out.append(Finding("L01", "error", _ptr(fp, "applies_to"), "`applies_to` must be a mapping {query_id: column}", ""))
		for j, it in enumerate(_list(d, "items", p, out)):
			ip = _ptr(p, "items", j)
			if _check(it, "item", ip, out):
				_one_of(it, ("chart", "text"), ip, out)

	if "desk" in spec:
		if _check(spec["desk"], "desk", "/desk", out) and isinstance(spec["desk"].get("sidebar"), dict):
			if "section" not in spec["desk"]["sidebar"]:
				out.append(Finding("L01", "error", "/desk/sidebar/section", "unknown or missing required key `section`", ""))
	if "share" in spec:
		_check(spec["share"], "share", "/share", out)

	# L03 ids
	seen = {}
	for val, ptr in ids:
		if not isinstance(val, str) or not ID_RE.match(val):
			out.append(Finding("L03", "error", ptr, f"id `{val}` not ^[a-z][a-z0-9_]{{1,39}}$", "use lowercase letters, digits, underscore"))
			continue
		if val in seen:
			out.append(Finding("L03", "error", ptr, f"id `{val}` duplicated (first at {seen[val]})", "ids are unique across workbook/queries/charts/dashboards/filters"))
		else:
			seen[val] = ptr

	# L04 titles
	for kind, items in (("queries", queries), ("charts", charts), ("dashboards", dashboards)):
		titles = {}
		for i, it in enumerate(items):
			t = it.get("title") if isinstance(it, dict) else None
			if not isinstance(t, str):
				continue
			if t in titles:
				out.append(Finding("L04", "error", _ptr("", kind, i, "title"), f"duplicate title `{t}` within {kind}", "title is the live upsert key; make it unique"))
			else:
				titles[t] = i
	return out


def _check_y(y, path, out):
	if isinstance(y, str):
		return True
	if not _check(y, "y", path, out):
		return False
	if "column" not in y and "expr" not in y:
		out.append(Finding("L01", "error", path, "unknown or missing required key: one of column|expr", ""))
	return True
