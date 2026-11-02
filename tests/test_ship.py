import json

import pytest

from finsights_lib import ship as shipmod
from finsights_lib import spec as specmod
from finsights_lib.common import FinsightsError
from finsights_lib.compile import compile_spec, dumps
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def spec():
	return specmod.load_spec(FIX / "ury_overview.yaml")


@pytest.fixture
def cat():
	return json.loads((FIX / "catalog.json").read_text())


@pytest.fixture
def app(tmp_path):
	pkg = tmp_path / "ury" / "ury"
	pkg.mkdir(parents=True)
	(pkg / "hooks.py").write_text('app_name = "ury"\n')
	return tmp_path / "ury"


def test_dry_run_writes_nothing(spec, cat, app):
	doc, _ = compile_spec(spec, cat)
	res = shipmod.ship(doc, spec, app)
	assert res.changed and any("workbook.json" in ln for ln in res.lines)
	assert not (app / "ury" / "workbook_templates").exists()
	assert (app / "ury" / "hooks.py").read_text() == 'app_name = "ury"\n'
	assert shipmod.DEPRECATED in res.warnings


def test_write_layout_and_manifest(spec, cat, app):
	doc, _ = compile_spec(spec, cat)
	shipmod.ship(doc, spec, app, write=True)
	d = app / "ury" / "workbook_templates" / "ury-sales"
	assert (d / "workbook.json").read_text() == dumps(doc)
	assert json.loads((d / "manifest.json").read_text()) == {
		"description": "", "module": "URY", "required_apps": ["ury"],
		"source_doctypes": ["POS Invoice"], "title": "URY Sales", "version": 1,
	}
	assert (app / "ury" / "hooks.py").read_text() == 'app_name = "ury"\ninsights_workbooks = "workbook_templates"\n'


def test_second_write_is_idempotent(spec, cat, app):
	doc, _ = compile_spec(spec, cat)
	shipmod.ship(doc, spec, app, write=True)
	before = (app / "ury" / "hooks.py").read_text()
	res = shipmod.ship(doc, spec, app, write=True)
	assert not res.changed and res.lines == ["no changes"]
	assert (app / "ury" / "hooks.py").read_text() == before
	assert before.count("insights_workbooks") == 1


def test_existing_hook_line_untouched(spec, cat, app):
	hp = app / "ury" / "hooks.py"
	hp.write_text('insights_workbooks = "other_dir"')
	doc, _ = compile_spec(spec, cat)
	shipmod.ship(doc, spec, app, write=True)
	assert hp.read_text() == 'insights_workbooks = "other_dir"'


def test_hooks_without_trailing_newline(spec, cat, app):
	hp = app / "ury" / "hooks.py"
	hp.write_text("x = 1")
	doc, _ = compile_spec(spec, cat)
	shipmod.ship(doc, spec, app, write=True)
	assert hp.read_text() == 'x = 1\ninsights_workbooks = "workbook_templates"\n'


def test_standard_requires_forward(spec, cat, app):
	doc, _ = compile_spec(spec, cat, "standard")
	with pytest.raises(FinsightsError) as e:
		shipmod.ship(doc, spec, app, standard=True)
	assert e.value.exit_code == 2


def test_standard_forward_layout(spec, cat, app):
	doc, _ = compile_spec(spec, cat, "standard")
	shipmod.ship(doc, spec, app, write=True, standard=True, forward=True)
	pkg = app / "ury"
	assert (pkg / "ury" / "insights_workbook" / "ury_sales" / "ury_sales.json").read_text() == dumps(doc)
	dash = json.loads((pkg / "ury" / "ury_dashboard" / "ury_sales" / "ury_sales.json").read_text())
	assert dash["insights_dashboard"] == "ury-sales-overview" and dash["is_standard"] == 1
	assert (pkg / "hooks.py").read_text() == 'app_name = "ury"\n'


def test_missing_hooks_exits_2(spec, cat, tmp_path):
	doc, _ = compile_spec(spec, cat)
	with pytest.raises(FinsightsError) as e:
		shipmod.ship(doc, spec, tmp_path)
	assert e.value.exit_code == 2
