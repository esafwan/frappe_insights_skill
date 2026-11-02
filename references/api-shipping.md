# API surface and shipping (Insights 3.15.1, Frappe v16)

Source paths written `insights/...` are relative to the Insights app repo (`apps/insights`). Live findings (verified on Frappe 16.54.0 + Insights 3.15.1, 2026-11-02) win over source reading when they conflict. Spec grammar: `spec-format.md`.

## 1. REST surface that works (proven live)
Use REST, not `import_workbook`, to write; use `run_doc_method` to execute. Env: `INSIGHTS_URL`, `INSIGHTS_USER`/`INSIGHTS_PASSWORD` (or `INSIGHTS_API_KEY`/`INSIGHTS_API_SECRET`). Never echo them.

```bash
J=$(mktemp)                                   # cookie jar
curl -sc $J -X POST "$INSIGHTS_URL/api/method/login" -d "usr=$INSIGHTS_USER" -d "pwd=$INSIGHTS_PASSWORD"
# token auth instead: -H "Authorization: token $INSIGHTS_API_KEY:$INSIGHTS_API_SECRET"
curl -sb $J "$INSIGHTS_URL/api/resource/Insights%20Query%20v3?filters=%5B%5B%22workbook%22,%22=%22,%221%22%5D%5D&fields=%5B%22name%22,%22title%22%5D"
curl -sb $J -X POST "$INSIGHTS_URL/api/resource/Insights%20Workbook" -H 'Content-Type: application/json' -d '{"title":"URY Sales"}'
curl -sb $J -X PUT  "$INSIGHTS_URL/api/resource/Insights%20Workbook/1" -H 'Content-Type: application/json' -d '{"from_template":"finsights:ury-sales"}'
```

| # | Fact | Cite |
|---|---|---|
| 1 | POST `/api/resource/<Doctype>` works for Insights Workbook, Query v3, Chart v3, Dashboard v3. Workbook name is a counter ("1","2"); children get server hash names. `operations`, `config`, `items` accept a list or a JSON string; GET returns them as JSON STRINGS, so `json.loads` before diffing | |
| 2 | Creating a chart auto-creates an EMPTY `data_query`. Never write `data_query` or `linked_charts`; the server maintains them (`linked_charts` is filled from dashboard `items`). Charts without data_query operations render on a dashboard; the browser builds them | |
| 3 | Dashboard URL: `/insights/dashboards/<dashboard docname>` | |
| 4 | Frappe v16 `get_list` rejects SQL functions in `fields` strings; use plain fields or filters | |
| 5 | Chart `query` = query live name; dashboard item `chart` = chart live name; filter `links` = `{<chart live name>: "`<query live name>`.`<column>`"}` (backticks) | |

`run_doc_method` needs the FULL doc as returned by GET, not `{doctype,name}` (that fails with TypeError, operations None).
```bash
DOC=$(curl -sb $J "$INSIGHTS_URL/api/resource/Insights%20Query%20v3/<name>" | jq -c .data)
curl -sb $J -X POST "$INSIGHTS_URL/api/method/insights.api.run_doc_method" -H 'Content-Type: application/json' \
  -d "{\"method\":\"execute\",\"docs\":$DOC,\"args\":{\"force\":1,\"page\":1,\"page_size\":10}}" | jq '.message.rows'
```
Response: `message.rows` (list of dicts) + `message.columns`. To check a chart, execute the chart's SOURCE query (`chart.query`), not its `data_query`.

## 2. Whitelisted v3-safe functions (call as `/api/method/insights.api.<module>.<fn>`)
Every row needs role Insights User unless noted. (row numbers there).

| Function | Args | Effect |
|---|---|---|
| `workbooks.get_workbooks` | search_term, limit, scope owned/shared | list workbooks |
| `workbooks.import_workbook` | workbook (dict or JSON str) | creates a NEW workbook with all children; ALWAYS duplicates; returns workbook name only |
| `workbooks.create_folder` / `rename_folder` / `delete_folder` | workbook,title,folder_type / folder_name,... | folder CRUD (`api/workbooks.py:252-283`) |
| `workbooks.move_item_to_folder` | item_type, item_name, folder_name | set `folder` on query/chart |
| `workbooks.update_sort_orders` (POST) | workbook, items | rewrite sort_order |
| `workbooks.get_share_permissions` / `update_share_permissions` | workbook_name, ... | DocShare rows; needs `share` |
| `dashboards.get_dashboards` | search_term, limit, scope | v3 dashboard list (`api/dashboards.py:67`) |
| `dashboards.update_dashboard_preview` (POST) | dashboard_name | generate preview image |
| `data_sources.get_all_data_sources` | none | v3 sources |
| `data_sources.get_data_source_tables` | data_source, search_term, limit | v3 Table list |
| `data_sources.get_data_source_table_columns` | data_source, table_name (`tab<DocType>`) | column catalog; used by `finsights catalog` |
| `data_sources.fetch_column_values` | data_source, table, column, search_text | distinct values (all 3 args required) |
| `data_sources.update_data_source_tables` (POST) | data_source | resync table list |
| `templates.get_workbook_templates` | none | shipped template listing |
| `templates.create_workbook_from_template` (POST) | template_name `<app>/<folder>` | Insights Admin; create-once per `from_template` |
| `templates.update_workbook_from_template` (POST) | template_name | Insights Admin; delete-and-rebuild children in place |
| `run_doc_method` / `get_doc` (module root, guest-reachable) | method, docs, args | doc methods; `Insights Workbook.export`, `Insights Query v3.execute`, `Insights Chart v3.export` |

v2 TRAPS (never call for v3 work): `dashboards.get_dashboard_list`, `create_dashboard`, `get_dashboard_options`, `add_chart_to_dashboard`; all of `queries.*` (`pivot` is guest-open); all of `public.*`; `home.*`; `data_sources.get_data_sources`, `get_tables`, `delete_data_source`. They read/write v2 doctypes.

## 3. Workbook export/import envelope
`Insights Workbook.export` (doc method, read-only) returns this; `import_workbook` consumes it.
```json
{"version":"1.0","type":"Workbook","name":"ury-sales-proof","doc":{"name":"ury-sales-proof","title":"URY Sales Proof"},
 "dependencies":{"folders":[],"queries":{"tq-pos-invoices":{...}},"charts":{"tc-kpis":{...}},"dashboards":{"td-overview":{...}}}}
```
| Key | Content |
|---|---|
| `version` / `type` | `"1.0"` string / `"Workbook"`. `export()` also adds `timestamp`; `finsights compile` omits it so output is byte-stable |
| `name`, `doc{name,title}` | file-local id (seeds the id_map); only `doc.title` is used on import |
| `dependencies.folders` | `[]` or `{name,title,type,sort_order}`; only folders holding items are exported |
| `dependencies.queries` | keyed by file id (`tq-...`); each `{name,title,workbook,folder,sort_order,use_live_connection,is_script_query,is_builder_query,is_native_query,operations[]}`; `operations[0].type=="source"`. Queries that are some chart's `data_query` are NOT exported |
| `dependencies.charts` | keyed `tc-...`; each `{name,title,workbook,folder,sort_order,query,chart_type,config}`; `query` = a key in `queries`; `data_query` is NOT exported |
| `dependencies.dashboards` | keyed `td-...`; each `{name,title,workbook,items[]}`; chart item `{type:"chart",chart,layout}`; filter item `{type:"filter",filter_name,filter_type,icon,default_operator,default_value,links,layout}` |

Import rules: file ids are discarded and become `id_map` entries; queries are inserted in dependency order; a filter link is silently dropped when its chart key or query is not in `id_map` or the value lacks the backtick-dot form.

**`import_workbook` ALWAYS duplicates**: no lookup precedes any insert, so two calls give workbooks "1" and "2". `Workbook.duplicate` is the same path. Do not use it for re-deploys. (`insights/insights/doctype/insights_workbook/insights_workbook.py:515-529`)

## 4. finsights shipping modes
| Mode | State on 3.15.1 | Behaviour |
|---|---|---|
| `compile` | core | spec -> envelope above (`--format template`, default) or flat standard shape (`--format standard`); `--check FILE` exits 1 on diff. |
| `apply --write` | core, the only live writer | REST upsert in order workbook, queries (topological), charts, dashboards. Idempotent: a second run prints `no changes`, exit 0. Dry run by default. Does not call `import_workbook`. |
| `ship --write` | works, DEPRECATED | writes `<app>/<app>/workbook_templates/<slug>/{manifest.json,workbook.json}` and `insights_workbooks = "workbook_templates"` in hooks.py; always warns (`insights/migrate.py:62`: hook removed next major). |
| `ship --standard --forward` | forward only, NOT loadable on 3.15.1 | writes `<app>/<module>/insights_workbook/<slug>/<slug>.json` with `is_standard:1`; Workbook has no `is_standard` field here. Without `--forward` it exits 2. |

Install/update of a shipped template (`insights/api/templates.py`): `create_workbook_from_template` takes a file lock, returns the existing copy when one has the same `from_template` (`:222-230,:301-304`), else imports, sets owner Administrator, grants org-wide read, stamps `imported_version` + `imported_checksum`. `update_workbook_from_template` deletes children and rebuilds in place (name, URLs, shares survive; `:363-414`). `sync_workbook_template_updates` runs every migrate and upgrades pristine copies when manifest `version` > `imported_version`; edited copies (checksum differs) are left alone (`:417-447`).

Manifest keys: required `title, description, required_apps, source_doctypes`; optional `version` (int), `notes`, `module`. Real one: `{"version":5,"title":"Sales Performance","module":"Selling","required_apps":["erpnext"],"source_doctypes":["Sales Invoice",...]}`. (`api/templates.py:15,51-62`). Template id = `<app>/<folder>`.

Proof status: `apply` idempotency (create all, second run no-op, one PUT per change, orphans kept without `--prune`, exit 3 on ambiguity) is proven by the fake-site tests in `tests/test_apply.py`. The live run of `finsights apply` is not recorded in; the REST create/update steps it uses are proven live (section 1). `tests/test_apply.py`

## 5. Upstream ERPNext/HRMS pattern (Insights develop; not on this bench)
Reference templates: the ERPNext and HRMS develop-branch workbooks and Insights develop source (fetch from their repos).
- Layout: `<app>/<module_folder>/insights_workbook/<slug>/<slug>.json`, same `<doctype>/<name>/<name>.json` convention as Reports. 6 ERPNext + 4 HRMS workbooks.
- Flat file: `doctype:"Insights Workbook"`, `name:<slug>`, `is_standard:1`, `module:"HR"`, `folders`, and `queries`/`charts`/`dashboards` dicts keyed by readable slug ids (no `dependencies` wrapper, no `version`/`timestamp`). Children carry `workbook:<slug>`; slug ids are the real docnames, so re-sync upserts. Dashboards add `visible_to_roles`.
- Loader: frappe `model/sync.py:72,148-181` syncs files by name on migrate, hash-skipping unchanged files; it only sees the folder if Insights declares `importable_doctypes` (not on 3.15.1).
- Desk link: a desk `Dashboard` doc `<module>/<module>_dashboard/<slug>/<slug>.json` with `insights_dashboard` = the Insights dashboard (Custom Field Link to Insights Dashboard v3); `Dashboard Chart.insights_chart` / `Number Card.insights_chart` likewise. Rendered by Frappe "UI islands" (`frappe/utils/island.py`), Frappe develop only.
- Develop-only workbook keys, not valid on 3.15.1: `number_format`, `window`, object-valued `comparison`, `layouts.sm`, `default_user_key`, `visible_to_roles`, string `default_operator`.
- Tier detect: `frappe/utils/island*` in frappe and `insights/desk.py` in insights both present = Tier B (embed). Otherwise Tier A: URL shortcut to `/insights/dashboards/<name>` (see desk-integration.md).
- Upstream test `hrms/tests/test_insights_workbooks.py` asserts every role in `visible_to_roles` can read every table its queries touch; copy that check when you ship role-gated dashboards.

## 6. Idempotency, naming, never-write
- Spec ids never become docnames. Workbook lookup: `from_template == "finsights:<workbook id>"`, then exact `title`; 0 matches create, >1 exits 3; a match carrying an app template id exits 3 unless `--adopt`. Marker is PUT right after the create. migrate ignores unknown markers (`api/templates.py:417-440`).
- Query/Chart/Dashboard are keyed by `(workbook, title)`; titles must be unique per kind (lint L04). Rename = `--rename <id>=<old title>`; without it a rename is create + orphan. Deletes only with `--prune` (order dashboards, charts, queries).
- Rewrite every spec-local name through `name_map` before diffing: source/join/union `query_name`, `chart.query`, item `chart`, filter `links` keys and values.
- Diff after normalising: parse JSON fields, sort keys, drop `layout.moved`, `modified`, `owner`, `data_query`, `linked_charts`.
- Never written by finsights: `data_query`, `linked_charts`.
- `--materialize-data-query` is off and not needed: API-created charts render without it.

## 7. Env, auth, exit codes
`finsights [--spec FILE] [--json] [--write] <lint|compile|catalog|plan|apply|verify|desk|ship|fmt>`. Env only: `INSIGHTS_URL` (required; missing = exit 2), then `INSIGHTS_API_KEY`+`INSIGHTS_API_SECRET` (header token) else `INSIGHTS_USER`+`INSIGHTS_PASSWORD` (login + cookie jar). urllib only; secrets never in `--json` output. Writes need Insights User; sharing needs `share`.

| Code | Meaning |
|---|---|
| 0 | ok / no changes |
| 1 | lint errors, verify failure, `compile --check` mismatch |
| 2 | usage or spec parse error (also `ship --standard` without `--forward`, missing INSIGHTS_URL) |
| 3 | live conflict: ambiguous lookup, template-managed workbook without `--adopt`, orphan drift |
| 4 | auth, network, HTTP 5xx |
| 5 | `plan --exit-code` with pending changes |

## Unverified
- Live end-to-end `finsights apply` run (only fake-site tests and the underlying REST calls are proven).
- Whether Insights develop's `importable_doctypes` loader preserves hand edits on migrate.
- `import_workbook` HTTP body as a raw dict vs JSON string.
- Hash naming of children is confirmed live; fixtures-based shipping of workbooks is untested.
