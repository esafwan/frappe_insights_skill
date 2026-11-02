"""Tests for lint rules L29 (bare HTML specials in text items) and L30 (unknown expression functions)."""

import copy
import json
from pathlib import Path

import pytest

from finsights_lib.lint import exit_code_for, lint_spec
from finsights_lib.schema import EXPR_FUNCTIONS
from finsights_lib.spec import load_spec

FIX = Path(__file__).parent / "fixtures"
CATALOG = json.loads((FIX / "catalog.json").read_text())


def hits(findings, rule):
	return [f for f in findings if f.rule == rule]


def _text_spec(text):
	return {"workbook": {"id": "wb", "title": "WB"}, "queries": [], "charts": [],
		"dashboards": [{"id": "d", "title": "D", "items": [{"text": text, "layout": {"x": 0, "y": 0, "w": 6, "h": 2}}]}]}


def _expr_spec(expr, where="mutate"):
	q = {"id": "qq", "title": "Q", "table": "POS Invoice"}
	c = {"id": "cc", "title": "C", "type": "Table", "query": "qq", "rows": ["name"]}
	if where == "mutate":
		q["mutate"] = [{"as": "m", "type": "Integer", "expr": expr}]
	elif where == "filter":
		q["filters"] = [{"expr": expr}]
	elif where == "measure":
		q["summarize"] = {"by": [{"column": "name"}], "measures": [{"as": "m", "agg": "sum", "expr": expr}]}
	elif where == "chart":
		c = {"id": "cc", "title": "C", "type": "Bar", "query": "qq", "x": "name", "y": [{"as": "m", "agg": "sum", "expr": expr}]}
	return {"workbook": {"id": "wb", "title": "WB"}, "queries": [q], "charts": [c]}


@pytest.mark.parametrize("txt,ch", [("sales > 100", ">"), ("a < b", "<"), ("Tom & Jerry", "&")])
def test_l29_fires(txt, ch):
	h = hits(lint_spec(_text_spec(txt), CATALOG), "L29")
	assert len(h) == 1 and h[0].severity == "error"
	assert f"`{ch}`" in h[0].message and h[0].path == "/dashboards/0/items/0/text"
	assert "above" in h[0].fix_hint and "entity" in h[0].fix_hint


@pytest.mark.parametrize("txt", [
	"<p>Hello <b>bold</b></p>", "line<br>break<br/>x", "<ul><li>one</li><li>two</li></ul>",
	"a &gt; b &lt; c &amp; d&nbsp;e", "&#62; &#x3E;", "<p class=\"x\">ok</p>",
])
def test_l29_allows_tags_and_entities(txt):
	assert not hits(lint_spec(_text_spec(txt), CATALOG), "L29")


def test_l29_bare_after_valid_tag():
	assert len(hits(lint_spec(_text_spec("<p>x > y</p>"), CATALOG), "L29")) == 1


def test_l30_count_distinct_did_you_mean():
	h = hits(lint_spec(_expr_spec("count_distinct(name)"), CATALOG), "L30")
	assert len(h) == 1 and h[0].severity == "error"
	assert "unknown function `count_distinct`" in h[0].message
	assert "distinct_count" in h[0].fix_hint
	assert h[0].path == "/queries/0/mutate/0/expr"


@pytest.mark.parametrize("expr", [
	"hour(posting_time)", "sum(grand_total)", "count_if(status == 'Paid')", 'qty.cast("int64")',
	"if_else(status == 'x', 1, 0)", "grand_total", "'count_distinct(x)'", "\"nope(1)\"",
	"if_else(a, round(b.abs(), 2), 0)", "ibis.literal(1)",
])
def test_l30_allows(expr):
	assert not hits(lint_spec(_expr_spec(expr), CATALOG), "L30")


@pytest.mark.parametrize("where,path", [
	("filter", "/queries/0/filters/0/expr"), ("measure", "/queries/0/summarize/measures/0/expr"),
	("chart", "/charts/0/y/0/expr"),
])
def test_l30_locations(where, path):
	h = hits(lint_spec(_expr_spec("bogus_fn(x)", where), CATALOG), "L30")
	assert [f.path for f in h] == [path]


def test_l30_native_sql_exempt():
	spec = _expr_spec("1")
	spec["queries"][0] = {"id": "qq", "title": "Q", "sql": "SELECT count_distinct(name) AS n FROM `tabPOS Invoice`"}
	assert not hits(lint_spec(spec, CATALOG), "L30")


def test_expr_functions_set():
	assert {"distinct_count", "count_if", "hour", "if_else", "sum", "ibis"} <= EXPR_FUNCTIONS
	assert "count_distinct" not in EXPR_FUNCTIONS


def test_only_filtering_and_exit_code():
	spec = _expr_spec("count_distinct(name)")
	spec["dashboards"] = _text_spec("a > b")["dashboards"]
	both = lint_spec(spec, CATALOG, only=["L29", "L30"])
	assert {f.rule for f in both} == {"L29", "L30"}
	assert {f.rule for f in lint_spec(spec, CATALOG, only=["L30"])} == {"L30"}
	assert exit_code_for(both) == 1
	assert exit_code_for(lint_spec(_text_spec("ok"), CATALOG, only=["L29"])) == 0
	assert both == sorted(both, key=lambda f: (f.rule, f.path, f.message))


def test_no_false_positives_on_fixture():
	fs = lint_spec(copy.deepcopy(load_spec(FIX / "ury_overview.yaml")), CATALOG)
	assert not hits(fs, "L29") and not hits(fs, "L30")


def test_no_false_positives_on_examples():
	for f in sorted((Path(__file__).parent.parent / "examples").glob("*.yaml")):
		fs = lint_spec(copy.deepcopy(load_spec(f)), CATALOG)
		assert not hits(fs, "L29") and not hits(fs, "L30"), f.name
