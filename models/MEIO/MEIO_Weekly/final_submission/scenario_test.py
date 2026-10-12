"""
Clark-Scarf v2 — Scenario Testing: Re-Optimized Stocking vs. Baseline
===============================================================================

Feature 2 (front-end scenario-testing agent): given a user-defined scenario
(demand surge is the only type wired so far -- see scenario_engine.py in
models/twin/), produces a NEW MEIO stocking recommendation that anticipates
it, and compares three arms:

  A. baseline    -- today's policy (alpha=0.2, netting_discount=0.0, no
                     scenario), simulated under NORMAL demand. The
                     reference point.
  B. unprepared  -- the SAME baseline policy, simulated under SCENARIO
                     demand. Shows the risk if the scenario happens and
                     stocking isn't adjusted for it.
  C. prepared    -- a policy REBUILT with the scenario's demand bump
                     applied to its own sizing, simulated under SCENARIO
                     demand. The actual recommendation.

B vs C answers "does the recommended response help, and by how much" -- the
core of what this feature is for. A vs B shows the scenario's raw impact if
ignored.

Synthetic (--mode forward) basis, matching segment_alpha_explore.py's
single-seed "what-if" diagnostic use -- a scenario test is inherently
forward-looking; there's no real-data equivalent to walk forward against
for something that hasn't happened yet.

    python scenario_test.py --scenario ../../../twin/scenario_config.example.json
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clark_scarf_v2 as csv2
import segment_alpha_explore as sae

REPO_ROOT = sae.REPO_ROOT
DEFAULT_DATA = sae.DEFAULT_DATA
STATE_CSV = sae.STATE_CSV
TWIN = sae.TWIN
RUN_OUT_DIR = sae.RUN_OUT_DIR
RESULTS_DIR = sae.RESULTS_DIR

ALPHA = 0.2                 # current recommended config
NETTING_DISCOUNT = 0.0      # current recommended config (this session's fix)
N_SIMS = 1200
SEED = 2026
AS_OF_WEEK = "2025-W52"

KPI_KEYS = ["customer_fill_rate", "internal_replen_fill_rate", "avg_inventory_value_usd",
            "holding_cost_usd", "stockout_cost_usd", "total_landed_cost_usd"]


def _latest_scenario_dir(out_root):
    """digital_twin.py's --scenario-config path writes into a standardized
    SCENARIO_####_Name folder under --out-root, IGNORING --run-name for the
    output location -- discovered running this script, not documented
    anywhere beforehand. Since calls here are sequential, not concurrent,
    "most recently created SCENARIO_* folder" is unambiguous."""
    dirs = [d for d in os.listdir(out_root)
            if d.startswith("SCENARIO_") and os.path.isdir(os.path.join(out_root, d))]
    dirs.sort(key=lambda d: os.path.getmtime(os.path.join(out_root, d)))
    return os.path.join(out_root, dirs[-1])


def run_twin_forward(run_name, policy_csv, scenario_config_path=None, seed=SEED):
    """Same pattern as segment_alpha_explore.py's run_twin_forward, plus an
    optional --scenario-config pass-through (see _latest_scenario_dir for
    why that changes where the output actually lands)."""
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", STATE_CSV, "--seed", str(seed),
           "--out-root", RUN_OUT_DIR, "--label", run_name]
    if scenario_config_path:
        cmd += ["--scenario-config", scenario_config_path]
    result = subprocess.run(cmd, check=False, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"digital_twin.py failed for {run_name}:\n{result.stderr}")
    out_dir = _latest_scenario_dir(RUN_OUT_DIR) if scenario_config_path else os.path.join(RUN_OUT_DIR, run_name)
    with open(os.path.join(out_dir, "run_kpis.json")) as f:
        kpi = json.load(f)
    inv = pd.read_parquet(os.path.join(out_dir, "inventory.parquet"))
    return kpi, inv


def build_policy(out_path, scenario_changes=None, seed=SEED):
    rng = np.random.default_rng(seed)
    return csv2.build_rolling_policy(DEFAULT_DATA, AS_OF_WEEK, ALPHA, N_SIMS, rng, out_path,
                                      netting_discount=NETTING_DISCOUNT, scenario_changes=scenario_changes)


def run_scenario(scenario_path):
    with open(scenario_path) as f:
        config = json.load(f)
    changes = config["changes"]
    name = config.get("scenario_name", "scenario")
    tag = name.replace(" ", "")
    print(f"=== Scenario: {name} ===\n  changes: {changes}")

    print("\n--- A. baseline policy, normal demand ---")
    baseline_policy_csv = os.path.join(RUN_OUT_DIR, f"policy_{tag}_baseline.parquet")
    build_policy(baseline_policy_csv)
    kpi_a, _ = run_twin_forward(f"{tag}_A_baseline", baseline_policy_csv)

    # A standardized scenario-config JSON digital_twin.py's --scenario-config
    # can consume, written fresh each run so this works for any input file
    # (the caller's own scenario_path) without touching it.
    tmp_scenario = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(config, tmp_scenario)
    tmp_scenario.close()

    print("\n--- B. baseline policy, SCENARIO demand (unprepared) ---")
    kpi_b, _ = run_twin_forward(f"{tag}_B_unprepared", baseline_policy_csv,
                                 scenario_config_path=tmp_scenario.name, seed=SEED)

    print("\n--- C. scenario-reoptimized policy, SCENARIO demand (prepared) ---")
    scenario_policy_csv = os.path.join(RUN_OUT_DIR, f"policy_{tag}_scenario.parquet")
    build_policy(scenario_policy_csv, scenario_changes=changes)
    kpi_c, _ = run_twin_forward(f"{tag}_C_prepared", scenario_policy_csv,
                                 scenario_config_path=tmp_scenario.name, seed=SEED)

    os.unlink(tmp_scenario.name)

    summary = pd.DataFrame([
        {"arm": "A: baseline (normal demand)", **{k: kpi_a[k] for k in KPI_KEYS}},
        {"arm": "B: unprepared (scenario demand, baseline policy)", **{k: kpi_b[k] for k in KPI_KEYS}},
        {"arm": "C: prepared (scenario demand, scenario-aware policy)", **{k: kpi_c[k] for k in KPI_KEYS}},
    ])
    out_csv = os.path.join(RESULTS_DIR, f"scenario_{tag}_comparison.csv")
    summary.to_csv(out_csv, index=False)
    print("\n" + summary.to_string(index=False))

    # DC-level stocking recommendation diff: what the scenario-aware policy
    # actually recommends differently, by DC -- the "recommendations for
    # MEIO based stocking across DCs" half of the feature ask.
    base_pol = pd.read_parquet(baseline_policy_csv)
    scen_pol = pd.read_parquet(scenario_policy_csv)
    merged = base_pol.merge(scen_pol, on=["period", "dc_id", "sku_id"], suffixes=("_baseline", "_scenario"))
    by_dc = merged.groupby("dc_id").agg(
        baseline_safety_stock=("safety_stock_baseline", "sum"),
        scenario_safety_stock=("safety_stock_scenario", "sum"),
    ).reset_index()
    by_dc["safety_stock_delta"] = by_dc.scenario_safety_stock - by_dc.baseline_safety_stock
    by_dc["safety_stock_delta_pct"] = (
        by_dc.safety_stock_delta / by_dc.baseline_safety_stock.replace(0, np.nan) * 100).round(2)
    by_dc_csv = os.path.join(RESULTS_DIR, f"scenario_{tag}_stocking_by_dc.csv")
    by_dc.to_csv(by_dc_csv, index=False)
    print(f"\nRecommended stocking change by DC (scenario-aware vs baseline):\n{by_dc.to_string(index=False)}")

    a_vs_b_fill = (kpi_b["customer_fill_rate"] - kpi_a["customer_fill_rate"]) * 100
    b_vs_c_fill = (kpi_c["customer_fill_rate"] - kpi_b["customer_fill_rate"]) * 100
    print(f"\nScenario impact if unprepared (A -> B): {a_vs_b_fill:+.2f}pp customer fill rate")
    print(f"Value of the scenario-aware recommendation (B -> C): {b_vs_c_fill:+.2f}pp customer fill rate")
    print(f"\nWrote {out_csv}, {by_dc_csv}")
    return summary, by_dc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True,
                     help="Path to a scenario-config JSON (see models/twin/scenario_config.example.json)")
    args = ap.parse_args()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    run_scenario(args.scenario)


if __name__ == "__main__":
    main()
