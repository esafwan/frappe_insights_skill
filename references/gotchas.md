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

## 3. Queries

11. **`run_doc_method` TypeError / operations None.** Cause: it needs the FULL query doc from GET, not `{doctype,name}`. Fix: GET the doc, pass it as `docs`. Owner: [queries.md](queries.md), [api-shipping.md](api-shipping.md). Proof: verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02.
12. **`time_diff` returns 0 / wrong.** Cause: casts both sides to TIME, dropping date and fractions (wrong across midnight, 0 for ms gaps); unit `millisecond` errors on MariaDB. Sign (a-b vs b-a) is (unverified). Fix: use native SQL `TIMESTAMPDIFF(SECOND, ...)` for kitchen durations. Owner: [queries.md](queries.md). Proof: verified live, Frappe 16.54.0 + Insights 3.15.1, 2026-11-02.
13. **`hour()` on a Time column.** Works: `hour(KOT.time)` confirmed live despite non-padded storage. Owner: [queries.md](queries.md). Proof: demo-data check, 2026-11-02.
14. **Joined docstatus collision.** After a join two `docstatus` columns exist and finsights cannot qualify columns by table, so a filter hits ambiguously. Fix: filter child `docstatus = 1` (mirrors parent) and do not filter parent `docstatus` on joined queries; never select the joined `docstatus`. Owner: [queries.md](queries.md). Proof: `examples/ury_overview.yaml` header.
15. **`window_operation`, `remove`, `pivot_wider`, `custom_operation`.** Read from code/types only; `window_operation` appears ignored. (unverified). Owner: [queries.md](queries.md).

## 4. Charts

16. **Map, Bubble, Sankey, Table `conditional_formatting`.** Never rendered live; configs from types only. (unverified). Owner: [charts.md](charts.md).

## 5. Dashboards and filters

17. **Filter link silently dropped on import.** Cause: link dropped when its chart/query is not in the file, value empty, or no ".". On REST write, no validation at all. Fix: GET the dashboard after write and check each `links` entry. Owner: [dashboards-filters.md](dashboards-filters.md). Proof: (from Insights source: insights/doctype/insights_workbook/insights_workbook.py:144-174).
18. **Grid.** 20 columns; `x+w > 20` is corrected at render, not rejected. Owner: [dashboards-filters.md](dashboards-filters.md). Proof: "Overlap rules" (from Insights source: frontend/src2 common.ts:126-145).
19. **Number/non-`within` Date filter defaults.** No fixture, not run. (unverified). Owner: [dashboards-filters.md](dashboards-filters.md).

## 6. Desk

20. **No UI islands on Frappe v16.** Cause: v16.54.0 and tag v16.50.0 contain 0 island paths; develop (`17.0.0-dev`) has 78; Insights develop is 3.50.0-dev. Fix: Tier A only (URL shortcut/sidebar/dock to `/insights/dashboards/<name>`); do not add `Dashboard.insights_dashboard`. Owner: [desk-integration.md](desk-integration.md). Proof: file listing of Frappe v16.54.0 vs develop, 2026-11-02.
21. **Iframing Insights fails.** `X-Frame-Options: SAMEORIGIN`; shared pages need `is_public`. Owner: [desk-integration.md](desk-integration.md). Proof: (iframe rejection; not re-tested here). Tier A shortcut live render is (unverified).

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

## Unverified

- Gotcha 3 mechanism; 12 sign; 15, 16, 19, 21 (Tier A render).
- Permission traps are not listed: nothing in [permissions.md](permissions.md) was exercised with a non-admin user.
- Gotcha 30 fix pattern is standard shell practice, not tried on the bench.
