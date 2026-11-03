# Gotchas

Each trap: symptom, cause, fix, proof. Only evidence-backed traps are listed; `(unverified)` marks anything not proven live. Evidence notes: 'verified live' means checked against a running Frappe 16.54.0 + Insights 3.15.1 site on 2026-11-02; 'demo data' counts come from a URY demo site and are examples, not universal facts.

## 1. Authoring

1. **Number chart shows one period, not the total.** Cause: with `date_column` (+`comparison`) the card shows the LATEST period; without it, the TOTAL. Live: Oct 21 = 19 invoices, 18,005, -25.24% vs 24,085, equal to SQL. Fix: omit `date_column` for a grand total; add it only for "latest + delta". Owner: [charts.md](charts.md). Proof: verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02.
2. **Develop-only template keys do nothing (or break).** Cause: `number_format`, `window`, object `comparison`, `layouts.sm`, `default_user_key` exist only on Insights develop. Fix: do not emit them on 3.15.1. Effect of unknown keys is (unverified). Owner: [charts.md](charts.md), [dashboards-filters.md](dashboards-filters.md). Proof: from Insights source: frontend/src2 chart type enum.
3. **Overlapping or missing filter flashes on a freshly REST-written dashboard.** Cause: transient render for a few seconds after load; layout was valid. Fix: wait, reload, then judge; run `finsights lint` (layout overlap check) before blaming the data. Owner: [dashboards-filters.md](dashboards-filters.md). Proof: verified live, 2026-11-02 (observation, not isolated; mechanism unverified).
4. **Wrong pivot or chart type.** There is no Pie/KPI/Pivot enum: Pie = Donut, KPI = Number, Pivot = Table with `columns`. Owner: [charts.md](charts.md). Proof: from Insights source: frontend/src2 chart type enum.

## 2. Apply and idempotency

5. **`import_workbook` creates duplicates.** Cause: it always inserts a NEW workbook (no upsert); names become server hash ids. Fix: never use it to re-apply; use `finsights apply` (REST, lookup by `from_template == "finsights:<id>"`, then title). Owner: [api-shipping.md](api-shipping.md). Proof: from Insights source: insights/doctype/insights_workbook/insights_workbook.py:515-529.
6. **Spec ids are not docnames.** Cause: live names are counters (workbook) or hashes (query/chart/dashboard). Fix: resolve by marker/title, never assume `name == id`. Owner: [model.md](model.md). Proof: verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02.
7. **Rename = create plus orphan.** Fix: pass `--rename <id>=<old title>`; orphans are kept unless `--prune`. Owner: [api-shipping.md](api-shipping.md). Proof: see queries.md.
8. **Chart helper queries look like orphans.** Cause: each chart auto-creates an empty `data_query` helper (null title, no operations). Fix: plan_apply excludes them; do not delete them by hand. Owner: [model.md](model.md). Proof: `tests/test_apply_orphans.py`.
9. **`from_template` marker is safe.** `from_template` is writable; migrate's `sync_workbook_template_updates` ignores unknown markers. Do not set it to a real app template name (exit 3 without `--adopt`). Owner: [api-shipping.md](api-shipping.md). Proof: from Insights source: insights/api/templates.py:417-440.
10. **Never write `data_query` or `linked_charts`.** Cause: server auto-creates the first empty and fills the second from dashboard `items`. API-created charts render with no editor. `--materialize-data-query` is not needed. Owner: [charts.md](charts.md). Proof: verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02.

33. **Unmarked same-title workbook is refused, and `--prune` is blocked right after `--adopt`.** Symptom: `apply` exits 3 "a workbook titled ... exists that finsights did not create". Cause: the workbook has no `finsights:<id>` marker; taking it over by title could rewrite someone's work, and `--prune` deletes every child not in the spec, so adopt plus prune in one run would delete content before you saw `plan`. Fix: `apply --write --adopt`, read `plan`, then `apply --write --prune`. New workbooks get the marker in the create request. Owner: [api-shipping.md](api-shipping.md). Proof: (covered by tests: `tests/test_apply_safety.py`).
34. **A failed `apply` leaves part of the workbook written.** Cause: REST writes are one request per doc, not a transaction. The lines done so far are printed to stderr. Fix: fix the cause (HTTP errors show the server `exc_type` and message) and re-run `apply --write`; it matches by marker and title and continues. Owner: [api-shipping.md](api-shipping.md). Proof: (covered by tests: `tests/test_apply.py`).

## 3. Queries

11. **`run_doc_method` TypeError / operations None.** Cause: it needs the FULL query doc from GET, not `{doctype,name}`. Fix: GET the doc, pass it as `docs`. Owner: [queries.md](queries.md), [api-shipping.md](api-shipping.md). Proof: verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02.
12. **`time_diff` returns 0 / wrong.** Cause: casts both sides to TIME, dropping date and fractions (wrong across midnight, 0 for ms gaps); unit `millisecond` errors on MariaDB. Sign (a-b vs b-a) is (unverified). Fix: use native SQL `TIMESTAMPDIFF(SECOND, ...)` for kitchen durations. Owner: [queries.md](queries.md). Proof: verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02.
13. **`hour()` on a Time column.** Works: `hour(KOT.time)` confirmed live despite non-padded storage. Owner: [queries.md](queries.md). Proof: demo-data check, 2026-11-02.
14. **Joined docstatus collision.** After a join two `docstatus` columns exist and finsights cannot qualify columns by table, so a filter hits ambiguously. Fix: filter child `docstatus = 1` (mirrors parent) and do not filter parent `docstatus` on joined queries; never select the joined `docstatus`. Owner: [queries.md](queries.md). Proof: `examples/ury_overview.yaml` header.
15. **`window_operation`, `remove`, `pivot_wider`, `custom_operation`.** Read from code/types only; `window_operation` appears ignored. (unverified). Owner: [queries.md](queries.md).

35. **Hour of day, ratio or cast gives wrong or empty values.** Use the recipes in [expressions.md](expressions.md) instead of improvising; remember `time_diff` (gotcha 12) and text-typed numbers (gotcha 24). Owner: [expressions.md](expressions.md). Proof: see that file's evidence notes.

## 4. Charts

16. **Map, Bubble, Sankey, Table `conditional_formatting`.** Never rendered live; configs from types only. (unverified). Owner: [charts.md](charts.md).

36. **`yes`/`no`/`on`/`off` and `08` in a spec are strings, not booleans or numbers.** Symptom: a filter value `no` or `08` compiles as the string, or an expected boolean is not one. Cause: the built-in YAML parser accepts only `true`/`false` as booleans and plain decimals as numbers (PyYAML is not used, so every machine agrees). Fix: write `true`/`false`, and quote values that must stay strings (`"08"`, `"no"`). Owner: [spec-format.md](spec-format.md). Proof: (covered by tests: `tests/test_yamlish.py`).
37. **`share:` in a spec does nothing.** Cause: accepted by the grammar, never applied; lint L26 warns. Fix: grant access in the Insights UI or via `insights.api.workbooks.update_share_permissions` ([permissions.md](permissions.md)). Proof: from finsights code (no code path applies it).
38. **Join column collision.** A join `columns` entry with the same name as a base column lands in the same output name (lint L28). Fix: drop it from that join's `columns`, or use a `source_query` with a mutate alias. Owner: [queries.md](queries.md). Proof: (covered by tests: `tests/test_lint_extra.py`).

## 5. Dashboards and filters

17. **Filter link silently dropped on import.** Cause: link dropped when its chart/query is not in the file, value empty, or no ".". On REST write, no validation at all. Fix: GET the dashboard after write and check each `links` entry. Owner: [dashboards-filters.md](dashboards-filters.md). Proof: (from Insights source: insights/doctype/insights_workbook/insights_workbook.py:144-174).
18. **Grid.** 20 columns; `x+w > 20` is corrected at render, not rejected. Owner: [dashboards-filters.md](dashboards-filters.md). Proof: "Overlap rules" (from Insights source: frontend/src2 common.ts:126-145).
19. **Number/non-`within` Date filter defaults.** No fixture, not run. (unverified). Owner: [dashboards-filters.md](dashboards-filters.md).
39. **Dead filter link is now caught by `verify`.** Cause: gotcha 17 (links dropped or wrong on write). `verify` rule V05 checks each link: chart is on the dashboard, format is `` `query`.`column` ``, the query is the chart's query, the column is in the query result; a filter with no links is a warning. Fix: correct the `applies_to` mapping in the spec and re-apply. Owner: [dashboards-filters.md](dashboards-filters.md). Proof: (covered by tests: `tests/test_verify_links.py`); not yet run against a live site.

## 6. Desk

20. **No UI islands on Frappe v16.** Cause: v16.54.0 and tag v16.50.0 contain 0 island paths; develop (`17.0.0-dev`) has 78; Insights develop is 3.50.0-dev. Fix: Tier A only (URL shortcut/sidebar/dock to `/insights/dashboards/<name>`); do not add `Dashboard.insights_dashboard`. Owner: [desk-integration.md](desk-integration.md). Proof: file listing of Frappe v16.54.0 vs develop, 2026-11-02.
21. **Iframing Insights fails.** `X-Frame-Options: SAMEORIGIN`; shared pages need `is_public`. Owner: [desk-integration.md](desk-integration.md). Proof: (iframe rejection; not re-tested here). Tier A shortcut live render is (unverified).

40. **`ship` writes nothing when hooks.py points elsewhere.** Symptom: `ship` exits 3 "hooks.py already declares insights_workbooks = ...". Cause: the app already declares a different templates directory, and writing into `workbook_templates` would never load. Fix: re-run with `--templates-dir <that directory>` (single directory name), or change the hook. A computed hook value also exits 3. Owner: [api-shipping.md](api-shipping.md). Proof: (covered by tests: `tests/test_ship_hook.py`).

## 7. URY data

22. **Revenue short by ~2/3.** Cause: filtering POS Invoice `status='Paid'`; about two thirds are `Consolidated` (demo data example: 2102 of 3177). Fix: `docstatus=1`, `is_return=0`. Owner: [ury-analytics.md](ury-analytics.md). Proof: demo-data check, 2026-11-02.
23. **Payments doubled/mixed.** Sales Invoice Payment holds POS (3278) and Sales Invoice (1778) rows. Fix: filter `parenttype='POS Invoice'`. Owner: [ury-analytics.md](ury-analytics.md). Proof: demo-data check, 2026-11-02.
24. **`no_of_pax` sums as text.** Data/varchar on POS Invoice; cast to int. Owner: [ury-analytics.md](ury-analytics.md). Proof: demo-data check, 2026-11-02.
25. **Branch empty on cash-up.** POS Closing Entry `branch` is null on 101/101; use `pos_opening_entry`. Owner: [ury-analytics.md](ury-analytics.md). Proof: demo-data check, 2026-11-02.
26. **Flat/empty charts on demo.** Returns, loyalty, line discounts, terminal transactions, `committed_qty` are empty; demo has one branch. Build anyway; expect zeros. Owner: [ury-analytics.md](ury-analytics.md). Proof: demo-data check, 2026-11-02.
27. **Dine-in count.** 995 submitted, not 1,045; pair `order_type='Dine In'` with `docstatus=1`. Not re-queried. Owner: [ury-analytics.md](ury-analytics.md). Proof: demo-data check, 2026-11-02.

## 8. Environment and tooling

28. **`get_list` rejects SQL functions in `fields`.** Frappe v16: use plain fields or filters on `/api/resource`. Owner: [api-shipping.md](api-shipping.md). Proof: verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02.
29. **Built-in browser pane cannot register service workers.** The control (existing `/ury/serve` SW) fails identically. Fix: test SW/offline in Playwright Chromium. Proof: verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02.
30. **`pkill -f <pattern>` kills your own shell.** The command line contains the pattern. Fix: match by pid/port, or use `pkill -f '[b]ench'`. Proof: observed during a bench restart.
31. **Test with the right runner.** `uv run --with pytest python -m pytest -q skills/frappe-insights/tests` from the skill repo root. Proof: run from the skill repo root.
32. **v2 doctypes and `api/dashboards.py` top half.** Never use for v3 work. Owner: [v2-v3-traps.md](v2-v3-traps.md).

## 9. One-call API

41. **`build` write refused: confirm token.** Symptom: `error.exit_code` 3 "confirm token required" or "does not match". Cause: prune or title adoption without the token of an identical dry run, or flags/plan changed since. Fix: dry run again, pass `confirm=result["confirm"]`. Owner: [api.md](api.md). Proof: (covered by tests: `tests/test_api.py`).
42. **Healed spec is not saved.** `build`/`heal` return the fixed spec in `result["spec"]`; the file on disk is unchanged. Fix: write it back, else the next run re-heals. Owner: [api.md](api.md). Proof: read from code.
43. **`needs_decision` is never auto-applied.** Unknown table/column/agg and plan conflicts need a choice; apply an alternative's `patch` with `heal.apply_patch`. Owner: [api.md](api.md). Proof: (covered by tests: `tests/test_heal.py`).
44. **`export` is not a spec.** It is the compiled envelope with live names and a `lossy` list; do not feed it to lint or `apply`. Owner: [api.md](api.md). Proof: read from code.
45. **Workbook ref ambiguous (exit 3).** Several workbooks match a title or marker; use the live name. A title is not normalised to a marker. Owner: [api.md](api.md). Proof: (covered by tests: `tests/test_export.py`).
46. **Offline `build` looks like success.** With no `INSIGHTS_*` env it stops after compile with `ok=True` and `plan=None`; nothing was planned or written. Owner: [api.md](api.md). Proof: read from code.

## 10. Number tiles, expressions and renames (verified live 2026-11-03, Frappe 16.54.0 + Insights 3.15.1)

47. **`round(x, 0)` in a measure expression renders a blank tile; use the method form `.round(0)`.** Symptom: a Number chart whose measure is `round(sum(net_total), 0)` shows nothing (`verify` still reports ok because the query runs). Cause: the function form is not evaluated by the Number chart. Fix: `(sum(net_total)).round(0)`; wrap an aggregate in parentheses before the method. Proof: scratch workbook, `round(sum(net_total), 0)` tile blank next to `(sum(net_total)).round(0)` tile showing 42,18,966. Owner: [expressions.md](expressions.md), [queries.md](queries.md).
48. **`--rename` takes the compiled key, not the spec id.** Symptom: `apply --rename db04_unassigned=OLD TITLE` still plans "create" plus two orphans. Cause: `plan_apply` matches the rename against the compiled dependency key (`<workbook-id>-<chart-id>` in kebab case, as printed by `compile`), or the live doc name. Fix: `--rename ury-db04-menu-category-db04-unassigned="OLD TITLE"`. Retitling a chart without it creates a new chart and leaves the old one as an orphan (kept unless `--prune`). Proof: `plan` with both forms against the URY site.
49. **Number tiles render at a fixed tile width: keep them w:4 or w:5.** Symptom: a Number chart placed at w:6 rendered a clipped 160px tile; at w:8 it rendered a 220px tile and left the rest of the box empty; w:4 (about 220px) and w:5 (about 278px) render full tiles and fit titles up to about 26 characters at w:5. Fix: KPI rows of 5 x w:4 or 4 x w:5; if a row would leave a hole, widen a neighbouring non-Number chart (a Row chart) instead of the tile. Proof: DB01, DB04 captures in `proof/ux8*`.
50. **A multi-measure Number chart becomes several fixed-width tiles inside one box.** Symptom: a Number chart with two measures in a w:10 box shows two ~280px tiles flush right with a blank hole on the left. Fix: one Number chart per measure, one tile each (the URY dashboards do this).
51. **Two joins to the same table clash on the joined column.** Joining `User` twice (waiter and cashier names) would give two `full_name` columns. Fix: one join per query in a `source_query` chain, take the name into a mutate column (`coalesce(full_name, waiter)`) and leave `full_name` out of that query's `select`; see `fact_invoices_w` and `fact_invoices_named` in `solutions/ury_restaurant/semantic_layer.yaml`. Proof: DB08 shows full names on the live site.
52. **`format: percent` only formats Number tiles; Row, Bar and Line axes still show the 0-1 fraction.** Symptom: a Row chart titled "(%)" with a ratio measure and `format: percent` has axis ticks 0, 0.2 ... 1 (or 0, 0, 0.1, 0.1 when the values are small). Fix: for non-Number charts write `(100 * sum(a) / sum(b)).round(1)` with no `format`; keep `format: percent` on Number tiles (lint L33 then warns on the chart; that warning is expected). Proof: DB05, DB08, DB10, DB12, DB13, DB14 rechecked live 2026-11-03.
53. **A V07 warning from `verify --deep` on a chart with a filter is usually an empty slice, not a bug.** Symptom: `chart data query returned 0 rows ... although its source query has rows`. Cause: the chart's own filter (for example `ready_by != Administrator`, `difference != 0`) removes every row on this site. Check the doctype first (all ready taps by Administrator; no closing line with a difference), then say so on the dashboard; do not drop the filter to make the check quiet.
54. **Stale "not recorded yet" banners outlive a re-seed.** After seeding more data, grep the dashboard `text` items and the portfolio notes for "not recorded", "no ... yet", "T4"; a banner that says wastage or targets are empty above a chart that shows them is the first thing a reviewer sees. Keep the banner only for what is still missing (estimates, proxies, commission not stored).

## Unverified

- Gotcha 3 mechanism; 12 sign; 15, 16, 19, 21 (Tier A render).
- Permission traps are not listed: nothing in [permissions.md](permissions.md) was exercised with a non-admin user.
- Gotcha 30 fix pattern is standard shell practice, not tried on the bench.
