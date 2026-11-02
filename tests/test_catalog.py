import json

import pytest

from finsights_lib import catalog
from finsights_lib.common import FinsightsError


class FakeClient:
	def __init__(self, data):
		self.data = data
		self.calls = []

	def get(self, path, params=None):
		self.calls.append((path, params))
		return {"message": self.data.get(params["table_name"], [])}


ROWS = {
	"tabPOS Invoice": [
		{"column": "name", "label": "name", "type": "String"},
		{"column": "grand_total", "label": "grand_total", "type": "Decimal"},
		{"column": "posting_date", "label": "posting_date", "type": "Date"},
	],
}


def test_snapshot_against_fake_client():
	c = FakeClient(ROWS)
	cat = catalog.snapshot(c, ["POS Invoice"])
	assert cat == {"POS Invoice": {"grand_total": "Decimal", "name": "String", "posting_date": "Date"}}
	path, params = c.calls[0]
	assert path.endswith("get_data_source_table_columns")
	assert params == {"data_source": "Site DB", "table_name": "tabPOS Invoice"}


def test_snapshot_unknown_table_exits_2():
	with pytest.raises(FinsightsError) as e:
		catalog.snapshot(FakeClient({}), ["Nope"])
	assert e.value.exit_code == 2


def test_dump_load_roundtrip(tmp_path):
	cat = {"B": {"z": "String", "a": "Date"}, "A": {"x": "Integer"}}
	text = catalog.dump(cat)
	assert text.endswith("\n") and text.index('"A"') < text.index('"B"')
	assert text.index('"a"') < text.index('"z"')
	p = tmp_path / "c.json"
	p.write_text(text)
	assert catalog.load(p) == cat
	assert catalog.dump(catalog.load(p)) == text


def test_load_rejects_bad_shape(tmp_path):
	p = tmp_path / "c.json"
	p.write_text(json.dumps({"A": ["x"]}))
	with pytest.raises(FinsightsError):
		catalog.load(p)
	p.write_text("{nope")
	with pytest.raises(FinsightsError):
		catalog.load(p)


def test_spec_tables_includes_joins():
	spec = {"queries": [{"id": "a", "table": "B", "joins": [{"table": "A"}]}, {"id": "b", "source_query": "a"}]}
	assert catalog.spec_tables(spec) == ["A", "B"]
