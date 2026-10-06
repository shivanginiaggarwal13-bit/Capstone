"""
Clark-Scarf v2 — Per-Cluster Alpha Response Curve (Diagnostic, Not a Tuning Pass)
===============================================================================

Earlier work found that moving alpha from the synthetic-chosen 0.2 toward 0.6
helps the original 3-way SKU segment (Fast-Moving/Seasonal/Intermediate)
very unevenly -- Intermediate gained ~4x what Fast-Moving or Seasonal did.
That's a real signal that one portfolio-wide alpha is averaging over
genuine heterogeneity. But the "Intermediate" label is the dataset's own
baked-in 3-way segment, NOT what the Clustering & Segmentation deliverable
actually produced: that team's own analysis (segmentation_v4.py) ran an
independent k-means clustering (k=4) over 10 features (margin, demand CV,
promo depth, forecast error, lead-time mean/CV, OTD%, shelf life) and found
four archetypes that cut across the original 3-way labels -- Flagship
Promo-Driven, Mid-Tier Core, Long-Tail Erratic, Long-Tail Supply-Risk. This
script re-runs the same response-curve diagnostic on THOSE four clusters
instead.

**This is explicitly a diagnostic, not a tuning exercise** -- same framing
as the original: it answers "is the heterogeneity across clusters real and
big enough to be worth pursuing," not "what alpha should each cluster use."
Picking per-cluster production values off this single synthetic rolling
year would be the same overfitting problem already flagged for the
portfolio-wide alpha choice, just subdivided four ways instead of avoided.

Design: for each cluster, sweep its own alpha over {0.0, 0.2, 0.4, 0.6, 0.8,
1.0} while holding the OTHER THREE clusters at a fixed reference alpha
(0.2 -- the original synthetic-basis pick, for continuity with every other
number already reported), via `clark_scarf_v2_model.ALPHA_OVERRIDE_BY_SKU`
(a per-SKU override, empty by default, added specifically for this
exercise -- every other call site is unaffected). Rolling 52-week basis,
synthetic 2026 demand, scored against the same baseline as everything else
in this project. 4 clusters x 6 alpha values = 24 full rolling-policy
builds + twin scores.

    python cluster_alpha_response_curve.py

Output: alt_output/cluster_alpha_response_curve.csv (repo-tracked)
        chart_cluster_alpha_response_curve.png (repo-tracked)
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meio_weekly_lib as lib
import clark_scarf_v2_model as csv2
import clark_scarf_v2_rolling as csv2r

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MEIO_WEEKLY_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DEFAULT_DATA = os.path.normpath(os.path.join(REPO_ROOT, "input datasets"))
CLUSTER_CSV = os.path.join(REPO_ROOT, "Clustering & Segmentation", "segmentation_v4", "sku_clusters.csv")
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")
OUT_DIR = os.path.join(SCRIPT_DIR, "cluster_alpha_curve_output")
STATE_CSV = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")
CACHED_BASELINE_DIR = os.path.join(MEIO_WEEKLY_DIR, "cluster_csl_output", "baseline_ctrl")

CLUSTERS = ["Flagship Promo-Driven", "Mid-Tier Core", "Long-Tail Erratic", "Long-Tail Supply-Risk"]
ALPHAS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
REFERENCE_ALPHA = 0.2   # held for every cluster NOT currently being swept
N_SIMS = 1200
SEED = 2026
AS_OF_WEEK = "2025-W52"


def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def load_cluster_map():
    c = pd.read_csv(CLUSTER_CSV, usecols=["sku_id", "archetype"])
    return dict(zip(c.sku_id, c.archetype))


def load_cached_baseline():
    with open(os.path.join(CACHED_BASELINE_DIR, "run_kpis.json")) as f:
        return json.load(f)


def run_twin(run_name, policy_csv, seed=SEED):
    out_dir = os.path.join(OUT_DIR, run_name)
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", STATE_CSV, "--seed", str(seed),
           "--out-root", OUT_DIR, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_dir, "run_kpis.json")) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    cluster_map = load_cluster_map()
    print(f"Cluster map loaded: {len(cluster_map)} SKUs")
    baseline_kpi = load_cached_baseline()
    print(f"Baseline (cached): fill {baseline_kpi['customer_fill_rate']:.4f}  "
          f"cost ${baseline_kpi['total_landed_cost_usd']:,.0f}")

    rng = np.random.default_rng(args.seed)
    rows = [{
        "cluster": "ALL (baseline)", "alpha": None, "fill_rate": baseline_kpi["customer_fill_rate"],
        "internal_replen_fill_rate": baseline_kpi["internal_replen_fill_rate"],
        "total_landed_cost_usd": baseline_kpi["total_landed_cost_usd"], "cost_delta_usd": 0.0,
    }]

    run_n = 0
    for cluster in CLUSTERS:
        for alpha in ALPHAS:
            run_n += 1
            tag = f"{cluster.replace(' ', '')}_a{alpha:.1f}".replace(".", "")
            print(f"\n=== [{run_n}/{len(CLUSTERS) * len(ALPHAS)}] cluster={cluster} alpha={alpha:.1f} ===")

            csv2.ALPHA_OVERRIDE_BY_SKU.clear()
            csv2.ALPHA_OVERRIDE_BY_SKU.update(
                {sku: (alpha if arch == cluster else REFERENCE_ALPHA) for sku, arch in cluster_map.items()})
            policy_csv = os.path.join(OUT_DIR, f"policy_{tag}.csv")
            csv2r.build_rolling_policy(DEFAULT_DATA, AS_OF_WEEK, REFERENCE_ALPHA, N_SIMS, rng, policy_csv)
            csv2.ALPHA_OVERRIDE_BY_SKU.clear()

            kpi = run_twin(tag, policy_csv, seed=args.seed)
            cost_delta = kpi["total_landed_cost_usd"] - baseline_kpi["total_landed_cost_usd"]
            rows.append({
                "cluster": cluster, "alpha": alpha, "fill_rate": kpi["customer_fill_rate"],
                "internal_replen_fill_rate": kpi["internal_replen_fill_rate"],
                "total_landed_cost_usd": kpi["total_landed_cost_usd"], "cost_delta_usd": cost_delta,
            })
            print(f"  fill {kpi['customer_fill_rate']:.4f}  internal_replen {kpi['internal_replen_fill_rate']:.4f}  "
                  f"cost_delta ${cost_delta:+,.0f}")

    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(SCRIPT_DIR, "cluster_alpha_response_curve.csv"), index=False)
    print("\n" + summary.to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    colors = {"Flagship Promo-Driven": "#2B6CB0", "Mid-Tier Core": "#2F7D4F",
              "Long-Tail Erratic": "#C05621", "Long-Tail Supply-Risk": "#8B4513"}
    for cluster in CLUSTERS:
        sub = summary[summary.cluster == cluster].sort_values("alpha")
        axes[0].plot(sub.alpha, sub.fill_rate * 100, marker="o", label=cluster, color=colors[cluster])
        axes[1].plot(sub.alpha, sub.cost_delta_usd / 1e6, marker="o", label=cluster, color=colors[cluster])
    axes[0].axhline(baseline_kpi["customer_fill_rate"] * 100, color="#9aa5b1", linestyle="--", label="Baseline")
    axes[0].axvline(REFERENCE_ALPHA, color="gray", linestyle=":", alpha=0.5)
    axes[0].set_xlabel(f"alpha for this cluster (others held at {REFERENCE_ALPHA})")
    axes[0].set_ylabel("Portfolio customer fill rate (%)")
    axes[0].set_title("Fill rate response curve, per cluster")
    axes[0].legend(fontsize=8)

    axes[1].axhline(0, color="#9aa5b1", linestyle="--")
    axes[1].axvline(REFERENCE_ALPHA, color="gray", linestyle=":", alpha=0.5)
    axes[1].set_xlabel(f"alpha for this cluster (others held at {REFERENCE_ALPHA})")
    axes[1].set_ylabel("Total landed cost delta vs baseline ($M)")
    axes[1].set_title("Cost response curve, per cluster")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_cluster_alpha_response_curve.png"), dpi=150)
    plt.close(fig)

    print("\n=== Diagnostic read (NOT a recommendation) ===")
    for cluster in CLUSTERS:
        sub = summary[summary.cluster == cluster].sort_values("alpha")
        spread_pp = (sub.fill_rate.max() - sub.fill_rate.min()) * 100
        print(f"  {cluster:24s}: fill rate spread across alpha = {spread_pp:.2f}pp "
              f"(min {sub.fill_rate.min():.4f} @ alpha={sub.loc[sub.fill_rate.idxmin(),'alpha']}, "
              f"max {sub.fill_rate.max():.4f} @ alpha={sub.loc[sub.fill_rate.idxmax(),'alpha']})")

    print("\nWrote cluster_alpha_response_curve.csv, chart_cluster_alpha_response_curve.png")


if __name__ == "__main__":
    main()
