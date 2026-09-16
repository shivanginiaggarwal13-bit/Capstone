"""
Supply Brain — Multi-Echelon Inventory Optimization (MEIO)
============================================================

Computes SKU x stocking-DC safety stock, reorder points and order-up-to
levels for the 2026 forward planning horizon, using a two-echelon
guaranteed-service-style stacking model on top of the Supply Brain
synthetic dataset (../input datasets/).

Network (from network_lanes.csv, restricted to whichever DCs actually
carry demand/inventory in demand_forecast.csv — 23 in the current extract,
8 RDC + 15 CDC):

    Plant --(0.5d)--> Plant DC --(primary lane)--> Regional DC --(primary lane)--> Customer DC

Method
------
1. Primary lane selection: for every stocking node, the upstream lane
   with the lowest mean lead time is treated as its replenishment route
   (single-sourcing assumption — standard simplification when actual
   sourcing flags aren't in the extract).
2. Cumulative replenishment lead time (mean, variance in days) to every
   RDC and CDC, built by walking Plant -> PDC -> RDC (-> CDC).
3. Demand mean/std per (SKU, DC, week) estimated from 2025 in-sample
   actuals; forward (2026) mean taken from forecast_qty, forward std
   scaled by the SKU's historical coefficient of variation.
4. RDC "total demand" = RDC's own direct (BULT) demand + the demand of
   every CDC for which that RDC is the primary source (echelon /
   installation-stock pooling) — RDCs must carry stock to replenish
   their downstream CDCs, not just serve their own direct customers.
5. Target cycle service level per SKU x DC derived from a newsvendor
   critical ratio: Cu = margin_usd * stockout_cost_multiplier (cost of a
   lost sale), Co = holding cost amortized over the lead time, where
   holding cost = storage_cost_per_unit_year_usd + unit_cost_usd *
   inventory_carrying_pct_annual (cost_parameters.csv, keyed by
   dc_id x subcategory — both terms vary by stocking location, not just
   by category). z = Phi^-1(Cu / (Cu + Co)).
6. Safety stock under demand AND lead-time uncertainty:
       SS = z * sqrt(LT_weeks * sigma_d^2 + mean_d^2 * sigma_LT_weeks^2)
   Reorder point = mean_d * LT_weeks + SS
   Order-up-to (base stock) = ROP + mean_d * review_period(=1 week)
7. Compare against the as-is safety_stock/reorder_point carried in
   inventory.csv (2025 policy) to quantify the working-capital delta.

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
inv = pd.read_csv(f"{DATA}/inventory.csv")

# demand_forecast.csv is actually (period, sku, dc, b2b_customer) grain -- ~5% of
# sku x dc x period combos are split across 2 B2B customers. Collapse to true
# DC-level weekly totals before anything else touches it, or per-customer rows
# leak through as duplicate SKU lines downstream.
dfc = (dfc.groupby(["period", "week_start_date", "horizon", "sku_id", "dc_id"], as_index=False)
       .agg(forecast_qty=("forecast_qty", "sum"), actual_qty=("actual_qty", lambda s: s.sum(min_count=1))))

active_dc = sorted(dfc["dc_id"].unique())
active_cdc = [d for d in active_dc if d.startswith("CDC")]
active_rdc = [d for d in active_dc if d.startswith("RDC")]
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

# cumulative (mean, var) days from Plant to each RDC
rdc_leadtime = {}
for rdc, row in pdc_to_rdc.iterrows():
    pdc = row.origin_node_id
    p2p = plant_to_pdc.loc[pdc]
    mean = p2p.lead_time_mean_days + row.lead_time_mean_days
    var = p2p.lead_time_std_days**2 + row.lead_time_std_days**2
    rdc_leadtime[rdc] = (mean, var)

# cumulative (mean, var) days from Plant to each CDC (via its primary RDC)
cdc_leadtime = {}
cdc_primary_rdc = {}
for cdc, row in rdc_to_cdc.iterrows():
    rdc = row.origin_node_id
    cdc_primary_rdc[cdc] = rdc
    r_mean, r_var = rdc_leadtime[rdc]
    mean = r_mean + row.lead_time_mean_days
    var = r_var + row.lead_time_std_days**2
    cdc_leadtime[cdc] = (mean, var)

print("\nPrimary RDC per CDC:")
for cdc, rdc in cdc_primary_rdc.items():
    print(f"  {cdc} <- {rdc}")

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
# cost_parameters.csv is keyed by (dc_id, subcategory), not category alone — storage
# cost and stockout multiplier both vary by stocking location. Holding cost per unit-year
# = location storage cost + (carrying % x unit cost); applied per node once dc_id is known
# (after the CDC/RDC split below), since it's no longer a flat per-SKU figure.
sku_c = sku[["sku_id", "category", "subcategory", "unit_cost_usd", "margin_usd"]].copy()

demand = demand.merge(sku_c, on="sku_id", how="left")

# ---------------------------------------------- CDC-level recommendations ---
cdc_rows = []
for cdc in active_cdc:
    lt_mean_days, lt_var_days = cdc_leadtime[cdc]
    lt_mean_wk = lt_mean_days / 7.0
    lt_std_wk = np.sqrt(lt_var_days) / 7.0
    d = demand[demand.dc_id == cdc].copy()
    d["lt_mean_wk"], d["lt_std_wk"] = lt_mean_wk, lt_std_wk
    cdc_rows.append(d)
cdc_demand = pd.concat(cdc_rows, ignore_index=True)

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
    lt_mean_days, lt_var_days = rdc_leadtime[rdc]
    merged["lt_mean_wk"] = lt_mean_days / 7.0
    merged["lt_std_wk"] = np.sqrt(lt_var_days) / 7.0
    rdc_rows.append(merged[["sku_id", "dc_id", "fwd_mean", "fwd_std", "lt_mean_wk", "lt_std_wk"]])
rdc_demand = pd.concat(rdc_rows, ignore_index=True)
rdc_demand = rdc_demand.merge(sku_c, on="sku_id", how="left")

# ------------------------------------------------------ combine & solve ---
node_cols = ["sku_id", "dc_id", "category", "subcategory", "fwd_mean", "fwd_std",
             "lt_mean_wk", "lt_std_wk", "unit_cost_usd", "margin_usd"]
all_nodes = pd.concat([cdc_demand[node_cols], rdc_demand[node_cols]], ignore_index=True)

# DC-specific cost join: holding cost = location storage cost + (carrying % x unit cost)
all_nodes = all_nodes.merge(cost_p, on=["dc_id", "subcategory"], how="left")
all_nodes["holding_per_unit_week"] = (
    all_nodes.storage_cost_per_unit_year_usd
    + all_nodes.unit_cost_usd * all_nodes.inventory_carrying_pct_annual / 100.0
) / 52.0
all_nodes["stockout_cost_unit"] = all_nodes.margin_usd * all_nodes.stockout_cost_multiplier

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

# ----------------------------------------- compare vs current (2025) policy ---
current = (inv.groupby(["sku_id", "dc_id"])
           .agg(current_safety_stock=("safety_stock", "mean"),
                current_reorder_point=("reorder_point", "mean"))
           .reset_index())
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
    lt_mean_wk=("lt_mean_wk", "first"),
).reset_index()
by_dc["delta_usd"] = by_dc.optimized_ss_usd - by_dc.current_ss_usd
by_dc.to_csv(f"{OUT}/meio_summary_by_dc.csv", index=False)

total_current = all_nodes.current_safety_stock_usd.sum()
total_opt = all_nodes.safety_stock_usd.sum()
print(f"\nTotal current SS (2025 policy): ${total_current:,.0f}")
print(f"Total optimized SS (MEIO 2026): ${total_opt:,.0f}")
print(f"Delta: ${total_opt - total_current:,.0f}  ({(total_opt/total_current-1)*100:.1f}%)")

# ------------------------------------------------------------------ charts ---
fig, ax = plt.subplots(figsize=(9, 5))
by_cat_sorted = by_cat.sort_values("optimized_ss_usd", ascending=False)
x = np.arange(len(by_cat_sorted))
w = 0.38
ax.bar(x - w/2, by_cat_sorted.current_ss_usd/1000, width=w, label="Current (2025 policy)", color="#9aa5b1", edgecolor="#5c6169")
ax.bar(x + w/2, by_cat_sorted.optimized_ss_usd/1000, width=w, label="MEIO optimized (2026)", color="#e2913c", edgecolor="#a86d31")
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
ax.bar(x - w/2, by_dc_sorted.current_ss_usd/1000, width=w, label="Current (2025 policy)", color="#9aa5b1", edgecolor="#5c6169")
ax.bar(x + w/2, by_dc_sorted.optimized_ss_usd/1000, width=w, label="MEIO optimized (2026)", color="#e2913c", edgecolor="#a86d31")
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
Supply Brain capstone | generated by `meio_optimizer.py`

## Network scope
- {len(active_rdc)} Regional DCs + {len(active_cdc)} Customer DCs actively carry demand/inventory in this dataset.
- Replenishment path: Plant -> Plant DC -> Regional DC (-> Customer DC), single-sourced
  via the fastest (min mean lead-time) available lane at each node.
- RDC demand pools its own direct (BULK) volume with the demand of every CDC it primary-sources
  (echelon / installation-stock pooling), so RDC safety stock reflects the full downstream buffer it must support.

## Method
1. Demand mean from 2026 `forecast_qty`; demand std scaled from each SKU's 2025 in-sample coefficient of variation.
2. Cumulative lead-time mean/variance built by walking the network graph, converted to weeks.
3. Target cycle service level per SKU-DC from a newsvendor critical ratio:
   `CR = stockout_cost / (stockout_cost + holding_cost_over_lead_time)`, clipped to [80%, 99.9%].
4. Safety stock = `z * sqrt(LT_weeks * sigma_demand^2 + demand_mean^2 * sigma_LT_weeks^2)`.
5. Reorder point = cycle stock + safety stock; order-up-to = reorder point + 1 week of demand (weekly review).

## Headline result
- Total current (2025 policy) safety-stock investment: **${total_current:,.0f}**
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
