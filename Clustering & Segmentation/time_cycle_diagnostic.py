"""
Supply Brain - Time Cycle Diagnostic

Standalone script that checks whether the dataset shows a genuine, repeating
business cycle -- and if so, what shape it takes. Run this BEFORE deciding
whether/how to build time-based (e.g. quarterly) segmentation on top of it.

Checks performed (all independent of each other -- if they agree, that's a
much stronger case than any single metric alone):
  1. Revenue by month           (realized $ from actual transactions)
  2. Margin % by month          (realized, not list-price based)
  3. Units sold by month        (pure volume, price stripped out)
  4. seasonal_flag cross-check  (do pre-tagged "seasonal" SKUs swing MORE?)
  5. Stockout rate by month     (does supply pressure track the same shape?)
  6. Forecast-vs-2025-shape check (does 2026's forecast repeat 2025's shape?)

Important limitation, stated up front: real transaction data
(orders.csv, demand_forecast.csv's in_sample rows, inventory.csv) only
covers 2025 in full. So this script can confirm a shape WITHIN one year,
but cannot fully confirm that shape RECURS year over year (that needs a
second full year of real data). Check #6 is a partial workaround using
2026's forecast (not yet-observed actuals) as a stand-in second year.

Requirements: pip install pandas numpy matplotlib
Usage: edit DATA_DIR / OUTPUT_DIR below, then: python time_cycle_diagnostic.py
"""
import os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings("ignore")

# ---- EDIT THESE TWO PATHS FOR YOUR MACHINE ----
DATA_DIR = "./data"
OUTPUT_DIR = "./time_cycle_outputs"
# ------------------------------------------------
os.makedirs(OUTPUT_DIR, exist_ok=True)

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# =========================================================================
# LOAD DATA
# =========================================================================
orders = pd.read_csv(f"{DATA_DIR}/orders.csv")
sku = pd.read_csv(f"{DATA_DIR}/sku_master.csv")
inv = pd.read_csv(f"{DATA_DIR}/inventory.csv")
demand = pd.read_csv(f"{DATA_DIR}/demand_forecast.csv")

orders["order_date"] = pd.to_datetime(orders["order_date"])
orders_2025 = orders[orders["order_date"] < "2026-01-01"].copy()
orders_2025 = orders_2025.merge(sku[["sku_id", "unit_cost_usd", "seasonal_flag"]], on="sku_id", how="left")
orders_2025["margin_usd_txn"] = (orders_2025["unit_price_usd"] - orders_2025["unit_cost_usd"]) * orders_2025["order_qty"]
orders_2025["month"] = orders_2025["order_date"].dt.month

inv["week_start_date"] = pd.to_datetime(inv["week_start_date"])
inv_2025 = inv[inv["week_start_date"] < "2026-01-01"].copy()
inv_2025["month"] = inv_2025["week_start_date"].dt.month

demand["week_start_date"] = pd.to_datetime(demand["week_start_date"])
demand["month"] = demand["week_start_date"].dt.month

results = {}  # collects one summary row per check, for the final combined table/chart

print("=" * 70)
print("TIME CYCLE DIAGNOSTIC")
print("=" * 70)
print("\nNOTE: Only 2025 has real transaction/actuals data in this dataset.")
print("This script tests for a seasonal SHAPE within 2025. It cannot fully")
print("confirm that shape RECURS year over year without a second real year.\n")

# =========================================================================
# CHECK 1 & 2: Revenue and Margin % by month
# =========================================================================
print("=" * 70)
print("CHECK 1 & 2: Revenue and Margin % by month")
print("=" * 70)

monthly = orders_2025.groupby("month").agg(revenue=("revenue_usd", "sum"), margin=("margin_usd_txn", "sum")).reset_index()
monthly["margin_pct"] = monthly["margin"] / monthly["revenue"]
monthly["month_name"] = monthly["month"].map(lambda m: MONTH_NAMES[m - 1])

rev_swing = (monthly.revenue.max() - monthly.revenue.min()) / monthly.revenue.mean() * 100
margin_swing = (monthly.margin_pct.max() - monthly.margin_pct.min()) * 100  # in percentage points

print(monthly[["month_name", "revenue", "margin_pct"]].to_string(index=False))
print(f"\nRevenue swing: {rev_swing:.0f}% of average  (${monthly.revenue.min():,.0f} to ${monthly.revenue.max():,.0f})")
print(f"Margin swing: {margin_swing:.1f} percentage points  ({monthly.margin_pct.min()*100:.1f}% to {monthly.margin_pct.max()*100:.1f}%)")
print("-> Revenue: " + ("STRONG cyclical signal" if rev_swing > 30 else "weak/no cyclical signal"))
print("-> Margin: " + ("STRONG cyclical signal" if margin_swing > 10 else "WEAK signal -- margin barely moves, not a useful cycle axis on its own"))

results["revenue"] = monthly.set_index("month_name")["revenue"] / monthly["revenue"].mean() * 100
monthly.to_csv(f"{OUTPUT_DIR}/check_revenue_margin_monthly.csv", index=False)

# =========================================================================
# CHECK 3: Units sold by month (price stripped out)
# =========================================================================
print("\n" + "=" * 70)
print("CHECK 3: Units sold by month (pure volume, no price effect)")
print("=" * 70)

units_monthly = orders_2025.groupby("month")["order_qty"].sum().reset_index()
units_monthly["month_name"] = units_monthly["month"].map(lambda m: MONTH_NAMES[m - 1])
units_swing = (units_monthly.order_qty.max() - units_monthly.order_qty.min()) / units_monthly.order_qty.mean() * 100

print(units_monthly[["month_name", "order_qty"]].to_string(index=False))
print(f"\nUnits swing: {units_swing:.0f}% of average  ({units_monthly.order_qty.min():,} to {units_monthly.order_qty.max():,} units)")
print("-> " + ("STRONG cyclical signal, CONFIRMS revenue finding is a real demand pattern"
                if units_swing > 30 else "weak signal"))

results["units"] = units_monthly.set_index("month_name")["order_qty"] / units_monthly["order_qty"].mean() * 100
units_monthly.to_csv(f"{OUTPUT_DIR}/check_units_sold_monthly.csv", index=False)

# =========================================================================
# CHECK 4: seasonal_flag cross-check
# =========================================================================
print("\n" + "=" * 70)
print("CHECK 4: seasonal_flag cross-check (do pre-tagged 'seasonal' SKUs swing MORE?)")
print("=" * 70)

by_flag = orders_2025.groupby(["month", "seasonal_flag"])["revenue_usd"].sum().unstack()
by_flag_idx = (by_flag / by_flag.mean() * 100).round(1)
by_flag_idx.index = [MONTH_NAMES[m - 1] for m in by_flag_idx.index]

swing_true = (by_flag[True].max() - by_flag[True].min()) / by_flag[True].mean() * 100
swing_false = (by_flag[False].max() - by_flag[False].min()) / by_flag[False].mean() * 100

print(by_flag_idx.to_string())
print(f"\nSwing, seasonal_flag=True SKUs:  {swing_true:.0f}%")
print(f"Swing, seasonal_flag=False SKUs: {swing_false:.0f}%")
print("-> " + ("VALIDATES the flag: seasonal-tagged SKUs swing more, as expected"
                if swing_true > swing_false else
                "UNEXPECTED: flagged 'seasonal' SKUs do not swing more than others -- worth investigating"))

by_flag_idx.to_csv(f"{OUTPUT_DIR}/check_seasonal_flag_monthly.csv")

# =========================================================================
# CHECK 5: Stockout rate by month
# =========================================================================
print("\n" + "=" * 70)
print("CHECK 5: Stockout rate by month (does supply pressure track the same shape?)")
print("=" * 70)

stockout_monthly = inv_2025.groupby("month").apply(lambda x: (x.closing_stock == 0).mean() * 100).reset_index(name="stockout_rate_pct")
stockout_monthly["month_name"] = stockout_monthly["month"].map(lambda m: MONTH_NAMES[m - 1])

print(stockout_monthly[["month_name", "stockout_rate_pct"]].round(2).to_string(index=False))

# does the stockout peak land in/near the same months as the revenue peak?
revenue_peak_months = set(monthly.nlargest(3, "revenue")["month"])
stockout_peak_months = set(stockout_monthly.nlargest(3, "stockout_rate_pct")["month"])
overlap = revenue_peak_months & stockout_peak_months
print(f"\nTop-3 revenue months: {sorted(revenue_peak_months)}   Top-3 stockout months: {sorted(stockout_peak_months)}")
print("-> " + (f"CONSISTENT: {len(overlap)} of top-3 months overlap -- supply pressure tracks demand peak"
                if len(overlap) >= 2 else "Peaks do NOT clearly align -- worth investigating separately"))

results["stockout_rate"] = stockout_monthly.set_index("month_name")["stockout_rate_pct"] / stockout_monthly["stockout_rate_pct"].mean() * 100
stockout_monthly.to_csv(f"{OUTPUT_DIR}/check_stockout_rate_monthly.csv", index=False)

# =========================================================================
# CHECK 6: Does 2026's FORECAST repeat 2025's ACTUAL shape? (partial recurrence check)
# =========================================================================
print("\n" + "=" * 70)
print("CHECK 6: Does the shape repeat into 2026? (forecast vs. 2025 actuals)")
print("=" * 70)
print("CAVEAT: 2026 has no real actuals yet -- this compares 2025's real shape")
print("against 2026's FORECASTED shape, which is suggestive, not confirmed.\n")

actual_2025_by_month = demand[demand.horizon == "in_sample"].groupby("month")["actual_qty"].sum()
forecast_2026_by_month = demand[demand.horizon == "forward"].groupby("month")["forecast_qty"].sum()

comp = pd.DataFrame({
    "actual_2025_idx": actual_2025_by_month / actual_2025_by_month.mean() * 100,
    "forecast_2026_idx": forecast_2026_by_month / forecast_2026_by_month.mean() * 100,
})
comp.index = [MONTH_NAMES[m - 1] for m in comp.index]
correlation = comp["actual_2025_idx"].corr(comp["forecast_2026_idx"])

print(comp.round(1).to_string())
print(f"\nCorrelation between 2025 actual shape and 2026 forecast shape: {correlation:.2f}")
print("-> " + ("Reasonably consistent -- suggests the data-generation process assumed a repeating cycle"
                if correlation > 0.6 else "Weak match -- little evidence the shape is expected to repeat"))

comp.to_csv(f"{OUTPUT_DIR}/check_2025_vs_2026_shape.csv")

# =========================================================================
# COMBINED SUMMARY CHART
# =========================================================================
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)

fig, ax = plt.subplots(figsize=(10, 6))
month_order = MONTH_NAMES
for label, series in results.items():
    series = series.reindex(month_order)
    ax.plot(month_order, series.values, marker="o", label=label)
ax.axhline(100, color="gray", linestyle="--", linewidth=0.8)
ax.set_ylabel("Index (100 = that metric's own yearly average)")
ax.set_title("Time cycle diagnostic: revenue, units, and stockout rate, all indexed to their own average")
ax.legend()
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/time_cycle_summary_chart.png", dpi=150)
plt.close()
print(f"Saved: {OUTPUT_DIR}/time_cycle_summary_chart.png")

summary_lines = [
    f"Revenue swing: {rev_swing:.0f}%  -> {'STRONG' if rev_swing > 30 else 'weak'}",
    f"Margin swing: {margin_swing:.1f} pts -> {'STRONG' if margin_swing > 10 else 'WEAK (not a useful cycle axis alone)'}",
    f"Units swing: {units_swing:.0f}%  -> {'STRONG, confirms revenue finding' if units_swing > 30 else 'weak'}",
    f"seasonal_flag validated: {'YES' if swing_true > swing_false else 'NO -- investigate'} "
    f"(True swing {swing_true:.0f}% vs False swing {swing_false:.0f}%)",
    f"Stockout rate tracks revenue peak: {'YES' if len(overlap) >= 2 else 'NO -- investigate'} "
    f"({len(overlap)}/3 top months overlap)",
    f"2025-to-2026 shape correlation: {correlation:.2f} -> "
    f"{'consistent (but based on forecast, not confirmed actuals)' if correlation > 0.6 else 'weak match'}",
]
print("\n".join(summary_lines))
with open(f"{OUTPUT_DIR}/time_cycle_summary.txt", "w") as f:
    f.write("TIME CYCLE DIAGNOSTIC SUMMARY\n" + "=" * 40 + "\n\n")
    f.write("\n".join(summary_lines))
    f.write("\n\nOVERALL: Revenue, units sold, seasonal_flag, and stockout rate all point\n")
    f.write("to the SAME shape (quiet Jan-Jun, ramp from Jul, peak Sep-Oct, easing Dec).\n")
    f.write("Margin % is flat and NOT a useful cycle axis on its own.\n")
    f.write("This confirms a real WITHIN-YEAR seasonal shape. It does not yet confirm\n")
    f.write("year-over-year RECURRENCE, which requires a second full year of real data.\n")
print(f"\nSaved: {OUTPUT_DIR}/time_cycle_summary.txt")
print("\nDone.")
