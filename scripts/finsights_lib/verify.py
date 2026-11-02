"""Read-back verification of a compiled workbook against a live site."""

from __future__ import annotations

import copy
import json
import re

from .apply import DOCTYPES, KIND_OF, plan_apply
from .common import Finding, FinsightsError


def _linked_names(rows) -> set:
	out = set()
	for r in rows or []:
		if isinstance(r, str):
			out.add(r)
		elif isinstance(r, dict):
			v = r.get("chart") or r.get("name")
			if v:
				out.add(v)
	return out


_LINK = re.compile(r"^`([^`]+)`\.`([^`]+)`$")


def _run_cached(client, ran, qlive):
	if qlive not in ran:
		try:
			out = client.run_query(qlive)
			ran[qlive] = ("ok", len(out.get("rows") or []), [c.get("name") for c in out.get("columns") or [] if isinstance(c, dict)])
		except FinsightsError as e:
			ran[qlive] = ("err", str(e), [])
	return ran[qlive]


def _check_links(client, live, dpath, ran):
	out = []
	items = live.get("items") or []
	chart_live = {i.get("chart") for i in items if i.get("type") == "chart" and i.get("chart")}
	for it in items:
		if it.get("type") != "filter":
			continue
		fp = f"{dpath}/filters/{it.get('filter_name') or it.get('layout', {}).get('i', '?')}"
		links = it.get("links")
		if not links or not isinstance(links, dict):
			out.append(Finding("V05", "warn", fp, "filter has no links; it binds to no chart",
				"add links in the spec: chart -> `query`.`column`"))
			continue
		cq = {}
		for ck, ref in links.items():
			cp = f"{fp}/links/{ck}"
			if ck not in chart_live:
				out.append(Finding("V05", "error", cp, f"link target chart {ck!r} is not a chart item on this dashboard",
					"link only charts placed on the dashboard"))
				continue
			m = _LINK.match(ref) if isinstance(ref, str) else None
			if not m:
				out.append(Finding("V05", "error", cp, f"malformed link value {ref!r}",
					"use the format `<query>`.`<column>`"))
				continue
			qn, col = m.groups()
			if ck not in cq:
				try:
					cq[ck] = client.get_doc(DOCTYPES["chart"], ck).get("query")
				except FinsightsError:
					cq[ck] = None
			if qn != cq[ck]:
				out.append(Finding("V05", "error", cp, f"link query {qn!r} != chart query {cq[ck]!r}",
					"link to a column of the query the chart uses"))
				continue
			state, _, cols = _run_cached(client, ran, qn)
			if state == "err":
				continue  # reported once as V03
			if col not in cols:
				out.append(Finding("V05", "error", cp, f"column {col!r} not in result columns {sorted(cols)} of query {qn!r}",
					"fix the column name or select it in the query"))
	return out


# ---------------------------------------------------------------------------------------------------------
# Deep mode: rebuild each chart's client-side "data query" and execute it.
# Port of the Insights frontend (apps/insights/frontend/src2/charts/chart.ts: refresh(), addSourceOperation,
# addFilterOperation, addChartOperation, addAxisChartOperation, addNumberChartOperation,
# addDonutChartOperation, addTableChartOperation, addMapChartOperation, addBubbleChartOperation,
# addOrderByOperation, validateConfig; and query/helpers.ts: query_table, column, filter_group, summarize,
# pivot_wider, order_by, count; query/query.ts addOrderBy dedup). Sankey has no operation builder there and is
# not ported (skipped).
# ---------------------------------------------------------------------------------------------------------

AXIS_CHARTS = ("Bar", "Line", "Row")
MAX_DEEP_QUERIES = 40
DEEP_PAGE_SIZE = 5


def _count_measure() -> dict:
	return {"column_name": "count", "data_type": "Integer", "aggregation": "count", "measure_name": "count_of_rows"}


def _add_order_by(ops: list, column_name: str, direction: str) -> None:
	# query.ts addOrderBy: skip identical, replace same-column in place, else append
	for op in ops:
		if op.get("type") == "order_by" and op["column"]["column_name"] == column_name and op.get("direction") == direction:
			return
	new = {"type": "order_by", "column": {"type": "column", "column_name": column_name}, "direction": direction}
	for i, op in enumerate(ops):
		if op.get("type") == "order_by" and op["column"]["column_name"] == column_name:
			ops[i] = new
			return
	ops.append(new)


def chart_operations(chart_doc: dict, source_doc: dict | None = None) -> list:
	"""Operations of the chart's data query, as the frontend builds them. [] when the config is invalid
	(the frontend does not execute then) or the chart type has no builder."""
	cfg = chart_doc.get("config") or {}
	if isinstance(cfg, str):
		try:
			cfg = json.loads(cfg)
		except ValueError:
			return []
	ctype = chart_doc.get("chart_type")
	qname = chart_doc.get("query") or (source_doc or {}).get("name") or ""
	if not qname or not ctype:
		return []
	ops = [{"type": "source", "table": {"type": "query", "workbook": "", "query_name": qname}}]
	flt = cfg.get("filters") or {}
	if flt.get("filters"):
		ops.append({"type": "filter_group", **copy.deepcopy(flt)})
	summ = lambda ms, ds: {"type": "summarize", "measures": copy.deepcopy(ms), "dimensions": copy.deepcopy(ds)}
	pivot = lambda rows, cols, vals, mx: {"type": "pivot_wider", "rows": copy.deepcopy(rows),
		"columns": copy.deepcopy(cols), "values": copy.deepcopy(vals), "max_column_values": mx}
	if ctype in AXIS_CHARTS:
		x = (cfg.get("x_axis") or {}).get("dimension")
		sb = cfg.get("split_by") or {}
		if not x or not x.get("column_name") or x.get("column_name") == (sb.get("dimension") or {}).get("column_name"):
			return []
		vals = [s.get("measure") for s in (cfg.get("y_axis") or {}).get("series") or []
			if (s.get("measure") or {}).get("measure_name")]
		vals = vals or [_count_measure()]
		if (sb.get("dimension") or {}).get("column_name"):
			ops.append(pivot([x], [sb["dimension"]], vals, sb.get("max_split_values") or 10))
		else:
			ops.append(summ(vals, [x]))
	elif ctype == "Number":
		meas = [m for m in cfg.get("number_columns") or [] if m.get("measure_name")]
		if not meas:
			return []
		dc = cfg.get("date_column") or {}
		ops.append(summ(meas, [dc] if dc.get("column_name") else []))
	elif ctype in ("Donut", "Funnel"):
		lab, val = cfg.get("label_column") or {}, cfg.get("value_column") or {}
		if not lab.get("column_name") or not val.get("measure_name"):
			return []
		ops.append(summ([val], [lab]))
		_add_order_by(ops, val["measure_name"], "desc")
	elif ctype == "Table":
		rows = [r for r in cfg.get("rows") or [] if r.get("column_name")]
		cols = [c for c in cfg.get("columns") or [] if c.get("column_name")]
		vals = [v for v in cfg.get("values") or [] if v.get("measure_name")]
		if not rows:
			return []
		if cols:
			ops.append(pivot(rows, cols, vals, cfg.get("max_column_values") or 10))
		else:
			ops.append(summ(vals, rows))
	elif ctype == "Map":
		loc, val = cfg.get("location_column") or {}, cfg.get("value_column") or {}
		if not loc.get("column_name") or not val.get("measure_name"):
			return []
		ops.append(summ([val], [loc]))
	elif ctype == "Bubble":
		xa, ya, sz = cfg.get("xAxis") or {}, cfg.get("yAxis") or {}, cfg.get("size_column") or {}
		if not xa.get("measure_name") or not ya.get("measure_name"):
			return []
		meas = [m for m in (xa, ya, sz) if m.get("measure_name")]
		dims = [d for d in (cfg.get("dimension") or {}, cfg.get("quadrant_column") or {}) if d.get("column_name")]
		ops.append(summ(meas, dims))
	else:
		return []
	for srt in cfg.get("order_by") or []:
		cn = (srt.get("column") or {}).get("column_name")
		if cn and srt.get("direction"):
			_add_order_by(ops, cn, srt["direction"])
	return ops


_FUNC_RE = re.compile(r"(?:name|function|attribute)[^'\"`]*['\"`]([A-Za-z_][\w]*)['\"`]", re.I)
_FUNC_HINTS = {"count_distinct": "distinct_count"}


def _split_error(msg: str) -> tuple:
	"""'... server error (ExcType | first line)' -> (ExcType, first line, no traceback)."""
	m = re.search(r"\(([^()]*)\)\s*$", msg)
	inner = m.group(1) if m else msg
	parts = [p.strip() for p in inner.split(" | ", 1)]
	if len(parts) == 2:
		etype, text = parts
	elif m and re.fullmatch(r"[\w.]+", parts[0]):
		etype, text = parts[0], ""
	else:
		etype, text = "", parts[0]
	mm = re.match(r"^([\w.]+(?:Error|Exception)):\s*(.*)$", text, re.S)
	if mm:
		etype, text = mm.group(1), mm.group(2)
	text = re.split(r"\s*Traceback \(most recent call last\)", text.splitlines()[0] if text else "")[0].strip()
	etype = etype.rsplit(".", 1)[-1]
	return etype, text[:300]


def _fix_hint(text: str) -> str:
	low = text.lower()
	for bad, good in _FUNC_HINTS.items():
		if bad in low:
			return f"{bad} is not an Insights function: use {good}(...)"
	m = _FUNC_RE.search(text)
	if m and ("not defined" in low or "unknown" in low or "no function" in low or "nameerror" in low):
		return f"unknown function {m.group(1)!r}: check the Insights expression function list"
	return ""


def _run_ops(client, source_doc: dict, ops: list, page_size: int) -> dict:
	doc = copy.deepcopy(source_doc)
	doc["operations"] = ops
	doc["name"] = "new-query-finsights-deep"  # adhoc like the frontend; same name as the source = circular reference
	r = client.post("/api/method/insights.api.run_doc_method",
		{"method": "execute", "docs": doc, "args": {"force": 1, "page": 1, "page_size": page_size}})
	return r.get("message") or {}


def _deep_check(client, compiled, nm, ran, max_queries) -> list:
	out = []
	budget = max_queries
	src_docs = {}
	for ckey, chart in (compiled.get("dependencies", {}).get("charts") or {}).items():
		clive, qlive = nm.get(ckey), nm.get(chart.get("query"))
		if not clive or not qlive:
			continue
		path = f"/charts/{ckey}"
		if budget <= 0:
			out.append(Finding("V06", "info", path, f"deep check skipped: cap of {max_queries} chart queries reached"))
			continue
		try:
			cdoc = client.get_doc(DOCTYPES["chart"], clive)
			if qlive not in src_docs:
				src_docs[qlive] = client.get_doc("Insights Query v3", qlive)
		except FinsightsError as e:
			out.append(Finding("V06", "error", path, f"chart data query failed: could not read chart/query: {e}"))
			continue
		ops = chart_operations(cdoc, src_docs[qlive])
		if not ops:
			continue
		budget -= 1
		title, ctype = cdoc.get("title") or ckey, cdoc.get("chart_type")
		try:
			res = _run_ops(client, src_docs[qlive], ops, DEEP_PAGE_SIZE)
		except FinsightsError as e:
			etype, text = _split_error(str(e))
			msg = f"chart data query failed: {title!r} ({ctype}): {etype or 'error'}: {text}".rstrip(": ")
			out.append(Finding("V06", "error", path, msg, _fix_hint(text)))
			continue
		if not (res.get("rows") or []):
			state, nrows, _ = _run_cached(client, ran, qlive)
			if state == "ok" and nrows > 0:
				out.append(Finding("V07", "warn", path,
					f"chart data query returned 0 rows: {title!r} ({ctype}) although its source query has rows",
					"check chart filters and dimensions"))
	return out


def verify(compiled: dict, client, min_rows: int = 1, deep: bool = False, max_deep: int = MAX_DEEP_QUERIES) -> list:
	findings = []
	plan = plan_apply(compiled, client, adopt=True)
	deps = compiled.get("dependencies", {})
	if plan.workbook_live_name is None:
		return [Finding("V01", "error", "/workbook", "workbook not found on site", "run apply --write")]
	for a in plan.actions:
		if a.op in ("create", "update") and a.doctype != DOCTYPES["workbook"]:
			kind = KIND_OF[a.doctype]
			what = "missing on site" if a.op == "create" else "drift in " + ",".join(sorted(a.fields))
			findings.append(Finding("V01", "error", f"/{kind}s/{a.key}", f"{kind} {a.title!r}: {what}",
				"run apply --write"))
	nm = plan.name_map
	# V02/V03: run each chart's source query once
	ran = {}
	for ckey, chart in (deps.get("charts") or {}).items():
		qlive = nm.get(chart["query"])
		if not qlive:
			continue
		state, val, _ = _run_cached(client, ran, qlive)
		if state == "err":
			findings.append(Finding("V03", "error", f"/charts/{ckey}", f"query {qlive} failed: {val}"))
		elif val < min_rows:
			findings.append(Finding("V02", "error", f"/charts/{ckey}",
				f"query {qlive} returned {val} rows (< {min_rows})"))
	# V04: linked_charts == chart items
	for dkey, dash in (deps.get("dashboards") or {}).items():
		dlive = nm.get(dkey)
		if not dlive:
			continue
		live = client.get_doc(DOCTYPES["dashboard"], dlive)
		want = {nm.get(i["chart"]) for i in dash.get("items") or [] if i.get("type") == "chart"}
		got = _linked_names(live.get("linked_charts"))
		if want != got:
			findings.append(Finding("V04", "error", f"/dashboards/{dkey}",
				f"linked_charts {sorted(got)} != chart items {sorted(x for x in want if x)}"))
		findings.extend(_check_links(client, live, f"/dashboards/{dkey}", ran))
	if deep:
		findings.extend(_deep_check(client, compiled, nm, ran, max_deep))
	return findings
