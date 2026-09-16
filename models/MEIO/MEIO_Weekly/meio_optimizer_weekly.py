"""
Supply Brain — MEIO Weekly
============================================================
Weekly-cadence version of `../meio_optimizer.py`.

The committed MEIO model is a once-a-year exercise: it averages the whole
2026 forecast horizon into a single flat safety-stock / reorder-point /
order-up-to number per SKU x stocking-DC. That hides week-to-week
seasonality and promo lift, and gives planners nothing to act on between
annual refreshes.

This script turns the same method into something you re-run every week:

  python meio_optimizer_weekly.py --as-of 2026-W10

Each run:
1. Uses that week's own `forecast_qty` (not a 52-week average) for every
   SKU x DC in a rolling look-ahead window starting at --as-of, so
   seasonality/promo lift shows up decision-week by decision-week.
2. Re-estimates each SKU's demand coefficient of variation from a rolling
   trailing window of *realized* actuals ending just before --as-of
   (default 13 weeks), so the variability estimate refreshes as new
   actuals land — not once at the start of the year. If fewer than
   `--min-trailing-periods` realized weeks are available in that window
   for a SKU x DC (true today for every 2026 as-of date, since this
   synthetic extract has no realized 2026 actuals yet — see note below),
   it falls back to the full in-sample CV, i.e. the original annual
   method, for that line only.
3. Recomputes the same newsvendor-critical-ratio safety stock / ROP /
   order-up-to logic as the annual model, per period, over the
   look-ahead window, and flags the as-of period's row as the
   `is_decision_week`.
4. Appends this run's category-level totals to a persistent
   `output/meio_weekly_history.csv` log and redraws a trend chart from
   it, so successive weekly runs build an actual time series instead of
   a one-off snapshot.
5. Reports week-over-week deltas vs the prior logged run so a planner
   knows what changed and whether it crosses a review threshold.

Data limitation (flagged, not glossed over)
---------------------------------------------
`demand_forecast.csv` ships one year of in-sample actuals (2025) and one
year of forward forecast (2026) with `actual_qty` left null for every
forward row — there are no realized 2026 outcomes in this extract yet.
That means the rolling trailing-actuals window described in step 2 will,
for every as-of date in 2026, fall back to the full 2025 in-sample CV
(step 2's fallback path) until a live weekly feed starts populating
`actual_qty` for elapsed 2026 weeks. The code is written against
`week_start_date`, not against the `in_sample`/`forward` flag, so the
day that feed exists this script starts using it automatically — no
changes needed. Until then, what actually varies week to week here is
the seasonality/promo shape in `forecast_qty`, which is real and already
priced in.

Network/lead-time/service-level method is otherwise identical to
`../meio_optimizer.py` — see that file's docstring for the full
methodology (echelon pooling, newsvendor critical ratio, etc.).

Outputs (./output/):
    <as_of>/meio_recommendations_weekly.csv   SKU x DC x week look-ahead lines
    <as_of>/meio_summary_by_category.csv
    <as_of>/meio_summary_by_dc.csv
    <as_of>/meio_weekly_report.md
    <as_of>/chart_ss_by_category.png
    meio_weekly_history.csv                   one row per (run, category) — appended every run
    chart_ss_trend.png                        redrawn every run from the history log
"""

import argparse
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
OUT_BASE = "output"

REVIEW_PERIOD_WEEKS = 1.0     # weekly review/replenishment cycle
Z_MIN, Z_MAX = 0.84, 3.10     # clip service level to [80%, 99.9%]

DEFAULT_LOOKAHEAD_WEEKS = 8
DEFAULT_TRAILING_WEEKS = 13
DEFAULT_MIN_TRAILING_PERIODS = 8
WOW_FLAG_PCT = 10.0            # flag category SS $ moves bigger than this since last logged run

# --------------------------------------------------------------- CLI ---
parser = argparse.ArgumentParser(description="Weekly-cadence MEIO run")
parser.add_argument("--as-of", dest="as_of", default=None,
                     help="Decision week, e.g. 2026-W10. Defaults to the first forward period.")
parser.add_argument("--lookahead", type=int, default=DEFAULT_LOOKAHEAD_WEEKS,
                     help=f"Weeks of forward look-ahead to compute alongside the decision week (default {DEFAULT_LOOKAHEAD_WEEKS}).")
parser.add_argument("--trailing", type=int, default=DEFAULT_TRAILING_WEEKS,
                     help=f"Trailing weeks of realized actuals used to refresh each SKU's demand CV (default {DEFAULT_TRAILING_WEEKS}).")
parser.add_argument("--min-trailing-periods", type=int, default=DEFAULT_MIN_TRAILING_PERIODS,
                     help=f"Minimum realized weeks required in the trailing window before it's trusted over the in-sample fallback (default {DEFAULT_MIN_TRAILING_PERIODS}).")
args = parser.parse_args()

# ---------------------------------------------------------------- load ---
sku = pd.read_csv(f"{DATA}/sku_master.csv")
cost_p = pd.read_csv(f"{DATA}/cost_parameters.csv")
lanes = pd.read_csv(f"{DATA}/network_lanes.csv")
dfc = pd.read_csv(f"{DATA}/demand_forecast.csv")
inv = pd.read_csv(f"{DATA}/inventory.csv")

# demand_forecast.csv is actually (period, sku, dc, b2b_customer) grain -- ~5% of
# sku x dc x period combos are split across 2 B2B customers. Collapse to true
# DC-level weekly totals before anything else touches it: this script iterates
# per period, so an un-collapsed duplicate SKU row would survive into the node
# tables, double-count in rollups, and fan out the RDC-pooling merge.
dfc = (dfc.groupby(["period", "week_start_date", "horizon", "sku_id", "dc_id"], as_index=False)
       .agg(forecast_qty=("forecast_qty", "sum"), actual_qty=("actual_qty", lambda s: s.sum(min_count=1))))

dfc["week_start_date"] = pd.to_datetime(dfc["week_start_date"])
period_calendar = (dfc[["period", "week_start_date"]].drop_duplicates()
                   .sort_values("week_start_date").reset_index(drop=True))
all_periods = period_calendar["period"].tolist()
forward_periods = sorted(dfc.loc[dfc.horizon == "forward", "period"].unique(),
                          key=lambda p: period_calendar.set_index("period").loc[p, "week_start_date"])

as_of = args.as_of or forward_periods[0]
if as_of not in all_periods:
    raise SystemExit(f"--as-of '{as_of}' is not a known period in demand_forecast.csv")

as_of_idx = all_periods.index(as_of)
lookahead_periods = all_periods[as_of_idx: as_of_idx + args.lookahead]
if not lookahead_periods:
    raise SystemExit(f"No periods available at/after {as_of} for a look-ahead window")

trailing_periods = all_periods[max(0, as_of_idx - args.trailing): as_of_idx]

OUT = f"{OUT_BASE}/{as_of}"
os.makedirs(OUT, exist_ok=True)

active_dc = sorted(dfc["dc_id"].unique())
active_cdc = [d for d in active_dc if d.startswith("CDC")]
active_rdc = [d for d in active_dc if d.startswith("RDC")]
print(f"As-of: {as_of} | look-ahead: {lookahead_periods[0]}..{lookahead_periods[-1]} "
      f"({len(lookahead_periods)} wk) | trailing CV window: {len(trailing_periods)} wk")
print(f"Active stocking DCs: {len(active_rdc)} RDC + {len(active_cdc)} CDC = {len(active_dc)}")

# --------------------------------------------------- primary lane graph ---
def primary_upstream(dest_ids, dest_type, allowed_origins=None):
    """For each destination node, pick the min-mean-lead-time inbound lane."""
    sub = lanes[lanes.destination_node_type == dest_type]
    if allowed_origins is not None:
        sub = sub[sub.origin_node_id.isin(allowed_origins)]
    sub = sub[sub.destination_node_id.isin(dest_ids)]
    best = sub.sort_values("lead_time_mean_days").drop_duplicates("destination_node_id", keep="first")
    return best.set_index("destination_node_id")[
        ["origin_node_id", "lead_time_mean_days", "lead_time_std_days"]
    ]

pdc_ids = lanes.loc[lanes.destination_node_type == "PLANT_DC", "destination_node_id"].unique()
plant_to_pdc = primary_upstream(pdc_ids, "PLANT_DC")
pdc_to_rdc = primary_upstream(active_rdc, "REGIONAL_DC", allowed_origins=pdc_ids)
rdc_to_cdc = primary_upstream(active_cdc, "CUSTOMER_DC", allowed_origins=active_rdc)

rdc_leadtime = {}
for rdc, row in pdc_to_rdc.iterrows():
    pdc = row.origin_node_id
    p2p = plant_to_pdc.loc[pdc]
    mean = p2p.lead_time_mean_days + row.lead_time_mean_days
    var = p2p.lead_time_std_days**2 + row.lead_time_std_days**2
    rdc_leadtime[rdc] = (mean, var)

cdc_leadtime = {}
cdc_primary_rdc = {}
for cdc, row in rdc_to_cdc.iterrows():
    rdc = row.origin_node_id
    cdc_primary_rdc[cdc] = rdc
    r_mean, r_var = rdc_leadtime[rdc]
    mean = r_mean + row.lead_time_mean_days
    var = r_var + row.lead_time_std_days**2
    cdc_leadtime[cdc] = (mean, var)

# ---------------------------------------------- rolling demand CV per SKU x DC ---
# Fallback CV: same method as the annual model — all in-sample 2025 actuals.
in_sample = dfc[dfc.horizon == "in_sample"]
fallback_hist = (in_sample.groupby(["sku_id", "dc_id"])["actual_qty"]
                 .agg(hist_mean="mean", hist_std="std").reset_index())
fallback_hist["cv_fallback"] = (fallback_hist.hist_std / fallback_hist.hist_mean.replace(0, np.nan)).fillna(0).clip(upper=3)

# Rolling CV: realized actuals strictly inside the trailing window ending at as-of.
trailing = dfc[dfc.period.isin(trailing_periods) & dfc.actual_qty.notna()]
trailing_hist = (trailing.groupby(["sku_id", "dc_id"])["actual_qty"]
                  .agg(trail_mean="mean", trail_std="std", trail_n="count").reset_index())
trailing_hist["cv_trailing"] = (trailing_hist.trail_std / trailing_hist.trail_mean.replace(0, np.nan)).fillna(0).clip(upper=3)

cv_table = fallback_hist[["sku_id", "dc_id", "cv_fallback"]].merge(
    trailing_hist[["sku_id", "dc_id", "cv_trailing", "trail_n"]], on=["sku_id", "dc_id"], how="left")
cv_table["trail_n"] = cv_table["trail_n"].fillna(0)
cv_table["cv_source"] = np.where(cv_table.trail_n >= args.min_trailing_periods, "trailing", "fallback_in_sample")
cv_table["cv"] = np.where(cv_table.cv_source == "trailing", cv_table.cv_trailing, cv_table.cv_fallback)
cv_table["cv"] = cv_table["cv"].fillna(cv_table["cv"].median())
n_trailing_lines = (cv_table.cv_source == "trailing").sum()
print(f"CV source: {n_trailing_lines:,} SKU x DC lines used the rolling trailing window "
      f"({args.min_trailing_periods}+ realized weeks); {len(cv_table) - n_trailing_lines:,} fell back to in-sample.")

# ------------------------------------------------- cost & service level ---
# cost_parameters.csv is keyed by (dc_id, subcategory) in the current dataset, not by
# category alone — storage cost and stockout multiplier both vary by stocking location.
# Holding cost per unit-year = location storage cost + (carrying % x unit cost), applied
# per node below once we know which DC a demand line is sitting at.
sku_c = sku[["sku_id", "category", "subcategory", "unit_cost_usd", "margin_usd"]].copy()

# ----------------------------------------- compare vs current (2025) policy ---
current = (inv.groupby(["sku_id", "dc_id"])
           .agg(current_safety_stock=("safety_stock", "mean"),
                current_reorder_point=("reorder_point", "mean"))
           .reset_index())

# --------------------------------------------------- per-period solve loop ---
period_frames = []
fwd = dfc[dfc.period.isin(lookahead_periods)][["period", "sku_id", "dc_id", "forecast_qty"]].rename(
    columns={"forecast_qty": "fwd_mean"})

for period in lookahead_periods:
    p_fwd = fwd[fwd.period == period].merge(cv_table[["sku_id", "dc_id", "cv", "cv_source"]],
                                             on=["sku_id", "dc_id"], how="left")
    p_fwd["cv"] = p_fwd["cv"].fillna(cv_table["cv"].median())
    p_fwd["fwd_std"] = p_fwd["fwd_mean"] * p_fwd["cv"]
    p_fwd = p_fwd.merge(sku_c, on="sku_id", how="left")

    # CDC nodes: demand as-is
    cdc_rows = []
    for cdc in active_cdc:
        lt_mean_days, lt_var_days = cdc_leadtime[cdc]
        d = p_fwd[p_fwd.dc_id == cdc].copy()
        d["lt_mean_wk"] = lt_mean_days / 7.0
        d["lt_std_wk"] = np.sqrt(lt_var_days) / 7.0
        cdc_rows.append(d)
    cdc_demand = pd.concat(cdc_rows, ignore_index=True)

    # RDC nodes: pooled (own direct + every CDC it primary-sources)
    rdc_rows = []
    for rdc in active_rdc:
        own = p_fwd[p_fwd.dc_id == rdc][["sku_id", "fwd_mean", "fwd_std"]].rename(
            columns={"fwd_mean": "own_mean", "fwd_std": "own_std"})
        served_cdcs = [c for c, r in cdc_primary_rdc.items() if r == rdc]
        if served_cdcs:
            down = p_fwd[p_fwd.dc_id.isin(served_cdcs)].groupby("sku_id").agg(
                down_mean=("fwd_mean", "sum"),
                down_var=("fwd_std", lambda s: (s**2).sum()),
            ).reset_index()
        else:
            down = pd.DataFrame(columns=["sku_id", "down_mean", "down_var"])
        merged = pd.merge(own, down, on="sku_id", how="outer").fillna(0.0)
        for col in ["own_mean", "own_std", "down_mean", "down_var"]:
            merged[col] = merged[col].astype(float)
        merged["fwd_mean"] = merged.own_mean + merged.down_mean
        merged["fwd_std"] = np.sqrt(merged.own_std**2 + merged.down_var)
        merged["dc_id"] = rdc
        lt_mean_days, lt_var_days = rdc_leadtime[rdc]
        merged["lt_mean_wk"] = lt_mean_days / 7.0
        merged["lt_std_wk"] = np.sqrt(lt_var_days) / 7.0
        rdc_rows.append(merged[["sku_id", "dc_id", "fwd_mean", "fwd_std", "lt_mean_wk", "lt_std_wk"]])
    rdc_demand = pd.concat(rdc_rows, ignore_index=True)
    rdc_demand = rdc_demand.merge(sku_c, on="sku_id", how="left")

    node_cols = ["sku_id", "dc_id", "category", "subcategory", "fwd_mean", "fwd_std",
                 "lt_mean_wk", "lt_std_wk", "unit_cost_usd", "margin_usd"]
    period_nodes = pd.concat([cdc_demand[node_cols], rdc_demand[node_cols]], ignore_index=True)
    period_nodes["period"] = period
    period_frames.append(period_nodes)

all_nodes = pd.concat(period_frames, ignore_index=True)
all_nodes = all_nodes.merge(period_calendar, on="period", how="left")
all_nodes = all_nodes.merge(cost_p, on=["dc_id", "subcategory"], how="left")
all_nodes["holding_per_unit_week"] = (
    all_nodes.storage_cost_per_unit_year_usd
    + all_nodes.unit_cost_usd * all_nodes.inventory_carrying_pct_annual / 100.0
) / 52.0
all_nodes["stockout_cost_unit"] = all_nodes.margin_usd * all_nodes.stockout_cost_multiplier
all_nodes["is_decision_week"] = all_nodes["period"] == as_of
period_to_weeks_ahead = {p: i for i, p in enumerate(lookahead_periods)}
all_nodes["weeks_ahead"] = all_nodes["period"].map(period_to_weeks_ahead)

# newsvendor critical ratio -> service level -> z
co_lt = all_nodes.holding_per_unit_week * all_nodes.lt_mean_wk.clip(lower=1.0)
cr = all_nodes.stockout_cost_unit / (all_nodes.stockout_cost_unit + co_lt)
cr = cr.clip(0.80, 0.999)
all_nodes["target_csl"] = cr
all_nodes["z"] = norm.ppf(cr).clip(Z_MIN, Z_MAX)

all_nodes["sigma_lt_demand"] = np.sqrt(
    all_nodes.lt_mean_wk * all_nodes.fwd_std**2
    + (all_nodes.fwd_mean**2) * all_nodes.lt_std_wk**2
)
all_nodes["safety_stock"] = (all_nodes.z * all_nodes.sigma_lt_demand).round(1)
all_nodes["cycle_stock"] = (all_nodes.fwd_mean * all_nodes.lt_mean_wk).round(1)
all_nodes["reorder_point"] = (all_nodes.cycle_stock + all_nodes.safety_stock).round(1)
all_nodes["order_up_to"] = (all_nodes.reorder_point + all_nodes.fwd_mean * REVIEW_PERIOD_WEEKS).round(1)
all_nodes["safety_stock_usd"] = (all_nodes.safety_stock * all_nodes.unit_cost_usd).round(2)

all_nodes = all_nodes.merge(current, on=["sku_id", "dc_id"], how="left")
all_nodes["current_safety_stock_usd"] = all_nodes.current_safety_stock * all_nodes.unit_cost_usd
all_nodes["ss_delta_usd"] = all_nodes.safety_stock_usd - all_nodes.current_safety_stock_usd

all_nodes = all_nodes.sort_values(["period", "dc_id", "sku_id"]).reset_index(drop=True)
all_nodes.to_csv(f"{OUT}/meio_recommendations_weekly.csv", index=False)
print(f"\nWrote {OUT}/meio_recommendations_weekly.csv  ({len(all_nodes):,} SKU x DC x week rows)")

# --------------------------------------------------------------- summaries (decision week only) ---
decision = all_nodes[all_nodes.is_decision_week]

by_cat = decision.groupby("category").agg(
    skus=("sku_id", "nunique"),
    optimized_ss_usd=("safety_stock_usd", "sum"),
    current_ss_usd=("current_safety_stock_usd", "sum"),
    avg_target_csl=("target_csl", "mean"),
).reset_index()
by_cat["delta_usd"] = by_cat.optimized_ss_usd - by_cat.current_ss_usd
by_cat["delta_pct"] = (by_cat.delta_usd / by_cat.current_ss_usd * 100).round(1)
by_cat.to_csv(f"{OUT}/meio_summary_by_category.csv", index=False)

by_dc = decision.groupby("dc_id").agg(
    skus=("sku_id", "nunique"),
    optimized_ss_usd=("safety_stock_usd", "sum"),
    current_ss_usd=("current_safety_stock_usd", "sum"),
    lt_mean_wk=("lt_mean_wk", "first"),
).reset_index()
by_dc["delta_usd"] = by_dc.optimized_ss_usd - by_dc.current_ss_usd
by_dc.to_csv(f"{OUT}/meio_summary_by_dc.csv", index=False)

total_current = decision.current_safety_stock_usd.sum()
total_opt = decision.safety_stock_usd.sum()
print(f"\nDecision week {as_of} — total current SS (2025 policy): ${total_current:,.0f}")
print(f"Decision week {as_of} — total MEIO-optimized SS: ${total_opt:,.0f}")
print(f"Delta: ${total_opt - total_current:,.0f}  ({(total_opt/total_current-1)*100:.1f}%)")

# ------------------------------------------------------- history log + WoW ---
history_path = f"{OUT_BASE}/meio_weekly_history.csv"
run_log = by_cat[["category", "skus", "optimized_ss_usd", "current_ss_usd", "delta_pct"]].copy()
run_log.insert(0, "run_as_of", as_of)
as_of_date = period_calendar.set_index("period").loc[as_of, "week_start_date"]
run_log.insert(1, "week_start_date", as_of_date.date().isoformat())

prev_run_by_cat = None
if os.path.exists(history_path):
    hist_log = pd.read_csv(history_path)
    hist_log = hist_log[hist_log.run_as_of != as_of]  # re-running the same as-of overwrites, not duplicates
    prior_runs = sorted(hist_log.run_as_of.unique(),
                         key=lambda p: period_calendar.set_index("period").loc[p, "week_start_date"]
                         if p in all_periods else pd.Timestamp.min)
    prior_runs_before = [p for p in prior_runs
                         if p in all_periods and all_periods.index(p) < as_of_idx]
    if prior_runs_before:
        prev_run = prior_runs_before[-1]
        prev_run_by_cat = hist_log[hist_log.run_as_of == prev_run].set_index("category")["optimized_ss_usd"]
    hist_log = pd.concat([hist_log, run_log], ignore_index=True)
else:
    hist_log = run_log
hist_log.to_csv(history_path, index=False)

wow_lines = []
if prev_run_by_cat is not None:
    for _, r in by_cat.iterrows():
        prev_val = prev_run_by_cat.get(r.category)
        if prev_val is None or prev_val == 0:
            continue
        wow_pct = (r.optimized_ss_usd / prev_val - 1) * 100
        flag = " ⚠ review" if abs(wow_pct) >= WOW_FLAG_PCT else ""
        wow_lines.append(f"- **{r.category}**: {wow_pct:+.1f}% vs prior logged run{flag}")

# ------------------------------------------------------------------ charts ---
w = 0.38
fig, ax = plt.subplots(figsize=(9, 5))
by_cat_sorted = by_cat.sort_values("optimized_ss_usd", ascending=False)
x = np.arange(len(by_cat_sorted))
ax.bar(x - w/2, by_cat_sorted.current_ss_usd/1000, width=w, label="Current (2025 policy)", color="#9aa5b1", edgecolor="#5c6169")
ax.bar(x + w/2, by_cat_sorted.optimized_ss_usd/1000, width=w, label=f"MEIO optimized ({as_of})", color="#e2913c", edgecolor="#a86d31")
ax.set_xticks(x)
ax.set_xticklabels(by_cat_sorted.category, rotation=20, ha="right")
ax.set_ylabel("Safety stock value ($000)")
ax.set_title(f"Safety Stock Investment: Current vs MEIO-Optimized — decision week {as_of}")
ax.legend()
fig.tight_layout()
fig.savefig(f"{OUT}/chart_ss_by_category.png", dpi=150)
plt.close(fig)

# trend chart across every logged run so far
fig, ax = plt.subplots(figsize=(9, 5))
for cat, grp in hist_log.sort_values("week_start_date").groupby("category"):
    ax.plot(grp.week_start_date, grp.optimized_ss_usd/1000, marker="o", label=cat)
ax.set_ylabel("Optimized safety stock ($000)")
ax.set_title("MEIO Weekly — optimized safety stock by category, across logged runs")
ax.legend(fontsize=8)
ax.tick_params(axis="x", rotation=45)
fig.tight_layout()
fig.savefig(f"{OUT_BASE}/chart_ss_trend.png", dpi=150)
plt.close(fig)

print(f"Wrote charts + summaries to {OUT}/, updated {history_path} and {OUT_BASE}/chart_ss_trend.png")

# --------------------------------------------------------------- report ---
wow_block = "\n".join(wow_lines) if wow_lines else "_No prior logged run before this as-of date yet — nothing to compare._"
lookahead_note = (
    f"Recommendations also cover the {len(lookahead_periods) - 1} weeks after {as_of} "
    f"({lookahead_periods[1]}..{lookahead_periods[-1]}) for planning visibility, "
    f"but the numbers below are the decision-week ({as_of}) figures."
    if len(lookahead_periods) > 1 else ""
)

report = f"""# MEIO Weekly — decision week {as_of}
Supply Brain capstone | generated by `meio_optimizer_weekly.py --as-of {as_of}`

## What's different from the annual model
The committed `../meio_optimizer.py` produces one flat SKU x DC safety-stock
number for all of 2026. This run instead:
- uses {as_of}'s own forecast (not a 52-week average), so seasonality/promo
  lift show up week to week;
- refreshes each SKU's demand CV from a rolling {args.trailing}-week trailing
  window of realized actuals ({n_trailing_lines:,} of {len(cv_table):,} SKU x DC
  lines qualified; the rest fell back to the full in-sample CV — see the
  script docstring for why that's expected until 2026 actuals start landing);
- logs this run's category totals to `meio_weekly_history.csv` and flags
  week-over-week moves of {WOW_FLAG_PCT:.0f}%+ for review.

{lookahead_note}

## Week-over-week vs prior logged run
{wow_block}

## Decision-week headline ({as_of})
- Total current (2025 policy) safety-stock investment: **${total_current:,.0f}**
- Total MEIO-optimized safety-stock investment: **${total_opt:,.0f}**
- Net change: **${total_opt - total_current:,.0f}** ({(total_opt/total_current-1)*100:+.1f}%)

## By category ($, safety-stock value, decision week only)
{df_to_md(by_cat_sorted[['category','current_ss_usd','optimized_ss_usd','delta_pct']].round(0))}

## By stocking DC ($, safety-stock value, decision week only)
{df_to_md(by_dc.sort_values('optimized_ss_usd', ascending=False)[['dc_id','skus','lt_mean_wk','current_ss_usd','optimized_ss_usd']].round(1))}

## Files
- `meio_recommendations_weekly.csv` — SKU x DC x week rows for {as_of}..{lookahead_periods[-1]}; filter `is_decision_week` for this week's action
- `meio_summary_by_category.csv`, `meio_summary_by_dc.csv` — decision-week only
- `chart_ss_by_category.png` — this run
- `../{os.path.basename(history_path)}`, `../chart_ss_trend.png` — cross-run history (all as-of dates run so far)

## How to run this weekly
```
python meio_optimizer_weekly.py --as-of 2026-W11
```
Advance `--as-of` by one ISO week each cadence. Re-running the same `--as-of`
overwrites that run's row in the history log rather than duplicating it.
"""
with open(f"{OUT}/meio_weekly_report.md", "w") as f:
    f.write(report)
print(f"Wrote {OUT}/meio_weekly_report.md")
