# Dashboard plan: <name>
Fill every line before writing a finsights spec. "n/a" needs a reason. See `references/analytics-planning.md`.

- Owner / decision it supports:
- Question (one sentence ending in a decision):
- Audience (roles, frequency):
- Metric: name = <formula>; amount field = <net_total|grand_total|net_amount>; included statuses = <...>
- Grain (one row =):
- Dimensions (each on the grain row or 1:1 / N:1):
- Source table(s): <DocType> ; columns verified with `finsights catalog` on <site/date>: <cols>
- Joins (cardinality each; fan-out risk?):
- Filters: date range (bounded: ___) ; docstatus=___ ; is_return=___ (net|exclude) ; branch/company=___
- Query shape: row-level (select only) ; aggregation done in charts? yes/no (why)
- Charts (<=8): id | type | x | y (agg, format) | KPI date column? | order/limit | question answered
- Dashboard filters: id | type | default | applies_to {query: column}
- Layout (20-col grid): filters row 0 ; KPI w20 h3 ; trend w12 h9 ; ranking w8 h9
- Permissions / data exposure (roles, branch scoping, sensitive tables):
- Demo-data readiness: row counts per needed field = ___ ; empty fields = ___
- Validation SQL (read-only) + expected result for the window/filter (optional; write `N/A, no DB access` if you cannot run SQL, and use the alternative below):
- Alternative validation (when no DB access): compare dashboard totals with a Frappe report or list view count/sum for the same window/filter: report/list = ___ ; dashboard = ___
- Proof (optional; SQL output file, or screenshot/notes of the report comparison) + dashboard value observed:
- Pitfalls checked: grain mix / avg-of-avg / drafts / returns / fan-out / text-typed numbers / slices<=5 / KPI latest-period
- Desk placement: workspace ___ label ___
- Unknowns / sign-off (who confirmed the metric definition):
