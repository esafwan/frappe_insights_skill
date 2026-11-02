import json
from pathlib import Path

import pytest

from finsights_lib import ship as shipmod
from finsights_lib import spec as specmod
from finsights_lib.common import FinsightsError
from finsights_lib.compile import compile_spec

FIX = Path(__file__).resolve().parent / "fixtures"
LINE = 'insights_workbooks = "workbook_templates"'


@pytest.fixture
def ctx(tmp_path):
	spec = specmod.load_spec(FIX / "ury_overview.yaml")
	cat = json.loads((FIX / "catalog.json").read_text())
	doc, _ = compile_spec(spec, cat)
	pkg = tmp_path / "ury" / "ury"
	pkg.mkdir(parents=True)
	return doc, spec, tmp_path / "ury", pkg / "hooks.py"


def _ship(ctx, hooks_text, **kw):
	doc, spec, app, hp = ctx
	hp.write_text(hooks_text)
	return shipmod.ship(doc, spec, app, **kw)


def test_a_no_assignment_appends(ctx):
	_ship(ctx, "x = 1\n", write=True)
	assert ctx[3].read_text() == f"x = 1\n{LINE}\n"


def test_b_default_value_no_edit(ctx):
	_ship(ctx, f"{LINE}\n", write=True)
	assert ctx[3].read_text() == f"{LINE}\n"
	assert (ctx[3].parent / "workbook_templates" / "ury-sales").is_dir()


def test_c_other_dir_exit3(ctx):
	with pytest.raises(FinsightsError) as e:
		_ship(ctx, 'insights_workbooks = "templates"\n', write=True)
	assert e.value.exit_code == 3 and "--templates-dir templates" in str(e.value)
	assert not (ctx[3].parent / "workbook_templates").exists()
	assert not (ctx[3].parent / "templates").exists()


def test_c_templates_dir_matching_writes_there(ctx):
	_ship(ctx, 'insights_workbooks = "templates"\n', write=True, templates_dir="templates")
	assert (ctx[3].parent / "templates" / "ury-sales" / "workbook.json").is_file()
	assert not (ctx[3].parent / "workbook_templates").exists()
	assert ctx[3].read_text() == 'insights_workbooks = "templates"\n'


def test_templates_dir_mismatch_exit3(ctx):
	with pytest.raises(FinsightsError) as e:
		_ship(ctx, 'insights_workbooks = "templates"\n', templates_dir="other")
	assert e.value.exit_code == 3


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", "a\\b", ""])
def test_templates_dir_traversal_rejected(ctx, bad):
	with pytest.raises(FinsightsError):
		_ship(ctx, 'insights_workbooks = "templates"\n', templates_dir=bad)


def test_d_computed_exit3(ctx):
	with pytest.raises(FinsightsError) as e:
		_ship(ctx, 'insights_workbooks = "work" + "books"\n', write=True)
	assert e.value.exit_code == 3 and "verify" in str(e.value)


def test_commented_assignment_ignored(ctx):
	_ship(ctx, '# insights_workbooks = "templates"\n', write=True)
	assert ctx[3].read_text().endswith(f"{LINE}\n")


def test_docstring_assignment_ignored(ctx):
	_ship(ctx, '"""\ninsights_workbooks = "templates"\n"""\n', write=True)
	assert ctx[3].read_text().endswith(f"{LINE}\n")


def test_single_quotes(ctx):
	_ship(ctx, "insights_workbooks   =  'workbook_templates'\n", write=True)
	assert ctx[3].read_text() == "insights_workbooks   =  'workbook_templates'\n"
	with pytest.raises(FinsightsError):
		_ship(ctx, "insights_workbooks='templates'\n")


def test_idempotent_rerun(ctx):
	_ship(ctx, "x = 1\n", write=True)
	res = shipmod.ship(ctx[0], ctx[1], ctx[2], write=True)
	assert not res.changed and ctx[3].read_text().count("insights_workbooks") == 1


def test_dry_run_writes_nothing(ctx):
	res = _ship(ctx, "x = 1\n")
	assert res.changed and ctx[3].read_text() == "x = 1\n"
	assert not (ctx[3].parent / "workbook_templates").exists()


def test_unparsable_hooks_regex_fallback(ctx):
	_ship(ctx, "def (:\n", write=True)
	assert ctx[3].read_text().endswith(f"{LINE}\n")
	with pytest.raises(FinsightsError) as e:
		_ship(ctx, "def (:\ninsights_workbooks = 'templates'\n")
	assert e.value.exit_code == 3
