import importlib
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from finsights_lib.cli import main

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
GOLD = HERE / "golden"
SPEC = str(FIX / "ury_overview.yaml")
CAT = str(FIX / "catalog.json")
SHIM = HERE.parent / "scripts" / "finsights"


def run(capsys, *argv):
	rc = main(list(argv))
	cap = capsys.readouterr()
	return rc, cap.out, cap.err


@pytest.fixture
def badspec(tmp_path):
	p = tmp_path / "bad.json"
	p.write_text(json.dumps({
		"workbook": {"id": "w", "title": "W"},
		"queries": [{"id": "q1", "title": "Q", "table": "POS Invoice"}],
		"charts": [{"id": "c1", "title": "C", "query": "nope", "type": "Line"}],
	}))
	return str(p)


def test_lint_clean(capsys):
	rc, out, _ = run(capsys, "--spec", SPEC, "lint", "--catalog", CAT)
	assert rc == 0 and out.strip() == "ok"


def test_lint_bad_exit_1(capsys, badspec):
	rc, out, _ = run(capsys, "lint", "--spec", badspec, "--catalog", CAT)
	assert rc == 1 and "L07" in out


def test_lint_json_and_only(capsys, badspec):
	rc, out, _ = run(capsys, "--json", "--spec", badspec, "lint", "--catalog", CAT, "--only", "L07")
	data = json.loads(out)
	assert rc == 1 and not data["ok"] and {f["rule"] for f in data["findings"]} == {"L07"}


def test_lint_strict_fails_on_warn(capsys, tmp_path):
	spec = json.loads(Path(SPEC).read_text()) if SPEC.endswith(".json") else None
	from finsights_lib import spec as specmod
	spec = specmod.load_spec(SPEC)
	spec["queries"].append({"id": "unused", "title": "Unused", "table": "POS Invoice"})
	p = tmp_path / "s.json"
	p.write_text(json.dumps(spec))
	assert run(capsys, "--spec", str(p), "lint", "--catalog", CAT)[0] == 0
	rc, out, _ = run(capsys, "--spec", str(p), "lint", "--catalog", CAT, "--strict")
	assert rc == 1 and "L24" in out


def test_compile_stdout_equals_golden(capsys):
	rc, out, _ = run(capsys, "--spec", SPEC, "compile", "--catalog", CAT)
	assert rc == 0 and out == (GOLD / "ury_overview.json").read_text()


def test_compile_standard_golden(capsys):
	rc, out, _ = run(capsys, "--spec", SPEC, "compile", "--catalog", CAT, "--format", "standard")
	assert rc == 0 and out == (GOLD / "ury_overview.standard.json").read_text()


def test_compile_check_ok_and_mismatch(capsys, tmp_path):
	assert run(capsys, "--spec", SPEC, "compile", "--catalog", CAT, "--check", str(GOLD / "ury_overview.json"))[0] == 0
	other = tmp_path / "x.json"
	other.write_text("{}\n")
	rc, _, err = run(capsys, "--spec", SPEC, "compile", "--catalog", CAT, "--check", str(other))
	assert rc == 1 and "differs" in err


def test_compile_output_file(capsys, tmp_path):
	o = tmp_path / "o.json"
	rc, _, _ = run(capsys, "--spec", SPEC, "compile", "--catalog", CAT, "-o", str(o))
	assert rc == 0 and o.read_text() == (GOLD / "ury_overview.json").read_text()


def test_compile_lint_errors_exit_1(capsys, badspec):
	rc, out, err = run(capsys, "--spec", badspec, "compile", "--catalog", CAT)
	assert rc == 1 and out == "" and "L07" in err


def test_fmt_to_json(capsys, tmp_path):
	rc, out, _ = run(capsys, "--spec", SPEC, "fmt", "--to", "json")
	assert rc == 0
	from finsights_lib import spec as specmod
	assert json.loads(out) == specmod.load_spec(SPEC)


def test_spec_parse_error_exit_2(capsys, tmp_path):
	p = tmp_path / "bad.yaml"
	p.write_text("a:\n\t- b\n")
	rc, _, err = run(capsys, "--spec", str(p), "lint")
	assert rc == 2 and err.startswith("error:")


def test_missing_spec_and_usage_errors_exit_2(capsys):
	assert run(capsys, "lint")[0] == 2
	assert run(capsys, "bogus")[0] == 2
	assert run(capsys, "--spec", SPEC, "compile", "--format", "xml")[0] == 2
	assert run(capsys, "--spec", "/nonexistent/x.yaml", "lint")[0] == 2


def test_help_exit_0(capsys):
	rc, out, _ = run(capsys, "--help")
	assert rc == 0 and "lint" in out


def test_desk_url_and_island(capsys, monkeypatch):
	monkeypatch.delenv("INSIGHTS_URL", raising=False)
	rc, out, _ = run(capsys, "--spec", SPEC, "desk", "--dashboard", "d9", "--app", "apps/ury")
	assert rc == 0 and "--url /insights/dashboards/d9" in out and "workspace add-shortcut URY" in out
	rc, out, _ = run(capsys, "--spec", SPEC, "desk", "--tier", "island")
	assert rc == 0 and json.loads(out)["insights_dashboard"] == "ury-sales-overview"


@pytest.fixture
def app(tmp_path):
	(tmp_path / "ury" / "ury").mkdir(parents=True)
	(tmp_path / "ury" / "ury" / "hooks.py").write_text("x = 1\n")
	return tmp_path / "ury"


def test_ship_dry_run_then_write(capsys, app):
	rc, out, err = run(capsys, "--spec", SPEC, "ship", "--catalog", CAT, "--app", str(app))
	assert rc == 0 and "dry-run" in out and "deprecated" in err
	assert not (app / "ury" / "workbook_templates").exists()
	rc, out, _ = run(capsys, "--write", "--spec", SPEC, "ship", "--catalog", CAT, "--app", str(app))
	assert rc == 0 and (app / "ury" / "workbook_templates" / "ury-sales" / "workbook.json").is_file()
	hooks = (app / "ury" / "hooks.py").read_text()
	rc, out, _ = run(capsys, "--spec", SPEC, "ship", "--catalog", CAT, "--app", str(app), "--write")
	assert rc == 0 and out.strip() == "no changes" and (app / "ury" / "hooks.py").read_text() == hooks


def test_ship_standard_needs_forward(capsys, app):
	rc, _, err = run(capsys, "--spec", SPEC, "ship", "--catalog", CAT, "--app", str(app), "--standard")
	assert rc == 2 and "--forward" in err
	rc, out, _ = run(capsys, "--spec", SPEC, "ship", "--catalog", CAT, "--app", str(app), "--standard", "--forward", "--write")
	assert rc == 0 and (app / "ury" / "ury" / "insights_workbook" / "ury_sales" / "ury_sales.json").is_file()


# ---- fakes for live commands (WP-D1 modules may not exist yet)

def _mod(monkeypatch, name):
	full = f"finsights_lib.{name}"
	try:
		return importlib.import_module(full)
	except ImportError:
		m = types.ModuleType(full)
		monkeypatch.setitem(sys.modules, full, m)
		return m


class FakeClient:
	def get(self, path, params=None):
		assert "get_data_source_table_columns" in path
		return {"message": [{"column": "name", "label": "name", "type": "String"},
			{"column": "grand_total", "label": "grand_total", "type": "Decimal"}]}


@pytest.fixture
def fake_site(monkeypatch):
	cm = _mod(monkeypatch, "client")
	monkeypatch.setattr(cm, "Client", type("Client", (), {"from_env": staticmethod(lambda: FakeClient())}), raising=False)
	return cm


def test_catalog_command_writes_deterministic_file(capsys, fake_site, tmp_path):
	o = tmp_path / "cat.json"
	rc, out, _ = run(capsys, "catalog", "--tables", "POS Invoice", "-o", str(o))
	assert rc == 0
	assert json.loads(o.read_text()) == {"POS Invoice": {"grand_total": "Decimal", "name": "String"}}


def test_lint_catalog_live(capsys, fake_site):
	rc, out, _ = run(capsys, "--spec", SPEC, "lint", "--catalog", "live")
	assert "L06" in out and rc == 1  # live fake only has two columns


def test_plan_and_apply_use_apply_module(capsys, monkeypatch, fake_site):
	am = _mod(monkeypatch, "apply")
	from types import SimpleNamespace as NS
	seen = {}

	def plan_apply(doc, client, adopt=False, renames=None):
		seen["plan"] = (adopt, renames)
		return NS(actions=[NS(op="create", doctype="Insights Query v3", key="q", live_name=None, fields={}, title="POS Invoices"),
			NS(op="noop", doctype="X", key="k", live_name="1", fields={}, title="T")], name_map={}, workbook_live_name=None)

	def apply_plan(plan, doc, client, write=False, prune=False):
		seen["apply"] = (write, prune)
		return NS(changed=True, lines=["+ create Insights Query v3 \"POS Invoices\""], names={})

	monkeypatch.setattr(am, "plan_apply", plan_apply, raising=False)
	monkeypatch.setattr(am, "apply_plan", apply_plan, raising=False)
	rc, out, _ = run(capsys, "--spec", SPEC, "plan", "--catalog", CAT, "--adopt", "--rename", "kpis=Old KPIs", "--exit-code")
	assert rc == 5 and out.strip() == '+ create Insights Query v3 "POS Invoices"'
	assert seen["plan"] == (True, {"kpis": "Old KPIs"})
	rc, out, _ = run(capsys, "--spec", SPEC, "apply", "--catalog", CAT, "--write", "--prune")
	assert rc == 0 and seen["apply"] == (True, True)


def test_verify_exit_codes(capsys, monkeypatch, fake_site):
	from finsights_lib.common import Finding
	vm = _mod(monkeypatch, "verify")
	am = _mod(monkeypatch, "apply")
	from types import SimpleNamespace as NS
	monkeypatch.setattr(am, "plan_apply", lambda doc, client, **k: NS(actions=[], name_map={"ury-sales-overview": "7"}), raising=False)
	monkeypatch.setattr(vm, "verify", lambda doc, client, min_rows=1: [], raising=False)
	rc, out, _ = run(capsys, "--spec", SPEC, "verify", "--catalog", CAT)
	assert rc == 0 and "/insights/dashboards/7" in out
	monkeypatch.setattr(vm, "verify", lambda doc, client, min_rows=1: [Finding("V02", "error", "/charts/x", "empty")], raising=False)
	assert run(capsys, "--spec", SPEC, "verify", "--catalog", CAT)[0] == 1


def test_network_error_exit_4(capsys, monkeypatch):
	from finsights_lib.common import FinsightsError
	cm = _mod(monkeypatch, "client")

	def fail():
		raise FinsightsError("cannot reach https://x (secret redacted)", 4)
	monkeypatch.setattr(cm, "Client", type("Client", (), {"from_env": staticmethod(fail)}), raising=False)
	rc, _, err = run(capsys, "catalog", "--tables", "POS Invoice")
	assert rc == 4 and "error:" in err


def test_shim_help_and_lint():
	r = subprocess.run([sys.executable, str(SHIM), "--help"], capture_output=True, text=True)
	assert r.returncode == 0 and "lint" in r.stdout
	r = subprocess.run([sys.executable, str(SHIM), "--spec", SPEC, "lint", "--catalog", CAT], capture_output=True, text=True)
	assert r.returncode == 0 and r.stdout.strip() == "ok"
	assert SHIM.stat().st_mode & 0o111
