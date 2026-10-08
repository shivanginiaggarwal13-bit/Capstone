"""
Alt MEIO Model 4 — Thompson Sampling Bandit over Service-Level Tiers (v2)
==============================================================================

A working implementation of the "6.3 Service-level target selection"
decision point specified in this project's own `Obsidian-Notes/Multi-Armed
Bandits — Supply Brain Strategy.md`. v2 fixes four issues found by a
qualitative self-review of v1, in priority order:

1. **Arms now include the segment's own current default**, not just the 4
   fixed tiers {90,95,97,99}%. v1's fixed tiers never included a segment's
   ACTUAL default CSL (e.g. Fast-Moving's z=2.05 is ~98%, not exactly any
   of the 4 offered tiers), so the bandit could never produce the same
   validating "confirms the hand-tuned default was already good" result
   `sim_optimize_model.py` got by always including trial 0 = current
   defaults. Now it can.

2. **Forced initial exploration**: the first `len(tiers)` rounds of each
   segment's bandit play every arm once, in order, before Thompson
   Sampling proper begins. v1's rolling run (3 rounds, 4 arms) converged
   Fast-Moving to a tier that was NEVER PLAYED -- its belief was still the
   untouched prior mean (0), which happened to beat every arm that WAS
   played (all scored negative that round). That's not evidence the
   unplayed arm is best; it's an artifact of too few rounds. This is now
   structurally impossible.

3. **Tier-order penalty in the reward.** The Accenture slide's own
   "Target Service Level Optimization" objective explicitly includes "a
   linear penalty for breaking tier order" -- v1 had no such term, so
   nothing stopped Intermediate from converging to MORE protection than
   Fast-Moving, contradicting the dataset's own baseline convention
   (Fast-Moving > Seasonal > Intermediate, its z's are 2.05 > 1.64 > 1.28).
   `PENALTY_PER_Z_VIOLATION_USD` is a disclosed modeling choice, not a
   calibrated business input -- no real $-per-tier-break number exists to
   calibrate against.

4. **Seasonal-phase awareness -- scoped, not the full contextual rebuild.**
   The strategy note calls segment x seasonal-phase context "required, not
   optional," and warns a vanilla bandit "will keep confidently
   recommending the summer answer into the fall." Properly wiring phase
   context into `bandit_rolling.py`'s 52-week loop would mean each of the
   52 weeks uses a phase-specific arm and a single round's reward is a
   MIXTURE of three phases' choices -- a genuinely different (and much
   larger) bandit-attribution problem than what's built here, not
   attempted in this round. What IS tested here, on the frozen basis:
   does the Seasonal segment's converged tier actually DIFFER between a
   peak week and a trough week (found via `build_seasonal_index()`,
   pooled across the Seasonal segment's own subcategories)? If it does,
   that's direct evidence the fuller contextual rebuild is worth the
   investment; if it doesn't, less so. See `run_seasonal_phase_check()`.

Output: alt_output/bandit_policy.csv, alt_output/bandit_belief_history.csv,
        alt_output/bandit_seasonal_phase_check.csv
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

BASE_TIER_PCTS = [0.90, 0.95, 0.97, 0.99]
SEGMENTS = ["Fast-Moving", "Seasonal", "Intermediate"]
TIER_ORDER = ["Fast-Moving", "Seasonal", "Intermediate"]  # baseline's own z ordering: 2.05 > 1.64 > 1.28
PENALTY_PER_Z_VIOLATION_USD = 300_000  # disclosed modeling choice -- see docstring point 3
PRIOR_MEAN, PRIOR_VAR = 0.0, 2.0e12
OBS_VAR = 5.0e11


def segment_tiers(segment):
    """This segment's arms: the 4 fixed CSL tiers plus its own current
    hand-tuned default as a named 5th arm (fix #1)."""
    default_z = lib.Z_BY_SEGMENT.get(segment, lib.Z_DEFAULT)
    default_pct = norm.cdf(default_z)
    tiers = {f"{p * 100:.0f}%": norm.ppf(p) for p in BASE_TIER_PCTS}
    tiers[f"current({default_pct * 100:.1f}%)"] = default_z
    return tiers


def tier_order_penalty(z_by_segment):
    """$ penalty for violating Fast-Moving >= Seasonal >= Intermediate
    service (fix #3). Zero if the order holds."""
    penalty = 0.0
    for hi, lo in zip(TIER_ORDER, TIER_ORDER[1:]):
        gap = z_by_segment[lo] - z_by_segment[hi]  # positive = lo has MORE service than hi -- a violation
        if gap > 0:
            penalty += gap * PENALTY_PER_Z_VIOLATION_USD
    return penalty


def build_policy(data_dir, as_of_week, z_by_segment, target_week_num=None):
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
        lib.Z_BY_SEGMENT.clear()
        lib.Z_BY_SEGMENT.update(orig)


def score(policy_csv, state_csv, run_name, out_root, seed):
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--seed", str(seed),
           "--out-root", out_root, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_root, run_name, "run_kpis.json")) as f:
        return json.load(f)


def peak_trough_weeks(data_dir, segment):
    """Pooled across `segment`'s own subcategories: the week-of-year with
    the highest and lowest average seasonal index (fix #4's data source)."""
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku = pd.read_csv(f"{data_dir}/sku_master.csv", usecols=["subcategory", "segment"])
    subcats = sku.loc[sku.segment == segment, "subcategory"].unique()
    df = seasonal_index[seasonal_index.subcategory.isin(subcats)]
    avg = df.groupby("week_of_year")["seasonal_index"].mean()
    return int(avg.idxmax()), int(avg.idxmin()), avg


def run_segment_bandit(segment, as_of_week, target_week_num, other_z, rounds, rng,
                        data_dir, state_csv, baseline_policy_csv, trial_out_root, label,
                        history):
    """One segment's Thompson Sampling loop against a FIXED background for
    every other segment. Forced round-robin through every arm first (fix
    #2), then genuine Thompson Sampling for any rounds beyond that.
    Returns (converged_tier_name, converged_z)."""
    tiers = segment_tiers(segment)
    tier_names = list(tiers)
    belief = {t: [PRIOR_MEAN, PRIOR_VAR] for t in tiers}

    for r in range(rounds):
        if r < len(tier_names):
            played_tier = tier_names[r]  # forced exploration: play every arm once first
        else:
            draws = {t: rng.normal(m, np.sqrt(v)) for t, (m, v) in belief.items()}
            played_tier = max(draws, key=draws.get)

        z_trial = dict(other_z)
        z_trial[segment] = tiers[played_tier]
        penalty = tier_order_penalty(z_trial)

        round_seed = 2026 + len(history) + 1
        policy = build_policy(data_dir, as_of_week, z_trial, target_week_num=target_week_num)
        run_name = f"{label}_r{r:02d}"
        trial_csv = os.path.join(trial_out_root, f"{run_name}_policy.csv")
        os.makedirs(trial_out_root, exist_ok=True)
        policy.to_csv(trial_csv, index=False)
        kpi = score(trial_csv, state_csv, run_name, trial_out_root, seed=round_seed)
        base_kpi = score(baseline_policy_csv, state_csv, f"{run_name}_base", trial_out_root, seed=round_seed)
        raw_reward = base_kpi["total_landed_cost_usd"] - kpi["total_landed_cost_usd"]
        reward = raw_reward - penalty

        m0, v0 = belief[played_tier]
        post_var = 1.0 / (1.0 / v0 + 1.0 / OBS_VAR)
        post_mean = post_var * (m0 / v0 + reward / OBS_VAR)
        belief[played_tier] = [post_mean, post_var]

        history.append({"label": label, "round": r, "arm_played": played_tier,
                         "forced_exploration": r < len(tier_names),
                         "raw_reward_usd": raw_reward, "tier_order_penalty_usd": penalty,
                         "reward_usd": reward, "fill_rate": kpi["customer_fill_rate"],
                         "belief_mean": post_mean, "belief_std": np.sqrt(post_var)})
        tag = " [forced]" if r < len(tier_names) else ""
        print(f"  [{label}] round {r + 1}/{rounds}{tag}: played {played_tier}  "
              f"reward ${reward:+,.0f} (raw ${raw_reward:+,.0f}, penalty ${penalty:,.0f})  "
              f"fill {kpi['customer_fill_rate']:.2%}")

    best_tier = max(belief, key=lambda t: belief[t][0])
    print(f"  -> {label} converged to {best_tier} (z={tiers[best_tier]:.3f}, "
          f"belief ${belief[best_tier][0]:+,.0f})\n")
    return best_tier, tiers[best_tier]


def run_seasonal_phase_check(as_of_week, current_best, rounds, rng, data_dir, state_csv,
                              baseline_policy_csv, trial_out_root, history):
    """Fix #4: does the Seasonal segment's converged tier actually differ
    between a peak week and a trough week? Runs two INDEPENDENT bandits
    (own belief sets, not shared with the main 3-segment run), holding
    Fast-Moving and Intermediate at their already-converged choices from
    the main run."""
    peak_woy, trough_woy, avg = peak_trough_weeks(data_dir, "Seasonal")
    period_index = lib.load_period_index(data_dir)
    as_of_num = int(period_index.loc[period_index.period == as_of_week, "week_num"].iloc[0])

    def week_num_for(week_of_year):
        target_period = f"2026-W{week_of_year:02d}"
        row = period_index[period_index.period == target_period]
        return int(row["week_num"].iloc[0]) if len(row) else None

    peak_wk, trough_wk = week_num_for(peak_woy), week_num_for(trough_woy)
    print(f"\nSeasonal segment: peak week-of-year={peak_woy} (avg index {avg[peak_woy]:.2f}), "
          f"trough week-of-year={trough_woy} (avg index {avg[trough_woy]:.2f})")

    other_z = {s: current_best[s] for s in SEGMENTS if s != "Seasonal"}
    results = {}
    for phase, wk in [("peak", peak_wk), ("trough", trough_wk)]:
        if wk is None:
            print(f"  {phase} week-of-year {peak_woy if phase == 'peak' else trough_woy} "
                  f"not in the forward horizon -- skipping")
            continue
        tier, z = run_segment_bandit("Seasonal", as_of_week, wk, other_z, rounds, rng,
                                      data_dir, state_csv, baseline_policy_csv, trial_out_root,
                                      f"seasonal-{phase}", history)
        results[phase] = {"week_of_year": peak_woy if phase == "peak" else trough_woy, "tier": tier, "z": z}
    return results


def main(rounds_per_segment=8, seasonal_phase_rounds=6, seed=7, as_of_week="2025-W52", data_dir=None):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "bandit_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")
    baseline_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")

    rng = np.random.default_rng(seed)
    current_best = {s: lib.Z_BY_SEGMENT.get(s, lib.Z_DEFAULT) for s in SEGMENTS}
    history = []

    print("=== Main run: one bandit per segment, sequential, each against the others' latest choice ===")
    for seg in SEGMENTS:
        other_z = {s: current_best[s] for s in SEGMENTS if s != seg}
        _tier, z = run_segment_bandit(seg, as_of_week, None, other_z, rounds_per_segment, rng,
                                       data_dir, state_csv, baseline_policy_csv, trial_out_root,
                                       f"seg-{seg.replace(' ', '')}", history)
        current_best[seg] = z

    print("Final converged service-level (z) per segment:")
    for seg in SEGMENTS:
        print(f"  {seg:12s}: z={current_best[seg]:.3f}  (CSL {norm.cdf(current_best[seg]):.1%})")

    print("\n=== Seasonal-phase check: does the answer change between peak and trough weeks? ===")
    phase_results = run_seasonal_phase_check(as_of_week, current_best, seasonal_phase_rounds, rng,
                                              data_dir, state_csv, baseline_policy_csv, trial_out_root, history)
    if len(phase_results) == 2:
        # Compare the actual z-values, not tier LABELS: two arms can be
        # numerically almost identical (the fixed 95% tier vs a segment's
        # own ~94.9% current default, e.g.) and "win" in different phases
        # by a hair, which looks like "different tiers" if you compare
        # names but isn't a real seasonal signal at all. Found exactly
        # this in the first real run -- z=1.645 vs z=1.640, a difference
        # of 0.005, reported as "DIFFERS" by a naive label comparison.
        z_gap = abs(phase_results["peak"]["z"] - phase_results["trough"]["z"])
        MEANINGFUL_Z_GAP = 0.15  # ~ half the spacing between adjacent fixed tiers (90/95/97/99%)
        if z_gap >= MEANINGFUL_Z_GAP:
            print(f"DIFFERS: peak -> {phase_results['peak']['tier']} (z={phase_results['peak']['z']:.3f}), "
                  f"trough -> {phase_results['trough']['tier']} (z={phase_results['trough']['z']:.3f}), "
                  f"gap {z_gap:.3f}. Evidence the fuller phase-contextual rebuild would matter.")
        else:
            print(f"SAME, functionally: peak z={phase_results['peak']['z']:.3f} vs "
                  f"trough z={phase_results['trough']['z']:.3f} (gap {z_gap:.3f}, below the "
                  f"{MEANINGFUL_Z_GAP} threshold) -- different tier LABELS won in each phase, but the "
                  f"underlying service levels are essentially the same. No real evidence from this "
                  f"check that phase context would change the answer; {seasonal_phase_rounds} rounds "
                  f"each is also still a small sample.")

    final_policy = build_policy(data_dir, as_of_week, current_best)
    final_policy.to_csv(os.path.join(out_dir, "bandit_policy.csv"), index=False)
    pd.DataFrame(history).to_csv(os.path.join(out_dir, "bandit_belief_history.csv"), index=False)
    pd.DataFrame([{"phase": k, **v} for k, v in phase_results.items()]).to_csv(
        os.path.join(out_dir, "bandit_seasonal_phase_check.csv"), index=False)
    print(f"\nWrote {out_dir}/bandit_policy.csv, bandit_belief_history.csv, bandit_seasonal_phase_check.csv")
    return os.path.join(out_dir, "bandit_policy.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds-per-segment", type=int, default=8)
    ap.add_argument("--seasonal-phase-rounds", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    main(rounds_per_segment=args.rounds_per_segment, seasonal_phase_rounds=args.seasonal_phase_rounds,
         seed=args.seed)
