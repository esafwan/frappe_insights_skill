import copy
import io
import json
import sys
import types
import urllib.error
from pathlib import Path

import pytest
from fakesite import FakeSite
from finsights_lib import api
from finsights_lib.client import Client
from finsights_lib.lint import lint_spec
from finsights_lib.spec import load_spec

FIX = Path(__file__).parent / "fixtures"
GOLDEN = json.loads((Path(__file__).parent / "golden" / "ury_overview.json").read_text())
ROWS = [{"posting_date": "2026-01-01", "branch": "b", "x": 1}]
WB = "Insights Workbook"


def spec():
	return load_spec(FIX / "ury_overview.yaml")


def cat():
	return json.loads((FIX / "catalog.json").read_text())


def mk(**kw):
	kw.setdefault("canned_rows", ROWS)
	site = FakeSite(**kw)
	return site, site.client()


def run(site_client, sp=None, **kw):
	site, c = site_client
	kw.setdefault("catalog", cat())
	return api.build(sp if sp is not None else spec(), client=c, **kw)


def other_spec(wid, title):
	s = spec()
	s["workbook"]["id"] = wid
	s["workbook"]["title"] = title
	return s


@pytest.fixture
def stub_heal(monkeypatch):
	"""Isolated stand-in for heal.py: fixes chart type aliases (pie -> Donut)."""
	m = types.ModuleType("finsights_lib.heal")

	def heal_spec(sp, catalog=None, *, max_rounds=3, allow="safe"):
		sp = copy.deepcopy(sp)
		applied, rounds = [], 0
		for _ in range(max_rounds):
			errs = [f for f in lint_spec(sp, catalog) if f.severity == "error"]
			fixes = []
			for ch in sp.get("charts", []):
				if ch.get("type") == "pie":
					ch["type"] = "Donut"
					fixes.append({"rule": "L09", "path": "/charts", "safe": True, "description": "pie->Donut", "patch": [], "alternatives": []})
			if not errs or not fixes:
				break
			applied += fixes
			rounds += 1
		rem = [f for f in lint_spec(sp, catalog) if f.severity == "error"]
		return {"spec": sp, "rounds": rounds, "applied": applied,
			"proposed": [{"rule": f.rule, "path": f.path, "safe": False, "description": f.message, "patch": [], "alternatives": []} for f in rem],
			"remaining": [f.to_dict() for f in rem]}

	m.heal_spec = heal_spec
	m.suggest = lambda findings, sp, catalog=None: [{"rule": f.rule, "path": f.path, "safe": False,
		"description": f.message, "patch": [], "alternatives": []} for f in findings if f.severity == "error"]
	monkeypatch.setitem(sys.modules, "finsights_lib.heal", m)
	monkeypatch.setattr("finsights_lib.heal", m, raising=False)
	return m


# ------------------------------------------------------------ build basics
def test_dry_run_plans_and_writes_nothing():
	site, c = mk()
	r = run((site, c))
	assert r["ok"] and r["stage"] == "plan" and r["write"] is False
	assert r["plan"]["creates"] == 1 + 1 + 3 + 1 and r["plan"]["updates"] == 0
	assert r["confirm"] and len(r["confirm"]) == 16
	assert r["applied"] is None and r["rounds"] == 0
	assert not [x for x in site.calls if x[0] in ("POST", "PUT") and "login" not in x[1]]
	assert sum(len(v) for v in site.docs.values()) == 0


def test_write_creates_everything_then_noop():
	site, c = mk()
	r = run((site, c), write=True)
	assert r["ok"] and r["stage"] == "done" and r["applied"]["changed"] is True
	assert r["verify"]["ok"] and r["workbook"]["live_name"]
	assert r["dashboards"][0]["path"] == "/insights/dashboards/" + r["dashboards"][0]["live_name"]
	assert sum(len(v) for v in site.docs.values()) == 6
	site.calls.clear()
	r2 = run((site, c), write=True)
	assert r2["ok"] and r2["applied"]["changed"] is False and r2["plan"]["creates"] == 0
	assert site.writes() == []


def test_result_is_json_serialisable_and_has_contract_keys():
	site, c = mk()
	r = run((site, c), write=True)
	d = json.loads(json.dumps(r))
	for k in ("ok", "stage", "write", "workbook", "findings", "plan", "applied", "verify", "dashboards", "rounds",
			"healed", "needs_decision", "spec", "confirm", "error"):
		assert k in d


def test_no_secrets_in_results():
	site, c = mk(password="s3cretpw-xyz")
	r = run((site, c), write=True)
	assert "s3cretpw-xyz" not in json.dumps(r)
	site2 = FakeSite(password="s3cretpw-xyz")
	bad = Client("http://site.test", user="Administrator", password="s3cretpw-xyz", opener=site2)
	site2.user = "other"
	r2 = api.build(spec(), client=bad, catalog=cat())
	assert r2["error"] and "s3cretpw-xyz" not in json.dumps(r2)


def test_never_prints(capsys):
	site, c = mk()
	run((site, c), write=True)
	out = capsys.readouterr()
	assert out.out == "" and out.err == ""


def test_offline_without_client(monkeypatch):
	for k in ("INSIGHTS_URL", "INSIGHTS_USER", "INSIGHTS_PASSWORD", "INSIGHTS_API_KEY", "INSIGHTS_API_SECRET"):
		monkeypatch.delenv(k, raising=False)
	r = api.build(spec(), catalog=cat())
	assert r["ok"] and r["plan"] is None and r["stage"] == "compile" and r["dashboards"][0]["live_name"] is None


def test_spec_from_path_and_text(tmp_path):
	site, c = mk()
	r = api.build(str(FIX / "ury_overview.yaml"), client=c, catalog=str(FIX / "catalog.json"))
	assert r["ok"] and r["plan"]["creates"] == 6
	r2 = api.build(json.dumps(spec()), client=c, catalog=cat())
	assert r2["ok"]
	r3 = api.build(str(tmp_path / "missing.yaml"), client=c)
	assert not r3["ok"] and r3["error"]["exit_code"] == 2 and r3["stage"] == "load"


def test_compiled_envelope_skips_lint():
	site, c = mk()
	env = copy.deepcopy(GOLDEN)
	r = api.build(env, client=c, write=True)
	assert r["ok"] and r["findings"] == [] and r["rounds"] == 0
	assert r["workbook"]["id"] == "ury-sales" and r["applied"]["changed"]


# ------------------------------------------------------------ lint / heal
def test_lint_error_stops_at_lint_and_writes_nothing():
	site, c = mk()
	sp = spec()
	sp["charts"][0]["query"] = "nope"
	r = run((site, c), sp, write=True, heal="off")
	assert not r["ok"] and r["stage"] == "lint" and r["plan"] is None
	assert any(f["severity"] == "error" for f in r["findings"])
	assert site.calls == []


def test_heal_safe_round_fixes_alias_then_proceeds(stub_heal):
	site, c = mk()
	sp = spec()
	sp["charts"][2]["type"] = "pie"
	r0 = run((site, c), copy.deepcopy(sp), heal="off")
	assert not r0["ok"]
	r = run((site, c), sp, write=True)
	assert r["ok"] and r["rounds"] >= 1 and r["healed"] and r["spec"]["charts"][2]["type"] == "Donut"
	assert r["stage"] == "done"


def test_unsafe_defect_needs_decision_and_writes_nothing(stub_heal):
	site, c = mk()
	sp = spec()
	sp["charts"][0]["query"] = "nope"
	r = run((site, c), sp, write=True)
	assert not r["ok"] and r["stage"] == "lint" and r["needs_decision"] and r["healed"] == []
	assert site.calls == []


def test_heal_propose_applies_nothing(stub_heal):
	site, c = mk()
	sp = spec()
	sp["charts"][2]["type"] = "pie"
	r = run((site, c), sp, heal="propose")
	assert not r["ok"] and r["healed"] == [] and r["needs_decision"] and r["spec"]["charts"][2]["type"] == "pie"


def test_heal_missing_module_degrades(monkeypatch):
	monkeypatch.setitem(sys.modules, "finsights_lib.heal", None)  # makes the import fail
	site, c = mk()
	sp = spec()
	sp["charts"][0]["query"] = "nope"
	r = run((site, c), sp)
	assert not r["ok"] and r["stage"] == "lint"


def test_bad_heal_value():
	r = api.build(spec(), heal="loud")
	assert not r["ok"] and r["error"]["exit_code"] == 2


# ------------------------------------------------------------ adoption / prune
def unmarked(site, title="URY Sales"):
	return site.client().post("/api/resource/" + WB, {"title": title})["data"]["name"]


def test_adoption_needs_confirm_with_alternatives():
	site, c = mk()
	unmarked(site)
	r = run((site, c))
	assert not r["ok"] and r["error"]["exit_code"] == 3 and r["stage"] == "plan"
	alts = r["needs_decision"][0]["alternatives"]
	assert {a["kind"] for a in alts} == {"adopt", "rename_workbook"}
	ren = next(a for a in alts if a["kind"] == "rename_workbook")
	assert ren["patch"][0]["value"] == "URY Sales (2)"


def test_adopt_with_correct_confirm_succeeds():
	site, c = mk()
	unmarked(site)
	dry = run((site, c), adopt=True)
	assert dry["ok"] and dry["confirm"]
	nowrite = run((site, c), adopt=True, write=True)
	assert not nowrite["ok"] and nowrite["error"]["exit_code"] == 3 and "confirm token required" in nowrite["error"]["message"]
	wrong = run((site, c), adopt=True, write=True, confirm="0" * 16)
	assert not wrong["ok"] and wrong["error"]["exit_code"] == 3
	assert sum(len(v) for k, v in site.docs.items() if k != WB) == 0
	r = run((site, c), adopt=True, write=True, confirm=dry["confirm"])
	assert r["ok"] and r["applied"]["changed"]
	assert next(iter(site.docs[WB].values()))["from_template"].startswith("finsights:")


def test_rename_alternative_patch_builds_second_workbook():
	site, c = mk()
	unmarked(site)
	r = run((site, c))
	sp = spec()
	sp["workbook"]["title"] = r["needs_decision"][0]["alternatives"][1]["patch"][0]["value"]
	r2 = run((site, c), sp, write=True)
	assert r2["ok"] and len(site.docs[WB]) == 2


def test_foreign_marker_alternative_is_rename_only():
	site, c = mk()
	site.client().post("/api/resource/" + WB, {"title": "URY Sales", "from_template": "finsights:other"})
	r = run((site, c))
	assert r["error"]["exit_code"] == 3
	assert [a["kind"] for a in r["needs_decision"][0]["alternatives"]] == ["rename_workbook"]


def test_ambiguity_lists_live_names():
	site, c = mk()
	a, b = unmarked(site), unmarked(site)
	r = run((site, c))
	assert r["error"]["exit_code"] == 3
	desc = r["needs_decision"][0]["description"]
	assert a in desc and b in desc


def test_prune_needs_confirm():
	site, c = mk()
	run((site, c), write=True)
	sp = spec()
	sp["charts"].pop()
	sp["dashboards"][0]["items"] = sp["dashboards"][0]["items"][:2]
	sp["dashboards"][0]["filters"] = []
	dry = run((site, c), copy.deepcopy(sp), prune=True)
	assert dry["ok"] and dry["plan"]["orphans"] == 1 and dry["confirm"]
	n = len(site.docs["Insights Chart v3"])
	r = run((site, c), copy.deepcopy(sp), prune=True, write=True)
	assert not r["ok"] and r["error"]["exit_code"] == 3 and len(site.docs["Insights Chart v3"]) == n
	r2 = run((site, c), copy.deepcopy(sp), prune=True, write=True, confirm=dry["confirm"])
	assert r2["ok"] and len(site.docs["Insights Chart v3"]) == n - 1


def test_token_is_deterministic_and_plan_sensitive():
	a = [{"op": "create", "doctype": "D", "title": "t", "live_name": None}]
	t = api.make_token("w", "T", a, False, False)
	assert t == api.make_token("w", "T", a, False, False) and len(t) == 16
	assert t != api.make_token("w", "T", a, True, False) and t != api.make_token("w", "T", a, False, True)


# ------------------------------------------------------------ failure / retry / verify
class Flaky:
	"""Opener wrapper: fails the first POST to a given doctype with HTTP 500."""

	def __init__(self, site, times=1, status=500, match="Chart%20v3"):
		self.site, self.left, self.status, self.match = site, times, status, match

	def open(self, req, timeout=None):
		if req.get_method() == "POST" and self.match in req.full_url and self.left > 0:
			self.left -= 1
			raise urllib.error.HTTPError(req.full_url, self.status, "err", {}, io.BytesIO(b"{}"))
		return self.site.open(req, timeout=timeout)


def flaky_client(site, **kw):
	return Client("http://site.test", user=site.user, password=site.password, opener=Flaky(site, **kw))


def test_partial_failure_then_automatic_retry_succeeds():
	site = FakeSite(canned_rows=ROWS)
	r = api.build(spec(), client=flaky_client(site), catalog=cat(), write=True)
	assert r["ok"] and r["stage"] == "done" and r["error"] is None
	assert r["applied"]["changed"] and any(l.startswith("created workbook") for l in r["applied"]["lines"])
	assert sum(len(v) for v in site.docs.values()) == 6


def test_persistent_failure_returns_error_with_partial_lines():
	site = FakeSite(canned_rows=ROWS)
	r = api.build(spec(), client=flaky_client(site, times=99), catalog=cat(), write=True)
	assert not r["ok"] and r["stage"] == "apply" and r["error"]["exit_code"] == 4
	assert r["error"]["partial_lines"]
	# idempotent: a later healthy run converges
	r2 = api.build(spec(), client=site.client(), catalog=cat(), write=True)
	assert r2["ok"] and sum(len(v) for v in site.docs.values()) == 6


def test_validation_error_not_retried_and_carries_message():
	site = FakeSite(canned_rows=ROWS)
	fl = Flaky(site, times=99, status=417)
	r = api.build(spec(), client=Client("http://site.test", user=site.user, password=site.password, opener=fl), catalog=cat(), write=True)
	assert not r["ok"] and r["error"]["exit_code"] == 4 and "HTTP 417" in r["error"]["message"]
	assert fl.left == 98


def test_verify_failure_sets_not_ok_and_needs_decision():
	site, c = mk(canned_rows=[{"x": 1}])  # no posting_date/branch -> V05
	r = run((site, c), write=True)
	assert not r["ok"] and r["stage"] == "verify" and r["verify"]["ok"] is False
	assert any(f["rule"] == "V05" for f in r["verify"]["findings"])
	assert r["needs_decision"] and r["needs_decision"][0]["alternatives"]
	assert r["applied"]["changed"]


def test_verify_can_be_skipped():
	site, c = mk(canned_rows=[{"x": 1}])
	r = run((site, c), write=True, verify=False)
	assert r["ok"] and r["verify"] is None


def test_auto_catalog_snapshot_when_missing(monkeypatch):
	site, c = mk()
	seen = {}

	def fake_snapshot(client, tables, ds="Site DB"):
		seen["tables"] = tables
		return cat()

	monkeypatch.setattr(api.catmod, "snapshot", fake_snapshot)
	r = api.build(spec(), client=c)
	assert seen["tables"] == ["POS Invoice"] and r["ok"]


# ------------------------------------------------------------ build_many
def test_build_many_happy_path_and_summary():
	site, c = mk()
	specs = [other_spec("wb_a", "WB A"), other_spec("wb_b", "WB B")]
	r = api.build_many(specs, client=c, catalog=cat(), write=True)
	assert r["ok"] and r["summary"] == {"total": 2, "ok": 2, "failed": 0, "skipped": 0}
	assert len(site.docs[WB]) == 2
	json.dumps(r)


def test_build_many_duplicate_guard_runs_nothing():
	site, c = mk()
	r = api.build_many([other_spec("wb_a", "A"), other_spec("wb_a", "B")], client=c, catalog=cat(), write=True)
	assert not r["ok"] and r["error"] and r["items"] == [] and site.calls == []
	r = api.build_many([other_spec("wb_a", "A"), other_spec("wb_b", "A")], client=c, catalog=cat(), write=True)
	assert not r["ok"] and site.calls == []


def test_build_many_max_items():
	site, c = mk()
	r = api.build_many([other_spec(f"wb_{i}x", f"T{i}") for i in range(3)], client=c, max_items=2, write=True)
	assert not r["ok"] and "max_items" in r["error"]["message"] and site.calls == []


def bad(wid, title):
	s = other_spec(wid, title)
	s["charts"][0]["query"] = "nope"
	return s


def test_build_many_isolation_and_stop_on_error():
	site, c = mk()
	specs = [other_spec("wb_a", "A"), bad("wb_bad", "Bad"), other_spec("wb_c", "C")]
	r = api.build_many(specs, client=c, catalog=cat(), write=True, heal="off")
	assert r["summary"] == {"total": 3, "ok": 2, "failed": 1, "skipped": 0} and not r["ok"]
	assert [i["ok"] for i in r["items"]] == [True, False, True]
	site2, c2 = mk()
	r2 = api.build_many(specs, client=c2, catalog=cat(), write=True, heal="off", stop_on_error=True)
	assert r2["summary"] == {"total": 3, "ok": 1, "failed": 1, "skipped": 1}
	assert r2["items"][2]["stage"] == "skipped" and len(site2.docs[WB]) == 1


def test_build_many_per_item_confirm_dict():
	site, c = mk()
	site.client().post("/api/resource/" + WB, {"title": "A"})
	specs = [other_spec("wb_a", "A")]
	dry = api.build_many(specs, client=c, catalog=cat(), adopt=True)
	tok = dry["items"][0]["confirm"]
	assert api.build_many(specs, client=c, catalog=cat(), adopt=True, write=True)["ok"] is False
	r = api.build_many(specs, client=c, catalog=cat(), adopt=True, write=True, confirm={"wb_a": tok})
	assert r["ok"]


# ------------------------------------------------------------ diff
def test_diff_same_added_removed_changed():
	a = copy.deepcopy(GOLDEN)
	assert api.diff(a, copy.deepcopy(a))["same"] is True
	b = copy.deepcopy(GOLDEN)
	ch = b["dependencies"]["charts"]
	k = next(iter(ch))
	title = ch[k]["title"]
	removed = ch.pop(k)
	d = api.diff(a, b)
	assert d["removed"] == {"charts": [title]} and not d["added"] and not d["same"]
	d2 = api.diff(b, a)
	assert d2["added"] == {"charts": [title]}
	c = copy.deepcopy(GOLDEN)
	c["dependencies"]["charts"][k]["config"] = {"limit": 7}
	d3 = api.diff(a, c)
	assert d3["changed"]["charts"][0]["title"] == title and "config" in d3["changed"]["charts"][0]["fields"]
	assert d3["added"] == {} and d3["removed"] == {}


def test_diff_ignores_names_and_roundtrips_json():
	a = copy.deepcopy(GOLDEN)
	b = copy.deepcopy(GOLDEN)
	# rename every doc key (live names) consistently
	for kind in ("queries", "charts", "dashboards"):
		for key in list(b["dependencies"][kind]):
			b["dependencies"][kind]["h_" + key] = b["dependencies"][kind].pop(key)
	assert api.diff(a, b)["same"] is True
	json.dumps(api.diff(a, b))


def test_diff_rejects_non_envelope():
	d = api.diff({"x": 1}, {"y": 2})
	assert d["same"] is False and d["error"]["exit_code"] == 2
