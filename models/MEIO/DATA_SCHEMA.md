# MEIO — Required Input Data Schema

`meio_optimizer.py`, `MEIO_Weekly/meio_optimizer_weekly.py`,
`MEIO_Weekly/backtest_csl.py`, and `MEIO_Weekly/simulate_policy.py` all read from
`../input datasets/` (or `../../../input datasets/` from `MEIO_Weekly/`), relative
to this folder. **That data is not committed to this branch** — see
`PROGRESS.md` for why. This file documents exactly what each script needs so
you can point it at your own local copy of the dataset (or a subset of it)
and have everything run correctly.

Only the columns actually read by these scripts are listed below — extra
columns in your files are ignored, not an error.

## `sku_master.csv` — grain: one row per SKU

| Column | Type | Used for |
|---|---|---|
| `sku_id` | string, unique | Join key everywhere |
| `category` | string | Rollup grouping in every output |
| `subcategory` | string | Joins to `cost_parameters.csv` |
| `unit_cost_usd` | float | Converts unit safety stock to $; feeds holding cost |
| `margin_usd` | float | Feeds the newsvendor stockout-cost term |

## `cost_parameters.csv` — grain: one row per (DC, subcategory)

| Column | Type | Used for |
|---|---|---|
| `dc_id` | string | Joins to the stocking-node id used elsewhere |
| `subcategory` | string | Joins to `sku_master.subcategory` |
| `storage_cost_per_unit_year_usd` | float | Holding cost, location-specific term |
| `inventory_carrying_pct_annual` | float (percent, e.g. `10.2` = 10.2%) | Holding cost, applied to `unit_cost_usd` |
| `stockout_cost_multiplier` | float | Newsvendor `Cu = margin_usd * stockout_cost_multiplier` |

**Every `(dc_id, subcategory)` combination that appears anywhere in `sku_master.csv`
x the active DC list needs a row here** — the scripts left-join and will
produce `NaN` costs (propagating to `NaN` recommendations) for any missing
combination. In the reference dataset this is a complete cross product: 27
DCs x 25 subcategories = 675 rows.

> **Note:** this is a *different* grain than what `meio_optimizer.py` originally
> shipped against (`category`-only). If your `cost_parameters.csv` is still
> `category`-keyed, either regenerate it at `(dc_id, subcategory)` grain, or
> you'll need to revert the cost-join logic these scripts currently use — see
> `PROGRESS.md` item 1.

## `network_lanes.csv` — grain: one row per lane (network edge)

| Column | Type | Used for |
|---|---|---|
| `origin_node_id`, `origin_node_type` | string | Graph edge start; types are `PLANT` / `PLANT_DC` / `REGIONAL_DC` |
| `destination_node_id`, `destination_node_type` | string | Graph edge end; types are `PLANT_DC` / `REGIONAL_DC` / `CUSTOMER_DC` |
| `lead_time_mean_days` | float | Primary-lane selection (fastest lane wins) + cumulative lead time |
| `lead_time_std_days` | float | Cumulative lead-time variance |

The network must form a connected path from every `PLANT` through `PLANT_DC`
and `REGIONAL_DC` to every active `CUSTOMER_DC` — the scripts pick, per
destination node, the single inbound lane with the lowest `lead_time_mean_days`
(single-sourcing assumption, see `PROGRESS.md` / the script docstrings).

## `demand_forecast.csv` — grain: one row per (period, SKU, DC, B2B customer)

| Column | Type | Used for |
|---|---|---|
| `period` | string, ISO week (`2025-W01`) | Time axis |
| `week_start_date` | date | Chronological ordering of periods |
| `horizon` | `in_sample` or `forward` | Splits realized history from the planning horizon |
| `sku_id`, `dc_id` | string | Join keys |
| `forecast_qty` | float | Demand mean input (every row, both horizons) |
| `actual_qty` | float, **null for `forward` rows** | Realized demand (`in_sample` only) — drives CV estimation and every backtest/simulation comparison |

**Important — all four scripts aggregate this file to `(period, sku_id, dc_id)`
grain immediately after loading**, summing `forecast_qty` and `actual_qty`
across any B2B-customer split rows (see `PROGRESS.md` item 2 — this was a real
bug before the fix). If your extract is already at `(period, sku_id, dc_id)`
grain with no `b2b_customer_id` splits, the aggregation is a no-op and safe
either way.

## `inventory.csv` — grain: one row per (period, SKU, DC)

Only read by `MEIO_Weekly/simulate_policy.py` (the "current policy" baseline
arm) and the current-vs-optimized comparison in `meio_optimizer.py` /
`meio_optimizer_weekly.py`.

| Column | Type | Used for |
|---|---|---|
| `period`, `week_start_date` | as above | Time axis |
| `sku_id`, `dc_id` | string | Join keys |
| `opening_stock` | float | Simulation's starting on-hand for the first simulated week |
| `demand_qty` | float | Real demand each node had to fulfill (simulation's exogenous demand stream) |
| `unmet_qty` | float | Real recorded shortfall (units) |
| `lost_margin_usd` | float | Real recorded shortfall ($) — see `simulate_policy.py`'s report for why this isn't directly comparable to the simulation's own shortfall-$ convention |
| `closing_stock` | float | Real recorded ending inventory (working-capital comparison) |
| `safety_stock`, `reorder_point` | float | The *current* (as-is) policy, for the annual/weekly scripts' delta calculation |

## Minimal viable local setup

To run just `meio_optimizer.py` or `MEIO_Weekly/meio_optimizer_weekly.py`
(not the backtest or simulation), you only need `sku_master.csv`,
`cost_parameters.csv`, `network_lanes.csv`, `demand_forecast.csv`, and
`inventory.csv` (for the baseline comparison only — the scripts would need a
small edit to skip it entirely). All five together are what
`backtest_csl.py` and `simulate_policy.py` need too, since they reuse the
same recommendation engine plus `inventory.csv` as ground truth.
