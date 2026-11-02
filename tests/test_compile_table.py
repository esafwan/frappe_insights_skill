import json

import pytest

from finsights_lib.compile import compile_spec, dumps


def spec_with(chart):
	q = {"id": "q", "title": "Q", "table": "POS Invoice"}
	return {"workbook": {"id": "wb", "title": "WB"}, "queries": [q],
		"charts": [dict({"id": "c", "title": "C", "query": "q"}, **chart)]}


def cfgs(spec):
	d, _ = compile_spec(spec, None)
	return list(d["dependencies"]["charts"].values())


def tbl(chart):
	return cfgs(spec_with(dict({"type": "Table"}, **chart)))[0]["config"]


def test_flat_always_has_columns():
	c = tbl({"rows": ["customer"], "y": [{"column": "grand_total", "agg": "sum"}]})
	assert c["columns"] == [] and len(c["rows"]) == 1 and len(c["values"]) == 1


def test_flat_x_only():
	c = tbl({"x": "customer", "y": [{"column": "grand_total", "agg": "sum"}]})
	assert c["columns"] == [] and c["rows"]


def test_pivot_columns_present():
	c = tbl({"rows": ["customer"], "columns": ["branch"], "y": [{"column": "grand_total", "agg": "sum"}]})
	assert len(c["columns"]) == 1 and c["columns"][0]["column_name"] == "branch"


def test_required_keys_lists():
	c = tbl({"rows": ["customer"], "y": [{"column": "grand_total", "agg": "sum"}]})
	for k in ("rows", "columns", "values"):
		assert isinstance(c[k], list)


def test_shapes():
	c = tbl({"rows": ["customer"], "y": [{"column": "grand_total", "agg": "sum"}]})
	assert all("column_name" in r and "dimension_name" in r for r in c["rows"])
	assert all("measure_name" in v and "column_name" in v and "aggregation" in v for v in c["values"])


def test_rows_empty_still_lists():
	c = tbl({"y": [{"column": "grand_total", "agg": "sum"}]})
	assert c["rows"] == [] and c["columns"] == []


@pytest.mark.parametrize("v", [0, False, ""])
def test_falsy_filter_values_verbatim(v):
	c = cfgs(spec_with({"type": "Table", "rows": ["customer"], "y": [{"column": "grand_total", "agg": "sum"}],
		"filters": [{"column": "is_take_away", "op": "=", "value": v}]}))[0]["config"]
	f = c["filters"]["filters"][0]
	assert f["value"] == v and type(f["value"]) is type(v) and f["operator"] == "="


@pytest.mark.parametrize("v", [1, "x", [1, 2]])
def test_nonfalsy_unchanged(v):
	c = cfgs(spec_with({"type": "Table", "rows": ["customer"], "y": [{"column": "grand_total", "agg": "sum"}],
		"filters": [{"column": "docstatus", "op": "=", "value": v}]}))[0]["config"]
	assert c["filters"]["filters"][0]["value"] == v


def test_byte_stable():
	s = spec_with({"type": "Table", "rows": ["customer"], "y": [{"column": "grand_total", "agg": "sum"}]})
	assert dumps(compile_spec(s, None)[0]) == dumps(compile_spec(s, None)[0])
