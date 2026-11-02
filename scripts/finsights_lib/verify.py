"""Read-back verification of a compiled workbook against a live site."""

from __future__ import annotations

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


def verify(compiled: dict, client, min_rows: int = 1) -> list:
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
	return findings
