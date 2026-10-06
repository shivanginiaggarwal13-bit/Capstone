"""
Clark-Scarf v2 — Multi-Seed Robustness Check on the Real-Data Walk-Forward
Backtest, at alpha=0.4 and alpha=0.6
===============================================================================

The alpha sweep (`backtest_rolling_real_clark_scarf_alpha_sweep.py`) found
alpha=0.6 gives the best real-data fill rate and alpha=0.4 the lowest
real-data cost -- both better than alpha=0.2 (the value a grid search
against SYNTHETIC 2026 demand had picked). But that sweep ran at ONE seed:
a single Monte Carlo DDLT draw for the policy, and a single lead-time/
production-adherence realization in the twin. A win on one seed is not
evidence it generalizes -- exactly the reasoning `multi_seed_robustness.py`
and the original (script-not-preserved) 5-seed check on alpha=0.2's
SYNTHETIC-basis result both used. This applies that same standard here.

Design: for each seed, "seed" defines ONE complete alternate world --
it seeds BOTH the Monte Carlo DDLT draws inside `clark_scarf_v2_model.solve`
(so the policy itself is a different realization, not just the twin's
noise) AND the twin's lead-time/production RNG for the paired "actual" and
"clark_scarf_v2" runs at that seed (demand is NOT random here -- both arms
replay the same real orders.csv). The "actual" arm is genuinely re-run per
seed (not reused from the single cached seed=2026 run the sweep used),
because the twin's own lead-time/production draws still vary by seed even
though its policy (baseline_policy.csv) doesn't -- a fair paired comparison
needs both arms on the same seed.

    python backtest_rolling_real_clark_scarf_multiseed.py --alphas 0.4 0.6 --seeds 2026 7 42 123 2027

Outputs (this dir):
    backtest_rolling_real_output/multiseed/{actual,clark_scarf_v2}_alpha{a}_seed{s}/  (gitignored)
    backtest_rolling_real_clark_scarf_multiseed_summary.csv                           (repo-tracked)
    chart_backtest_rolling_real_clark_scarf_multiseed.png                             (repo-tracked)
    backtest_rolling_real_clark_scarf_multiseed_report.md                             (repo-tracked)
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_rolling_real_clark_scarf as b

SCRIPT_DIR = b.SCRIPT_DIR
MULTISEED_OUT = os.path.join(b.OUT_DIR, "multiseed")
DEFAULT_ALPHAS = (0.4, 0.6)
DEFAULT_SEEDS = (2026, 7, 42, 123, 2027)


def df_to_md(df):
    return b.df_to_md(df)


def run_pair(alpha, seed, shared):
    rng = np.random.default_rng(seed)  # one world: drives Monte Carlo DDLT for this alpha+seed

    tag = f"{alpha:.1f}".replace(".", "")
    policy_csv = b.build_walkforward_policy(
        b.DEFAULT_DATA,
        os.path.join(MULTISEED_OUT, f"policy_alpha{tag}_seed{seed}.csv"),
        alpha=alpha, rng=rng, shared=shared)

    actual_run = f"actual_seed{seed}"
    csv2_run = f"clark_scarf_v2_alpha{tag}_seed{seed}"

    actual_out = os.path.join(MULTISEED_OUT, actual_run)
    if os.path.exists(os.path.join(actual_out, "run_kpis.json")):
        print(f"  [reuse] {actual_run} already run (shared across alphas at this seed)")
        import json
        with open(os.path.join(actual_out, "run_kpis.json")) as f:
            actual_kpi = json.load(f)
        actual_inv = pd.read_csv(os.path.join(actual_out, "inventory.csv"))
    else:
        actual_kpi, actual_inv = _run_twin_in(actual_run, b.BASELINE_POLICY_CSV, seed)

    csv2_kpi, csv2_inv = _run_twin_in(csv2_run, policy_csv, seed)

    ok, _, _ = b.warmup_parity_check(actual_inv, csv2_inv)
    if not ok:
        print(f"  WARNING: alpha={alpha} seed={seed} warm-up diverged -- cost delta not test-window-clean")

    test_actual = actual_inv[(actual_inv.period >= b.TEST_START) & (actual_inv.period <= b.TEST_END)]
    test_csv2 = csv2_inv[(csv2_inv.period >= b.TEST_START) & (csv2_inv.period <= b.TEST_END)]
    fr_actual = b.fill_rate_kpis(test_actual, "actual")
    fr_csv2 = b.fill_rate_kpis(test_csv2, "clark_scarf_v2")

    return {
        "alpha": alpha, "seed": seed,
        "actual_fill": fr_actual["customer_fill_rate"],
        "csv2_fill": fr_csv2["customer_fill_rate"],
        "fill_delta_pp": (fr_csv2["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100,
        "actual_internal_replen": fr_actual["internal_replen_fill_rate"],
        "csv2_internal_replen": fr_csv2["internal_replen_fill_rate"],
        "actual_total_cost": actual_kpi["total_landed_cost_usd"],
        "csv2_total_cost": csv2_kpi["total_landed_cost_usd"],
        "cost_delta_usd": csv2_kpi["total_landed_cost_usd"] - actual_kpi["total_landed_cost_usd"],
    }


def _run_twin_in(run_name, policy_csv, seed):
    import json, subprocess
    out_dir = os.path.join(MULTISEED_OUT, run_name)
    cmd = [sys.executable, b.TWIN, "--mode", "history", "--horizon-weeks", str(b.HISTORY_WEEKS),
           "--run-name", run_name, "--policy", policy_csv, "--seed", str(seed),
           "--data-dir", b.DEFAULT_DATA, "--out-root", MULTISEED_OUT, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=b.REPO_ROOT)
    with open(os.path.join(out_dir, "run_kpis.json")) as f:
        kpi = json.load(f)
    inv = pd.read_csv(os.path.join(out_dir, "inventory.csv"))
    return kpi, inv


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alphas", type=float, nargs="+", default=list(DEFAULT_ALPHAS))
    ap.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    args = ap.parse_args()

    os.makedirs(MULTISEED_OUT, exist_ok=True)
    print("Loading shared (alpha/seed-independent) inputs once ...")
    shared = b.load_shared(b.DEFAULT_DATA)

    rows = []
    for alpha in args.alphas:
        for seed in args.seeds:
            print(f"\n=== alpha={alpha:.1f} seed={seed} ===")
            row = run_pair(alpha, seed, shared)
            rows.append(row)
            print(f"  actual fill {row['actual_fill']:.2%}  csv2 fill {row['csv2_fill']:.2%}  "
                  f"delta {row['fill_delta_pp']:+.2f}pp  cost delta ${row['cost_delta_usd']:+,.0f}")

    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(SCRIPT_DIR, "backtest_rolling_real_clark_scarf_multiseed_summary.csv"), index=False)

    agg_rows = []
    for alpha in args.alphas:
        sub = summary[summary.alpha == alpha]
        n_wins = (sub.fill_delta_pp > 0).sum()
        agg_rows.append({
            "alpha": alpha, "n_seeds": len(sub), "n_wins_fill": n_wins,
            "fill_delta_mean_pp": sub.fill_delta_pp.mean(), "fill_delta_std_pp": sub.fill_delta_pp.std(),
            "fill_delta_min_pp": sub.fill_delta_pp.min(), "fill_delta_max_pp": sub.fill_delta_pp.max(),
            "cost_delta_mean_usd": sub.cost_delta_usd.mean(), "cost_delta_std_usd": sub.cost_delta_usd.std(),
        })
    agg = pd.DataFrame(agg_rows)
    print("\n=== Aggregate across seeds ===")
    print(agg.to_string(index=False))

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    colors = {0.4: "#2b6cb0", 0.6: "#c0392b"}
    for alpha in args.alphas:
        sub = summary[summary.alpha == alpha].sort_values("seed")
        c = colors.get(alpha, None)
        axes[0].plot(sub.seed.astype(str), sub.fill_delta_pp, marker="o", label=f"alpha={alpha:.1f}", color=c)
        axes[1].plot(sub.seed.astype(str), sub.cost_delta_usd / 1e6, marker="o", label=f"alpha={alpha:.1f}", color=c)
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_xlabel("Seed")
    axes[0].set_ylabel("Fill rate delta (pp, Clark-Scarf v2 - actual)")
    axes[0].set_title("Real-data fill rate delta by seed")
    axes[0].legend()
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_xlabel("Seed")
    axes[1].set_ylabel("Total landed cost delta ($M)")
    axes[1].set_title("Real-data cost delta by seed")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_backtest_rolling_real_clark_scarf_multiseed.png"), dpi=150)
    plt.close(fig)

    def verdict_for(row):
        if row.n_wins_fill == row.n_seeds:
            return "Holds up in every seed"
        elif row.n_wins_fill == 0:
            return "Loses in every seed"
        else:
            return f"Mixed -- wins {row.n_wins_fill}/{row.n_seeds} seeds"

    report_lines = [f"""# Clark-Scarf v2 — Multi-Seed Robustness on the Real-Data Walk-Forward Backtest
Supply Brain capstone | generated by `backtest_rolling_real_clark_scarf_multiseed.py`

## What this checks
The alpha sweep found alpha=0.6 best for real-data fill rate and alpha=0.4
best for real-data cost, both at a single seed (one Monte Carlo DDLT draw,
one twin lead-time/production realization). This re-runs BOTH alphas across
{len(args.seeds)} seeds ({', '.join(str(s) for s in args.seeds)}) -- each
seed reseeds the policy's own Monte Carlo draws AND the twin's lead-time/
production RNG together (a genuinely different world), with actual and
Clark-Scarf v2 paired on the same seed for a fair comparison. Demand itself
is NOT random here (real orders.csv replay, identical across seeds) -- only
the policy's Monte Carlo estimate and the twin's internal dynamics vary.
"""]
    for _, r in agg.iterrows():
        sub = summary[summary.alpha == r.alpha]
        report_lines.append(f"""
## alpha={r.alpha:.1f}: {verdict_for(r)}
- Clark-Scarf v2 beats actual on customer fill rate in **{int(r.n_wins_fill)}/{int(r.n_seeds)}** seeds.
- Fill rate delta: mean {r.fill_delta_mean_pp:+.2f}pp, std {r.fill_delta_std_pp:.2f}pp,
  range [{r.fill_delta_min_pp:+.2f}, {r.fill_delta_max_pp:+.2f}]pp
- Cost delta: mean ${r.cost_delta_mean_usd:+,.0f}, std ${r.cost_delta_std_usd:,.0f}

{df_to_md(sub[["seed", "actual_fill", "csv2_fill", "fill_delta_pp", "actual_internal_replen",
               "csv2_internal_replen", "cost_delta_usd"]].round(4))}
""")
    report_lines.append("""
## Files
- `backtest_rolling_real_output/multiseed/{actual,clark_scarf_v2}_alpha{a}_seed{s}/` (gitignored; full twin run detail)
- `backtest_rolling_real_clark_scarf_multiseed_summary.csv`, `chart_backtest_rolling_real_clark_scarf_multiseed.png`
""")
    with open(os.path.join(SCRIPT_DIR, "backtest_rolling_real_clark_scarf_multiseed_report.md"), "w") as f:
        f.write("".join(report_lines))

    for _, r in agg.iterrows():
        print(f"\nalpha={r.alpha:.1f}: {verdict_for(r)}")
    print("\nWrote backtest_rolling_real_clark_scarf_multiseed_summary.csv, "
          "chart_backtest_rolling_real_clark_scarf_multiseed.png, "
          "backtest_rolling_real_clark_scarf_multiseed_report.md")


if __name__ == "__main__":
    main()
