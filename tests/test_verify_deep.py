import copy
import io
import json
import urllib.error

import pytest

from fakesite import FakeSite
from finsights_lib import cli, verify as vmod
from finsights_lib.apply import apply_plan, plan_apply
from finsights_lib.verify import chart_operations, verify
from test_verify import GOLDEN

M = {"aggregation": "sum", "column_name": "grand_total", "data_type": "Decimal", "measure_name": "Sales"}
D = {"column_name": "posting_date", "data_type": "Date", "dimension_name": "posting_date", "granularity": "day"}
B = {"column_name": "branch", "data_type": "String", "dimension_name": "branch"}
FLT = {"filters": [], "logical_operator": "And"}


def ch(ctype, cfg):
	return {"chart_type": ctype, "query": "q1", "config": {"filters": FLT, "order_by": [], "limit": 100, **cfg}}


def types(ops):
	return [o["type"] for o in ops]


def test_ops_number_plain():
	ops = chart_operations(ch("Number", {"number_columns": [M]}))
	assert types(ops) == ["source", "summarize"]
	assert ops[0]["table"] == {"type": "query", "workbook": "", "query_name": "q1"}
	assert ops[1] == {"type": "summarize", "measures": [M], "dimensions": []}


def test_ops_number_date_comparison():
	ops = chart_operations(ch("Number", {"number_columns": [M], "date_column": D, "comparison": True,
		"order_by": [{"column": {"column_name": "posting_date", "type": "column"}, "direction": "asc"}]}))
	assert ops[1]["dimensions"] == [D]
	assert ops[2]["type"] == "order_by" and ops[2]["direction"] == "asc"


def test_ops_number_without_measure_is_invalid():
	assert chart_operations(ch("Number", {"number_columns": [{"column_name": "x"}]})) == []


def test_ops_line_summarize():
	ops = chart_operations(ch("Line", {"x_axis": {"dimension": D}, "y_axis": {"series": [{"measure": M}]}}))
	assert ops[1] == {"type": "summarize", "measures": [M], "dimensions": [D]}


def test_ops_bar_default_count_measure():
	ops = chart_operations(ch("Bar", {"x_axis": {"dimension": B}, "y_axis": {"series": []}}))
	assert ops[1]["measures"][0]["measure_name"] == "count_of_rows"


def test_ops_row_split_by_pivots():
	ops = chart_operations(ch("Row", {"x_axis": {"dimension": B}, "y_axis": {"series": [{"measure": M}]},
		"split_by": {"dimension": D, "max_split_values": 4}}))
	p = ops[1]
	assert p["type"] == "pivot_wider" and p["rows"] == [B] and p["columns"] == [D] and p["max_column_values"] == 4


def test_ops_axis_x_equals_split_invalid():
	assert chart_operations(ch("Line", {"x_axis": {"dimension": B}, "split_by": {"dimension": B}})) == []


def test_ops_donut_orders_desc_by_measure():
	ops = chart_operations(ch("Donut", {"label_column": B, "value_column": M}))
	assert types(ops) == ["source", "summarize", "order_by"]
	assert ops[2]["column"]["column_name"] == "Sales" and ops[2]["direction"] == "desc"


def test_ops_funnel_same_as_donut():
	assert types(chart_operations(ch("Funnel", {"label_column": B, "value_column": M}))) == ["source", "summarize", "order_by"]


def test_ops_table_flat_and_pivot():
	flat = chart_operations(ch("Table", {"rows": [B], "columns": [], "values": [M]}))
	assert flat[1] == {"type": "summarize", "measures": [M], "dimensions": [B]}
	piv = chart_operations(ch("Table", {"rows": [B], "columns": [D], "values": [M]}))
	assert piv[1]["type"] == "pivot_wider" and piv[1]["max_column_values"] == 10


def test_ops_expression_measure_and_filter_and_order_dedup():
	expr = {"data_type": "Decimal", "measure_name": "T", "expression": {"type": "expression", "expression": "count_distinct(a)"}}
	cfg = {"rows": [B], "columns": [], "values": [expr],
		"filters": {"filters": [{"column": {"column_name": "branch", "type": "column"}, "operator": "=", "value": "x"}], "logical_operator": "And"},
		"order_by": [{"column": {"column_name": "T"}, "direction": "asc"}]}
	ops = chart_operations(ch("Table", cfg))
	assert types(ops) == ["source", "filter_group", "summarize", "order_by"]
	assert ops[2]["measures"] == [expr] and ops[3]["direction"] == "asc"
	d = chart_operations(ch("Donut", {"label_column": B, "value_column": M, "order_by": [{"column": {"column_name": "Sales"}, "direction": "asc"}]}))
	assert types(d) == ["source", "summarize", "order_by"] and d[2]["direction"] == "asc"


def test_ops_config_string_and_unknown_type():
	c = ch("Number", {"number_columns": [M]})
	c["config"] = json.dumps(c["config"])
	assert len(chart_operations(c)) == 2
	assert chart_operations(ch("Sankey", {})) == []


# ---------------------------------------------------------------- live-ish verify
class DeepSite(FakeSite):
	"""Fails a run when the posted operations mention `boom`; source-query runs (1 op) always succeed."""

	boom = "count_distinct"
	empty_deep = False

	def open(self, req, timeout=None):
		if req.data and req.full_url.endswith("run_doc_method"):
			body = json.loads(req.data.decode())
			ops = (body.get("docs") or {}).get("operations") or []
			if self.boom in json.dumps(ops):
				payload = json.dumps({"exc_type": "NameError", "_server_messages": json.dumps(
					[json.dumps({"message": "NameError: name 'count_distinct' is not defined\nTraceback (most recent call last)"})])})
				raise urllib.error.HTTPError(req.full_url, 500, "err", {}, io.BytesIO(payload.encode()))
			if self.empty_deep and any(o.get("type") in ("summarize", "pivot_wider") for o in ops):
				self.calls.append(("POST", req.full_url))
				return type(self)._R({"message": {"rows": [], "columns": []}})
		return super().open(req, timeout)

	class _R:
		def __init__(self, p):
			self._b = json.dumps(p).encode()

		def read(self):
			return self._b


def applied(site_cls=DeepSite, **kw):
	kw.setdefault("canned_rows", [{"posting_date": "2026-01-01", "branch": "b", "x": 1}])
	site = site_cls(**kw)
	comp = copy.deepcopy(GOLDEN)
	c = site.client()
	apply_plan(plan_apply(comp, c), comp, c, write=True)
	return site, comp, c


def test_deep_off_by_default_no_extra_calls():
	site, comp, c = applied()
	n0 = len([b for b in site.bodies if "run_doc_method" in b[1]])
	assert verify(comp, c) == []
	n1 = len([b for b in site.bodies if "run_doc_method" in b[1]])
	verify(comp, c, deep=True)
	assert len([b for b in site.bodies if "run_doc_method" in b[1]]) > 2 * (n1 - n0) - 1


def test_deep_clean_site_passes_and_posts_chart_ops():
	site, comp, c = applied()
	assert verify(comp, c, deep=True) == []
	posted = [b[2]["docs"] for b in site.bodies if "run_doc_method" in b[1] and b[2]]
	assert any(any(o["type"] == "summarize" for o in d["operations"]) for d in posted)
	assert all(d["name"] != d.get("operations")[0]["table"]["query_name"] for d in posted if d["operations"][0]["type"] == "source" and d["operations"][0].get("table", {}).get("type") == "query")


def test_deep_v06_on_failing_chart_query():
	site, comp, c = applied()
	cd = next(d for d in site.docs["Insights Chart v3"].values() if d["title"] == "Daily Sales")
	cd["config"]["y_axis"]["series"][0]["measure"] = {"measure_name": "M", "data_type": "Integer",
		"expression": {"type": "expression", "expression": "count_distinct(name)"}}
	fs = verify(comp, c, deep=True)
	fs = [f for f in fs if f.rule != "V01"]  # the hand-edited config also drifts
	assert [f.rule for f in fs] == ["V06"]
	f = fs[0]
	assert f.severity == "error" and "Daily Sales" in f.message and "Line" in f.message and "NameError" in f.message
	assert "name 'count_distinct' is not defined" in f.message and "Traceback" not in f.message
	assert "distinct_count" in f.fix_hint


def test_deep_v07_only_when_source_has_rows():
	site, comp, c = applied()
	site.empty_deep = True
	fs = verify(comp, c, deep=True)
	assert fs and {f.rule for f in fs} == {"V07"} and all(f.severity == "warn" for f in fs)
	site2, comp2, c2 = applied(canned_rows=[])
	site2.empty_deep = True
	assert "V07" not in {f.rule for f in verify(comp2, c2, deep=True)}  # no V07 when source itself is empty


def test_deep_cap_limits_chart_queries():
	site, comp, c = applied()
	before = len([b for b in site.bodies if "run_doc_method" in b[1] and b[2]["docs"]["name"] == "new-query-finsights-deep"])
	assert before == 0
	verify(comp, c, deep=True, max_deep=1)
	n = len([b for b in site.bodies if "run_doc_method" in b[1] and b[2]["docs"]["name"] == "new-query-finsights-deep"])
	assert n == 1
	fs = verify(comp, c, deep=True, max_deep=1)
	assert any(f.severity == "info" and "cap" in f.message for f in fs)


def test_deep_findings_json_serialisable():
	site, comp, c = applied()
	cd = next(d for d in site.docs["Insights Chart v3"].values() if d["title"] == "Daily Sales")
	cd["config"]["y_axis"]["series"][0]["measure"]["expression"] = {"type": "expression", "expression": "count_distinct(x)"}
	json.dumps([f.to_dict() for f in verify(comp, c, deep=True)])


def test_split_error_and_hint():
	assert vmod._split_error("POST x -> HTTP 500: server error (NameError | boom\nTrace)") == ("NameError", "boom")
	assert vmod._split_error("x (ibis.common.exceptions.IbisTypeError | ibis.common.exceptions.IbisTypeError: bad types)") == ("IbisTypeError", "bad types")
	assert vmod._fix_hint("name 'count_distinct' is not defined").endswith("distinct_count(...)")
	assert vmod._fix_hint("something else") == ""


# ---------------------------------------------------------------- CLI wiring
def test_cli_deep_flag_wiring(monkeypatch):
	seen = {}

	def fake_verify(doc, client, min_rows=1, deep=False, **kw):
		seen["deep"] = deep
		return []

	monkeypatch.setattr(vmod, "verify", fake_verify)
	monkeypatch.setattr(cli, "_client", lambda: object())
	monkeypatch.setattr(cli.importlib.import_module("finsights_lib.apply"), "plan_apply",
		lambda doc, client, **k: type("P", (), {"name_map": {}})())
	from test_cli import CAT, SPEC
	assert cli.main(["verify", "--deep", "--spec", SPEC, "--catalog", CAT]) == 0
	assert seen["deep"] is True
	assert cli.main(["verify", "--spec", SPEC, "--catalog", CAT]) == 0
	assert seen["deep"] is False
