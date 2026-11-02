# Insights v3 data model (3.15.1)

Only the doctypes below exist for you. Names are exact. `Insights Workbook`, `Insights Folder`, `Insights Team` carry NO `_v3`; Query/Chart/Dashboard/Data Source/Table carry it. See [v2-v3-traps.md](v2-v3-traps.md) for the v2 look-alikes.

## Doctype map

| Doctype | Key fields | JSON fields | Links | Naming |
|---|---|---|---|---|
| Insights Workbook | title, from_template, imported_version, imported_checksum | `data_backup` (written on trash) | root object | counter: "1","2",... (`getseries`); live-proven `R1 s1`, `insights_workbook.py:45-48` |
| Insights Folder | title, type (`query`/`chart`), sort_order | - | `workbook` | default hash `R1 s1` |
| Insights Query v3 | title, is_builder_query, is_native_query, is_script_query, use_live_connection, sort_order | `operations` | `workbook`; `folder` is plain Data (a Folder name, unchecked) | server hash `wpd_interfaces F1`; no autoname in json, so an explicit name is possible (`SWARM_STATE R2 gate`) |
| Insights Chart v3 | title, chart_type (Data, not Select), is_public, sort_order | `config` | `query`, `data_query` (derived), `workbook`, `permission_user` | server hash `wpd_interfaces F1` |
| Insights Dashboard v3 | title, is_public, share_link, vertical_compact_layout | `items` | `workbook`, `permission_user`, `linked_charts` (child, derived) | server hash `wpd_interfaces F1` |
| Insights Dashboard Chart v3 | chart | - | `chart` -> Chart v3 | child table of Dashboard v3 `insights_dashboard_chart_v3.json:25` |
| Insights Data Source v3 | title, status, database_type, is_site_db, type (Database/REST API) | key/header blobs | none | `scrub(title)`; site DB is `site_db` `insights_data_source_v3.py:49-50` |
| Insights Table v3 | table, label, data_source, last_synced_on, stored, sync_mode | `before_import_script` | `data_source` | `md5(data_source+table)[:10]`, NOT the table name; real name in field `table` (e.g. `tabPOS Invoice`) `insights_table_v3.py:199-200` |
| Insights Table Link v3 | left_table, right_table, left_column, right_column | - | `data_source` | autoincrement; standalone, not a child (v2 was) `insights_table_link_v3.json:3` |
| Insights Team | team_name | - | `team_members`, `team_permissions` | `field:team_name` `insights_team.json:4` |
| Insights Team Member / Resource Permission | user / resource_type, resource_name | - | child tables of Team | autoincrement |

Team grants accept only Data Source v3, Table v3, Dashboard v3, Chart v3; Workbook and Query v3 are not grant targets `insights_team.py:19-24` (detail in [permissions.md](permissions.md)).

## Live-proven REST behaviour (bench, Insights 3.15.1)

| Fact | Consequence |
|---|---|
| `POST /api/resource/<Doctype>` creates Workbook, Query v3, Chart v3, Dashboard v3 | no import step needed; `wpd_interfaces F1` |
| Workbook name is a counter; children get server hash names | resolve live names after create, never predict them; `wpd_interfaces F1` |
| `operations`, `config`, `items` accept a list/dict or a JSON string on write; GET returns JSON STRINGS (pretty-printed) | `json.loads` before diffing; `wpd_interfaces F1` |
| Children are matched by (workbook, title) | stable titles are the idempotency key; `wpd_interfaces F4` |
| `from_template` is writable (marker `finsights:<id>`, PUT after create); migrate ignores unknown markers | usable as a workbook lookup key; `wpd_interfaces F3` |
| `import_workbook` always inserts a NEW workbook (no upsert) | do not use it for re-apply; `R1 hand-off`, `insights_workbook.py:515-529` |
| Dashboard URL is `/insights/dashboards/<live name>` | `wpd_interfaces F7` |

## Server-maintained fields (never write)

| Field | Behaviour |
|---|---|
| Chart `data_query` | creating a chart auto-creates an EMPTY derived Query v3; the browser builds the real one at render. Charts without it render fine on a dashboard (proof/01_dashboard.jpg). `wpd_interfaces F2`, `SWARM_STATE live proof #1` |
| Dashboard `linked_charts` | rebuilt in `before_validate` from every `items[]` entry with `type == "chart"` `insights_dashboard_v3.py:126-134` |
| Dashboard text items | HTML-sanitized in `before_validate` `insights_dashboard_v3.py:46-51` |
| Workbook trash | cascades by `workbook` filter to Query v3, Chart v3, Dashboard v3, Folder (code, not a link rule); backup kept in `data_backup` `insights_workbook.py:53-64` |

Where cascade matters: deleting a workbook deletes everything in it. Never delete as a "reset" on a workbook holding user-built content.

## ER sketch

```
Insights Workbook ("1","2",...)
 |-- Insights Folder            (workbook; type query|chart)
 |-- Insights Query v3          (workbook; folder=Folder name as Data)
 |     `-- data source implicit in operations[0] (source table.data_source); other queries via table.type=="query"
 |-- Insights Chart v3          (workbook; query -> Query v3; data_query -> derived Query v3)
 `-- Insights Dashboard v3      (workbook)
       |-- items JSON  [{type:"chart", chart:<Chart live name>, layout}, {type:"filter", links:{<chart live name>:"`<query live name>`.`<col>`"}}, {type:"text"}]
       `-- linked_charts (derived) -> Dashboard Chart v3 -> Chart v3
Insights Data Source v3 --1:N-- Insights Table v3 ; Insights Table Link v3 (data_source, left/right table+column)
Insights Team --1:N-- Team Member(user), Resource Permission(resource_type in DS/Table/Dashboard/Chart v3, resource_name)
```
Filter `links` use the chart LIVE name as key and a backticked `` `<query live name>`.`<column>` `` as value `wpd_interfaces F4`. Data Source/Table/Team have no `workbook` field `R1 s1`.

## Shipping order and name remapping

`restore_workbook_contents` imports folders, then queries (topologically by query references), then charts, then dashboards; file-local ids (e.g. `tq-sales-invoices`) are remapped through an `id_map` to fresh docnames `insights_workbook.py:94-159`. Your own apply must write in the same order and rewrite chart/query names in `items` before writing.

Export envelope keys: `dependencies` (charts, dashboards, folders, queries), `doc`, `name`, `type`, `version` `workbook_templates/sales/workbook.json:2-4`. Payload shapes: see [queries.md](queries.md), [charts.md](charts.md), [dashboards-filters.md](dashboards-filters.md); real payloads in `proof/ury_sales_proof.json`.

## Unverified

- Folder autoname is inferred as hash (no `autoname` in json), not observed live; Query/Chart/Dashboard hash confirmed live only.
- Query v3 `folder` with a stale Folder name: behaviour untested.
- How a template's `data_source: "Site DB"` display string maps to `site_db`: not traced here (see queries.md).
- `Insights Query Variable` child fields not read.
