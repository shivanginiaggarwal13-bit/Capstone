"""
Alt Model 4, Rolling-Policy Variant — Thompson Sampling Over the Full
2026 Horizon
================================================================================

`bandit_model.py` ran on the frozen single-week basis, where every reward
came back negative (baseline beat every tier combination) -- expected,
since that test structurally caps every model regardless of z-tuning (see
README v6). This variant builds a full 52-week ROLLING policy per round
instead of a single frozen week, to test whether the bandit's tier choice
matters on the basis that actually let the hand-tuned rolling policy beat
baseline.

Cost note: each round rebuilds a full 52-week policy (~90-140s) plus TWO
twin scores at the same seed (trial + baseline, ~18s) -- same
same-seed-per-round fix as the frozen-week bandit, for the same reason
(a fixed reference seed would bias every arm's belief in the same
direction). Rounds cut to 3 per segment (vs 8 for the frozen-week version)
-- 9 rounds x ~2 minutes each is already ~18-20 minutes.

Output: alt_output/bandit_rolling_policy.csv, alt_output/bandit_rolling_belief_history.csv
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import meio_weekly_lib as lib
import roll_forward_policy

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")

TIERS = {"90%": norm.ppf(0.90), "95%": norm.ppf(0.95), "97%": norm.ppf(0.97), "99%": norm.ppf(0.99)}
SEGMENTS = ["Fast-Moving", "Seasonal", "Intermediate"]
PRIOR_MEAN, PRIOR_VAR = 0.0, 2.0e12
OBS_VAR = 5.0e11


def build_rolling_policy(data_dir, as_of_week, z_by_segment):
    orig = dict(lib.Z_BY_SEGMENT)
    try:
        lib.Z_BY_SEGMENT.clear()
        lib.Z_BY_SEGMENT.update(z_by_segment)
        # dedicated path, not roll_forward_policy.py's shared default -- see
        # its module docstring for why (this script and sim_optimize_rolling.py
        # collided when run concurrently, sharing that default path)
        out_path = os.path.join(SCRIPT_DIR, "bandit_rolling_trials", "_building_policy.csv")
        return roll_forward_policy.run(data_dir, as_of_week, out_path=out_path)
    finally:
        lib.Z_BY_SEGMENT.clear()
        lib.Z_BY_SEGMENT.update(orig)


def score(policy_csv, state_csv, run_name, out_root, seed):
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--seed", str(seed),
           "--out-root", out_root, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_root, run_name, "run_kpis.json")) as f:
        return json.load(f)


def main(rounds_per_segment=3, seed=13, as_of_week="2025-W52", data_dir=None):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "bandit_rolling_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")
    baseline_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")

    rng = np.random.default_rng(seed)
    belief = {s: {t: [PRIOR_MEAN, PRIOR_VAR] for t in TIERS} for s in SEGMENTS}
    current_best = {s: lib.Z_BY_SEGMENT.get(s, lib.Z_DEFAULT) for s in SEGMENTS}
    history = []
    last_policy_csv = None

    round_n = 0
    for seg in SEGMENTS:
        for r in range(rounds_per_segment):
            round_n += 1
            round_seed = 2026 + round_n
            draws = {t: rng.normal(m, np.sqrt(v)) for t, (m, v) in belief[seg].items()}
            played_tier = max(draws, key=draws.get)
            z_trial = dict(current_best)
            z_trial[seg] = TIERS[played_tier]

            print(f"\n--- [{seg}] round {r+1}/{rounds_per_segment}: building 52-week policy, "
                  f"playing {played_tier} ---")
            policy_csv = build_rolling_policy(data_dir, as_of_week, z_trial)
            run_name = f"seg-{seg.replace(' ', '')}_r{r:02d}"
            last_policy_csv = policy_csv

            kpi = score(policy_csv, state_csv, run_name, trial_out_root, seed=round_seed)
            base_kpi = score(baseline_policy_csv, state_csv, f"{run_name}_base", trial_out_root, seed=round_seed)
            reward = base_kpi["total_landed_cost_usd"] - kpi["total_landed_cost_usd"]

            m0, v0 = belief[seg][played_tier]
            post_var = 1.0 / (1.0 / v0 + 1.0 / OBS_VAR)
            post_mean = post_var * (m0 / v0 + reward / OBS_VAR)
            belief[seg][played_tier] = [post_mean, post_var]

            history.append({"round": round_n, "segment": seg, "arm_played": played_tier,
                             "reward_usd_saved": reward, "fill_rate": kpi["customer_fill_rate"],
                             "belief_mean": post_mean, "belief_std": np.sqrt(post_var)})
            print(f"  reward ${reward:+,.0f}  fill {kpi['customer_fill_rate']:.2%}  "
                  f"belief now ${post_mean:+,.0f} +/- ${np.sqrt(post_var):,.0f}")

        best_tier = max(belief[seg], key=lambda t: belief[seg][t][0])
        current_best[seg] = TIERS[best_tier]
        print(f"  -> {seg} converged to {best_tier} tier (z={TIERS[best_tier]:.3f})")

    print("\nFinal converged service-level tier per segment:")
    for seg in SEGMENTS:
        best_tier = max(belief[seg], key=lambda t: belief[seg][t][0])
        print(f"  {seg:12s}: {best_tier}  (z={TIERS[best_tier]:.3f}, belief ${belief[seg][best_tier][0]:+,.0f})")

    final_policy_csv = build_rolling_policy(data_dir, as_of_week, current_best)
    shutil.copy(final_policy_csv, os.path.join(out_dir, "bandit_rolling_policy.csv"))
    pd.DataFrame(history).to_csv(os.path.join(out_dir, "bandit_rolling_belief_history.csv"), index=False)
    print(f"\nWrote {out_dir}/bandit_rolling_policy.csv, bandit_rolling_belief_history.csv")
    return os.path.join(out_dir, "bandit_rolling_policy.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds-per-segment", type=int, default=3)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    main(rounds_per_segment=args.rounds_per_segment, seed=args.seed)
