# Insights v3 charts (Insights Chart v3)

Ratio, hour-of-day and cast measures for charts: see [expressions.md](expressions.md).
Target: Insights 3.15.1. Paths: FE = `apps/insights/frontend/src2` in the Insights repo (v3 UI; `src` is v2); other paths are relative to the skill root. A chart is a `chart_type` + a `config` JSON over the columns of one query. See [queries.md](queries.md) for the query and [dashboards-filters.md](dashboards-filters.md) for placement.

## Live findings (verified on Frappe 16.54.0 + Insights 3.15.1, 2026-11-02; they win over source reading where they differ)
1. **A chart needs only `chart_type`, `config`, `query`, `title`, `workbook`.** The backend never reads `config`; the browser builds the chart's `data_query` from it at render time. Never write `data_query`: it is auto-created empty. API/REST/import-created charts render on a dashboard without opening the editor. 2026-11-02 LIVE PROOF #1
2. **Number chart semantics.** WITHOUT `date_column`: the TOTAL of the filtered rows. WITH `date_column` (+`comparison`/`sparkline`): the LATEST period, plus the % change vs the previous period. Verified live: Oct 21 = 19 invoices / 18,005, -25.24% vs Oct 20 (24,085), equal to SQL. 2026-11-02 LIVE FINDINGS. Code: current = last row, previous = second-to-last (`FE/charts/components/NumberChart.vue:44-50`)
3a. **Dashboard Date filters reach Number charts without a `date_column`.** The filter binds through `links` to the chart's query column, so a Number KPI with no date column still obeys it (verified live: 440 invoices / 618,750.00 under 'within Last 1 months', equal to SQL for `posting_date` >= first of that month, 2026-11-02). `Last N days` alignment is unverified; see dashboards-filters.md.
3. **Develop-only keys are ignored/unverified on 3.15.1: `number_format`, `number_formats`, `window`, an object-valued `number_column_options[].comparison`, `id` on a number column, `layouts.sm`.** They appear in ERPNext/HRMS develop workbooks (version skew). Do not emit them. 3.15.1 knows per option only `shorten_numbers/decimal/prefix/suffix/color` and a boolean top-level `comparison`., `FE/types/chart.types.ts:105-111`
4. **Line + `order_by` asc + `limit` keeps the OLDEST rows when no date filter applies.** A "last 30 days" line with `limit:30` shows the first 30 days of history. Either give the dashboard/chart a date filter, order desc and accept the reversed fetch, or set `limit` above the bucket count (goldens use 366 for daily). Verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02; `tests/golden/ury_overview.json` Line `limit:366`

## The 10 chart types
`chart_type` is a case-sensitive Data field: `Number, Bar, Line, Row, Donut, Funnel, Table, Map, Bubble, Sankey`. The backend does not validate it; the frontend rejects unknown values. `FE/types/chart.types.ts:4,7`; `FE/charts/chart.ts:115`

| Name people use | Use |
|---|---|
| Pie | `Donut` |
| KPI, big number, scorecard | `Number` |
| Pivot | `Table` with non-empty `columns` (becomes pivot_wider, `max_column_values` default 10) |
| Horizontal bar | `Row` (Bar with swapped axes, top item at top) |

`FE/charts/chart.ts:~290`; `FE/charts/components/ChartRenderer.vue:52-57`

## Chooser
| Question shape | Type | Limits / notes |
|---|---|---|
| One headline total (sales today, open orders) | Number | one card per `number_columns` entry; add `date_column` for latest period + delta |
| Trend over time | Line | Date dimension with `granularity`; order_by asc on the date alias |
| Compare a handful of categories | Bar | stacked by default (`stack` true); `split_by` for a second dimension, `max_split_values` default 10 |
| Ranking / top N with long labels | Row | `order_by` desc on the measure alias + `limit` N |
| Share of whole | Donut | `max_slices` default 10 (rest grouped); keep to 6 or fewer; always ordered desc by value |
| Stage drop-off | Funnel | one dimension + one measure; always ordered desc by value; `show_percentage` default true |
| Detail list, or measure x two dimensions | Table | `columns` empty = flat; non-empty = pivot |
| Value by country/state | Map | `map_type` world or india; unverified, see below |
| Two measures per entity (scatter) | Bubble | `xAxis`, `yAxis`, optional `size_column`; unverified |
| Flow between two dimensions | Sankey | `source_column`, `target_column`, `value_column`; unverified |

Donut/Funnel ordering and `max_slices`:; `FE/charts/helpers.ts:582,730`.

## Minimal config per type
Every example is a chart `config`. The frontend fills `filters`, `order_by`, `limit` (default 100) when absent (`FE/charts/chart.ts:555-575`); the compiler always writes them.

**Number** (verified live first chart = total, no date):
```json
{"number_columns":[{"measure_name":"Sales","column_name":"grand_total","data_type":"Decimal","aggregation":"sum"},{"measure_name":"Invoices","column_name":"name","data_type":"Integer","aggregation":"count"}]}
```
Number with latest period + delta (`tests/golden/ury_overview.json`):
```json
{"comparison":true,"date_column":{"column_name":"posting_date","data_type":"Date","dimension_name":"posting_date","granularity":"day"},"filters":{"filters":[],"logical_operator":"And"},"limit":100,"number_columns":[{"aggregation":"sum","column_name":"grand_total","data_type":"Decimal","format":"currency","measure_name":"Sales"},{"aggregation":"count","column_name":"name","data_type":"Integer","measure_name":"Invoices"}],"order_by":[{"column":{"column_name":"posting_date","type":"column"},"direction":"asc"}],"sparkline":true}
```
**Line**:
```json
{"x_axis":{"dimension":{"dimension_name":"posting_date","column_name":"posting_date","data_type":"Date","granularity":"day"}},"y_axis":{"series":[{"measure":{"measure_name":"Sales","column_name":"grand_total","data_type":"Decimal","aggregation":"sum"}}],"show_data_labels":false},"order_by":[{"column":{"type":"column","column_name":"posting_date"},"direction":"asc"}],"limit":100,"filters":{"logical_operator":"And","filters":[]}}
```
**Row** (top 10; order_by uses the measure ALIAS `Sales`, `tests/golden/ury_overview.json`):
```json
{"x_axis":{"dimension":{"column_name":"branch","data_type":"String","dimension_name":"branch"}},"y_axis":{"series":[{"measure":{"aggregation":"sum","column_name":"grand_total","data_type":"Decimal","measure_name":"Sales"}}],"show_data_labels":false},"order_by":[{"column":{"column_name":"Sales","type":"column"},"direction":"desc"}],"limit":10}
```
**Bar** with `split_by`:
```json
{"x_axis":{"dimension":{"dimension_name":"Month","column_name":"posting_date","data_type":"Date","granularity":"month"}},"split_by":{"dimension":{"dimension_name":"Flow","column_name":"flow","data_type":"String"},"max_split_values":5},"y_axis":{"series":[{"measure":{"measure_name":"Amount","column_name":"amount","data_type":"Decimal","aggregation":"sum"}}]},"order_by":[{"column":{"type":"column","column_name":"Month"},"direction":"asc"}]}
```
**Donut** ("tc-revenue-by-item-group"):
```json
{"label_column":{"dimension_name":"item_group","column_name":"item_group","data_type":"String"},"value_column":{"measure_name":"Revenue","column_name":"base_net_amount","data_type":"Decimal","aggregation":"sum"},"legend_position":"bottom","order_by":[],"limit":100,"filters":{"logical_operator":"And","filters":[]}}
```
**Funnel** ("tc-quotation-funnel"): same `label_column`/`value_column` slots plus `"show_percentage":true,"label_position":"left"`.
**Table / pivot** ("tc-overdue-deliveries"; add `columns:[Dimension]` to pivot):
```json
{"rows":[{"dimension_name":"Customer","column_name":"customer","data_type":"String"}],"columns":[],"values":[{"measure_name":"Order Value","column_name":"base_grand_total","data_type":"Decimal","aggregation":"sum","format":"currency","currency_column":"default_currency"}],"order_by":[{"column":{"type":"column","column_name":"Order Value"},"direction":"desc"}],"limit":20}
```
**Map / Bubble / Sankey: no fixture exists; slots below are derived from TS types only (unverified).**; `FE/types/chart.types.ts:142-170`
```json
{"Map":{"location_column":{"dimension_name":"country","column_name":"country","data_type":"String"},"value_column":{"measure_name":"Revenue","column_name":"base_net_total","data_type":"Decimal","aggregation":"sum"},"map_type":"world"}}
{"Bubble":{"xAxis":{"measure_name":"X","column_name":"a","data_type":"Decimal","aggregation":"sum"},"yAxis":{"measure_name":"Y","column_name":"b","data_type":"Decimal","aggregation":"sum"}}}
{"Sankey":{"source_column":{"dimension_name":"src","column_name":"src","data_type":"String"},"target_column":{"dimension_name":"dst","column_name":"dst","data_type":"String"},"value_column":{"measure_name":"V","column_name":"v","data_type":"Decimal","aggregation":"sum"}}}
```
Required slots (frontend `validateConfig`): Bar/Line/Row `x_axis.dimension.column_name` (x_axis and split_by must differ); Number >=1 `number_columns[].measure_name`; Donut/Funnel `label_column.column_name` + `value_column.measure_name`; Table >=1 `rows[].column_name`; Map `location_column` + `value_column.measure_name`; Bubble `xAxis`+`yAxis` `.measure_name`. `FE/charts/chart.ts:100-211`

## Common config keys
| Key | Shape | Notes |
|---|---|---|
| `filters` | `{"logical_operator":"And","filters":[]}` | the chart's own filter group; applied only when non-empty. Item grammar = query filters ([queries.md](queries.md)). `FE/charts/chart.ts:~218` |
| `order_by` | `[{"column":{"type":"column","column_name":<ALIAS>},"direction":"asc"\|"desc"}]` | alias = `measure_name`/`dimension_name`, NOT the source column. Applied after the Donut/Funnel built-in desc sort. |
| `limit` | int, default 100 | applies to the aggregated result, not the source rows |

Naming rule: a Dimension/Measure has `column_name` (column in the source query) and `dimension_name`/`measure_name` (output alias after summarize). `dimension_name` is auto-filled from `column_name` if missing; `measure_name` is NOT and is required for Number/Donut/Funnel/Map. `FE/charts/helpers.ts:1533-1566`

## Measures and dimensions
- Dimension: `{dimension_name, column_name, data_type: String|Date|Datetime|Time, granularity?}`. Granularity: `second minute hour day week month quarter year fiscal_year`. `FE/types/query.types.ts:32-37`; `FE/helpers/constants.ts:65-75`
- Column measure: `{measure_name, column_name, data_type: String|Integer|Decimal, aggregation: sum|count|avg|min|max|count_distinct, format?, currency_column?}`; expression measure: `{measure_name, expression, data_type, ...}`. `FE/types/query.types.ts:15-30`
- Compiler data types: count/count_distinct -> Integer; sum/avg -> Decimal; else the schema type. `scripts/finsights_lib/compile.py:63-82`

## Formats
| Where | Key | Effect |
|---|---|---|
| measure | `format:"currency"` | site currency symbol, or the `currency_column` value of the last row |
| measure | `format:"percent"` | value x 100 plus `%` |
| Number card | `prefix`, `suffix`, `decimal`, `shorten_numbers`, `color` in `number_column_options[i]` (index-aligned with `number_columns`) | `prefix` overrides the currency prefix; shorten = 1.2K/3M |
`FE/charts/components/NumberChart.vue:52-65,96-99`; `FE/helpers/index.ts:176-232`

## Colours and series overrides
No theme/palette key exists. Fixed palette, series i takes `colors[i]` (`FE/charts/colors.ts:1-28`; blue #318AD8 first) unless `y_axis.series[i].color[0]` is set. Per series: `name, color[], type: line|bar` (mixed chart inside Line), `align: Left|Right`, `show_data_labels`, `hide_from_chart`. Y axis: `min, max, axis_label, show_axis_label, show_data_labels, show_scrollbar, reference_lines`. Bar-only: `stack, overlap, normalize`. Line-only: `smooth, show_area, show_data_points`. Number: `sparkline_color`, option `color`. `FE/charts/helpers.ts:107-118,344-361,543`

## NOT configurable
Tooltips: one hardcoded builder per chart family (`FE/charts/helpers.ts:1422-1471`; others at 679,758,1073,1272,1357). No palette/theme key, no per-point colours, no custom number format beyond the keys above.

## finsights spec mapping
Spec chart keys -> config slots, from `scripts/finsights_lib/compile.py:151-240`.
| Spec key | Bar/Line/Row | Number | Donut/Funnel | Table | Map | Bubble | Sankey |
|---|---|---|---|---|---|---|---|
| `x` | `x_axis.dimension` | - | `label_column` | `rows` (if no `rows`) | `location_column` | `dimension` | `source_column` |
| `y[]` | `y_axis.series[].measure` | `number_columns` | `value_column` (first) | `values` | `value_column` (first) | `xAxis`,`yAxis`,`size_column` (1st-3rd) | `value_column` (first) |
| `split_by {column,max}` | `split_by.dimension`+`max_split_values` | - | - | - | - | - | `target_column` |
| `rows`/`columns` | - | - | - | `rows`/`columns` | - | - | - |
| `date {column,granularity}` | - | `date_column` | - | - | - | - | - |
| `comparison`, `sparkline` | - | same-named booleans | - | - | - | - | - |
| `stack` / `smooth` | `y_axis.stack` (Bar) / `y_axis.smooth` (Line) | - | - | - | - | - | - |
| `show_data_labels` | `y_axis.show_data_labels` | - | - | - | - | `show_data_labels` | - |
| `legend`, `max_slices` | - | - | Donut only | - | - | - | - |
| `map_type` | - | - | - | - | `map_type` | - | - |
Common: `order [{by:<alias>,dir}]` -> `order_by`; `filters [{column,op,value}]` -> `filters.filters[{column,operator,value}]`; `limit` -> `limit` (default 100). Measure spec `{column|expr, agg=sum, as, format, currency_column}` -> measure object (`as` becomes `measure_name`). Auto order: Bar/Line/Row with a granular x, and Number with `date`, get order_by asc on the date alias when `order` is omitted. Lint: L22 warns on Number comparison/sparkline without `date`; L23 errors on order.by that is not an alias; L10 required slots. `scripts/finsights_lib/compile.py:151-240`

## Unverified
- Map, Bubble, Sankey configs (no fixture, never rendered live); Sankey required keys.
- `conditional_formatting` grammar for Table; `limit` before/after pivot with `split_by`.
- Whether the develop-only keys in finding 3 are silently ignored or break rendering (assumed ignored).
- Finding 4 is a coordinator observation without a proof file.
