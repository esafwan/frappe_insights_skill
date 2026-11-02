# Dashboards and filters (Insights Dashboard v3, 3.15.1)

Doctype `Insights Dashboard v3`; child table `Insights Dashboard Chart v3` (`linked_charts`). URL: `/insights/dashboards/<dashboard live name>` (hash name, not the title) `design/wpd_interfaces.md fact 7`. Never write `linked_charts`: it is rebuilt from `items` on every validate `R4 §Item types, insights_dashboard_v3.py:46-50,129-133`.

## `items`
One JSON field holding a list. Three types only: `chart`, `filter`, `text`. No dashboard variables, headings or tabs `R4 §Summary`. Every item MUST carry `type` (server does `item["type"]`, KeyError otherwise) `R4 §Item types`. REST accepts a list or a JSON string; GET returns a string, so `json.loads` before diffing `wpd_interfaces.md fact 1`.

Chart item (`ury_sales_proof.json`, imported live):
```json
{"type":"chart","chart":"tc-kpis","layout":{"i":"item-kpis","x":0,"y":1,"w":20,"h":3}}
```
`chart` = live name of an `Insights Chart v3` (in an import file: the file-local name, remapped on import) `R4 §Import path`.

Text item (`R4 §Shapes`, stock template): `{"type":"text","text":"","layout":{"i":"jgp91auh","x":12,"y":0,"w":8,"h":1}}`. `text` is HTML, sanitized server-side on save, rendered with `v-html`; the editor refuses to save empty text `R4 §Item types`. finsights spec: `{text:"<html>", x,y,w,h}` compiles to this with `i` = `text-<n>` `compile.py:299-301`.

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
| 20 columns, row height 52px, margin 0 | `R4 §Item types`, `DashboardBuilder.vue:191` |
| `layout` = `{i,x,y,w,h}`; `i` unique string per item | `moved` optional, omit `R4 §Item types` |
| Keep `x>=0, w>=1, x+w<=20` | FE corrects `x+w>20` to `x=20-w` at render; server does not reject. finsights lint L11 enforces it `R4, finsights_spec.md L11` |
| Overlap is legal in JSON | `preventCollision=false`; the grid pushes colliders down at render, so the saved layout is not what is shown. Do not overlap: finsights lint L12 `R4 §Item types` |
| UI defaults: chart w10 h8 (Number w20 h3), text w10 h1, filter w4 h1 | `dashboard.ts:65-121` |
| Filters in the top row(s), h=1, w=4, left to right, wrapping at x+w>20; other items start below | `dashboard.ts:173-210`; compile does the same unless `layout` is given `compile.py:261-283` |
| Auto-placement is FE-only | API-written items keep exactly the x/y you send; you must compute y yourself |
| Load order = `y*20+x` (top-left first) | `dashboard.ts:227-233` |
| A chart may appear once | UI refuses a duplicate; server does not, but `links` is keyed by chart name so duplicates would share filters `R4 §Item types` |
| `vertical_compact_layout` doctype field | not read by the 3.15.1 FE (toggle lives in localStorage) `R4 §Item types` |

## Filter types and operators
`filter_type` is exactly `Date | String | Number` `R4 §Filter types`. Linked column type must match: String = Text/String/JSON/Array; Number = Integer/Decimal; Date = Date/Datetime/Time `constants.ts:3-5`. Changing type in the editor clears default and links.

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

(`filter_utils.ts:10-46`, `R4 §Filter types`.) Literal shapes for Number and Date-`between` defaults are code-derived, no shipped fixture (`R4 §Shapes`): `"default_operator":"between","default_value":[100,500]` and `["2025-01-01","2025-12-31"]`.

`within` grammar: `<Span> <N> <Unit>[ (include current)]`; Span `Last|Next|Current`; Unit `Day|Week|Month|Quarter|Year|Fiscal Year`; `Current <Unit>` takes no N; `(include current)` not allowed with `Current` or `Fiscal Year`. Backend lowercases and singularises, so `Last 12 months` = `Last 12 Months`; anything not ending in a unit raises `Invalid timespan unit` `R4 §Filter types, sql_functions.py:230-251`. Used live: `Last 12 months`, `Last 30 days` (compiled golden `ury_overview.json`).

## Defaults
`default_operator` and `default_value` seed the filter on load ONLY if BOTH are truthy; `is_set` defaults and operator-without-value are ignored. Author default overrides a viewer's remembered value at every load. No default and no selection = filter not applied `R4 §Default values`.

## `links`
Format: `{"<chart name>": "`<query name>`.`<column name>`"}`: backticks required, names not titles. Regex `^`([^`]+)`\.`([^`]+)`$` `R4 §Link, dashboard.ts:309-320`.
- Target query = the chart's own query or any query it is built on (`source`/join/union tree) `R4 §Link 2`. A link to a query outside that tree has no effect.
- Several filters on one chart AND together.
- Wrong at write time = no error: the server does not validate links on REST write `R4 §Unknowns 3`. On `import_workbook` a link is silently DROPPED when its chart or query is not in the import file, the value is empty, or it lacks "`.`" `R4 §Import path, insights_workbook.py:144-174`. After import, GET the dashboard and check each `links` entry survived.
- Import rewrites names to new hash ids in both keys and values; the proof import did this `SWARM_STATE.md 2026-11-02 LIVE PROOF #1`.

## Runtime propagation
1. FE builds `adhoc_filters = {"<query name>": {"type":"filter_group","logical_operator":"And","filters":[{"column":{"type":"column","column_name":"<col>"},"operator":..,"value":..}]}}` and sends it to `Insights Query v3.execute` `R4 §Link 4-5`.
2. Python appends that group as the LAST operation of the named query `ibis_utils.py:153-177` (live-confirmed region `ibis_utils.py:155-165`, `SWARM_STATE.md` gate1).
3. Therefore the column must exist in that query's FINAL output. A trailing `select` or `summarize` that drops it makes the filter fail or do nothing. This is why finsights keeps queries row-level (`posting_date`, `branch` retained) and aggregates in the chart `finsights_spec.md L13, ury_sales_proof.json`.
4. The adhoc digest is part of the cache key, so filtered runs are cached separately `ibis_utils.py:1001-1002`.
5. String dropdown values come from `get_distinct_column_values`, served only for columns present in some filter `links` `insights_dashboard_v3.py:136-171`. Options come from the data: the demo bench has one branch, so the Branch dropdown shows only `URY Branch` `SWARM_STATE.md LIVE PROOF #1 (d)`.

## Live proof (bench sa-v16-latest-demo)
- Imported `proof/ury_sales_proof.json`; dashboard rendered from API-created charts with no UI editing; Sales 3,748,846.48 over 3,165 invoices equals SQL `proof/01_dashboard.jpg`, `proof/sql_truth.txt`.
- Date filter `within` / `Last 1 months` (UI "last 1 months") returned rows with `posting_date >= first of that month` (2026-10-01): 440 invoices / 618,750.00, equal to SQL `proof/02_filter_changed.jpg`, `proof/sql_truth.txt`. So `Last N months` is calendar-aligned to the month start, not a rolling 30-day window.
- The finsights-compiled dashboard also renders `proof/03_compiled_dashboard.jpg`.

## finsights spec -> dashboard JSON
Spec shape `finsights_spec.md` (dashboards[]); mapping `compile.py:255-302`:
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
- Develop-branch keys (`layouts.sm`, `default_user_key`, `visible_to_roles`, String `default_operator "="`, `vertical_compact_layout`) are NOT present in 3.15.1 code or FE; do not use them. Import behaviour of unknown keys untested `R4 §Develop, §Unknowns 4`.
- Exact adhoc `column` object key set (`type`,`column_name`) is inferred, not read from `query/helpers` `R4 §Unknowns 1`.
- Number filters and non-`within` Date defaults: no fixture, not run live.
- Effect of a malformed/nonexistent link or absent column on REST-written dashboards (runtime error vs no-op) not probed live.
- No hard limit found on item count, `y`, or `h`.
