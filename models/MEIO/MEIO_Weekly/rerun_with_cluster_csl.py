"""
Supply Brain — Rerun MEIO Weekly and Clark-Scarf v2 Against the Segmentation
Deliverable's Own CSL Ladder
===============================================================================

Every model in this project sizes safety stock off `sku_master.csv`'s baked-in
3-way `segment` (Fast-Moving/Seasonal/Intermediate -> Z_BY_SEGMENT), a field
the dataset generator supplies directly -- NOT the Clustering & Segmentation
deliverable's own analysis. That deliverable (`segmentation_v4.py`) runs an
independent k-means clustering (k=4, auto-selected) over 10 features (margin,
demand CV, promo depth, forecast error, lead-time mean/CV, OTD%, shelf life)
and recommends a DIFFERENT service-level ladder per cluster
(`cluster_service_levels.csv`): Flagship Promo-Driven / Mid-Tier Core -> 97%
(z=1.881), Long-Tail Erratic / Long-Tail Supply-Risk -> 90% (z=1.282). The two
schemes disagree, not just differ in name -- e.g. two SKUs both labeled
"Fast-Moving" in the original scheme (which every model actually uses) land
in DIFFERENT clusters here, both capped at 97% instead of the original 98%.

This script re-scores the two leading models (MEIO Weekly hand-tuned, and
Clark-Scarf v2 at alpha=0.2, the same alpha reported as the project's
synthetic-basis headline) with the cluster ladder's z substituted in via
`meio_weekly_lib.Z_OVERRIDE_BY_SKU` (a per-SKU override, empty by default,
added specifically for this comparison -- every other call site in the
project is unaffected). Both a segment-based CONTROL run and a cluster-based
run are built fresh in this same script for a clean, apples-to-apples diff,
on the same synthetic 2026 rolling basis the project's other "headline"
numbers use (not the real-data walk-forward, to keep this tractable -- can
be extended there if this comparison turns out to matter).

    python rerun_with_cluster_csl.py

Outputs:
    cluster_csl_output/policy_*.csv         (gitignored; the 4 rolling policies built)
    cluster_csl_output/{run_name}/          (gitignored; full twin run detail)
    cluster_csl_comparison.csv              (repo-tracked)
    cluster_csl_report.md                   (repo-tracked)
"""
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "alt_models"))
import meio_weekly_lib as lib
import roll_forward_policy
import clark_scarf_v2_rolling as csv2r

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
DEFAULT_DATA = os.path.normpath(os.path.join(REPO_ROOT, "input datasets"))
CLUSTER_CSV = os.path.join(REPO_ROOT, "Clustering & Segmentation", "segmentation_v4", "sku_clusters.csv")
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")
OUT_DIR = os.path.join(SCRIPT_DIR, "cluster_csl_output")
BASELINE_POLICY_CSV = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")
STATE_CSV = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")

ALPHA = 0.2     # Clark-Scarf v2's synthetic-basis headline alpha, for direct comparability
N_SIMS = 1200
SEED = 2026
AS_OF_WEEK = "2025-W52"


def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def load_cluster_z():
    c = pd.read_csv(CLUSTER_CSV, usecols=["sku_id", "cluster", "archetype", "safety_factor_z"])
    print(f"Cluster ladder loaded: {c.sku_id.nunique()} SKUs, "
          f"{c.groupby(['cluster', 'archetype', 'safety_factor_z']).size().to_dict()}")
    return dict(zip(c.sku_id, c.safety_factor_z))


def run_twin(run_name, policy_csv):
    out_dir = os.path.join(OUT_DIR, run_name)
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", STATE_CSV, "--seed", str(SEED),
           "--out-root", OUT_DIR, "--label", run_name]
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    with open(os.path.join(out_dir, "run_kpis.json")) as f:
        return json.load(f)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cluster_z = load_cluster_z()

    print("\n=== 0. Baseline (dataset's own forward_policy.csv) ===")
    baseline_kpi = run_twin("baseline_ctrl", BASELINE_POLICY_CSV)

    print("\n=== 1. MEIO Weekly rolling, SEGMENT-based z (control) ===")
    lib.Z_OVERRIDE_BY_SKU.clear()
    meio_seg_csv = roll_forward_policy.run(DEFAULT_DATA, AS_OF_WEEK,
                                            out_path=os.path.join(OUT_DIR, "policy_meio_segment.csv"))
    meio_seg_kpi = run_twin("meio_segment", meio_seg_csv)

    print("\n=== 2. MEIO Weekly rolling, CLUSTER-based z ===")
    lib.Z_OVERRIDE_BY_SKU.update(cluster_z)
    meio_cluster_csv = roll_forward_policy.run(DEFAULT_DATA, AS_OF_WEEK,
                                                out_path=os.path.join(OUT_DIR, "policy_meio_cluster.csv"))
    meio_cluster_kpi = run_twin("meio_cluster", meio_cluster_csv)
    lib.Z_OVERRIDE_BY_SKU.clear()

    print(f"\n=== 3. Clark-Scarf v2 (alpha={ALPHA}) rolling, SEGMENT-based z (control) ===")
    rng = np.random.default_rng(SEED)
    csv2_seg_csv = os.path.join(OUT_DIR, "policy_csv2_segment.csv")
    csv2r.build_rolling_policy(DEFAULT_DATA, AS_OF_WEEK, ALPHA, N_SIMS, rng, csv2_seg_csv)
    csv2_seg_kpi = run_twin("csv2_segment", csv2_seg_csv)

    print(f"\n=== 4. Clark-Scarf v2 (alpha={ALPHA}) rolling, CLUSTER-based z ===")
    lib.Z_OVERRIDE_BY_SKU.update(cluster_z)
    rng2 = np.random.default_rng(SEED)
    csv2_cluster_csv = os.path.join(OUT_DIR, "policy_csv2_cluster.csv")
    csv2r.build_rolling_policy(DEFAULT_DATA, AS_OF_WEEK, ALPHA, N_SIMS, rng2, csv2_cluster_csv)
    csv2_cluster_kpi = run_twin("csv2_cluster", csv2_cluster_csv)
    lib.Z_OVERRIDE_BY_SKU.clear()

    rows = [
        {"run": "baseline", "z_source": "-", **baseline_kpi},
        {"run": "meio_weekly", "z_source": "segment (original)", **meio_seg_kpi},
        {"run": "meio_weekly", "z_source": "cluster (segmentation deliverable)", **meio_cluster_kpi},
        {"run": f"clark_scarf_v2 (alpha={ALPHA})", "z_source": "segment (original)", **csv2_seg_kpi},
        {"run": f"clark_scarf_v2 (alpha={ALPHA})", "z_source": "cluster (segmentation deliverable)", **csv2_cluster_kpi},
    ]
    keys = ["run", "z_source", "customer_fill_rate", "customer_fill_rate_cdc", "customer_fill_rate_rdc",
            "internal_replen_fill_rate", "safety_stock_value_usd", "holding_cost_usd", "stockout_cost_usd",
            "total_landed_cost_usd"]
    summary = pd.DataFrame(rows)[keys]
    summary.to_csv(os.path.join(SCRIPT_DIR, "cluster_csl_comparison.csv"), index=False)
    print("\n" + summary.to_string(index=False))

    def delta(a, b, key):
        return b[key] - a[key]

    meio_fill_delta = delta(meio_seg_kpi, meio_cluster_kpi, "customer_fill_rate") * 100
    meio_cost_delta = delta(meio_seg_kpi, meio_cluster_kpi, "total_landed_cost_usd")
    csv2_fill_delta = delta(csv2_seg_kpi, csv2_cluster_kpi, "customer_fill_rate") * 100
    csv2_cost_delta = delta(csv2_seg_kpi, csv2_cluster_kpi, "total_landed_cost_usd")

    # Report text only. With Z_OVERRIDE_BY_SKU cleared, meio_weekly_lib.assign_z()
    # resolves to the canonical segmentation cluster z, not sku_master.csv's
    # segment, so the md table describes the z sources by what the code does.
    # (cluster_csl_comparison.csv keeps its original z_source labels.)
    cluster_rel = os.path.relpath(CLUSTER_CSV, REPO_ROOT).replace(os.sep, "/")
    control_label = "control: assign_z() default"
    treatment_label = f"override: {cluster_rel}"
    report_summary = summary.copy()
    report_summary["z_source"] = report_summary["z_source"].replace({
        "segment (original)": control_label,
        "cluster (segmentation deliverable)": treatment_label,
    })

    report = f"""# MEIO Weekly & Clark-Scarf v2 — Rerun Against the Segmentation Deliverable's CSL Ladder
Supply Brain capstone | generated by `rerun_with_cluster_csl.py`

## What this compares
Two z sources, each applied to MEIO Weekly and to Clark-Scarf v2 (alpha={ALPHA}):
- **{control_label}**: `meio_weekly_lib.assign_z()` with `Z_OVERRIDE_BY_SKU`
  cleared. That is the live default: the canonical segmentation cluster z in
  `output datasets/segmentation/sku_clusters.csv`, with `sku_master.csv`'s
  segment `Z_BY_SEGMENT` only as a fallback for SKUs missing from that file.
  It is not the `sku_master.csv` segment ladder.
- **{treatment_label}**: per-SKU `safety_factor_z` loaded from that file
  into `Z_OVERRIDE_BY_SKU`.

If the two cluster files carry the same per-SKU z, the two runs coincide by
construction. Both are built fresh in this same run on the synthetic 2026
rolling basis (`--mode forward`, policies as-of {AS_OF_WEEK}, twin seed {SEED}, opening
state `output datasets/history/opening_position.csv`), not the real-data
walk-forward. Clark-Scarf v2 here is the `alt_models/clark_scarf_v2_rolling.py` /
`clark_scarf_v2_model.py` prototype (pooled RDC SS always netted fully against
downstream CDC SS), with `N_SIMS={N_SIMS}` and a fresh RNG seeded {SEED} for each build.

## Result
{df_to_md(report_summary.round(4))}

- **MEIO Weekly**: fill rate {meio_fill_delta:+.2f}pp, cost {meio_cost_delta:+,.0f} USD
  switching from the control z to the override z.
- **Clark-Scarf v2 (alpha={ALPHA})**: fill rate {csv2_fill_delta:+.2f}pp, cost {csv2_cost_delta:+,.0f} USD
  switching from the control z to the override z.

## Files
- `cluster_csl_output/policy_*.csv` (gitignored; the 4 rolling policies built)
- `cluster_csl_output/{{run_name}}/` (gitignored; full twin run detail per run)
- `cluster_csl_comparison.csv`
"""
    with open(os.path.join(SCRIPT_DIR, "cluster_csl_report.md"), "w") as f:
        f.write(report)
    print(f"\nMEIO Weekly: fill {meio_fill_delta:+.2f}pp, cost {meio_cost_delta:+,.0f}")
    print(f"Clark-Scarf v2: fill {csv2_fill_delta:+.2f}pp, cost {csv2_cost_delta:+,.0f}")
    print("\nWrote cluster_csl_comparison.csv, cluster_csl_report.md")


if __name__ == "__main__":
    main()
