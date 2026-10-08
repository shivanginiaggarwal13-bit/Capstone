"""
Cluster Bandit, Rolling-Policy Variant — Thompson Sampling Over the Full
2026 Horizon, Per the Segmentation Deliverable's 4 Clusters
===============================================================================

`cluster_bandit.py` found the twin prefers MORE protection than the
segmentation deliverable's ladder for 3 of 4 clusters -- but that search ran
on the FROZEN basis (one week's policy, frozen for the whole simulated
year), the same basis that capped every OTHER model in this project around
80-85% fill regardless of target CSL. This rebuilds the same search on the
ROLLING basis instead (policy recomputed every week across the full
52-week horizon), matching exactly how `bandit_rolling.py` and
`clark_scarf_v2_rolling.py` earned this project's 92-94% headline numbers --
the fair comparison the frozen-basis result couldn't give on its own.

Same v2 design as `cluster_bandit.py`: forced round-robin exploration, a
named "current" arm per cluster (the segmentation deliverable's own
recommendation), no tier-order penalty (no disclosed ordering convention
exists for these 4 archetypes -- see `cluster_bandit.py`'s docstring).

Cost note, same shape as `bandit_rolling.py`: each round rebuilds a full
52-week policy (~90-140s) plus two full-horizon twin scores. Five arms need
at least 5 rounds/cluster just to finish forced exploration -- no budget
left in this pass for genuine Thompson rounds beyond that, so
`rounds_per_cluster` defaults to exactly 5, same reasoning
`bandit_rolling.py` used. Read this run as "forced exploration only," not a
converged bandit.

    python cluster_bandit_rolling.py

Output: alt_output/cluster_bandit_rolling_policy.csv, alt_output/cluster_bandit_rolling_belief_history.csv
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meio_weekly_lib as lib
import roll_forward_policy
import cluster_bandit as cb

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")


def build_rolling_policy(data_dir, as_of_week, z_by_archetype, sku_to_archetype, out_path):
    orig = dict(lib.Z_OVERRIDE_BY_SKU)
    try:
        lib.Z_OVERRIDE_BY_SKU.clear()
        lib.Z_OVERRIDE_BY_SKU.update({sku: z_by_archetype[arch] for sku, arch in sku_to_archetype.items()})
        return roll_forward_policy.run(data_dir, as_of_week, out_path=out_path)
    finally:
        lib.Z_OVERRIDE_BY_SKU.clear()
        lib.Z_OVERRIDE_BY_SKU.update(orig)


def score(policy_csv, state_csv, run_name, out_root, seed):
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--seed", str(seed),
           "--out-root", out_root, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_root, run_name, "run_kpis.json")) as f:
        return json.load(f)


def main(rounds_per_cluster=5, seed=13, as_of_week="2025-W52", data_dir=None):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "cluster_bandit_rolling_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")
    baseline_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")

    sku_to_archetype, default_z_by_archetype = cb.load_cluster_map(data_dir)
    print(f"Cluster defaults (segmentation deliverable's own recommendation): "
          f"{ {k: round(v, 3) for k, v in default_z_by_archetype.items()} }")

    rng = np.random.default_rng(seed)
    current_best = dict(default_z_by_archetype)
    history = []

    round_n = 0
    for arch in cb.CLUSTERS:
        tiers = cb.cluster_tiers(default_z_by_archetype[arch])
        tier_names = list(tiers)
        belief = {t: [cb.PRIOR_MEAN, cb.PRIOR_VAR] for t in tiers}

        for r in range(rounds_per_cluster):
            round_n += 1
            round_seed = 2026 + round_n
            if r < len(tier_names):
                played_tier = tier_names[r]
            else:
                draws = {t: rng.normal(m, np.sqrt(v)) for t, (m, v) in belief.items()}
                played_tier = max(draws, key=draws.get)

            z_trial = dict(current_best)
            z_trial[arch] = tiers[played_tier]

            tag = " [forced]" if r < len(tier_names) else ""
            print(f"\n--- [{arch}] round {r + 1}/{rounds_per_cluster}{tag}: building 52-week policy, "
                  f"playing {played_tier} ---")
            policy_csv = build_rolling_policy(data_dir, as_of_week, z_trial, sku_to_archetype,
                                               os.path.join(trial_out_root, "_building_policy.csv"))
            run_name = f"clu-{arch.replace(' ', '')}_r{r:02d}"

            kpi = score(policy_csv, state_csv, run_name, trial_out_root, seed=round_seed)
            base_kpi = score(baseline_policy_csv, state_csv, f"{run_name}_base", trial_out_root, seed=round_seed)
            reward = base_kpi["total_landed_cost_usd"] - kpi["total_landed_cost_usd"]

            m0, v0 = belief[played_tier]
            post_var = 1.0 / (1.0 / v0 + 1.0 / cb.OBS_VAR)
            post_mean = post_var * (m0 / v0 + reward / cb.OBS_VAR)
            belief[played_tier] = [post_mean, post_var]

            history.append({"round": round_n, "cluster": arch, "arm_played": played_tier,
                             "forced_exploration": r < len(tier_names), "reward_usd": reward,
                             "fill_rate": kpi["customer_fill_rate"],
                             "internal_replen_fill_rate": kpi["internal_replen_fill_rate"],
                             "belief_mean": post_mean, "belief_std": np.sqrt(post_var)})
            print(f"  reward ${reward:+,.0f}  fill {kpi['customer_fill_rate']:.2%}  "
                  f"internal_replen {kpi['internal_replen_fill_rate']:.2%}  "
                  f"belief now ${post_mean:+,.0f} +/- ${np.sqrt(post_var):,.0f}")

        best_tier = max(belief, key=lambda t: belief[t][0])
        current_best[arch] = tiers[best_tier]
        print(f"  -> {arch} converged to {best_tier} (z={tiers[best_tier]:.3f})")

    print("\nFinal converged service-level (z) per cluster:")
    for arch in cb.CLUSTERS:
        print(f"  {arch:24s}: z={current_best[arch]:.3f}  (CSL {norm.cdf(current_best[arch]):.1%})")

    final_policy_csv = build_rolling_policy(data_dir, as_of_week, current_best, sku_to_archetype,
                                             os.path.join(out_dir, "cluster_bandit_rolling_policy.csv"))
    pd.DataFrame(history).to_csv(os.path.join(out_dir, "cluster_bandit_rolling_belief_history.csv"), index=False)
    print(f"\nWrote {out_dir}/cluster_bandit_rolling_policy.csv, cluster_bandit_rolling_belief_history.csv")
    return final_policy_csv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds-per-cluster", type=int, default=5)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    main(rounds_per_cluster=args.rounds_per_cluster, seed=args.seed)
