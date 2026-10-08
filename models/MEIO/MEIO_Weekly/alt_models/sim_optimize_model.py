"""
Alt MEIO Model 3 — Simulation-Optimization Over the Existing Formula
========================================================================

Not a new formula -- the same (R,s,S) newsvendor logic already in
`meio_weekly_lib.solve_policy`/`solve_pdc_policy`, but with its hand-derived
constants (SIGMA_UPLIFT multipliers, PDC_COVER_WEEKS, MEAN_BLEND_ALPHA)
treated as a search space instead of fixed values, optimized directly
against the digital twin's simulated outcome. This is literally what
happened by hand across this project's v1-v10 rounds (try a value, measure
via the twin, keep or discard) -- systematized here as random search
instead of manual reasoning, in the same spirit as the bandit-engine
strategy already documented for this project
(`Obsidian-Notes/Multi-Armed Bandits — Supply Brain Strategy.md`).

Search space (4 dims, sampled around the hand-derived defaults):
    rdc_uplift_mult   in [0.7, 1.5]  x SIGMA_UPLIFT["RDC"]   (default 1.25)
    pdc_uplift_mult   in [0.7, 1.5]  x SIGMA_UPLIFT["PDC"]   (default 1.45)
    pdc_cover_weeks   in [2.0, 7.0]                          (default 4.5)
    mean_blend_alpha  in [0.3, 0.9]                          (default 0.6)

Objective: minimize total_landed_cost_usd from a full-horizon twin forward
run scoring the resulting single-week (2026-W01) frozen policy -- the same
basis `simulate_policy.py` uses, so results are directly comparable to
every prior round in the README.

Method: random search, not Bayesian optimization or a real bandit -- no
new dependencies, transparent, and the search space is small enough (4
dims) that a modest budget of random draws covers it reasonably. Each
trial monkey-patches meio_weekly_lib's module constants for the duration
of ONE solve (never touches digital_twin.py, which scores each candidate
in a fresh subprocess), then restores them -- trials are independent and
order doesn't matter.

Output: alt_output/sim_optimize_policy.csv, alt_output/sim_optimize_trials.csv
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import meio_weekly_lib as lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")


def build_policy(data_dir, as_of_week, params):
    """One solve of the existing formula with params monkey-patched in."""
    orig = {
        "SIGMA_UPLIFT": dict(lib.SIGMA_UPLIFT),
        "PDC_COVER_WEEKS": lib.PDC_COVER_WEEKS,
        "MEAN_BLEND_ALPHA": lib.MEAN_BLEND_ALPHA,
    }
    try:
        lib.SIGMA_UPLIFT["RDC"] = orig["SIGMA_UPLIFT"]["RDC"] * params["rdc_uplift_mult"]
        lib.SIGMA_UPLIFT["PDC"] = orig["SIGMA_UPLIFT"]["PDC"] * params["pdc_uplift_mult"]
        lib.PDC_COVER_WEEKS = params["pdc_cover_weeks"]
        lib.MEAN_BLEND_ALPHA = params["mean_blend_alpha"]

        dfc = lib.load_demand_forecast(data_dir)
        period_index = lib.load_period_index(data_dir)
        seasonal_index = lib.build_seasonal_index(data_dir)
        sku_subcat = lib.load_sku_subcategory(data_dir)
        active_rdc, active_cdc = lib.active_nodes(dfc)
        rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
        leadtime_by_dc = {**rdc_lt, **cdc_lt}
        cost = lib.load_sku_cost(data_dir)

        as_of_num = int(period_index.loc[period_index.period == as_of_week, "week_num"].iloc[0])
        target_num = as_of_num + 1
        demand = lib.target_week_demand(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                         target_week_num=target_num)
        policy = lib.solve_policy(demand, leadtime_by_dc, cost, active_rdc, active_cdc, cdc_primary_rdc)
        rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                                        active_rdc, target_num)
        pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
        if len(pdc_policy):
            policy = pd.concat([policy, pdc_policy], ignore_index=True)
        return policy[["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]]
    finally:
        lib.SIGMA_UPLIFT.clear()
        lib.SIGMA_UPLIFT.update(orig["SIGMA_UPLIFT"])
        lib.PDC_COVER_WEEKS = orig["PDC_COVER_WEEKS"]
        lib.MEAN_BLEND_ALPHA = orig["MEAN_BLEND_ALPHA"]


def score(policy_csv, state_csv, run_name, out_root):
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--out-root", out_root, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_root, run_name, "run_kpis.json")) as f:
        return json.load(f)


def main(n_trials=15, seed=42, as_of_week="2025-W52", data_dir=None):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "sim_optimize_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")

    rng = np.random.default_rng(seed)
    bounds = {"rdc_uplift_mult": (0.7, 1.5), "pdc_uplift_mult": (0.7, 1.5),
              "pdc_cover_weeks": (2.0, 7.0), "mean_blend_alpha": (0.3, 0.9)}

    # trial 0 is always the current hand-derived defaults, as a baseline reference
    trials = [{"rdc_uplift_mult": 1.0, "pdc_uplift_mult": 1.0, "pdc_cover_weeks": lib.PDC_COVER_WEEKS,
               "mean_blend_alpha": lib.MEAN_BLEND_ALPHA}]
    for _ in range(n_trials - 1):
        trials.append({k: rng.uniform(lo, hi) for k, (lo, hi) in bounds.items()})

    results = []
    best = None
    for i, params in enumerate(trials):
        policy = build_policy(data_dir, as_of_week, params)
        run_name = f"trial{i:02d}"
        trial_csv = os.path.join(trial_out_root, f"{run_name}_policy.csv")
        os.makedirs(trial_out_root, exist_ok=True)
        policy.to_csv(trial_csv, index=False)
        kpi = score(trial_csv, state_csv, run_name, trial_out_root)
        row = {**params, "trial": i, "fill_rate": kpi["customer_fill_rate"],
               "total_landed_cost_usd": kpi["total_landed_cost_usd"]}
        results.append(row)
        print(f"  trial {i:02d}: fill {kpi['customer_fill_rate']:.2%}  "
              f"cost ${kpi['total_landed_cost_usd']:,.0f}  params={ {k: round(v, 3) for k, v in params.items()} }")
        if best is None or kpi["total_landed_cost_usd"] < best["total_landed_cost_usd"]:
            best = row
            best_policy = policy

    trials_df = pd.DataFrame(results)
    trials_df.to_csv(os.path.join(out_dir, "sim_optimize_trials.csv"), index=False)
    best_policy.to_csv(os.path.join(out_dir, "sim_optimize_policy.csv"), index=False)
    print(f"\nBest trial: {best['trial']} (fill {best['fill_rate']:.2%}, "
          f"cost ${best['total_landed_cost_usd']:,.0f})")
    print(f"  params: { {k: round(v, 3) for k, v in best.items() if k in bounds} }")
    print(f"Wrote {out_dir}/sim_optimize_policy.csv, sim_optimize_trials.csv")
    return os.path.join(out_dir, "sim_optimize_policy.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n-trials", type=int, default=15)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    main(n_trials=args.n_trials, seed=args.seed)
