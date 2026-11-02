"""Catalog snapshot: {DocType: {column: Insights data_type}}, deterministic dump/load."""

from __future__ import annotations

import json
from pathlib import Path

from .common import FinsightsError

API = "/api/method/insights.api.data_sources.get_data_source_table_columns"


def spec_tables(spec: dict) -> list:
	"""Sorted DocTypes a spec reads (query tables and join tables)."""
	out = set()
	for q in (spec.get("queries") or []):
		if not isinstance(q, dict):
			continue
		if isinstance(q.get("table"), str):
			out.add(q["table"])
		for j in (q.get("joins") or []):
			if isinstance(j, dict) and isinstance(j.get("table"), str):
				out.add(j["table"])
	return sorted(out)


def snapshot(client, tables: list, data_source: str = "Site DB") -> dict:
	"""Fetch columns for each DocType through get_data_source_table_columns(data_source, 'tab<DocType>').

	The API returns a list of {column, label, type}; `type` is already an Insights data_type.
	"""
	cat: dict = {}
	for dt in sorted(set(tables)):
		resp = client.get(API, {"data_source": data_source, "table_name": f"tab{dt}"})
		rows = resp.get("message", resp) if isinstance(resp, dict) else resp
		if not isinstance(rows, list) or not rows:
			raise FinsightsError(f"no columns returned for DocType {dt!r} (unknown table or no permission)", 2)
		cols = {}
		for r in rows:
			if isinstance(r, dict) and r.get("column"):
				cols[r["column"]] = r.get("type") or "String"
		cat[dt] = dict(sorted(cols.items()))
	return cat


def dump(catalog: dict) -> str:
	return json.dumps(catalog, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def load(path) -> dict:
	p = Path(path)
	try:
		data = json.loads(p.read_text(encoding="utf-8"))
	except OSError as e:
		raise FinsightsError(f"cannot read catalog {path}: {e}", 2)
	except json.JSONDecodeError as e:
		raise FinsightsError(f"catalog {path} is not valid JSON: {e.msg} at {e.lineno}:{e.colno}", 2)
	if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
		raise FinsightsError(f"catalog {path} must be {{DocType: {{column: data_type}}}}", 2)
	return data
