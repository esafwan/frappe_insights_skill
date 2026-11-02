import json
import subprocess
import sys

import pytest

from finsights_lib import desk
from finsights_lib.common import FinsightsError

SPEC = {
	"workbook": {"id": "ury_sales", "title": "URY Sales", "module": "URY"},
	"dashboards": [{"id": "overview", "title": "URY Daily Sales"}],
	"desk": {"workspace": "URY", "label": "Sales Analytics"},
}


def test_url_tier_workspace_shortcut():
	out = desk.commands(SPEC, "abc123", app_hint="apps/ury")
	assert out[0] == ("fdesk --app apps/ury --write workspace add-shortcut URY --type URL "
		"--url /insights/dashboards/abc123 --label 'Sales Analytics'")
	assert out[-1].startswith("fdesk --app apps/ury lint")


def test_url_tier_sidebar_and_dock():
	spec = {**SPEC, "desk": {**SPEC["desk"], "sidebar": {"section": "Dashboards"}, "dock": True}}
	out = desk.commands(spec, "d1")
	assert any("sidebar add-item URY --section Dashboards --link-type URL" in c for c in out)
	assert any("dock add-tile --link-type URL --url /insights/dashboards/d1" in c for c in out)


def test_url_tier_placeholder_and_no_desk():
	assert "<dashboard name>" in desk.commands(SPEC, None)[0]
	with pytest.raises(FinsightsError):
		desk.commands({"workbook": SPEC["workbook"]}, "x")


def test_island_tier_json():
	out = desk.commands(SPEC, None, tier="island")
	assert len(out) == 1
	assert json.loads(out[0]) == {
		"doctype": "Dashboard", "name": "Sales Analytics", "dashboard_name": "Sales Analytics", "docstatus": 0,
		"module": "URY", "is_standard": 1, "insights_dashboard": "ury-sales-overview",
	}


def test_desk_never_executes(monkeypatch):
	def boom(*a, **k):
		raise AssertionError("desk must not execute anything")
	monkeypatch.setattr(subprocess, "run", boom)
	monkeypatch.setattr(subprocess, "Popen", boom)
	desk.commands(SPEC, "x")
