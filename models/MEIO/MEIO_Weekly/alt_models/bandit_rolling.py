"""
Alt Model 4, Rolling-Policy Variant — Thompson Sampling Over the Full
2026 Horizon (v2)
================================================================================

Same three fixes as `bandit_model.py` v2 (see its docstring for the full
reasoning behind each):

1. **Arms include the segment's own current default** as a named 5th tier,
   not just the 4 fixed {90,95,97,99}% -- so the bandit CAN land on "the
   hand-tuned default was already fine," the way `sim_optimize_rolling.py`
   did with its trial 0.
2. **Forced initial exploration**: the first 5 rounds of each segment play
   every arm once, in order. v1's actual run here is the concrete reason
   this was needed: with 3 rounds against 4 arms, Fast-Moving converged to
   a tier that was NEVER PLAYED (belief still at the untouched prior),
   which only "won" because every arm that WAS played scored negative that
   round. Not evidence -- an artifact of too few rounds.
3. **Tier-order penalty** in the reward, same as v2's frozen-basis sibling.

**NOT done here, unlike bandit_model.py**: seasonal-phase context. Properly
wiring it into this script's 52-week loop means each week needs its OWN
phase-specific arm, and a single round's reward becomes a MIXTURE of three
phases' choices -- a materially bigger bandit-attribution restructure than
fixes 1-3, not attempted in this pass. `bandit_model.py`'s
`run_seasonal_phase_check()` tests the underlying hypothesis (does the
answer differ by season) on the cheaper frozen basis instead.

Cost note: each round still rebuilds a full 52-week policy (~90-140s) plus
two twin scores (~18s). Five arms now need at least 5 rounds/segment just
to finish forced exploration (15 rounds minimum, ~30+ min) -- no budget
left in this pass for genuine Thompson rounds beyond that, so
`rounds_per_segment` defaults to exactly 5. Read this run as "forced
exploration only," not a converged bandit.

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

BASE_TIER_PCTS = [0.90, 0.95, 0.97, 0.99]
SEGMENTS = ["Fast-Moving", "Seasonal", "Intermediate"]
TIER_ORDER = ["Fast-Moving", "Seasonal", "Intermediate"]
PENALTY_PER_Z_VIOLATION_USD = 300_000
PRIOR_MEAN, PRIOR_VAR = 0.0, 2.0e12
OBS_VAR = 5.0e11


def segment_tiers(segment):
    default_z = lib.Z_BY_SEGMENT.get(segment, lib.Z_DEFAULT)
    default_pct = norm.cdf(default_z)
    tiers = {f"{p * 100:.0f}%": norm.ppf(p) for p in BASE_TIER_PCTS}
    tiers[f"current({default_pct * 100:.1f}%)"] = default_z
    return tiers


def tier_order_penalty(z_by_segment):
    penalty = 0.0
    for hi, lo in zip(TIER_ORDER, TIER_ORDER[1:]):
        gap = z_by_segment[lo] - z_by_segment[hi]
        if gap > 0:
            penalty += gap * PENALTY_PER_Z_VIOLATION_USD
    return penalty


def build_rolling_policy(data_dir, as_of_week, z_by_segment):
    orig = dict(lib.Z_BY_SEGMENT)
    try:
        lib.Z_BY_SEGMENT.clear()
        lib.Z_BY_SEGMENT.update(z_by_segment)
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


def main(rounds_per_segment=5, seed=13, as_of_week="2025-W52", data_dir=None):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "bandit_rolling_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")
    baseline_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")

    rng = np.random.default_rng(seed)
    current_best = {s: lib.Z_BY_SEGMENT.get(s, lib.Z_DEFAULT) for s in SEGMENTS}
    history = []

    round_n = 0
    for seg in SEGMENTS:
        tiers = segment_tiers(seg)
        tier_names = list(tiers)
        belief = {t: [PRIOR_MEAN, PRIOR_VAR] for t in tiers}

        for r in range(rounds_per_segment):
            round_n += 1
            round_seed = 2026 + round_n
            if r < len(tier_names):
                played_tier = tier_names[r]
            else:
                draws = {t: rng.normal(m, np.sqrt(v)) for t, (m, v) in belief.items()}
                played_tier = max(draws, key=draws.get)

            z_trial = dict(current_best)
            z_trial[seg] = tiers[played_tier]
            penalty = tier_order_penalty(z_trial)

            tag = " [forced]" if r < len(tier_names) else ""
            print(f"\n--- [{seg}] round {r + 1}/{rounds_per_segment}{tag}: building 52-week policy, "
                  f"playing {played_tier} ---")
            policy_csv = build_rolling_policy(data_dir, as_of_week, z_trial)
            run_name = f"seg-{seg.replace(' ', '')}_r{r:02d}"

            kpi = score(policy_csv, state_csv, run_name, trial_out_root, seed=round_seed)
            base_kpi = score(baseline_policy_csv, state_csv, f"{run_name}_base", trial_out_root, seed=round_seed)
            raw_reward = base_kpi["total_landed_cost_usd"] - kpi["total_landed_cost_usd"]
            reward = raw_reward - penalty

            m0, v0 = belief[played_tier]
            post_var = 1.0 / (1.0 / v0 + 1.0 / OBS_VAR)
            post_mean = post_var * (m0 / v0 + reward / OBS_VAR)
            belief[played_tier] = [post_mean, post_var]

            history.append({"round": round_n, "segment": seg, "arm_played": played_tier,
                             "forced_exploration": r < len(tier_names),
                             "raw_reward_usd": raw_reward, "tier_order_penalty_usd": penalty,
                             "reward_usd": reward, "fill_rate": kpi["customer_fill_rate"],
                             "belief_mean": post_mean, "belief_std": np.sqrt(post_var)})
            print(f"  reward ${reward:+,.0f} (raw ${raw_reward:+,.0f}, penalty ${penalty:,.0f})  "
                  f"fill {kpi['customer_fill_rate']:.2%}  belief now ${post_mean:+,.0f} +/- ${np.sqrt(post_var):,.0f}")

        best_tier = max(belief, key=lambda t: belief[t][0])
        current_best[seg] = tiers[best_tier]
        print(f"  -> {seg} converged to {best_tier} (z={tiers[best_tier]:.3f})")

    print("\nFinal converged service-level (z) per segment:")
    for seg in SEGMENTS:
        print(f"  {seg:12s}: z={current_best[seg]:.3f}  (CSL {norm.cdf(current_best[seg]):.1%})")

    final_policy_csv = build_rolling_policy(data_dir, as_of_week, current_best)
    shutil.copy(final_policy_csv, os.path.join(out_dir, "bandit_rolling_policy.csv"))
    pd.DataFrame(history).to_csv(os.path.join(out_dir, "bandit_rolling_belief_history.csv"), index=False)
    print(f"\nWrote {out_dir}/bandit_rolling_policy.csv, bandit_rolling_belief_history.csv")
    return os.path.join(out_dir, "bandit_rolling_policy.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds-per-segment", type=int, default=5)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    main(rounds_per_segment=args.rounds_per_segment, seed=args.seed)
