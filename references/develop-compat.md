# Insights 3.50-dev (Frappe develop) compatibility shim

Verified 2026-11-03 on Frappe 17.0.0-dev + Insights 3.50.0-dev (commit 8352da5). Not for production; the target is Tier B (islands).
`finsights` 3.15 specs apply unchanged except for these differences, all handled by the shim:

| Difference on 3.50-dev | Shim |
|---|---|
| Site DB source refuses a table with no DocType (`DocType POS Invoice not found`) | Create a second `Insights Data Source v3` (type Database, MariaDB, same DB, `is_site_db` 0) and `export INSIGHTS_DATA_SOURCE=<its name>`; used by compile and `catalog`. Then `POST insights.api.data_sources.update_data_source_tables` to sync its tables. |
| `Insights Workbook.from_template` and `Insights Chart v3.data_query` fields are gone | `Client.has_field` probes the live DocType; with no marker a workbook is matched by title and `--prune` stays refused. |
| Server rewrites chart config on save (adds `id`, `number_column_options`; Number `comparison: true` -> `number_column_options[].comparison.source = previous`; `date_column.granularity` -> `window.grain`) | `apply.to_develop_config` plus a subset compare (`_contains`), so verify/plan do not report drift. |

Tests: `tests/test_develop_compat.py`. Full report: ury_workspaces/tracks/frappe-insights-skill/design/islands_proof_report.md.

## Islands on Frappe 16.50 + PR 45046 (verified 2026-11-03)

A throwaway v16 bench can run islands before the backport ships: `bench init --frappe-branch version-16-hotfix`, then in `apps/frappe` run `git fetch --unshallow upstream` (the remote is `upstream`, and a shallow clone shares no history with the PR) and `git fetch upstream pull/45046/head`, merge it, and install Insights `develop`. After restoring a demo site, `desk_islands.setup()` created all 16 desk dashboards and they rendered with data at `/desk/build/dashboard-view/<name>`.

Bench gotchas hit on the way:
- `yarn config set ignore-engines true` when a dependency (jsdom) wants a newer Node than the container has.
- `sites/apps.txt` needs a trailing newline before appending an app name, or two names fuse (`erpnextury`).
- Restoring a demo backup needs every app in the backup installed on the target bench (Huf included).
- A site restored from a fresh install may route to the setup wizard; mark `Installed Application.is_setup_complete` and System Settings `setup_complete`.
- `localhost` matches no site folder: run `bench use <site>` before `bench serve`, or requests fail with `AttributeError: is_ajax`.
- Browsers cache `app.js?v=<stamp>` aggressively; hard-reload after rebuilding a bundle.
- Read `frappe.get_meta("Dashboard", cached=False)` inside `after_migrate`; the cached meta predates the doctype sync, so the islands support check wrongly reports "not supported" on the first migrate.
