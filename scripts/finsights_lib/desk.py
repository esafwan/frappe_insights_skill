"""Desk surfacing. Emits fdesk commands (tier 'url') or a desk Dashboard doc (tier 'island'). Never runs fdesk."""

from __future__ import annotations

import json
import shlex

from .common import FinsightsError
from .compile import kebab


def _q(s) -> str:
	return shlex.quote(str(s))


def _first_dashboard_name(spec: dict) -> str | None:
	dashes = spec.get("dashboards") or []
	if not dashes:
		return None
	return f"{kebab(spec['workbook']['id'])}-{kebab(dashes[0]['id'])}"


def island_doc(spec: dict, dashboard_name: str | None = None) -> dict:
	wb = spec.get("workbook") or {}
	desk = spec.get("desk") or {}
	dashes = spec.get("dashboards") or []
	slug = dashboard_name or _first_dashboard_name(spec)
	if not slug:
		raise FinsightsError("spec has no dashboards to claim from a desk Dashboard", 2)
	title = desk.get("label") or dashes[0].get("title") or slug
	return {
		"doctype": "Dashboard", "name": title, "dashboard_name": title, "docstatus": 0,
		"module": wb.get("module") or "Insights", "is_standard": 1, "insights_dashboard": slug,
	}


def commands(spec: dict, dashboard_live_name: str | None, app_hint: str = "<app path>", tier: str = "url") -> list:
	if tier == "island":
		return [json.dumps(island_doc(spec, dashboard_live_name), sort_keys=True, indent=1, ensure_ascii=False)]
	if tier != "url":
		raise FinsightsError(f"unknown desk tier {tier!r} (use url or island)", 2)
	desk = spec.get("desk")
	if not desk:
		raise FinsightsError("spec has no `desk` section", 2)
	name = dashboard_live_name or "<dashboard name>"
	url = f"/insights/dashboards/{name}"
	f = f"fdesk --app {_q(app_hint)} --write"
	ws, label = desk["workspace"], desk["label"]
	out = [f"{f} workspace add-shortcut {_q(ws)} --type URL --url {_q(url)} --label {_q(label)}"]
	sidebar = desk.get("sidebar")
	if sidebar:
		sec = sidebar.get("section") if isinstance(sidebar, dict) else None
		cmd = f"{f} sidebar add-item {_q(ws)}"
		if sec:
			cmd += f" --section {_q(sec)}"
		out.append(f"{cmd} --link-type URL --url {_q(url)} --label {_q(label)}")
	if desk.get("dock"):
		out.append(f"{f} dock add-tile --link-type URL --url {_q(url)} --title {_q(label)} --icon chart-line")
	out.append(f"fdesk --app {_q(app_hint)} lint --only L07,L10,L13")
	return out
