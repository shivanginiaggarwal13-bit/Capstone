"""
Clark-Scarf v2 — Continuous-Alpha Sweep on the Real-Data Walk-Forward Backtest
===============================================================================

`backtest_rolling_real_clark_scarf.py` validated ONE alpha (0.2 -- the value
`clark_scarf_v2_rolling.py`'s grid search picked as lowest-cost on the
SYNTHETIC 2026 demand stream) against real replayed 2025 demand. The
obvious follow-up: does 0.2 still look right once you're scoring against
demand the estimator never saw, or would a different point on the
continuous pooling dial have done better/worse on the real holdout?

This script re-runs that same real-data walk-forward -- identical warm-up,
identical 2025-W02..2025-W52 test window, identical "no lookahead" per-week
refit -- at every alpha in the original synthetic-basis grid (0.0, 0.2, 0.4,
0.6, 0.8, 1.0), all scored against the SAME real demand and the SAME cached
"actual" arm. This is a sensitivity check, not a re-optimization: the
question is "how much does the answer move across the alpha range on real
data," not "which alpha should we now switch to" -- picking a NEW alpha
after seeing it win on this same held-out window would just relocate the
overfitting risk this whole exercise exists to catch.

    python backtest_rolling_real_clark_scarf_alpha_sweep.py

Outputs (this dir):
    backtest_rolling_real_output/policy_clark_scarf_v2_weekly_alpha{a}_156wk.csv  (gitignored, one per alpha)
    backtest_rolling_real_output/clark_scarf_v2_weekly_alpha{a}/                  (gitignored; full twin run detail per alpha)
    backtest_rolling_real_clark_scarf_alpha_sweep_summary.csv                     (repo-tracked)
    chart_backtest_rolling_real_clark_scarf_alpha_sweep.png                       (repo-tracked)
    backtest_rolling_real_clark_scarf_alpha_sweep_report.md                       (repo-tracked)
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
ALPHAS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def df_to_md(df):
    return b.df_to_md(df)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alphas", type=float, nargs="+", default=list(ALPHAS))
    ap.add_argument("--seed", type=int, default=b.SEED)
    args = ap.parse_args()

    print("=== 1. Loading the cached 'actual' arm ===")
    actual_kpi, inv_actual = b.load_cached_actual()
    test_actual = inv_actual[(inv_actual.period >= b.TEST_START) & (inv_actual.period <= b.TEST_END)]
    fr_actual = b.fill_rate_kpis(test_actual, "actual")

    print("\n=== 2. Loading shared (alpha-independent) inputs once ===")
    shared = b.load_shared(b.DEFAULT_DATA)
    rng = np.random.default_rng(args.seed)  # one shared stream across the whole sweep, matching clark_scarf_v2_rolling.py

    fr_rows = [fr_actual]
    cost_rows = []
    for alpha in args.alphas:
        tag = f"{alpha:.1f}".replace(".", "")
        print(f"\n=== alpha={alpha:.1f}: building walk-forward policy ===")
        policy_csv = b.build_walkforward_policy(
            b.DEFAULT_DATA, os.path.join(b.OUT_DIR, f"policy_clark_scarf_v2_weekly_alpha{tag}_156wk.csv"),
            alpha=alpha, rng=rng, shared=shared)

        run_name = f"clark_scarf_v2_weekly_alpha{tag}"
        print(f"=== alpha={alpha:.1f}: running digital twin (156-week history replay) ===")
        kpi, inv = b.run_twin(run_name, policy_csv, seed=args.seed)

        ok, a_tot, c_tot = b.warmup_parity_check(inv_actual, inv)
        if not ok:
            print(f"  WARNING: alpha={alpha:.1f} warm-up diverged from 'actual' -- its cost delta below "
                  f"is not safely attributable to the test window alone.")

        test_inv = inv[(inv.period >= b.TEST_START) & (inv.period <= b.TEST_END)]
        fr = b.fill_rate_kpis(test_inv, f"alpha={alpha:.1f}")
        fr["alpha"] = alpha
        fr_rows.append(fr)

        for k in ["holding_cost_usd", "stockout_cost_usd", "transport_cost_usd", "total_landed_cost_usd"]:
            cost_rows.append({"alpha": alpha, "metric": k, "actual_full156wk": actual_kpi.get(k),
                               f"clark_scarf_v2_full156wk": kpi.get(k),
                               "delta_attributable_to_test_window": kpi.get(k) - actual_kpi.get(k)
                               if kpi.get(k) is not None and actual_kpi.get(k) is not None else None})
        print(f"  alpha={alpha:.1f}: fill {fr['customer_fill_rate']:.2%}  "
              f"internal_replen {fr['internal_replen_fill_rate']:.2%}  "
              f"total_landed_cost_delta ${cost_rows[-1]['delta_attributable_to_test_window']:+,.0f}")

    fr_summary = pd.DataFrame(fr_rows)
    cost_summary = pd.DataFrame(cost_rows)
    fr_summary.to_csv(os.path.join(SCRIPT_DIR, "backtest_rolling_real_clark_scarf_alpha_sweep_summary.csv"), index=False)
    cost_summary.to_csv(os.path.join(SCRIPT_DIR, "backtest_rolling_real_clark_scarf_alpha_sweep_cost_summary.csv"),
                         index=False)
    print("\n" + fr_summary.to_string(index=False))

    total_cost = cost_summary[cost_summary.metric == "total_landed_cost_usd"].set_index("alpha")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    alphas_plotted = [r["alpha"] for r in fr_rows if "alpha" in r]
    axes[0].axhline(fr_actual["customer_fill_rate"] * 100, color="#9aa5b1", linestyle="--", label="Actual")
    axes[0].plot(alphas_plotted, [r["customer_fill_rate"] * 100 for r in fr_rows if "alpha" in r],
                 marker="o", color="#c0392b", label="Customer fill rate")
    axes[0].plot(alphas_plotted, [r["internal_replen_fill_rate"] * 100 for r in fr_rows if "alpha" in r],
                 marker="s", color="#2b6cb0", label="Internal replen fill rate")
    axes[0].axvline(0.2, color="gray", linestyle=":", alpha=0.6)
    axes[0].annotate("chosen (synthetic-basis best)", xy=(0.2, axes[0].get_ylim()[0]), fontsize=8, color="gray")
    axes[0].set_xlabel("alpha (0=independent, 1=fully pooled)")
    axes[0].set_ylabel("Fill rate (%)")
    axes[0].set_title("Real 2025-W02..W52 demand: Fill Rate vs. alpha")
    axes[0].legend(fontsize=8)

    axes[1].plot(total_cost.index, total_cost.delta_attributable_to_test_window / 1e6, marker="o", color="#c0392b")
    axes[1].axhline(0, color="#9aa5b1", linestyle="--")
    axes[1].axvline(0.2, color="gray", linestyle=":", alpha=0.6)
    axes[1].set_xlabel("alpha")
    axes[1].set_ylabel("Total landed cost delta vs. actual ($M, test-window)")
    axes[1].set_title("Cost Delta vs. alpha")
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_backtest_rolling_real_clark_scarf_alpha_sweep.png"), dpi=150)
    plt.close(fig)

    best_fill_row = max([r for r in fr_rows if "alpha" in r], key=lambda r: r["customer_fill_rate"])
    best_cost_alpha = total_cost.delta_attributable_to_test_window.idxmin()

    report = f"""# Clark-Scarf v2 — Continuous-Alpha Sweep on the Real-Data Walk-Forward Backtest
Supply Brain capstone | generated by `backtest_rolling_real_clark_scarf_alpha_sweep.py`

## What this answers
`backtest_rolling_real_clark_scarf.py` validated alpha=0.2 (picked by a grid
search against SYNTHETIC 2026 demand) against real replayed 2025 demand.
This sweep re-runs the identical real-data walk-forward at every alpha in
that same grid ({", ".join(f"{a:.1f}" for a in args.alphas)}), so we can see
whether 0.2 is still a reasonable choice once scored on demand the
estimator never saw, or whether the real-data answer would have pointed
somewhere else. **This is a sensitivity check, not a re-optimization** --
picking a new alpha off the back of this same held-out window would just
relocate the overfitting risk, not remove it.

## Result: fill rate vs. alpha, real 2025-W02..W52 demand only
{df_to_md(fr_summary[["arm", "customer_fill_rate", "customer_fill_rate_cdc", "customer_fill_rate_rdc",
                       "internal_replen_fill_rate", "pct_weeks_at_zero_stock"]].round(4))}

- Best real-data customer fill rate: **{best_fill_row['arm']}** ({best_fill_row['customer_fill_rate']:.2%})
- Lowest real-data total landed cost delta vs. actual: **alpha={best_cost_alpha:.1f}**
  (${total_cost.loc[best_cost_alpha, 'delta_attributable_to_test_window']:+,.0f})
- alpha=0.2 (the synthetic-basis choice) scored {fr_summary.loc[fr_summary.alpha == 0.2, 'customer_fill_rate'].iloc[0]:.2%}
  fill / ${cost_summary[(cost_summary.alpha == 0.2) & (cost_summary.metric == 'total_landed_cost_usd')]['delta_attributable_to_test_window'].iloc[0]:+,.0f}
  cost delta on real data, for direct comparison.

## Result: cost delta vs. alpha (full-156-week totals; delta = test-window effect)
{df_to_md(cost_summary[cost_summary.metric == "total_landed_cost_usd"][["alpha", "delta_attributable_to_test_window"]].round(2))}

## Files
- `backtest_rolling_real_output/policy_clark_scarf_v2_weekly_alpha{{a}}_156wk.csv` (gitignored, one per alpha)
- `backtest_rolling_real_output/clark_scarf_v2_weekly_alpha{{a}}/` (gitignored; full twin run detail per alpha)
- `backtest_rolling_real_clark_scarf_alpha_sweep_summary.csv`,
  `backtest_rolling_real_clark_scarf_alpha_sweep_cost_summary.csv`,
  `chart_backtest_rolling_real_clark_scarf_alpha_sweep.png`
"""
    with open(os.path.join(SCRIPT_DIR, "backtest_rolling_real_clark_scarf_alpha_sweep_report.md"), "w") as f:
        f.write(report)
    print(f"\nBest real-data fill: {best_fill_row['arm']} ({best_fill_row['customer_fill_rate']:.2%}); "
          f"lowest real-data cost delta: alpha={best_cost_alpha:.1f}")
    print("Wrote backtest_rolling_real_clark_scarf_alpha_sweep_summary.csv, "
          "backtest_rolling_real_clark_scarf_alpha_sweep_cost_summary.csv, "
          "chart_backtest_rolling_real_clark_scarf_alpha_sweep.png, "
          "backtest_rolling_real_clark_scarf_alpha_sweep_report.md")


if __name__ == "__main__":
    main()
