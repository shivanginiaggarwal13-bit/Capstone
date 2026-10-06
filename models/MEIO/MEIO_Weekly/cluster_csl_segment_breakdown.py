"""
Segment/Cluster-Level Breakdown of the Cluster-CSL Rerun
===============================================================================

`rerun_with_cluster_csl.py` found the portfolio-level fill-rate and cost
deltas from switching MEIO Weekly / Clark-Scarf v2 to the segmentation
deliverable's 4-cluster CSL ladder are noise (<0.1pp fill, <0.3% cost) --
but that's an average over a real redistribution: the cluster ladder caps
former-Fast-Moving SKUs at 97% (z=1.881, down from 98%/z=2.05) while lifting
former-Seasonal SKUs to the same 97% (up from 95%/z=1.64). This re-slices
the same cached twin runs by BOTH the original segment and the new cluster,
to see who actually wins and loses underneath the flat portfolio average.

    python cluster_csl_segment_breakdown.py

Output: cluster_csl_segment_breakdown.csv (repo-tracked)
"""
import os

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
CLUSTER_CSV = os.path.join(REPO_ROOT, "Clustering & Segmentation", "segmentation_v4", "sku_clusters.csv")
OUT_DIR = os.path.join(SCRIPT_DIR, "cluster_csl_output")

RUNS = {
    "meio_weekly (segment)": os.path.join(OUT_DIR, "meio_segment"),
    "meio_weekly (cluster)": os.path.join(OUT_DIR, "meio_cluster"),
    "clark_scarf_v2 (segment)": os.path.join(OUT_DIR, "csv2_segment"),
    "clark_scarf_v2 (cluster)": os.path.join(OUT_DIR, "csv2_cluster"),
}


def load_maps():
    c = pd.read_csv(CLUSTER_CSV, usecols=["sku_id", "segment", "cluster", "archetype"])
    seg_map = dict(zip(c.sku_id, c.segment))
    arch_map = dict(zip(c.sku_id, c.archetype))
    return seg_map, arch_map


def by_group(inv, group_col, group_map):
    inv = inv.copy()
    inv["group"] = inv.sku_id.map(group_map)
    rows = []
    for g, gdf in inv.groupby("group"):
        cd, cu = gdf.demand_customer_qty.sum(), gdf.unmet_customer_qty.sum()
        rows.append({
            "group_type": group_col, "group": g,
            "n_skus": gdf.sku_id.nunique(),
            "customer_demand_units": float(cd),
            "customer_fill_rate": round(float(1 - cu / cd), 4) if cd else None,
            "safety_stock_units_avg_week": round(float(gdf.groupby("period").safety_stock.sum().mean()), 1),
        })
    return rows


def main():
    seg_map, arch_map = load_maps()
    all_rows = []
    for run_label, run_dir in RUNS.items():
        inv_path = os.path.join(run_dir, "inventory.csv")
        if not os.path.exists(inv_path):
            print(f"SKIP {run_label}: no cached inventory.csv")
            continue
        print(f"Reading {run_label} ...")
        inv = pd.read_csv(inv_path, usecols=["period", "sku_id", "demand_customer_qty", "unmet_customer_qty",
                                              "safety_stock"])
        for row in by_group(inv, "original_segment", seg_map) + by_group(inv, "new_cluster_archetype", arch_map):
            row["run"] = run_label
            all_rows.append(row)

    out = pd.DataFrame(all_rows)[["run", "group_type", "group", "n_skus", "customer_demand_units",
                                   "customer_fill_rate", "safety_stock_units_avg_week"]]
    out.to_csv(os.path.join(SCRIPT_DIR, "cluster_csl_segment_breakdown.csv"), index=False)

    print("\n=== Fill rate by ORIGINAL segment (Fast-Moving/Seasonal/Intermediate) ===")
    seg_pivot = out[out.group_type == "original_segment"].pivot(index="run", columns="group",
                                                                  values="customer_fill_rate")
    print(seg_pivot.to_string())

    print("\n=== Safety stock (avg units/week) by ORIGINAL segment ===")
    seg_ss_pivot = out[out.group_type == "original_segment"].pivot(index="run", columns="group",
                                                                     values="safety_stock_units_avg_week")
    print(seg_ss_pivot.to_string())

    print("\n=== Fill rate by NEW cluster archetype ===")
    arch_pivot = out[out.group_type == "new_cluster_archetype"].pivot(index="run", columns="group",
                                                                        values="customer_fill_rate")
    print(arch_pivot.to_string())

    print("\n=== Safety stock (avg units/week) by NEW cluster archetype ===")
    arch_ss_pivot = out[out.group_type == "new_cluster_archetype"].pivot(index="run", columns="group",
                                                                           values="safety_stock_units_avg_week")
    print(arch_ss_pivot.to_string())

    print("\nWrote cluster_csl_segment_breakdown.csv")


if __name__ == "__main__":
    main()
