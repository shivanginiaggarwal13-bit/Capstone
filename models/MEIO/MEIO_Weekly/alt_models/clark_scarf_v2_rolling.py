"""
Clark-Scarf v2 (continuous alpha + Monte Carlo DDLT), Rolling-Policy Variant
================================================================================

`clark_scarf_v2_model.py` found alpha≈0.2-0.4 best on the frozen single-week
basis (85.08-85.34% fill vs baseline's 91.0%) -- a real improvement over v1
(80.5%), but the frozen basis structurally caps every model in this project
(see README v6). This variant builds a full 52-week ROLLING policy per
alpha value, reusing `roll_forward_policy.py`'s per-week loop structure,
to see whether the same continuous-pooling + Monte Carlo upgrade helps on
the basis that actually let the hand-tuned rolling policy beat baseline.

Cost note: Monte Carlo DDLT is the expensive part (4000 Gamma draws per
line by default). Reduced to `n_sims=1200` here and shared data/RNG loaded
ONCE for all 52 weeks (see `clark_scarf_v2_model.build_full_policy`'s
`_shared`/`rng` params) to keep a 4-alpha x 52-week sweep tractable
(~3-4 min/alpha based on frozen-basis timing at n_sims=4000, scaled down).

Output: alt_output/clark_scarf_v2_rolling_policy.csv, alt_output/clark_scarf_v2_rolling_alpha_search.csv
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import meio_weekly_lib as lib
import clark_scarf_v2_model as csv2

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")


def build_rolling_policy(data_dir, as_of_week, alpha, n_sims, rng, out_path):
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)
    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)
    shared = (dfc, period_index, seasonal_index, sku_subcat, active_rdc, active_cdc,
              leadtime_by_dc, cost, cdc_primary_rdc)

    as_of_num = int(period_index.loc[period_index.period == as_of_week, "week_num"].iloc[0])
    forward_weeks = sorted(period_index[period_index.week_num > as_of_num]["week_num"].tolist())

    all_weeks = []
    for wk_num in forward_weeks:
        target_period = period_index.loc[period_index.week_num == wk_num, "period"].iloc[0]
        demand = lib.target_week_demand(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                         target_week_num=wk_num)
        policy = csv2.solve(dfc, as_of_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
                             cost, active_rdc, active_cdc, cdc_primary_rdc, wk_num, alpha, rng,
                             n_sims=n_sims)
        rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                                        active_rdc, wk_num)
        pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
        if len(pdc_policy):
            policy = pd.concat([policy, pdc_policy], ignore_index=True)
        policy["period"] = target_period
        all_weeks.append(policy[["period", "dc_id", "sku_id", "safety_stock",
                                  "reorder_point", "order_up_to_level"]])
        if wk_num % 10 == 0 or wk_num == forward_weeks[-1]:
            print(f"  {target_period}: {len(policy):,} SKU x DC lines")

    rolling = pd.concat(all_weeks, ignore_index=True)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rolling.to_csv(out_path, index=False)
    print(f"Wrote {len(rolling):,} rows -> {out_path}")
    return out_path


def score(policy_csv, state_csv, run_name, out_root):
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--out-root", out_root, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_root, run_name, "run_kpis.json")) as f:
        return json.load(f)


def main(as_of_week="2025-W52", data_dir=None, alphas=(0.0, 0.2, 0.4, 1.0), n_sims=1200, seed=2026):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "clark_scarf_v2_rolling_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")

    rng = np.random.default_rng(seed)  # shared across all alphas/weeks -- each Gamma draw still independent
    results = []
    best = None
    for a in alphas:
        print(f"\n=== alpha={a:.1f}: building 52-week Monte Carlo Clark-Scarf policy ===")
        build_path = os.path.join(trial_out_root, "_building_policy.csv")
        build_rolling_policy(data_dir, as_of_week, a, n_sims, rng, build_path)
        run_name = f"cs_v2_rolling_alpha{a:.1f}".replace(".", "")
        kpi = score(build_path, state_csv, run_name, trial_out_root)
        row = {"alpha": a, "fill_rate": kpi["customer_fill_rate"],
               "total_landed_cost_usd": kpi["total_landed_cost_usd"],
               "internal_replen_fill_rate": kpi["internal_replen_fill_rate"]}
        results.append(row)
        print(f"  alpha={a:.1f}: fill {kpi['customer_fill_rate']:.2%}  cost ${kpi['total_landed_cost_usd']:,.0f}  "
              f"internal_replen {kpi['internal_replen_fill_rate']:.2%}")
        if best is None or kpi["total_landed_cost_usd"] < best["total_landed_cost_usd"]:
            best = row
            shutil.copy(build_path, os.path.join(out_dir, "clark_scarf_v2_rolling_policy.csv"))

    results_df = pd.DataFrame(results)
    results_df.to_csv(os.path.join(out_dir, "clark_scarf_v2_rolling_alpha_search.csv"), index=False)
    print(f"\nBest alpha: {best['alpha']:.1f}  (fill {best['fill_rate']:.2%}, "
          f"cost ${best['total_landed_cost_usd']:,.0f})")
    print(results_df.to_string(index=False))
    print(f"\nWrote {out_dir}/clark_scarf_v2_rolling_policy.csv, clark_scarf_v2_rolling_alpha_search.csv")
    return os.path.join(out_dir, "clark_scarf_v2_rolling_policy.csv"), best["alpha"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default="2025-W52")
    ap.add_argument("--n-sims", type=int, default=1200)
    args = ap.parse_args()
    main(as_of_week=args.as_of_week, n_sims=args.n_sims)
