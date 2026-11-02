# Dashboards and filters (Insights Dashboard v3, 3.15.1)

Doctype `Insights Dashboard v3`; child table `Insights Dashboard Chart v3` (`linked_charts`). URL: `/insights/dashboards/<dashboard live name>` (hash name, not the title). Never write `linked_charts`: it is rebuilt from `items` on every validate.

## `items`
One JSON field holding a list. Three types only: `chart`, `filter`, `text`. No dashboard variables, headings or tabs. Every item MUST carry `type` (server does `item["type"]`, KeyError otherwise). REST accepts a list or a JSON string; GET returns a string, so `json.loads` before diffing.

Chart item (`ury_sales_proof.json`, imported live):
```json
{"type":"chart","chart":"tc-kpis","layout":{"i":"item-kpis","x":0,"y":1,"w":20,"h":3}}
```
`chart` = live name of an `Insights Chart v3` (in an import file: the file-local name, remapped on import).

Text item: `{"type":"text","text":"","layout":{"i":"jgp91auh","x":12,"y":0,"w":8,"h":1}}`. `text` is HTML, sanitized server-side on save, rendered with `v-html`; the editor refuses to save empty text. finsights spec: `{text:"<html>", x,y,w,h}` compiles to this with `i` = `text-<n>` `compile.py:299-301`.

Filter item with Date default and links (`ury_sales_proof.json`):
```json
{"type":"filter","filter_name":"Date Range","filter_type":"Date","icon":"calendar",
 "default_operator":"within","default_value":"Last 12 months",
 "links":{"tc-kpis":"`tq-pos-invoices`.`posting_date`","tc-trend":"`tq-pos-invoices`.`posting_date`"},
 "layout":{"i":"item-f-date","x":0,"y":0,"w":10,"h":1}}
```
String filter, no default (same file): `{"type":"filter","filter_name":"Branch","filter_type":"String","icon":"building-2","links":{"tc-kpis":"`tq-pos-invoices`.`branch`"},"layout":{"i":"item-f-branch","x":10,"y":0,"w":10,"h":1}}`.

## Grid and layout
| Rule | Detail |
|---|---|
| 20 columns, row height 52px, margin 0 |, `DashboardBuilder.vue:191` |
| `layout` = `{i,x,y,w,h}`; `i` unique string per item | `moved` optional, omit |
| Keep `x>=0, w>=1, x+w<=20` | FE corrects `x+w>20` to `x=20-w` at render; server does not reject. finsights lint L11 enforces it |
| Overlap is legal in JSON | `preventCollision=false`; the grid pushes colliders down at render, so the saved layout is not what is shown. Do not overlap: finsights lint L12 |
| UI defaults: chart w10 h8 (Number w20 h3), text w10 h1, filter w4 h1 | `dashboard.ts:65-121` |
| Filters in the top row(s), h=1, w=4, left to right, wrapping at x+w>20; other items start below | `dashboard.ts:173-210`; compile does the same unless `layout` is given `compile.py:261-283` |
| Auto-placement is FE-only | API-written items keep exactly the x/y you send; you must compute y yourself |
| Load order = `y*20+x` (top-left first) | `dashboard.ts:227-233` |
| A chart may appear once | UI refuses a duplicate; server does not, but `links` is keyed by chart name so duplicates would share filters |
| `vertical_compact_layout` doctype field | not read by the 3.15.1 FE (toggle lives in localStorage) |

## Filter types and operators
`filter_type` is exactly `Date | String | Number`. Linked column type must match: String = Text/String/JSON/Array; Number = Integer/Decimal; Date = Date/Datetime/Time `constants.ts:3-5`. Changing type in the editor clears default and links.

| Type | Operators | Value |
|---|---|---|
| String | `in`, `not_in` | array of strings |
| | `contains`, `not_contains`, `starts_with`, `ends_with` | string |
| | `is_set`, `is_not_set` | none |
| Number | `=`,`!=`,`>`,`>=`,`<`,`<=` | number |
| | `between` | `[min,max]` |
| Date | `between` | `["YYYY-MM-DD","YYYY-MM-DD"]` |
| | `=`,`!=`,`>`,`>=`,`<`,`<=` | one date string |
| | `within` | relative-date string |

(`filter_utils.ts:10-46`.) Literal shapes for Number and Date-`between` defaults are code-derived, no shipped fixture: `"default_operator":"between","default_value":[100,500]` and `["2025-01-01","2025-12-31"]`.

`within` grammar: `<Span> <N> <Unit>[ (include current)]`; Span `Last|Next|Current`; Unit `Day|Week|Month|Quarter|Year|Fiscal Year`; `Current <Unit>` takes no N; `(include current)` not allowed with `Current` or `Fiscal Year`. Backend lowercases and singularises, so `Last 12 months` = `Last 12 Months`; anything not ending in a unit raises `Invalid timespan unit`. Used live: `Last 12 months`, `Last 30 days` (compiled golden `ury_overview.json`).

## Defaults
`default_operator` and `default_value` seed the filter on load ONLY if BOTH are truthy; `is_set` defaults and operator-without-value are ignored. Author default overrides a viewer's remembered value at every load. No default and no selection = filter not applied.

## `links`
Format: `{"<chart name>": "`<query name>`.`<column name>`"}`: backticks required, names not titles. Regex `^`([^`]+)`\.`([^`]+)`$`.
- Target query = the chart's own query or any query it is built on (`source`/join/union tree). A link to a query outside that tree has no effect.
- Several filters on one chart AND together.
- Wrong at write time = no error: the server does not validate links on REST write. On `import_workbook` a link is silently DROPPED when its chart or query is not in the import file, the value is empty, or it lacks "`.`". After import, GET the dashboard and check each `links` entry survived.
- Import rewrites names to new hash ids in both keys and values; the proof import did this.

## Runtime propagation
1. FE builds `adhoc_filters = {"<query name>": {"type":"filter_group","logical_operator":"And","filters":[{"column":{"type":"column","column_name":"<col>"},"operator":..,"value":..}]}}` and sends it to `Insights Query v3.execute`.
2. Python appends that group as the LAST operation of the named query `ibis_utils.py:153-177` (live-confirmed region `ibis_utils.py:155-165` gate1).
3. Therefore the column must exist in that query's FINAL output. A trailing `select` or `summarize` that drops it makes the filter fail or do nothing. This is why finsights keeps queries row-level (`posting_date`, `branch` retained) and aggregates in the chart.
4. The adhoc digest is part of the cache key, so filtered runs are cached separately `ibis_utils.py:1001-1002`.
5. String dropdown values come from `get_distinct_column_values`, served only for columns present in some filter `links` `insights_dashboard_v3.py:136-171`. Options come from the data: the demo bench has one branch, so the Branch dropdown shows only `URY Branch`.

## Live proof
- Imported; dashboard rendered from API-created charts with no UI editing; Sales 3,748,846.48 over 3,165 invoices equals SQL.
- Date filter `within` / `Last 1 months` (UI "last 1 months") returned rows with `posting_date >= first of that month` (2026-10-01): 440 invoices / 618,750.00, equal to SQL. So `Last N months` is calendar-aligned to the month start, not a rolling 30-day window (verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02). Alignment of `Last N days` / `Last N weeks` was NOT verified: check it against SQL before promising an exact window.
- **A dashboard Date filter also applies to a Number chart that has NO `date_column`.** Filters bind through `links` to the chart's query column, not to the chart's own date setting. Verified live: a Number KPI without `date_column` showed 440 invoices / 618,750.00 with the filter set to 'within Last 1 months', equal to SQL for `posting_date >= first of that month` (Frappe 16.54.0 + Insights 3.15.1, 2026-11-02). So no `date_column` on the chart is needed for the filter to take effect; `date_column` only adds latest-period + comparison semantics.
- The finsights-compiled dashboard also renders.

## finsights spec -> dashboard JSON
Spec shape (dashboards[]); mapping `compile.py:255-302`:
| Spec key | Output |
|---|---|
| `dashboards[].id/title` | doc `name` = `<wb-slug>-<id-kebab>`, `title`, `workbook` |
| `filters[].label` | `filter_name` (unique per dashboard) |
| `filters[].type` date/string/number | `filter_type` Date/String/Number |
| `filters[].icon` | `icon` (omitted if absent) |
| `filters[].default {op,value}` | `default_operator`, `default_value`; only when value is not null |
| `filters[].applies_to {<query id>: <col>}` | `links`: for EVERY chart on this dashboard whose query is that query or depends on it, `"`<query name>`.`<col>`"` (own query wins over a dependent's) |
| `filters[].layout` absent | auto: row 0, w4 h1, advance x by 4, wrap at 20; `i` = `filter-<id-kebab>` |
| `items[] {chart,x,y,w,h}` | `{"type":"chart","chart":<chart name>,"layout":{i:"chart-<id-kebab>",x,y,w,h}}` |
| `items[] {text,...}` | `{"type":"text","text":..,"layout":{i:"text-<n>",...}}` |
| optional column pruned | filter dropped when all its links are pruned (finding L21 info) |
Filters are emitted before charts. Lint L11-L15, L13, L14 check bounds, overlap, link validity, default op/grammar, chart placed twice.

## Unverified
- Develop-branch keys (`layouts.sm`, `default_user_key`, `visible_to_roles`, String `default_operator "="`, `vertical_compact_layout`) are NOT present in 3.15.1 code or FE; do not use them. Import behaviour of unknown keys untested.
- Exact adhoc `column` object key set (`type`,`column_name`) is inferred, not read from `query/helpers`.
- Number filters and non-`within` Date defaults: no fixture, not run live.
- Effect of a malformed/nonexistent link or absent column on REST-written dashboards (runtime error vs no-op) not probed live.
- No hard limit found on item count, `y`, or `h`.
