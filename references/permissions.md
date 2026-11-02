# Permissions and sharing

Cites: `R6` = research/R6_desk_perms.md (S-numbers are its Sources), `SRC` = apps/insights/insights. Nothing here is live-proven unless marked; see Unverified.

## 1. Roles
| Role | Gets | Cite |
|---|---|---|
| `Insights User` | app-switcher tile, default gate of every whitelisted API (`@insights_whitelist`) | `R6` 2.1 S1,S2,S23 |
| `Insights Admin` | admin APIs, full rights on Data Source/Table/Team/Settings | S23,S26 |
Admin = Administrator, OR member of Insights Team named exactly `Admin`, OR Frappe role `System Manager` (`is_admin`, S24). Membership of team `Admin` auto-grants `Insights Admin`; that team cannot be renamed, deleted or emptied (S25).
DocType matrix (S26): Workbook / Query v3 / Dashboard v3 / Chart v3: Insights User has read write create delete share. Data Source v3: User read+share only. Table v3, Team: User read+share. Settings: Admin only (plus System Manager). On Dashboard/Chart v3 `is_public` and `permission_user` are permlevel 1: change them only via `update_access` (S22).

## 2. Settings that turn enforcement on (Insights Settings)
| Field | Effect |
|---|---|
| `enable_permissions` | Team-based access on Data Sources, Tables, Dashboards, Charts. OFF = every Insights User sees all sources/tables (S2). |
| `apply_user_permissions` | Applies Frappe User Permissions to rows (Site DB reads, native SQL) (S27,S28) |
| `allowed_origins` | extra `frame-ancestors` for `/insights/{public,shared}` pages (S13) |
**Insights ignores Frappe User Permissions by default.** Row scoping by Branch/Company needs `apply_user_permissions=1` (or `enable_permissions=1`, S24 `apply_table_restrictions`) or a team `table_restrictions` expression. Never assume a desk user's Branch restriction carries over to a dashboard.

## 3. Teams and Resource Permission
- `Insights Team`: `team_name`, `team_members` (child `Insights Team Member`: `user`), `team_permissions` (child `Insights Resource Permission`: `resource_type`, `resource_name`, `table_restrictions`) (S29).
- `resource_type` must be exactly one of `Insights Data Source v3`, `Insights Table v3`, `Insights Dashboard v3`, `Insights Chart v3`; anything else throws (S25).
- Grant on a Data Source = all its tables; grant on a Table = that table (S2). A user in no team matches no grant (S2). User teams cached 24h, cleared on team change (S24,S25).
- Workbooks, Queries, Alerts are NOT team resources: access = owner + DocShare + workbook linkage (S2).
- Literal child row: `{"resource_type": "Insights Table v3", "resource_name": "...", "table_restrictions": "..."}` (S29).

## 4. Share APIs
| Object | API | Notes |
|---|---|---|
| Workbook | `insights.api.workbooks.update_share_permissions(workbook_name, user_permissions=[{"user":"a@x.com","read":1,"write":0}], organization_access="view")`; `get_share_permissions(workbook_name)` | needs `share` ptype; `organization_access` = `"view"` / `"edit"` / None (removes everyone row); only Insights Users/Admins nameable (S30) |
| Dashboard | doc method `Insights Dashboard v3.update_access(data)`, `data = {"is_public": True, "is_shared_with_organization": False, "people_with_access": ["a@x.com"]}` | creates read-only DocShare rows; `db_set`s `is_public` and `permission_user = session.user`; public publish first runs `check_dashboard_chart_access` (S22) |
| Chart | `Insights Chart v3.update_access(is_public)` | same `permission_user` recording, needs `share` (S31) |
| Team grant | `insights.api.permissions.get_resource_access_info / grant_access / revoke_access(resource_type, resource_name, team)` | caller must be the resource OWNER, else PermissionError; writes with `ignore_permissions=True` (S32) |
Share link in UI: `<origin>/insights/shared/dashboard/<name>` (S33). Write-time guards: a chart may only use a query its author can read; a dashboard only charts its author can read (S2).

## 5. Public / guest flags: never for financial data
- v3 public path = `api/shared.py`; `api/public.py` is v2, do not use (S34,S35). Only Dashboard v3, Chart v3, Query v3 can be public; a query is public only via a public chart; a chart if `is_public=1` or on a public dashboard (S35).
- Public runs filter rows as the recorded `permission_user` (the publisher), NOT as the viewer: a publisher with full access exposes full-access rows to anyone with the link (S22,S38).
- Guests may only call allow-listed methods (`Insights Query v3.execute`, `download_results`, `Insights Dashboard v3.get_distinct_column_values`, `track_view`) and extra args are dropped (S37).
RULE: never set `is_public` on a dashboard/chart with sales, payments, cash, margin or payroll data, and never to make an iframe work (see desk-integration.md). Share by DocShare or team grant. If a public link is explicitly requested, publish from a restricted user (so `permission_user` is limited) and say so.

## 6. How a non-admin's table access is enforced (code order, S37,S24,S40,S28,S41)
1. `has_role("Insights User")` gate on the HTTP API.
2. `run_doc_method` -> `check_stored_document`: `frappe.has_permission(read)` on the stored query (rules: owner, workbook access, DocShare, linked readable chart).
3. Every table read -> `InsightsTablev3.get_ibis_table` -> `check_table_permission(data_source, table, user=permission_user)`: no-op if `enable_permissions` off or admin; else table must be in the user's allowed set, else `PermissionError "You do not have permission to access this table"`.
4. `apply_table_restrictions`: team `table_restrictions` strings run as ibis filter expressions; then Frappe user permissions if `apply_user_permissions`/`enable_permissions`.
5. Native SQL: table names are extracted and re-bound through the same checked path when either setting is on.
6. `api/data_sources.py` helpers check source/table permission before returning metadata/columns/values.
Consequence: with both settings OFF (default), any Insights User can query any table of any source; the only boundary is who has the role.

## 7. Cheat-sheet
| Goal | Do |
|---|---|
| User sees Insights | role `Insights User` |
| Make admin | add to Team `Admin` (or System Manager) |
| Read-only workbook for one user | `update_share_permissions` `{user, read:1, write:0}` |
| Dashboard to whole org | `update_access` `is_shared_with_organization: true` |
| Limit team to a table subset | Team row `Insights Table v3` + `table_restrictions`; `enable_permissions=1` |
| Branch-scoped dashboards | `apply_user_permissions=1` and verify with a restricted user |

## Unverified
1. None of section 2-6 exercised live with a non-admin user (SWARM_STATE: "permissions allowed/denied" still to prove).
2. `grant_access.resource_name` format for tables (likely `<data_source>.<table>`) and the `table_restrictions` expression syntax/context (`R6` Unknown 4).
3. Semantics of `allow_download`, `disable_user_lookup`, `max_execution_time`, `query_result_limit`, `max_memory_usage` (`R6` Unknown 5).
4. Whether non-owner admins can grant/revoke (R6 Unknown 9); whether `apply_user_permissions` alone is enough for Branch scoping on Site DB queries.
5. "Publish from a restricted user" mitigation is inferred from `permission_user` semantics, not tested.
