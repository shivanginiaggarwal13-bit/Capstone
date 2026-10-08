"""
Clark-Scarf v2 — Per-Segment Alpha Exploration (Diagnostic, Not Yet a Recommendation)
===============================================================================

Explores whether a single portfolio-wide alpha is leaving value on the
table, using the segments actually identified by the Clustering &
Segmentation deliverable's own analysis (`segmentation_v4.py`'s k-means
clustering, k=4: Flagship Promo-Driven, Mid-Tier Core, Long-Tail Erratic,
Long-Tail Supply-Risk) -- NOT the sku_master.csv 3-way label
(Fast-Moving/Seasonal/Intermediate) that the rest of this submission's
validated results use.

Design: for each cluster, sweep its own alpha over {0.0, 0.2, 0.4, 0.6, 0.8,
1.0} while holding the OTHER THREE clusters at a fixed reference (0.2, the
real-data-headline value, for continuity). Synthetic rolling 2026 basis,
NOT real data -- the full real-data equivalent (4 clusters x 6 alphas x 5
seeds = 120 runs) would take ~9 hours and hasn't been run; this is the
single-seed, single-basis version only.

**Read the result as a diagnostic, not a production recommendation.** Two
things to check before trusting any number here:
  1. Portfolio-level fill rate dilutes small clusters' own signal (a
     2%-of-revenue cluster's effect on PORTFOLIO fill is small almost by
     construction, regardless of how much it matters to itself) -- the
     `own_fill` stage corrects for this by re-slicing each run's own
     inventory.csv down to just that cluster's SKUs.
  2. Clusters are not independent: raising one cluster's setting can hurt
     another's fill rate through shared RDC/PDC capacity, even with that
     other cluster's own setting untouched (observed directly in this
     project's cluster-bandit work). A per-cluster sweep holding the others
     fixed finds each cluster's MARGINAL response, not a validated joint
     optimum.

    python segment_alpha_explore.py              # full sweep + own-fill correction
    python segment_alpha_explore.py --stage sweep # portfolio-level sweep only
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
import clark_scarf_v2 as csv2

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DEFAULT_DATA = os.path.normpath(os.path.join(REPO_ROOT, "input datasets"))
sys.path.insert(0, os.path.join(REPO_ROOT, "models"))
import scenario

SCENARIO_CONFIG = Path(REPO_ROOT) / "scenario_config.json"
SCENARIO_SETTINGS = scenario.load_config(SCENARIO_CONFIG)
SCENARIO_NAME = scenario.scenario_folder_name(SCENARIO_SETTINGS)
DEFAULT_DATA = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "meio", "data_dir"))
CLUSTER_CSV = os.path.join(REPO_ROOT, "Clustering & Segmentation", "segmentation_v4", "sku_clusters.csv")
TWIN = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "entrypoint"))
SCENARIO_OUT_DIR = str(scenario.resolve_scenario_path(
    SCENARIO_CONFIG, SCENARIO_SETTINGS, "artifacts", "scenario"))
TWIN_OUT_ROOT = str(scenario.resolve_path(
    SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "output_root"))
RUN_OUT_DIR = os.path.join(SCENARIO_OUT_DIR, "validation_output")
RESULTS_DIR = os.path.join(SCENARIO_OUT_DIR, "results")
STATE_CSV = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "state"))
BASELINE_POLICY_CSV = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "policy"))
os.makedirs(SCENARIO_OUT_DIR, exist_ok=True)
scenario.copy_config(SCENARIO_CONFIG, Path(SCENARIO_OUT_DIR))

CLUSTERS = ["Flagship Promo-Driven", "Mid-Tier Core", "Long-Tail Erratic", "Long-Tail Supply-Risk"]
ALPHAS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
REFERENCE_ALPHA = 0.2
N_SIMS = 1200
SEED = 2026
AS_OF_WEEK = "2025-W52"


def load_cluster_map():
    c = pd.read_csv(CLUSTER_CSV, usecols=["sku_id", "archetype"])
    return dict(zip(c.sku_id, c.archetype))


def run_twin_forward(run_name, policy_csv, seed=SEED):
    """Synthetic 2026 rolling basis (--mode forward, default 52-week horizon)."""
    out_dir = os.path.join(TWIN_OUT_ROOT, "projections", SCENARIO_NAME, run_name)
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", STATE_CSV, "--seed", str(seed),
           "--out-root", TWIN_OUT_ROOT, "--scenario-config", SCENARIO_CONFIG,
           "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_dir, "run_kpis.json")) as f:
        kpi = json.load(f)
    inv = pd.read_csv(os.path.join(out_dir, "inventory.csv"))
    return kpi, inv


def stage_baseline():
    """Synthetic-basis baseline (dataset's own forward_policy.csv). Cached
    after first run."""
    out_dir = os.path.join(TWIN_OUT_ROOT, "projections", SCENARIO_NAME, "synthetic_baseline")
    if os.path.exists(os.path.join(out_dir, "run_kpis.json")):
        with open(os.path.join(out_dir, "run_kpis.json")) as f:
            kpi = json.load(f)
        inv = pd.read_csv(os.path.join(out_dir, "inventory.csv"))
        return kpi, inv
    forward_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")
    return run_twin_forward("synthetic_baseline", forward_policy_csv)


def stage_sweep(seed=SEED):
    """Portfolio-level fill rate per (cluster, alpha). 24 full rolling-policy
    builds + twin scores -- ~90 min at the pace measured in this project."""
    print("=== Stage: sweep (portfolio-level) ===")
    cluster_map = load_cluster_map()
    baseline_kpi, _ = stage_baseline()
    rng = np.random.default_rng(seed)

    rows = [{"cluster": "ALL (baseline)", "alpha": None, "fill_rate": baseline_kpi["customer_fill_rate"],
             "internal_replen_fill_rate": baseline_kpi["internal_replen_fill_rate"],
             "total_landed_cost_usd": baseline_kpi["total_landed_cost_usd"], "cost_delta_usd": 0.0}]

    run_n = 0
    for cluster in CLUSTERS:
        for alpha in ALPHAS:
            run_n += 1
            tag = f"{cluster.replace(' ', '')}_a{alpha:.1f}".replace(".", "")
            print(f"--- [{run_n}/{len(CLUSTERS) * len(ALPHAS)}] cluster={cluster} alpha={alpha:.1f} ---")

            csv2.ALPHA_OVERRIDE_BY_SKU.clear()
            csv2.ALPHA_OVERRIDE_BY_SKU.update(
                {sku: (alpha if arch == cluster else REFERENCE_ALPHA) for sku, arch in cluster_map.items()})
            policy_csv = os.path.join(RUN_OUT_DIR, f"policy_{tag}.csv")
            csv2.build_rolling_policy(DEFAULT_DATA, AS_OF_WEEK, REFERENCE_ALPHA, N_SIMS, rng, policy_csv)
            csv2.ALPHA_OVERRIDE_BY_SKU.clear()

            kpi, _ = run_twin_forward(tag, policy_csv, seed=seed)
            cost_delta = kpi["total_landed_cost_usd"] - baseline_kpi["total_landed_cost_usd"]
            rows.append({"cluster": cluster, "alpha": alpha, "fill_rate": kpi["customer_fill_rate"],
                         "internal_replen_fill_rate": kpi["internal_replen_fill_rate"],
                         "total_landed_cost_usd": kpi["total_landed_cost_usd"], "cost_delta_usd": cost_delta})
            print(f"  fill {kpi['customer_fill_rate']:.4f}  cost_delta ${cost_delta:+,.0f}")

    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(RESULTS_DIR, "segment_alpha_sweep_portfolio.csv"), index=False)
    print(summary.to_string(index=False))
    return summary


def stage_own_fill():
    """Re-slice each sweep run's own inventory.csv down to just that
    cluster's SKUs -- corrects the portfolio-level dilution. Requires
    `sweep` to have already run (reuses its cached per-run inventory.csv,
    no new twin runs)."""
    print("=== Stage: own_fill (corrects portfolio-level dilution) ===")
    cluster_map = load_cluster_map()
    baseline_kpi, base_inv = stage_baseline()
    rows = []
    for cluster in CLUSTERS:
        for alpha in ALPHAS:
            tag = f"{cluster.replace(' ', '')}_a{alpha:.1f}".replace(".", "")
            inv_path = os.path.join(TWIN_OUT_ROOT, "projections", SCENARIO_NAME,
                                    tag, "inventory.csv")
            if not os.path.exists(inv_path):
                print(f"  SKIP {tag}: run the 'sweep' stage first")
                continue
            inv = pd.read_csv(inv_path, usecols=["sku_id", "demand_customer_qty", "unmet_customer_qty"])
            inv["archetype"] = inv.sku_id.map(cluster_map)
            g = inv[inv.archetype == cluster]
            cd, cu = g.demand_customer_qty.sum(), g.unmet_customer_qty.sum()
            rows.append({"cluster": cluster, "alpha": alpha,
                         "own_fill_rate": round(1 - cu / cd, 4) if cd else None})

    out = pd.DataFrame(rows)
    piv = out.pivot(index="alpha", columns="cluster", values="own_fill_rate")[CLUSTERS]
    piv.to_csv(os.path.join(RESULTS_DIR, "segment_alpha_sweep_own_fill.csv"))
    print(piv.to_string())
    print("\n=== Spread across alpha, OWN cluster fill (diagnostic, not a recommendation) ===")
    for cl in CLUSTERS:
        s = piv[cl]
        print(f"  {cl:24s}: spread {((s.max() - s.min()) * 100):.2f}pp  "
              f"best alpha={s.idxmax()} ({s.max():.4f})")
    return piv


STAGES = {"baseline": stage_baseline, "sweep": stage_sweep, "own_fill": stage_own_fill}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=list(STAGES), default=None,
                     help="Run just one stage; omit to run sweep then own_fill.")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    if args.stage:
        STAGES[args.stage](args.seed) if args.stage == "sweep" else STAGES[args.stage]()
    else:
        stage_sweep(args.seed)
        stage_own_fill()


if __name__ == "__main__":
    main()
