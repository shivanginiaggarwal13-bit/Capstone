"""
Supply Brain — Multi-Seed Robustness Check for the Rolling MEIO Policy
=========================================================================

Every simulation result in this project so far (v1-v8, see README) used the
digital twin's default seed (2026) for exactly one demand/lead-time/
production-adherence realization. A win on one random draw is not evidence
a policy generalizes -- it could be a fluke of that specific draw. This
script re-runs baseline vs. MEIO rolling weekly at several different seeds
(same seed used for both arms within a pair, so each pair stays a fair
apples-to-apples comparison; --seed reseeds demand, lead-time AND
production RNG together, so each seed is a genuinely different world, not
just noisier demand).

    python multi_seed_robustness.py --seeds 2026,7,42,123,2027

Outputs:
    simulation_output/twin_runs/robustness/  (gitignored; full per-seed twin run detail)
    robustness_summary.csv                   (repo-tracked)
    chart_robustness.png                     (repo-tracked)
    robustness_report.md                     (repo-tracked)
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")
TWIN_OUT = os.path.join(SCRIPT_DIR, "simulation_output", "twin_runs", "robustness")


def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def run_twin(run_name, policy_csv, state_csv, seed, label):
    out_dir = os.path.join(TWIN_OUT, run_name)
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--seed", str(seed),
           "--out-root", TWIN_OUT, "--label", label]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_dir, "run_kpis.json")) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", default="2026,7,42,123,2027")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    baseline_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.csv")
    rolling_policy_csv = os.path.join(SCRIPT_DIR, "output", "rolling_2026", "meio_policy_rolling.csv")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")
    if not os.path.exists(rolling_policy_csv):
        raise SystemExit(f"{rolling_policy_csv} not found -- run roll_forward_policy.py first")

    rows = []
    for seed in seeds:
        print(f"\n=== seed {seed} ===")
        b = run_twin(f"baseline_seed{seed}", baseline_policy_csv, state_csv, seed, f"baseline seed={seed}")
        m = run_twin(f"meio_rolling_seed{seed}", rolling_policy_csv, state_csv, seed, f"meio rolling seed={seed}")
        rows.append({
            "seed": seed,
            "baseline_fill_rate": b["customer_fill_rate"],
            "meio_fill_rate": m["customer_fill_rate"],
            "fill_rate_delta_pp": (m["customer_fill_rate"] - b["customer_fill_rate"]) * 100,
            "baseline_total_cost": b["total_landed_cost_usd"],
            "meio_total_cost": m["total_landed_cost_usd"],
            "cost_delta_usd": m["total_landed_cost_usd"] - b["total_landed_cost_usd"],
            "baseline_internal_replen_fill": b["internal_replen_fill_rate"],
            "meio_internal_replen_fill": m["internal_replen_fill_rate"],
        })
        print(f"  baseline fill {b['customer_fill_rate']:.2%}  meio fill {m['customer_fill_rate']:.2%}  "
              f"delta {(m['customer_fill_rate']-b['customer_fill_rate'])*100:+.1f}pp  "
              f"cost delta ${m['total_landed_cost_usd']-b['total_landed_cost_usd']:+,.0f}")

    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(SCRIPT_DIR, "robustness_summary.csv"), index=False)

    n_wins = (summary.fill_rate_delta_pp > 0).sum()
    print(f"\nMEIO rolling beats baseline on fill rate in {n_wins}/{len(seeds)} seeds")
    print(f"Fill rate delta: mean {summary.fill_rate_delta_pp.mean():+.2f}pp, "
          f"std {summary.fill_rate_delta_pp.std():.2f}pp, "
          f"range [{summary.fill_rate_delta_pp.min():+.2f}, {summary.fill_rate_delta_pp.max():+.2f}]pp")
    print(f"Cost delta: mean ${summary.cost_delta_usd.mean():+,.0f}, "
          f"std ${summary.cost_delta_usd.std():,.0f}")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    x = range(len(summary))
    axes[0].bar(x, summary.fill_rate_delta_pp, color=["#2b6cb0" if v > 0 else "#e53e3e"
                                                        for v in summary.fill_rate_delta_pp])
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(summary.seed)
    axes[0].set_xlabel("Seed")
    axes[0].set_ylabel("Fill rate delta (pp, MEIO - baseline)")
    axes[0].set_title("Customer Fill Rate Delta by Seed")

    axes[1].bar(x, summary.cost_delta_usd / 1e6, color=["#e53e3e" if v > 0 else "#2b6cb0"
                                                          for v in summary.cost_delta_usd])
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels(summary.seed)
    axes[1].set_xlabel("Seed")
    axes[1].set_ylabel("Total landed cost delta ($M, MEIO - baseline)")
    axes[1].set_title("Cost Delta by Seed")
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_robustness.png"), dpi=150)
    plt.close(fig)

    verdict = ("Holds up" if n_wins == len(seeds) else
               "Mixed -- NOT robust across seeds" if n_wins <= len(seeds) // 2 else
               "Mostly holds, not universal")
    report = f"""# MEIO Rolling Weekly — Multi-Seed Robustness Check
Supply Brain capstone | generated by `multi_seed_robustness.py`

Every prior result in this project (v1-v8) used the digital twin's default
seed (2026) for one specific demand/lead-time/production-adherence
realization. This re-runs baseline vs. MEIO rolling weekly at
{len(seeds)} different seeds ({', '.join(str(s) for s in seeds)}) -- each
seed reseeds demand, lead-time AND production RNG together (a genuinely
different world per seed), with the same seed used for both arms within a
pair so each comparison stays fair.

Both arms are policies held fixed across seeds: the baseline is
`output datasets/history/forward_policy.csv`, and the MEIO arm is the rolling
policy already on disk at
`{os.path.relpath(rolling_policy_csv, REPO_ROOT).replace(os.sep, "/")}` (read as-is, not
rebuilt by this script). Only the twin's `--mode forward` simulation is
reseeded; both start from `output datasets/history/opening_position.csv`.

## Result: {verdict}
- MEIO rolling beats baseline on customer fill rate in **{n_wins}/{len(seeds)}** seeds.
- Fill rate delta: mean {summary.fill_rate_delta_pp.mean():+.2f}pp, std {summary.fill_rate_delta_pp.std():.2f}pp,
  range [{summary.fill_rate_delta_pp.min():+.2f}, {summary.fill_rate_delta_pp.max():+.2f}]pp
- Cost delta: mean ${summary.cost_delta_usd.mean():+,.0f}, std ${summary.cost_delta_usd.std():,.0f}

## Per-seed detail
{df_to_md(summary.round(4))}

## Files
- `simulation_output/twin_runs/robustness/` (gitignored; full per-seed twin run detail)
- `robustness_summary.csv`, `chart_robustness.png`
"""
    with open(os.path.join(SCRIPT_DIR, "robustness_report.md"), "w") as f:
        f.write(report)
    print(f"\n{verdict}")
    print("Wrote robustness_summary.csv, chart_robustness.png, robustness_report.md")


if __name__ == "__main__":
    main()
