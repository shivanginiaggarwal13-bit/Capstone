"""
Clark-Scarf v2 (continuous alpha + Monte Carlo DDLT) — Extended Real-Data
Walk-Forward Backtest
===============================================================================

The Clark-Scarf v2 rolling counterpart to `../backtest_rolling_real.py`. That
script validated the hand-tuned MEIO Weekly tool against REAL replayed 2025
demand (not the synthetic forecast-error-resampled 2026 stream every other
result in this project uses); this one applies the identical design to
Clark-Scarf v2's continuous-pooling + Monte Carlo DDLT policy, at
**alpha=0.2** -- the alpha `clark_scarf_v2_rolling.py`'s own search selected
as lowest-cost on the synthetic 2026 basis, and the one
`clark_scarf_v2_robustness.csv`'s 5-seed validation (mean +2.63pp fill vs.
baseline, 5/5 seeds) was run at. Reusing that alpha rather than re-searching
here: a fresh alpha sweep would mean re-running Monte Carlo DDLT x 51 weeks
x several alpha values, and the whole point of walk-forward testing is to
check whether an ALREADY-CHOSEN policy holds up on demand it never saw --
re-optimizing alpha against the same real 2025 data this backtest is meant
to hold out would defeat that.

Design (see `../backtest_rolling_real.py`'s docstring for the full reasoning
behind the window boundaries and the warm-up/cost-delta argument -- repeated
only in brief here):

  - `demand_forecast.csv` (every trailing bias/CV statistic either estimator
    reads) only has rows for 2025-W01..2026-W52. 2023-W01..2025-W01 (105
    weeks) is unavoidable warm-up, identical in both arms (the literal
    `baseline_policy.csv`).
  - 2025-W02..2025-W52 (51 weeks): a genuine walk-forward. For target week
    t, as-of = t-1, `clark_scarf_v2_model.solve(..., alpha=0.2, rng=...)` is
    recomputed fresh from only real data at or before week t-1 -- no
    lookahead, and NOT the fixed-as-of design `clark_scarf_v2_rolling.py`
    uses for its 2026 build (there is no real future data there to refit
    on; here there is, so this exercises it).
  - The "actual" arm is NOT re-run: it is byte-identical to the one
    `../backtest_rolling_real.py` already produced (same baseline_policy.csv,
    same seed, same cold start, same demand replay), so this script reads
    its cached inventory.csv/run_kpis.json directly from
    `../backtest_rolling_output/actual/` rather than repeating an ~24s twin
    run for no new information. If that cache is missing, run
    `../backtest_rolling_real.py` first.

    python backtest_rolling_real_clark_scarf.py

Outputs (this dir):
    backtest_rolling_real_output/policy_clark_scarf_v2_weekly_156wk.csv  (gitignored)
    backtest_rolling_real_output/clark_scarf_v2_weekly/                  (gitignored; full twin run detail)
    backtest_rolling_real_clark_scarf_summary.csv                        (repo-tracked)
    backtest_rolling_real_clark_scarf_cost_summary.csv                   (repo-tracked)
    chart_backtest_rolling_real_clark_scarf.png                          (repo-tracked)
    backtest_rolling_real_clark_scarf_report.md                          (repo-tracked)
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MEIO_WEEKLY_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DEFAULT_DATA = os.path.normpath(os.path.join(REPO_ROOT, "input datasets"))
BASELINE_POLICY_CSV = os.path.join(DEFAULT_DATA, "baseline_policy.csv")
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")
OUT_DIR = os.path.join(SCRIPT_DIR, "backtest_rolling_real_output")
CACHED_ACTUAL_DIR = os.path.join(MEIO_WEEKLY_DIR, "backtest_rolling_output", "actual")

ALPHA = 0.2
N_SIMS = 1200
SEED = 2026

WARMUP_START = "2023-W01"
WARMUP_END = "2025-W01"
TEST_START = "2025-W02"
TEST_END = "2025-W52"
HISTORY_WEEKS = 156

POLICY_COLS = ["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]


def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def load_shared(data_dir):
    """Everything build_walkforward_policy needs that doesn't depend on
    alpha -- load once, reuse across an alpha sweep."""
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)  # 2025-W01..2026-W52 only
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)
    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)
    all_periods = sorted(pd.read_csv(os.path.join(data_dir, "orders.csv"), usecols=["period"]).period.unique())
    return dict(dfc=dfc, period_index=period_index, seasonal_index=seasonal_index, sku_subcat=sku_subcat,
                active_rdc=active_rdc, active_cdc=active_cdc, leadtime_by_dc=leadtime_by_dc, cost=cost,
                cdc_primary_rdc=cdc_primary_rdc, all_periods=all_periods)


def build_walkforward_policy(data_dir, out_path, alpha=ALPHA, rng=None, shared=None):
    s = shared or load_shared(data_dir)
    dfc, period_index, seasonal_index, sku_subcat = s["dfc"], s["period_index"], s["seasonal_index"], s["sku_subcat"]
    active_rdc, active_cdc, leadtime_by_dc = s["active_rdc"], s["active_cdc"], s["leadtime_by_dc"]
    cost, cdc_primary_rdc, all_periods = s["cost"], s["cdc_primary_rdc"], s["all_periods"]
    rng = rng if rng is not None else np.random.default_rng(SEED)

    baseline_pol = pd.read_csv(BASELINE_POLICY_CSV)[POLICY_COLS]
    warmup_periods = [p for p in all_periods if WARMUP_START <= p <= WARMUP_END]

    def wk_num(period):
        return int(period_index.loc[period_index.period == period, "week_num"].iloc[0])

    test_start_num, test_end_num = wk_num(TEST_START), wk_num(TEST_END)

    frames = []
    for period in warmup_periods:
        frames.append(baseline_pol.assign(period=period)[["period"] + POLICY_COLS])
    print(f"Warm-up (identical to actual arm): {len(warmup_periods)} weeks "
          f"({WARMUP_START}..{WARMUP_END}) -- no forecast/actual data exists before 2025-W01.")

    for wn in range(test_start_num, test_end_num + 1):
        as_of_num = wn - 1
        target_period = period_index.loc[period_index.week_num == wn, "period"].iloc[0]
        as_of_period = period_index.loc[period_index.week_num == as_of_num, "period"].iloc[0]
        policy = csv2.solve(dfc, as_of_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
                             cost, active_rdc, active_cdc, cdc_primary_rdc, wn, alpha, rng, n_sims=N_SIMS)
        rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                                        active_rdc, wn)
        pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
        if len(pdc_policy):
            policy = pd.concat([policy, pdc_policy], ignore_index=True)
        policy["period"] = target_period
        frames.append(policy[["period"] + POLICY_COLS])
        print(f"  walk-forward as-of {as_of_period} -> target {target_period}: {len(policy):,} lines")

    rolling = pd.concat(frames, ignore_index=True)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    rolling.to_csv(out_path, index=False)
    print(f"\nWrote {len(rolling):,} (period, dc, sku) policy rows to {out_path}")
    return out_path


def run_twin(run_name, policy_csv, seed=SEED):
    out_dir = os.path.join(OUT_DIR, run_name)
    cmd = [sys.executable, TWIN, "--mode", "history", "--horizon-weeks", str(HISTORY_WEEKS),
           "--run-name", run_name, "--policy", policy_csv, "--seed", str(seed),
           "--data-dir", DEFAULT_DATA, "--out-root", OUT_DIR, "--label", run_name]
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    with open(os.path.join(out_dir, "run_kpis.json")) as f:
        kpi = json.load(f)
    inv = pd.read_csv(os.path.join(out_dir, "inventory.csv"))
    return kpi, inv


def load_cached_actual():
    kpi_path = os.path.join(CACHED_ACTUAL_DIR, "run_kpis.json")
    inv_path = os.path.join(CACHED_ACTUAL_DIR, "inventory.csv")
    if not (os.path.exists(kpi_path) and os.path.exists(inv_path)):
        raise FileNotFoundError(
            f"No cached 'actual' arm at {CACHED_ACTUAL_DIR} -- run "
            f"../backtest_rolling_real.py first (it produces the byte-identical "
            f"baseline this script reuses instead of re-running).")
    with open(kpi_path) as f:
        kpi = json.load(f)
    inv = pd.read_csv(inv_path)
    return kpi, inv


def fill_rate_kpis(inv, label):
    dc = inv[inv.dc_id.str.startswith("CDC")]
    rc = inv[inv.dc_id.str.startswith("RDC")]
    cd, cu = inv.demand_customer_qty.sum(), inv.unmet_customer_qty.sum()
    rd, ru = inv.demand_replen_qty.sum(), inv.unmet_replen_qty.sum()
    dw = inv[inv.demand_customer_qty > 0]
    return {
        "arm": label,
        "weeks": int(inv.period.nunique()),
        "customer_demand_units": float(cd),
        "customer_fill_rate": round(float(1 - cu / cd), 4) if cd else None,
        "customer_fill_rate_cdc": round(float(1 - dc.unmet_customer_qty.sum() / dc.demand_customer_qty.sum()), 4)
        if dc.demand_customer_qty.sum() else None,
        "customer_fill_rate_rdc": round(float(1 - rc.unmet_customer_qty.sum() / rc.demand_customer_qty.sum()), 4)
        if rc.demand_customer_qty.sum() else None,
        "pct_demand_weeks_in_full": round(float((dw.unmet_customer_qty <= 0).mean()), 4) if len(dw) else None,
        "internal_replen_fill_rate": round(float(1 - ru / rd), 4) if rd else None,
        "pct_weeks_at_zero_stock": round(float((inv.closing_stock <= 0).mean()), 4),
        "holding_cost_usd_window": round(float(inv.holding_cost_usd.sum()), 2),
    }


def warmup_parity_check(inv_actual, inv_cs2):
    a = inv_actual[inv_actual.period <= WARMUP_END]
    c = inv_cs2[inv_cs2.period <= WARMUP_END]
    cols = ["demand_customer_qty", "unmet_customer_qty", "avg_on_hand", "holding_cost_usd"]
    a_tot, c_tot = a[cols].sum(), c[cols].sum()
    diff = (a_tot - c_tot).abs()
    ok = bool((diff < 1e-6).all())
    return ok, a_tot, c_tot


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    print(f"=== 1. Building the Clark-Scarf v2 walk-forward policy (alpha={ALPHA}, "
          f"{TEST_START}..{TEST_END}, no lookahead) ===")
    cs2_policy_csv = build_walkforward_policy(
        DEFAULT_DATA, os.path.join(OUT_DIR, "policy_clark_scarf_v2_weekly_156wk.csv"))

    print("\n=== 2. Loading the cached 'actual' arm (from ../backtest_rolling_real.py) ===")
    actual_kpi, inv_actual = load_cached_actual()

    print("\n=== 3. Running digital twin: 'clark_scarf_v2_weekly' arm (walk-forward, all 156 weeks) ===")
    cs2_kpi, inv_cs2 = run_twin("clark_scarf_v2_weekly", cs2_policy_csv, seed=args.seed)

    print("\n=== 4. Verifying warm-up parity (2023-W01..2025-W01 must be byte-identical) ===")
    ok, a_tot, c_tot = warmup_parity_check(inv_actual, inv_cs2)
    print(f"  identical through warm-up: {ok}")
    print(f"  actual        : {a_tot.to_dict()}")
    print(f"  clark_scarf_v2: {c_tot.to_dict()}")
    if not ok:
        print("  WARNING: warm-up arms diverged -- the full-run cost delta below is NOT "
              "safely attributable to the test window alone. Investigate before trusting it.")

    print("\n=== 5. Scoring the real 2025-W02..2025-W52 test window only ===")
    test_actual = inv_actual[(inv_actual.period >= TEST_START) & (inv_actual.period <= TEST_END)]
    test_cs2 = inv_cs2[(inv_cs2.period >= TEST_START) & (inv_cs2.period <= TEST_END)]
    fr_actual = fill_rate_kpis(test_actual, "actual")
    fr_cs2 = fill_rate_kpis(test_cs2, "clark_scarf_v2_weekly")
    fr_summary = pd.DataFrame([fr_actual, fr_cs2])
    print(fr_summary.to_string(index=False))

    cost_keys = ["holding_cost_usd", "stockout_cost_usd", "transport_cost_usd", "total_landed_cost_usd"]
    cost_rows = []
    for k in cost_keys:
        b, m = actual_kpi.get(k), cs2_kpi.get(k)
        cost_rows.append({"metric": k, "actual_full156wk": b, "clark_scarf_v2_full156wk": m,
                           "delta_attributable_to_test_window": (m - b) if (m is not None and b is not None) else None})
    cost_summary = pd.DataFrame(cost_rows)
    print("\n" + cost_summary.to_string(index=False))

    fr_summary.to_csv(os.path.join(SCRIPT_DIR, "backtest_rolling_real_clark_scarf_summary.csv"), index=False)
    cost_summary.to_csv(os.path.join(SCRIPT_DIR, "backtest_rolling_real_clark_scarf_cost_summary.csv"), index=False)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    fr_keys = ["customer_fill_rate", "customer_fill_rate_cdc", "customer_fill_rate_rdc",
               "internal_replen_fill_rate"]
    x = np.arange(len(fr_keys))
    axes[0].bar(x - 0.2, [fr_actual[k] * 100 if fr_actual[k] is not None else 0 for k in fr_keys],
                width=0.4, label="Actual (as-generated)", color="#9aa5b1")
    axes[0].bar(x + 0.2, [fr_cs2[k] * 100 if fr_cs2[k] is not None else 0 for k in fr_keys],
                width=0.4, label=f"Clark-Scarf v2 (α={ALPHA}, walk-forward)", color="#c0392b")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(["Customer", "CDC", "RDC direct", "Internal replen"], rotation=15)
    axes[0].set_ylabel("Fill rate (%)")
    axes[0].set_title("Real 2025-W02..W52 demand: Fill Rate")
    axes[0].legend()

    cs = cost_summary.set_index("metric")
    x2 = np.arange(len(cost_keys))
    axes[1].bar(x2 - 0.2, cs.actual_full156wk / 1e6, width=0.4, label="Actual", color="#9aa5b1")
    axes[1].bar(x2 + 0.2, cs.clark_scarf_v2_full156wk / 1e6, width=0.4, label="Clark-Scarf v2", color="#c0392b")
    axes[1].set_xticks(x2)
    axes[1].set_xticklabels(["Holding", "Stockout", "Transport", "Total"], rotation=15)
    axes[1].set_ylabel("$ millions (full 156-week run; delta = test-window effect)")
    axes[1].set_title("Cost (full-run totals, arms differ only in test window)")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_backtest_rolling_real_clark_scarf.png"), dpi=150)
    plt.close(fig)

    fr_delta = (fr_cs2["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100
    total_delta = cost_summary.loc[cost_summary.metric == "total_landed_cost_usd",
                                    "delta_attributable_to_test_window"].iloc[0]
    verdict = ("Clark-Scarf v2 walk-forward wins on real-demand fill rate" if fr_delta > 0
               else "Actual policy wins on real-demand fill rate")
    cost_note = "at LOWER cost too" if total_delta < 0 else "at higher cost"

    report = f"""# Clark-Scarf v2 (alpha={ALPHA}) — Extended Real-Data Walk-Forward Backtest
Supply Brain capstone | generated by `backtest_rolling_real_clark_scarf.py`

## What this is
The Clark-Scarf v2 counterpart to `../backtest_rolling_real.py`'s MEIO
Weekly result. Same design: `orders.csv` replayed via `digital_twin.py
--mode history` for {TEST_START}..{TEST_END} ({fr_cs2['weeks']} real weeks),
policy recomputed fresh every week from only real data available as of that
week (as-of = target week - 1, no lookahead) -- not the synthetic
forecast-error-resampled 2026 stream every other Clark-Scarf v2 number in
this project (including the 93.88%/+2.63pp headline) was scored against.

**Alpha = {ALPHA}**, not re-searched here: this is the alpha
`clark_scarf_v2_rolling.py`'s own grid search picked as lowest-cost on the
synthetic 2026 basis, and the one the 5-seed robustness check
(`clark_scarf_v2_robustness.csv`, mean +2.63pp fill vs. baseline, 5/5 seeds)
validated. Re-optimizing alpha against this same real 2025 data would defeat
the purpose of a held-out check.

Window boundary and warm-up design are identical to the MEIO Weekly version
-- see `../backtest_rolling_real.py`'s docstring. The "actual" arm is the
SAME cached run that script produced (byte-identical inputs), reused here
rather than re-run.

## Warm-up parity check (verified, not assumed)
**{"MATCH" if ok else "MISMATCH -- see warning above, do not trust the cost delta below"}**
through {WARMUP_END}, which licenses reading the full-156-week cost delta as
the test window's effect alone.

## Result: real-demand fill rate, {TEST_START}..{TEST_END} only
{df_to_md(fr_summary.round(4))}

**{verdict}.**

## Result: cost (full-156-week totals; delta = test-window effect, see above)
{df_to_md(cost_summary.round(2))}

Total landed cost delta attributable to the test window: **${total_delta:+,.0f}** ({cost_note}).

## Files
- `backtest_rolling_real_output/policy_clark_scarf_v2_weekly_156wk.csv` (gitignored)
- `backtest_rolling_real_output/clark_scarf_v2_weekly/` (gitignored; full twin run detail)
- `backtest_rolling_real_clark_scarf_summary.csv`, `backtest_rolling_real_clark_scarf_cost_summary.csv`,
  `chart_backtest_rolling_real_clark_scarf.png`
"""
    with open(os.path.join(SCRIPT_DIR, "backtest_rolling_real_clark_scarf_report.md"), "w") as f:
        f.write(report)
    print(f"\n{verdict}, {cost_note}.")
    print("Wrote backtest_rolling_real_clark_scarf_summary.csv, "
          "backtest_rolling_real_clark_scarf_cost_summary.csv, "
          "chart_backtest_rolling_real_clark_scarf.png, backtest_rolling_real_clark_scarf_report.md")


if __name__ == "__main__":
    main()
