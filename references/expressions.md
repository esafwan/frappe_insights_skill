# Expressions (mutate columns and expression measures)

Recipes for computed columns: hour of day, text-to-number casts, ratios, conditional counts. Every recipe below was run on a live site (Frappe 16.54.0 + Insights 3.15.1, MariaDB, 2026-11-03) and compared with SQL where a number is involved. Anything not run is marked `(unverified)`.

Where expressions go in a spec: a query `mutate:` entry (`{as, type, expr}`), or a chart `y:` measure given as `expr` instead of `column` (see `spec-format.md`). In raw query JSON an expression is `{"type":"expression","expression":"<text>"}`; see `queries.md`.

## Rules
1. Column names are used bare (`posting_time`), functions are the Insights function set (`hour`, `day_name`, `if_else`, `count_if`, `sum_if`, `date_diff`, `time_diff`, `within`, `coalesce`, `round`, ...). The full set is in the Insights source `insights/insights/doctype/insights_data_source_v3/ibis/functions.py`; every function has an `Examples:` docstring.
2. Column methods work too: `no_of_pax.cast("int64")`. There is no `cast(...)` function.
3. Aggregates inside an expression measure (`sum(...)`, `count_if(...)`) are allowed; a plain column expression in a measure is not an aggregate.
4. Group before dividing: a ratio of totals is `sum(a) / sum(b)`, never `avg(a / b)` (averaging averages is wrong, see `analytics-planning.md`).
5. Division by zero returns null (not an error): leave it null in charts, do not coalesce it to 0 (a 0 reads as "zero spend").

## Verified recipes
| Need | Expression | Result on the demo site |
|---|---|---|
| Hour of day from a Time column | `hour(posting_time)` (type Integer) | `18:49:00` -> `18`; also `hour(KOT.time)` works despite non-padded seconds |
| Text column to integer | `no_of_pax.cast("int64")` (type Integer) | `"5"` -> `5` |
| Text column to decimal | `no_of_pax.cast("float64")` (type Decimal) | `"5"` -> `5.0` |
| Weekday name | `day_name(posting_date)` (type String) | `Wednesday` |
| 0/1 flag | `if_else(grand_total > 500, 1, 0)` (type Integer) | `1` |
| Count with a condition | `count_if(docstatus == 2)` as a measure | 60 cancelled, 3177 submitted, equal to SQL |
| Ratio of two sums | measure `expression: "sum(net_total) / sum(pax)"` (type Decimal) | Dine In 2025-08: 268.0, 2025-09: 176.72, equal to SQL; null where covers = 0 |

## Hour-of-day (orders per hour)
Row-level query: add a mutate `hour_of_day = hour(posting_time)` (Integer), keep `name`; chart: x = `hour_of_day`, y = `count` of `name`, type Bar. Filter by `docstatus = 1`. (Chart wiring: `charts.md`.)

## Spend per cover
1. Query (row level, Dine In): mutate `pax = no_of_pax.cast("int64")` (guest count is stored as text, see `ury-analytics.md`).
2. Chart measure `expr: "sum(net_total) / sum(pax)"`, x = `posting_date` with a granularity. Covers of 0 give null for that bucket: expected, not an error.
Alternative with the same result: two measures (`sum(net_total)` as `sales`, `sum(pax)` as `covers`) and a mutate `sales / covers` after the summarize (verified, identical numbers).

## Durations
`date_diff` takes units year..day only. `time_diff` casts to TIME: it drops fractions of a second, is wrong across midnight, and the unit `millisecond` errors on MariaDB. The argument order (which side is subtracted) is `(unverified)`: the demo data has only millisecond gaps, so the sign could not be observed. For prep-time metrics use native SQL (`TIMESTAMPDIFF`) and verify the sign on real data first.

## Not verified
- `sum_if`, `case` and `format_date` were not run (documented in the source only). `coalesce(a, b)` is used in the shared URY layer name joins. For rounding use the method form `(sum(x)).round(0)`; `round(sum(x), 0)` renders a blank Number tile (gotcha 47).
- `hour()` on a Datetime column (cast to time first) was not run.
- Expression measures inside a Number chart config were not run; a summarize measure was.
