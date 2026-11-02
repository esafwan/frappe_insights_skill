---
name: frappe-insights
description: Build, change and verify Frappe Insights v3 (3.15.x, version-3 branch) dashboards, workbooks, queries, charts, dashboard filters, KPI number cards and analytics plans for a Frappe v16 app such as ERPNext or URY (sales, menu performance, payments dashboards), publish them deterministically with the bundled `finsights` CLI (spec -> lint -> compile -> plan -> apply -> verify), and surface a dashboard in desk as a Workspace/Sidebar/Dock link. Not for Insights v2 (`Insights Query`/`Insights Dashboard` doctypes), Frappe v15, desk Dashboard Chart or Number Card definitions that do not involve Insights (use frappe-desk), or website/portal pages.
---

# frappe-insights

Insights v3 stores a Workbook that owns Queries, Charts and Dashboards. The agent decides the analytics (what question, which table, which grain); `scripts/finsights` turns a short YAML/JSON spec into exact workbook JSON, diffs it against the live site, writes it through the API, and proves it by running every query. Hand-written JSON and raw `import_workbook` calls are the usual source of duplicate workbooks, blank charts and dead filters.

Frappe v16 + Insights 3.15.1 only. If the site shows `Insights Query` / `Insights Dashboard` (no `v3`), it is v2: stop and say so (`references/v2-v3-traps.md`).

## The loop
One sequence, in this order. `<F>` = `<this skill>/scripts/finsights` (python3, stdlib only).
```bash
export INSIGHTS_URL=https://site        # + INSIGHTS_USER/INSIGHTS_PASSWORD, or INSIGHTS_API_KEY/INSIGHTS_API_SECRET
# 0. plan the analytics first: references/analytics-planning.md (+ templates/plan_template.md); write the spec (examples/*.yaml are models; grammar: references/spec-format.md)
F=<this skill>/scripts/finsights
$F --spec s.yaml catalog -o cat.json           # 1. NEEDS network + INSIGHTS_*: live column snapshot
$F --spec s.yaml lint --strict --catalog cat.json   # 2. no network; exit 1 on errors
$F --spec s.yaml compile -o out.json           # 3. no network; inspect; --check FILE exits 1 on drift
$F --spec s.yaml plan                          # 4. NEEDS INSIGHTS_*: GET only, + create / ~ update / - orphan
$F --spec s.yaml --write apply                 # 5. NEEDS INSIGHTS_*: POST/PUT; dry-run without --write
$F --spec s.yaml verify                        # 6. NEEDS INSIGHTS_*: GET back, zero diff, run every query, rows >= 1
$F --spec s.yaml desk --dashboard <live name> --app <app path>   # 7. prints fdesk commands; never executes them
```
Steps 2 and 3 need no env vars. Step 7 needs none either when `--dashboard` is given; without `--dashboard` and with `INSIGHTS_URL` set, `desk` looks up the live name itself, otherwise it prints a placeholder name.
**The live dashboard name (a server-generated id, not the spec slug) only exists after `apply --write`.** So desk is always last: apply -> verify -> read the live name from the output -> `desk --dashboard <live name>`. `desk` itself never executes `fdesk`; you review and run the printed commands.

Exit codes: 0 ok, 1 lint/verify failure, 2 usage/parse, 3 live conflict, 4 auth/network, 5 `plan --exit-code` pending. Auth comes from env only, never stored. Add `--json` for machine output. `apply` is idempotent: a second run reports no changes. `references/spec-format.md` has the spec grammar; `finsights <cmd> -h` is the authoritative flag list.

## Task -> command (details in the references)
| Task | Command / file |
|---|---|
| Decide what to build | `references/analytics-planning.md`, `templates/plan_template.md`, URY: `references/ury-analytics.md` |
| New dashboard/query/chart | edit spec, `lint`, `compile`, `plan`, `apply --write` (`references/queries.md`, `charts.md`, `dashboards-filters.md`) |
| Check column names exist | `catalog -o cat.json` then `lint --catalog cat.json` |
| Hour of day, ratio, cast, date bucket | `references/expressions.md` (then `queries.md`) |
| Rename a chart/query | `apply --rename id=OLD TITLE` (children are keyed by workbook + title; exit 3 if OLD is also another spec item's title) |
| Take over an existing workbook | `apply --write --adopt`, review `plan`, only then `apply --write --prune` (rule 13) |
| Prove it works | `verify --min-rows 1`; opens `/insights/dashboards/<name>` |
| Link it in desk | apply, verify, then `desk --dashboard <live name> --app <app path>`, then run the printed `fdesk` commands (frappe-desk skill) |
| Ship inside an app | `ship --app PATH [--templates-dir DIR] [--standard --forward]` (`references/api-shipping.md`) |
| YAML to JSON | `fmt --to json` |
| One call: build, heal, review, export | `build --spec s.yaml [--write]`, `bulk`, `review --workbook REF`, `export --workbook REF`, `heal` (`references/api.md`) |

## One-call API (for LLM loops)
`finsights build --spec s.yaml --json` (or `api.build` in Python) does lint, safe self-heal, compile, plan, apply, verify in one call and returns `ok`, `healed`, `needs_decision`, `confirm`. Dry run first; write only when clean. Safe fixes (ids, duplicate titles, chart-type aliases, layout) are applied; unknown tables/columns come back as `needs_decision` with alternatives and are never auto-applied. Prune and title-adoption need the `confirm` token from a dry run. `bulk`, `review`, `export` (faithful compiled envelope, not a spec) and `heal` are in `references/api.md`.

## Hard rules (each has a reason)
1. **v3 doctypes only**: Insights Workbook, Insights Query v3, Chart v3, Dashboard v3, Data Source v3, Table v3. The v2 doctypes and old `api/dashboards.py`, `queries.py`, `public.py` do not drive the v3 UI.
2. **Never hand-edit workbook JSON**; edit the spec and `compile`. Names, ordering and normalisation are the CLI's job, and `verify` diffs against the compile.
3. **`import_workbook` creates a new workbook every call** (duplicates). Use `finsights apply`, which resolves live names and updates in place.
4. **`run_doc_method` needs the FULL query doc**, not `{doctype,name}`; the short form fails with `NoneType` not iterable. `verify` does this for you.
5. **Never write `data_query` or `linked_charts`**: the server creates/maintains them (an empty `data_query` is auto-created per chart; dashboard `linked_charts` is derived from `items`).
6. **A chart's `config` carries dimensions/measures; the query is the base table plus filters.** Do not pre-aggregate in the query for charts that dashboard filters must hit.
7. **Row-level query + chart-side aggregation**, so dashboard filters bind to the query's final columns. An aggregated query hides the column the filter needs.
8. **Number chart: with a date column + comparison it shows the LATEST period, without a date it shows the TOTAL.** Pick deliberately (verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02).
9. **A malformed filter link is dropped silently.** Link shape: `{<chart live name>: "`<query live name>`.`<column>`"}` (backticks). `verify` rule V05 checks every filter link (chart is on the dashboard, format, query equals the chart's query, column is in the query result) and warns on a filter with no links; a filter with no effect means a bad link.
10. **Money questions filter `docstatus = 1`.** URY: use POS Invoice with `docstatus=1`; never filter `status = 'Paid'` (about two thirds of rows are `Consolidated`) and always filter `Sales Invoice Payment.parenttype = 'POS Invoice'` (see `references/ury-analytics.md`).
11. **Two desk tiers** (`references/desk-integration.md`). Tier A (Frappe 16.54.0 + Insights 3.15.1, today; every v16 release): dashboard lives at `/insights/dashboards/<name>`, surfaced by a Workspace URL shortcut / Sidebar URL item / Dock URL tile; not embedded. Tier B (Frappe develop 17.0.0-dev islands + Insights develop): `Dashboard.insights_dashboard` embeds it; islands exist in no v16 release. Detect with the commands in `references/desk-integration.md` section 0.
12. **Never make a dashboard or chart public to share financial data.** Share with named users/roles (`references/permissions.md`).
13. **Adopt, review, then prune; never both in one run.** A workbook with the same title that finsights did not create is refused (exit 3) unless `--adopt`; `--prune` deletes every query/chart/dashboard not in the spec, so with `--adopt` it would wipe someone else's content unseen. `apply --prune` is refused (exit 3) in the run that adopted by title. Order: `apply --write --adopt`, read `plan`, then `apply --write --prune`. If `apply` fails part-way it prints the lines done so far to stderr; fix the cause and re-run (idempotent).

## When something looks wrong
| Symptom (rows tagged (unverified) have no live reproduction) | Look at |
|---|---|
| Blank chart | chart `config` columns not in the query output; query returns 0 rows (`verify` V02); catalog column name wrong (`references/charts.md`, `gotchas.md`) |
| Dashboard filter has no effect | link malformed or points at an aggregated column (rules 7, 9; `dashboards-filters.md`) |
| Duplicate workbook on the site | someone used `import_workbook`; keep one, `apply --adopt`, delete the other by hand |
| `NoneType` not iterable on execute | `run_doc_method` sent without the full doc (rule 4) |
| Numbers off by a day (unverified) | date grain/timezone: group on the date column at day grain and check the site timezone (`gotchas.md`) |
| Branch (or any link) filter shows empty options (unverified) | wrong column, or column NULL in the base rows; check with `catalog` and a row-level query (`dashboards-filters.md`) |
| Number card shows one period, expected total (or reverse) | rule 8 |
| `plan` says orphan | spec lost a title; use `--rename`, else `--prune` deliberately |
| `ship --standard` loads nothing on 3.15.1 | forward format, Tier B only (`api-shipping.md`) |

## Where to read what
- `references/spec-format.md`: spec grammar, lint rules L01-L28, commands, verify rules V01-V05, YAML scalar rules.
- `references/model.md`: how Workbook/Query/Chart/Dashboard/Data Source relate; the document fields.
- `references/v2-v3-traps.md`: v2 names that look right and are wrong.
- `references/queries.md`: operations, expressions, row-level patterns, MariaDB quirks.
- `references/expressions.md`: hour of day, ratio, cast, date-bucket recipes for query expressions.
- `references/charts.md`: chart types, `config` shapes, number/trend/breakdown recipes.
- `references/dashboards-filters.md`: layout, items, filter links, global filters.
- `references/api.md`: one-call build, bulk, heal, review and export API, self-heal contract, confirm tokens.
- `references/api-shipping.md`: the HTTP API and shipping a workbook inside an app.
- `references/desk-integration.md`: Tier A vs Tier B and the exact fdesk commands.
- `references/permissions.md`: who can read, write, share; sharing without going public.
- `references/analytics-planning.md`: from business question to metric, grain, dimensions.
- `references/ury-analytics.md`: URY tables, trap columns, proven queries.
- `references/gotchas.md`: every trap with its cause and fix.
- `examples/ury_overview.yaml`, `ury_menu_performance.yaml`, `ury_payments.yaml`: working specs.

## Do not
- Do not call `import_workbook`, or write `data_query`/`linked_charts`, directly.
- Do not guess a column: `catalog` first; lint L05, L06 and L08 flag unknown tables/columns (only with a catalog).
- Do not rely on `share:` in a spec: it is not applied (lint L26 warns); grant access in the Insights UI (`references/permissions.md`).
- Do not present v2 doctypes or APIs as usable.
- Do not use `bench console` or SQL writes to fix Insights docs; fix the spec and re-apply.
- Do not claim a dashboard works until `verify` passes and the page was opened.

## Unverified
- Symptom rows "numbers off by a day" and "empty branch filter" are experience-level pointers, not reproduced live.
