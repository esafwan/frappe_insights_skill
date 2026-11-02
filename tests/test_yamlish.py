from pathlib import Path

import pytest

from finsights_lib import yamlish
from finsights_lib.common import SpecParseError

FIX = Path(__file__).parent / "fixtures" / "ury_overview.yaml"


def L(t):
	return yamlish.loads(t, force_builtin=True)


def test_scalars():
	assert L("a: 1\nb: 2.5\nc: true\nd: false\ne: null\nf: hello world\ng: \"x: y\"\nh: 'it''s'\ni: -3\n") == {
		"a": 1, "b": 2.5, "c": True, "d": False, "e": None, "f": "hello world", "g": "x: y", "h": "it's", "i": -3}


def test_block_seq_and_map():
	t = "a:\n  - x\n  - y: 1\n    z: 2\n  - - n1\n    - n2\nb:\n  c: d\n"
	assert L(t) == {"a": ["x", {"y": 1, "z": 2}, ["n1", "n2"]], "b": {"c": "d"}}


def test_seq_at_key_indent():
	assert L("a:\n- 1\n- 2\nb: 3\n") == {"a": [1, 2], "b": 3}


def test_flow_and_multiline_flow():
	assert L("a: {x: 1, y: [p, \"q r\", 3]}\n") == {"a": {"x": 1, "y": ["p", "q r", 3]}}
	assert L("a: {x: 1,\n     y: [2,\n 3]}\nb: 1\n") == {"a": {"x": 1, "y": [2, 3]}, "b": 1}


def test_comments():
	assert L("# top\na: 1  # trailing\nb: \"# not comment\"\n") == {"a": 1, "b": "# not comment"}


def test_escapes_and_empty_values():
	assert L('a: "l1\\nl2 \\u0041"\nb:\nc: []\n') == {"a": "l1\nl2 A", "b": None, "c": []}


def test_operator_scalars():
	assert L("op: \"=\"\nop2: >=\nv: {op: '!='}\n")["op2"] == ">="


@pytest.mark.parametrize("text,line,col,frag", [
	("a:\n\tb: 1\n", 2, 1, "tab"),
	("a: &x 1\n", 1, 4, "anchor"),
	("a: *x\n", 1, 4, "anchor"),
	("a: !!str 1\n", 1, 4, "tag"),
	("a: |\n  text\n", 1, 4, "block scalar"),
	("a: >\n  text\n", 1, 4, "block scalar"),
	("a: 1\n---\nb: 2\n", 2, 1, "multi-document"),
	("a: 1\na: 2\n", 2, 1, "duplicate"),
	("a: {x: 1\n", 1, 4, "unterminated"),
	("a: \"abc\n", 1, 4, "unterminated"),
	("a: 1\n   b: 2\n", 2, 4, "indent"),
	("a: [1, &x 2]\n", 1, 8, "anchor"),
])
def test_rejections(text, line, col, frag):
	with pytest.raises(SpecParseError) as e:
		L(text)
	assert frag in e.value.msg
	assert (e.value.line, e.value.col) == (line, col), (e.value.line, e.value.col)
	assert str(e.value).startswith(f"{line}:{col}:")
	assert e.value.exit_code == 2


def test_fixture_loads():
	d = yamlish.load(FIX, force_builtin=True)
	assert d["workbook"]["id"] == "ury_sales"
	assert d["queries"][0]["filters"] == [{"column": "docstatus", "op": "=", "value": 1}]
	assert d["dashboards"][0]["filters"][0]["default"] == {"op": "within", "value": "Last 30 days"}
	assert len(d["charts"]) == 3 and d["charts"][0]["y"][1]["agg"] == "count"
	assert d["desk"] == {"workspace": "URY", "label": "Sales Analytics"}


def test_builtin_equals_pyyaml():
	yaml = pytest.importorskip("yaml")
	text = FIX.read_text()
	assert yamlish.loads(text, force_builtin=True) == yaml.safe_load(text)


@pytest.mark.parametrize("tok", ["yes", "No", "ON", "off", "y", "N", "Y"])
def test_yes_no_stay_strings(tok):
	assert L(f"a: {tok}\n") == {"a": tok}


@pytest.mark.parametrize("tok,val", [("true", True), ("True", True), ("false", False), ("False", False), ("null", None), ("~", None)])
def test_bool_null(tok, val):
	assert L(f"a: {tok}\n") == {"a": val}


@pytest.mark.parametrize("tok", ["TRUE", "FALSE", "Null", "NULL"])
def test_other_case_forms_are_strings(tok):
	assert L(f"a: {tok}\n") == {"a": tok}


@pytest.mark.parametrize("tok", ["08", "0123", "007", "00.5", "1_000", "0x1f", "0o7", "1e3", "1.5e3", ".5", "1.", "2026-10-21"])
def test_zero_padded_and_nondecimal_are_strings(tok):
	assert L(f"a: {tok}\n") == {"a": tok}


@pytest.mark.parametrize("tok,val", [("0", 0), ("7", 7), ("-3", -3), ("1.5", 1.5), ("0.25", 0.25), ("-0.5", -0.5)])
def test_plain_decimals(tok, val):
	got = L(f"a: {tok}\n")["a"]
	assert got == val and type(got) is type(val)


def test_quoted_always_strings():
	assert L("a: \"5\"\nb: 'true'\nc: \"null\"\n") == {"a": "5", "b": "true", "c": "null"}


def test_flow_scalars_same_rules():
	assert L("a: [08, yes, 7, null]\n") == {"a": ["08", "yes", 7, None]}
