import copy
import json
from pathlib import Path

import pytest

from finsights_lib import spec as specmod
from finsights_lib.compile import compile_spec, dumps
from finsights_lib.heal import apply_patch, heal_spec, suggest
from finsights_lib.lint import lint_spec

FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def spec():
	return specmod.load_spec(FIX / "ury_overview.yaml")


@pytest.fixture
def cat():
	return json.loads((FIX / "catalog.json").read_text())


def rules(spec, cat, rule):
	return [f for f in lint_spec(spec, cat) if f.rule == rule and f.severity == "error"]


def errors(spec, cat):
	return [f for f in lint_spec(spec, cat) if f.severity == "error"]


def counts(spec, cat):
	d, _ = compile_spec(spec, cat)
	dep = d["dependencies"]
	return len(dep["queries"]), len(dep["charts"]), len(dep["dashboards"])


def heal(spec, cat, **kw):
	return heal_spec(spec, cat, **kw)


def test_baseline_clean(spec, cat):
	assert errors(spec, cat) == []
	r = heal(spec, cat)
	assert r["applied"] == [] and r["rounds"] == 0 and r["spec"] == spec and r["remaining"] == []


# ---- L09
@pytest.mark.parametrize("bad,good", [("pie", "Donut"), ("kpi", "Number"), ("pivot", "Table"), ("Pie", "Donut")])
def test_l09_type(spec, cat, bad, good):
	spec["charts"][1]["type"] = bad
	if good in ("Donut", "Table", "Number"):
		spec["charts"][1].pop("limit")
	assert rules(spec, cat, "L09")
	r = heal(spec, cat)
	assert r["spec"]["charts"][1]["type"] == good
	assert rules(r["spec"], cat, "L09") == []
	assert [f["rule"] for f in r["applied"]] == ["L09"] and all(f["safe"] for f in r["applied"])


# ---- L03
def test_l03_invalid_id_rewrites_refs(spec, cat):
	spec["queries"][0]["id"] = "POS Invoices!"
	spec["charts"][0]["id"] = "KPIs 1"
	for c in spec["charts"]:
		c["query"] = "POS Invoices!"
	spec["dashboards"][0]["items"][0]["chart"] = "KPIs 1"
	assert rules(spec, cat, "L03")
	r = heal(spec, cat)
	s = r["spec"]
	assert s["queries"][0]["id"] == "pos_invoices"
	assert s["charts"][0]["id"] == "kpis_1"
	assert all(c["query"] == "pos_invoices" for c in s["charts"])
	assert s["dashboards"][0]["items"][0]["chart"] == "kpis_1"
	assert list(s["dashboards"][0]["filters"][0]["applies_to"]) == ["pos_invoices"]
	assert errors(s, cat) == []


def test_l03_source_query_ref(spec, cat):
	spec["queries"].append({"id": "Derived Q", "title": "Derived", "source_query": "pos_invoices"})
	spec["queries"].append({"id": "second", "title": "Second", "source_query": "Derived Q"})
	r = heal(spec, cat)
	assert r["spec"]["queries"][1]["id"] == "derived_q"
	assert r["spec"]["queries"][2]["source_query"] == "derived_q"
	assert rules(r["spec"], cat, "L03") == [] and rules(r["spec"], cat, "L05") == []


def test_l03_dedupe_and_duplicates(spec, cat):
	spec["queries"][0]["id"] = "Sales Q"
	spec["queries"].append({"id": "sales q", "title": "Other", "table": "POS Invoice"})
	r = heal(spec, cat)
	ids = [q["id"] for q in r["spec"]["queries"]]
	assert len(set(ids)) == 2 and all(i.startswith("sales_q") for i in ids)
	assert rules(r["spec"], cat, "L03") == []


def test_l03_collides_with_existing_id(spec, cat):
	spec["charts"][1]["id"] = "Sales KPIs"  # slug sales_kpis free
	spec["charts"][2]["id"] = "sales-kpis"
	spec["dashboards"][0]["items"][1]["chart"] = "Sales KPIs"
	spec["dashboards"][0]["items"][2]["chart"] = "sales-kpis"
	r = heal(spec, cat)
	cid = [c["id"] for c in r["spec"]["charts"]]
	assert len(set(cid)) == 3 and rules(r["spec"], cat, "L03") == []
	assert r["spec"]["dashboards"][0]["items"][1]["chart"] == "sales_kpis"
	assert r["spec"]["dashboards"][0]["items"][2]["chart"] in cid


def test_l03_digit_start_and_filter_id(spec, cat):
	spec["dashboards"][0]["filters"][0]["id"] = "1 Date"
	r = heal(spec, cat)
	assert r["spec"]["dashboards"][0]["filters"][0]["id"] == "x_1_date"
	assert rules(r["spec"], cat, "L03") == []


# ---- L04
def test_l04_duplicate_titles(spec, cat):
	for c in spec["charts"]:
		c["title"] = "Same"
	spec["queries"].append({"id": "q2", "title": "POS Invoices", "table": "POS Invoice"})
	assert rules(spec, cat, "L04")
	r = heal(spec, cat)
	assert [c["title"] for c in r["spec"]["charts"]] == ["Same", "Same (2)", "Same (3)"]
	assert r["spec"]["queries"][1]["title"] == "POS Invoices (2)"
	assert rules(r["spec"], cat, "L04") == []


# ---- L11
def test_l11_clamp(spec, cat):
	it = spec["dashboards"][0]["items"]
	it[1].update({"x": 15, "w": 12})
	it[2].update({"x": -3})
	assert rules(spec, cat, "L11")
	r = heal(spec, cat)
	items = r["spec"]["dashboards"][0]["items"]
	assert items[1]["x"] + items[1]["w"] <= 20 and items[2]["x"] >= 0
	assert rules(r["spec"], cat, "L11") == [] and errors(r["spec"], cat) == []


def test_l11_wide_and_zero(spec, cat):
	it = spec["dashboards"][0]["items"][0]
	it.update({"w": 30, "h": 0, "y": -2})
	r = heal(spec, cat)
	got = r["spec"]["dashboards"][0]["items"][0]
	assert (got["x"], got["w"], got["h"]) == (0, 20, 1) and got["y"] >= 0
	assert errors(r["spec"], cat) == []


def test_l11_filter_layout(spec, cat):
	spec["dashboards"][0]["filters"][0]["layout"] = {"x": 18, "y": 0, "w": 6, "h": 1}
	r = heal(spec, cat)
	lay = r["spec"]["dashboards"][0]["filters"][0]["layout"]
	assert lay["x"] + lay["w"] <= 20 and rules(r["spec"], cat, "L11") == []


# ---- L12
def test_l12_reflow(spec, cat):
	it = spec["dashboards"][0]["items"]
	it[1].update({"x": 0, "y": 1, "w": 12, "h": 9})  # overlaps kpis (y1..4)
	it[2].update({"x": 10, "y": 3, "w": 8, "h": 9})
	assert rules(spec, cat, "L12")
	r = heal(spec, cat)
	new = r["spec"]["dashboards"][0]["items"]
	assert rules(r["spec"], cat, "L12") == [] and errors(r["spec"], cat) == []
	assert [i["chart"] for i in new] == ["kpis", "daily_sales", "sales_by_branch"]
	assert [(i["w"], i["h"]) for i in new] == [(20, 3), (12, 9), (8, 9)]
	assert new[0]["x"] == 0 and new[0]["y"] == 1  # first item stays


def test_l12_keeps_non_overlapping_untouched(spec, cat):
	spec["dashboards"][0]["items"][2].update({"x": 0, "y": 4, "w": 8, "h": 9})
	spec["dashboards"][0]["items"][1].update({"x": 0, "y": 4, "w": 8, "h": 9})
	r = heal(spec, cat)
	items = r["spec"]["dashboards"][0]["items"]
	assert items[0] == spec["dashboards"][0]["items"][0]
	assert rules(r["spec"], cat, "L12") == []


# ---- L01
def test_l01_typo_rename(spec, cat):
	spec["charts"][1]["tilte"] = spec["charts"][1].pop("title")
	spec["dashboards"][0]["itms"] = spec["dashboards"][0].pop("items")
	assert rules(spec, cat, "L01")
	r = heal(spec, cat)
	assert r["spec"]["charts"][1]["title"] == "Daily Sales"
	assert "items" in r["spec"]["dashboards"][0]
	assert rules(r["spec"], cat, "L01") == []


def test_l01_nested_and_workbook(spec, cat):
	spec["workbook"]["titel"] = spec["workbook"].pop("title")
	spec["charts"][0]["y"][0]["aggg"] = spec["charts"][0]["y"][0].pop("agg")
	r = heal(spec, cat)
	assert r["spec"]["workbook"]["title"] == "URY Sales"
	assert r["spec"]["charts"][0]["y"][0]["agg"] == "sum"
	assert errors(r["spec"], cat) == []


def test_l01_far_key_no_proposal(spec, cat):
	spec["charts"][1]["zzzzzzzz"] = 1
	r = heal(spec, cat)
	assert r["spec"]["charts"][1]["zzzzzzzz"] == 1
	assert any(f["rule"] == "L01" for f in r["remaining"])
	assert not [f for f in r["applied"] + r["proposed"] if f["rule"] == "L01"]


def test_l01_ambiguous_no_proposal(spec, cat):
	spec["dashboards"][0]["items"][0]["z"] = 1  # one edit from x, y, w, h
	r = heal(spec, cat)
	assert r["spec"]["dashboards"][0]["items"][0]["z"] == 1
	assert not [f for f in r["applied"] if f["rule"] == "L01"]


def test_l01_target_exists_no_rename(spec, cat):
	spec["charts"][1]["titl"] = "dup"  # title already present
	r = heal(spec, cat)
	assert "titl" in r["spec"]["charts"][1]


# ---- unsafe
def test_unsafe_table_alternatives(spec, cat):
	spec["queries"][0]["table"] = "POS Invoce"
	before = copy.deepcopy(spec)
	fx = [f for f in suggest(lint_spec(spec, cat), spec, cat) if f["rule"] == "L05"]
	assert fx and not fx[0]["safe"] and fx[0]["patch"] == []
	assert fx[0]["alternatives"][0]["patch"] == [{"op": "replace", "path": "/queries/0/table", "value": "POS Invoice"}]
	r = heal(spec, cat)
	assert spec == before and r["spec"]["queries"][0]["table"] == "POS Invoce"
	assert any(f["rule"] == "L05" for f in r["proposed"]) and any(f["rule"] == "L05" for f in r["remaining"])


def test_unsafe_table_no_catalog(spec):
	spec["queries"][0]["table"] = "POS Invoce"
	assert [f for f in suggest(lint_spec(spec, None), spec, None) if f["rule"] in ("L05", "L06")] == []


def test_unsafe_column_alternatives(spec, cat):
	spec["queries"][0]["filters"][0]["column"] = "docstatuss"
	r = heal(spec, cat)
	fx = [f for f in r["proposed"] if f["rule"] == "L06"]
	assert fx and fx[0]["alternatives"][0]["patch"][0]["value"] == "docstatus"
	assert r["spec"]["queries"][0]["filters"][0]["column"] == "docstatuss"


def test_unsafe_chart_column_alternatives(spec, cat):
	spec["charts"][1]["y"][0]["column"] = "grand_totl"
	r = heal(spec, cat)
	fx = [f for f in r["proposed"] if f["rule"] == "L08"]
	assert fx and fx[0]["alternatives"][0]["patch"][0]["path"] == "/charts/1/y/0/column"
	assert fx[0]["alternatives"][0]["patch"][0]["value"] == "grand_total"
	assert len(fx[0]["alternatives"]) <= 3


def test_unsafe_applies_to_column(spec, cat):
	spec["dashboards"][0]["filters"][1]["applies_to"] = {"pos_invoices": "brnch"}
	r = heal(spec, cat)
	fx = [f for f in r["proposed"] if f["rule"] == "L13"]
	assert fx and not fx[0]["safe"]
	descs = [a["patch"][0]["op"] for a in fx[0]["alternatives"]]
	assert "replace" in descs and "remove" in descs
	assert r["spec"]["dashboards"][0]["filters"][1]["applies_to"] == {"pos_invoices": "brnch"}


def test_unsafe_missing_slot_never_mutates(spec, cat):
	spec["charts"][1].pop("x")
	r = heal(spec, cat)
	assert [f["rule"] for f in r["proposed"]] == ["L10"] and r["proposed"][0]["safe"] is False
	assert "x" not in r["spec"]["charts"][1] and r["applied"] == []


def test_unsafe_l20_l16_l02(spec, cat):
	spec["charts"][2]["y"][0] = {"column": "branch", "agg": "sum", "as": "Sales"}
	spec["queries"].append({"id": "raw", "title": "Raw", "sql": "DELETE FROM x"})
	spec["workbook"]["description"] = "uses Insights Query doc"
	r = heal(spec, cat)
	got = {f["rule"] for f in r["proposed"]}
	assert {"L20", "L16", "L02"} <= got
	assert all(not f["safe"] for f in r["proposed"])
	assert r["spec"]["queries"][-1]["sql"] == "DELETE FROM x"


# ---- apply_patch
def test_patch_ops(spec):
	s = apply_patch(spec, [
		{"op": "replace", "path": "/charts/0/title", "value": "T"},
		{"op": "add", "path": "/charts/0/limit", "value": 5},
		{"op": "remove", "path": "/charts/1/limit"},
		{"op": "add", "path": "/queries/-", "value": {"id": "zz"}},
		{"op": "add", "path": "/queries/0", "value": {"id": "first"}},
	])
	assert s["charts"][0]["title"] == "T" and s["charts"][0]["limit"] == 5 and "limit" not in s["charts"][1]
	assert s["queries"][-1] == {"id": "zz"} and s["queries"][0] == {"id": "first"}
	assert spec["charts"][0]["title"] == "Sales KPIs"  # original untouched


@pytest.mark.parametrize("op", [
	{"op": "replace", "path": "/nope/x", "value": 1},
	{"op": "replace", "path": "/charts/9/title", "value": 1},
	{"op": "remove", "path": "/charts/0/zzz"},
	{"op": "add", "path": "/charts/abc", "value": 1},
	{"op": "move", "path": "/charts", "value": 1},
	{"op": "replace", "path": "charts", "value": 1},
	{"op": "add", "path": "/charts/99", "value": 1},
])
def test_patch_errors(spec, op):
	with pytest.raises(ValueError):
		apply_patch(spec, [op])


def test_patch_escape_and_root():
	assert apply_patch({"a/b": 1}, [{"op": "replace", "path": "/a~1b", "value": 2}]) == {"a/b": 2}
	assert apply_patch({"a": 1}, [{"op": "replace", "path": "", "value": {"z": 1}}]) == {"z": 1}


# ---- heal_spec behaviour
def test_idempotent(spec, cat):
	spec["charts"][1]["type"] = "pie"
	spec["queries"][0]["id"] = "POS Q"
	for c in spec["charts"]:
		c["query"] = "POS Q"
	spec["dashboards"][0]["filters"][0]["applies_to"] = {"POS Q": "posting_date"}
	spec["dashboards"][0]["filters"][1]["applies_to"] = {"POS Q": "branch"}
	once = heal(spec, cat)
	twice = heal(once["spec"], cat)
	assert twice["spec"] == once["spec"] and twice["applied"] == [] and twice["rounds"] == 0


def test_max_rounds_bound(spec, cat):
	spec["charts"][1]["type"] = "pie"
	r = heal(spec, cat, max_rounds=0)
	assert r["rounds"] == 0 and r["spec"] == spec and r["applied"] == []
	r1 = heal(spec, cat, max_rounds=1)
	assert r1["rounds"] == 1


def test_allow_none_changes_nothing(spec, cat):
	spec["charts"][1]["type"] = "pie"
	spec["queries"][0]["id"] = "Bad Id"
	before = copy.deepcopy(spec)
	r = heal(spec, cat, allow="none")
	assert r["spec"] == before and spec == before and r["applied"] == [] and r["rounds"] == 0
	assert any(f["safe"] for f in r["proposed"])
	assert r["remaining"]


def test_multi_defect_one_call(spec, cat):
	spec["charts"][0]["type"] = "kpi"
	spec["charts"][1]["type"] = "Pie"
	spec["charts"][1].pop("limit")
	spec["charts"][2]["title"] = spec["charts"][1]["title"]
	spec["queries"][0]["id"] = "Pos Invoices"
	for c in spec["charts"]:
		c["query"] = "Pos Invoices"
	spec["dashboards"][0]["filters"][0]["applies_to"] = {"Pos Invoices": "posting_date"}
	spec["dashboards"][0]["filters"][1]["applies_to"] = {"Pos Invoices": "branch"}
	spec["dashboards"][0]["items"][2].update({"x": 15, "y": 2})
	spec["dashboards"][0]["itms"] = spec["dashboards"][0].pop("items")
	assert errors(spec, cat)
	r = heal(spec, cat)
	assert r["remaining"] == [] and errors(r["spec"], cat) == []
	assert {f["rule"] for f in r["applied"]} >= {"L01", "L03", "L04", "L09"}
	assert r["rounds"] <= 3


def test_meaning_preserved_counts(spec, cat):
	base = counts(spec, cat)
	base_ops = [q["operations"] for q in compile_spec(spec, cat)[0]["dependencies"]["queries"].values()]
	spec["charts"][1]["type"] = "line"
	spec["charts"][2]["title"] = spec["charts"][1]["title"]
	spec["dashboards"][0]["items"][2].update({"x": 15})
	r = heal(spec, cat)
	assert counts(r["spec"], cat) == base
	new_ops = [q["operations"] for q in compile_spec(r["spec"], cat)[0]["dependencies"]["queries"].values()]
	assert new_ops == base_ops
	assert len(r["spec"]["charts"]) == 3 and len(r["spec"]["queries"]) == 1
	assert len(r["spec"]["dashboards"][0]["filters"]) == 2


def test_healed_compiles_deterministically(spec, cat):
	spec["charts"][1]["type"] = "pie"
	spec["charts"][1].pop("limit")
	spec["queries"][0]["id"] = "Pos Q"
	for c in spec["charts"]:
		c["query"] = "Pos Q"
	spec["dashboards"][0]["filters"][0]["applies_to"] = {"Pos Q": "posting_date"}
	spec["dashboards"][0]["filters"][1]["applies_to"] = {"Pos Q": "branch"}
	s = heal(spec, cat)["spec"]
	a = dumps(compile_spec(s, cat)[0])
	b = dumps(compile_spec(copy.deepcopy(heal(s, cat)["spec"]), cat)[0])
	assert a == b


def test_heal_without_catalog(spec):
	spec["charts"][1]["type"] = "line"
	spec["queries"][0]["id"] = "Q 1"
	for c in spec["charts"]:
		c["query"] = "Q 1"
	spec["dashboards"][0]["filters"][0]["applies_to"] = {"Q 1": "posting_date"}
	spec["dashboards"][0]["filters"][1]["applies_to"] = {"Q 1": "branch"}
	r = heal_spec(spec, None)
	assert r["remaining"] == [] and r["spec"]["queries"][0]["id"] == "q_1"


def test_non_dict_spec_safe():
	r = heal_spec("nope", None)
	assert r["spec"] == "nope" and r["applied"] == []
	assert suggest([], 5) == []
