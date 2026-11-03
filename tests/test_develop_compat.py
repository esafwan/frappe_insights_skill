"""Insights 3.50-dev (Frappe develop) compatibility shim: data source env, marker-less workbooks, config drift."""
import pytest

from finsights_lib import apply as A
from finsights_lib import compile as C
from finsights_lib.common import FinsightsError


def test_data_source_default_and_env(monkeypatch):
	monkeypatch.delenv("INSIGHTS_DATA_SOURCE", raising=False)
	assert C._table_ref("POS Invoice")["data_source"] == "Site DB"
	monkeypatch.setenv("INSIGHTS_DATA_SOURCE", "ury_db")
	assert C._table_ref("POS Invoice") == {"type": "table", "data_source": "ury_db", "table_name": "tabPOS Invoice"}


def test_to_develop_config_maps_comparison_and_granularity():
	cfg = {"comparison": True, "date_column": {"column_name": "d", "granularity": "month"},
		"number_columns": [{"measure_name": "x"}]}
	out = A.to_develop_config(cfg)
	assert out["number_column_options"] == [{"comparison": {"source": "previous"}}]
	assert out["window"] == {"grain": "month"} and "comparison" not in out
	assert "granularity" not in out["date_column"]


def test_contains_ignores_server_added_keys_only():
	want = {"a": 1, "l": [{"k": "v"}]}
	assert A._contains(want, {"a": 1, "l": [{"k": "v", "id": "x"}], "extra": {}})
	assert not A._contains(want, {"a": 2, "l": [{"k": "v"}]})
	assert not A._contains(want, {"a": 1, "l": []})


def test_diff_fields_tolerates_server_defaults():
	desired = {"config": {"number_columns": [{"measure_name": "x"}]}, "workbook": "w"}
	live = {"config": '{"number_columns": [{"measure_name": "x", "id": "x"}], "number_column_options": [{}]}', "workbook": "w"}
	assert A.diff_fields("chart", desired, live) == {}
	live["config"] = '{"number_columns": [{"measure_name": "y"}]}'
	assert "config" in A.diff_fields("chart", desired, live)


class _Dev:
	"""A client for a site whose Workbook has no from_template field."""
	def __init__(self, rows):
		self.rows = rows
	def has_field(self, dt, f):
		return f != "from_template"
	def list_docs(self, dt, filters=None, fields=None, limit=0):
		assert "from_template" not in (filters or {}) and "from_template" not in (fields or [])
		return [r for r in self.rows if r["title"] == filters["title"]]


COMPILED = {"doc": {"name": "wb", "title": "T"}}


def test_find_workbook_markerless_matches_title_and_blocks_prune():
	assert A._find_workbook(COMPILED, _Dev([]), False) is None
	hit = A._find_workbook(COMPILED, _Dev([{"name": "1", "title": "T"}]), False)
	assert hit["name"] == "1" and hit["_by_title"] is True  # _by_title keeps --prune refused
	with pytest.raises(FinsightsError):
		A._find_workbook(COMPILED, _Dev([{"name": "1", "title": "T"}, {"name": "2", "title": "T"}]), False)
