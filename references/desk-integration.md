# Desk integration (two tiers)

Target the tier the bench supports; detect first, never assume. Today (Frappe 16.54.0 / Insights 3.15.1) the answer is **Tier A**. Islands exist only on Frappe `develop` (17.0.0-dev), not in any v16 release.

## 0. Detect the tier (read-only, do this first)
```bash
BENCH=<path to your bench>
ls $BENCH/apps/frappe/frappe/utils | grep -i island    # no output = Tier A
ls $BENCH/apps/insights/insights/desk.py               # "No such file" = Tier A
```
| Result | Tier |
|---|---|
| no island file AND no `insights/desk.py` (Frappe 16.54.0 / Insights 3.15.1) | **A (url)** |
| island file in `frappe/utils` AND `insights/desk.py` present | **B (island)** |
| only one of the two | A. Partial = unsupported; the claim field is ignored without the renderer. |
Facts (verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02): Frappe `version-16` has no `frappe/utils/island*`; the `Dashboard` doctype fields are only `dashboard_name is_default charts chart_options cards is_standard module`; Insights 3.15.1 has no `desk.py`. In Insights develop, `desk_islands()` is `find_spec("frappe.utils.island") is not None` (from Insights source: insights/desk.py).

## 1. Tier A: link, do not embed (works today)
Dashboard URL: `/insights/dashboards/<dashboard_name>` where `<dashboard_name>` is the LIVE server id (hash), not the spec slug; read it from `finsights apply`/`verify` output (the `desk` command prints a placeholder name until you pass `--dashboard`). Deep links under `/insights/dashboards`, `/insights/workbook`, `/insights/shared/*` skip the v2 redirect (verified live, 2026-11-02).
Insights is already in the v16 app switcher (`/apps`) for Insights User/Admin; it has no sidebar, workspace or dock entry of its own (verified live, 2026-11-02).

### `finsights desk` (never executes; prints commands)
Order matters: the live dashboard name exists only after `apply --write`. Sequence: `apply --write` -> `verify` -> read the live name from the output -> `desk`.
```
finsights --spec <spec.yaml> desk --dashboard <live-name> --app <app path> [--tier url|island] [--json] [--write]
```
`desk` itself never executes `fdesk`; it only prints commands (or, with `--tier island`, a Dashboard doc). It needs `INSIGHTS_*` env vars only when `--dashboard` is omitted and `INSIGHTS_URL` is set (it then resolves the live name via the API); with `--dashboard` it works offline.

What each `desk:` spec key emits (from scripts/finsights_lib/desk.py):
| Spec key | Emitted command |
|---|---|
| `workspace`, `label` (required) | `fdesk ... workspace add-shortcut <workspace> --type URL --url /insights/dashboards/<name> --label <label>` |
| `sidebar` (optional; `{section: ...}` or true) | `fdesk ... sidebar add-item <workspace> [--section ...] --link-type URL --url ... --label <label>` |
| `dock: true` (optional) | `fdesk ... dock add-tile --link-type URL --url ... --title <label> --icon chart-line` |
| always last | `fdesk --app <app> lint --only L07,L10,L13` |
The dock icon `chart-line` is a hardcoded placeholder default, not a validated icon: run `fdesk icons check` (frappe-desk skill) and edit the printed command if it does not resolve.

Spec input (`examples/ury_overview.yaml` shape): `desk: {workspace: URY, label: Sales Analytics}`; optional `sidebar: {section: Dashboards}`, `dock: true`. Example output for `desk --dashboard abc123 --app apps/ury` with sidebar+dock set (illustrative values):
```
fdesk --app apps/ury --write workspace add-shortcut URY --type URL --url /insights/dashboards/abc123 --label 'Sales Analytics'
fdesk --app apps/ury --write sidebar add-item URY --section Dashboards --link-type URL --url /insights/dashboards/abc123 --label 'Sales Analytics'
fdesk --app apps/ury --write dock add-tile --link-type URL --url /insights/dashboards/abc123 --title 'Sales Analytics' --icon chart-line
fdesk --app apps/ury lint --only L07,L10,L13
```
You run these yourself, after reviewing.

### Underlying `fdesk` flags (copied from `fdesk <group> <cmd> -h`)
| Command | Flags |
|---|---|
| `fdesk workspace add-shortcut <workspace>` | `--type {DocType,Report,Page,Dashboard,URL}` `--url` `--label` (required) `[--to --doc-view --color --stats-filter --col --after]` |
| `fdesk sidebar add-item <sidebar>` | `--link-type {DocType,Page,Report,Workspace,Dashboard,URL}` (required) `[--section --to --url --label --icon --after --open-in-new-tab --hidden ...]` |
| `fdesk dock add-tile` | `--link-type {Sidebar,Workspace,URL}` `--title` `--icon` (required) `[--to --url --after]` |
Global flags go BEFORE the group: `fdesk --app . --write workspace ...`; dry-run unless `--write`. Shortcut row produced: `{"type": "URL", "label": "Sales Dashboard", "url": "/insights/dashboards/<name>"}`.
Use a relative URL; one URL item per surface; DocType shortcuts need no URL. Verify with `fdesk lint --only L07,L10,L13`.

### Why not iframe (do not build it)
- Nginx sends `X-Frame-Options "SAMEORIGIN"` and Insights adds `frame-ancestors 'self' <allowed_origins>` only on `/insights/{public,shared}` paths and only if Insights Settings `allowed_origins` is set (from Insights source: insights/api/dashboards.py and shared-route handling; nginx header verified live, 2026-11-02).
- A private `/insights/dashboards/<name>` has no CSP header logic of its own; a same-origin frame would use the viewer's session, but the app SPA is a full page (header, routing) and it was not proven to work.
- The only embeddable form is `/insights/shared/dashboard/<name>`, which needs `is_public: 1` (see `references/permissions.md`: never for financial data). It was never confirmed that an `<iframe>` survives Custom HTML Block sanitising.
Decision: Tier A = link. Do not publish a dashboard just to iframe it.

## 2. Tier B: forward-only (Frappe develop + Insights develop)
Mechanism (verified in upstream code, not runnable here): Frappe UI islands, a Vue component behind a shadow root registered via assets.json (`frappe/utils/island.py`, `ui/island/`). Insights develop `insights/desk.py` `DESK_ISLANDS` creates Custom Fields that link desk docs to Insights content; when the link is set the doc's `onload` sets `__onload.island` and desk renders the island instead of its own definition (from Insights develop source: insights/desk.py):
| Desk doctype | Field (Link) | Island | Notes |
|---|---|---|---|
| `Dashboard` | `insights_dashboard` -> `Insights Dashboard v3` | `insights.dashboard` | folder `{module}_dashboard` |
| `Dashboard Chart` | `insights_chart` -> `Insights Chart v3` | `insights.chart` | |
| `Number Card` | `insights_chart` -> `Insights Chart v3` | `insights.chart` | props `{card: false}` |
Link (not Data) so standard sync and rename work and a claimed member cannot be deleted.

**Note**: "Insights chart as a desk Number Card: no native path" is true only for 3.15.1 (it uses `Custom` + a method you write). On Tier B it is native via `Number Card.insights_chart`. Never promise it on Tier A.

Shipping on Tier B (ERPNext/HRMS develop pattern): standard docs `<module>/insights_workbook/<slug>/<slug>.json` (Insights Workbook, `is_standard=1`, `module`) plus `<module>/<module>_dashboard/<slug>/<slug>.json`, a desk `Dashboard` carrying `insights_dashboard`. Literal shape (from ERPNext develop, stock dashboard json, trimmed):
```json
{"cards": [{"card": "Total Active Items"}], "charts": [{"chart": "Warehouse wise Stock Value", "width": "Full"}],
 "dashboard_name": "Stock", "doctype": "Dashboard", "insights_dashboard": "stock", "is_default": 1, "is_standard": 1, "module": "Stock", "name": "Stock"}
```
`finsights desk --tier island` prints that doc for a spec; `finsights compile --format standard` emits the workbook (golden `tests/golden/ury_overview.standard.json`). Output: `{"doctype":"Dashboard","name":"Sales Analytics","dashboard_name":"Sales Analytics","docstatus":0,"module":"URY","is_standard":1,"insights_dashboard":"ury-sales-overview"}`.
Templates: study the ERPNext (selling, buying, stock) and HRMS develop-branch workbooks for layout: KPI row + trend + breakdown + colour-coded table + global filters.

Develop-only features shown in the keynote and workbook keys (NOT in 3.15.1; do not write them for Tier A): targets and last-year comparison on number cards (object-valued `comparison`), `number_format`, `window`, `layouts.sm`, `default_user_key`, `visible_to_roles`, string `default_operator`, global filters across all charts, click-to-drill-down (view records / break down by dimension, backtrack, jump to doc), in-progress "ask the dashboard" agent respecting user permissions.

## Unverified
1. Tier B was read from upstream sources only; no develop bench exists, nothing run live.
2. Whether `fdesk lint L10` accepts a relative `/insights/...` URL, and whether the desk opens it same-tab: not executed. Tier A shortcut/sidebar/dock live render not yet proven.
3. Frame behaviour of a private dashboard and `<iframe>` survival in Custom HTML Block.
4. Develop's Frappe-side Dashboard field definition and island renderer: not read.
