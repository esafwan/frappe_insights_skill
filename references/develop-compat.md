# Insights 3.50-dev (Frappe develop) compatibility shim

Verified 2026-11-03 on Frappe 17.0.0-dev + Insights 3.50.0-dev (commit 8352da5). Not for production; the target is Tier B (islands).
`finsights` 3.15 specs apply unchanged except for these differences, all handled by the shim:

| Difference on 3.50-dev | Shim |
|---|---|
| Site DB source refuses a table with no DocType (`DocType POS Invoice not found`) | Create a second `Insights Data Source v3` (type Database, MariaDB, same DB, `is_site_db` 0) and `export INSIGHTS_DATA_SOURCE=<its name>`; used by compile and `catalog`. Then `POST insights.api.data_sources.update_data_source_tables` to sync its tables. |
| `Insights Workbook.from_template` and `Insights Chart v3.data_query` fields are gone | `Client.has_field` probes the live DocType; with no marker a workbook is matched by title and `--prune` stays refused. |
| Server rewrites chart config on save (adds `id`, `number_column_options`; Number `comparison: true` -> `number_column_options[].comparison.source = previous`; `date_column.granularity` -> `window.grain`) | `apply.to_develop_config` plus a subset compare (`_contains`), so verify/plan do not report drift. |

Tests: `tests/test_develop_compat.py`. Full report: ury_workspaces/tracks/frappe-insights-skill/design/islands_proof_report.md.
