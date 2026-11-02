import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from finsights_lib import spec as specmod
from finsights_lib.compile import compile_spec, dumps

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
GOLD = HERE / "golden"
PROOF = Path("/Users/safwan/Code/URY/ury_workspaces/tracks/frappe-insights-skill/proof/ury_sales_proof.json")
SCRIPTS = HERE.parent / "scripts"


@pytest.fixture
def spec():
	return specmod.load_spec(FIX / "ury_overview.yaml")


@pytest.fixture
def catalog():
	return json.loads((FIX / "catalog.json").read_text())


def nobranch(cat):
	cat = copy.deepcopy(cat)
	cat["POS Invoice"].pop("branch")
	return cat


def mini(chart, extra_q=None):
	"""Minimal one-query spec with one chart."""
	q = {"id": "q", "title": "Q", "table": "POS Invoice"}
	q.update(extra_q or {})
	chart = dict({"id": "c", "title": "C", "query": "q"}, **chart)
	return {"workbook": {"id": "wb", "title": "WB"}, "queries": [q], "charts": [chart]}


def chart_cfg(s, cat, name="wb-c"):
	d, _ = compile_spec(s, cat)
	return d["dependencies"]["charts"][name]["config"]


def test_golden_template(spec, catalog):
	d, f = compile_spec(spec, catalog)
	assert f == []
	assert dumps(d) == (GOLD / "ury_overview.json").read_text()


def test_golden_standard(spec, catalog):
	d, _ = compile_spec(spec, catalog, "standard")
	assert dumps(d) == (GOLD / "ury_overview.standard.json").read_text()
	assert d["doctype"] == "Insights Workbook" and d["is_standard"] == 1 and d["module"] == "URY"
	assert "dependencies" not in d and d["queries"]["ury-sales-pos-invoices"]["workbook"] == "ury-sales"


def test_byte_stable_twice(spec, catalog):
	a = dumps(compile_spec(spec, catalog)[0])
	b = dumps(compile_spec(copy.deepcopy(spec), catalog)[0])
	assert a == b and a.endswith("}\n")


def test_byte_stable_hashseed(tmp_path):
	code = ("import sys,json;sys.path.insert(0,%r);from finsights_lib import spec,compile as c;"
		"s=spec.load_spec(%r);cat=json.load(open(%r));sys.stdout.write(c.dumps(c.compile_spec(s,cat)[0]))"
		% (str(SCRIPTS), str(FIX / "ury_overview.yaml"), str(FIX / "catalog.json")))
	outs = []
	for seed in ("0", "1"):
		env = dict(os.environ, PYTHONHASHSEED=seed)
		outs.append(subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout)
	assert outs[0] == outs[1] == (GOLD / "ury_overview.json").read_text()


def test_optional_branch_pruned(spec, catalog):
	d, f = compile_spec(spec, nobranch(catalog))
	deps = d["dependencies"]
	assert "ury-sales-sales-by-branch" not in deps["charts"]
	dash = deps["dashboards"]["ury-sales-overview"]["items"]
	assert [i.get("filter_name") for i in dash if i["type"] == "filter"] == ["Date"]
	assert all(i.get("chart") != "ury-sales-sales-by-branch" for i in dash)
	sel = [o for o in deps["queries"]["ury-sales-pos-invoices"]["operations"] if o["type"] == "select"][0]
	assert "branch" not in sel["column_names"]
	l21 = [x for x in f if x.rule == "L21"]
	assert l21 and all(x.severity == "info" for x in l21)
	assert any("`branch`" in x.message and "3 refs" in x.message for x in l21)
	assert "branch" not in dumps(d).replace("sales-by-branch", "")


def test_name_rewriting(spec, catalog):
	d, _ = compile_spec(spec, catalog)
	deps = d["dependencies"]
	assert set(deps["queries"]) == {"ury-sales-pos-invoices"}
	for ch in deps["charts"].values():
		assert ch["query"] == "ury-sales-pos-invoices" and ch["workbook"] == "ury-sales"
	dash = deps["dashboards"]["ury-sales-overview"]
	for it in dash["items"]:
		if it["type"] == "filter":
			for k, v in it["links"].items():
				assert k in deps["charts"] and v.startswith("`ury-sales-pos-invoices`.`")
		else:
			assert it["chart"] in deps["charts"]


def test_source_query_chaining(catalog):
	s = mini({"type": "Line", "x": {"column": "posting_date", "granularity": "day"}, "y": [{"column": "total", "as": "T"}]},
		{"summarize": {"by": [{"column": "posting_date", "granularity": "day"}], "measures": [{"column": "grand_total", "agg": "sum", "as": "total"}]}})
	s["queries"].append({"id": "q2", "title": "Q2", "source_query": "q", "limit": 5})
	s["charts"][0]["query"] = "q2"
	s["dashboards"] = [{"id": "d", "title": "D", "filters": [
		{"id": "f", "label": "Day", "type": "date", "applies_to": {"q": "posting_date"}}],
		"items": [{"chart": "c", "x": 0, "y": 1, "w": 10, "h": 5}]}]
	d, _ = compile_spec(s, catalog)
	q2 = d["dependencies"]["queries"]["wb-q2"]
	assert q2["operations"][0] == {"type": "source", "table": {"type": "query", "workbook": "wb", "query_name": "wb-q"}}
	assert q2["operations"][-1] == {"type": "limit", "limit": 5}
	# chart on q2 depends on q, so the filter on q links it (link rule 2)
	link = d["dependencies"]["dashboards"]["wb-d"]["items"][0]["links"]
	assert link == {"wb-c": "`wb-q`.`posting_date`"}
	ops = d["dependencies"]["queries"]["wb-q"]["operations"]
	assert [o["type"] for o in ops] == ["source", "summarize"]
	assert ops[1]["dimensions"][0]["granularity"] == "day" and ops[1]["measures"][0]["measure_name"] == "total"


def test_query_op_order_and_native(catalog):
	s = mini({"type": "Table", "x": "name", "y": [{"column": "grand_total"}]}, {
		"joins": [{"table": "POS Invoice Item", "type": "left", "on": ["name", "parent"], "columns": ["item_code"]}],
		"filters": [{"column": "docstatus", "op": "=", "value": 1}, {"expr": "grand_total > 0"}],
		"mutate": [{"as": "x2", "type": "Decimal", "expr": "grand_total * 2"}],
		"select": ["name", "grand_total"], "order_by": [{"column": "name", "dir": "desc"}], "limit": 7})
	s["queries"].append({"id": "n", "title": "N", "sql": "SELECT 1 AS a"})
	d, _ = compile_spec(s, catalog)
	q = d["dependencies"]["queries"]["wb-q"]
	assert [o["type"] for o in q["operations"]] == ["source", "join", "filter_group", "mutate", "select", "order_by", "limit"]
	j = q["operations"][1]
	assert j["join_type"] == "left" and j["join_condition"]["right_column"]["column_name"] == "parent"
	assert q["operations"][2]["filters"][1] == {"expression": {"type": "expression", "expression": "grand_total > 0"}}
	n = d["dependencies"]["queries"]["wb-n"]
	assert n["is_native_query"] == 1 and n["is_builder_query"] == 0
	assert n["operations"] == [{"type": "sql", "data_source": "Site DB", "raw_sql": "SELECT 1 AS a"}]


def test_default_within_mapping(spec, catalog):
	d, _ = compile_spec(spec, catalog)
	items = d["dependencies"]["dashboards"]["ury-sales-overview"]["items"]
	date_f, branch_f = items[0], items[1]
	assert (date_f["default_operator"], date_f["default_value"]) == ("within", "Last 30 days")
	assert "default_operator" not in branch_f and "default_value" not in branch_f
	assert date_f["layout"] == {"h": 1, "i": "filter-f-date", "w": 4, "x": 0, "y": 0}
	assert branch_f["layout"]["x"] == 4 and branch_f["filter_type"] == "String"


def test_filter_layout_wrap_and_text_items(catalog):
	s = mini({"type": "Number", "y": [{"column": "name", "agg": "count"}]})
	fl = [{"id": f"f{i}", "label": f"F{i}", "type": "string", "applies_to": {"q": "branch"}} for i in range(6)]
	s["dashboards"] = [{"id": "d", "title": "D", "filters": fl, "items": [
		{"text": "<p>hi</p>", "x": 0, "y": 2, "w": 5, "h": 1}, {"text": "<p>yo</p>", "x": 5, "y": 2, "w": 5, "h": 1}]}]
	d, _ = compile_spec(s, catalog)
	items = d["dependencies"]["dashboards"]["wb-d"]["items"]
	pos = [(i["layout"]["x"], i["layout"]["y"]) for i in items[:6]]
	assert pos == [(0, 0), (4, 0), (8, 0), (12, 0), (16, 0), (0, 1)]
	assert [i["layout"]["i"] for i in items[6:]] == ["text-0", "text-1"]
	assert items[6] == {"type": "text", "text": "<p>hi</p>", "layout": {"h": 1, "i": "text-0", "w": 5, "x": 0, "y": 2}}


@pytest.mark.parametrize("ctype,chart,slots", [
	("Number", {"y": [{"column": "grand_total", "as": "S"}]}, {"number_columns"}),
	("Bar", {"x": "branch", "y": [{"column": "grand_total", "as": "S"}], "stack": False, "split_by": {"column": "status", "max": 5}}, {"x_axis", "y_axis", "split_by"}),
	("Line", {"x": "posting_date", "y": [{"column": "grand_total"}], "smooth": True}, {"x_axis", "y_axis"}),
	("Row", {"x": "branch", "y": [{"column": "grand_total"}]}, {"x_axis", "y_axis"}),
	("Donut", {"x": "branch", "y": [{"column": "grand_total", "as": "S"}], "max_slices": 5, "legend": "right"}, {"label_column", "value_column", "legend_position", "max_slices"}),
	("Funnel", {"x": "status", "y": [{"column": "name", "agg": "count", "as": "N"}]}, {"label_column", "value_column"}),
	("Table", {"x": "branch", "y": [{"column": "grand_total", "as": "S"}]}, {"rows", "values"}),
	("Map", {"x": "branch", "y": [{"column": "grand_total", "as": "S"}], "map_type": "india"}, {"location_column", "value_column", "map_type"}),
	("Bubble", {"x": "branch", "y": [{"column": "grand_total", "as": "A"}, {"column": "paid_amount", "as": "B"}]}, {"xAxis", "yAxis", "dimension"}),
	("Sankey", {"x": "branch", "split_by": {"column": "status"}, "y": [{"column": "grand_total", "as": "S"}]}, {"source_column", "target_column", "value_column"}),
])
def test_chart_type_minimal_configs(catalog, ctype, chart, slots):
	cfg = chart_cfg(mini(dict(chart, type=ctype)), catalog)
	assert slots <= set(cfg)
	assert cfg["filters"] == {"filters": [], "logical_operator": "And"} and cfg["limit"] == 100
	assert isinstance(cfg["order_by"], list)


def test_chart_type_details(catalog):
	cfg = chart_cfg(mini({"type": "bar", "x": "branch", "y": [{"column": "grand_total"}], "stack": False}), catalog)
	assert cfg["y_axis"]["stack"] is False
	d, _ = compile_spec(mini({"type": "pie", "x": "branch", "y": [{"column": "grand_total", "as": "S"}]}), catalog)
	assert d["dependencies"]["charts"]["wb-c"]["chart_type"] == "Donut"
	cfg = chart_cfg(mini({"type": "Table", "x": "branch", "columns": ["status"], "y": [{"column": "grand_total", "as": "S"}]}), catalog)
	assert cfg["columns"][0]["column_name"] == "status"


def test_order_by_uses_alias(catalog):
	cfg = chart_cfg(mini({"type": "Row", "x": "branch", "y": [{"column": "grand_total", "as": "Sales"}],
		"order": [{"by": "Sales", "dir": "desc"}], "limit": 3}), catalog)
	assert cfg["order_by"] == [{"column": {"column_name": "Sales", "type": "column"}, "direction": "desc"}]
	assert cfg["limit"] == 3


def _proof():
	return json.loads(PROOF.read_text())["dependencies"]


@pytest.mark.skipif(not PROOF.exists(), reason="proof payload not present")
def test_parity_chart_configs_with_proof(spec, catalog):
	ours = compile_spec(spec, catalog)[0]["dependencies"]["charts"]
	proof = _proof()["charts"]
	pairs = {"ury-sales-kpis": "tc-kpis", "ury-sales-daily-sales": "tc-trend", "ury-sales-sales-by-branch": "tc-by-branch"}
	for ours_name, proof_name in pairs.items():
		o, p = ours[ours_name], proof[proof_name]
		assert set(p) <= set(o), (ours_name, set(p) - set(o))
		assert o["chart_type"] == p["chart_type"]
		for k in p["config"]:
			assert k in o["config"], (ours_name, k)
		if "x_axis" in p["config"]:
			assert set(o["config"]["x_axis"]["dimension"]) >= set(p["config"]["x_axis"]["dimension"]) - {"granularity"}
			assert set(o["config"]["y_axis"]) == set(p["config"]["y_axis"])
			assert set(o["config"]["y_axis"]["series"][0]["measure"]) == set(p["config"]["y_axis"]["series"][0]["measure"])
		if "number_columns" in p["config"]:
			assert set(o["config"]["number_columns"][0]) >= set(p["config"]["number_columns"][0])
		for ob in o["config"]["order_by"]:
			assert set(ob) == {"column", "direction"} and set(ob["column"]) == {"type", "column_name"}


@pytest.mark.skipif(not PROOF.exists(), reason="proof payload not present")
def test_parity_dashboard_items_and_envelope_with_proof(spec, catalog):
	d = compile_spec(spec, catalog)[0]
	proof = json.loads(PROOF.read_text())
	assert set(d) == set(proof) and set(d["doc"]) == set(proof["doc"])
	assert set(d["dependencies"]) == set(proof["dependencies"])
	ours = list(d["dependencies"]["dashboards"].values())[0]
	pdash = list(proof["dependencies"]["dashboards"].values())[0]
	assert set(ours) == set(pdash)
	pf = {i["filter_type"]: i for i in pdash["items"] if i["type"] == "filter"}
	of = {i["filter_type"]: i for i in ours["items"] if i["type"] == "filter"}
	for ft in ("Date", "String"):
		assert set(of[ft]) - {"default_operator", "default_value"} == set(pf[ft]) - {"default_operator", "default_value"}
		assert set(of[ft]["layout"]) == set(pf[ft]["layout"])
	pc = [i for i in pdash["items"] if i["type"] == "chart"][0]
	oc = [i for i in ours["items"] if i["type"] == "chart"][0]
	assert set(oc) == set(pc) and set(oc["layout"]) == set(pc["layout"])
	pq = list(proof["dependencies"]["queries"].values())[0]
	oq = list(d["dependencies"]["queries"].values())[0]
	assert set(oq) == set(pq)
	assert oq["operations"][0] == pq["operations"][0] and oq["operations"][1] == pq["operations"][1]
