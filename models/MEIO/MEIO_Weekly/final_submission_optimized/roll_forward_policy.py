"""
Supply Brain -- Roll the Weekly MEIO Policy Across the Full Forward Horizon (optimized)
=======================================================================================

The same tool as final_submission/roll_forward_policy.py: one policy row set per
(period, dc_id, sku_id) for every week of the 2026 forward horizon, every week sized as
of the last real week (2025-W52). See the original's docstring for the method and its
scope note.

Faster because every week reuses one as-of week's trailing statistics (they were
recomputed six times a week before), and the quadrature DDLT the config selects
bisects across CPU threads. With `ddlt: monte_carlo` the weeks still draw from one
generator in order, so results match the original either way.

    python roll_forward_policy.py

Output: <repo-root>/output datasets/MEIO/<scenario>/rolling_2026/meio_policy_rolling.parquet
    (or `out_path` if passed to `run()`): period, dc_id, sku_id, safety_stock,
    reorder_point, order_up_to_level; plus meio_parameters.json next to it.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                     # noqa: E402
import meio_weekly_lib as lib            # noqa: E402
import meio_optimizer_weekly as weekly   # noqa: E402
import meio_parameters                   # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = str(rt.REPO_ROOT)
OUTPUT_DATASETS_DIR = os.path.join(REPO_ROOT, "output datasets", "MEIO")
DEFAULT_SCENARIO_CONFIG = os.path.join(REPO_ROOT, "scenario_config.json")
import scenario                          # noqa: E402
import overrides as override_engine      # noqa: E402

COLS = ["period", "dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]


def run(data_dir: str | None, as_of_week: str, out_path: str = None,
        scenario_config_path: str = DEFAULT_SCENARIO_CONFIG):
    t0 = time.time()
    config_path = Path(os.path.abspath(scenario_config_path))
    config = scenario.load_config(config_path)
    data_dir = data_dir or str(scenario.resolve_path(config_path, config, "meio", "data_dir"))
    ovs, plan = weekly.plan_overrides(config_path, config, data_dir, "meio")
    params = meio_parameters.resolve(config)
    print(params.summary())
    rng = np.random.default_rng(params.seed)      # Monte Carlo only; quadrature draws nothing
    dfc = lib.load_demand_forecast(data_dir)
    period_index = dfc.period_index
    as_of_week_num = dfc.week_of[as_of_week]

    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)

    forward_weeks = [w for w in range(dfc.n_weeks) if w > as_of_week_num]
    print(f"As-of: {as_of_week}  ->  rolling policy across {len(forward_weeks)} forward weeks")

    all_weeks = []
    constrained_weeks = 0
    for wk_num in forward_weeks:
        target_period = dfc.periods[wk_num]
        lanes = plan.lanes(target_period) if plan else None
        week_leadtime = leadtime_by_dc
        if lanes is not None:            # this week's lead-time overrides, on the same routing
            week_rdc_lt, week_cdc_lt, _ = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc, lanes)
            week_leadtime = {**week_rdc_lt, **week_cdc_lt}
        policy = weekly.size_rdc_cdc(params, dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                     week_leadtime, cost, active_rdc, active_cdc, cdc_primary_rdc, wk_num, plan, rng)
        rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                                       active_rdc, wk_num, overrides=plan)
        pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost, overrides=plan, period=target_period,
                                          lanes=lanes, z_by_sku=params.z_by_sku or None)
        n_constrained = int(pdc_policy["capacity_constrained"].sum()) if pdc_policy.height else 0
        if n_constrained:
            constrained_weeks += 1
        if pdc_policy.height:
            policy = pl.concat([policy, pdc_policy], how="diagonal_relaxed")
        policy = weekly.apply_policy_hook(plan, policy, target_period)
        all_weeks.append(policy.select(COLS[1:]).with_columns(pl.lit(target_period).alias("period")).select(COLS))
        if wk_num % 10 == 0 or wk_num == forward_weeks[-1] or n_constrained:
            tag = f"  ({n_constrained} PDC-SKU lines capacity-clipped)" if n_constrained else ""
            print(f"  {target_period}: {policy.height:,} SKU x DC lines{tag}", flush=True)
    print(f"\n{constrained_weeks}/{len(forward_weeks)} weeks had at least one capacity-clipped PDC-SKU line")

    rolling = pl.concat(all_weeks, how="vertical_relaxed")
    if out_path is None:
        output_root = scenario.resolve_path(config_path, config, "meio", "output_root")
        out_dir = output_root / scenario.scenario_folder_name(config) / "rolling_2026"
        os.makedirs(out_dir, exist_ok=True)
        scenario.copy_config(config_path, out_dir.parent, config)
        out_path = str(out_dir / "meio_policy_rolling.parquet")
    else:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rt.write_table(rolling, out_path)
    print(f"\nWrote {rolling.height:,} (period, dc, sku) policy rows to {out_path}")
    if params.model != "meio_weekly":
        print(f"Parameters: {params.write(Path(out_path).parent / 'meio_parameters.json')}")
    if plan:
        receipt = override_engine.write_receipts(scenario.overrides_dir(config_path, config), "meio", plan.ovs, ovs)
        print(f"Override receipts: {receipt}")
    print(f"Weeks covered: {rolling['period'].n_unique()}  "
          f"Mean order_up_to_level: {rt.nanmean_pandas(rolling['order_up_to_level'].to_numpy()):.1f}  "
          f"(static single-week policy was 195.3 -- see README)")
    print(f"({time.time() - t0:.0f}s)")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default="2025-W52")
    ap.add_argument("--data-dir", default=weekly.DEFAULT_DATA)
    ap.add_argument("--scenario-config", default=DEFAULT_SCENARIO_CONFIG,
                    help="Shared root scenario JSON used for this run.")
    args = ap.parse_args()
    run(args.data_dir, args.as_of_week, scenario_config_path=args.scenario_config)


if __name__ == "__main__":
    main()
