"""Faithful read-back of a live workbook (export) and a health report (review)."""

from __future__ import annotations

import json
import re

from .apply import DEP_KEY, DOCTYPES, FIELDS, JSON_FIELDS, _is_empty_helper
from .common import Finding, FinsightsError
from .compile import kebab
from .verify import _check_links, _linked_names, _run_cached

SERVER_FIELDS = {"data_query", "linked_charts", "creation", "modified", "modified_by", "owner",
	"idx", "docstatus", "doctype", "_user_tags", "_comments", "_assign", "_liked_by"}
KINDS = ("query", "chart", "dashboard")
_WB_FIELDS = ["name", "title", "from_template", "modified"]


def _err(e) -> dict:
	return {"ok": False, "error": {"exit_code": getattr(e, "exit_code", 2), "message": str(e)}}


def _marker_id(ref: str) -> str:
	return ref[len("finsights:"):] if ref.startswith("finsights:") else ref


def _resolve(client, ref) -> dict:
	"""Return the live workbook row for ref; raise FinsightsError on unknown/ambiguous."""
	if not isinstance(ref, str) or not ref.strip():
		raise FinsightsError("workbook reference is required", 2)
	ref = ref.strip()
	rows = client.list_docs(DOCTYPES["workbook"], fields=_WB_FIELDS)
	# The marker is written from the kebab-cased spec workbook id (compile.kebab), so accept the spec id
	# as typed (`ury_heal_demo`), the kebab form (`ury-heal-demo`) and the full marker.
	# Only id-looking refs (lowercase, no spaces) are normalised: a title such as "URY Sales" must NOT
	# silently match the marker of spec id `ury_sales`; it stays a title lookup (and may be ambiguous).
	bare = _marker_id(ref)
	mks = {"finsights:" + bare}
	if re.fullmatch(r"[a-z][a-z0-9_-]*", bare):
		mks.add("finsights:" + kebab(bare))
	stages = (
		("name", [r for r in rows if r.get("name") == ref]),
		("marker", [r for r in rows if r.get("from_template") in mks]),
		("title", [r for r in rows if r.get("title") == ref]),
	)
	for what, hits in stages:
		if len(hits) == 1:
			return hits[0]
		if len(hits) > 1:
			cands = ", ".join(f"{r['name']} ({r.get('title')!r}, {r.get('from_template') or 'no marker'})" for r in hits)
			raise FinsightsError(f"ambiguous: {len(hits)} workbooks match {what} {ref!r}: {cands}", 3)
	raise FinsightsError(f"no workbook matches {ref!r} by name, marker or title", 2)


def _marker_of(row) -> str | None:
	m = row.get("from_template")
	return m if isinstance(m, str) and m.startswith("finsights:") else None


def _list_kind(client, kind, wb_live):
	flds = ["name", "title"] + {"chart": ["data_query"], "query": ["operations"]}.get(kind, [])
	return client.list_docs(DOCTYPES[kind], filters={"workbook": wb_live}, fields=flds)


def _user_queries(rows, charts):
	helpers = {r.get("data_query") for r in charts if r.get("data_query")}
	return [r for r in rows if r["name"] not in helpers and not _is_empty_helper(r)]


def _parse(doc):
	for f in JSON_FIELDS:
		v = doc.get(f)
		if isinstance(v, str) and v.strip():
			try:
				doc[f] = json.loads(v)
			except ValueError:
				pass
	return doc


def _keep(kind, doc, lossy):
	allowed = set(FIELDS[kind]) | {"name", "workbook"}
	out = {}
	for k in sorted(doc):
		v = doc[k]
		if k in SERVER_FIELDS:
			continue
		if k in allowed:
			out[k] = v
			if k in JSON_FIELDS and isinstance(v, str) and v.strip():
				lossy.append(f"{kind} {doc.get('title')!r}: field {k} is not valid JSON; kept as string")
		elif v not in (None, "", [], {}, 0):
			lossy.append(f"{kind} {doc.get('title')!r}: field {k} is not representable in the envelope (dropped)")
	return out


def _count_filters(dashboards):
	return sum(1 for d in dashboards.values() for i in d.get("items") or [] if i.get("type") == "filter")


def export_workbook(client, ref) -> dict:
	try:
		wb = _resolve(client, ref)
		wl = wb["name"]
		charts_rows = _list_kind(client, "chart", wl)
		rows = {
			"chart": charts_rows,
			"query": _user_queries(_list_kind(client, "query", wl), charts_rows),
			"dashboard": _list_kind(client, "dashboard", wl),
		}
		lossy, deps, names = [], {}, {"workbook": {wb.get("title"): wl}}
		for kind in KINDS:
			docs = {}
			for r in sorted(rows[kind], key=lambda r: r["name"]):
				doc = _keep(kind, _parse(client.get_doc(DOCTYPES[kind], r["name"])), lossy)
				doc.setdefault("name", r["name"])
				docs[r["name"]] = doc
				names.setdefault(kind, {})[doc.get("title")] = r["name"]
			deps[DEP_KEY[kind]] = docs
			names.setdefault(kind, {})
		full = client.get_doc(DOCTYPES["workbook"], wl)
		for k, v in sorted(full.items()):
			if k not in SERVER_FIELDS | {"name", "title", "from_template"} and v not in (None, "", [], {}, 0):
				lossy.append(f"workbook {wb.get('title')!r}: field {k} is not representable in the envelope (dropped)")
		compiled = {
			"version": "1.0", "type": "Workbook", "name": wl,
			"doc": {"name": wl, "title": wb.get("title")},
			"dependencies": {"folders": [], **deps},
		}
		return {
			"ok": True,
			"workbook": {"name": wl, "title": wb.get("title"), "marker": _marker_of(wb), "modified": wb.get("modified")},
			"compiled": compiled,
			"summary": {"queries": len(deps["queries"]), "charts": len(deps["charts"]),
				"dashboards": len(deps["dashboards"]), "filters": _count_filters(deps["dashboards"])},
			"names": names, "lossy": lossy, "error": None,
		}
	except FinsightsError as e:
		return _err(e)


def export_all(client) -> dict:
	try:
		out = []
		for wb in sorted(client.list_docs(DOCTYPES["workbook"], fields=_WB_FIELDS), key=lambda r: r["name"]):
			wl = wb["name"]
			charts = _list_kind(client, "chart", wl)
			out.append({
				"name": wl, "title": wb.get("title"), "marker": _marker_of(wb),
				"queries": len(_user_queries(_list_kind(client, "query", wl), charts)),
				"charts": len(charts), "dashboards": len(_list_kind(client, "dashboard", wl)),
				"modified": wb.get("modified"),
			})
		return {"ok": True, "workbooks": out}
	except FinsightsError as e:
		return _err(e)


def review(client, ref, *, min_rows: int = 1) -> dict:
	try:
		wb = _resolve(client, ref)
		wl = wb["name"]
		findings, ran, chart_out, dash_out = [], {}, [], []
		charts = [client.get_doc(DOCTYPES["chart"], r["name"]) for r in sorted(_list_kind(client, "chart", wl), key=lambda r: r["name"])]
		for ch in charts:
			q = ch.get("query")
			item = {"title": ch.get("title"), "type": ch.get("chart_type"), "query": q, "rows": None, "ok": False, "error": None}
			path = f"/charts/{ch['name']}"
			if not q:
				item["error"] = "chart has no source query"
				findings.append(Finding("V03", "error", path, "chart has no source query"))
			else:
				state, val, _ = _run_cached(client, ran, q)
				if state == "err":
					item["error"] = str(val)
					findings.append(Finding("V03", "error", path, f"query {q} failed: {val}"))
				else:
					item["rows"] = val
					item["ok"] = val >= min_rows
					if not item["ok"]:
						item["error"] = f"query {q} returned {val} rows (< {min_rows})"
						findings.append(Finding("V02", "error", path, item["error"]))
			chart_out.append(item)
		for r in sorted(_list_kind(client, "dashboard", wl), key=lambda r: r["name"]):
			dl = r["name"]
			live = client.get_doc(DOCTYPES["dashboard"], dl)
			items = live.get("items") or []
			dpath = f"/dashboards/{dl}"
			want = {i.get("chart") for i in items if i.get("type") == "chart"}
			got = _linked_names(live.get("linked_charts"))
			if want != got:
				findings.append(Finding("V04", "error", dpath,
					f"linked_charts {sorted(got)} != chart items {sorted(x for x in want if x)}"))
			lf = _check_links(client, live, dpath, ran)
			findings.extend(lf)
			filters = [{"name": i.get("filter_name") or (i.get("layout") or {}).get("i"),
				"type": i.get("filter_type"),
				"links": len(i["links"]) if isinstance(i.get("links"), dict) else 0}
				for i in items if i.get("type") == "filter"]
			dash_out.append({"title": live.get("title"), "live_name": dl, "path": f"/insights/dashboards/{dl}",
				"charts": len(want), "filters": filters,
				"links_ok": not any(f.severity == "error" for f in lf)})
		return {
			"ok": not any(f.severity == "error" for f in findings),
			"workbook": {"name": wl, "title": wb.get("title"), "marker": _marker_of(wb), "modified": wb.get("modified")},
			"charts": chart_out, "dashboards": dash_out,
			"findings": [f.to_dict() for f in findings],
		}
	except FinsightsError as e:
		return _err(e)
