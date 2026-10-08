"""
Segment Breakdown (Fast-Moving / Seasonal / Intermediate) of the Real-Data
Walk-Forward Backtest
===============================================================================

Every result in `../backtest_rolling_real.py`, `backtest_rolling_real_clark_scarf.py`
and `backtest_rolling_real_clark_scarf_alpha_sweep.py` reports customer fill
rate at the portfolio/CDC/RDC level only. This script re-slices the SAME
cached inventory.csv outputs (no twin re-run -- reads what those scripts
already wrote to disk) by each SKU's `segment` tag (Fast-Moving / Seasonal /
Intermediate, from `input datasets/baseline_policy.csv` -- confirmed a pure
per-SKU attribute, identical across every DC a SKU appears at), filtered to
the same real 2025-W02..2025-W52 test window.

Segment matters here specifically because `Z_BY_SEGMENT` sizes safety stock
differently per segment (Fast-Moving gets the highest z, Intermediate the
lowest) -- a portfolio-level fill-rate number can hide a segment picking up
all of a policy's gain (or all of its cost) while another segment is
untouched or worse off.

    python backtest_rolling_real_segment_breakdown.py

Output: backtest_rolling_real_segment_breakdown.csv (repo-tracked)
"""
import os
import sys

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MEIO_WEEKLY_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DEFAULT_DATA = os.path.normpath(os.path.join(REPO_ROOT, "input datasets"))

TEST_START = "2025-W02"
TEST_END = "2025-W52"

RUNS = {
    "actual": os.path.join(MEIO_WEEKLY_DIR, "backtest_rolling_output", "actual"),
    "meio_weekly": os.path.join(MEIO_WEEKLY_DIR, "backtest_rolling_output", "meio_weekly"),
    "clark_scarf_v2 (alpha=0.2, headline)": os.path.join(SCRIPT_DIR, "backtest_rolling_real_output",
                                                          "clark_scarf_v2_weekly"),
    "clark_scarf_v2 (alpha=0.0)": os.path.join(SCRIPT_DIR, "backtest_rolling_real_output",
                                                "clark_scarf_v2_weekly_alpha00"),
    "clark_scarf_v2 (alpha=0.4)": os.path.join(SCRIPT_DIR, "backtest_rolling_real_output",
                                                "clark_scarf_v2_weekly_alpha04"),
    "clark_scarf_v2 (alpha=0.6, best fill)": os.path.join(SCRIPT_DIR, "backtest_rolling_real_output",
                                                           "clark_scarf_v2_weekly_alpha06"),
    "clark_scarf_v2 (alpha=1.0)": os.path.join(SCRIPT_DIR, "backtest_rolling_real_output",
                                                "clark_scarf_v2_weekly_alpha10"),
}


def load_segment_map(data_dir):
    bp = pd.read_csv(os.path.join(data_dir, "baseline_policy.csv"), usecols=["sku_id", "segment"])
    m = bp.drop_duplicates("sku_id").set_index("sku_id")["segment"]
    assert bp.groupby("sku_id")["segment"].nunique().max() == 1, "segment is not a stable per-SKU attribute"
    return m


def segment_fill_rates(inv, seg_map):
    inv = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)].copy()
    inv["segment"] = inv.sku_id.map(seg_map)
    rows = []
    for seg, g in inv.groupby("segment"):
        cd, cu = g.demand_customer_qty.sum(), g.unmet_customer_qty.sum()
        rd, ru = g.demand_replen_qty.sum(), g.unmet_replen_qty.sum()
        rows.append({
            "segment": seg,
            "customer_demand_units": float(cd),
            "customer_fill_rate": round(float(1 - cu / cd), 4) if cd else None,
            "internal_replen_fill_rate": round(float(1 - ru / rd), 4) if rd else None,
        })
    portfolio_cd, portfolio_cu = inv.demand_customer_qty.sum(), inv.unmet_customer_qty.sum()
    portfolio_rd, portfolio_ru = inv.demand_replen_qty.sum(), inv.unmet_replen_qty.sum()
    rows.append({
        "segment": "ALL (portfolio)",
        "customer_demand_units": float(portfolio_cd),
        "customer_fill_rate": round(float(1 - portfolio_cu / portfolio_cd), 4) if portfolio_cd else None,
        "internal_replen_fill_rate": round(float(1 - portfolio_ru / portfolio_rd), 4) if portfolio_rd else None,
    })
    return rows


def main():
    seg_map = load_segment_map(DEFAULT_DATA)
    print(f"Segment map: {seg_map.value_counts().to_dict()}")

    all_rows = []
    for run_label, run_dir in RUNS.items():
        inv_path = os.path.join(run_dir, "inventory.csv")
        if not os.path.exists(inv_path):
            print(f"  SKIP {run_label}: no cached inventory.csv at {inv_path}")
            continue
        print(f"Reading {run_label} ...")
        inv = pd.read_csv(inv_path, usecols=["period", "sku_id", "demand_customer_qty", "unmet_customer_qty",
                                              "demand_replen_qty", "unmet_replen_qty"])
        for row in segment_fill_rates(inv, seg_map):
            row["run"] = run_label
            all_rows.append(row)

    out = pd.DataFrame(all_rows)[["run", "segment", "customer_demand_units", "customer_fill_rate",
                                   "internal_replen_fill_rate"]]
    out.to_csv(os.path.join(SCRIPT_DIR, "backtest_rolling_real_segment_breakdown.csv"), index=False)

    pivot_customer = out.pivot(index="run", columns="segment", values="customer_fill_rate")
    seg_order = ["Fast-Moving", "Seasonal", "Intermediate", "ALL (portfolio)"]
    pivot_customer = pivot_customer[[c for c in seg_order if c in pivot_customer.columns]]
    print("\n=== Customer fill rate by segment (real 2025-W02..W52 test window) ===")
    print(pivot_customer.to_string())

    pivot_internal = out.pivot(index="run", columns="segment", values="internal_replen_fill_rate")
    pivot_internal = pivot_internal[[c for c in seg_order if c in pivot_internal.columns]]
    print("\n=== Internal replenishment fill rate by segment ===")
    print(pivot_internal.to_string())

    print("\nWrote backtest_rolling_real_segment_breakdown.csv")


if __name__ == "__main__":
    main()
