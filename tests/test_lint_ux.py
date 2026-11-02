"""Tests for lint rules L31..L35 (UX standard content and format warnings)."""

import json
from pathlib import Path

import pytest

from finsights_lib.lint import lint_spec

CATALOG = json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text())


def hits(findings, rule):
	return [f for f in findings if f.rule == rule]


def _text_spec(text):
	return {"workbook": {"id": "wb", "title": "WB"}, "queries": [], "charts": [],
		"dashboards": [{"id": "d", "title": "D", "items": [{"text": text, "layout": {"x": 0, "y": 0, "w": 6, "h": 2}}]}]}


def _chart_spec(ctype, y, measures=None):
	q = {"id": "qq", "title": "Q", "table": "POS Invoice",
		"summarize": {"by": [{"column": "name"}], "measures": measures or [{"as": "m", "agg": "sum", "column": "grand_total"}]}}
	c = {"id": "cc", "title": "C", "type": ctype, "query": "qq", "x": "name", "y": y}
	return {"workbook": {"id": "wb", "title": "WB"}, "queries": [q], "charts": [c]}


@pytest.mark.parametrize("txt", ["<b>**x**</b>", "use `code`", "# Heading", "<p># Heading</p>"])
def test_l31_fires(txt):
	h = hits(lint_spec(_text_spec(txt), CATALOG), "L31")
	assert len(h) == 1 and h[0].severity == "warn" and h[0].path == "/dashboards/0/items/0/text"


@pytest.mark.parametrize("txt", ["<p>Plain <b>bold</b></p>", "Issue #5 today", "<ul><li>one</li></ul>"])
def test_l31_clean(txt):
	assert not hits(lint_spec(_text_spec(txt), CATALOG), "L31")


@pytest.mark.parametrize("txt,code", [("see SAL-012", "SAL-012"), ("MAR-005 sales", "MAR-005"), ("DQ-010", "DQ-010"),
	("(T2) tier", "T2"), ("done in R15", "R15"), ("roadmap R1", "R1")])
def test_l32_fires(txt, code):
	h = hits(lint_spec(_text_spec(txt), CATALOG), "L32")
	assert len(h) == 1 and h[0].severity == "warn" and f"`{code}`" in h[0].message


@pytest.mark.parametrize("txt", ["<p>Total sales</p>", "Table T5 and R16 and R0", "ATM2 and ab-123", "<span class=\"T1\">x</span>"])
def test_l32_clean(txt):
	assert not hits(lint_spec(_text_spec(txt), CATALOG), "L32")


def test_l33_expr_in_query_measure():
	s = _chart_spec("Bar", ["r"], [{"as": "r", "expr": "sum(a) / sum(b)"}])
	h = hits(lint_spec(s, CATALOG), "L33")
	assert len(h) == 1 and h[0].severity == "warn" and "percent" in h[0].fix_hint


def test_l33_name_suffix_on_chart_y():
	s = _chart_spec("Bar", [{"column": "grand_total", "agg": "sum", "as": "discount_pct"}])
	assert len(hits(lint_spec(s, CATALOG), "L33")) == 1


def test_l33_inherits_query_format():
	s = _chart_spec("Bar", ["r"], [{"as": "r", "expr": "sum(a)/sum(b)", "format": "percent"}])
	assert not hits(lint_spec(s, CATALOG), "L33")


def test_l33_chart_format_overrides():
	s = _chart_spec("Bar", [{"column": "r", "format": "percent"}], [{"as": "r", "expr": "sum(a)/sum(b)"}])
	assert not hits(lint_spec(s, CATALOG), "L33")


def test_l33_not_ratio():
	assert not hits(lint_spec(_chart_spec("Bar", ["m"], [{"as": "migrate", "agg": "sum", "column": "grand_total"}]), CATALOG), "L33")


@pytest.mark.parametrize("name", ["net_sales", "variance", "stock_difference"])
def test_l34_fires(name):
	s = _chart_spec("Donut", [{"column": "grand_total", "agg": "sum", "as": name}])
	h = hits(lint_spec(s, CATALOG), "L34")
	assert len(h) == 1 and h[0].severity == "warn"


def test_l34_pie_alias_and_inherited_name():
	s = _chart_spec("pie", ["net_total"], [{"as": "net_total", "agg": "sum", "column": "grand_total"}])
	assert len(hits(lint_spec(s, CATALOG), "L34")) == 1


def test_l34_clean():
	assert not hits(lint_spec(_chart_spec("Donut", [{"column": "grand_total", "agg": "sum", "as": "sales"}]), CATALOG), "L34")
	assert not hits(lint_spec(_chart_spec("Bar", [{"column": "grand_total", "agg": "sum", "as": "net_sales"}]), CATALOG), "L34")
	assert not hits(lint_spec(_chart_spec("Donut", [{"column": "grand_total", "agg": "sum", "as": "internet"}]), CATALOG), "L34")


def test_l35_fires():
	y = [{"column": "grand_total", "agg": "sum", "as": "Sales", "format": "currency"}, {"column": "name", "agg": "count", "as": "Bills"}]
	h = hits(lint_spec(_chart_spec("Bar", y), CATALOG), "L35")
	assert len(h) == 1 and h[0].severity == "warn" and h[0].path == "/charts/0/y"


def test_l35_clean():
	y = [{"column": "grand_total", "agg": "sum", "as": "A", "format": "currency"}, {"column": "x", "agg": "sum", "as": "B", "format": "currency"}]
	assert not hits(lint_spec(_chart_spec("Bar", y), CATALOG), "L35")
	y = [{"column": "name", "agg": "count", "as": "A"}, {"column": "name", "agg": "count_distinct", "as": "B"}]
	assert not hits(lint_spec(_chart_spec("Bar", y), CATALOG), "L35")


def test_l35_number_and_table_exempt():
	y = [{"column": "grand_total", "agg": "sum", "as": "Sales", "format": "currency"}, {"column": "name", "agg": "count", "as": "Bills"}]
	for t in ("Number", "Table"):
		assert not hits(lint_spec(_chart_spec(t, y), CATALOG), "L35")
