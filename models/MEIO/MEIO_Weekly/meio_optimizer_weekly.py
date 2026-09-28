"""
Supply Brain — Weekly MEIO Optimizer
=====================================

Weekly-cadence sibling of `models/MEIO/meio_optimizer.py`. Where the annual
script produces one flat SKU x DC safety-stock number for the whole forward
horizon, this script answers the operational question a planner asks every
week: **given everything known up to this week, how much stock should each
DC carry to cover next week?**

Run it once a week, as-of the most recently closed week:

    python meio_optimizer_weekly.py --as-of-week 2025-W52

which produces the recommendation for the week that follows (2026-W01 in
that example). With no --as-of-week, it uses the latest week that has
actual demand on record.

No lookahead: every estimate (trailing demand mean, blended CV, forecast
bias correction) is computed from data at or before --as-of-week only, even
when run against a week that is not really "now" (e.g. during a backtest
sweep -- see backtest_csl.py, which calls the same estimation functions).

Method (see meio_weekly_lib.py for the fixes this bakes in vs. the version
that was found underperforming):
    1. Aggregate demand_forecast.csv to true (period, sku, dc) grain.
    2. Bias-correct next week's forecast_qty by the trailing actual/forecast
       ratio (short window).
    3. Size demand uncertainty from a blended short+long trailing CV.
    4. Pool RDC demand with its downstream CDCs' demand (echelon stacking).
    5. Newsvendor critical ratio -> z -> safety stock -> reorder point ->
       order-up-to, using DC-specific holding/stockout cost.

Outputs:
    output/<target_week>/meio_recommendations_weekly.csv   SKU x DC lines
    output/<target_week>/meio_policy_weekly.csv             dc_id,sku_id,safety_stock,
                                                              reorder_point,order_up_to_level
                                                              (digital_twin.py --policy contract)
    output/<target_week>/meio_summary_by_dc_weekly.csv       "stock to maintain" rollup per DC
    meio_weekly_history.csv   (repo-tracked) one row appended per run
    chart_ss_trend.png        (repo-tracked) total recommended SS $ across runs
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meio_weekly_lib as lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "input datasets"))


def run(data_dir: str, as_of_week: str | None, short_window: int, long_window: int):
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)

    if as_of_week is None:
        has_actual = dfc[dfc.actual_qty.notna() & (dfc.horizon == "in_sample")]
        as_of_week = period_index[period_index.period.isin(has_actual.period.unique())]["period"].iloc[-1]

    row = period_index[period_index.period == as_of_week]
    if row.empty:
        raise SystemExit(f"--as-of-week {as_of_week!r} not found in demand_forecast.csv periods")
    as_of_week_num = int(row.iloc[0]["week_num"])

    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)
    demand = lib.target_week_demand(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat)
    target_period = demand["target_period"].iloc[0]
    print(f"As-of week: {as_of_week}  ->  target (next) week: {target_period}")

    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)

    policy = lib.solve_policy(demand, leadtime_by_dc, cost, active_rdc, active_cdc, cdc_primary_rdc)

    rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                                    active_rdc, as_of_week_num + 1)
    pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
    if len(pdc_policy):
        policy = pd.concat([policy, pdc_policy], ignore_index=True)
        print(f"PDC coverage: {len(pdc_policy):,} SKU x PDC rows added "
              f"({pdc_policy.dc_id.nunique()} PDCs)")

    policy = policy.sort_values(["dc_id", "sku_id"]).reset_index(drop=True)

    run_dir = os.path.join(SCRIPT_DIR, "output", target_period)
    os.makedirs(run_dir, exist_ok=True)

    reco_cols = ["dc_id", "sku_id", "category", "subcategory", "target_mean", "target_std",
                 "lt_mean_wk", "lt_std_wk", "target_csl", "z", "cycle_stock", "safety_stock",
                 "reorder_point", "order_up_to_level", "safety_stock_usd",
                 "mu_requested", "capacity_constrained"]
    policy[reco_cols].to_csv(os.path.join(run_dir, "meio_recommendations_weekly.csv"), index=False)

    policy_csv = policy[["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]]
    policy_csv.to_csv(os.path.join(run_dir, "meio_policy_weekly.csv"), index=False)

    by_dc = policy.groupby("dc_id").agg(
        skus=("sku_id", "nunique"),
        total_reorder_point=("reorder_point", "sum"),
        total_safety_stock=("safety_stock", "sum"),
        total_order_up_to=("order_up_to_level", "sum"),
        total_safety_stock_usd=("safety_stock_usd", "sum"),
        lt_mean_wk=("lt_mean_wk", "first"),
    ).reset_index().sort_values("total_safety_stock_usd", ascending=False)
    by_dc.to_csv(os.path.join(run_dir, "meio_summary_by_dc_weekly.csv"), index=False)

    print(f"\nWrote {len(policy):,} SKU x DC recommendation rows to {run_dir}/")
    print(f"\nStock to maintain next week ({target_period}), by DC:")
    print(by_dc.to_string(index=False))

    total_ss_usd = policy.safety_stock_usd.sum()
    total_rop = policy.reorder_point.sum()
    history_path = os.path.join(SCRIPT_DIR, "meio_weekly_history.csv")
    history_row = pd.DataFrame([{
        "run_generated_at": pd.Timestamp.now("UTC").isoformat(),
        "as_of_week": as_of_week,
        "target_week": target_period,
        "short_window_weeks": short_window,
        "long_window_weeks": long_window,
        "sku_dc_lines": len(policy),
        "total_safety_stock_units": policy.safety_stock.sum(),
        "total_safety_stock_usd": total_ss_usd,
        "total_reorder_point_units": total_rop,
        "avg_target_csl": policy.target_csl.mean(),
    }])
    if os.path.exists(history_path):
        history = pd.concat([pd.read_csv(history_path), history_row], ignore_index=True)
        history = history.drop_duplicates(subset=["target_week"], keep="last")
    else:
        history = history_row
    history = history.sort_values("target_week").reset_index(drop=True)
    history.to_csv(history_path, index=False)
    print(f"\nAppended run to {history_path}")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(history["target_week"], history["total_safety_stock_usd"] / 1000, marker="o", color="#2b6cb0")
    ax.set_ylabel("Recommended safety stock ($000)")
    ax.set_xlabel("Target week")
    ax.set_title("MEIO Weekly — Recommended Safety Stock Across Runs")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_ss_trend.png"), dpi=150)
    plt.close(fig)
    print(f"Total recommended safety stock for {target_period}: ${total_ss_usd:,.0f}")

    return policy


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default=None,
                     help="e.g. 2025-W52. Recommendation is produced for the following week. "
                          "Default: latest week with actual demand on record.")
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--short-window", type=int, default=lib.SHORT_WINDOW_WEEKS)
    ap.add_argument("--long-window", type=int, default=lib.LONG_WINDOW_WEEKS)
    args = ap.parse_args()
    run(args.data_dir, args.as_of_week, args.short_window, args.long_window)


if __name__ == "__main__":
    main()
