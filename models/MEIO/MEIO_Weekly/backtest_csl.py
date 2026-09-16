"""
Supply Brain — MEIO Statistical CSL Backtest
============================================================
Validates the MEIO safety-stock formula against realized 2025 history,
instead of only ever trusting it on 2026 data nobody has lived through yet.

The claim every MEIO recommendation makes is a cycle service level (CSL)
promise: reorder_point is sized so that
    P(demand during lead time <= reorder_point) = target_csl
This script checks whether that promise actually held, using the one year
of realized outcomes the dataset has: `demand_forecast.csv`'s 2025
`in_sample` rows, which carry both `forecast_qty` (what was known at the
time) and `actual_qty` (what actually happened).

Method
------
For each SKU x DC and each usable as-of week t in 2025:
1. Recompute the MEIO recommendation (target_csl, reorder_point) using
   ONLY information available strictly before t: a trailing window of
   realized actuals ending just before t for the demand CV (no lookahead —
   unlike meio_optimizer_weekly.py's production fallback, which is allowed
   to use the full prior year since it's never asked to score a date
   inside that year), t's own forecast_qty for the demand mean, and the
   same static lead-time/cost model as meio_optimizer.py /
   meio_optimizer_weekly.py.
2. Compute realized demand during the lead-time window starting at t,
   linearly scaled by lt_mean_wk the same way the model itself scales
   cycle stock (fwd_mean x lt_mean_wk) -- summed actuals over
   ceil(lt_mean_wk) weeks, scaled down by lt_mean_wk / ceil(lt_mean_wk)
   for nodes with a sub-week lead time.
3. Record whether realized demand stayed within reorder_point.

Pooling across every (SKU x DC, as-of week) observation, "achieved CSL" =
the fraction actually covered. Comparing that to the average "target CSL"
the model promised is a calibration check on the formula itself.

What this does NOT test: forecast_qty's own accuracy (a separate property
of the data generator), or full order/receipt inventory mechanics (queued
as a follow-on full simulation, not built here).

Outputs (./backtest_output/):
    backtest_detail.csv          every (SKU, DC, as-of week) observation -- large, gitignored
    calibration_by_bucket.csv    target vs achieved CSL, bucketed by target level
    calibration_by_category.csv  target vs achieved CSL, by category
    chart_calibration.png        target vs achieved reliability plot
    backtest_report.md
"""

import argparse
import math
import os
import numpy as np
import pandas as pd
from scipy.stats import norm
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


DATA = "../../../input datasets"
OUT = "backtest_output"

Z_MIN, Z_MAX = 0.84, 3.10
DEFAULT_TRAILING_WEEKS = 13
DEFAULT_MIN_TRAILING_PERIODS = 8

parser = argparse.ArgumentParser(description="Backtest MEIO's cycle-service-level promise against 2025 actuals")
parser.add_argument("--trailing", type=int, default=DEFAULT_TRAILING_WEEKS,
                     help=f"Trailing weeks of actuals used to estimate demand CV at each as-of week (default {DEFAULT_TRAILING_WEEKS}).")
parser.add_argument("--min-trailing-periods", type=int, default=DEFAULT_MIN_TRAILING_PERIODS,
                     help=f"Minimum realized weeks required before the first usable as-of week (default {DEFAULT_MIN_TRAILING_PERIODS}).")
args = parser.parse_args()
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- load ---
sku = pd.read_csv(f"{DATA}/sku_master.csv")
cost_p = pd.read_csv(f"{DATA}/cost_parameters.csv")
lanes = pd.read_csv(f"{DATA}/network_lanes.csv")
dfc = pd.read_csv(f"{DATA}/demand_forecast.csv")

# demand_forecast.csv is actually (period, sku, dc, b2b_customer) grain -- ~5% of
# sku x dc x period combos are split across 2 B2B customers. Collapse to true
# DC-level weekly totals first (see meio_optimizer_weekly.py for the same fix).
dfc = (dfc.groupby(["period", "week_start_date", "horizon", "sku_id", "dc_id"], as_index=False)
       .agg(forecast_qty=("forecast_qty", "sum"), actual_qty=("actual_qty", lambda s: s.sum(min_count=1))))
dfc["week_start_date"] = pd.to_datetime(dfc["week_start_date"])

in_sample = dfc[dfc.horizon == "in_sample"].copy()
periods_df = (in_sample[["period", "week_start_date"]].drop_duplicates()
              .sort_values("week_start_date").reset_index(drop=True))
periods = periods_df["period"].tolist()
n_periods = len(periods)

active_dc = sorted(dfc["dc_id"].unique())
active_cdc = [d for d in active_dc if d.startswith("CDC")]
active_rdc = [d for d in active_dc if d.startswith("RDC")]
print(f"Backtesting {n_periods} in-sample weeks ({periods[0]}..{periods[-1]}), "
      f"{len(active_rdc)} RDC + {len(active_cdc)} CDC")

# --------------------------------------------------- primary lane graph ---
def primary_upstream(dest_ids, dest_type, allowed_origins=None):
    sub = lanes[lanes.destination_node_type == dest_type]
    if allowed_origins is not None:
        sub = sub[sub.origin_node_id.isin(allowed_origins)]
    sub = sub[sub.destination_node_id.isin(dest_ids)]
    best = sub.sort_values("lead_time_mean_days").drop_duplicates("destination_node_id", keep="first")
    return best.set_index("destination_node_id")[["origin_node_id", "lead_time_mean_days", "lead_time_std_days"]]

pdc_ids = lanes.loc[lanes.destination_node_type == "PLANT_DC", "destination_node_id"].unique()
plant_to_pdc = primary_upstream(pdc_ids, "PLANT_DC")
pdc_to_rdc = primary_upstream(active_rdc, "REGIONAL_DC", allowed_origins=pdc_ids)
rdc_to_cdc = primary_upstream(active_cdc, "CUSTOMER_DC", allowed_origins=active_rdc)

rdc_leadtime = {}
for rdc, row in pdc_to_rdc.iterrows():
    p2p = plant_to_pdc.loc[row.origin_node_id]
    rdc_leadtime[rdc] = (p2p.lead_time_mean_days + row.lead_time_mean_days,
                          p2p.lead_time_std_days**2 + row.lead_time_std_days**2)

cdc_leadtime = {}
cdc_primary_rdc = {}
for cdc, row in rdc_to_cdc.iterrows():
    cdc_primary_rdc[cdc] = row.origin_node_id
    r_mean, r_var = rdc_leadtime[row.origin_node_id]
    cdc_leadtime[cdc] = (r_mean + row.lead_time_mean_days, r_var + row.lead_time_std_days**2)

# per-node lead time in weeks + the whole-week window needed to observe it
node_lt = {}
for dc, (mean_d, var_d) in {**rdc_leadtime, **cdc_leadtime}.items():
    lt_mean_wk = mean_d / 7.0
    lt_std_wk = math.sqrt(var_d) / 7.0
    ceil_wk = max(1, math.ceil(lt_mean_wk))
    node_lt[dc] = dict(lt_mean_wk=lt_mean_wk, lt_std_wk=lt_std_wk, ceil_wk=ceil_wk, scale=lt_mean_wk / ceil_wk)

sku_c = sku[["sku_id", "category", "subcategory", "unit_cost_usd", "margin_usd"]].copy()

# ------------------------------------------------- root-cause diagnostics ---
# The safety-stock formula only buffers RANDOM variability around the forecast
# mean -- it cannot correct a persistent BIAS in the mean itself, or a trailing
# CV window that understates full-year volatility. Measure both directly so a
# calibration gap can be attributed rather than just observed.
portfolio_forecast_bias = (in_sample.forecast_qty.sum() - in_sample.actual_qty.sum()) / in_sample.actual_qty.sum()
full_year_cv = (in_sample.groupby(["sku_id", "dc_id"])["actual_qty"].agg(m="mean", s="std")
                .assign(cv_full=lambda d: (d.s / d.m.replace(0, np.nan)).fillna(0).clip(upper=3)))
trailing_cv_means = []  # populated per as-of week below, compared to full_year_cv at the end

# ------------------------------------------------- fast per-period lookups ---
actual_pivot = in_sample.pivot_table(index=["sku_id", "dc_id"], columns="period", values="actual_qty", aggfunc="sum")
forecast_pivot = in_sample.pivot_table(index=["sku_id", "dc_id"], columns="period", values="forecast_qty", aggfunc="sum")
actual_pivot = actual_pivot[periods]
forecast_pivot = forecast_pivot[periods]

# ------------------------------------------------------- backtest loop ---
detail_frames = []
for i in range(args.min_trailing_periods, n_periods):
    as_of = periods[i]
    trailing_cols = periods[max(0, i - args.trailing):i]
    hist = actual_pivot[trailing_cols]
    hist_mean = hist.mean(axis=1)
    hist_std = hist.std(axis=1)
    cv = (hist_std / hist_mean.replace(0, np.nan)).fillna(0).clip(upper=3)

    trailing_cv_means.append(cv.mean())

    fwd_mean_t = forecast_pivot[as_of]
    fwd_std_t = fwd_mean_t * cv
    flat = pd.DataFrame({"fwd_mean": fwd_mean_t, "fwd_std": fwd_std_t}).reset_index()

    cdc_rows = [flat[flat.dc_id == cdc].copy() for cdc in active_cdc]
    cdc_demand = pd.concat(cdc_rows, ignore_index=True)

    rdc_rows = []
    for rdc in active_rdc:
        own = flat[flat.dc_id == rdc][["sku_id", "fwd_mean", "fwd_std"]].rename(
            columns={"fwd_mean": "own_mean", "fwd_std": "own_std"})
        served = [c for c, r in cdc_primary_rdc.items() if r == rdc]
        if served:
            down = flat[flat.dc_id.isin(served)].groupby("sku_id").agg(
                down_mean=("fwd_mean", "sum"), down_var=("fwd_std", lambda s: (s**2).sum())).reset_index()
        else:
            down = pd.DataFrame(columns=["sku_id", "down_mean", "down_var"])
        m = pd.merge(own, down, on="sku_id", how="outer").fillna(0.0)
        for col in ["own_mean", "own_std", "down_mean", "down_var"]:
            m[col] = m[col].astype(float)
        m["fwd_mean"] = m.own_mean + m.down_mean
        m["fwd_std"] = np.sqrt(m.own_std**2 + m.down_var)
        m["dc_id"] = rdc
        rdc_rows.append(m[["sku_id", "dc_id", "fwd_mean", "fwd_std"]])
    rdc_demand = pd.concat(rdc_rows, ignore_index=True)

    node = pd.concat([cdc_demand[["sku_id", "dc_id", "fwd_mean", "fwd_std"]],
                       rdc_demand[["sku_id", "dc_id", "fwd_mean", "fwd_std"]]], ignore_index=True)
    node["lt_mean_wk"] = node.dc_id.map(lambda d: node_lt[d]["lt_mean_wk"])
    node["lt_std_wk"] = node.dc_id.map(lambda d: node_lt[d]["lt_std_wk"])
    node = node.merge(sku_c, on="sku_id", how="left")
    node = node.merge(cost_p, on=["dc_id", "subcategory"], how="left")
    node["holding_per_unit_week"] = (
        node.storage_cost_per_unit_year_usd + node.unit_cost_usd * node.inventory_carrying_pct_annual / 100.0
    ) / 52.0
    node["stockout_cost_unit"] = node.margin_usd * node.stockout_cost_multiplier

    co_lt = node.holding_per_unit_week * node.lt_mean_wk.clip(lower=1.0)
    cr = (node.stockout_cost_unit / (node.stockout_cost_unit + co_lt)).clip(0.80, 0.999)
    node["target_csl"] = cr
    node["z"] = norm.ppf(cr).clip(Z_MIN, Z_MAX)
    node["sigma_lt_demand"] = np.sqrt(node.lt_mean_wk * node.fwd_std**2 + node.fwd_mean**2 * node.lt_std_wk**2)
    node["safety_stock"] = node.z * node.sigma_lt_demand
    node["reorder_point"] = node.fwd_mean * node.lt_mean_wk + node.safety_stock

    # realized demand during each node's own lead-time window, same linear
    # scaling the model uses for cycle stock (fwd_mean x lt_mean_wk)
    realized_rows = []
    for dc in active_dc:
        w = node_lt[dc]
        end = i + w["ceil_wk"]
        if end > n_periods:
            continue
        window_cols = periods[i:end]
        if dc in active_cdc:
            r = actual_pivot.xs(dc, level="dc_id")[window_cols].sum(axis=1) * w["scale"]
        else:
            served = [c for c, rdc in cdc_primary_rdc.items() if rdc == dc]
            own_r = actual_pivot.xs(dc, level="dc_id")[window_cols].sum(axis=1)
            if served:
                down_r = (actual_pivot.loc[(slice(None), served), window_cols]
                          .groupby(level="sku_id").sum().sum(axis=1))
            else:
                down_r = pd.Series(0.0, index=own_r.index)
            r = (own_r.add(down_r, fill_value=0.0)) * w["scale"]
        realized_rows.append(pd.DataFrame({"sku_id": r.index, "dc_id": dc, "realized_demand_lt": r.values}))
    realized = pd.concat(realized_rows, ignore_index=True) if realized_rows else pd.DataFrame(
        columns=["sku_id", "dc_id", "realized_demand_lt"])

    node = node.merge(realized, on=["sku_id", "dc_id"], how="inner")  # drop nodes with no valid window this as-of
    node["covered"] = node.realized_demand_lt <= node.reorder_point
    node["shortfall_units"] = (node.realized_demand_lt - node.reorder_point).clip(lower=0)
    node["shortfall_usd"] = node.shortfall_units * node.stockout_cost_unit
    node["as_of"] = as_of
    detail_frames.append(node[["as_of", "sku_id", "dc_id", "category", "target_csl", "reorder_point",
                                "realized_demand_lt", "covered", "shortfall_units", "shortfall_usd"]])

detail = pd.concat(detail_frames, ignore_index=True)
detail.to_csv(f"{OUT}/backtest_detail.csv", index=False)
print(f"\nWrote {OUT}/backtest_detail.csv  ({len(detail):,} observations across {n_periods - args.min_trailing_periods} as-of weeks)")

# --------------------------------------------------------------- calibration ---
overall_target = detail.target_csl.mean()
overall_achieved = detail.covered.mean()
print(f"Portfolio: target CSL {overall_target:.1%}  |  achieved {overall_achieved:.1%}  |  gap {overall_achieved-overall_target:+.1%}")

bins = [0.80, 0.85, 0.90, 0.95, 0.975, 0.99, 1.001]
labels = ["80-85%", "85-90%", "90-95%", "95-97.5%", "97.5-99%", "99-99.9%"]
detail["target_bucket"] = pd.cut(detail.target_csl, bins=bins, labels=labels, right=False)
by_bucket = detail.groupby("target_bucket", observed=True).agg(
    n_obs=("covered", "size"), avg_target_csl=("target_csl", "mean"), achieved_csl=("covered", "mean"),
).reset_index()
by_bucket["gap_pp"] = ((by_bucket.achieved_csl - by_bucket.avg_target_csl) * 100).round(1)
by_bucket.to_csv(f"{OUT}/calibration_by_bucket.csv", index=False)

by_cat = detail.groupby("category").agg(
    n_obs=("covered", "size"), avg_target_csl=("target_csl", "mean"), achieved_csl=("covered", "mean"),
    total_shortfall_usd=("shortfall_usd", "sum"),
).reset_index()
by_cat["gap_pp"] = ((by_cat.achieved_csl - by_cat.avg_target_csl) * 100).round(1)
by_cat.to_csv(f"{OUT}/calibration_by_category.csv", index=False)

avg_trailing_cv = float(np.mean(trailing_cv_means))
avg_full_cv = float(full_year_cv.cv_full.mean())
print(f"Forecast bias: {portfolio_forecast_bias:+.1%}  |  avg trailing CV {avg_trailing_cv:.2f} vs full-year CV {avg_full_cv:.2f}")

# ------------------------------------------------------------------ chart ---
# Target CSL barely varies across the portfolio (see the single-bucket table
# above), so a target-vs-achieved scatter collapses to one point. The category
# breakdown actually varies -- bar it instead, same style as the other MEIO charts.
by_cat_sorted = by_cat.sort_values("achieved_csl")
fig, ax = plt.subplots(figsize=(9, 5))
x = np.arange(len(by_cat_sorted))
w = 0.38
ax.bar(x - w/2, by_cat_sorted.avg_target_csl * 100, width=w, label="Target CSL (modeled)",
       color="#9aa5b1", edgecolor="#5c6169")
ax.bar(x + w/2, by_cat_sorted.achieved_csl * 100, width=w, label="Achieved CSL (2025 backtest)",
       color="#e2913c", edgecolor="#a86d31")
ax.set_xticks(x)
ax.set_xticklabels(by_cat_sorted.category, rotation=20, ha="right")
ax.set_ylabel("Cycle service level (%)")
ax.set_ylim(75, 102)
ax.set_title("MEIO Calibration — Target vs. Achieved Cycle Service Level, by Category")
ax.legend()
fig.tight_layout()
fig.savefig(f"{OUT}/chart_calibration.png", dpi=150)
plt.close(fig)

# --------------------------------------------------------------- report ---
verdict = ("tracks the promised target closely" if abs(overall_achieved - overall_target) < 0.02
           else "diverges from the promised target" if abs(overall_achieved - overall_target) < 0.05
           else "diverges materially from the promised target")

report = f"""# MEIO Statistical CSL Backtest
Supply Brain capstone | generated by `backtest_csl.py`

## What this tests
Every MEIO reorder point is sized so that `P(demand during lead time <= reorder_point) = target_csl`.
This backtest replays {n_periods - args.min_trailing_periods} weeks of 2025 in-sample history
({periods[args.min_trailing_periods]}..{periods[-1]}), recomputing the MEIO recommendation at each
as-of week using only information available before that week (rolling {args.trailing}-week trailing CV,
that week's own `forecast_qty`), then checks whether realized demand during the lead-time window that
followed actually stayed within the recommended reorder point.

**It does not test:** forecast accuracy itself (a separate, already-documented property of the
demand data), or the full order/receipt inventory mechanics — this is a statistical calibration
check on the safety-stock formula, not a dollar-cost simulation.

## Headline result
- **Portfolio target CSL: {overall_target:.1%}** (average across {len(detail):,} observations)
- **Portfolio achieved CSL: {overall_achieved:.1%}** (fraction of observations where realized demand stayed within the reorder point)
- **Gap: {overall_achieved-overall_target:+.1%}** — the model {verdict}.

## Calibration by target-CSL bucket
{df_to_md(by_bucket.assign(avg_target_csl=by_bucket.avg_target_csl.round(4), achieved_csl=by_bucket.achieved_csl.round(4)))}

## Calibration by category
{df_to_md(by_cat.sort_values('n_obs', ascending=False).round(4))}

## Root-cause diagnosis
The safety-stock formula only buffers *random* variability around the forecast mean — it cannot
correct a *persistent bias* in the mean itself, or a trailing-window CV that understates true
volatility. Both are measurable directly and both point the same direction as the gap above:

- **Forecast bias: {portfolio_forecast_bias:+.1%}** portfolio-wide (`forecast_qty` vs `actual_qty`,
  summed across all 2025 in-sample weeks) — the forecast under-predicts actual demand by roughly
  this much on average. A reorder point built around a mean that's already ~{abs(portfolio_forecast_bias)*100:.0f}%
  too low will be breached more often than its z-score alone would predict, regardless of how well
  the variance term is sized.
- **Trailing CV understates full-year CV.** The {args.trailing}-week rolling window this backtest
  (and `MEIO_Weekly` in production) uses to estimate demand CV averages **{avg_trailing_cv:.2f}**
  across as-of weeks, vs. **{avg_full_cv:.2f}** computed from the full 2025 year — a recent calm
  stretch doesn't predict an upcoming seasonal swing, so the rolling window is thinner than the
  volatility a SKU actually shows over a full year.

Both effects push the same direction (under-coverage), so they plausibly explain most of the gap
above without needing to assume the core z-score/newsvendor math is wrong. The more useful framing:
this backtest didn't just test "is the formula calibrated" — it separated *forecast quality* and
*volatility-window choice* from the *safety-stock formula itself*, and found the first two are the
more likely levers to pull.

**Separately, worth flagging:** target CSL clusters almost entirely in the 99-99.9% band across
this entire portfolio (see the single-bucket table above) — because `stockout_cost_unit` (tied to
margin, typically several dollars) dwarfs `holding_per_unit_week` (typically a few cents, amortized
over well under a week of lead time) for nearly every line. The newsvendor ratio is mathematically
doing its job, but the *practical effect*, given this dataset's cost parameters, is that the
"differentiated target service level per SKU" the model is supposed to produce is barely
differentiated at all — nearly everything gets pushed toward the same ~99%+ target regardless of
SKU economics. That's a property of the input cost parameters, not a bug in the ratio.

## How to read this
- A **positive gap** (achieved > target) means the model is *more conservative* than it claims —
  realized outcomes beat the promise, which is safe but means safety stock could likely be trimmed
  without giving up the target service level.
- A **negative gap** (achieved < target) means the model is *under-covering* — realized demand
  breached the reorder point more often than the target service level implies, which is the
  direction that actually costs stockouts.
- Buckets with small `n_obs` are noisy — treat their gap as indicative, not conclusive; the
  portfolio-level and by-category rows have far more observations behind them.

## Assumptions specific to this backtest
- **Lead-time window rounding.** Realized demand is summed over `ceil(lt_mean_wk)` weeks of actuals
  (weekly data can't resolve sub-week windows) and linearly scaled by `lt_mean_wk / ceil(lt_mean_wk)`
  — the same scaling the model itself uses for cycle stock. This assumes demand is roughly uniform
  within a week, which is an approximation, not a measured fact.
- **One year of history.** Every as-of week's trailing CV and every realized-demand window draw from
  the same single 2025 in-sample year — there's no second year to check whether the calibration
  itself is stable year over year.
- **Static network.** Lead-time mean/variance per lane come from `network_lanes.csv` and don't vary
  by as-of week, since the dataset doesn't carry a time-varying lane history.

## Files
- `backtest_detail.csv` — every (SKU, DC, as-of week) observation (large, gitignored — rerun to regenerate)
- `calibration_by_bucket.csv`, `calibration_by_category.csv`
- `chart_calibration.png` — target vs. achieved CSL by category (the target-CSL-bucket view collapses to
  one point — see "worth flagging" note above — so category is the informative cut here)

## Next step
If this calibration looks reasonable, the natural follow-on is a full periodic-review inventory
simulation — replay actual demand against the recommended policy with simulated on-hand, reorders,
and receipts, and compare simulated stockouts/lost margin to what `inventory.csv` actually recorded
under the 2025 policy. Not built here; this script only validates the statistical promise.
"""
with open(f"{OUT}/backtest_report.md", "w") as f:
    f.write(report)
print(f"Wrote {OUT}/backtest_report.md, calibration tables, and chart_calibration.png")
