import copy
import json
from pathlib import Path

import pytest

from finsights_lib import spec as S
from finsights_lib import yamlish
from finsights_lib.common import Finding, FinsightsError, SpecParseError

FIX = Path(__file__).parent / "fixtures" / "ury_overview.yaml"


@pytest.fixture
def good():
	return S.load_spec(FIX)


def rules(spec):
	return [f.rule for f in S.shape_findings(spec)]


def test_valid_fixture_clean(good):
	assert S.shape_findings(good) == []


def test_finding_shape():
	f = Finding("L01", "error", "/x", "m", "h")
	assert f.to_dict() == {"rule": "L01", "severity": "error", "path": "/x", "message": "m", "fix_hint": "h"}
	assert FinsightsError("x").exit_code == 2 and FinsightsError("x", 3).exit_code == 3


def test_l01_unknown_key(good):
	good["charts"][0]["bogus"] = 1
	fs = S.shape_findings(good)
	assert [(f.rule, f.path) for f in fs] == [("L01", "/charts/0/bogus")]


def test_l01_missing_required(good):
	del good["queries"][0]["title"]
	del good["workbook"]["id"]
	paths = {f.path for f in S.shape_findings(good) if f.rule == "L01"}
	assert {"/queries/0/title", "/workbook/id"} <= paths


def test_l01_nested_and_source(good):
	good["dashboards"][0]["items"][0]["extra"] = 1
	good["queries"][0]["table"] = "A"
	good["queries"][0]["sql"] = "select 1"
	fs = S.shape_findings(good)
	assert {f.path for f in fs if f.rule == "L01"} == {"/dashboards/0/items/0/extra", "/queries/0"}


def test_l03_bad_and_duplicate_ids(good):
	good["charts"][1]["id"] = "Bad-Id"
	good["charts"][2]["id"] = "kpis"
	fs = [f for f in S.shape_findings(good) if f.rule == "L03"]
	assert {f.path for f in fs} == {"/charts/1/id", "/charts/2/id"}


def test_l03_cross_kind_duplicate(good):
	good["dashboards"][0]["filters"][0]["id"] = "pos_invoices"
	fs = S.shape_findings(good)
	assert [f.rule for f in fs] == ["L03"]


def test_l04_duplicate_titles(good):
	good["charts"][1]["title"] = good["charts"][0]["title"]
	fs = S.shape_findings(good)
	assert [(f.rule, f.path) for f in fs] == [("L04", "/charts/1/title")]


def test_json_equals_yaml(good, tmp_path):
	p = tmp_path / "s.json"
	p.write_text(json.dumps(good))
	assert S.load_spec(p) == good
	assert S.load_spec(FIX) == yamlish.load(FIX, force_builtin=True)


def test_load_errors(tmp_path):
	b = tmp_path / "b.json"
	b.write_text('{"a": ')
	with pytest.raises(SpecParseError):
		S.load_spec(b)
	y = tmp_path / "b.yaml"
	y.write_text("a:\n\tb: 1\n")
	with pytest.raises(SpecParseError) as e:
		S.load_spec(y)
	assert (e.value.line, e.value.col) == (2, 1)
	t = tmp_path / "b.txt"
	t.write_text("x")
	with pytest.raises(FinsightsError):
		S.load_spec(t)
