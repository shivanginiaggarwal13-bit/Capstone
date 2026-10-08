"""
Alt Model 3, Rolling-Policy Variant — Simulation-Optimization Over the Full
2026 Horizon
================================================================================

`sim_optimize_model.py` searched the same 4 constants on the frozen
single-week basis, where the search ceiling is capped by the
frozen-vs-rolling structural gap documented in the README (v6): no
frozen-policy model beat baseline, no matter how well-tuned, because
freezing one week's policy for 52 weeks is a different, harder problem
than what the tool is designed to solve. This variant searches the SAME 4
constants but builds a full 52-week ROLLING policy per trial
(`roll_forward_policy.run()`, reusing the exact machinery v6/v9/v10
validated), to see whether search can find something that beats baseline
the way the hand-tuned rolling policy already does.

Cost note: each trial rebuilds the full 52-week policy (~52 x ~6
target_week_demand calls including the PDC-smoothing window -- 90-140s
based on prior timing) plus one twin score (~9s). Trial count cut to 6 (vs
15 for the frozen-week version) to keep total runtime to ~15-20 minutes
rather than ~40.

Output: alt_output/sim_optimize_rolling_policy.csv, alt_output/sim_optimize_rolling_trials.csv
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
import roll_forward_policy

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")


def build_rolling_policy(data_dir, as_of_week, params):
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
        # dedicated path, not roll_forward_policy.py's shared default --
        # bandit_rolling.py uses its own dedicated path too, so the two
        # scripts can never collide even if run concurrently again (they
        # did once; see roll_forward_policy.py's module docstring)
        out_path = os.path.join(SCRIPT_DIR, "sim_optimize_rolling_trials", "_building_policy.csv")
        return roll_forward_policy.run(data_dir, as_of_week, out_path=out_path)
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


def main(n_trials=6, seed=99, as_of_week="2025-W52", data_dir=None):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "sim_optimize_rolling_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")

    rng = np.random.default_rng(seed)
    bounds = {"rdc_uplift_mult": (0.7, 1.5), "pdc_uplift_mult": (0.7, 1.5),
              "pdc_cover_weeks": (2.0, 7.0), "mean_blend_alpha": (0.3, 0.9)}
    trials = [{"rdc_uplift_mult": 1.0, "pdc_uplift_mult": 1.0, "pdc_cover_weeks": lib.PDC_COVER_WEEKS,
               "mean_blend_alpha": lib.MEAN_BLEND_ALPHA}]  # trial 0 = current hand-derived rolling policy
    for _ in range(n_trials - 1):
        trials.append({k: rng.uniform(lo, hi) for k, (lo, hi) in bounds.items()})

    results = []
    best = None
    for i, params in enumerate(trials):
        print(f"\n--- trial {i}: building 52-week policy with {({k: round(v,3) for k,v in params.items()})} ---")
        policy_csv = build_rolling_policy(data_dir, as_of_week, params)
        run_name = f"rolltrial{i:02d}"
        kpi = score(policy_csv, state_csv, run_name, trial_out_root)
        row = {**params, "trial": i, "fill_rate": kpi["customer_fill_rate"],
               "internal_replen_fill_rate": kpi["internal_replen_fill_rate"],
               "total_landed_cost_usd": kpi["total_landed_cost_usd"]}
        results.append(row)
        print(f"  trial {i}: fill {kpi['customer_fill_rate']:.2%}  cost ${kpi['total_landed_cost_usd']:,.0f}  "
              f"internal_replen {kpi['internal_replen_fill_rate']:.2%}")
        if best is None or kpi["total_landed_cost_usd"] < best["total_landed_cost_usd"]:
            best = row
            os.makedirs(out_dir, exist_ok=True)
            shutil.copy(policy_csv, os.path.join(out_dir, "sim_optimize_rolling_policy.csv"))

    trials_df = pd.DataFrame(results)
    trials_df.to_csv(os.path.join(out_dir, "sim_optimize_rolling_trials.csv"), index=False)
    print(f"\nBest trial: {best['trial']} (fill {best['fill_rate']:.2%}, cost ${best['total_landed_cost_usd']:,.0f})")
    print(f"  params: { {k: round(v, 3) for k, v in best.items() if k in bounds} }")
    print(f"Wrote {out_dir}/sim_optimize_rolling_policy.csv, sim_optimize_rolling_trials.csv")
    return os.path.join(out_dir, "sim_optimize_rolling_policy.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n-trials", type=int, default=6)
    ap.add_argument("--seed", type=int, default=99)
    args = ap.parse_args()
    main(n_trials=args.n_trials, seed=args.seed)
