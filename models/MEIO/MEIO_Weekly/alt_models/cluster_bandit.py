"""
Cluster Bandit — Thompson Sampling Over Service-Level Tiers, Per the
Segmentation Deliverable's 4 Clusters (not the original 3-way segment)
===============================================================================

`cluster_csl_segment_breakdown.py` found that adopting the Clustering &
Segmentation deliverable's prescribed CSL ladder (`cluster_service_levels.csv`)
as-is drops fill rate meaningfully for the two long-tail clusters --
Long-Tail Erratic and, worse, Long-Tail Supply-Risk (the 16 SKUs with the
worst lead-time variability) -- while leaving the two big "core" clusters
essentially unchanged. That ladder's own report is explicit that its CSL
split is "driven entirely by the value floor/cap business rule, not by the
underlying economics" (median newsvendor CR is flat at 95.3-95.5% across
all four clusters) -- it was never twin-validated.

This applies the exact same twin-driven Thompson Sampling search
`bandit_model.py` already runs over the original 3 segments, but over the
4 cluster archetypes instead, via `meio_weekly_lib.Z_OVERRIDE_BY_SKU` (the
per-SKU seam added for `rerun_with_cluster_csl.py`). Same v2 fixes as
`bandit_model.py`: forced round-robin exploration before genuine Thompson
Sampling, and a named "current" arm per cluster so the bandit can confirm
the segmentation deliverable's own recommendation rather than being forced
away from it.

**Deliberately NO tier-order penalty here**, unlike `bandit_model.py`.
Fast-Moving >= Seasonal >= Intermediate was a disclosed, deliberate
convention baked into this dataset's own baseline policy. No equivalent
convention exists for the 4 cluster archetypes -- segmentation_v4's own
report says the underlying economics don't cleanly separate them, and the
whole point of this run is to let the twin discover whether Long-Tail
Supply-Risk specifically needs MORE protection than its revenue rank would
suggest (its own worst-in-class lead-time variability, not its revenue
share, is what should drive its z). Imposing a revenue-rank ordering
constraint would beg exactly the question this script exists to answer.

Frozen basis only (as-of 2025-W52, target 2026-W01, one week's policy
frozen for the full synthetic 2026 year) -- same scope `bandit_model.py`'s
own frozen run used, for speed and direct comparability.

    python cluster_bandit.py

Output: alt_output/cluster_bandit_policy.csv, alt_output/cluster_bandit_belief_history.csv
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
import meio_weekly_lib as lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")
CLUSTER_CSV = os.path.join(REPO_ROOT, "Clustering & Segmentation", "segmentation_v4", "sku_clusters.csv")

BASE_TIER_PCTS = [0.90, 0.95, 0.97, 0.99]
CLUSTERS = ["Flagship Promo-Driven", "Mid-Tier Core", "Long-Tail Erratic", "Long-Tail Supply-Risk"]
PRIOR_MEAN, PRIOR_VAR = 0.0, 2.0e12
OBS_VAR = 5.0e11


def load_cluster_map(data_dir):
    c = pd.read_csv(CLUSTER_CSV, usecols=["sku_id", "archetype", "safety_factor_z"])
    sku_to_archetype = dict(zip(c.sku_id, c.archetype))
    archetype_default_z = c.drop_duplicates("archetype").set_index("archetype")["safety_factor_z"].to_dict()
    return sku_to_archetype, archetype_default_z


def cluster_tiers(default_z):
    default_pct = norm.cdf(default_z)
    tiers = {f"{p * 100:.0f}%": norm.ppf(p) for p in BASE_TIER_PCTS}
    tiers[f"current({default_pct * 100:.1f}%)"] = default_z
    return tiers


def build_policy(data_dir, as_of_week, z_by_archetype, sku_to_archetype, target_week_num=None):
    orig = dict(lib.Z_OVERRIDE_BY_SKU)
    try:
        lib.Z_OVERRIDE_BY_SKU.clear()
        lib.Z_OVERRIDE_BY_SKU.update({sku: z_by_archetype[arch] for sku, arch in sku_to_archetype.items()})

        dfc = lib.load_demand_forecast(data_dir)
        period_index = lib.load_period_index(data_dir)
        seasonal_index = lib.build_seasonal_index(data_dir)
        sku_subcat = lib.load_sku_subcategory(data_dir)
        active_rdc, active_cdc = lib.active_nodes(dfc)
        rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
        leadtime_by_dc = {**rdc_lt, **cdc_lt}
        cost = lib.load_sku_cost(data_dir)

        as_of_num = int(period_index.loc[period_index.period == as_of_week, "week_num"].iloc[0])
        target_num = target_week_num if target_week_num is not None else as_of_num + 1
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
        lib.Z_OVERRIDE_BY_SKU.clear()
        lib.Z_OVERRIDE_BY_SKU.update(orig)


def score(policy_csv, state_csv, run_name, out_root, seed):
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--seed", str(seed),
           "--out-root", out_root, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_root, run_name, "run_kpis.json")) as f:
        return json.load(f)


def run_cluster_bandit(archetype, as_of_week, other_z, rounds, rng, data_dir, state_csv,
                        baseline_policy_csv, trial_out_root, label, history, sku_to_archetype,
                        default_z_by_archetype):
    tiers = cluster_tiers(default_z_by_archetype[archetype])
    tier_names = list(tiers)
    belief = {t: [PRIOR_MEAN, PRIOR_VAR] for t in tiers}

    for r in range(rounds):
        if r < len(tier_names):
            played_tier = tier_names[r]
        else:
            draws = {t: rng.normal(m, np.sqrt(v)) for t, (m, v) in belief.items()}
            played_tier = max(draws, key=draws.get)

        z_trial = dict(other_z)
        z_trial[archetype] = tiers[played_tier]

        round_seed = 2026 + len(history) + 1
        policy = build_policy(data_dir, as_of_week, z_trial, sku_to_archetype)
        run_name = f"{label}_r{r:02d}"
        trial_csv = os.path.join(trial_out_root, f"{run_name}_policy.csv")
        os.makedirs(trial_out_root, exist_ok=True)
        policy.to_csv(trial_csv, index=False)
        kpi = score(trial_csv, state_csv, run_name, trial_out_root, seed=round_seed)
        base_kpi = score(baseline_policy_csv, state_csv, f"{run_name}_base", trial_out_root, seed=round_seed)
        reward = base_kpi["total_landed_cost_usd"] - kpi["total_landed_cost_usd"]

        m0, v0 = belief[played_tier]
        post_var = 1.0 / (1.0 / v0 + 1.0 / OBS_VAR)
        post_mean = post_var * (m0 / v0 + reward / OBS_VAR)
        belief[played_tier] = [post_mean, post_var]

        history.append({"label": label, "round": r, "arm_played": played_tier,
                         "forced_exploration": r < len(tier_names), "reward_usd": reward,
                         "fill_rate": kpi["customer_fill_rate"],
                         "internal_replen_fill_rate": kpi["internal_replen_fill_rate"],
                         "belief_mean": post_mean, "belief_std": np.sqrt(post_var)})
        tag = " [forced]" if r < len(tier_names) else ""
        print(f"  [{label}] round {r + 1}/{rounds}{tag}: played {played_tier}  "
              f"reward ${reward:+,.0f}  fill {kpi['customer_fill_rate']:.2%}  "
              f"internal_replen {kpi['internal_replen_fill_rate']:.2%}")

    best_tier = max(belief, key=lambda t: belief[t][0])
    print(f"  -> {label} converged to {best_tier} (z={tiers[best_tier]:.3f}, "
          f"belief ${belief[best_tier][0]:+,.0f})\n")
    return best_tier, tiers[best_tier]


def main(rounds_per_cluster=8, seed=7, as_of_week="2025-W52", data_dir=None):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "cluster_bandit_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")
    baseline_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")

    sku_to_archetype, default_z_by_archetype = load_cluster_map(data_dir)
    print(f"Cluster defaults (segmentation deliverable's own recommendation): "
          f"{ {k: round(v, 3) for k, v in default_z_by_archetype.items()} }")

    rng = np.random.default_rng(seed)
    current_best = dict(default_z_by_archetype)
    history = []

    print("\n=== One bandit per cluster, sequential, each against the others' latest choice ===")
    for arch in CLUSTERS:
        other_z = {a: current_best[a] for a in CLUSTERS if a != arch}
        _tier, z = run_cluster_bandit(arch, as_of_week, other_z, rounds_per_cluster, rng,
                                       data_dir, state_csv, baseline_policy_csv, trial_out_root,
                                       f"clu-{arch.replace(' ', '')}", history, sku_to_archetype,
                                       default_z_by_archetype)
        current_best[arch] = z

    print("Final converged service-level (z) per cluster:")
    for arch in CLUSTERS:
        default_z = default_z_by_archetype[arch]
        moved = "moved" if abs(current_best[arch] - default_z) > 1e-9 else "unchanged"
        print(f"  {arch:24s}: z={current_best[arch]:.3f}  (CSL {norm.cdf(current_best[arch]):.1%})  "
              f"vs segmentation deliverable's {default_z:.3f}  [{moved}]")

    final_policy = build_policy(data_dir, as_of_week, current_best, sku_to_archetype)
    final_policy.to_csv(os.path.join(out_dir, "cluster_bandit_policy.csv"), index=False)
    pd.DataFrame(history).to_csv(os.path.join(out_dir, "cluster_bandit_belief_history.csv"), index=False)
    print(f"\nWrote {out_dir}/cluster_bandit_policy.csv, cluster_bandit_belief_history.csv")
    return os.path.join(out_dir, "cluster_bandit_policy.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds-per-cluster", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    main(rounds_per_cluster=args.rounds_per_cluster, seed=args.seed)
