---
name: frappe-insights
description: Build, change and verify Frappe Insights v3 (3.15.x, version-3 branch) dashboards, workbooks, queries, charts, dashboard filters, KPI number cards and analytics plans for a Frappe v16 app such as ERPNext or URY (sales, menu performance, payments dashboards), publish them deterministically with the bundled `finsights` CLI (spec -> lint -> compile -> plan -> apply -> verify), and surface a dashboard in desk as a Workspace/Sidebar/Dock link. Not for Insights v2 (`Insights Query`/`Insights Dashboard` doctypes), Frappe v15, desk Dashboard Chart or Number Card definitions that do not involve Insights (use frappe-desk), or website/portal pages.
---

# frappe-insights

Insights v3 stores a Workbook that owns Queries, Charts and Dashboards. The agent decides the analytics (what question, which table, which grain); `scripts/finsights` turns a short YAML/JSON spec into exact workbook JSON, diffs it against the live site, writes it through the API, and proves it by running every query. Hand-written JSON and raw `import_workbook` calls are the usual source of duplicate workbooks, blank charts and dead filters.

Frappe v16 + Insights 3.15.1 only. If the site shows `Insights Query` / `Insights Dashboard` (no `v3`), it is v2: stop and say so (`references/v2-v3-traps.md`).

## The loop
```bash
F=<this skill>/scripts/finsights        # python3, stdlib only
export INSIGHTS_URL=https://site        # + INSIGHTS_USER/INSIGHTS_PASSWORD, or INSIGHTS_API_KEY/INSIGHTS_API_SECRET
# 0. plan the analytics first: references/analytics-planning.md (+ templates/plan_template.md)
# 1. write the spec (examples/*.yaml are models)
$F --spec s.yaml lint --strict                 # no network; exit 1 on errors
$F --spec s.yaml compile -o out.json           # inspect; --check FILE exits 1 on drift
$F --spec s.yaml catalog -o cat.json           # live column snapshot; then lint --catalog cat.json
$F --spec s.yaml plan                          # GET only: + create / ~ update / - orphan
$F --spec s.yaml --write apply                 # POST/PUT; dry-run without --write
$F --spec s.yaml verify                        # GET back, zero diff, run every query, rows >= 1
$F --spec s.yaml desk --tier url               # prints fdesk commands for the desk link
```
Exit codes: 0 ok, 1 lint/verify failure, 2 usage/parse, 3 live conflict, 4 auth/network, 5 `plan --exit-code` pending. Auth comes from env only, never stored. Add `--json` for machine output. `apply` is idempotent: a second run reports no changes. `finsights_spec.md` section 4 has the full flag list; `finsights <cmd> -h` is authoritative.

## Task -> command (details in the references)
| Task | Command / file |
|---|---|
| Decide what to build | `references/analytics-planning.md`, `templates/plan_template.md`, URY: `references/ury-analytics.md` |
| New dashboard/query/chart | edit spec, `lint`, `compile`, `plan`, `apply --write` (`references/queries.md`, `charts.md`, `dashboards-filters.md`) |
| Check column names exist | `catalog -o cat.json` then `lint --catalog cat.json` |
| Rename a chart/query | `apply --rename id=OLD TITLE` (children are keyed by workbook + title) |
| Take over an existing workbook | `apply --adopt`; remove orphans only with `--prune` |
| Prove it works | `verify --min-rows 1`; opens `/insights/dashboards/<name>` |
| Link it in desk | `desk --tier url`, then run the printed `fdesk` commands (frappe-desk skill) |
| Ship inside an app | `ship --app PATH [--standard --forward]` (`references/api-shipping.md`) |
| YAML to JSON | `fmt --to json` |

## Hard rules (each has a reason)
1. **v3 doctypes only**: Insights Workbook, Insights Query v3, Chart v3, Dashboard v3, Data Source v3, Table v3. The v2 doctypes and old `api/dashboards.py`, `queries.py`, `public.py` do not drive the v3 UI.
2. **Never hand-edit workbook JSON**; edit the spec and `compile`. Names, ordering and normalisation are the CLI's job, and `verify` diffs against the compile.
3. **`import_workbook` creates a new workbook every call** (duplicates). Use `finsights apply`, which resolves live names and updates in place.
4. **`run_doc_method` needs the FULL query doc**, not `{doctype,name}`; the short form fails with `NoneType` not iterable. `verify` does this for you.
5. **Never write `data_query` or `linked_charts`**: the server creates/maintains them (an empty `data_query` is auto-created per chart; dashboard `linked_charts` is derived from `items`).
6. **A chart's `config` carries dimensions/measures; the query is the base table plus filters.** Do not pre-aggregate in the query for charts that dashboard filters must hit.
7. **Row-level query + chart-side aggregation**, so dashboard filters bind to the query's final columns. An aggregated query hides the column the filter needs.
8. **Number chart: with a date column + comparison it shows the LATEST period, without a date it shows the TOTAL.** Pick deliberately (live finding, `SWARM_STATE.md` 2026-11-02).
9. **A malformed filter link is dropped silently.** Link shape: `{<chart live name>: "`<query live name>`.`<column>`"}` (backticks). `verify` checks links; a filter with no effect means a bad link.
10. **Money questions filter `docstatus = 1`.** URY: use POS Invoice with `docstatus=1`; never filter `status = 'Paid'` (about two thirds of rows are `Consolidated`) and always filter `Sales Invoice Payment.parenttype = 'POS Invoice'` (`research/U1_ury_sales_plan.md`).
11. **Two desk tiers** (`research/C_keynote_clues.md`). Tier A (Frappe 16.54 + Insights 3.15.1, today): dashboard lives at `/insights/dashboards/<name>`, surfaced by a Workspace URL shortcut / Sidebar URL item / Dock URL tile; not embedded. Tier B (Frappe develop islands + Insights develop): `Dashboard.insights_dashboard` embeds it; not loadable here. Detect: `frappe/utils/island*` in the bench frappe and `insights/desk.py` in the insights app.
12. **Never make a dashboard or chart public to share financial data.** Share with named users/roles (`references/permissions.md`).

## When something looks wrong
| Symptom | Look at |
|---|---|
| Blank chart | chart `config` columns not in the query output; query returns 0 rows (`verify` V02); catalog column name wrong (`references/charts.md`, `gotchas.md`) |
| Dashboard filter has no effect | link malformed or points at an aggregated column (rules 7, 9; `dashboards-filters.md`) |
| Duplicate workbook on the site | someone used `import_workbook`; keep one, `apply --adopt`, delete the other by hand |
| `NoneType` not iterable on execute | `run_doc_method` sent without the full doc (rule 4) |
| Numbers off by a day | date grain/timezone: group on the date column at day grain and check the site timezone (`gotchas.md`) |
| Branch (or any link) filter shows empty options | wrong column, or column NULL in the base rows; check with `catalog` and a row-level query (`dashboards-filters.md`) |
| Number card shows one period, expected total (or reverse) | rule 8 |
| `plan` says orphan | spec lost a title; use `--rename`, else `--prune` deliberately |
| `ship --standard` loads nothing on 3.15.1 | forward format, Tier B only (`api-shipping.md`) |

## Where to read what
- `references/model.md`: how Workbook/Query/Chart/Dashboard/Data Source relate; the document fields.
- `references/v2-v3-traps.md`: v2 names that look right and are wrong.
- `references/queries.md`: operations, expressions, row-level patterns, MariaDB quirks.
- `references/charts.md`: chart types, `config` shapes, number/trend/breakdown recipes.
- `references/dashboards-filters.md`: layout, items, filter links, global filters.
- `references/api-shipping.md`: the HTTP API and shipping a workbook inside an app.
- `references/desk-integration.md`: Tier A vs Tier B and the exact fdesk commands.
- `references/permissions.md`: who can read, write, share; sharing without going public.
- `references/analytics-planning.md`: from business question to metric, grain, dimensions.
- `references/ury-analytics.md`: URY tables, trap columns, proven queries.
- `references/gotchas.md`: every trap with its cause and fix.
- `examples/ury_overview.yaml`, `ury_menu_performance.yaml`, `ury_payments.yaml`: working specs.

## Do not
- Do not call `import_workbook`, or write `data_query`/`linked_charts`, directly.
- Do not guess a column: `catalog` first; lint L-rules flag unknown columns.
- Do not present v2 doctypes or APIs as usable.
- Do not use `bench console` or SQL writes to fix Insights docs; fix the spec and re-apply.
- Do not claim a dashboard works until `verify` passes and the page was opened.

## Unverified
- Symptom rows "numbers off by a day" and "empty branch filter" are from experience-level pointers; the cause lines are not cited to a live reproduction.
- Exact lint rule IDs for unknown columns not checked against `finsights_lib/lint.py`.
