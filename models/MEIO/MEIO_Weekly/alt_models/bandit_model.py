"""
Alt MEIO Model 4 — Thompson Sampling Bandit over Service-Level Tiers
========================================================================

A real, working (if small-scale) implementation of the "6.3 Service-level
target selection" decision point already specified in this project's own
`Obsidian-Notes/Multi-Armed Bandits — Supply Brain Strategy.md`, not a
generic RL system invented for this comparison. That note recommends
Thompson Sampling specifically because it expresses uncertainty as a
belief distribution rather than a point estimate -- see its worked example
in section 4, which this script's round-by-round belief table mirrors.

Setup, per that note's §6.3 and §5 (seasonal-phase context noted but not
implemented here -- see Scope below):
    Arms   = 4 discrete CSL tiers per SKU segment: 90% / 95% / 97% / 99%
             (z = 1.28 / 1.64 / 1.88 / 2.33), replacing the fixed
             Z_BY_SEGMENT value for that segment.
    Reward = $ saved that round vs. the dataset's own baseline policy,
             as reported by the digital twin (baseline_total_landed_cost
             - trial_total_landed_cost) -- mirrors the note's worked
             example reward definition exactly.
    Rounds = repeated twin evaluations; belief narrows toward the
             observed reward each round (Normal-Normal conjugate update
             -- simpler than the note's implied Beta/Normal machinery, but
             the same exploration/exploitation shape: draw a sample from
             each arm's belief, play the highest draw, narrow that arm's
             belief toward what was observed).

Three independent bandits run, one per segment (Fast-Moving, Seasonal,
Intermediate) -- a segment's SKUs get the round's sampled tier, every
other segment stays at its own CURRENT BEST arm (not the original fixed
default), so bandits converge somewhat jointly rather than each assuming
the others are frozen at their un-optimized starting point.

Scope vs. the full documented design: no seasonal-phase context (one
belief per segment, not per segment x phase -- the note itself says this
is fine for Fast-Moving/Intermediate and matters most for Seasonal, so
treat Seasonal's result here as the more scope-limited one), no sliding
window for non-stationarity, no batched multi-week feedback. A real
build should add those before trusting this beyond a demonstration.

Output: alt_output/bandit_policy.csv, alt_output/bandit_belief_history.csv
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

TIERS = {"90%": norm.ppf(0.90), "95%": norm.ppf(0.95), "97%": norm.ppf(0.97), "99%": norm.ppf(0.99)}
SEGMENTS = ["Fast-Moving", "Seasonal", "Intermediate"]
PRIOR_MEAN, PRIOR_VAR = 0.0, 2.0e12          # wide prior in $^2 -- "could be anywhere"
OBS_VAR = 5.0e11                             # assumed noise variance per twin observation


def build_policy(data_dir, as_of_week, z_by_segment):
    orig = dict(lib.Z_BY_SEGMENT)
    try:
        lib.Z_BY_SEGMENT.clear()
        lib.Z_BY_SEGMENT.update(z_by_segment)

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
        lib.Z_BY_SEGMENT.clear()
        lib.Z_BY_SEGMENT.update(orig)


def score(policy_csv, state_csv, run_name, out_root, seed):
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--seed", str(seed),
           "--out-root", out_root, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_root, run_name, "run_kpis.json")) as f:
        return json.load(f)


def main(rounds_per_segment=8, seed=7, as_of_week="2025-W52", data_dir=None):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "bandit_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")
    baseline_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")

    rng = np.random.default_rng(seed)

    # belief[segment][tier] = (mean, var); current-best z per segment used for
    # whichever segment ISN'T being played this round
    belief = {s: {t: [PRIOR_MEAN, PRIOR_VAR] for t in TIERS} for s in SEGMENTS}
    current_best = {s: lib.Z_BY_SEGMENT.get(s, lib.Z_DEFAULT) for s in SEGMENTS}
    history = []

    round_n = 0
    for seg in SEGMENTS:
        for r in range(rounds_per_segment):
            round_n += 1
            draws = {t: rng.normal(m, np.sqrt(v)) for t, (m, v) in belief[seg].items()}
            played_tier = max(draws, key=draws.get)
            z_trial = dict(current_best)
            z_trial[seg] = TIERS[played_tier]

            round_seed = 2026 + round_n
            policy = build_policy(data_dir, as_of_week, z_trial)
            run_name = f"seg-{seg.replace(' ', '')}_r{r:02d}"
            trial_csv = os.path.join(trial_out_root, f"{run_name}_policy.csv")
            os.makedirs(trial_out_root, exist_ok=True)
            policy.to_csv(trial_csv, index=False)
            kpi = score(trial_csv, state_csv, run_name, trial_out_root, seed=round_seed)
            # baseline re-scored at the SAME seed as this round, not a fixed
            # reference seed -- otherwise "reward" partly reflects whether this
            # round's seed happened to be an easy/hard demand draw rather than
            # the arm's own merit (a real confound, not just extra noise: a
            # fixed-seed reference biases every arm's belief by that seed's own
            # deviation from typical, in the same direction every round).
            base_kpi = score(baseline_policy_csv, state_csv, f"{run_name}_base", trial_out_root, seed=round_seed)
            reward = base_kpi["total_landed_cost_usd"] - kpi["total_landed_cost_usd"]  # $ saved, higher=better

            m0, v0 = belief[seg][played_tier]
            post_var = 1.0 / (1.0 / v0 + 1.0 / OBS_VAR)
            post_mean = post_var * (m0 / v0 + reward / OBS_VAR)
            belief[seg][played_tier] = [post_mean, post_var]

            history.append({"round": round_n, "segment": seg, "arm_played": played_tier,
                             "reward_usd_saved": reward, "fill_rate": kpi["customer_fill_rate"],
                             "belief_mean": post_mean, "belief_std": np.sqrt(post_var)})
            print(f"  [{seg:12s}] round {r+1}/{rounds_per_segment}: played {played_tier}  "
                  f"reward ${reward:+,.0f}  fill {kpi['customer_fill_rate']:.2%}  "
                  f"belief now ${post_mean:+,.0f} +/- ${np.sqrt(post_var):,.0f}")

        best_tier = max(belief[seg], key=lambda t: belief[seg][t][0])
        current_best[seg] = TIERS[best_tier]
        print(f"  -> {seg} converged to {best_tier} tier (z={TIERS[best_tier]:.3f})\n")

    print("Final converged service-level tier per segment:")
    for seg in SEGMENTS:
        best_tier = max(belief[seg], key=lambda t: belief[seg][t][0])
        print(f"  {seg:12s}: {best_tier}  (z={TIERS[best_tier]:.3f}, "
              f"belief ${belief[seg][best_tier][0]:+,.0f})")

    final_policy = build_policy(data_dir, as_of_week, current_best)
    final_policy.to_csv(os.path.join(out_dir, "bandit_policy.csv"), index=False)
    pd.DataFrame(history).to_csv(os.path.join(out_dir, "bandit_belief_history.csv"), index=False)
    print(f"\nWrote {out_dir}/bandit_policy.csv, bandit_belief_history.csv")
    return os.path.join(out_dir, "bandit_policy.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds-per-segment", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    main(rounds_per_segment=args.rounds_per_segment, seed=args.seed)
