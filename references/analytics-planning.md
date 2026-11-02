# Analytics planning (do this BEFORE writing a spec)

An Insights dashboard built without a plan shows numbers nobody can trust. Fill `templates/plan_template.md` first; build only when every step below has a written answer.

## Procedure (in order)
1. **Question**: one sentence, owned by a named person, ending in a decision ("which branch adds a cashier on Fridays?"). No decision, no chart.
2. **Audience and decision**: who opens it, how often, what they do differently. Decides density and filters.
3. **Metric definition**: formula, numerator/denominator, amount field (`grand_total` incl. tax vs `net_total` excl.), which statuses count. Write it as `name = SUM(field) / COUNT(...)`.
4. **Grain**: what one row of the source is (invoice, invoice line, KOT, plan line). Metric grain must equal query grain or be a clean roll-up. `P §1.3`
5. **Dimensions**: only those that answer the question; each must sit on the grain row or be reachable by 1:1 / N:1 join.
6. **Source table and fields**: header table for header metrics, child table for line metrics. Verify every column with `finsights catalog` (live snapshot), not memory; custom fields appear in the catalog, not in doctype JSON. `P §1.5`
7. **Filters and docstatus rules**: bounded date range, `docstatus=1` (cancelled = 2, draft = 0), `is_return` decision (net or exclude, say which), branch/company scope. Returns carry negative totals.
8. **Chart choice**: apply `references/charts.md`. Rule of thumb: trend = Line; ranking = Row/Bar sorted desc, limit 10-15; part-of-whole <=5 slices = Donut; one KPI = Number; two categorical axes = Table with rows+columns; stages = Funnel; exact values/drill = Table.
9. **Layout on the 20-column grid**: filters on row 0 (w4 h1 each); KPI Number row full width (w20 h3); trends w12-w14 h9 beside a ranking w6-w8 h9; at most 8 charts; every title states its question; default date filter last 7 or 30 days.
10. **Permissions**: who may read which tables. Insights queries run as SQL against the Site DB; Frappe User Permissions (e.g. on Branch) are not guaranteed to apply, so scope with separate dashboards or query filters and see `references/permissions.md`. Keep staff-level and payment data off shared screens. `P §1.8`
11. **Validation plan**: write the raw SQL and its expected result BEFORE building (recipe below).
12. **Spec file**: translate the plan into a finsights YAML (mapping below), then `finsights lint`, `plan`, `apply`, `verify`.

## Stop and ask the user when
- [ ] No decision/owner can be named for the question.
- [ ] Metric is ambiguous (net vs gross, returns in or out, which statuses).
- [ ] Two sources could give the figure (POS Invoice vs Sales Invoice: POS invoices later become "Consolidated" into Sales Invoices; query ONE source). `P §2`
- [ ] A needed column is missing from `finsights catalog` or is text-typed (Data/varchar holding numbers: cast or ask).
- [ ] Audience includes people who must not see all branches/staff data.
- [ ] Demo/live data for the chart is empty (see readiness check).
- [ ] Question needs >8 charts or >1 grain on one dashboard: split into two dashboards.

## Anti-patterns
| Anti-pattern | Why it breaks | Do instead |
|---|---|---|
| Mixing grains (SUM header `grand_total` after joining items) | each invoice repeats per line | line metrics from item `net_amount`; header metrics on a header-only query |
| Averaging averages | wrong weight per branch | SUM(total)/COUNT at target grain |
| No `docstatus` filter | counts Draft and Cancelled | `docstatus = 1` |
| Filtering `status = Paid` on POS Invoice | 66% of demo rows are status Consolidated | filter `docstatus=1` only |
| Returns ignored | negative totals silently net | decide and write it down |
| Unbounded date range | all-time numbers, full scans | bounded default filter |
| Joining header + items + payments | split payments multiply amounts | aggregate each child separately, join pre-aggregated |
| Payments without `parenttype='POS Invoice'` | child doctype shared with Sales Invoice | always filter parenttype |
| Donut with >5 slices | unreadable | Row sorted desc, keep Other |
| Text numbers (`no_of_pax`, KOT `quantity`, `production_time` are Data/varchar) | wrong sums/sorting | cast in a mutate or choose a typed field |
| Free-text dimension | spelling variants split groups | normalise first or avoid |
| Charting before checking data exists | empty chart promised | readiness check |

## Lessons proven live
- **KPI Number shows the LATEST period when it has a date column**: Number with `date_column`+comparison showed Oct 21 only (19 invoices, 18,005; -25.24% vs prior) while Number without a date shows the TOTAL. If the user wants a window total, omit the date column or use a separate query.
- **Row-level queries + chart-side aggregation**: keep the query at row grain (select columns, only the `docstatus` filter) and let each chart aggregate (`summarize` in chart config). Dashboard filters attach to query columns, so a query already summed by day cannot be filtered by branch or hour. `ury_sales_proof.json`
- **Demo-data readiness**: before promising a chart, count rows for the fields it needs (`SELECT COUNT(*), COUNT(field) ...`). Demo data has one branch, empty returns/loyalty/line discounts/committed_qty, and KOT Execution has few rows with both timestamps.

## Validation recipe (the proof pattern)
1. Run read-only SQL on the bench DB for the exact window and filters; save as. Example result: `3165  3748846.48  (invoices, sum grand_total, docstatus=1)`.
2. Open the dashboard with filters set to that window; read the chart value.
3. Totals must match to the cent; then change the date filter (e.g. last 1 month) and compare to SQL again (`440 / 618,750.00` matched).
4. Also compare row counts to catch join fan-out; reconcile payments to `grand_total`, not to payment rows (change returned lowers cash).
5. `finsights verify` runs every query server-side; it does not replace step 1-3.

## Plan to finsights spec (field mapping)
| Plan field | Spec key |
|---|---|
| Source table | `queries[].table` (DocType name) |
| Docstatus / returns / scope filters | `queries[].filters: [{column, op, value}]` |
| Columns needed by charts and filters | `queries[].select` (`{column, optional: true}` for fields absent on some sites) |
| Grain roll-up | `queries[].summarize` only when no chart needs finer rows |
| Metric | `charts[].y: [{column, agg, as, format}]` |
| Dimension | `charts[].x` (`{column, granularity}` for dates) |
| Chart type | `charts[].type` |
| KPI period | `charts[].date` (+ `comparison`, `sparkline`) |
| Top-N | `charts[].order` + `limit` |
| Dashboard filter | `dashboards[].filters[].applies_to: {query: column}` |
| Layout | `dashboards[].items: [{chart, x, y, w, h}]` |
| Desk placement | `desk: {workspace, label}` |
Full example: `examples/ury_overview.yaml`.

## URY examples (3 short; catalogue in `references/ury-analytics.md`)
- **Daily sales by branch**: POS Invoice, grain invoice, SUM(`net_total`), docstatus=1, is_return handled; Line by `posting_date`, series branch. Avg spend per cover only for Dine In (`no_of_pax` empty otherwise).
- **Top items**: POS Invoice Item, grain line, SUM(`net_amount`) and SUM(`qty`) ranked separately; Row limit 15; never use parent `grand_total` after the join.
- **Payment mix**: Sales Invoice Payment, grain payment row, filter `parenttype='POS Invoice'` and parent docstatus=1; Donut if <=5 modes.

## Unverified
- Insights row-level permission behaviour for branch managers (see `permissions.md`).
- `posting_time` is site-local; `total_spend_time`/`production_time` text formats unparsed.
- `charts.md` chart-type rule of thumb is taken from source reading, not re-tested per type live.
