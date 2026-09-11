"""
Supply Brain — Multi-Echelon Inventory Optimization (MEIO)
============================================================

Computes SKU x stocking-DC safety stock, reorder points and order-up-to
levels for the 2026 forward planning horizon, using a two-echelon
guaranteed-service-style stacking model on top of the Supply Brain
synthetic dataset (../input datasets/).

Network (from network_lanes.csv, restricted to the 23 DCs that actually
carry demand/inventory in this dataset):

    Plant --(0.5d)--> Plant DC --(SKU-specific primary-plant lane)--> Regional DC --(fastest lane)--> Customer DC

v2 update (Sep 2026 dataset refresh) — two things changed structurally and
this script now reflects both:

  * Regional DCs no longer buy from one globally-fastest Plant DC. Each SKU
    is sourced from whichever plant is *qualified to make it*
    (`sku_plant_sourcing.csv`, `is_primary=True`), and every Plant DC has a
    lane to every Regional DC (`network_lanes.csv`) — so the Plant DC leg,
    and therefore the cumulative lead time, is now SKU-specific, not just
    node-specific. The Regional DC -> Customer DC leg is unchanged
    (fastest available lane, global per CDC).
  * `cost_parameters.csv` moved from a 5-row category table to a 675-row
    (dc_id x subcategory) table with a genuinely location- and
    density-aware holding-cost model:
        holding_cost_per_unit_year = storage_cost_per_unit_year_usd
                                   + unit_cost_usd * inventory_carrying_pct_annual / 100
    (per `input datasets/DATASET_DOCUMENTATION.md` §5.3 — this replaces
    `sku_master.holding_cost_per_unit_year_usd`, which is documented as
    just the national average of this same calculation, kept "for
    convenience" but explicitly superseded for anything location-aware.)
  * The "current policy" baseline moved from `inventory.csv` (now a weekly
    flow/shortfall ledger with no safety_stock/reorder_point columns) to
    `opening_inventory_2026.csv` (the 2026-W01 snapshot, which does carry
    them).

Method
------
1. Primary-plant lane selection: each SKU's primary plant (is_primary=True
   in sku_plant_sourcing.csv) fixes its Plant DC; every Plant DC has a
   direct lane to every Regional DC, so the Plant DC -> Regional DC leg is
   a direct lookup, not a "pick the fastest origin" search. The Regional DC
   -> Customer DC leg keeps the original fastest-available-lane logic
   (real per-SKU sourcing data isn't published at that leg).
2. Cumulative replenishment lead time (mean, variance in days) to every
   (SKU, RDC) and (SKU, CDC) pair, walking Plant -> PDC -> RDC (-> CDC).
3. Demand mean/std per (SKU, DC, week) estimated from 2025 in-sample
   actuals; forward (2026) mean taken from forecast_qty, forward std
   scaled by the SKU's historical coefficient of variation.
4. RDC "total demand" = RDC's own direct (BULK) volume + the demand of
   every CDC for which that RDC is the primary source (echelon /
   installation-stock pooling) — RDCs must carry stock to replenish
   their downstream CDCs, not just serve their own direct customers.
5. Target cycle service level per SKU-DC derived from a newsvendor
   critical ratio: Cu = margin_usd * stockout_cost_multiplier (cost of a
   lost sale), Co = holding_cost_per_unit_year_usd (location- and
   density-aware, see above) amortized over the lead time.
   z = Phi^-1(Cu / (Cu + Co)).
6. Safety stock under demand AND lead-time uncertainty:
       SS = z * sqrt(LT_weeks * sigma_demand^2 + demand_mean^2 * sigma_LT_weeks^2)
   Reorder point = cycle stock + safety stock
   Order-up-to (base stock) = ROP + mean_d * review_period(=1 week)
7. Compare against the 2026-W01 safety_stock/reorder_point carried in
   opening_inventory_2026.csv (the as-is baseline policy) to quantify the
   working-capital delta.

Outputs (./output/):
    meio_recommendations.csv   SKU x DC line-level recommendations
    meio_summary_by_category.csv
    meio_summary_by_dc.csv
    meio_report.md
    chart_ss_by_category.png
    chart_current_vs_optimized.png
"""

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

DATA = "../../input datasets"
OUT = "output"

REVIEW_PERIOD_WEEKS = 1.0     # weekly review/replenishment cycle
Z_MIN, Z_MAX = 0.84, 3.10     # clip service level to [80%, 99.9%]

# ---------------------------------------------------------------- load ---
sku = pd.read_csv(f"{DATA}/sku_master.csv")
cost_p = pd.read_csv(f"{DATA}/cost_parameters.csv")
lanes = pd.read_csv(f"{DATA}/network_lanes.csv")
dfc = pd.read_csv(f"{DATA}/demand_forecast.csv")
opening_inv = pd.read_csv(f"{DATA}/opening_inventory_2026.csv")
sourcing = pd.read_csv(f"{DATA}/sku_plant_sourcing.csv")

active_dc = sorted(dfc["dc_id"].unique())
active_cdc = [d for d in active_dc if d.startswith("CDC")]
active_rdc = [d for d in active_dc if d.startswith("RDC")]
print(f"Active stocking DCs: {len(active_rdc)} RDC + {len(active_cdc)} CDC = {len(active_dc)}")

# --------------------------------------------------- primary lane graph ---
# Plant -> Plant DC: fixed 1:1 per plant.
plant_pdc = lanes[(lanes.origin_node_type == "PLANT") & (lanes.destination_node_type == "PLANT_DC")]
plant_to_pdc = plant_pdc.set_index("origin_node_id")[
    ["destination_node_id", "lead_time_mean_days", "lead_time_std_days"]
]

# Each SKU's primary plant (sku_plant_sourcing.csv) fixes its Plant DC.
primary_plant = (sourcing[sourcing.is_primary.astype(str) == "True"]
                  .set_index("sku_id")["plant_id"])
missing_primary = sku[~sku.sku_id.isin(primary_plant.index)]
if len(missing_primary):
    print(f"WARNING: {len(missing_primary)} SKUs have no primary-plant row; dropped from MEIO scope.")

# Plant DC -> Regional DC: every PDC has a direct lane to every RDC now —
# a table lookup keyed on (pdc, rdc), not a "fastest origin" search.
pdc_rdc = lanes[(lanes.origin_node_type == "PLANT_DC") & (lanes.destination_node_type == "REGIONAL_DC")]
pdc_rdc_idx = pdc_rdc.set_index(["origin_node_id", "destination_node_id"])[
    ["lead_time_mean_days", "lead_time_std_days"]
]

# Regional DC -> Customer DC: unchanged — fastest available lane, per CDC.
rdc_cdc = lanes[(lanes.origin_node_type == "REGIONAL_DC") & (lanes.destination_node_type == "CUSTOMER_DC")]
rdc_cdc = rdc_cdc[rdc_cdc.origin_node_id.isin(active_rdc) & rdc_cdc.destination_node_id.isin(active_cdc)]
best_rdc_cdc = (rdc_cdc.sort_values("lead_time_mean_days")
                .drop_duplicates("destination_node_id", keep="first")
                .set_index("destination_node_id"))
cdc_primary_rdc = best_rdc_cdc["origin_node_id"].to_dict()
print("\nPrimary RDC per CDC:")
for cdc, rdc in cdc_primary_rdc.items():
    print(f"  {cdc} <- {rdc}")

# ------------------------------------------- SKU-specific cumulative LT ---
sku_ids = sorted(primary_plant.index)
rdc_lt_rows = []
for s in sku_ids:
    plant_id = primary_plant.loc[s]
    p2p = plant_to_pdc.loc[plant_id]
    pdc = p2p.destination_node_id
    for rdc in active_rdc:
        leg = pdc_rdc_idx.loc[(pdc, rdc)]
        mean = p2p.lead_time_mean_days + leg.lead_time_mean_days
        var = p2p.lead_time_std_days**2 + leg.lead_time_std_days**2
        rdc_lt_rows.append((s, rdc, mean, var))
rdc_lt = pd.DataFrame(rdc_lt_rows, columns=["sku_id", "dc_id", "lt_mean_days", "lt_var_days"])

cdc_lt_rows = []
rdc_lt_lookup = rdc_lt.set_index(["sku_id", "dc_id"])
for s in sku_ids:
    for cdc in active_cdc:
        rdc = cdc_primary_rdc[cdc]
        r_mean, r_var = rdc_lt_lookup.loc[(s, rdc), ["lt_mean_days", "lt_var_days"]]
        leg = best_rdc_cdc.loc[cdc]
        mean = r_mean + leg.lead_time_mean_days
        var = r_var + leg.lead_time_std_days**2
        cdc_lt_rows.append((s, cdc, mean, var))
cdc_lt = pd.DataFrame(cdc_lt_rows, columns=["sku_id", "dc_id", "lt_mean_days", "lt_var_days"])

# ------------------------------------------------- demand stats per SKU/DC ---
in_sample = dfc[dfc.horizon == "in_sample"]
forward = dfc[dfc.horizon == "forward"]

hist = (in_sample.groupby(["sku_id", "dc_id"])["actual_qty"]
        .agg(hist_mean="mean", hist_std="std").reset_index())
hist["cv"] = (hist.hist_std / hist.hist_mean.replace(0, np.nan)).fillna(0).clip(upper=3)

fwd = (forward.groupby(["sku_id", "dc_id"])["forecast_qty"]
       .agg(fwd_mean="mean").reset_index())

demand = fwd.merge(hist[["sku_id", "dc_id", "cv"]], on=["sku_id", "dc_id"], how="left")
demand["cv"] = demand["cv"].fillna(demand["cv"].median())
demand["fwd_std"] = demand["fwd_mean"] * demand["cv"]

# ------------------------------------------------- cost & service level ---
# holding_cost_per_unit_year = storage leg (location + density) + capital
# leg (value-based) — location-aware per DATASET_DOCUMENTATION.md §5.3.
sku_cost = sku[["sku_id", "category", "subcategory", "unit_cost_usd", "margin_usd"]]
demand = demand.merge(sku_cost, on="sku_id", how="left")
demand = demand.merge(cost_p, on=["dc_id", "subcategory"], how="left")
demand["holding_per_unit_year"] = (
    demand.storage_cost_per_unit_year_usd
    + demand.unit_cost_usd * demand.inventory_carrying_pct_annual / 100.0
)
demand["holding_per_unit_week"] = demand.holding_per_unit_year / 52.0
demand["stockout_cost_unit"] = demand.margin_usd * demand.stockout_cost_multiplier

# ---------------------------------------------- CDC-level recommendations ---
cdc_demand = demand[demand.dc_id.isin(active_cdc)].merge(cdc_lt, on=["sku_id", "dc_id"], how="left")
cdc_demand["lt_mean_wk"] = cdc_demand.lt_mean_days / 7.0
cdc_demand["lt_std_wk"] = np.sqrt(cdc_demand.lt_var_days) / 7.0

# ---------------------------------------------- RDC pooled total demand ---
# RDC total = its own direct demand + demand of every CDC it primary-sources
rdc_rows = []
for rdc in active_rdc:
    own = demand[demand.dc_id == rdc][["sku_id", "fwd_mean", "fwd_std"]].rename(
        columns={"fwd_mean": "own_mean", "fwd_std": "own_std"})
    served_cdcs = [c for c, r in cdc_primary_rdc.items() if r == rdc]
    if served_cdcs:
        down = demand[demand.dc_id.isin(served_cdcs)].groupby("sku_id").agg(
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
    rdc_rows.append(merged[["sku_id", "dc_id", "fwd_mean", "fwd_std"]])
rdc_demand = pd.concat(rdc_rows, ignore_index=True)
rdc_demand = rdc_demand.merge(
    sku_cost, on="sku_id", how="left",
).merge(cost_p, on=["dc_id", "subcategory"], how="left")
rdc_demand["holding_per_unit_year"] = (
    rdc_demand.storage_cost_per_unit_year_usd
    + rdc_demand.unit_cost_usd * rdc_demand.inventory_carrying_pct_annual / 100.0
)
rdc_demand["holding_per_unit_week"] = rdc_demand.holding_per_unit_year / 52.0
rdc_demand["stockout_cost_unit"] = rdc_demand.margin_usd * rdc_demand.stockout_cost_multiplier
rdc_demand = rdc_demand.merge(rdc_lt, on=["sku_id", "dc_id"], how="left")
rdc_demand["lt_mean_wk"] = rdc_demand.lt_mean_days / 7.0
rdc_demand["lt_std_wk"] = np.sqrt(rdc_demand.lt_var_days) / 7.0

# ------------------------------------------------------ combine & solve ---
cols = ["sku_id", "dc_id", "category", "fwd_mean", "fwd_std", "lt_mean_wk", "lt_std_wk",
        "unit_cost_usd", "holding_per_unit_week", "stockout_cost_unit"]
all_nodes = pd.concat([cdc_demand[cols], rdc_demand[cols]], ignore_index=True)
all_nodes = all_nodes.dropna(subset=["lt_mean_wk", "holding_per_unit_week", "stockout_cost_unit"])

# newsvendor critical ratio -> service level -> z
co_lt = all_nodes.holding_per_unit_week * all_nodes.lt_mean_wk.clip(lower=1.0)
cr = all_nodes.stockout_cost_unit / (all_nodes.stockout_cost_unit + co_lt)
cr = cr.clip(0.80, 0.999)
all_nodes["target_csl"] = cr
all_nodes["z"] = norm.ppf(cr).clip(Z_MIN, Z_MAX)

# safety stock under combined demand + lead-time variance
all_nodes["sigma_lt_demand"] = np.sqrt(
    all_nodes.lt_mean_wk * all_nodes.fwd_std**2
    + (all_nodes.fwd_mean**2) * all_nodes.lt_std_wk**2
)
all_nodes["safety_stock"] = (all_nodes.z * all_nodes.sigma_lt_demand).round(1)
all_nodes["cycle_stock"] = (all_nodes.fwd_mean * all_nodes.lt_mean_wk).round(1)
all_nodes["reorder_point"] = (all_nodes.cycle_stock + all_nodes.safety_stock).round(1)
all_nodes["order_up_to"] = (all_nodes.reorder_point + all_nodes.fwd_mean * REVIEW_PERIOD_WEEKS).round(1)
all_nodes["safety_stock_usd"] = (all_nodes.safety_stock * all_nodes.unit_cost_usd).round(2)

# ----------------------------------------- compare vs current (2026-W01) policy ---
# opening_inventory_2026.csv is the 2026-W01 snapshot — the as-is baseline
# policy carried into the forward horizon (inventory.csv, the weekly
# flow/shortfall ledger, no longer carries safety_stock/reorder_point).
current = opening_inv[opening_inv.dc_id.isin(active_rdc + active_cdc)][
    ["sku_id", "dc_id", "safety_stock", "reorder_point"]
].rename(columns={"safety_stock": "current_safety_stock", "reorder_point": "current_reorder_point"})
all_nodes = all_nodes.merge(current, on=["sku_id", "dc_id"], how="left")
all_nodes["current_safety_stock_usd"] = all_nodes.current_safety_stock * all_nodes.unit_cost_usd
all_nodes["ss_delta_usd"] = all_nodes.safety_stock_usd - all_nodes.current_safety_stock_usd

all_nodes = all_nodes.sort_values(["dc_id", "sku_id"]).reset_index(drop=True)
all_nodes.to_csv(f"{OUT}/meio_recommendations.csv", index=False)
print(f"\nWrote {OUT}/meio_recommendations.csv  ({len(all_nodes):,} SKU x DC rows)")

# --------------------------------------------------------------- summaries ---
by_cat = all_nodes.groupby("category").agg(
    skus=("sku_id", "nunique"),
    optimized_ss_usd=("safety_stock_usd", "sum"),
    current_ss_usd=("current_safety_stock_usd", "sum"),
    avg_target_csl=("target_csl", "mean"),
).reset_index()
by_cat["delta_usd"] = by_cat.optimized_ss_usd - by_cat.current_ss_usd
by_cat["delta_pct"] = (by_cat.delta_usd / by_cat.current_ss_usd * 100).round(1)
by_cat.to_csv(f"{OUT}/meio_summary_by_category.csv", index=False)

by_dc = all_nodes.groupby("dc_id").agg(
    skus=("sku_id", "nunique"),
    optimized_ss_usd=("safety_stock_usd", "sum"),
    current_ss_usd=("current_safety_stock_usd", "sum"),
    lt_mean_wk=("lt_mean_wk", "mean"),
).reset_index()
by_dc["delta_usd"] = by_dc.optimized_ss_usd - by_dc.current_ss_usd
by_dc.to_csv(f"{OUT}/meio_summary_by_dc.csv", index=False)

total_current = all_nodes.current_safety_stock_usd.sum()
total_opt = all_nodes.safety_stock_usd.sum()
print(f"\nTotal current SS (2026-W01 policy): ${total_current:,.0f}")
print(f"Total optimized SS (MEIO 2026):     ${total_opt:,.0f}")
print(f"Delta: ${total_opt - total_current:,.0f}  ({(total_opt/total_current-1)*100:.1f}%)")

# ------------------------------------------------------------------ charts ---
fig, ax = plt.subplots(figsize=(9, 5))
by_cat_sorted = by_cat.sort_values("optimized_ss_usd", ascending=False)
x = np.arange(len(by_cat_sorted))
w = 0.38
ax.bar(x - w/2, by_cat_sorted.current_ss_usd/1000, width=w, label="Current (2026-W01 policy)", color="#9aa5b1")
ax.bar(x + w/2, by_cat_sorted.optimized_ss_usd/1000, width=w, label="MEIO optimized (2026)", color="#2b6cb0")
ax.set_xticks(x)
ax.set_xticklabels(by_cat_sorted.category, rotation=20, ha="right")
ax.set_ylabel("Safety stock value ($000)")
ax.set_title("Safety Stock Investment: Current vs MEIO-Optimized, by Category")
ax.legend()
fig.tight_layout()
fig.savefig(f"{OUT}/chart_ss_by_category.png", dpi=150)
plt.close(fig)

fig, ax = plt.subplots(figsize=(9, 5))
by_dc_sorted = by_dc.sort_values("optimized_ss_usd", ascending=False)
x = np.arange(len(by_dc_sorted))
ax.bar(x - w/2, by_dc_sorted.current_ss_usd/1000, width=w, label="Current (2026-W01 policy)", color="#9aa5b1")
ax.bar(x + w/2, by_dc_sorted.optimized_ss_usd/1000, width=w, label="MEIO optimized (2026)", color="#2b6cb0")
ax.set_xticks(x)
ax.set_xticklabels(by_dc_sorted.dc_id, rotation=45, ha="right")
ax.set_ylabel("Safety stock value ($000)")
ax.set_title("Safety Stock Investment: Current vs MEIO-Optimized, by Stocking DC")
ax.legend()
fig.tight_layout()
fig.savefig(f"{OUT}/chart_current_vs_optimized.png", dpi=150)
plt.close(fig)

print(f"Wrote charts + summaries to {OUT}/")

# --------------------------------------------------------------- report ---
report = f"""# MEIO — Multi-Echelon Inventory Optimization
Supply Brain capstone | generated by `meio_optimizer.py` (v2 — refreshed dataset, {pd.Timestamp.now().strftime('%Y-%m-%d')})

## Network scope
- {len(active_rdc)} Regional DCs + {len(active_cdc)} Customer DCs actively carry demand/inventory in this dataset.
- Replenishment path: Plant -> Plant DC -> Regional DC (-> Customer DC).
  Plant DC is fixed per SKU by its primary plant (`sku_plant_sourcing.csv`); every Plant DC
  has a direct lane to every Regional DC, so this leg is SKU-specific, not a single global
  fastest-origin choice. The Regional DC -> Customer DC leg is single-sourced via the
  fastest available lane (global per CDC — real per-SKU sourcing isn't published at that leg).
- RDC demand pools its own direct (BULK) volume with the demand of every CDC it primary-sources
  (echelon / installation-stock pooling), so RDC safety stock reflects the full downstream buffer it must support.

## Method
1. Demand mean from 2026 `forecast_qty`; demand std scaled from each SKU's 2025 in-sample coefficient of variation.
2. Cumulative lead-time mean/variance built by walking the network graph per SKU (via its primary plant), converted to weeks.
3. Holding cost per unit-year = `storage_cost_per_unit_year_usd` (location + product density) +
   `unit_cost_usd x inventory_carrying_pct_annual` (value-based capital/shrink leg) — both from
   `cost_parameters.csv` at (dc_id, subcategory) grain, per `DATASET_DOCUMENTATION.md` §5.3.
4. Target cycle service level per SKU-DC from a newsvendor critical ratio:
   `CR = stockout_cost / (stockout_cost + holding_cost_over_lead_time)`, clipped to [80%, 99.9%].
5. Safety stock = `z * sqrt(LT_weeks * sigma_demand^2 + demand_mean^2 * sigma_LT_weeks^2)`.
6. Reorder point = cycle stock + safety stock; order-up-to = reorder point + 1 week of demand (weekly review).
7. Baseline for comparison: the 2026-W01 snapshot in `opening_inventory_2026.csv`
   (`inventory.csv` is now a weekly flow/shortfall ledger and no longer carries a
   safety_stock / reorder_point column).

## Headline result
- Total current (2026-W01 policy) safety-stock investment: **${total_current:,.0f}**
- Total MEIO-optimized (2026) safety-stock investment: **${total_opt:,.0f}**
- Net change: **${total_opt - total_current:,.0f}** ({(total_opt/total_current-1)*100:+.1f}%)

## By category ($, safety-stock value)
{df_to_md(by_cat_sorted[['category','current_ss_usd','optimized_ss_usd','delta_pct']].round(0))}

## By stocking DC ($, safety-stock value)
{df_to_md(by_dc_sorted[['dc_id','skus','lt_mean_wk','current_ss_usd','optimized_ss_usd']].round(1))}

## Files
- `meio_recommendations.csv` — SKU x DC safety stock / ROP / order-up-to for the 2026 horizon
- `meio_summary_by_category.csv`, `meio_summary_by_dc.csv`
- `chart_ss_by_category.png`, `chart_current_vs_optimized.png`
"""
with open(f"{OUT}/meio_report.md", "w") as f:
    f.write(report)
print(f"Wrote {OUT}/meio_report.md")
