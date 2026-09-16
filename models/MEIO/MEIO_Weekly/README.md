# MEIO Weekly

Weekly-cadence version of `../meio_optimizer.py`. The committed annual model
averages the full 2026 forecast horizon into one flat safety-stock / reorder-point
/ order-up-to number per SKU x stocking-DC — a once-a-year exercise. This script
runs the same method every week instead, so the decision refreshes with new
forecast and demand data rather than being set once and left alone.

## What changed vs the annual model

1. **Week-specific demand, not a 52-week average.** Each run uses `--as-of`'s own
   `forecast_qty` (plus a look-ahead window) instead of averaging the whole year,
   so seasonality and promo lift show up week to week.
2. **Rolling demand variability.** Each SKU x DC's coefficient of variation is
   recomputed from a rolling trailing window of *realized* actuals ending just
   before `--as-of` (default 13 weeks), instead of being fixed from the 2025
   in-sample history once. If too few realized weeks are available in that
   window it falls back to the full in-sample CV (see the data-limitation note
   below and the script's docstring).
3. **A persistent history log.** Every run appends its category-level totals to
   `output/meio_weekly_history.csv` and redraws `output/chart_ss_trend.png`, so
   successive weekly runs build an actual time series.
4. **Week-over-week deltas in the report**, flagged when a category's optimized
   safety-stock $ moves 10%+ since the last logged run — that's the thing a
   planner actually needs to see each week.

Network structure, lead-time graph walk, echelon/RDC pooling, and the
newsvendor safety-stock formula are otherwise unchanged from the annual model.

## Dataset note

This version targets the **currently staged (uncommitted) input-dataset
refresh** — `cost_parameters.csv` keyed by `(dc_id, subcategory)` rather than
`category`, 27 DCs, 3 years of history. `../meio_optimizer.py` was migrated to
the same schema on 2026-09-15, so both scripts now agree on methodology and
totals should be comparable (subject to §"what changed" above).

Holding cost per unit-year here is derived per node as
`storage_cost_per_unit_year_usd + unit_cost_usd * inventory_carrying_pct_annual / 100`,
since `cost_parameters.csv` doesn't carry a flat category-level holding cost —
this comes out DC-specific, which is more correct than a single category-average figure.

**Rolling-CV limitation:** `demand_forecast.csv`'s forward (2026) rows have no
`actual_qty` yet in this synthetic extract — there are no realized 2026
outcomes to roll into the trailing window. Every 2026 `--as-of` date will
therefore fall back to the full 2025 in-sample CV until a live weekly feed
starts populating `actual_qty` for elapsed 2026 weeks. The code keys off
`week_start_date`, not the `in_sample`/`forward` flag, so it starts using real
rolling data automatically the day that feed exists — no changes needed here.

## Usage

```bash
python meio_optimizer_weekly.py --as-of 2026-W11
```

Flags (all optional):
- `--as-of` — decision week (`2026-Wnn`). Defaults to the first forward period.
- `--lookahead` — weeks of forward visibility to compute alongside the decision
  week (default 8).
- `--trailing` — trailing weeks of realized actuals used to refresh CV (default 13).
- `--min-trailing-periods` — realized weeks required before the rolling CV is
  trusted over the in-sample fallback (default 8).

Advance `--as-of` by one ISO week each cadence. Re-running the same `--as-of`
overwrites that run's row in the history log rather than duplicating it.

## Outputs (`./output/`)

- `<as_of>/meio_recommendations_weekly.csv` — SKU x DC x week rows across the
  look-ahead window; filter `is_decision_week` for that run's action list.
- `<as_of>/meio_summary_by_category.csv`, `<as_of>/meio_summary_by_dc.csv` —
  decision-week only.
- `<as_of>/meio_weekly_report.md` — headline, week-over-week deltas, tables.
- `<as_of>/chart_ss_by_category.png` — this run.
- `meio_weekly_history.csv`, `chart_ss_trend.png` — cross-run log/trend, shared
  across every `--as-of` you've run.

Dated `output/<as_of>/` folders are **gitignored by default** (see `.gitignore`
in this folder) — they're fully regenerable by re-running the command above,
and a single run's `meio_recommendations_weekly.csv` can run 15-20MB (every
SKU x DC x look-ahead-week line), which isn't worth carrying in git 52 times
a year. Only `meio_weekly_history.csv` (a few KB) and `chart_ss_trend.png`
are tracked — that's the actual cross-run trend this workflow exists to build.

**Weekly workflow, end to end:**
```bash
python meio_optimizer_weekly.py --as-of 2026-W11   # advance one ISO week each cadence
```
Then read `output/2026-W11/meio_weekly_report.md` for that week's decision
and WoW flags. The dated folder is scratch — delete it, or leave it, it won't
get committed. `output/meio_weekly_history.csv` and `chart_ss_trend.png`
accumulate across every run and are what you'd commit to keep the trend.

## Validation — `backtest_csl.py`

Before trusting either script's numbers, this checks whether MEIO's own
service-level promise actually held up against realized history:

```bash
python backtest_csl.py
```

Replays 44 weeks of 2025 in-sample history, recomputing the recommendation
at each as-of week from only prior information (no lookahead), then checks
whether realized demand during the lead-time window stayed within the
recommended reorder point — a calibration check on the safety-stock formula,
not a dollar simulation (see the script docstring for the full method and
`backtest_output/backtest_report.md` after running for results).

**Current finding (2026-09-15 run): achieved CSL is ~87% against a ~99.8%
target — a real, diagnosed gap**, not a bug in the backtest:
1. `forecast_qty` under-predicts `actual_qty` by ~7-8% portfolio-wide — a
   persistent bias the safety-stock formula can't correct (it only buffers
   random variance around the mean).
2. The 13-week trailing CV this workflow uses averages ~0.90 vs. ~1.05 for
   the full 2025 year — a recent calm stretch understates real volatility.
3. Separately: target CSL clusters almost entirely in the 99-99.9% band
   portfolio-wide, because `stockout_cost_unit` (margin-driven, several
   dollars) dwarfs `holding_per_unit_week` (a few cents) for nearly every
   line — the "differentiated target per SKU" the model is meant to produce
   is barely differentiated given this dataset's cost parameters.

`backtest_output/backtest_detail.csv` (~30MB, every SKU x DC x as-of-week
observation) is gitignored — regenerate by rerunning. Only the aggregated
calibration tables, chart, and report are tracked.

## Full simulation — `simulate_policy.py`

The follow-on to the statistical backtest: replays the same 2025 window
end-to-end as an actual periodic-review inventory simulation (on-hand,
weekly reorders, receipts after a deterministic lead time) under the
MEIO-recommended policy, and compares it head-to-head against the real 2025
trace already recorded in `inventory.csv` — same demand stream, two
policies.

```bash
python simulate_policy.py
```

**Current finding (2026-09-15 run): the MEIO policy underperforms the real
2025 policy** — fill rate 90.2% vs. 95.3%, at almost identical average
inventory carried (+$124K, +4%). This holds in every category, not one
outlier (`simulation_output/chart_fill_rate.png`). Net benefit (avoided
shortfall $ minus added inventory $) is **-$2.25M** over the window — a net
cost, not a net benefit, as simulated.

This is the same root cause as the statistical backtest's finding, now shown
in dollar terms: `inventory.csv`'s real policy appears to be calibrated
against the data generator's true underlying demand distribution, while
`MEIO_Weekly`'s reorder points are only ever computed from a forecast that's
biased ~-7.8% low, refreshed by a trailing-CV window that understates real
volatility. That's a forecast-quality / CV-window problem to fix, not
evidence the safety-stock formula itself is wrong — see
`simulation_output/simulation_report.md` for the full breakdown, including
what the dollar figures do and don't mean (the shortfall-$ convention used
here is deliberately simplified vs. `inventory.csv`'s own `lost_margin_usd`,
which forgives some backordered units — documented in the report).

`simulation_output/simulation_detail.csv` (~30MB, weekly per-node trace) is
gitignored; only the category summary, chart, and report are tracked.
