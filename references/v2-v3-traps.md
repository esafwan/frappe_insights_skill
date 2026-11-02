# v2 vs v3 traps

Insights 3.15.1 ships BOTH generations in one repo. Everything you author is v3. v2 names are listed here only so you can recognise and avoid them.

## Name table

| v2 (do not use) | v3 (use) | Tell apart in code | Tell apart in URL |
|---|---|---|---|
| Insights Query (`QRY-####`) | Insights Query v3 | v2 has Query Column/Table/Transform/Result children; v3 has `operations` JSON | v2 `/insights/query/build/<n>`; v3 `/insights/workbook/<wb>/query/<q>` |
| Insights Dashboard (`DSH-####`) + Dashboard Item | Insights Dashboard v3 (+ Dashboard Chart v3, derived) | v2 layout is child doctype Dashboard Item; v3 is `items` JSON | v2 `/insights/dashboard/<n>` (singular); v3 `/insights/dashboards/<n>` |
| Insights Chart | Insights Chart v3 | v2 chart linked via Insights Query Chart; v3 has `config` JSON + `query` Link | v2 `/insights/public/chart/<key>`; v3 `/insights/shared/chart/...` |
| Insights Data Source (`field:title`) | Insights Data Source v3 (`scrub(title)`) | hooks.py lists both; only v3 has `is_site_db`, REST API type, DuckDB/ClickHouse | same `/insights/data-source` prefix in both routers: URL does not disambiguate, doctype name does |
| Insights Table / Table Link (child table) | Insights Table v3 / Table Link v3 (standalone) | v2 Table Link has `istable=1` | - |
| (none) | Insights Workbook, Folder, Team, Team Member, Resource Permission | v3-era but NO `_v3` suffix; never append one | `/insights/workbook`, `/insights/workbook/<n>` |

Sources: `R1 s3`, `insights.py:14-35`, `hooks.py:97-101,123-140`, `src/router.ts:29-36`, `src2/router.ts:21-52`.

## API modules

| Module (`insights/api/`) | Generation | Rule |
|---|---|---|
| `workbooks.py` | v3 | usable |
| `data_sources.py`, `insights.api.run_doc_method` (`api/__init__.py:300`) | v3-compatible | usable; see [api-shipping.md](api-shipping.md) |
| `dashboards.py` top half, `queries.py`, `public.py`, `home.py` | v2 | never call; they operate on v2 doctypes `SWARM_STATE wave-1 gate`, `R5` |
| `v2_migration.py` | bridge | v2 -> v3 copy; tracked by `old_name` on the three v3 doctypes, no status doctype `v2_migration.py:9-10` |

## Frontend: src vs src2

| Path | Generation | Entry |
|---|---|---|
| `frontend/src` | v2 | `index_v2.html`, vite entry `insights_v2` `vite.config.js:37` |
| `frontend/src2` | v3 | `src2/router.ts` |

Trap: older plans and many docs say "frontend/src" for Insights UI. For v3 code, routes, query/chart types, always read `src2`. A route found in `src/router.ts` (`/dashboard`, `/query/build`) is v2 and must not be cited as v3. `R1 note`.

## Routing gate

`www/insights.py` serves v3 immediately when `frappe.db.count("Insights Query") == 0` (no v2 queries). A site WITH v2 queries is redirected to v2 for v2-pattern paths (`/insights/query`, `/insights/dashboard`, `/insights/public/*`) and goes to v3 only if the path contains `/insights/dashboards`, `/insights/workbook`, `/insights/shared/chart` or `/insights/shared/dashboard` `www/insights.py:14-35`. Hooks route `/insights` and `/insights_v2` separately `hooks.py:236-238`.

Rules: always write v3 doctypes; build desk links as `/insights/dashboards/<name>` (never `/insights/dashboard/`); when checking a doctype in code or an API response, a name without `v3` that is not Workbook/Folder/Team/Team Member/Resource Permission is v2.

## Unverified

- Behaviour of `/insights/dashboards/<n>` on a site that also holds v2 queries was read from `insights.py`, not tested live (the test bench has none).
