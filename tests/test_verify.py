import copy
import json
from pathlib import Path

from fakesite import FakeSite
from finsights_lib.apply import apply_plan, plan_apply
from finsights_lib.verify import verify

GOLDEN = json.loads((Path(__file__).parent / "golden" / "ury_overview.json").read_text())


def applied(**kw):
	site = FakeSite(**kw)
	comp = copy.deepcopy(GOLDEN)
	c = site.client()
	apply_plan(plan_apply(comp, c), comp, c, write=True)
	return site, comp, c


def rules(fs):
	return sorted({f.rule for f in fs})


def test_verify_passes_on_fresh_apply():
	site, comp, c = applied()
	assert verify(comp, c) == []


def test_verify_v01_after_manual_drift():
	site, comp, c = applied()
	ch = next(d for d in site.docs["Insights Chart v3"].values() if d["title"] == "Daily Sales")
	ch["config"]["limit"] = 7
	assert rules(verify(comp, c)) == ["V01"]


def test_verify_v01_missing_workbook():
	site = FakeSite()
	assert rules(verify(copy.deepcopy(GOLDEN), site.client())) == ["V01"]


def test_verify_v02_empty_rows():
	site, comp, c = applied(canned_rows=[])
	assert "V02" in rules(verify(comp, c))


def test_verify_min_rows_param():
	site, comp, c = applied(canned_rows=[{"a": 1}])
	assert verify(comp, c, min_rows=2) and verify(comp, c, min_rows=1) == []


def test_verify_v03_query_error():
	site, comp, c = applied()
	site.fail_run.update(site.docs["Insights Query v3"])
	assert rules(verify(comp, c)) == ["V03"]


def test_verify_v04_linked_charts_mismatch():
	site, comp, c = applied()
	d = next(iter(site.docs["Insights Dashboard v3"].values()))
	d["linked_charts"] = d["linked_charts"][:1]
	assert rules(verify(comp, c)) == ["V04"]


def test_verify_runs_each_source_query_once():
	site, comp, c = applied()
	site.calls.clear()
	verify(comp, c)
	assert len([x for x in site.calls if x[1].endswith("run_doc_method")]) == 1
