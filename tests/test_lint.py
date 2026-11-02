"""Tests for finsights_lib.lint (rules L02, L05..L25 + shape rules via spec.py)."""

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


def rules(findings):
	return {f.rule for f in findings}


def errors(findings):
	return {f.rule for f in findings if f.severity == "error"}


def _unused_query(s):
	s["queries"].append({"id": "extra", "title": "Extra", "table": "POS Invoice"})


# --- mutators: each takes the clean spec (and returns optional replacement catalog) ----------

def m_l02(s):
	s["workbook"]["description"] = "built with Insights Query docs"

def m_l05(s):
	s["queries"][0]["table"] = "Nope DocType"

def m_l06(s):
	s["queries"][0]["filters"][0]["column"] = "no_such_col"

def m_l07(s):
	s["charts"][1]["query"] = "ghost_query"

def m_l08(s):
	s["charts"][1]["x"] = {"column": "company"}

def m_l09(s):
	s["charts"][1]["type"] = "pie"

def m_l10(s):
	del s["charts"][1]["x"]

def m_l11(s):
	s["dashboards"][0]["items"][2]["x"] = 15

def m_l12(s):
	s["dashboards"][0]["items"][1]["y"] = 1

def m_l13(s):
	s["dashboards"][0]["filters"][0]["applies_to"] = {"pos_invoices": "grand_total"}

def m_l14(s):
	s["dashboards"][0]["filters"][0]["default"] = {"op": "contains", "value": "x"}

def m_l15(s):
	s["dashboards"][0]["items"][1]["chart"] = "ghost_chart"

def m_l16(s):
	s["queries"].append({"id": "raw", "title": "Raw", "sql": "DELETE FROM tabX"})
	s["charts"].append({"id": "rawc", "title": "RawC", "query": "raw", "type": "Number", "y": ["a"]})

def m_l17(s):
	s["queries"] += [
		{"id": "qa", "title": "QA", "source_query": "qb"},
		{"id": "qb", "title": "QB", "source_query": "qa"},
	]

def m_l18(s):
	s["queries"][0]["filters"][0]["op"] = "~~"

def m_l20(s):
	s["charts"][1]["y"][0]["column"] = "branch"

def m_l22(s):
	del s["charts"][0]["date"]

def m_l23(s):
	s["charts"][2]["order"] = [{"by": "Nope", "dir": "desc"}]

def m_l24(s):
	_unused_query(s)

def m_l25(s):
	s["dashboards"][0]["items"].append({"text": "  ", "x": 0, "y": 13, "w": 4, "h": 2})


CASES = [
	("L02", m_l02), ("L05", m_l05), ("L06", m_l06), ("L07", m_l07), ("L08", m_l08), ("L09", m_l09),
	("L10", m_l10), ("L11", m_l11), ("L12", m_l12), ("L13", m_l13), ("L14", m_l14), ("L15", m_l15),
	("L16", m_l16), ("L17", m_l17), ("L18", m_l18), ("L20", m_l20), ("L22", m_l22), ("L23", m_l23),
	("L24", m_l24), ("L25", m_l25),
]


def test_clean_spec_has_zero_findings(spec):
	assert lint_spec(spec, CATALOG) == []


@pytest.mark.parametrize("rule,mut", CASES, ids=[c[0] for c in CASES])
def test_bad_spec_yields_exactly_its_rule(spec, rule, mut):
	mut(spec)
	f = lint_spec(spec, CATALOG)
	hits = [x for x in f if x.rule == rule]
	assert hits, f"{rule} not reported: {f}"
	# no unrelated errors; for warn rules, no unrelated findings at all except benign L24
	assert errors(f) <= {rule}, f
	for x in hits:
		assert x.path.startswith("/") and x.message
	if rule in ("L20", "L22", "L24"):
		assert rules(f) == {rule}, f


def test_l17_message_has_cycle(spec):
	m_l17(spec)
	f = [x for x in lint_spec(spec, CATALOG) if x.rule == "L17"]
	assert len(f) == 1 and "qa->qb->qa" in f[0].message


def test_l19_develop_key_warns(spec):
	spec["charts"][0]["number_format"] = "x"
	f = lint_spec(spec, CATALOG)
	hit = [x for x in f if x.rule == "L19"]
	assert hit and hit[0].severity == "warn" and hit[0].path == "/charts/0/number_format"


def test_l21_optional_column_pruned(spec):
	cat = copy.deepcopy(CATALOG)
	del cat["POS Invoice"]["branch"]
	f = lint_spec(spec, cat)
	assert rules(f) == {"L21"}
	assert f[0].severity == "info"
	assert "branch" in f[0].message and "pruned 3 refs" in f[0].message


def test_l09_hint_mentions_alias(spec):
	m_l09(spec)
	f = [x for x in lint_spec(spec, CATALOG) if x.rule == "L09"]
	assert "Donut" in f[0].fix_hint


def test_l03_l04_come_through(spec):
	spec["charts"][1]["title"] = spec["charts"][0]["title"]
	spec["charts"][2]["id"] = "kpis"
	assert {"L03", "L04"} <= rules(lint_spec(spec, CATALOG))


def test_l01_unknown_key_and_no_crash(spec):
	spec["queries"][0]["bogus"] = 1
	spec["charts"][0] = {"id": "kpis"}  # missing query/title/type
	f = lint_spec(spec, CATALOG)
	assert "L01" in rules(f)


@pytest.mark.parametrize("junk", [{}, {"workbook": 3, "queries": "x"}, {"workbook": {}, "queries": [1, None]},
	{"workbook": {"id": "ab", "title": "t"}, "queries": [], "charts": [{"id": "cc", "title": "c", "query": 5, "type": 7}],
	"dashboards": [{"id": "dd", "title": "d", "items": [5, {"chart": "x", "x": "a", "y": 0, "w": 1, "h": 1}]}]}])
def test_never_crashes_on_malformed(junk):
	assert isinstance(lint_spec(junk, CATALOG), list)
	assert isinstance(lint_spec(junk, None), list)
	assert isinstance(lint_spec([], None), list)


def test_exit_codes(spec):
	assert exit_code_for(lint_spec(spec, CATALOG), strict=False) == 0
	assert exit_code_for(lint_spec(spec, CATALOG), strict=True) == 0
	m_l22(spec)
	f = lint_spec(spec, CATALOG)
	assert exit_code_for(f, strict=False) == 0
	assert exit_code_for(f, strict=True) == 1
	m_l07(spec)
	assert exit_code_for(lint_spec(spec, CATALOG), strict=False) == 1


def test_info_does_not_fail_strict(spec):
	cat = copy.deepcopy(CATALOG)
	del cat["POS Invoice"]["branch"]
	assert exit_code_for(lint_spec(spec, cat), strict=True) == 0


def test_only_filtering(spec):
	m_l07(spec)
	m_l22(spec)
	m_l24(spec)
	f = lint_spec(spec, CATALOG, only={"L22"})
	assert rules(f) == {"L22"}
	f = lint_spec(spec, CATALOG, only={"l07", "L24"})
	assert rules(f) == {"L07", "L24"}


def test_ordering_is_deterministic(spec):
	m_l07(spec)
	m_l22(spec)
	m_l24(spec)
	m_l18(spec)
	a = lint_spec(spec, CATALOG)
	b = lint_spec(copy.deepcopy(spec), CATALOG)
	assert [x.to_dict() for x in a] == [x.to_dict() for x in b]
	keys = [(x.rule, x.path) for x in a]
	assert keys == sorted(keys)


def test_no_catalog_downgrades(spec):
	f = lint_spec(spec, None)
	assert errors(f) == set()
	assert {x.rule for x in f} == {"L05", "L06", "L08"}
	assert all(x.severity == "warn" and "no catalog; column checks skipped" in x.message for x in f)
	assert len(f) == 3


def test_no_catalog_skips_column_dependent_l13(spec):
	m_l13(spec)  # type mismatch needs the catalog
	f = lint_spec(spec, None)
	assert "L13" not in rules(f)
	assert "L13" in rules(lint_spec(spec, CATALOG))


def test_no_catalog_still_runs_structural_rules(spec):
	m_l07(spec)
	m_l14(spec)
	assert {"L07", "L14"} <= rules(lint_spec(spec, None))


def test_l13_unused_query_on_dashboard(spec):
	_unused_query(spec)
	spec["dashboards"][0]["filters"][0]["applies_to"] = {"extra": "posting_date"}
	f = lint_spec(spec, CATALOG)
	assert any(x.rule == "L13" and "unused" in x.message for x in f)


def test_l13_column_absent_from_final_output(spec):
	spec["dashboards"][0]["filters"][0]["applies_to"] = {"pos_invoices": "company"}
	f = lint_spec(spec, CATALOG)
	assert any(x.rule == "L13" and "FINAL output" in x.message for x in f)


def test_l14_bad_within_grammar(spec):
	spec["dashboards"][0]["filters"][0]["default"] = {"op": "within", "value": "Last month please"}
	assert "L14" in errors(lint_spec(spec, CATALOG))
	spec["dashboards"][0]["filters"][0]["default"] = {"op": "within", "value": "Current Month"}
	assert lint_spec(spec, CATALOG) == []


def test_l12_filter_overlaps_chart(spec):
	spec["dashboards"][0]["items"][0]["y"] = 0
	f = lint_spec(spec, CATALOG)
	assert errors(f) == {"L12"}


def test_l11_zero_width(spec):
	spec["dashboards"][0]["items"][1]["w"] = 0
	assert errors(lint_spec(spec, CATALOG)) == {"L11"}


def test_l05_join_table_and_l06_join_column(spec):
	spec["queries"][0]["joins"] = [{"table": "Missing DT", "type": "left", "on": ["name", "parent"]}]
	assert "L05" in errors(lint_spec(spec, CATALOG))
	spec["queries"][0]["joins"] = [{"table": "POS Invoice Item", "type": "left", "on": ["name", "nope"]}]
	assert "L06" in errors(lint_spec(spec, CATALOG))
	spec["queries"][0]["joins"] = [{"table": "POS Invoice Item", "type": "bogus", "on": ["name", "parent"]}]
	assert "L18" in errors(lint_spec(spec, CATALOG))


def test_l16_variants(spec):
	for sql, bad in [("SELECT 1", False), ("WITH a AS (SELECT 1) SELECT * FROM a;", False),
			("SELECT 1; DROP TABLE x", True), ("UPDATE x SET a=1", True), ("SELECT ';' AS s", False)]:
		s = copy.deepcopy(spec)
		s["queries"].append({"id": "raw", "title": "Raw", "sql": sql})
		s["charts"].append({"id": "rawc", "title": "RawC", "query": "raw", "type": "Number", "y": ["a"]})
		assert ("L16" in rules(lint_spec(s, CATALOG))) is bad, sql


def test_l18_granularity_and_agg_in_chart(spec):
	spec["charts"][1]["x"]["granularity"] = "fortnight"
	spec["charts"][2]["y"][0]["agg"] = "median"
	f = lint_spec(spec, CATALOG)
	assert {x.path for x in f if x.rule == "L18"} == {"/charts/1/x/granularity", "/charts/2/y/0/agg"}


def test_l10_split_by_equals_x(spec):
	spec["charts"][2]["split_by"] = {"column": "branch"}
	assert errors(lint_spec(spec, CATALOG)) == {"L10"}


def test_l02_api_strings(spec):
	spec["dashboards"][0]["title"] = "see insights.api.queries.get"
	assert errors(lint_spec(spec, CATALOG)) == {"L02"}
	spec["dashboards"][0]["title"] = "Insights Dashboard v3 notes"
	assert lint_spec(spec, CATALOG) == []


def test_l24_source_query_counts_as_used(spec):
	spec["queries"].append({"id": "derived", "title": "Derived", "source_query": "pos_invoices"})
	spec["charts"].append({"id": "dchart", "title": "D", "query": "derived", "type": "Number", "y": [{"column": "grand_total", "agg": "sum"}]})
	assert lint_spec(spec, CATALOG) == []


def test_findings_are_json_serialisable(spec):
	m_l07(spec)
	json.dumps([f.to_dict() for f in lint_spec(spec, CATALOG)])
