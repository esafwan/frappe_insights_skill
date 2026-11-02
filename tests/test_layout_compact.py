import copy
import json
from pathlib import Path

from fakesite import FakeSite
from finsights_lib import apply as apply_mod, verify as verify_mod
from finsights_lib.apply import apply_plan, plan_apply, norm_dashboard_items, diff_fields
from finsights_lib.compile import compact_layout

GOLDEN = json.loads((Path(__file__).parent / "golden" / "ury_overview.json").read_text())


def mk(*boxes):
	return [{"type": "text", "layout": {"i": f"i{n}", "x": x, "y": y, "w": w, "h": h}}
		for n, (x, y, w, h) in enumerate(boxes)]


def ys(items):
	return [i["layout"]["y"] for i in items]


def test_gap_above_item_closes():
	assert ys(compact_layout(mk((0, 0, 4, 1), (12, 1, 4, 3)))) == [0, 0]


def test_side_by_side_unchanged():
	assert ys(compact_layout(mk((0, 0, 10, 2), (10, 0, 10, 2)))) == [0, 0]


def test_tall_item_blocks():
	items = compact_layout(mk((0, 0, 10, 5), (0, 9, 10, 2), (10, 0, 10, 1)))
	assert ys(items) == [0, 5, 0]


def test_fixed_point_and_idempotent():
	items = mk((0, 0, 4, 1), (4, 0, 4, 1), (0, 1, 20, 3), (0, 4, 10, 2))
	before = copy.deepcopy(items)
	assert compact_layout(items) == before
	a = compact_layout(mk((0, 0, 4, 1), (12, 3, 4, 3), (0, 9, 20, 2)))
	b = copy.deepcopy(a)
	assert compact_layout(a) == b


def test_deterministic():
	r = [compact_layout(mk((8, 4, 4, 2), (0, 7, 4, 2), (0, 2, 4, 2))) for _ in range(3)]
	assert r[0] == r[1] == r[2]


def test_never_reorders_and_keeps_xwh():
	src = mk((0, 5, 3, 2), (6, 0, 3, 2), (0, 9, 3, 2))
	out = compact_layout(copy.deepcopy(src))
	assert [i["layout"]["i"] for i in out] == ["i0", "i1", "i2"]
	for a, b in zip(src, out):
		assert all(a["layout"][k] == b["layout"][k] for k in "xwh")


def test_normaliser_ignores_moved_and_rewrites_names():
	a = [{"type": "chart", "chart": "ph", "layout": {"i": "c", "x": 0, "y": 0, "w": 4, "h": 2}}]
	b = [{"type": "chart", "chart": "live", "layout": {"i": "c", "x": 0, "y": 0, "w": 4, "h": 2, "moved": False}}]
	assert norm_dashboard_items(a, {"ph": "live"}) == norm_dashboard_items(b)


def _dash_site():
	site = FakeSite(canned_rows=[{"posting_date": "2026-01-01", "branch": "b", "x": 1}])
	comp = copy.deepcopy(GOLDEN)
	c = site.client()
	apply_plan(plan_apply(comp, c), comp, c, write=True)
	dash = next(iter(site.docs["Insights Dashboard v3"].values()))
	return site, comp, c, dash


def test_moved_false_noise_is_no_drift():
	site, comp, c, dash = _dash_site()
	items = json.loads(dash["items"]) if isinstance(dash["items"], str) else dash["items"]
	for it in items:
		it["layout"]["moved"] = False
	dash["items"] = json.dumps(items) if isinstance(dash["items"], str) else items
	assert all(a.op == "noop" for a in plan_apply(comp, c).actions)
	assert verify_mod.verify(comp, c) == []


def test_unexplained_y_difference_is_reported():
	site, comp, c, dash = _dash_site()
	items = json.loads(dash["items"]) if isinstance(dash["items"], str) else dash["items"]
	items[-1]["layout"]["y"] += 5
	dash["items"] = json.dumps(items) if isinstance(dash["items"], str) else items
	acts = [a for a in plan_apply(comp, c).actions if a.op == "update"]
	assert acts and "items" in acts[0].fields
	assert [f.rule for f in verify_mod.verify(comp, c)] == ["V01"]


def test_both_apply_and_verify_use_shared_normaliser(monkeypatch):
	site, comp, c, dash = _dash_site()
	calls = []
	orig = apply_mod.norm_dashboard_items

	def spy(*a, **k):
		calls.append(1)
		return orig(*a, **k)
	monkeypatch.setattr(apply_mod, "norm_dashboard_items", spy)
	plan_apply(comp, c)
	n_apply = len(calls)
	assert n_apply >= 2
	verify_mod.verify(comp, c)
	assert len(calls) > n_apply


def test_diff_fields_dashboard_uses_normaliser():
	want = {"title": "t", "items": mk((0, 0, 4, 1))}
	live = {"title": "t", "items": json.dumps([dict(want["items"][0], layout=dict(want["items"][0]["layout"], moved=False))])}
	assert diff_fields("dashboard", want, live) == {}


def test_golden_dashboard_layouts_are_compact():
	for d in GOLDEN["dependencies"]["dashboards"].values():
		before = copy.deepcopy(d["items"])
		assert compact_layout(copy.deepcopy(d["items"])) == before
