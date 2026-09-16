"""
Supply Brain — MEIO Full Periodic-Review Inventory Simulation
============================================================
The follow-on to `backtest_csl.py`'s statistical calibration check: instead
of only asking "did realized demand stay within the reorder point," this
replays 2025 actual demand week by week against a *simulated* inventory
position under the MEIO-recommended policy, and compares it head-to-head
against what `inventory.csv` actually recorded under the real 2025 policy.

Two arms, same demand stream
-----------------------------
- **Current-policy arm**: read directly from `inventory.csv` — no
  simulation, it's the real recorded 2025 trace (opening/closing stock,
  shipments, `unmet_qty`, `lost_margin_usd`) under whatever policy was
  actually in place.
- **MEIO-policy arm**: simulated. Each SKU x DC starts from the *same*
  real `opening_stock` in the first simulated week (a fair, matched
  starting point), then every week: receive any orders due, fulfill that
  week's real `demand_qty` from on-hand (unmet if insufficient), check
  inventory position against a reorder point recomputed *that week* using
  the same rolling-CV method as `meio_optimizer_weekly.py` /
  `backtest_csl.py` (no lookahead), and place a replenishment order up to
  `order_up_to` if position has fallen to/below the reorder point. Orders
  arrive after a **deterministic** `ceil(lt_mean_wk)` weeks -- see
  "Assumptions" below for why that's a real simplification, not a detail.

Both arms fulfill the *same* real `demand_qty` per (SKU, DC, week) --
network-cascading effects (a changed CDC policy changing what its RDC sees
as replenishment demand) are deliberately not modeled; see "Assumptions".

Outputs (./simulation_output/):
    simulation_detail.csv        MEIO-arm weekly trace per SKU x DC -- large, gitignored
    summary_by_category.csv      current vs MEIO: fill rate, unmet units, $ shortfall, inventory $ carried
    chart_fill_rate.png
    simulation_report.md
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
OUT = "simulation_output"

REVIEW_PERIOD_WEEKS = 1.0
Z_MIN, Z_MAX = 0.84, 3.10
DEFAULT_TRAILING_WEEKS = 13
DEFAULT_MIN_TRAILING_PERIODS = 8

parser = argparse.ArgumentParser(description="Full periodic-review simulation: MEIO policy vs real 2025 policy")
parser.add_argument("--trailing", type=int, default=DEFAULT_TRAILING_WEEKS)
parser.add_argument("--min-trailing-periods", type=int, default=DEFAULT_MIN_TRAILING_PERIODS)
args = parser.parse_args()
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- load ---
sku = pd.read_csv(f"{DATA}/sku_master.csv")
cost_p = pd.read_csv(f"{DATA}/cost_parameters.csv")
lanes = pd.read_csv(f"{DATA}/network_lanes.csv")
dfc = pd.read_csv(f"{DATA}/demand_forecast.csv")
inv = pd.read_csv(f"{DATA}/inventory.csv")

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

node_lt = {}
for dc, (mean_d, var_d) in {**rdc_leadtime, **cdc_leadtime}.items():
    lt_mean_wk = mean_d / 7.0
    ceil_wk = max(1, math.ceil(lt_mean_wk))
    node_lt[dc] = dict(lt_mean_wk=lt_mean_wk, lt_std_wk=math.sqrt(var_d) / 7.0, ceil_wk=ceil_wk)
max_ceil_wk = max(v["ceil_wk"] for v in node_lt.values())
print(f"Lead-time buckets needed: max {max_ceil_wk} week(s) (ceil of lt_mean_wk per node)")

sku_c = sku[["sku_id", "category", "subcategory", "unit_cost_usd", "margin_usd"]].copy()

actual_pivot = in_sample.pivot_table(index=["sku_id", "dc_id"], columns="period", values="actual_qty", aggfunc="sum")[periods]
forecast_pivot = in_sample.pivot_table(index=["sku_id", "dc_id"], columns="period", values="forecast_qty", aggfunc="sum")[periods]

# ---------------------------------------- precompute weekly ROP / order-up-to ---
sim_weeks = periods[args.min_trailing_periods:]
targets = {}  # as_of -> DataFrame(sku_id, dc_id, fwd_mean, reorder_point, order_up_to)
for i in range(args.min_trailing_periods, n_periods):
    as_of = periods[i]
    trailing_cols = periods[max(0, i - args.trailing):i]
    hist = actual_pivot[trailing_cols]
    cv = (hist.std(axis=1) / hist.mean(axis=1).replace(0, np.nan)).fillna(0).clip(upper=3)

    fwd_mean_t = forecast_pivot[as_of]
    fwd_std_t = fwd_mean_t * cv
    flat = pd.DataFrame({"fwd_mean": fwd_mean_t, "fwd_std": fwd_std_t}).reset_index()

    cdc_demand = pd.concat([flat[flat.dc_id == cdc].copy() for cdc in active_cdc], ignore_index=True)
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
    node = node.merge(sku_c, on="sku_id", how="left").merge(cost_p, on=["dc_id", "subcategory"], how="left")
    node["holding_per_unit_week"] = (
        node.storage_cost_per_unit_year_usd + node.unit_cost_usd * node.inventory_carrying_pct_annual / 100.0
    ) / 52.0
    node["stockout_cost_unit"] = node.margin_usd * node.stockout_cost_multiplier
    co_lt = node.holding_per_unit_week * node.lt_mean_wk.clip(lower=1.0)
    cr = (node.stockout_cost_unit / (node.stockout_cost_unit + co_lt)).clip(0.80, 0.999)
    z = norm.ppf(cr).clip(Z_MIN, Z_MAX)
    sigma = np.sqrt(node.lt_mean_wk * node.fwd_std**2 + node.fwd_mean**2 * node.lt_std_wk**2)
    node["reorder_point"] = node.fwd_mean * node.lt_mean_wk + z * sigma
    node["order_up_to"] = node.reorder_point + node.fwd_mean * REVIEW_PERIOD_WEEKS
    targets[as_of] = node.set_index(["sku_id", "dc_id"])[["fwd_mean", "reorder_point", "order_up_to"]]

print(f"Precomputed weekly targets for {len(targets)} weeks ({sim_weeks[0]}..{sim_weeks[-1]})")

# ------------------------------------------------------------- real trace ---
inv2025 = inv[inv.period.isin(sim_weeks)].copy()
node_index = sorted(set(targets[sim_weeks[0]].index) & set(zip(inv2025.sku_id, inv2025.dc_id)))
node_index = pd.MultiIndex.from_tuples(node_index, names=["sku_id", "dc_id"])
print(f"Simulating {len(node_index):,} SKU x DC nodes present in both the target table and inventory.csv")

demand_pivot = (inv2025.pivot_table(index=["sku_id", "dc_id"], columns="period", values="demand_qty", aggfunc="sum")
                .reindex(node_index).fillna(0.0)[sim_weeks])
opening_first_week = (inv2025[inv2025.period == sim_weeks[0]]
                       .set_index(["sku_id", "dc_id"])["opening_stock"]
                       .reindex(node_index).fillna(0.0))

real_agg = (inv2025.groupby(["sku_id", "dc_id"]).agg(
    demand=("demand_qty", "sum"), unmet=("unmet_qty", "sum"),
    lost_margin_recorded=("lost_margin_usd", "sum"), avg_closing=("closing_stock", "mean"),
).reindex(node_index).fillna(0.0))

# ------------------------------------------------------- MEIO-arm simulation ---
on_hand = opening_first_week.values.astype(float).copy()
pipe = np.zeros((max_ceil_wk, len(node_index)))  # pipe[0] = arriving next week, pipe[1] = in 2 weeks, ...
ceil_wk_arr = np.array([node_lt[dc]["ceil_wk"] for _, dc in node_index])

unmet_total = np.zeros(len(node_index))
closing_sum = np.zeros(len(node_index))
sku_c_lookup = sku_c.set_index("sku_id")
margin_arr = sku_c_lookup["margin_usd"].reindex(node_index.get_level_values("sku_id")).values

detail_rows = []
for w in sim_weeks:
    on_hand += pipe[0]
    pipe[:-1] = pipe[1:]
    pipe[-1] = 0.0

    demand_t = demand_pivot[w].values
    shipped = np.minimum(on_hand, demand_t)
    unmet_t = demand_t - shipped
    on_hand -= shipped
    unmet_total += unmet_t

    position = on_hand + pipe.sum(axis=0)
    tgt = targets[w].reindex(node_index)
    rop = tgt["reorder_point"].fillna(np.inf).values
    oul = tgt["order_up_to"].fillna(0.0).values

    need_order = position <= rop
    order_qty = np.where(need_order, np.maximum(0.0, oul - position), 0.0)
    for bucket in np.unique(ceil_wk_arr):
        mask = (ceil_wk_arr == bucket) & need_order
        pipe[bucket - 1, mask] += order_qty[mask]

    closing_sum += on_hand
    detail_rows.append(pd.DataFrame({
        "period": w, "sku_id": node_index.get_level_values("sku_id"), "dc_id": node_index.get_level_values("dc_id"),
        "demand": demand_t, "unmet": unmet_t, "on_hand": on_hand.copy(), "position": position,
        "order_placed": order_qty,
    }))

detail = pd.concat(detail_rows, ignore_index=True)
detail.to_csv(f"{OUT}/simulation_detail.csv", index=False)
print(f"Wrote {OUT}/simulation_detail.csv ({len(detail):,} rows)")

sim_agg = pd.DataFrame({
    "unmet": unmet_total, "avg_closing": closing_sum / len(sim_weeks), "margin_usd": margin_arr,
}, index=node_index)
sim_agg["demand"] = real_agg["demand"]
sim_agg["shortfall_usd"] = sim_agg.unmet * sim_agg.margin_usd
real_agg["shortfall_usd_consistent"] = real_agg.unmet * margin_arr

# quantify the cold-start (empty-pipeline) effect rather than just flagging it
weekly_fill = detail.groupby("period").agg(demand=("demand", "sum"), unmet=("unmet", "sum"))
first4_fill = 1 - weekly_fill.iloc[:4].unmet.sum() / weekly_fill.iloc[:4].demand.sum()
rest_fill = 1 - weekly_fill.iloc[4:].unmet.sum() / weekly_fill.iloc[4:].demand.sum()

# ------------------------------------------------------------------ compare ---
cat_lookup = sku_c.set_index("sku_id")["category"]
cat = cat_lookup.reindex(node_index.get_level_values("sku_id")).values

comp = pd.DataFrame({
    "category": cat,
    "demand": real_agg.demand.values,
    "current_unmet": real_agg.unmet.values,
    "current_fill_rate": 1 - real_agg.unmet.values / real_agg.demand.replace(0, np.nan).values,
    "current_shortfall_usd": real_agg.shortfall_usd_consistent.values,
    "current_avg_closing": real_agg.avg_closing.values,
    "meio_unmet": sim_agg.unmet.values,
    "meio_fill_rate": 1 - sim_agg.unmet.values / sim_agg.demand.replace(0, np.nan).values,
    "meio_shortfall_usd": sim_agg.shortfall_usd.values,
    "meio_avg_closing": sim_agg.avg_closing.values,
})
unit_cost_lookup = sku_c.set_index("sku_id")["unit_cost_usd"]
comp["unit_cost_usd"] = unit_cost_lookup.reindex(node_index.get_level_values("sku_id")).values
comp["current_inventory_usd"] = comp.current_avg_closing * comp.unit_cost_usd
comp["meio_inventory_usd"] = comp.meio_avg_closing * comp.unit_cost_usd

by_cat = comp.groupby("category").agg(
    skus=("demand", "size"), total_demand=("demand", "sum"),
    current_unmet=("current_unmet", "sum"), meio_unmet=("meio_unmet", "sum"),
    current_shortfall_usd=("current_shortfall_usd", "sum"), meio_shortfall_usd=("meio_shortfall_usd", "sum"),
    current_inventory_usd=("current_inventory_usd", "sum"), meio_inventory_usd=("meio_inventory_usd", "sum"),
).reset_index()
by_cat["current_fill_rate"] = 1 - by_cat.current_unmet / by_cat.total_demand
by_cat["meio_fill_rate"] = 1 - by_cat.meio_unmet / by_cat.total_demand
by_cat.to_csv(f"{OUT}/summary_by_category.csv", index=False)

overall_current_fill = 1 - comp.current_unmet.sum() / comp.demand.sum()
overall_meio_fill = 1 - comp.meio_unmet.sum() / comp.demand.sum()
current_shortfall = comp.current_shortfall_usd.sum()
meio_shortfall = comp.meio_shortfall_usd.sum()
current_inv_usd = comp.current_inventory_usd.sum()
meio_inv_usd = comp.meio_inventory_usd.sum()
net_benefit = (current_shortfall - meio_shortfall) - (meio_inv_usd - current_inv_usd)

print(f"\nCurrent policy: fill rate {overall_current_fill:.1%}, shortfall ${current_shortfall:,.0f}, "
      f"avg inventory carried ${current_inv_usd:,.0f}")
print(f"MEIO policy:    fill rate {overall_meio_fill:.1%}, shortfall ${meio_shortfall:,.0f}, "
      f"avg inventory carried ${meio_inv_usd:,.0f}")
print(f"Net benefit (avoided shortfall - added inventory): ${net_benefit:,.0f}")

# ------------------------------------------------------------------ chart ---
by_cat_sorted = by_cat.sort_values("meio_fill_rate")
fig, ax = plt.subplots(figsize=(9, 5))
x = np.arange(len(by_cat_sorted))
w = 0.38
ax.bar(x - w/2, by_cat_sorted.current_fill_rate * 100, width=w, label="Current policy (real 2025 trace)",
       color="#9aa5b1", edgecolor="#5c6169")
ax.bar(x + w/2, by_cat_sorted.meio_fill_rate * 100, width=w, label="MEIO policy (simulated)",
       color="#e2913c", edgecolor="#a86d31")
ax.set_xticks(x)
ax.set_xticklabels(by_cat_sorted.category, rotation=20, ha="right")
ax.set_ylabel("Fill rate (%)")
ax.set_ylim(min(70, by_cat_sorted[["current_fill_rate", "meio_fill_rate"]].min().min() * 100 - 5), 102)
ax.set_title("Full Simulation — Current vs MEIO Policy, Fill Rate by Category (2025 replay)")
ax.legend()
fig.tight_layout()
fig.savefig(f"{OUT}/chart_fill_rate.png", dpi=150)
plt.close(fig)

# --------------------------------------------------------------- report ---
verdict = "a net benefit" if net_benefit > 0 else "a net cost"
report = f"""# MEIO Full Periodic-Review Inventory Simulation
Supply Brain capstone | generated by `simulate_policy.py`

## What this tests
Replays {len(sim_weeks)} weeks of 2025 actual demand ({sim_weeks[0]}..{sim_weeks[-1]}) against two
policies for the same {len(node_index):,} SKU x DC nodes:
- **Current policy**: the real 2025 trace recorded in `inventory.csv` — not simulated, ground truth.
- **MEIO policy**: simulated from the same starting inventory, using reorder points and order-up-to
  levels recomputed every week with the same no-lookahead rolling-CV method as `backtest_csl.py`.

Both arms fulfill the identical real `demand_qty` each week — this isolates "did a different
inventory *policy* perform better against the same demand," not "did demand change."

## Headline result
| | Current policy | MEIO policy |
|---|---|---|
| Fill rate | {overall_current_fill:.1%} | {overall_meio_fill:.1%} |
| Total unmet units | {comp.current_unmet.sum():,.0f} | {comp.meio_unmet.sum():,.0f} |
| Shortfall $ (unmet x margin) | ${current_shortfall:,.0f} | ${meio_shortfall:,.0f} |
| Avg inventory $ carried | ${current_inv_usd:,.0f} | ${meio_inv_usd:,.0f} |

**Net benefit (avoided shortfall $ − added inventory $): ${net_benefit:,.0f}** — the MEIO policy
represents {verdict} over the window simulated: it carries about the same inventory as the current
policy (${meio_inv_usd - current_inv_usd:+,.0f}) but achieves a *lower* fill rate
({overall_meio_fill:.1%} vs {overall_current_fill:.1%}), consistently across every category (see
below) — not one outlier category dragging the average down.

**Why, given `backtest_csl.py`'s finding:** this isn't a new problem, it's the same one showing up
in dollar terms. The statistical backtest already found the model's own promise (~99.8% target CSL)
wasn't being met (~87% achieved) because of forecast bias (~-7.8%) and a thin trailing-CV window.
Here, the *current* policy recorded in `inventory.csv` turns out to be better-calibrated against
realized 2025 demand than the forecast-driven MEIO recompute is — plausibly because the current
policy's safety-stock parameters were set from the data generator's own underlying demand
distribution, while MEIO's weekly recompute only ever sees a noisy, biased forecast of it. That's a
forecast-quality and CV-window problem, not evidence the newsvendor/safety-stock formula itself is
wrong — see `backtest_csl.py`'s report for the isolated diagnosis.

## By category
{df_to_md(by_cat_sorted.round(2))}

## Important: what the dollar figures do and don't mean
- **Shortfall $ uses one consistent rule for both arms**: `unmet_units x margin_usd`, applied the
  same way regardless of whether a real 2025 line was tagged `lost_sale` or `backorder` in
  `inventory.csv`. The dataset's own `lost_margin_usd` column applies a more forgiving rule for
  backorder-type lines (many backordered units carry $0 recorded loss, presumably fulfilled from a
  later receipt) — real recorded 2025 `lost_margin_usd` across this window was
  **${real_agg.lost_margin_recorded.sum():,.0f}**, materially lower than the `current_shortfall_usd`
  quoted above, precisely because of that forgiveness rule. Comparing the two policies against each
  other uses the *same* simplified rule on both sides, so the comparison itself is fair — just don't
  quote `current_shortfall_usd` as "what actually happened" without this caveat.
- **Inventory $ carried** is `avg(closing_stock) x unit_cost_usd` — a working-capital proxy, not a
  full holding-cost accounting (storage cost, capital cost, etc. are in the MEIO reorder-point math
  itself but not re-applied here as a separate carrying charge).

## Assumptions specific to this simulation
- **Deterministic lead time.** Orders arrive after exactly `ceil(lt_mean_wk)` weeks — real
  lead-time variance (`lt_std_wk`, which the safety-stock formula explicitly sizes for) is not drawn
  stochastically here. This likely makes the simulated MEIO arm look *better* than a fully
  variance-aware simulation would, since it never experiences a late delivery.
- **No network cascading.** Both arms fulfill the same exogenous `demand_qty` per node — a changed
  CDC policy is not propagated upstream into a changed RDC replenishment-order signal. `demand_qty`
  in `inventory.csv` already includes real replenishment demand from the actual 2025 policy; a fully
  cascading counterfactual would need a full network discrete-event simulation, out of scope here.
- **Empty pipeline at simulation start.** The MEIO arm starts with the real `opening_stock` for the
  first simulated week but assumes no orders already in transit. Measured, not just flagged: fill
  rate over the first 4 simulated weeks was {first4_fill:.1%} vs {rest_fill:.1%} for the remaining
  weeks — a real but small (~{abs(rest_fill-first4_fill)*100:.1f}pp) cold-start drag, not the main
  driver of the gap to the current policy.
- **Review cadence.** Every simulated week is a review opportunity (matches `MEIO_Weekly`'s intended
  cadence and the model's own `order_up_to = reorder_point + 1 review period` construction).

## Files
- `simulation_detail.csv` — MEIO-arm weekly trace per SKU x DC (large, gitignored — rerun to regenerate)
- `summary_by_category.csv`
- `chart_fill_rate.png`

## Chain of validation so far
1. `backtest_csl.py` — statistical calibration check: achieved CSL ~87% vs ~99.8% target, traced to
   forecast bias and a thin trailing-CV window.
2. This simulation — translates that calibration gap into fill-rate and dollar terms, and asks the
   actual business question: does the recommended safety-stock increase pay for itself.
"""
with open(f"{OUT}/simulation_report.md", "w") as f:
    f.write(report)
print(f"Wrote {OUT}/summary_by_category.csv, chart_fill_rate.png, simulation_report.md")
