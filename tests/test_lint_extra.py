"""Tests for lint rules L26 (share), L27 (derived chart types), L28 (join column collision)."""

import copy
import json
from pathlib import Path

import pytest

from finsights_lib.lint import exit_code_for, lint_spec
from finsights_lib.spec import load_spec

FIX = Path(__file__).parent / "fixtures"
CATALOG = json.loads((FIX / "catalog.json").read_text())


@pytest.fixture
def spec():
	return copy.deepcopy(load_spec(FIX / "ury_overview.yaml"))


def hits(findings, rule):
	return [f for f in findings if f.rule == rule]


def _join_spec(columns, select=None):
	q = {"id": "jq", "title": "J", "table": "POS Invoice Item",
		"joins": [{"table": "POS Invoice", "on": ["parent", "name"], "columns": columns}]}
	if select:
		q["select"] = select
	return {"workbook": {"id": "wb", "title": "WB"}, "queries": [q],
		"charts": [{"id": "c", "type": "Table", "query": "jq", "rows": ["parent"]}]}


SHARED_CATALOG = {
	"POS Invoice Item": {"name": "String", "parent": "String", "creation": "Datetime", "qty": "Decimal"},
	"POS Invoice": {"name": "String", "creation": "Datetime", "posting_date": "Date"},
}


def _shared_col():
	return "creation"


def test_baseline_clean(spec):
	f = lint_spec(spec, CATALOG)
	assert not [x for x in f if x.rule in ("L26", "L27", "L28")]


def test_l26_fires_and_not(spec):
	assert not hits(lint_spec(spec, CATALOG), "L26")
	spec["share"] = {"team": "Ops"}
	f = hits(lint_spec(spec, CATALOG), "L26")
	assert len(f) == 1 and f[0].severity == "warn"
	assert "update_share_permissions" in f[0].fix_hint


@pytest.mark.parametrize("t", ["Map", "Bubble", "Sankey"])
def test_l27_fires(spec, t):
	spec["charts"][0]["type"] = t
	assert hits(lint_spec(spec, CATALOG), "L27")[0].severity == "warn"


def test_l27_not_for_bar(spec):
	spec["charts"][0]["type"] = "Bar"
	assert not hits(lint_spec(spec, CATALOG), "L27")


def test_l28_fires():
	col = _shared_col()
	f = hits(lint_spec(_join_spec([col, "posting_date"]), SHARED_CATALOG), "L28")
	assert len(f) == 1 and col in f[0].message and "remove" in f[0].fix_hint and f[0].severity == "warn"


def test_l28_not_when_unique_or_pruned_by_select():
	col = _shared_col()
	assert not hits(lint_spec(_join_spec(["posting_date"]), SHARED_CATALOG), "L28")
	assert not hits(lint_spec(_join_spec([col], select=["parent"]), SHARED_CATALOG), "L28")


def test_l28_skipped_without_catalog():
	assert not hits(lint_spec(_join_spec([_shared_col()]), None), "L28")


def test_only_filter_and_ordering(spec):
	spec["share"] = {"team": "x"}
	spec["charts"][0]["type"] = "Map"
	f = lint_spec(spec, CATALOG, only=["l26"])
	assert {x.rule for x in f} == {"L26"}
	allf = lint_spec(spec, CATALOG)
	keys = [(x.rule, x.path, x.message) for x in allf]
	assert keys == sorted(keys)
	assert lint_spec(spec, CATALOG) == allf


def test_strict_makes_warns_fail(spec):
	assert exit_code_for(lint_spec(spec, CATALOG), strict=True) == 0
	spec["share"] = {"team": "x"}
	f = lint_spec(spec, CATALOG)
	assert exit_code_for(f) == 0
	assert exit_code_for(f, strict=True) == 1
