import copy
import json
from pathlib import Path

import pytest
from fakesite import FakeSite
from finsights_lib.apply import apply_plan, plan_apply
from finsights_lib.common import FinsightsError

GOLDEN = json.loads((Path(__file__).parent / "golden" / "ury_overview.json").read_text())
WB = "Insights Workbook"


def compiled():
	return copy.deepcopy(GOLDEN)


def run(site, comp=None, prune=False, write=True, **kw):
	comp = comp or compiled()
	c = site.client()
	plan = plan_apply(comp, c, **kw)
	return apply_plan(plan, comp, c, write=write, prune=prune), plan


def unmarked(site, title="URY Sales"):
	return site.client().post("/api/resource/" + WB, {"title": title})["data"]["name"]


def test_unmarked_title_refused_without_adopt():
	site = FakeSite()
	unmarked(site)
	with pytest.raises(FinsightsError) as e:
		plan_apply(compiled(), site.client())
	assert e.value.exit_code == 3 and "--adopt" in str(e.value) and "--prune" in str(e.value)


def test_unmarked_title_refused_even_dry_run_and_writes_nothing():
	site = FakeSite()
	unmarked(site)
	site.calls.clear()
	with pytest.raises(FinsightsError):
		run(site, write=False)
	assert site.writes() == []


def test_adopt_allows_and_sets_marker():
	site = FakeSite()
	n = unmarked(site)
	r, plan = run(site, adopt=True)
	assert plan.workbook_live_name == n and plan.title_matched
	assert site.docs[WB][n]["from_template"] == "finsights:ury-sales"


def test_prune_with_title_match_refused_even_with_adopt():
	site = FakeSite()
	unmarked(site)
	comp = compiled()
	c = site.client()
	plan = plan_apply(comp, c, adopt=True)
	site.calls.clear()
	with pytest.raises(FinsightsError) as e:
		apply_plan(plan, comp, c, write=True, prune=True)
	assert e.value.exit_code == 3 and "Adopt first" in str(e.value)
	assert site.writes() == []


def test_prune_allowed_once_marker_exists():
	site = FakeSite()
	unmarked(site)
	run(site, adopt=True)
	r, plan = run(site, prune=True)
	assert not plan.title_matched


def test_foreign_marker_still_requires_adopt():
	site = FakeSite()
	n = unmarked(site)
	site.client().put(f"/api/resource/{WB}/{n}", {"from_template": "ury/sales"})
	with pytest.raises(FinsightsError) as e:
		plan_apply(compiled(), site.client())
	assert e.value.exit_code == 3


def test_create_post_carries_marker():
	site = FakeSite()
	run(site)
	posts = [b for m, p, b in site.bodies if m == "POST" and p.endswith("/" + WB)]
	assert posts and posts[0]["from_template"] == "finsights:ury-sales"
	assert not [1 for m, p, b in site.bodies if m == "PUT" and p.startswith("/api/resource/" + WB)]


class Boom(Exception):
	pass


def failing_site(after):
	site = FakeSite()
	real = site.open
	state = {"n": 0}

	def op(req, timeout=None):
		if req.get_method() in ("POST", "PUT") and "login" not in req.full_url:
			state["n"] += 1
			if state["n"] > after:
				raise Boom("net down")
		return real(req, timeout)

	site.open = op
	return site


def test_failure_mid_apply_leaves_marker_and_rerun_completes():
	site = failing_site(after=3)
	comp = compiled()
	c = site.client()
	plan = plan_apply(comp, c)
	with pytest.raises(Boom):
		apply_plan(plan, comp, c, write=True)
	wbs = list(site.docs[WB].values())
	assert len(wbs) == 1 and wbs[0]["from_template"] == "finsights:ury-sales"
	site.open = FakeSite.open.__get__(site)
	run(site)
	r, _ = run(site)
	assert r.lines == ["no changes"]
	assert len(site.docs[WB]) == 1


def test_partial_lines_attached_and_printed(capsys):
	site = failing_site(after=2)
	comp = compiled()
	c = site.client()
	plan = plan_apply(comp, c)
	with pytest.raises(Boom) as e:
		apply_plan(plan, comp, c, write=True)
	assert len(e.value.partial_lines) == 2 and e.value.partial_lines[0].startswith("created workbook")
	assert "created workbook" in capsys.readouterr().err


def test_failure_at_first_write_has_no_partial_lines():
	site = failing_site(after=0)
	comp = compiled()
	c = site.client()
	plan = plan_apply(comp, c)
	with pytest.raises(Boom) as e:
		apply_plan(plan, comp, c, write=True)
	assert e.value.partial_lines == []


def test_rename_old_title_matching_other_spec_item_exit_3():
	site = FakeSite()
	run(site)
	comp = compiled()
	charts = comp["dependencies"]["charts"]
	keys = list(charts)
	other = charts[keys[1]]["title"] if len(keys) > 1 else None
	if other is None:
		pytest.skip("golden has one chart")
	with pytest.raises(FinsightsError) as e:
		plan_apply(comp, site.client(), renames={keys[0]: other})
	assert e.value.exit_code == 3 and "ambiguous" in str(e.value)


def test_rename_old_title_unmatched_still_ok():
	site = FakeSite()
	run(site)
	comp = compiled()
	comp["dependencies"]["charts"]["ury-sales-kpis"]["title"] = "Headline KPIs"
	plan = plan_apply(comp, site.client(), renames={"ury-sales-kpis": "Sales KPIs"})
	assert [a.op for a in plan.actions if a.title == "Headline KPIs"] == ["update"]
