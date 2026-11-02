import copy
import json
from pathlib import Path

from fakesite import FakeSite
from finsights_lib.apply import apply_plan, plan_apply

GOLDEN = json.loads((Path(__file__).parent / "golden" / "ury_overview.json").read_text())


def run(site, prune=False):
	comp = copy.deepcopy(GOLDEN)
	c = site.client()
	plan = plan_apply(comp, c)
	return apply_plan(plan, comp, c, write=True, prune=prune), plan


def orphans(plan):
	return [a for a in plan.actions if a.op == "orphan"]


def add_query(site, name, title, operations, wb="1"):
	site.docs["Insights Query v3"][name] = {
		"name": name, "title": title, "operations": operations, "workbook": wb, "modified": "t0"}


def add_helpers(site):
	"""Mimic the server: each chart gets an auto-created empty data_query helper."""
	names = []
	for i, ch in enumerate(site.docs["Insights Chart v3"].values()):
		n = f"helper{i}"
		add_query(site, n, None, [])
		ch["data_query"] = n
		names.append(n)
	return names


def test_fresh_apply_then_plan_has_no_orphans():
	site = FakeSite()
	run(site)
	add_helpers(site)
	_, plan = run(site)
	assert orphans(plan) == []


def test_helpers_never_orphaned_or_deleted_with_prune():
	site = FakeSite()
	run(site)
	names = add_helpers(site)
	_, plan = run(site, prune=True)
	assert orphans(plan) == []
	assert all(n in site.docs["Insights Query v3"] for n in names)
	assert not any(c[0] == "DELETE" for c in site.calls)


def test_real_orphan_reported_and_pruned_only_with_prune():
	site = FakeSite()
	run(site)
	add_helpers(site)
	add_query(site, "real1", "Manual Query", [{"type": "source"}])
	_, plan = run(site)
	assert [a.live_name for a in orphans(plan)] == ["real1"]
	assert "real1" in site.docs["Insights Query v3"]
	run(site, prune=True)
	assert "real1" not in site.docs["Insights Query v3"]
	assert all(n in site.docs["Insights Query v3"] for n in ("helper0",))


def test_helper_detected_by_chart_data_query_reference():
	site = FakeSite()
	run(site)
	ch = next(iter(site.docs["Insights Chart v3"].values()))
	# titled and non-empty, but referenced by a chart: still a helper
	add_query(site, "ref1", "Titled Helper", [{"type": "source"}])
	ch["data_query"] = "ref1"
	_, plan = run(site)
	assert orphans(plan) == []


def test_null_title_empty_ops_safety_net_unreferenced():
	site = FakeSite()
	run(site)
	add_query(site, "stray1", None, [])
	add_query(site, "stray2", "", "")
	add_query(site, "stray3", None, "[]")
	_, plan = run(site)
	assert orphans(plan) == []
