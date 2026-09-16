# MEIO — Progress

Status snapshot of the Multi-Echelon Inventory Optimization work. Two deliverables live under
`models/MEIO/`:

```
models/MEIO/
├── meio_optimizer.py       annual model — one flat SS/ROP/order-up-to per SKU x DC for all of 2026
├── output/                 annual model's current results (tracked deliverable)
└── MEIO_Weekly/             weekly-cadence model + its validation tooling
    ├── meio_optimizer_weekly.py   re-run every week with --as-of <period>
    ├── backtest_csl.py            statistical calibration check against 2025 history
    ├── simulate_policy.py         full periodic-review inventory simulation
    ├── output/                    meio_weekly_history.csv + chart_ss_trend.png (cross-run log)
    ├── backtest_output/           calibration tables, chart, report
    ├── simulation_output/         simulation summary, chart, report
    └── README.md                  usage + full detail on every script above
```

## Done

1. **Annual model migrated to the current dataset schema.** `cost_parameters.csv` moved from a
   `category`-keyed table to `(dc_id, subcategory)` grain; `meio_optimizer.py` was broken
   (`KeyError: 'category'`) until patched to match. Holding cost per node is now
   `storage_cost_per_unit_year_usd + unit_cost_usd * inventory_carrying_pct_annual / 100`, derived
   per stocking location instead of a single category-average figure.
2. **A real duplicate-row bug found and fixed, in both scripts.** `demand_forecast.csv` is actually
   `(period, sku, dc, b2b_customer)` grain — ~5% of sku x dc x period combos split across 2 B2B
   customers. Neither script aggregated that away; the per-period `MEIO_Weekly` was hit hardest
   (duplicate demand rows corrupted the RDC-pooling merge and double-counted in rollups — safety
   stock is sqrt-scaled/subadditive, so this *overstated* recommendations). Fixed by collapsing to
   true weekly totals immediately after load. Effect: annual model barely moved (+0.16%); weekly
   model's optimized safety stock dropped ~10% on the test run.
3. **`MEIO_Weekly` built.** Same method as the annual model, but re-run on a cadence: each run uses
   its own decision week's `forecast_qty` (not a 52-week average) and a rolling trailing-CV window
   instead of a fixed year, logs every run to a persistent history file, and flags week-over-week
   moves ≥10% for review. See `MEIO_Weekly/README.md`.
4. **Statistical calibration backtest built and run.** Replayed 44 weeks of 2025 history with no
   lookahead. **Finding: achieved cycle service level ≈87% against a ≈99.8% modeled target** —
   traced to two measured causes, not a flaw in the newsvendor formula itself:
   - `forecast_qty` under-predicts `actual_qty` by ~7-8% portfolio-wide (a persistent bias the
     safety-stock z-score can't correct — it only buffers random variance around the mean).
   - The 13-week trailing CV this workflow uses averages ~0.90 vs. ~1.05 for the full 2025 year —
     recent calm periods understate real volatility.
   - Separately: target CSL clusters almost entirely in the 99-99.9% band portfolio-wide, because
     stockout cost (margin-driven) dwarfs holding cost for nearly every line — the "differentiated
     target per SKU" the model is meant to produce isn't really differentiated, given this dataset's
     cost parameters.
5. **Full periodic-review simulation built and run**, replaying the same 2025 window end-to-end
   (simulated on-hand, weekly reorders, deterministic lead-time receipts) under the MEIO policy vs.
   the real 2025 trace already recorded in `inventory.csv`. **Finding: the MEIO policy
   underperforms — fill rate 90.2% vs. 95.3% real, at nearly the same average inventory carried
   (+4%). Net benefit (avoided shortfall $ − added inventory $) = −$2.25M over the window: a net
   cost, not a net benefit, as currently calibrated.** Holds in every category, not one outlier;
   verified it isn't a simulation cold-start artifact (<1pp effect measured directly).

## What this means

The safety-stock/newsvendor *formula* isn't the problem — the real 2025 policy looks like it was
calibrated against the data generator's true underlying demand distribution, while `MEIO_Weekly`'s
weekly recompute only ever sees a forecast that's biased low and a CV window that understates real
volatility. Recommending more inventory hasn't yet been shown to buy a better outcome on this
dataset; it currently buys roughly the same inventory spend for a *worse* fill rate.

## Not done yet

- **Forecast-bias correction and/or a wider/blended CV window** — the concrete, diagnosed fix for
  the calibration gap above. Not implemented. Re-running `backtest_csl.py` and `simulate_policy.py`
  after any such change is how to verify it actually closes the gap, rather than trusting the
  formula's own promise again.
- The annual `meio_optimizer.py` has not been backtested/simulated the way `MEIO_Weekly` has — the
  same calibration issue almost certainly applies there too (same formula, same forecast), just not
  yet quantified for the annual cadence specifically.
- No network-cascading simulation (a changed policy at one node changing the replenishment signal
  its upstream node sees) — flagged as a scope simplification in `simulate_policy.py`'s docstring.

## Dataset dependency note

Both scripts require the newer `input datasets/` schema (`cost_parameters.csv` keyed by
`(dc_id, subcategory)`, richer `inventory.csv`) that was staged separately in this repo and is
**not included in this branch's commit** — `inventory.csv` alone is ~122MB, well past what's
sensible (and past GitHub's hard limit) to commit directly. If you check out this branch on a clean
tree without that dataset refresh also present, these scripts will fail against the older
`category`-keyed `cost_parameters.csv` still on `main`. Whoever finalizes that dataset commit should
treat this as one of the things that needs to land alongside it.
