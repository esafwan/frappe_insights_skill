import copy
import json
from pathlib import Path

import pytest
from fakesite import FakeSite
from finsights_lib.apply import apply_plan, plan_apply
from finsights_lib.common import FinsightsError

GOLDEN = json.loads((Path(__file__).parent / "golden" / "ury_overview.json").read_text())


def compiled():
	return copy.deepcopy(GOLDEN)


def run(site, comp=None, **kw):
	comp = comp or compiled()
	c = site.client()
	prune = kw.pop("prune", False)
	write = kw.pop("write", True)
	plan = plan_apply(comp, c, **kw)
	return apply_plan(plan, comp, c, write=write, prune=prune), plan


def n_docs(site):
	return sum(len(v) for v in site.docs.values())


def test_first_apply_creates_all_second_is_noop():
	site = FakeSite()
	r1, _ = run(site)
	assert r1.changed and n_docs(site) == 1 + 1 + 3 + 1
	site.calls.clear()
	r2, _ = run(site)
	assert r2.changed is False and r2.lines == ["no changes"]
	assert site.writes() == []


def test_workbook_marker_and_counter_name():
	site = FakeSite()
	run(site)
	wb = site.docs["Insights Workbook"]["1"]
	assert wb["from_template"] == "finsights:ury-sales" and wb["title"] == "URY Sales"


def test_children_have_hash_names_and_workbook_link():
	site = FakeSite()
	run(site)
	for dt in ("Insights Query v3", "Insights Chart v3", "Insights Dashboard v3"):
		for nm, d in site.docs[dt].items():
			assert nm.startswith("h") and d["workbook"] == "1"


def test_data_query_and_linked_charts_never_written():
	site = FakeSite()
	run(site)
	for m, p, b in site.bodies:
		if isinstance(b, dict):
			assert "data_query" not in b and "linked_charts" not in b


def test_name_map_rewriting_everywhere():
	site = FakeSite()
	run(site)
	q = next(iter(site.docs["Insights Query v3"]))
	charts = site.docs["Insights Chart v3"]
	assert all(c["query"] == q for c in charts.values())
	dash = next(iter(site.docs["Insights Dashboard v3"].values()))
	items = dash["items"]
	chart_items = [i["chart"] for i in items if i["type"] == "chart"]
	assert set(chart_items) == set(charts)
	for it in items:
		if it["type"] == "filter":
			assert set(it["links"]) == set(charts)
			assert all(v.startswith("`%s`." % q) for v in it["links"].values())
	assert "ury-sales" not in json.dumps(dash["items"]) and "ury-sales" not in json.dumps(site.docs["Insights Query v3"])


def test_query_to_query_reference_rewritten_and_topological():
	comp = compiled()
	qs = comp["dependencies"]["queries"]
	base = qs["ury-sales-pos-invoices"]
	qs["a-derived"] = {**copy.deepcopy(base), "name": "a-derived", "title": "Derived", "sort_order": 0,
		"operations": [{"type": "source", "table": {"type": "query", "workbook": "ury-sales",
		"query_name": "ury-sales-pos-invoices"}}]}
	base["sort_order"] = 5
	site = FakeSite()
	run(site, comp)
	posts = [p for m, p, b in site.bodies if m == "POST" and p.endswith("Query v3")]
	assert len(posts) == 2
	bodies = [b for m, p, b in site.bodies if m == "POST" and p.endswith("Query v3")]
	assert bodies[0]["title"] == "POS Invoices"  # dependency first despite sort_order
	base_live = next(n for n, d in site.docs["Insights Query v3"].items() if d["title"] == "POS Invoices")
	derived = next(d for d in site.docs["Insights Query v3"].values() if d["title"] == "Derived")
	assert derived["operations"][0]["table"]["query_name"] == base_live
	assert derived["operations"][0]["table"]["workbook"] == "1"


def test_title_only_edit_with_rename_is_one_put():
	site = FakeSite()
	run(site)
	comp = compiled()
	comp["dependencies"]["charts"]["ury-sales-kpis"]["title"] = "Headline KPIs"
	site.calls.clear()
	r, _ = run(site, comp, renames={"ury-sales-kpis": "Sales KPIs"})
	puts = [c for c in site.calls if c[0] == "PUT"]
	assert len(puts) == 1 and not [c for c in site.writes() if c[0] == "POST"]
	assert r.changed


def test_title_edit_without_rename_is_create_plus_orphan():
	site = FakeSite()
	run(site)
	comp = compiled()
	comp["dependencies"]["charts"]["ury-sales-kpis"]["title"] = "Headline KPIs"
	plan = plan_apply(comp, site.client())
	ops = [(a.op, a.title) for a in plan.actions if a.op in ("create", "orphan", "update")]
	assert ("create", "Headline KPIs") in ops and ("orphan", "Sales KPIs") in ops


def test_chart_config_change_is_one_put():
	site = FakeSite()
	run(site)
	comp = compiled()
	comp["dependencies"]["charts"]["ury-sales-sales-by-branch"]["config"]["limit"] = 5
	site.calls.clear()
	site.bodies.clear()
	run(site, comp)
	assert [c[0] for c in site.writes()] == ["PUT"]
	puts = [b for m, p, b in site.bodies if m == "PUT"]
	assert list(puts[0]) == ["config"]


def test_layout_moved_and_server_fields_ignored_in_diff():
	site = FakeSite()
	run(site)
	for d in site.docs["Insights Dashboard v3"].values():
		for it in d["items"]:
			it["layout"]["moved"] = True
		d["modified"] = "zzz"
	site.calls.clear()
	r, _ = run(site)
	assert r.changed is False and site.writes() == []


def test_ambiguous_workbook_title_exit_3():
	site = FakeSite()
	c = site.client()
	c.post("/api/resource/Insights Workbook", {"title": "URY Sales"})
	c.post("/api/resource/Insights Workbook", {"title": "URY Sales"})
	with pytest.raises(FinsightsError) as e:
		plan_apply(compiled(), c)
	assert e.value.exit_code == 3


def test_ambiguous_marker_exit_3():
	site = FakeSite()
	c = site.client()
	for _ in range(2):
		n = c.post("/api/resource/Insights Workbook", {"title": "x"})["data"]["name"]
		c.put("/api/resource/Insights Workbook/" + n, {"from_template": "finsights:ury-sales"})
	with pytest.raises(FinsightsError) as e:
		plan_apply(compiled(), c)
	assert e.value.exit_code == 3


def test_template_managed_workbook_needs_adopt():
	site = FakeSite()
	c = site.client()
	n = c.post("/api/resource/Insights Workbook", {"title": "URY Sales"})["data"]["name"]
	c.put("/api/resource/Insights Workbook/" + n, {"from_template": "ury/sales"})
	with pytest.raises(FinsightsError) as e:
		plan_apply(compiled(), c)
	assert e.value.exit_code == 3
	plan = plan_apply(compiled(), c, adopt=True)
	assert plan.workbook_live_name == n


def test_title_fallback_adopts_unmarked_workbook():
	site = FakeSite()
	c = site.client()
	n = c.post("/api/resource/Insights Workbook", {"title": "URY Sales"})["data"]["name"]
	r, plan = run(site)
	assert plan.workbook_live_name == n and len(site.docs["Insights Workbook"]) == 1
	assert site.docs["Insights Workbook"][n]["from_template"] == "finsights:ury-sales"


def test_dry_run_zero_writes():
	site = FakeSite()
	r, _ = run(site, write=False)
	assert r.changed and site.writes() == [] and n_docs(site) == 0
	assert any("would create" in l for l in r.lines)


def test_orphans_kept_without_prune_deleted_in_order_with_prune():
	site = FakeSite()
	run(site)
	comp = compiled()
	del comp["dependencies"]["charts"]["ury-sales-kpis"]
	for it in list(comp["dependencies"]["dashboards"]["ury-sales-overview"]["items"]):
		if it.get("chart") == "ury-sales-kpis":
			comp["dependencies"]["dashboards"]["ury-sales-overview"]["items"].remove(it)
		elif it["type"] == "filter":
			it["links"].pop("ury-sales-kpis")
	r, _ = run(site, comp)
	assert len(site.docs["Insights Chart v3"]) == 3 and any(l.startswith("orphan") for l in r.lines)
	site.calls.clear()
	r, _ = run(site, comp, prune=True)
	assert len(site.docs["Insights Chart v3"]) == 2
	assert [c[0] for c in site.calls if c[0] == "DELETE"] == ["DELETE"]


def test_prune_order_dashboards_charts_queries():
	site = FakeSite()
	run(site)
	empty = compiled()
	empty["dependencies"] = {"queries": {}, "charts": {}, "dashboards": {}, "folders": {}}
	site.calls.clear()
	run(site, empty, prune=True)
	kinds = [c[1].split("/")[3] for c in site.calls if c[0] == "DELETE"]
	assert kinds == ["Insights Dashboard v3"] + ["Insights Chart v3"] * 3 + ["Insights Query v3"]


def test_secrets_never_in_result():
	site = FakeSite(password="hunter2hunter2")
	r, plan = run(site)
	blob = repr(r) + repr(plan) + "\n".join(r.lines)
	assert "hunter2hunter2" not in blob


def test_result_names_maps_titles_to_live():
	site = FakeSite()
	r, _ = run(site)
	assert r.names["workbook"] == {"URY Sales": "1"}
	assert set(r.names["chart"]) == {"Sales KPIs", "Daily Sales", "Sales by Branch"}
	assert set(r.names["chart"].values()) == set(site.docs["Insights Chart v3"])
