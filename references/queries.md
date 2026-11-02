# Queries: data source, tables, operations grammar, expressions, running

Doctype `Insights Query v3` (never `Insights Query`, see v2-v3-traps.md). The query body is `operations`, a JSON list; the builder runs ops in order, each yields a new table. Src paths are relative to `apps/insights/insights`, FE = `apps/insights/frontend/src2`. `research/R2_queries.md` = T/research/R2_queries.md.

## Data source and tables
- The site DB is ONE fixture row named exactly `Site DB` (`is_site_db=1`, MariaDB). Never create it; reference it by that string: `"data_source": "Site DB"`. `fixtures/insights_data_source_v3.json:1-15`, `research/R2_queries.md` s1.
- Frappe tables are named `tab<DocType>`: DocType `POS Invoice` is table `tabPOS Invoice` (spaces kept). `research/R2_queries.md` s2.
- A query references `data_source` + raw `table_name`, NOT the `Insights Table v3` hash name (md5 of ds+table). `insights_table_v3.py:139,199-200`.
- Table list syncs from the DB (`update_data_source_tables`; saving the site DB triggers it; `^_` tables skipped). A table or custom field missing from Insights usually needs a resync. `api/data_sources.py:381-385`, `insights_data_source_v3.py:494-520`.
- `Insights Table Link v3` (built from Link/Table fields) is only a UI join suggestion: a join op carries its own explicit condition and execution never reads links. `research/R2_queries.md` s3.
- Table access applies Frappe permissions and user-permission row filters. `insights_table_v3.py:139-162`. See permissions.md.
- Verify column names against the live catalog (`finsights catalog`, sample `proof/live_catalog.json`); never guess a column. `research/R2_queries.md` Unknown 1.

## Query doc wrapper
`{"folder": null, "is_builder_query": 1, "is_native_query": 0, "is_script_query": 0, "name": "ury-sales-pos-invoices", "sort_order": 0, "title": "POS Invoices", "use_live_connection": 1, "workbook": "ury-sales", "operations": [...]}` (`design/finsights_spec.md` s6; wrapper keys `research/R2_queries.md` Shapes). `use_live_connection=1` queries the real DB (all ERPNext templates do); `0` reads the DuckDB copy. `insights_table_v3.py:152-156`.

## Operations grammar
Unknown `type` is silently ignored; `window_operation` exists in the TS types but has no Python handler, so never emit it. `ibis_utils.py:211-249`, FE `types/query.types.ts:183-201`. Sub-shapes: Column `{"type":"column","column_name":..}`; Expression `{"type":"expression","expression":"<ibis string>"}`.

| op | fields | literal example (cite) |
|---|---|---|
| source | `table` {type table, data_source, table_name} or {type query, workbook, query_name} | `{"table": {"data_source": "Site DB", "table_name": "tabPOS Invoice", "type": "table"}, "type": "source"}` (`proof/ury_sales_proof.json` queries) |
| filter | `column`, `operator`, `value` or `{expression}` | `{"type":"filter","column":{"type":"column","column_name":"description"},"operator":"contains","value":"x"}` (`tests/workbook/test_querying.py:~243`) |
| filter_group | `logical_operator` And/Or, `filters`[rule or {expression}] | `{"filters": [{"column": {"column_name": "docstatus", "type": "column"}, "operator": "=", "value": 1}], "logical_operator": "And", "type": "filter_group"}` (`proof/ury_sales_proof.json`) |
| join | `join_type` inner/left/right/full, `table`, `select_columns` (only these right columns kept), `join_condition` {left_column,right_column} or {join_expression}; clashing names get prefixed | `{"type":"join","join_type":"left","table":{"type":"table","data_source":"Site DB","table_name":"tabCompany"},"select_columns":[{"type":"column","column_name":"default_currency"}],"join_condition":{"left_column":{"type":"column","column_name":"company"},"right_column":{"type":"column","column_name":"name"}}}` (`workbook_templates/sales/workbook.json:~52-75`) |
| union | `table`, `distinct`; needs >=1 common column, result keeps only common columns | `{"distinct":false,"table":{"query_name":"payments-billed-invoices","type":"query","workbook":"payments"},"type":"union"}` (ERPNext `payments.json` op 2) |
| select | `column_names` [str] | `{"column_names": ["posting_date", "grand_total", "name", "branch"], "type": "select"}` (`proof/ury_sales_proof.json`) |
| rename | `column`, `new_name` | `{"type": "rename", "column": {"type": "column", "column_name": "status"}, "new_name": "Issue Status"}` (`tests/test_v2_query_translation.py:853`) |
| remove | `column_names` | UNVERIFIED shape (TS type only, no literal anywhere), prefer `select` |
| cast | `column`, `data_type` | `{"type":"cast","column":{"type":"column","column_name":"posting_time"},"data_type":"Time"}` (`tests/test_ibis_utils.py:27-31`) |
| mutate | `new_name`, `data_type`, `expression` | `{"data_type":"String","expression":{"expression":"name","type":"expression"},"new_name":"quotation","type":"mutate"}` (ERPNext `selling.json` first mutate) |
| summarize | `measures`[], `dimensions`[] | `{"type":"summarize","measures":[{"measure_name":"quotations","column_name":"count","data_type":"Integer","aggregation":"count"}],"dimensions":[]}` (`workbook_templates/sales/workbook.json:~239-250`) |
| order_by | `column`, `direction` asc/desc; ONE column per op, chain ops for multi-key; missing column skipped silently | `{"column":{"column_name":"period","type":"column"},"direction":"asc","type":"order_by"}` (ERPNext `accounts.json` op 5) |
| limit | `limit` int, clamped 1..1,000,000 | `{"type":"limit","limit":2}` (`tests/workbook/test_querying.py:166`) |
| sql | `raw_sql`, `data_source`; query needs `is_native_query=1`, single statement starting SELECT/WITH, tables rewritten through permission filters | `{"data_source":"Site DB","raw_sql":"SELECT 0 AS row_index\nUNION ALL SELECT 1 ...","type":"sql"}` (ERPNext `accounts.json` `accounts-week-spine`) |
| window_operation | none usable | ignored by Python (`ibis_utils.py:249`); use a mutate with `.over(...)` instead |
| pivot_wider | `rows`, `columns`, `values`, `max_column_values` (default 10, clamp 1..100, extras bucketed to "Others") | UNVERIFIED: no literal in templates/tests, untested live |
| custom_operation | `expression` replaces the table | UNVERIFIED: no literal found |

Summarize details (`ibis_utils.py:125-132,663,1066-1106`):
- Measure `{measure_name, column_name, aggregation, data_type}` or `{measure_name, expression, data_type}`. Aggregations: count, count_distinct, sum, avg, min, max. Row count = `column_name:"count"` + `aggregation:"count"`.
- Dimension `{dimension_name, column_name, data_type, granularity}`; Date/Datetime granularity second, minute, hour, day, week, month, quarter, year, fiscal_year; Time columns only second/minute/hour.
- Expression measure: `{"data_type":"Decimal","expression":{"expression":"sum(debit) - sum(credit)","type":"expression"},"measure_name":"movement"}` (ERPNext `accounts.json` `accounts-cash-bank-by-month` op 2).
- Window mutate: `{"data_type":"Decimal","expression":{"expression":"movement.sum().over(ibis.cumulative_window(order_by=period))","type":"expression"},"new_name":"cash_balance","type":"mutate"}` (same query op 3).

### Filter operators
`> < = != >= <= in not_in is_set is_not_set contains not_contains starts_with ends_with between within` (`ibis_utils.py:106-123`).

| column kind | operators to use | value |
|---|---|---|
| number (Integer, Decimal) | `= != > < >= <= between in not_in is_set is_not_set` | number; `between` `[lo,hi]`; `in` list |
| text (String) | `= != contains not_contains starts_with ends_with in not_in is_set is_not_set` | string; `contains` strips `%` |
| date, datetime | `= > < >= <= between within is_set is_not_set` | `between` `[start,end]` (bare dates become 00:00:00 / 23:59:59, `ibis_utils.py:498-506`); `within` timespan string e.g. `"Last 12 months"`, `"Last 7 days"` |
| any | value may be a Column to compare two columns (`:488-492`); `{"expression": ...}` rule | `{"expression":{"type":"expression","expression":"delivery_date < today()"}}` (`workbook_templates/sales/workbook.json:~392-398`) |

Dashboard (adhoc) filters are appended as a trailing `filter_group` on the query's FINAL output; expression filters are dropped. `ibis_utils.py:166-177`.

## Expressions (mutate, measure, filter)
Columns are referenced by bare name; helpers are in `insights/doctype/insights_data_source_v3/ibis/functions.py`. Useful ones:

| group | functions |
|---|---|
| aggregate | `count`, `count_if(cond)`, `sum`, `sum_if(cond, col)`, `avg`, `min`, `max`, `median`, `distinct_count`, `distinct_count_if`, `group_concat` |
| logic | `if_else(cond, a, b)`, `case`, `cases`, `one_if(cond)`, `coalesce`, `if_null`, `null_if`, `is_in`, `is_set`, `is_between` |
| number | `abs`, `round(col, n)`, `floor`, `ceil` |
| text | `lower`, `upper`, `concat`, `replace`, `substring`, `contains`, `starts_with`, `length`, `json_value`, `json_extract` |
| date parts | `year`, `quarter`, `month`, `week_of_year`, `day_of_year`, `day_of_week`, `day_name`, `day`, `hour`, `minute`, `second`, `format_date(col, '%Y-%m-%d')` |
| date math | `date_diff`, `time_diff`, `date_add(col, n, 'days')`, `date_sub`, `within(col, 'Last 7 days')`, `now()`, `today()`, `week_start`, `month_start`, `quarter_start`, `year_start` |
| window | `row_number`, `previous_value`, `next_value`, `previous_period_value`, `.over(ibis.cumulative_window(...))` |

Cites: `functions.py` defs (hour :844, date_diff :904, time_diff :923, date_add :946, within :972, month_start :1333). `within` accepts `Last [N] [Parts]`, `Current [Parts]`, `Next [N] [Parts]`, parts day/week/month/quarter/year/fiscal year (`functions.py:972-985`).

## Durations (LIVE findings, win over any research text)
- `date_diff(a, b, unit)`: casts both sides to DATE, so units are year, quarter, month, week, day only; no hours/minutes. `functions.py:904-920`.
- `time_diff(a, b, unit)`: casts both sides to TIME. That drops the date and fractions, so it is WRONG across midnight and for sub-second gaps. Unit `'millisecond'` errors on MariaDB. `functions.py:923-943`, `SWARM_STATE.md` 2026-11-02 live findings.
- Argument-order sign (a-b vs b-a) is UNVERIFIED on real data: demo gaps were milliseconds, which the TIME cast drops. `SWARM_STATE.md` U2 row.
- Rule: for prep-time / duration-between-timestamps metrics, use a native SQL op with `TIMESTAMPDIFF(SECOND, start_ts, end_ts)` (MariaDB), and accept the loss of builder-op joins.
- `hour(time)` is verified working on URY `KOT.time` (use for peak-hour dimensions). `SWARM_STATE.md` U2 row.

## Executing a query
`POST /api/method/insights.api.run_doc_method` with `{"method":"execute","docs":<FULL query doc>,"args":{"force":1,"page":1,"page_size":10}}`. `docs` must be the whole doc as returned by `GET /api/resource/Insights Query v3/<name>`; sending only `{doctype,name}` fails with a TypeError (operations None). Response: `message.rows` (list of dicts), `message.columns`, `message.sql`. `design/wpd_interfaces.md` fact 5, `api/__init__.py:300`, `insights_query_v3.py:220`.
- GET returns `operations` as a JSON STRING; `json.loads` before diffing. `design/wpd_interfaces.md` fact 1.
- `page_size` clamps 1..10000; results cache 600 s keyed on the SQL, bypass with `force:1`. `ibis_utils.py:1169-1186`, `insights_query_v3.py:~249`.
- `finsights verify` does this for every chart's source query (`Client.run_query`).

## Worked example: sales by day on POS Invoice
Pre-summarised query (legal, but a dashboard filter on a column dropped by `summarize`, e.g. `branch`, then has nothing to bind to). `research/R2_queries.md` s6:
```json
{
  "doctype": "Insights Query v3",
  "name": "ury-sales-daily",
  "title": "Daily Sales",
  "workbook": "ury-sales",
  "is_builder_query": 1, "is_native_query": 0, "is_script_query": 0,
  "use_live_connection": 1,
  "operations": [
    {"type": "source",
     "table": {"type": "table", "data_source": "Site DB", "table_name": "tabPOS Invoice"}},
    {"type": "filter_group", "logical_operator": "And",
     "filters": [{"column": {"type": "column", "column_name": "docstatus"}, "operator": "=", "value": 1}]},
    {"type": "summarize",
     "measures": [{"measure_name": "sales", "column_name": "grand_total", "data_type": "Decimal", "aggregation": "sum"}],
     "dimensions": [{"column_name": "posting_date", "data_type": "Date", "dimension_name": "posting_date", "granularity": "day"}]},
    {"type": "order_by", "column": {"type": "column", "column_name": "posting_date"}, "direction": "asc"}
  ]
}
```

## Preferred pattern: row-level query, charts aggregate
Keep the query row-level (source, filter_group docstatus=1, select) and let each chart aggregate, as ERPNext templates do. Dashboard filters then hit `posting_date`/`branch` in the query's FINAL output, and one query feeds many charts. Literal (`proof/ury_sales_proof.json`, `design/finsights_spec.md` s6):
```json
[{"table": {"data_source": "Site DB", "table_name": "tabPOS Invoice", "type": "table"}, "type": "source"},
 {"filters": [{"column": {"column_name": "docstatus", "type": "column"}, "operator": "=", "value": 1}], "logical_operator": "And", "type": "filter_group"},
 {"column_names": ["posting_date", "grand_total", "name", "branch"], "type": "select"}]
```
Charts: charts.md. Filter links: dashboards-filters.md. Order_by + limit on a row-level query keeps the OLDEST rows once a date filter is cleared, so size limits accordingly (`design/finsights_spec.md` s6 notes).

## Unverified
- `remove`, `pivot_wider`, `custom_operation` op shapes (TS types only).
- `time_diff` argument-order sign on real data; `date_diff` sign likewise.
- Whether `window_operation` is ever honoured (read as ignored, `ibis_utils.py:249`).
- Line numbers marked `~` are approximate (+-5).
