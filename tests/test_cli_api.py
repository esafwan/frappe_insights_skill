import io
import json
import sys
import types
from pathlib import Path

import pytest

import finsights_lib
from finsights_lib import cli
from finsights_lib.cli import main

FIX = Path(__file__).resolve().parent / "fixtures"
SPEC = str(FIX / "ury_overview.yaml")
CAT = str(FIX / "catalog.json")
SMALL = {"workbook": {"id": "w", "title": "W"}}


def run(capsys, *argv):
	rc = main(list(argv))
	cap = capsys.readouterr()
	return rc, cap.out, cap.err


def _stub(monkeypatch, name, **fns):
	m = types.ModuleType(f"finsights_lib.{name}")
	for k, v in fns.items():
		setattr(m, k, v)
	monkeypatch.setitem(sys.modules, f"finsights_lib.{name}", m)
	monkeypatch.setattr(finsights_lib, name, m, raising=False)
	return m


def ok_result(**kw):
	r = {"ok": True, "stage": "done", "write": False, "workbook": {"id": "w", "title": "W", "live_name": None},
		"findings": [], "plan": {"creates": 2, "updates": 1, "orphans": 0, "actions": []}, "applied": None,
		"verify": None, "dashboards": [{"title": "D", "live_name": "d1", "path": "/insights/dashboards/d1"}],
		"rounds": 0, "healed": [], "needs_decision": [], "spec": SMALL, "confirm": "tok123", "error": None}
	r.update(kw)
	return r


@pytest.fixture
def calls():
	return []


@pytest.fixture
def api(monkeypatch, calls):
	def build(spec, **kw):
		calls.append(("build", spec, kw))
		return ok_result()

	def build_many(specs, **kw):
		calls.append(("build_many", specs, kw))
		items = [ok_result() for _ in specs]
		return {"ok": True, "items": items, "summary": {"total": len(items), "ok": len(items), "failed": 0, "skipped": 0}}

	return _stub(monkeypatch, "api", build=build, build_many=build_many)


@pytest.fixture
def specfile(tmp_path):
	p = tmp_path / "s.json"
	p.write_text(json.dumps(SMALL))
	return str(p)


def test_build_dry_run_json(capsys, api, calls, specfile):
	rc, out, _ = run(capsys, "--spec", specfile, "--json", "build")
	assert rc == 0
	data = json.loads(out)
	assert data["confirm"] == "tok123" and data["ok"] is True
	assert calls[0][2]["write"] is False and calls[0][2]["heal"] == "safe" and calls[0][1] == SMALL


def test_build_human_summary(capsys, api, monkeypatch, specfile):
	_stub(monkeypatch, "api", build=lambda s, **kw: ok_result(
		healed=[{"rule": "L09", "path": "/charts/0/type", "description": "pie->Donut"}],
		needs_decision=[{"rule": "L05", "path": "/q", "description": "unknown col", "alternatives": [{"description": "use amount"}]}]))
	rc, out, _ = run(capsys, "build", "--spec", specfile)
	assert rc == 0
	for s in ("stage=done", "2 create, 1 update, 0 orphan", "healed: L09", "needs_decision: L05", "alternative: use amount",
			"/insights/dashboards/d1", "confirm: tok123"):
		assert s in out


@pytest.mark.parametrize("res_kw,expected", [
	({"ok": False, "stage": "lint", "findings": [{"rule": "L07", "severity": "error", "path": "/x", "message": "m", "fix_hint": None}]}, 1),
	({"ok": False, "error": {"exit_code": 3, "message": "conflict", "partial_lines": [], "stage": "plan"}}, 3),
	({"ok": False, "error": {"exit_code": 4, "message": "auth", "partial_lines": [], "stage": "plan"}}, 4),
	({"ok": False, "stage": "verify", "verify": {"ok": False, "findings": []}}, 1),
])
def test_build_exit_codes(capsys, monkeypatch, specfile, res_kw, expected):
	_stub(monkeypatch, "api", build=lambda s, **kw: ok_result(**res_kw))
	rc, out, _ = run(capsys, "build", "--spec", specfile)
	assert rc == expected


def test_build_flags_passthrough(capsys, api, calls, specfile):
	rc, _, _ = run(capsys, "build", "--spec", specfile, "--write", "--heal", "propose", "--max-rounds", "5", "--adopt",
		"--prune", "--confirm", "abc", "--no-verify", "--min-rows", "3", "--catalog", CAT)
	kw = calls[0][2]
	assert rc == 0 and kw["write"] is True and kw["heal"] == "propose" and kw["max_rounds"] == 5
	assert kw["adopt"] and kw["prune"] and kw["confirm"] == "abc" and kw["verify"] is False and kw["min_rows"] == 3
	assert "POS Invoice" in kw["catalog"]


def test_build_stdin_spec(capsys, api, calls, monkeypatch):
	monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SMALL)))
	rc, _, _ = run(capsys, "build", "--spec", "-")
	assert rc == 0 and calls[0][1] == SMALL


def test_build_stdin_yaml(capsys, api, calls, monkeypatch):
	monkeypatch.setattr(sys, "stdin", io.StringIO("workbook:\n  id: w\n  title: W\n"))
	rc, _, _ = run(capsys, "build", "--spec", "-")
	assert rc == 0 and calls[0][1]["workbook"]["id"] == "w"


def test_build_requires_spec(capsys, api):
	assert run(capsys, "build")[0] == 2


def test_bulk_list_and_specs_object(capsys, api, calls, tmp_path, specfile):
	a = tmp_path / "a.json"
	a.write_text(json.dumps([SMALL, specfile]))
	rc, out, _ = run(capsys, "bulk", str(a), "--json")
	assert rc == 0 and json.loads(out)["summary"]["total"] == 2
	assert calls[0][1] == [SMALL, SMALL]
	b = tmp_path / "b.json"
	b.write_text(json.dumps({"specs": [SMALL]}))
	rc, out, _ = run(capsys, "bulk", str(b))
	assert rc == 0 and "total=1" in out and len(calls[1][1]) == 1


def test_bulk_stop_on_error_and_failure_exit(capsys, monkeypatch, tmp_path):
	seen = {}

	def many(specs, **kw):
		seen.update(kw)
		return {"ok": False, "items": [ok_result(ok=False, stage="lint"), ok_result()],
			"summary": {"total": 2, "ok": 1, "failed": 1, "skipped": 0}}

	_stub(monkeypatch, "api", build_many=many)
	f = tmp_path / "a.json"
	f.write_text(json.dumps([SMALL, SMALL]))
	rc, out, _ = run(capsys, "bulk", str(f), "--stop-on-error", "--max-items", "5")
	assert rc == 1 and seen["stop_on_error"] is True and seen["max_items"] == 5 and "failed=1" in out


def test_bulk_confirm_parsing(capsys, api, calls, tmp_path):
	f = tmp_path / "a.json"
	f.write_text(json.dumps([SMALL]))
	rc, _, _ = run(capsys, "bulk", str(f), "--confirm", "w=T1", "--confirm", "x=T=2")
	assert rc == 0 and calls[0][2]["confirm"] == {"w": "T1", "x": "T=2"}
	assert run(capsys, "bulk", str(f), "--confirm", "nope")[0] == 2


def test_bulk_bad_file(capsys, api, tmp_path):
	f = tmp_path / "a.json"
	f.write_text('"str"')
	assert run(capsys, "bulk", str(f))[0] == 2


def test_export_workbook_and_all(capsys, monkeypatch):
	seen = []
	_stub(monkeypatch, "export",
		export_workbook=lambda c, ref: seen.append(ref) or {"ok": True, "workbook": {"name": ref}, "summary": {}},
		export_all=lambda c: {"ok": True, "workbooks": [{"name": "a"}]})
	monkeypatch.setattr(cli, "_client", lambda: object())
	rc, out, _ = run(capsys, "export", "--workbook", "WB-1")
	assert rc == 0 and json.loads(out)["workbook"]["name"] == "WB-1" and seen == ["WB-1"]
	rc, out, _ = run(capsys, "export", "--all")
	assert rc == 0 and json.loads(out)["workbooks"][0]["name"] == "a"
	assert run(capsys, "export")[0] == 2


def test_export_output_file(capsys, monkeypatch, tmp_path):
	_stub(monkeypatch, "export", export_all=lambda c: {"ok": True, "workbooks": []})
	monkeypatch.setattr(cli, "_client", lambda: object())
	o = tmp_path / "out.json"
	rc, out, _ = run(capsys, "export", "--all", "-o", str(o))
	assert rc == 0 and json.loads(o.read_text()) == {"ok": True, "workbooks": []} and "wrote" in out


def test_review_exit_codes(capsys, monkeypatch):
	state = {"ok": False}
	got = {}

	def review(c, ref, min_rows=1):
		got["min_rows"] = min_rows
		return {"ok": state["ok"], "workbook": {"title": "W"}, "charts": [{"title": "C", "rows": 0, "ok": False, "error": "e"}],
			"dashboards": [], "findings": []}

	_stub(monkeypatch, "export", review=review)
	monkeypatch.setattr(cli, "_client", lambda: object())
	rc, out, _ = run(capsys, "review", "--workbook", "W", "--min-rows", "4")
	assert rc == 1 and got["min_rows"] == 4 and "chart C" in out
	state["ok"] = True
	assert run(capsys, "review", "--workbook", "W", "--json")[0] == 0


def test_heal_offline_output(capsys, monkeypatch, specfile, tmp_path):
	def heal_spec(spec, catalog=None, max_rounds=3, allow="safe"):
		return {"spec": spec, "rounds": 1, "applied": [{"rule": "L09"}], "proposed": [], "remaining": []}

	_stub(monkeypatch, "heal", heal_spec=heal_spec)
	rc, out, _ = run(capsys, "heal", "--spec", specfile)
	data = json.loads(out)
	assert rc == 0 and set(data) == {"spec", "rounds", "applied", "proposed", "remaining"}
	o = tmp_path / "h.json"
	assert run(capsys, "heal", "--spec", specfile, "-o", str(o))[0] == 0 and json.loads(o.read_text())["rounds"] == 1


def test_heal_remaining_errors_exit_1_and_stdin(capsys, monkeypatch):
	_stub(monkeypatch, "heal", heal_spec=lambda s, c=None, max_rounds=3, allow="safe": {
		"spec": s, "rounds": 3, "applied": [], "proposed": [],
		"remaining": [{"rule": "L05", "severity": "error", "path": "/x", "message": "m"}]})
	monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(SMALL)))
	assert run(capsys, "heal", "--spec", "-")[0] == 1


def test_help_lists_new_commands(capsys):
	rc, out, _ = run(capsys, "--help")
	assert rc == 0
	for c in ("build", "bulk", "export", "review", "heal", "lint", "compile"):
		assert c in out


def test_existing_commands_still_work(capsys):
	rc, out, _ = run(capsys, "--spec", SPEC, "lint", "--catalog", CAT)
	assert rc == 0 and out.strip() == "ok"
	rc, out, _ = run(capsys, "--spec", SPEC, "compile", "--catalog", CAT)
	assert rc == 0 and out.lstrip().startswith("{")
