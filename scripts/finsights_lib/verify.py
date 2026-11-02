"""Read-back verification of a compiled workbook against a live site."""

from __future__ import annotations

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
		if qlive not in ran:
			try:
				out = client.run_query(qlive)
				ran[qlive] = ("ok", len(out.get("rows") or []))
			except FinsightsError as e:
				ran[qlive] = ("err", str(e))
		state, val = ran[qlive]
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
	return findings
