"""
Supply Brain — Roll the Weekly MEIO Policy Across the Full Forward Horizon
=============================================================================

`simulate_policy.py` tests one policy, computed once (as-of the last real
week of history), frozen for the entire simulated 2026 year -- because
`digital_twin.py`'s policy CSV has no time dimension. That's a fair test of
the ANNUAL model, but not of a tool whose whole premise is "recompute
weekly." Three rounds of refining the single-week demand estimate (see
meio_weekly_lib.py docstring points 7-8) each helped the one-week-ahead
backtest and increasingly plateaued or reversed on that frozen-policy
simulation -- evidence the remaining gap isn't demand-estimate quality
anymore, it's that the policy isn't allowed to move.

This script builds a genuinely time-varying policy instead: one row set per
(period, dc_id, sku_id) for every week of the 2026 forward horizon, so the
policy a planner would actually have in hand changes week to week the way
the tool is designed to be used.

Honest scope note: this does NOT close the demand-feedback loop (each
week's estimate doesn't see the twin's own simulated outcome for prior
2026 weeks -- that would require a much bigger loop between the twin's RNG
demand and this estimator). Every week uses the same fixed as-of point (the
last real week of 2025) for its trailing bias/CV/de-seasonalized-level
statistics -- confirmed safe because forward-horizon weeks have no
realized actual_qty to trail against anyway (see target_week_demand's
docstring). What DOES vary week to week is each week's own published
forecast_qty and its seasonal_index(subcategory, week-of-year) -- i.e. the
policy tracks each week's own demand level and season, it just doesn't
learn from how 2026 actually turns out as it goes.

Requires `models/twin/digital_twin.py` to support an optional `period`
column in the policy CSV (added alongside this script) -- falls back to
today's single static policy when that column is absent, so every existing
call site is unaffected.

    python roll_forward_policy.py

Output: output/rolling_2026/meio_policy_rolling.csv (or `out_path` if
    passed to `run()` directly -- added after `alt_models/sim_optimize_
    rolling.py` and `alt_models/bandit_rolling.py` were run IN PARALLEL,
    both hardcoded to this same default path, and silently overwrote each
    other's output mid-run. Caught it because two supposedly-different
    final policies scored identically; anything calling `run()` from a
    process that might overlap another caller in time MUST pass its own
    `out_path`.)
    (period, dc_id, sku_id, safety_stock, reorder_point, order_up_to_level)
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meio_weekly_lib as lib
import meio_optimizer_weekly as weekly

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run(data_dir: str, as_of_week: str, out_path: str = None):
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)
    as_of_week_num = int(period_index.loc[period_index.period == as_of_week, "week_num"].iloc[0])

    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)

    forward_weeks = sorted(period_index[period_index.week_num > as_of_week_num]["week_num"].tolist())
    print(f"As-of: {as_of_week}  ->  rolling policy across {len(forward_weeks)} forward weeks")

    all_weeks = []
    constrained_weeks = 0
    for wk_num in forward_weeks:
        target_period = period_index.loc[period_index.week_num == wk_num, "period"].iloc[0]
        demand = lib.target_week_demand(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                         target_week_num=wk_num)
        policy = lib.solve_policy(demand, leadtime_by_dc, cost, active_rdc, active_cdc, cdc_primary_rdc)
        rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                                        active_rdc, wk_num)
        pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
        n_constrained = int(pdc_policy.capacity_constrained.sum()) if len(pdc_policy) else 0
        if n_constrained:
            constrained_weeks += 1
        if len(pdc_policy):
            policy = pd.concat([policy, pdc_policy], ignore_index=True)
        policy["period"] = target_period
        all_weeks.append(policy[["period", "dc_id", "sku_id", "safety_stock",
                                  "reorder_point", "order_up_to_level"]])
        if wk_num % 10 == 0 or wk_num == forward_weeks[-1] or n_constrained:
            tag = f"  ({n_constrained} PDC-SKU lines capacity-clipped)" if n_constrained else ""
            print(f"  {target_period}: {len(policy):,} SKU x DC lines{tag}")
    print(f"\n{constrained_weeks}/{len(forward_weeks)} weeks had at least one capacity-clipped PDC-SKU line")

    rolling = pd.concat(all_weeks, ignore_index=True)
    if out_path is None:
        out_dir = os.path.join(SCRIPT_DIR, "output", "rolling_2026")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "meio_policy_rolling.csv")
    else:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rolling.to_csv(out_path, index=False)
    print(f"\nWrote {len(rolling):,} (period, dc, sku) policy rows to {out_path}")
    print(f"Weeks covered: {rolling.period.nunique()}  "
          f"Mean order_up_to_level: {rolling.order_up_to_level.mean():.1f}  "
          f"(static single-week policy was 195.3 -- see README)")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default="2025-W52")
    ap.add_argument("--data-dir", default=weekly.DEFAULT_DATA)
    args = ap.parse_args()
    run(args.data_dir, args.as_of_week)


if __name__ == "__main__":
    main()
