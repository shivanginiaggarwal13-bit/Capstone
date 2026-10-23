"""
Compare All MEIO Models Side by Side
========================================

Runs every model on the SAME frozen single-week basis (as-of 2025-W52,
target 2026-W01 -- matching `simulate_policy.py`'s scope, not the 52-week
rolling policy, so this stays fast and every model is compared on
identical footing) and scores each through the SAME shared physics engine
from the SAME opening state:

    - baseline           the dataset's own forward_policy.csv
    - meio_weekly (v10)  this project's current tool (meio_optimizer_weekly.py,
                          all fixes through the demand-smoothing + PDC-smoothing
                          rounds -- see README)
    - clark_scarf        Alt Model 1 -- echelon stock decomposition
    - gsm                Alt Model 2 -- Guaranteed Service Model (simplified)
    - sim_optimize       Alt Model 3 -- random search over meio_weekly's own
                          constants, twin as reward oracle
    - bandit             Alt Model 4 -- Thompson Sampling over service-level
                          tiers, twin as reward oracle

    python compare_models.py

sim_optimize and bandit run many internal twin evaluations already (search
trials / bandit rounds) -- expect this to take several minutes total.

Output: alt_output/model_comparison_summary.csv, chart_model_comparison.png,
        model_comparison_report.md
"""
import json
import os
import subprocess
import sys

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import meio_optimizer_weekly as weekly
import clark_scarf_model
import gsm_model
import sim_optimize_model
import bandit_model

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")
TWIN_OUT = os.path.join(SCRIPT_DIR, "compare_twin_runs")


def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def run_twin(run_name, policy_csv, state_csv, label):
    out_dir = os.path.join(TWIN_OUT, run_name)
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--out-root", TWIN_OUT, "--label", label]
    print(f"  scoring {run_name}...")
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_dir, "run_kpis.json")) as f:
        return json.load(f)


def main(rebuild=False, n_trials=15, rounds_per_segment=8):
    data_dir = weekly.DEFAULT_DATA
    as_of_week = "2025-W52"
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")
    baseline_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")
    out_dir_check = os.path.join(SCRIPT_DIR, "alt_output")

    reused = {}  # report text only: which policies were read from disk vs built this run

    def existing_or_build(path, build_fn, label):
        if not rebuild and os.path.exists(path):
            print(f"\n-- {label}: reusing existing {path} --")
            reused[os.path.basename(path)] = True
            return path
        print(f"\n-- {label} --")
        reused[os.path.basename(path)] = False
        return build_fn()

    print("=== Building each model's policy (reusing alt_output/*.csv already on disk unless --rebuild) ===")
    meio_weekly_csv = os.path.join(weekly.SCRIPT_DIR, "output", "2026-W01", "meio_policy_weekly.csv")
    reused["meio_policy_weekly.csv"] = not (rebuild or not os.path.exists(meio_weekly_csv))
    if rebuild or not os.path.exists(meio_weekly_csv):
        print("\n-- meio_weekly (v10, current tool) --")
        weekly.run(data_dir, as_of_week, weekly.lib.SHORT_WINDOW_WEEKS, weekly.lib.LONG_WINDOW_WEEKS)
    else:
        print(f"\n-- meio_weekly (v10): reusing existing {meio_weekly_csv} --")

    clark_scarf_csv = existing_or_build(
        os.path.join(out_dir_check, "clark_scarf_policy.csv"),
        lambda: clark_scarf_model.main(as_of_week=as_of_week, data_dir=data_dir), "clark_scarf")
    gsm_csv = existing_or_build(
        os.path.join(out_dir_check, "gsm_policy.csv"),
        lambda: gsm_model.main(as_of_week=as_of_week, data_dir=data_dir), "gsm")
    sim_optimize_csv = existing_or_build(
        os.path.join(out_dir_check, "sim_optimize_policy.csv"),
        lambda: sim_optimize_model.main(n_trials=n_trials, seed=42, as_of_week=as_of_week, data_dir=data_dir),
        "sim_optimize (runs many twin trials internally)")
    bandit_csv = existing_or_build(
        os.path.join(out_dir_check, "bandit_policy.csv"),
        lambda: bandit_model.main(rounds_per_segment=rounds_per_segment, seed=7, as_of_week=as_of_week,
                                   data_dir=data_dir),
        "bandit (runs many twin rounds internally)")

    models = {
        "baseline": baseline_policy_csv,
        "meio_weekly_v10": meio_weekly_csv,
        "clark_scarf": clark_scarf_csv,
        "gsm": gsm_csv,
        "sim_optimize": sim_optimize_csv,
        "bandit": bandit_csv,
    }

    print("\n=== Scoring each model's final policy through the twin ===")
    rows = []
    for name, policy_csv in models.items():
        kpi = run_twin(f"final_{name}", policy_csv, state_csv, name)
        rows.append({
            "model": name,
            "customer_fill_rate": kpi["customer_fill_rate"],
            "customer_fill_rate_cdc": kpi["customer_fill_rate_cdc"],
            "customer_fill_rate_rdc": kpi["customer_fill_rate_rdc"],
            "internal_replen_fill_rate": kpi["internal_replen_fill_rate"],
            "holding_cost_usd": kpi["holding_cost_usd"],
            "stockout_cost_usd": kpi["stockout_cost_usd"],
            "total_landed_cost_usd": kpi["total_landed_cost_usd"],
        })

    summary = pd.DataFrame(rows)
    baseline_cost = summary.loc[summary.model == "baseline", "total_landed_cost_usd"].iloc[0]
    baseline_fill = summary.loc[summary.model == "baseline", "customer_fill_rate"].iloc[0]
    summary["fill_rate_vs_baseline_pp"] = (summary.customer_fill_rate - baseline_fill) * 100
    summary["cost_vs_baseline_usd"] = summary.total_landed_cost_usd - baseline_cost

    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    summary.to_csv(os.path.join(out_dir, "model_comparison_summary.csv"), index=False)
    print("\n" + summary.to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    order = summary.sort_values("customer_fill_rate", ascending=False)
    x = range(len(order))
    colors = ["#9aa5b1" if m == "baseline" else "#2b6cb0" for m in order.model]
    axes[0].bar(x, order.customer_fill_rate * 100, color=colors)
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(order.model, rotation=30, ha="right")
    axes[0].set_ylabel("Customer fill rate (%)")
    axes[0].set_title("Fill Rate by Model")
    axes[0].axhline(baseline_fill * 100, color="#e53e3e", linestyle="--", linewidth=1, label="baseline")
    axes[0].legend()

    axes[1].bar(x, order.total_landed_cost_usd / 1e6, color=colors)
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels(order.model, rotation=30, ha="right")
    axes[1].set_ylabel("Total landed cost ($M)")
    axes[1].set_title("Total Landed Cost by Model")
    axes[1].axhline(baseline_cost / 1e6, color="#e53e3e", linestyle="--", linewidth=1, label="baseline")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "chart_model_comparison.png"), dpi=150)
    plt.close(fig)

    def src(fname, settings=""):
        if reused.get(fname):
            return f"reused from disk (`{fname}`, built by an earlier run; its settings are not re-checked here)"
        return f"built this run{settings}"

    report = f"""# MEIO Model Comparison — All 4 Alternative Models vs. Current Tool
Supply Brain capstone | generated by `compare_models.py`

All models sized against the same frozen single-week basis (as-of
{as_of_week}, target 2026-W01) and scored through the same shared physics
engine (`models/twin/digital_twin.py`) from the same opening state, across
the full 2026 forward horizon. Each model's policy is scored once
(`--mode forward`, the twin's default seed; no `--seed` passed).

## Policy sources in this run
- `meio_weekly_v10`: {src("meio_policy_weekly.csv")}
- `clark_scarf`: {src("clark_scarf_policy.csv")}
- `gsm`: {src("gsm_policy.csv")}
- `sim_optimize`: {src("sim_optimize_policy.csv", f" ({n_trials} random-search trials, seed 42)")}
- `bandit`: {src("bandit_policy.csv", f" ({rounds_per_segment} rounds per segment, seed 7)")}

Service level (z) as the current code builds each policy: `meio_weekly_v10`
and `sim_optimize` size through `meio_weekly_lib.solve_policy()`, so z comes
from `meio_weekly_lib.assign_z()` (canonical segmentation cluster z).
`clark_scarf` and `gsm` read `sku_master.csv`'s segment `Z_BY_SEGMENT` directly.
`bandit`'s arms swap `Z_BY_SEGMENT`, which `assign_z()` uses only as a fallback
for SKUs missing from the cluster file, so a rebuilt bandit's arms do not
change the sized service level.

## Result
{df_to_md(order.round(4))}

## Files
- `model_comparison_summary.csv`, `chart_model_comparison.png`
- Each model's own policy: `clark_scarf_policy.csv`, `gsm_policy.csv`,
  `sim_optimize_policy.csv` (+`sim_optimize_trials.csv`), `bandit_policy.csv`
  (+`bandit_belief_history.csv`)
- Full twin run detail: `compare_twin_runs/`, `sim_optimize_trials/`,
  `bandit_trials/` (all gitignored)
"""
    with open(os.path.join(out_dir, "model_comparison_report.md"), "w") as f:
        f.write(report)
    print(f"\nWrote {out_dir}/model_comparison_summary.csv, chart_model_comparison.png, "
          f"model_comparison_report.md")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                     help="Re-run every model's search/solve instead of reusing alt_output/*.csv on disk.")
    ap.add_argument("--n-trials", type=int, default=15)
    ap.add_argument("--rounds-per-segment", type=int, default=8)
    args = ap.parse_args()
    main(rebuild=args.rebuild, n_trials=args.n_trials, rounds_per_segment=args.rounds_per_segment)
