# finsights spec format, lint rules, commands

Self-contained reference for the `finsights` CLI (`scripts/finsights`, code in `scripts/finsights_lib/`). A spec is one `.yaml`/`.yml`/`.json` file. `finsights` compiles it to Insights v3 workbook JSON and upserts it by title. Unknown keys are errors (L01). Details per area: `charts.md`, `dashboards-filters.md`, `queries.md`, `desk-integration.md`, `api-shipping.md`. Working specs: `examples/*.yaml`, `tests/fixtures/ury_overview.yaml`.

## 1. Quickstart (lints clean against `tests/fixtures/catalog.json`)
```yaml
workbook: {id: ury_sales, title: "URY Sales", module: "URY", required_apps: [ury]}
queries:
  - id: pos_invoices
    title: "POS Invoices"
    table: "POS Invoice"
    filters: [{column: docstatus, op: "=", value: 1}]
    select: [posting_date, grand_total, name, {column: branch, optional: true}]
charts:
  - {id: daily_sales, title: "Daily Sales", query: pos_invoices, type: Line, limit: 366,
     x: {column: posting_date, granularity: day}, y: [{column: grand_total, agg: sum, as: Sales}]}
dashboards:
  - id: overview
    title: "URY Daily Sales"
    filters:
      - {id: f_date, label: Date, type: date, default: {op: within, value: "Last 30 days"}, applies_to: {pos_invoices: posting_date}}
    items: [{chart: daily_sales, x: 0, y: 1, w: 20, h: 9}]
desk: {workspace: "URY", label: "Sales Analytics"}
```
Ids match `^[a-z][a-z0-9_]{1,39}$` and are unique across workbook, queries, charts, dashboards and dashboard filters (L03). Titles are the live upsert key and must be unique within queries, within charts and within dashboards (L04).

## 2. Key grammar
`R` = required, `-` = optional. Anything not listed is an L01 error. A `column` may be a bare string where noted ("col or map").

**Top level**: `workbook` R, `queries` R, `catalog` - (string path to catalog JSON, relative to the spec file), `charts`, `dashboards`, `desk`, `share` -.

**workbook**: `id` R (compiled name is kebab-case of the id, so `ury_sales` -> `ury-sales`), `title` R, `module` - (needed by `ship --standard`; default `Insights`), `description` -, `required_apps` - (list), `version` - (int, default 1). `description`, `required_apps`, `version` are used only by `ship` (manifest.json).
**share**: `team` R. Parsed and linted for shape only; nothing in compile/apply/ship emits it (no-op today).

**queries[]** (exactly one of `table | source_query | sql`):
| key | meaning / compiled operation |
|---|---|
| id R, title R | title becomes the live `Insights Query v3` title |
| `table` | DocType name, e.g. "POS Invoice" -> `source` op on the Site DB table `tab<DocType>` |
| `source_query` | id of another query; builds on its output (`source` op of type query). No cycles (L17) |
| `sql` | one `SELECT`/`WITH` statement, no `;` (L16). Native query; output schema unknown, column checks skipped |
| `joins[]` | `{table R, on R: [leftcol, rightcol], type: inner*\|left\|right\|full, columns: [names]}` -> `join` op |
| `filters[]` | `{column, op, value}` or `{expr}`; ops: `= != > >= < <= in not_in between within contains not_contains starts_with ends_with is_set is_not_set`. Compiled to one `filter_group`; And by default, **Or if any filter has `any: true`** (whole group becomes Or) |
| `mutate[]` | `{as R, expr R, type: String*}` -> `mutate` op (new column; `type` is an Insights data type) |
| `select[]` | column name, or `{column R, optional: true}`. Selection after joins/mutate. `optional` columns missing from the catalog are pruned (see L21): dependent chart refs are dropped, charts using it are skipped, filters link-dropped |
| `summarize` | `{measures R: [...], by: [{column R, granularity, as}]}`; measure `{agg R (sum count avg min max count_distinct), column, expr, as, type, format (currency\|percent), currency_column}`; `column` or `expr` required unless `agg: count`; `by.granularity`: second..fiscal_year |
| `order_by[]` | `{column R, dir: asc*\|desc}` |
| `limit` | int -> `limit` op |
| `live` | bool, default true -> `use_live_connection` 1/0 |

Compiled ops order is fixed: source, joins, filter_group, mutate, select, summarize, order_by, limit. Keep dashboard-filtered queries row-level (summarize drops filter columns: L13).

**charts[]** (R: `id, title, query, type`; see `charts.md` for slot semantics):
`type` enum (case-insensitive; aliases `pie`->Donut, `kpi`->Number, `pivot`->Table): Number, Bar, Line, Row, Donut, Funnel, Table, Map, Bubble, Sankey. Map/Bubble/Sankey configs are derived, unverified against a live site.
| key | use |
|---|---|
| `x` | col or `{column R, granularity, as}`; Bar/Line/Row axis, Donut/Funnel label, Map location, Bubble x-measure, Sankey source |
| `y[]` | col or `{column\|expr, agg: sum*, as, format, currency_column}`; measures. Donut/Funnel/Map/Sankey use y[0] only; Number takes all as KPIs |
| `split_by` | `{column R, max}` series split (Bar/Line/Row), Sankey target. Must differ from `x` (L10) |
| `rows`, `columns` | Table dimension lists (col or map); Table needs >=1 `rows` (falls back to `x` at compile time, but L10 wants `rows`) |
| `date` | `{column R, granularity}` Number-chart time column; needed for `comparison`/`sparkline` (L22) |
| `comparison`, `sparkline` | Number only, bool |
| `stack` (Bar), `smooth` (Line), `show_data_labels` (default false), `legend` + `max_slices` (Donut), `map_type` (Map) | presentation flags |
| `order[]` | `{by R, dir}`; `by` must be an output alias (L23). Default: ascending on the date/x dimension when it has a granularity |
| `limit` | int, default **100** -> `config.limit` |
| `filters[]` | `{column R, op R, value}` chart-level -> `config.filters` (And) |
Compiled: `Insights Chart v3` {query, chart_type, config{x_axis/y_axis | number_columns | label_column/value_column | rows/columns/values, filters, order_by, limit}}.

**dashboards[]** (R: `id, title, items`; see `dashboards-filters.md`). Grid is 20 columns wide.
| key | use |
|---|---|
| `items[]` | each `{x, y, w, h}` (ints, R) plus exactly one of `chart: <chart id>` or `text: "<markdown/text>"`. A chart may appear once per dashboard (L15). `x+w <= 20`, `w,h >= 1` (L11), no overlap (L12) |
| `filters[]` | `id R, label R, type R (date\|string\|number), applies_to R: {query_id: column}`, `default: {op, value}`, `icon`, `layout: {x,y,w,h}` |
Filter layout default: auto-flowed 4x1 cells from (0,0) in declaration order; give charts `y >= 1` when filters use the default row. `default` operators by type: string `in not_in contains not_contains starts_with ends_with is_set is_not_set`; number `= != > >= < <= between ...`; date `between = != > >= < <= within ...`. `within` value: `Last N <Unit>` / `Next N <Unit>` / `Current <Unit>` (Day Week Month Quarter Year Fiscal Year). `applies_to` column types: date <- Date/Datetime/Time; number <- Integer/Decimal; string <- String/Text/JSON/Array. A filter links to every chart on the dashboard whose query is (or is built on) the named query. Compiled: `Insights Dashboard v3` items of type `filter`/`chart`/`text`, layout ids `filter-<id>`, `chart-<id>`, `text-<n>`.

**desk** (R: `workspace, label`; optional `sidebar`, `dock`). Never writes to the site; `finsights desk` only prints `fdesk` commands (see `desk-integration.md`):
- always: `fdesk --app <app> --write workspace add-shortcut <workspace> --type URL --url /insights/dashboards/<name> --label <label>`
- `sidebar: {section R}` adds `sidebar add-item <workspace> --section <section> --link-type URL ...`
- `dock: true` adds `dock add-tile --link-type URL --url ... --title <label> --icon chart-line`
- last line: `fdesk --app <app> lint --only L07,L10,L13`
With `--tier island` it prints a standard desk `Dashboard` doc JSON (`insights_dashboard: <live name>`, `is_standard: 1`, module from `workbook.module`) instead.

## 3. Catalog JSON
Shape: `{ "<DocType>": {"<column>": "<Insights data_type>"} }`, e.g. `{"POS Invoice": {"grand_total": "Decimal", "posting_date": "Date"}}`. Data types: String Integer Decimal Date Datetime Time Text JSON Array Auto. Sample: `tests/fixtures/catalog.json`. Files are sorted and deterministic.
Create (needs network + auth):
`finsights catalog --spec spec.yaml -o finsights_catalog.json` (default tables = every `table` and join `table` the spec reads) or `finsights catalog --tables "POS Invoice,POS Invoice Item" -o finsights_catalog.json` (`--data-source` default "Site DB"). An unknown/inaccessible DocType exits 2.
Discovery order for lint/compile/plan/apply/verify/ship: `--catalog FILE`, else spec `catalog:` path (missing file is exit 2), else `./finsights_catalog.json` in the current directory, else none. `lint --catalog live` snapshots the site on the fly (lint only).

## 4. Lint rules (`finsights lint`)
Severity: E error, W warning (fails only with `--strict`), I info. "(cat)" = needs a catalog; without one L05/L06/L08 each emit one W "no catalog; column checks skipped" and the checks do not run, so a no-catalog pass is NOT proof.
| id | sev | triggers | fix |
|---|---|---|---|
| L01 | E | unknown key, missing required key, wrong container type, not exactly one of table\|source_query\|sql / chart\|text, measure without column\|expr | use allowed keys (message lists them) |
| L02 | E | v2 trap string anywhere: `Insights Query/Chart/Dashboard` (not "v3"), `insights.api.queries/public/home`, `dashboards.create_dashboard` | use v3 doctypes / `insights.api.workbooks` |
| L03 | E | id fails regex or duplicated | lowercase letters, digits, underscore; unique |
| L04 | E | duplicate title within queries/charts/dashboards | titles are the upsert key; rename |
| L05 | E (cat) / W | table not in catalog; `source_query` unresolved (E always) | fix DocType name / query id |
| L06 | E (cat) / W | query column (select, filter, join, summarize, order_by) not in catalog or upstream output | pick an existing column; mark `optional` if it may be absent |
| L07 | E | chart `query` does not resolve | use an existing query id |
| L08 | E (cat) / W | chart column not in the query's final output | add it to select/summarize or use an output column |
| L09 | E | chart type outside the 10-type enum | use the enum |
| L10 | E | missing slot per type: Bar/Line/Row need `x`; Number needs `y`; Donut/Funnel/Map need `x`+`y`; Table needs `rows`; Bubble needs `x`+`y`; x equal to split_by | add the slot |
| L11 | E | dashboard item/filter layout not ints or out of 20-col grid | ints, x>=0,y>=0,w>=1,h>=1,x+w<=20 |
| L12 | E | layout rectangles overlap (items and filters) | move one |
| L13 | E | filter `applies_to`: query unresolved, no chart on that dashboard uses it, column absent from FINAL output (cat), or type mismatch (cat) | keep the query row-level, add a chart on it, fix the type |
| L14 | E | filter `default` op invalid for type, or bad `within` value | see allowed ops / within grammar in section 2 |
| L15 | E | dashboard chart item undefined, or chart placed twice | define under `charts`, place once |
| L16 | E | `sql` empty, contains `;`, or not SELECT/WITH | one read-only statement |
| L17 | E | `source_query` cycle | break the cycle |
| L18 | E | value outside enum: join type, filter op, granularity, agg, order dir, filter type | use the enum |
| L19 | W | develop-only key `number_format window layouts default_user_key visible_to_roles` (ignored on 3.15.1) | remove |
| L20 | W (cat) | sum/avg over a non-numeric column | use count/min/max or a numeric column |
| L21 | I (cat) | `optional` select column absent: pruned N refs (charts using it skipped, filters dropped) | add column to catalog if it should exist |
| L22 | W | Number chart has `comparison`/`sparkline` without `date` | add `date: {column, granularity}` |
| L23 | E | chart `order.by` not an output alias (measure `as`, dimension, rows/columns) | use an alias |
| L24 | W | query used by no chart and no `source_query` | add a chart or delete the query |
| L25 | E | dashboard `text` item empty | add content or remove |
Findings are JSON-pointer addressed (`/charts/0/y/1`); `--json` gives `{findings:[{rule,severity,path,message,fix_hint}], ok}`; `--only L06,L08` filters. Compile also emits L21 info lines on stderr. `compile`, `plan`, `apply`, `verify`, `ship` run lint first (with the discovered catalog) and stop with exit 1 on errors (warnings do not stop them).

## 5. Commands
Global flags work before or after the subcommand: `--spec FILE`, `--json`, `--write` (only meaningful for apply/ship; default is dry-run). All writes are dry-run unless `--write`.
| cmd | extra flags | effect | network |
|---|---|---|---|
| `lint` | `--strict`, `--only IDS`, `--catalog FILE\|live` | read-only | only for `--catalog live` |
| `compile` | `--format template*\|standard`, `-o FILE`, `--check FILE`, `--catalog` | prints workbook JSON; `--check` exits 1 if FILE differs (CI drift guard); writes only `-o` | no |
| `catalog` | `--tables`, `--data-source`, `-o` | snapshot columns | yes |
| `plan` | `--catalog`, `--adopt`, `--rename id="Old Title"` (repeatable), `--exit-code` | diff vs live: `+ create`, `~ update <fields>`, `- orphan`, or `no changes`; read-only | yes |
| `apply` | as plan + `--prune`; `--materialize-data-query` is ignored with a note | dry-run unless `--write`; creates/updates over REST | yes |
| `verify` | `--catalog`, `--min-rows N` (default 1) | read-only; V01 missing/drifted object, V02 query rows < N, V03 query run failed, V04 dashboard linked_charts != chart items; prints dashboard URLs | yes |
| `desk` | `--tier url*\|island`, `--dashboard NAME`, `--app PATH` | prints commands only | optional: resolves the live dashboard name via plan only when `INSIGHTS_URL` is set, `--tier url` and no `--dashboard`; otherwise prints `<dashboard name>` placeholders. Never needs it if `--dashboard` is given |
| `ship` | `--app PATH` (default .), `--standard`, `--forward`, `--catalog` | writes `workbook_templates/<id>/{manifest,workbook}.json` and the `insights_workbooks` hook line in the app's hooks.py (dry-run unless `--write`); `--standard` (needs `--forward`, needs `workbook.module`) writes the develop-branch standard layout, not loadable on 3.15.1 | no |
| `fmt` | `--to json` (R), `-o` | convert spec to JSON | no |
Env (network commands only: catalog, plan, apply, verify, `lint --catalog live`, `desk` as above): `INSIGHTS_URL` (e.g. https://site, required) plus either `INSIGHTS_API_KEY`+`INSIGHTS_API_SECRET` or `INSIGHTS_USER`+`INSIGHTS_PASSWORD`. Secrets are env-only and never printed. Missing env is exit 2; rejected login or HTTP/connection failure is exit 4.
**Exit codes**: 0 ok; 1 lint errors (or warnings with `--strict`), verify findings, or `compile --check` diff; 2 usage, unreadable/unparseable spec, bad catalog, missing env; 3 live conflict (ambiguous title/marker match, or workbook owned by another marker without `--adopt`); 4 auth/network failure; 5 `plan --exit-code` with pending changes.

## 6. Identity and idempotency (plan/apply)
- Workbook identity: the `from_template` field holds the marker `finsights:<workbook compiled name>` (e.g. `finsights:ury-sales`). Lookup order: marker match, then title. Two marker hits = exit 3. A title match owned by a different marker/template is exit 3 unless `--adopt`, which re-stamps the marker.
- Queries/charts/dashboards inside the workbook match by **title** (the spec title is the key). Changing a title therefore means create + orphan; use `--rename query_id="Old live title"` to update in place. Ambiguous duplicate live titles = exit 3.
- Written fields only: workbook `title, from_template`; query `title, folder, sort_order, use_live_connection, is_*_query, operations`; chart `title, folder, sort_order, query, chart_type, config`; dashboard `title, items`. It **never writes** `data_query` or `linked_charts`: Insights computes those server-side, and chart helper queries (`data_query`) and empty helper queries are ignored by matching.
- Cross-references in operations/config are rewritten from spec names to live names before diffing, so a second run gives `no changes`.
- Objects on the site but not in the spec are `orphan`; they are kept unless `apply --prune --write` deletes them.
- Re-running `apply --write` after `no changes` writes nothing.

## 7. Recommended order
1. `finsights catalog --spec s.yaml -o finsights_catalog.json`
2. `finsights lint --spec s.yaml --strict --catalog finsights_catalog.json` (fix until `ok`)
3. `finsights compile --spec s.yaml -o workbook.json` (inspect or `--check` in CI)
4. `finsights plan --spec s.yaml` (read what will change; `--rename` for retitles)
5. `finsights apply --spec s.yaml --write`, then run `plan` again: expect `no changes`
6. `finsights verify --spec s.yaml` (prints `/insights/dashboards/<live name>`)
7. `finsights desk --spec s.yaml --dashboard <live name>`; without a live dashboard (or without `--dashboard`/`INSIGHTS_URL`) it prints `<dashboard name>` placeholders, so run it after step 5 and execute the printed `fdesk` lines yourself.
For app delivery instead of a live site use `ship` (see `api-shipping.md`).
