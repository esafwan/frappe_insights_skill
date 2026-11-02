"""Enums (verified against Insights 3.15.1 frontend types) and output-schema calculator."""

from __future__ import annotations

from .common import FinsightsError

CHART_TYPES = ("Number", "Bar", "Line", "Row", "Donut", "Funnel", "Table", "Map", "Bubble", "Sankey")
CHART_ALIASES = {"pie": "Donut", "kpi": "Number", "pivot": "Table"}
AGGREGATIONS = ("sum", "count", "avg", "min", "max", "count_distinct")
GRANULARITIES = ("second", "minute", "hour", "day", "week", "month", "quarter", "year", "fiscal_year")
JOIN_TYPES = ("inner", "left", "right", "full")
FILTER_TYPES = ("Date", "String", "Number")
GRID_COLS = 20
DATA_TYPES = ("String", "Integer", "Decimal", "Date", "Datetime", "Time", "Text", "JSON", "Array", "Auto")
DATA_FORMATS = ("currency", "percent")
LOGICAL_OPERATORS = ("And", "Or")
ORDER_DIRS = ("asc", "desc")

# every operator the query builder accepts (R2, ibis_utils.py:106-123)
ALL_FILTER_OPERATORS = (
	"=", "!=", ">", ">=", "<", "<=", "in", "not_in", "between", "within",
	"contains", "not_contains", "starts_with", "ends_with", "is_set", "is_not_set",
)
# per column kind (R4 filter_utils.ts:10-46)
FILTER_OPERATORS = {
	"string": ("in", "not_in", "contains", "not_contains", "starts_with", "ends_with", "is_set", "is_not_set"),
	"number": ("=", "!=", ">", ">=", "<", "<=", "between", "is_set", "is_not_set"),
	"date": ("between", "=", "!=", ">", ">=", "<", "<=", "within", "is_set", "is_not_set"),
}
# dashboard filter type -> column kind and compatible column data types (R4, constants.ts:3-5)
FILTER_TYPE_KIND = {"Date": "date", "String": "string", "Number": "number"}
FILTER_COLUMN_TYPES = {
	"Date": ("Date", "Datetime", "Time"),
	"String": ("Text", "String", "JSON", "Array"),
	"Number": ("Integer", "Decimal"),
}


def column_kind(data_type: str) -> str:
	"""Map an Insights column data type to a filter-operator kind."""
	if data_type in ("Integer", "Decimal"):
		return "number"
	if data_type in ("Date", "Datetime", "Time"):
		return "date"
	return "string"


def norm_chart_type(s: str) -> str:
	"""Normalise lowercase / aliased chart type names to the enum; raise on unknown."""
	if not isinstance(s, str):
		raise FinsightsError(f"chart type must be a string, got {s!r}")
	key = s.strip().lower()
	for t in CHART_TYPES:
		if t.lower() == key:
			return t
	if key in CHART_ALIASES:
		return CHART_ALIASES[key]
	raise FinsightsError(f"unknown chart type {s!r}; expected one of {', '.join(CHART_TYPES)}")


def _name(entry) -> str:
	return entry if isinstance(entry, str) else entry.get("column")


def output_schema(query: dict, catalog: dict, queries: dict | None = None) -> dict:
	"""FINAL output column -> data_type of a spec query (spec section 2).

	`catalog` is {DocType: {col: type}}; `queries` (optional) maps id -> spec query so
	`source_query` can be resolved. Raw `sql` queries have an unknown schema ({}).
	"""
	if query.get("sql"):
		return {}
	if query.get("table"):
		cols = dict(catalog.get(query["table"], {}))
	elif query.get("source_query"):
		src = (queries or {}).get(query["source_query"])
		cols = output_schema(src, catalog, queries) if src else {}
	else:
		cols = {}
	for j in query.get("joins") or []:
		jt = catalog.get(j.get("table"), {})
		for c in j.get("columns") or []:
			n = _name(c)
			cols[n] = jt.get(n, "String")
	for m in query.get("mutate") or []:
		cols[m["as"]] = m.get("type", "String")
	if query.get("select"):
		kept = {}
		for e in query["select"]:
			n = _name(e)
			if n in cols:
				kept[n] = cols[n]
		cols = kept
	s = query.get("summarize")
	if s:
		out = {}
		for d in s.get("by") or []:
			src_t = cols.get(d["column"], "String")
			out[d.get("as") or d["column"]] = "Date" if d.get("granularity") else src_t
		for m in s.get("measures") or []:
			agg = m.get("agg", "sum")
			col = m.get("column")
			name = m.get("as") or col or ("count" if agg == "count" else None)
			if agg in ("count", "count_distinct"):
				t = "Integer"
			elif agg in ("sum", "avg"):
				t = "Decimal"
			else:
				t = cols.get(col, "Decimal")
			if m.get("expr") and not col:
				t = m.get("type", "Decimal")
			out[name] = t
		cols = out
	return cols
