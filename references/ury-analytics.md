# URY analytics catalogue

Pick-list of questions an agent can answer for URY (restaurant POS on ERPNext) with Insights v3. Method for choosing and validating: [analytics-planning.md](analytics-planning.md). Working payloads: [../examples/ury_overview.yaml](../examples/ury_overview.yaml), [../examples/ury_menu_performance.yaml](../examples/ury_menu_performance.yaml), [../examples/ury_payments.yaml](../examples/ury_payments.yaml).

Paths: `URY`=apps/ury/ury, `ERP`=apps/erpnext/erpnext on bench sa-v16-latest-demo. Every field below was re-confirmed in doctype JSON (`ERP`/`URY` `doctype/<name>/<name>.json`) or `URY/fixtures/custom_field.json` (CF) on 2026-11-02; none failed. Counts are from the demo site (single branch "URY Branch"), `research/U1_ury_sales_plan.md` s0, `research/U2_ury_ops_plan.md` s1.

## 1. Data traps (read before writing any query)

| Trap | Rule | Why |
|---|---|---|
| POS Invoice status | Filter `docstatus=1` (and `is_return=0`). NEVER filter `status='Paid'` | 3177 submitted: Consolidated 2102, Paid 1075; Paid-only loses ~2/3 of revenue `U1 s3.1` |
| Sales Invoice copies | Never union Sales Invoice with POS Invoice | 1329 SIs are consolidated copies of POS Invoices (merge logs 1326); sum doubles `U1 s0` |
| Payments child | `Sales Invoice Payment`: filter `parenttype='POS Invoice'` (or join parent, `parent=name`) | Same child doctype holds 3278 POS rows + 1778 Sales Invoice rows; Cash 2.35M vs 0.76M `U1 s3.2` |
| Join grain | Never join items and payments in one query; header sums (`net_total`, `no_of_pax`) never after an item join | PI->items ~3.4 lines, PI->payments split tenders `U1 s3.3` |
| `no_of_pax` | Data (varchar) on POS Invoice (Int on Sales Invoice); cast to int; "0" on 2118 rows (non-dine-in) | covers math fails or sums text `U1 s3.7` |
| `net_total` vs `grand_total` | `net_total` excludes tax; reconcile payments to `rounded_total`, not `net_total`; one amount field per dashboard, label it | change_amount>0 on 78 invoices `U1 s3.6` |
| POS Closing Entry `branch` | Null on all 101 rows; take branch via `pos_opening_entry` -> POS Opening Entry.branch (populated) or `pos_profile` | cash-up per branch otherwise empty `U1 s3.11` |
| `item_group` hygiene | Snapshot on invoice line; "All Item Groups" holds 430k/799 lines; Dessert/Desserts, Soups/Soups&Salads duplicates; clean item master or map | category chart wrong `U1 s3.10` |
| URY Order | Doctype has no DB table (query errors); never a source | `U1 s0` |
| KOT time fields | KOT.`time` is Time stored non-padded ("13:07:1.526008"); `hour(KOT.time)` works (live-confirmed); KOT.`production_time`, `quantity`, `cancelled_qty` are Data (cast); `start_time_prep` equals creation time (not a prep start), use KOT Execution stamps | `U2 s0, s3`; hour() live finding supersedes `U2 s5` unknown |
| Kitchen durations | Demo Execution stamps are milliseconds apart; `time_diff` truncates to whole seconds so duration charts read 0; sign of `time_diff` (other-col vs col-other) is unverified. Prefer native SQL `TIMESTAMPDIFF(SECOND, started_at, ready_at)` (midnight-safe, allows P90). `date_diff` casts to date (day+ units only) | `U2 s0` as corrected by caller from live runs; unit support `insights ibis/functions.py` |
| Dine-in count | Dine-in submitted invoices = 995 (1,045 included non-submitted); always pair `order_type='Dine In'` with `docstatus=1` | correction to `U1 s0`/`U2` T2 |
| Single branch | Demo has one branch; keep the branch dimension for production, charts look trivial here | `U1 s3.11` |
| Fields empty in demo | `committed_qty` (0), KOT Items `disposition` (all Pending), Yield Check `department`/`production_unit` (0), Stock Correction `difference_amount` (0), POS Closing difference (0 on all 308), line discounts (0), returns (0), loyalty (0), URY Wastage/Stock Movement/Payment Terminal Transaction (0 rows) | build, expect flat/empty `U1 s0`, `U2 s4` |
| `waiter` | Link to User (not Employee), set on takeaway too; mostly one user (2126). Restrict waiter charts to Dine In; `custom_waiter_employee` only 2275/3275 | `U1 s3.9`, `U2` S1 |
| `total_spend_time`, `arrived_time` | Data; unreliable in demo (1125 are 00:00:00; arrived_time dated 2026-10-21 for old invoices); do not chart dwell | `U2` T3 |
| Cancelled invoices | `docstatus=2` carries `cancel_reason` and `grand_total`; `docstatus=1` filters hide them, include deliberately | `U1 s3.5` |

## 2. Catalogue

Defaults unless stated: POS Invoice (PI) `docstatus=1`, `is_return=0`, bounded `posting_date`. PII = POS Invoice Item (join `parent=PI.name`), SIP = Sales Invoice Payment, PCED = POS Closing Entry Detail, KE = URY KOT Execution. Chart set: Number, Bar, Line, Row, Donut, Funnel, Table, Map, Bubble, Sankey (no heatmap: use Table). Readiness: Y real, P partial, N empty (expect 0). Source ids `S*`=U1, others=U2.

### Sales and menu
| id | Question | Formula | Doctype.fields | Chart | Filters | Ready | Pri |
|---|---|---|---|---|---|---|---|
| S01 | Net sales today/MTD | SUM(net_total) | PI.net_total, posting_date | Number | default | Y | P1 |
| S02 | Revenue by order type over time | SUM(net_total) by day x order_type | PI.order_type (CF) | Line | default | Y | P1 |
| S03 | Order-type share | SUM(net_total) by order_type | PI.order_type | Donut | range | Y | P1 |
| S04 | Avg ticket per order type | SUM(net_total)/COUNT(name) | PI.net_total, order_type | Bar | default | Y | P1 |
| S05 | Orders by hour | COUNT(name) by hour(posting_time) | PI.posting_time | Bar | 8 weeks | Y | P1 |
| S06 | Spend per cover | SUM(net_total)/SUM(CAST(no_of_pax))| PI.no_of_pax (CF Data) | Number/Line | Dine In, pax>0 | P (~1157) | P2 |
| S07 | Covers per day | SUM(CAST(no_of_pax)) by day | PI.no_of_pax | Bar | Dine In | P | P2 |
| S12 | Top 15 items by revenue | SUM(PII.net_amount) | PII.item_code, item_name, net_amount, qty | Row | parent docstatus=1 | Y | P1 |
| S13 | Revenue by menu category | SUM(PII.net_amount) by item_group | PII.item_group | Bar | as S12 | Y (hygiene trap) | P1 |
| S14 | Slow movers | SUM(qty) asc, qty>0 | PII.item_code, qty | Table | 30d | Y | P2 |
| S16 | Item group x weekday | SUM(qty) by item_group x day_of_week | PII.item_group, qty; PI.posting_date | Table | 8 weeks | Y | P2 |
| S15 | Price realisation | SUM(net_amount)/SUM(price_list_rate*qty) | PII.price_list_rate, net_amount, qty | Table | as S12 | Y (~1) | P3 |
| S17 | Starter/dessert attach rate | invoices with group G / dine-in invoices | PII.item_group | Number | Dine In | Y | P3 |
| K8 | Items clogging the pass | SUM(CAST(quantity)) by item | URY KOT Items.item, quantity, course; parent KOT.production | Row | type='New Order' | Y (203) | P2 |
| S11 | Customer-group share | SUM(net_total) by customer_group | PI.customer_group | Donut | default | P (85% null) | P3 |
| S27 | Aggregator share | SUM(net_total) where order_type='Aggregators' | PI.custom_aggregator_id | Number+Bar | default | P (4 rows) | P3 |

### Payments and cash-up
| id | Question | Formula | Doctype.fields | Chart | Filters | Ready | Pri |
|---|---|---|---|---|---|---|---|
| S18 | Payment mode mix | SUM(amount) by mode_of_payment | SIP.mode_of_payment, amount | Donut | `parenttype='POS Invoice'`, parent docstatus=1 | Y | P1 |
| S19 | Mode by order type | SUM(amount) by mode x PI.order_type | SIP + PI.order_type | Bar stacked | as S18 | Y | P2 |
| S20 | Cash-up variance | SUM(difference); sessions with difference<>0 | PCED.difference, expected_amount, closing_amount, mode_of_payment; PCE.posting_date, user | Bar+Number | PCE.docstatus=1 | N (flat 0) | P1 |
| S21 | Expected vs counted cash | SUM(expected_amount) vs SUM(closing_amount) | PCED.* , mode_of_payment='Cash' | Line | PCE.docstatus=1 | Y (101) | P2 |
| S22 | Shift performance | grand_total, total_quantity, period length per closing | PCE.grand_total, total_quantity, period_start_date, period_end_date, user, pos_profile | Table | docstatus=1 | Y | P2 |
| S3 | Cashier shifts | SUM(grand_total) per user/day | POS Opening Entry.user, period_start_date, status, branch; PCE | Table+Bar | docstatus=1 | Y (103/101) | P2 |
| S23 | Discount leakage | SUM(discount_amount)/SUM(total) | PI.discount_amount, total, staff_discount_policy (CF) | Number+Table | default | N (4 invoices) | P2 |
| S26 | Returns | SUM(grand_total) | PI.is_return, return_against | Number | is_return=1 | N | P3 |
| S30 | Daily P&L trend | gross/net sales, cogs, net_profit by date | URY Daily P and L.date, gross_sales, net_sales, cogs, net_profit | Line | docstatus=1 | Y (116) | P2 |

### Kitchen (KOT)
| id | Question | Formula | Doctype.fields | Chart | Filters | Ready | Pri |
|---|---|---|---|---|---|---|---|
| K1 | Tickets per state now | COUNT(name) by state | KE.state, production_unit, branch | Row (7 states) | exclude CANCELLED_* | Y (99) | P1 |
| K7 | Kitchen load by hour/unit | COUNT(KOT) by hour(time) x production | URY KOT.production, time, date, type | Bar stacked | type not Cancelled/Duplicate | Y (132) | P1 |
| C1 | KOT cancel/modify trend | COUNT(name) by type x day | URY KOT.type, date, production | Bar stacked | range | Y | P1 |
| C2 | When do cancels happen | COUNT by CANCELLED_BEFORE_START/AFTER_START/AFTER_READY | KE.state, cancelled_at | Bar | state like CANCELLED_% | Y (6) | P1 |
| C3 | Cancel reasons | COUNT(name) by cancel_reason | PI.cancel_reason (CF), waiter | Row | docstatus=2 | Y (60, 5 reasons) | P1 |
| S25 | Cancellation rate | cancelled/(submitted+cancelled) per day | PI.docstatus | Line | docstatus in (1,2) | Y | P2 |
| K2 | Start to ready time | AVG TIMESTAMPDIFF(SECOND, started_at, ready_at) | KE.started_at, ready_at, state, production_unit | Bar+Line | state READY/SERVED, stamps set | P (reads 0) | P1 |
| K3 | Prep time by hour | AVG prep by hour(started_at) | KE (as K2) | Line | as K2 | P (single day) | P2 |
| K5 | Queue to served | TIMESTAMPDIFF(KOT creation/time, served_at) | KE.served_at; URY KOT.date, time | Number+Line | state=SERVED | P (36) | P2 |
| K6 | SLA breach share | count_if(prep_min > target)/COUNT(*) | derived from K2 | Number+Bar | READY/SERVED | N (no SLA field; target is a constant) | P2 |
| F1 | Ticket funnel | COUNT started -> ready -> served | KE.started_at, ready_at, served_at | Funnel | range | Y (99/48/42/36) | P2 |
| C4 | Line-level voids | SUM(CAST(cancelled_qty)) by disposition | URY KOT Items.cancelled_qty, disposition | Bar | cancelled_qty>0 | P (8 rows, all Pending) | P2 |
| K9 | Slowest items | AVG(ready_at-started_at) | URY KOT Item Execution.state, started_at, ready_at, kot_item | Row | READY/SERVED | P (ms stamps) | P3 |
| C5 | Modification churn | COUNT(type='Order Modified')/COUNT | URY KOT.type, user, original_kot | Bar | range | P (15) | P3 |

### Floor, staff, stock
| id | Question | Formula | Doctype.fields | Chart | Filters | Ready | Pri |
|---|---|---|---|---|---|---|---|
| T1 | Tables occupied now | COUNT(occupied=1)/COUNT | URY Table.occupied, restaurant_room, no_of_seats, is_take_away | Number+Donut | is_take_away=0 | Y snapshot (21/12) | P2 |
| T2 | Table turnover | COUNT(invoice)/COUNT(DISTINCT restaurant_table) per day; S10 invoices and revenue per table | PI.restaurant_table, custom_restaurant_room (510 set), posting_date | Bar+Line | Dine In, docstatus=1 | Y | P1 |
| T4 | Covers by hour vs seats | SUM(CAST(no_of_pax)) by hour / SUM(no_of_seats) | PI.no_of_pax, posting_time; URY Table.no_of_seats | Bar+Line | Dine In, 8 weeks | P | P2 |
| T5 | Dine-in weekday x hour | COUNT by day_of_week x hour(posting_time) | PI.posting_date, posting_time, restaurant_table | Table | Dine In | Y | P2 |
| T6 | Service request response | AVG(resolved_at - requested_at) | URY Service Request.request_type, status, requested_at, resolved_at | Number+Bar | status=Resolved | N (4 rows) | P3 |
| S1 | Waiter ranking | SUM(net_total), COUNT, avg ticket by waiter (S8) | PI.waiter, custom_waiter_employee, net_total | Row | Dine In | Y (7 users) | P1 |
| S2 | Waiter cancel rate | COUNT(docstatus=2)/COUNT | PI.waiter, docstatus, cancel_reason | Bar | docstatus in (1,2) | Y | P2 |
| S9 | Cashier throughput | COUNT, SUM(net_total) by cashier | PI.cashier (CF) | Row | default | Y (3 null) | P3 |
| Y1 | Yield variance by item | AVG(variance_percent); SUM(output)/SUM(input) | URY Yield Check.item, input_qty, output_qty, actual_yield_percent, variance_percent, checked_on | Bar+Line | checked_on range | Y (66, 6 items) | P1 |
| Y2 | Yield check cadence | COUNT by check_type, week | URY Yield Check.check_type, checked_by | Bar | range | Y | P2 |
| W4 | Plan attainment | SUM(fulfilled_qty)/SUM(qty) by service_period/department/production_unit (S29) | URY Sales Plan Item.qty, fulfilled_qty, department, production_unit, production_policy; Sales Plan.plan_date, status, service_period | Bar+Table | status='Locked for Production' | Y (committed_qty N) | P1 |
| W5 | Plan vs units sold | plan qty vs SUM(PII.qty) per (date,item) | Sales Plan Item.item_code, qty; PII.item_code, qty | Bar | pre-aggregate both sides | Y | P2 |
| W1 | Wastage by reason | SUM(Wastage Item.amount) by reason_category | URY Wastage.reason_category, status; Wastage Item.item_code, qty, amount | Row+Donut | status=Authorized | N (0 rows) | P3 |
| W2 | Stock correction cost | SUM(difference_amount) by reason, month | Stock Correction.reason, difference_amount, posting_date, status | Bar | status=Submitted | N (all 0) | P3 |
| W3 | Stock movement | SUM(qty) from_location -> to_location | URY Stock Movement.movement_type, qty, from_location, to_location | Sankey | date | N (0 rows) | P3 |

Dropped as not worth a card: S28 loyalty (Loyalty Program 0 rows), S31 POS vs P&L reconcile (unverified), T3 dwell (unreliable data), S4 staff discount (not live-checked). Fields all exist; drop reason is data, not schema.

## 3. Recommended dashboards (20-col grid, Number h=4, charts h=8-10, row widths sum to 20)

Dashboard filters are Date, String, Number only `research/R4_dashboards.md`. Link each filter to the date/string column of every query.

| Dashboard | Audience | Default filters | Layout (row: [id wN]...) |
|---|---|---|---|
| Owner overview | Owner | Date 30d; order_type | r1 h4: [S01 w5][orders COUNT w5][S04 avg ticket w5][C3 cancelled count w5]; r2 h10: [S02 w12][S03 w8]; r3 h10: [S18 w8][S13 w12]; r4 h8 optional: [S30 w20] |
| Branch manager daily | Branch mgr | Date today; waiter; order_type | r1 h4: [sales w5][orders w5][S06 w5][S07 w5]; r2 h10: [S05 w12][S04 w8]; r3 h10: [S1 w10][T2 per table w10]; r4 h8: [C3 w10][S25 w10] |
| Menu performance | Owner, chef | Date 30d; item_group | r1 h10: [S12 revenue w10][S12 qty w10]; r2 h10: [S13 w10][S14 w10]; r3 h8: [S16 w20]; r4 h8: [W4 w20] |
| Payments and cash-up | Finance | Date 30d; mode_of_payment | r1 h4: [tendered w5][cash share w5][S20 sum diff w5][sessions w5]; r2 h10: [S18 w7][S19 w13]; r3 h10: [S21 w12][S22 w8]; r4 h8: [S20 by day w20] (flat in demo) |
| Kitchen | Lead chef, manager | Date 7d; production_unit; branch | r1 h4: [open tickets K1 w5][avg prep K2 w5][breach K6 w5][cancel C1 w5]; r2 h8: [K2 by unit w10][K2 by day w10]; r3 h8: [K7 w10][C1+C2 w10]. Build K7, C1 first; K2/K6 only on real timestamps |
| Floor and tables | Floor mgr | Date 30d; restaurant_room; Dine In fixed in query | r1 h4: [T1 w7][covers w7][spend/cover w6]; r2 h8: [T2 w10][T5 w10]; r3 h8: [T4 w10][T6 w10, hide until data] |
| Staff (restrict sharing) | Manager | Date; waiter | r1 h8: [S1 w10][S2 w10]; r2 h8: [S3 w10][C3 w10] |
| Stock, yield, plan | Kitchen lead, owner | Date; item; production_unit | r1 h4: [Y1 avg variance w7][W2 count w7][W4 attainment w6]; r2 h8: [Y1 by item w10][W4 by unit w10]; r3 h8: [Y2 w10][W1 w10, placeholder] |

Cap about 11 items per dashboard (7 charts + 4 numbers). Staff dashboards expose per-person data: Insights does not apply Frappe User Permissions automatically, share only with managers (`research/P_analytics_planning.md` s1 step 8, see [permissions.md](permissions.md)).

## Unverified
- Sign convention and midnight behaviour of `time_diff` in a `mutate`; P50/P90 are not builder aggregations (native SQL only) `U2 s5`.
- Dine-in = 995 submitted is a caller correction not re-queried here.
- Insights UI behaviour for the `parenttype` filter and child-to-parent Join keys untested `U1 s5`.
- Reconciliation of PI net_total vs Daily P&L net_sales (S31) not checked.
- Production sites: item_group tree, real KOT stamps, `total_spend_time` reliability, multi-branch `branch` population unknown.
- Whether `waiter` maps to `custom_waiter_employee` for per-employee reporting.
