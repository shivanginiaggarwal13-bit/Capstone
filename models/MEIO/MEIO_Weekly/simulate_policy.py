"""
Supply Brain — MEIO Weekly vs Baseline: Full Twin Simulation
================================================================

The CSL backtest (backtest_csl.py) checks one statistical property of the
recommended reorder point. This script asks the operational question
directly: **if the weekly MEIO policy had been in force for the 2026
forward horizon instead of the world-builder's baseline policy, what would
customer fill rate and total landed cost actually have been?**

Both arms are scored by the SAME shared physics engine
(`models/twin/digital_twin.py`) from the SAME opening state
(`output datasets/history/opening_position.csv`), so the comparison isolates
the policy decision -- this is the project's documented way to score a
candidate policy, not a bespoke simulator with its own (possibly
diverging) mechanics.

    python simulate_policy.py

Steps:
    1. Regenerate the weekly MEIO policy as-of the last historical week
       (2025-W52 in this dataset) -- the freshest recommendation an
       operator would actually have in hand going into 2026-W01.
    2. Run the digital twin forward for that policy AND for the
       world-builder's baseline forward_policy.csv, both against the
       identical opening_position.csv.
    3. Diff the two runs' run_kpis.json.

Outputs:
    simulation_output/twin_runs/<run>/   (gitignored; full twin run detail)
    simulation_summary.csv               (repo-tracked)
    chart_fill_rate_comparison.png       (repo-tracked)
    simulation_report.md                 (repo-tracked)
"""
import argparse
import json
import os
import subprocess
import sys

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")
TWIN_OUT = os.path.join(SCRIPT_DIR, "simulation_output", "twin_runs")


def run_twin(run_name, policy_csv, state_csv, label):
    out_dir = os.path.join(TWIN_OUT, run_name)
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv,
           "--out-root", TWIN_OUT, "--label", label]
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    with open(os.path.join(out_dir, "run_kpis.json")) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default=None,
                     help="Passed through to meio_optimizer_weekly.py. Default: latest week with actuals.")
    args = ap.parse_args()

    sys.path.insert(0, SCRIPT_DIR)
    import meio_optimizer_weekly as weekly

    print("=== 1. Regenerating weekly MEIO policy ===")
    weekly.run(weekly.DEFAULT_DATA, args.as_of_week, weekly.lib.SHORT_WINDOW_WEEKS,
               weekly.lib.LONG_WINDOW_WEEKS)
    # target week folder: re-derive from the newest dir under output/
    out_root = os.path.join(SCRIPT_DIR, "output")
    target_week = sorted(os.listdir(out_root))[-1]
    meio_policy_csv = os.path.join(out_root, target_week, "meio_policy_weekly.csv")

    baseline_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")

    print("\n=== 2. Running digital twin: baseline forward_policy.csv ===")
    baseline_kpi = run_twin("baseline_asis", baseline_policy_csv, state_csv, "Baseline (world-builder policy)")

    print(f"\n=== 3. Running digital twin: MEIO weekly policy (as-of -> {target_week}) ===")
    meio_kpi = run_twin("meio_weekly", meio_policy_csv, state_csv, f"MEIO weekly (target {target_week})")

    print("\n=== 4. Comparing ===")
    keys = ["customer_fill_rate", "customer_fill_rate_cdc", "customer_fill_rate_rdc",
            "pct_demand_weeks_in_full", "internal_replen_fill_rate",
            "avg_inventory_value_usd", "safety_stock_value_usd", "holding_cost_usd",
            "stockout_cost_usd", "transport_cost_usd", "total_landed_cost_usd",
            "pct_weeks_at_zero_stock"]
    rows = []
    for k in keys:
        b, m = baseline_kpi.get(k), meio_kpi.get(k)
        rows.append({"metric": k, "baseline": b, "meio_weekly": m,
                     "delta": (m - b) if (m is not None and b is not None) else None})
    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(SCRIPT_DIR, "simulation_summary.csv"), index=False)
    print(summary.to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    fr_keys = ["customer_fill_rate", "customer_fill_rate_cdc", "customer_fill_rate_rdc"]
    fr = summary[summary.metric.isin(fr_keys)]
    x = range(len(fr))
    axes[0].bar([i - 0.2 for i in x], fr.baseline * 100, width=0.4, label="Baseline", color="#9aa5b1")
    axes[0].bar([i + 0.2 for i in x], fr.meio_weekly * 100, width=0.4, label="MEIO weekly", color="#2b6cb0")
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(["Overall", "CDC", "RDC direct"])
    axes[0].set_ylabel("Customer fill rate (%)")
    axes[0].set_title("Fill Rate: Baseline vs MEIO Weekly")
    axes[0].legend()

    cost_keys = ["holding_cost_usd", "stockout_cost_usd", "total_landed_cost_usd"]
    cost = summary[summary.metric.isin(cost_keys)]
    x2 = range(len(cost))
    axes[1].bar([i - 0.2 for i in x2], cost.baseline / 1e6, width=0.4, label="Baseline", color="#9aa5b1")
    axes[1].bar([i + 0.2 for i in x2], cost.meio_weekly / 1e6, width=0.4, label="MEIO weekly", color="#2b6cb0")
    axes[1].set_xticks(list(x2))
    axes[1].set_xticklabels(["Holding", "Stockout", "Total landed"])
    axes[1].set_ylabel("$ millions (2026 forward horizon)")
    axes[1].set_title("Cost: Baseline vs MEIO Weekly")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_fill_rate_comparison.png"), dpi=150)
    plt.close(fig)

    fr_delta = (meio_kpi["customer_fill_rate"] - baseline_kpi["customer_fill_rate"]) * 100
    cost_delta = meio_kpi["total_landed_cost_usd"] - baseline_kpi["total_landed_cost_usd"]
    verdict = "MEIO weekly wins" if (fr_delta > 0 and cost_delta < 0) else (
        "Baseline wins" if (fr_delta < 0 and cost_delta > 0) else "Mixed -- see table")

    report = f"""# MEIO Weekly vs Baseline — Full Twin Simulation
Supply Brain capstone | generated by `simulate_policy.py`

Both arms run through the SAME shared physics engine
(`models/twin/digital_twin.py`) from the SAME opening state
(`output datasets/history/opening_position.csv`) across the full 2026
forward horizon -- the comparison isolates the policy decision.

- **Baseline**: `output datasets/history/forward_policy.csv` (the
  world-builder's own baseline policy).
- **MEIO weekly**: this run's `output/{target_week}/meio_policy_weekly.csv`,
  computed as-of the last historical week using bias-corrected demand and
  blended short+long trailing CV (see `meio_weekly_lib.py`).

## Result
- Customer fill rate: baseline {baseline_kpi['customer_fill_rate']:.2%} ->
  MEIO weekly {meio_kpi['customer_fill_rate']:.2%} ({fr_delta:+.1f}pp)
- Total landed cost: baseline ${baseline_kpi['total_landed_cost_usd']:,.0f} ->
  MEIO weekly ${meio_kpi['total_landed_cost_usd']:,.0f}
  ({cost_delta:+,.0f})
- **{verdict}**

## Full comparison
{df_to_md(summary.round(4))}

## Files
- `simulation_output/twin_runs/{{baseline_asis,meio_weekly}}/` (gitignored; full twin run detail)
- `simulation_summary.csv`, `chart_fill_rate_comparison.png`
"""
    with open(os.path.join(SCRIPT_DIR, "simulation_report.md"), "w") as f:
        f.write(report)
    print(f"\n{verdict}")
    print("Wrote simulation_summary.csv, chart_fill_rate_comparison.png, simulation_report.md")


if __name__ == "__main__":
    main()
