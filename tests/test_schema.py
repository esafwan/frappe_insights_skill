import json
from pathlib import Path

import pytest

from finsights_lib import schema
from finsights_lib.common import FinsightsError

CAT = json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text())


def test_enums():
	assert len(schema.CHART_TYPES) == 10
	assert schema.GRID_COLS == 20
	assert "count_distinct" in schema.AGGREGATIONS
	assert schema.JOIN_TYPES == ("inner", "left", "right", "full")
	assert schema.FILTER_TYPES == ("Date", "String", "Number")
	assert "within" in schema.FILTER_OPERATORS["date"]
	assert "within" not in schema.FILTER_OPERATORS["number"]


@pytest.mark.parametrize("raw,want", [("pie", "Donut"), ("kpi", "Number"), ("pivot", "Table"), ("line", "Line"), ("Row", "Row")])
def test_norm_chart_type(raw, want):
	assert schema.norm_chart_type(raw) == want


def test_norm_chart_type_rejects():
	with pytest.raises(FinsightsError):
		schema.norm_chart_type("radar")


def test_source_only():
	out = schema.output_schema({"table": "POS Invoice"}, CAT)
	assert out["posting_date"] == "Date" and out["grand_total"] == "Decimal"


def test_select_and_optional():
	q = {"table": "POS Invoice", "select": ["posting_date", {"column": "branch", "optional": True}, {"column": "nope", "optional": True}]}
	assert schema.output_schema(q, CAT) == {"posting_date": "Date", "branch": "String"}


def test_join_and_mutate():
	q = {"table": "POS Invoice", "joins": [{"table": "POS Invoice Item", "type": "left", "on": ["name", "parent"], "columns": ["item_code", "qty"]}],
		"mutate": [{"as": "margin", "type": "Decimal", "expr": "grand_total - net_total"}]}
	out = schema.output_schema(q, CAT)
	assert out["item_code"] == "String" and out["qty"] == "Decimal" and out["margin"] == "Decimal"


def test_summarize():
	q = {"table": "POS Invoice", "summarize": {
		"by": [{"column": "posting_date", "granularity": "month", "as": "Month"}, {"column": "branch"}],
		"measures": [{"column": "grand_total", "agg": "sum", "as": "Sales"}, {"agg": "count", "as": "N"},
			{"column": "posting_date", "agg": "max", "as": "Last"}, {"column": "grand_total", "agg": "avg"}]}}
	assert schema.output_schema(q, CAT) == {"Month": "Date", "branch": "String", "Sales": "Decimal", "N": "Integer", "Last": "Date", "grand_total": "Decimal"}


def test_source_query_and_sql():
	base = {"table": "POS Invoice", "select": ["name"]}
	q = {"source_query": "b", "mutate": [{"as": "x", "type": "Integer", "expr": "1"}]}
	assert schema.output_schema(q, CAT, {"b": base}) == {"name": "String", "x": "Integer"}
	assert schema.output_schema({"sql": "select 1"}, CAT) == {}
