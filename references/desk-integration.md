# Desk integration (two tiers)

Target the tier the bench supports; detect first, never assume. Cites: `R6` = research/R6_desk_perms.md, `C` = research/C_keynote_clues.md, `T` = tests/test_desk.py.

## 0. Detect the tier (read-only, do this first)
```
B=/Users/safwan/Code/Docker/frappe_docker/benches/<bench>/apps
fd -t f 'island*' $B/frappe/frappe/utils $B/frappe/frappe/public 2>/dev/null   # any hit = islands exist
fd -t f desk.py $B/insights/insights --max-depth 1                              # present = Insights claims desk docs
rg -n 'insights_dashboard' $B/frappe/frappe/desk/doctype/dashboard/dashboard.json   # field present = Frappe supports the claim
```
| Result | Tier |
|---|---|
| none of the three hit (Frappe 16.54.0 / Insights 3.15.1) | **A (url)** |
| `frappe/utils/island.py` AND `insights/desk.py` AND the Dashboard field | **B (island)** |
| only some hit | A. Partial = unsupported; the claim field is ignored without the renderer. |
Facts: Frappe `version-16` v16.54.0 has no `frappe/utils/island*` and `Dashboard` fields are only `dashboard_name is_default charts chart_options cards is_standard module` (`R6` "Upstream desk pattern" 2); Insights 3.15.1 has no `desk.py` (`C` 5). `desk_islands()` in Insights is `find_spec("frappe.utils.island") is not None` (`C` 1).

## 1. Tier A: link, do not embed (works today)
Dashboard URL: `/insights/dashboards/<dashboard_name>` where `<dashboard_name>` is the LIVE server id (hash), not the spec slug; read it from `finsights apply`/`verify` output (`R6` Part 1; `T` placeholder `<dashboard name>`). Deep links under `/insights/dashboards`, `/insights/workbook`, `/insights/shared/*` skip the v2 redirect (`R6` Part 1 "Do v2 sites hijack").
Insights is already in the v16 app switcher (`/apps`) for Insights User/Admin; it has no sidebar, workspace or dock entry of its own (`R6` Part 1 rows 1-3).

### `finsights desk` (never executes; prints commands)
```
finsights --spec <spec.yaml> desk --dashboard <live-name> --app <app path> [--tier url|island] [--json] [--write]
```
Spec input (`tests/fixtures/ury_overview.yaml` shape): `desk: {workspace: URY, label: Sales Analytics}`; optional `sidebar: {section: Dashboards}`, `dock: true`. Exact output for `desk --dashboard abc123 --app apps/ury` with sidebar+dock set (`T` test_url_tier_*, desk.py):
```
fdesk --app apps/ury --write workspace add-shortcut URY --type URL --url /insights/dashboards/abc123 --label 'Sales Analytics'
fdesk --app apps/ury --write sidebar add-item URY --section Dashboards --link-type URL --url /insights/dashboards/abc123 --label 'Sales Analytics'
fdesk --app apps/ury --write dock add-tile --link-type URL --url /insights/dashboards/abc123 --title 'Sales Analytics' --icon chart-line
fdesk --app apps/ury lint --only L07,L10,L13
```
You run these yourself, after reviewing. `--icon chart-line` is a guess: run `fdesk icons check` first (`R6` notes).

### Underlying `fdesk` flags (copied from `fdesk <group> <cmd> -h`)
| Command | Flags |
|---|---|
| `fdesk workspace add-shortcut <workspace>` | `--type {DocType,Report,Page,Dashboard,URL}` `--url` `--label` (required) `[--to --doc-view --color --stats-filter --col --after]` |
| `fdesk sidebar add-item <sidebar>` | `--link-type {DocType,Page,Report,Workspace,Dashboard,URL}` (required) `[--section --to --url --label --icon --after --open-in-new-tab --hidden ...]` |
| `fdesk dock add-tile` | `--link-type {Sidebar,Workspace,URL}` `--title` `--icon` (required) `[--to --url --after]` |
Global flags go BEFORE the group: `fdesk --app . --write workspace ...`; dry-run unless `--write` (`R6` snippets). Shortcut row produced: `{"type": "URL", "label": "Sales Dashboard", "url": "/insights/dashboards/<name>"}` (`R6` Shapes).
Use a relative URL; one URL item per surface; DocType shortcuts need no URL. Verify with `fdesk lint --only L07,L10,L13`.

### Why not iframe (do not build it)
- Nginx sends `X-Frame-Options "SAMEORIGIN"` and Insights adds `frame-ancestors 'self' <allowed_origins>` only on `/insights/{public,shared}` paths and only if Insights Settings `allowed_origins` is set (`R6` iframe 1,3, S13-S14).
- A private `/insights/dashboards/<name>` has no CSP header logic of its own; a same-origin frame would use the viewer's session, but the app SPA is a full page (header, routing) and nothing in the research proves it works.
- The only embeddable form is `/insights/shared/dashboard/<name>`, which needs `is_public: 1` (`R6` iframe 2, 6; see permissions.md section 5: never for financial data). R6 also never confirmed an `<iframe>` survives Custom HTML Block sanitising (`R6` Unknown 1).
Decision: Tier A = link. Do not publish a dashboard just to iframe it.

## 2. Tier B: forward-only (Frappe develop + Insights develop)
Mechanism (verified in upstream code, not runnable here): Frappe UI islands, a Vue component behind a shadow root registered via assets.json (`frappe/utils/island.py`, `ui/island/`) (`C` 1). Insights develop `insights/desk.py` `DESK_ISLANDS` creates Custom Fields that link desk docs to Insights content; when the link is set the doc's `onload` sets `__onload.island` and desk renders the island instead of its own definition (`C` 2; `research/refs/insights_develop/desk.py:44-80,348-362`):
| Desk doctype | Field (Link) | Island | Notes |
|---|---|---|---|
| `Dashboard` | `insights_dashboard` -> `Insights Dashboard v3` | `insights.dashboard` | folder `{module}_dashboard` |
| `Dashboard Chart` | `insights_chart` -> `Insights Chart v3` | `insights.chart` | |
| `Number Card` | `insights_chart` -> `Insights Chart v3` | `insights.chart` | props `{card: false}` |
Link (not Data) so standard sync and rename work and a claimed member cannot be deleted (`C` 2).

**Correction to R6**: "Insights chart as a desk Number Card: no native path" is true only for 3.15.1 (it uses `Custom` + a method you write). On Tier B it is native via `Number Card.insights_chart` (`C` Consequences). Never promise it on Tier A.

Shipping on Tier B (ERPNext/HRMS develop pattern, `C` 3): standard docs `<module>/insights_workbook/<slug>/<slug>.json` (Insights Workbook, `is_standard=1`, `module`) plus `<module>/<module>_dashboard/<slug>/<slug>.json`, a desk `Dashboard` carrying `insights_dashboard`. Literal shape (`R6` Upstream pattern, stock.json trimmed):
```json
{"cards": [{"card": "Total Active Items"}], "charts": [{"chart": "Warehouse wise Stock Value", "width": "Full"}],
 "dashboard_name": "Stock", "doctype": "Dashboard", "insights_dashboard": "stock", "is_default": 1, "is_standard": 1, "module": "Stock", "name": "Stock"}
```
`finsights desk --tier island` prints that doc for a spec; `finsights compile --format standard` emits the workbook (golden `tests/golden/ury_overview.standard.json`). Output (`T` test_island_tier_json): `{"doctype":"Dashboard","name":"Sales Analytics","dashboard_name":"Sales Analytics","docstatus":0,"module":"URY","is_standard":1,"insights_dashboard":"ury-sales-overview"}`.
Templates: study `research/refs/erpnext` (selling, buying, stock) and `research/refs/hrms` workbooks for layout: KPI row + trend + breakdown + colour-coded table + global filters (`C` Consequences).

Develop-only features shown in the keynote and workbook keys (NOT in 3.15.1; do not write them for Tier A): targets and last-year comparison on number cards (object-valued `comparison`), `number_format`, `window`, `layouts.sm`, `default_user_key`, `visible_to_roles`, string `default_operator`, global filters across all charts, click-to-drill-down (view records / break down by dimension, backtrack, jump to doc), in-progress "ask the dashboard" agent respecting user permissions (`C` talk + 4).

## Unverified
1. Tier B was read from upstream sources only; no develop bench exists, nothing run live.
2. Whether `fdesk lint L10` accepts a relative `/insights/...` URL, and whether the desk opens it same-tab: not executed (`R6` Unknowns 2-3). Tier A shortcut/sidebar/dock live render not yet proven (SWARM_STATE: desk URL shortcut "STILL TO PROVE").
3. Frame behaviour of a private dashboard and `<iframe>` survival in Custom HTML Block (`R6` Unknown 1).
4. Develop's Frappe-side Dashboard field definition and island renderer: not read (`R6` extra unknown).
