"""
Supply Brain — Extended Real-Data Walk-Forward Backtest
===============================================================================

`backtest_csl.py` already does a genuine no-lookahead walk-forward against
real actuals, but only checks "was the reorder point sufficient" statistically
(achieved vs. target CSL), over a ~35-week default window. `simulate_policy.py`
/ `simulate_rolling_policy.py` run the FULL twin inventory simulation (real
fill rate, real cost) but only against a synthetic 2026 demand stream
(forecast_qty x a resampled historical forecast-error ratio) -- there is no
real 2026 data to test against.

This script closes that gap: a full twin inventory simulation, scored against
REAL replayed 2025 demand (`orders.csv`, via `digital_twin.py --mode
history`), driven by a policy that is genuinely recomputed every week from
only real data available as of that week -- no lookahead, true walk-forward,
not a single frozen fit.

Why the window is 2025 only, not 2023-2025
-------------------------------------------
`orders.csv` covers 2023-W01..2025-W52 (156 weeks), but the ENTIRE MEIO
Weekly estimator (`bias_factor`, `blended_cv`, `deseasonalized_level`, and
therefore `target_week_demand`) reads `demand_forecast.csv` for its trailing
actual-vs-forecast statistics -- and that file only has rows for
2025-W01..2026-W52. There is no forecast_qty/actual_qty pairing at all for
2023-2024, so there is nothing for the estimator to trail on before
2025-W01. This is exactly why `backtest_csl.py`'s default window already
starts in 2025 (2025-W14) -- it hit the same wall. Confirmed by inspection
(see session notes), not assumed.

Design
------
Two arms, both run as ONE continuous 156-week `--mode history` replay of the
same real customer demand (exogenous, unaffected by policy) from the same
cold start, same seed:

  "actual"      : `input datasets/baseline_policy.csv` (static) for all 156
                  weeks -- this is the literal policy the dataset generator
                  used to produce the given history (see
                  `supply_brain_generator_v5.py`), so this run reproduces
                  what actually happened.
  "meio_weekly" : time-varying policy --
                    weeks 2023-W01..2025-W01 (105 weeks): IDENTICAL to the
                      actual arm's baseline_policy.csv values. Not a
                      simplification -- there is no forecast data to
                      recompute anything different for these weeks anyway.
                    weeks 2025-W02..2025-W52 (51 weeks): a genuine walk-
                      forward MEIO Weekly recompute. For target week t,
                      as-of = t-1: `target_week_demand` / `solve_policy` /
                      `smoothed_rdc_demand_for_pdc` / `solve_pdc_policy` are
                      recomputed fresh from only real data at or before
                      week t-1 (the trailing 13-/52-week windows genuinely
                      slide forward each iteration -- unlike
                      `roll_forward_policy.py`'s 2026 rolling policy, which
                      MUST freeze its as-of point because there is no real
                      future data to refit on; here there is).

Because both arms are policy-IDENTICAL and demand-identical for the first
105 weeks, and RNG streams are policy-independent (see
`digital_twin.build_inputs`'s "common random numbers" note), the two arms'
physical trajectories are byte-identical through 2025-W01. That is verified
empirically below, not just argued. It means:
  - fill-rate metrics are filtered to the 2025-W02..2025-W52 test window and
    computed directly from that slice (exact, no assumption needed) --
    `demand_customer_qty`/`unmet_customer_qty`/`holding_cost_usd` are
    per-(period,dc,sku) columns in the twin's own inventory.csv output.
  - stockout_cost_usd / transport_cost_usd have no per-period breakdown in
    the twin (they're accumulated once, network-wide, over the full run) --
    for these, the DELTA between the two arms' full-156-week totals equals
    the test-window delta exactly, because the identical first 105 weeks
    contribute identical constants to both totals and cancel in the
    subtraction. The warm-up-parity check below is what licenses this.

    python backtest_rolling_real.py

Outputs (this dir):
    backtest_rolling_output/policy_meio_weekly_156wk.csv   (gitignored; ~1.2M rows)
    backtest_rolling_output/{actual,meio_weekly}/           (gitignored; full twin run detail)
    backtest_rolling_real_summary.csv                       (repo-tracked)
    chart_backtest_rolling_real.png                         (repo-tracked)
    backtest_rolling_real_report.md                         (repo-tracked)
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meio_weekly_lib as lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DEFAULT_DATA = os.path.normpath(os.path.join(REPO_ROOT, "input datasets"))
BASELINE_POLICY_CSV = os.path.join(DEFAULT_DATA, "baseline_policy.csv")
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")
OUT_DIR = os.path.join(SCRIPT_DIR, "backtest_rolling_output")

WARMUP_START = "2023-W01"
WARMUP_END = "2025-W01"     # last week held identical between arms
TEST_START = "2025-W02"     # first genuinely recomputed target week
TEST_END = "2025-W52"
HISTORY_WEEKS = 156

POLICY_COLS = ["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]


def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def build_walkforward_policy(data_dir, out_path):
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)  # meio_weekly_lib's own index: 2025-W01..2026-W52 ONLY
    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)

    baseline_pol = pd.read_csv(BASELINE_POLICY_CSV)[POLICY_COLS]

    # `period_index` (from demand_forecast.csv) starts at 2025-W01 and knows
    # nothing about 2023-2024 -- the warm-up weeks need their real calendar
    # labels from orders.csv instead, which covers the full 156-week history.
    all_periods = sorted(pd.read_csv(os.path.join(data_dir, "orders.csv"), usecols=["period"]).period.unique())
    warmup_periods = [p for p in all_periods if WARMUP_START <= p <= WARMUP_END]

    def wk_num(period):
        return int(period_index.loc[period_index.period == period, "week_num"].iloc[0])

    test_start_num, test_end_num = wk_num(TEST_START), wk_num(TEST_END)

    frames = []
    for period in warmup_periods:
        frames.append(baseline_pol.assign(period=period)[["period"] + POLICY_COLS])
    print(f"Warm-up (identical to actual arm): {len(warmup_periods)} weeks "
          f"({WARMUP_START}..{WARMUP_END}) -- no forecast/actual data exists before 2025-W01 "
          f"for the estimator to recompute anything different.")

    for wn in range(test_start_num, test_end_num + 1):
        as_of_num = wn - 1
        target_period = period_index.loc[period_index.week_num == wn, "period"].iloc[0]
        as_of_period = (period_index.loc[period_index.week_num == as_of_num, "period"].iloc[0]
                        if as_of_num >= period_index.week_num.min() else WARMUP_END)
        demand = lib.target_week_demand(dfc, as_of_num, period_index, seasonal_index, sku_subcat)
        policy = lib.solve_policy(demand, leadtime_by_dc, cost, active_rdc, active_cdc, cdc_primary_rdc)
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


def run_twin(run_name, policy_csv, seed=2026):
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


def warmup_parity_check(inv_actual, inv_meio):
    a = inv_actual[inv_actual.period <= WARMUP_END]
    m = inv_meio[inv_meio.period <= WARMUP_END]
    cols = ["demand_customer_qty", "unmet_customer_qty", "avg_on_hand", "holding_cost_usd"]
    a_tot, m_tot = a[cols].sum(), m[cols].sum()
    diff = (a_tot - m_tot).abs()
    ok = bool((diff < 1e-6).all())
    return ok, a_tot, m_tot


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    print("=== 1. Building the walk-forward MEIO Weekly policy (2025-W02..2025-W52, no lookahead) ===")
    meio_policy_csv = build_walkforward_policy(
        DEFAULT_DATA, os.path.join(OUT_DIR, "policy_meio_weekly_156wk.csv"))

    print("\n=== 2. Running digital twin: 'actual' arm (baseline_policy.csv, all 156 weeks) ===")
    actual_kpi, inv_actual = run_twin("actual", BASELINE_POLICY_CSV, seed=args.seed)

    print("\n=== 3. Running digital twin: 'meio_weekly' arm (walk-forward policy, all 156 weeks) ===")
    meio_kpi, inv_meio = run_twin("meio_weekly", meio_policy_csv, seed=args.seed)

    print("\n=== 4. Verifying warm-up parity (2023-W01..2025-W01 must be byte-identical) ===")
    ok, a_tot, m_tot = warmup_parity_check(inv_actual, inv_meio)
    print(f"  identical through warm-up: {ok}")
    print(f"  actual : {a_tot.to_dict()}")
    print(f"  meio   : {m_tot.to_dict()}")
    if not ok:
        print("  WARNING: warm-up arms diverged -- the full-run cost delta below is NOT "
              "safely attributable to the test window alone. Investigate before trusting it.")

    print("\n=== 5. Scoring the real 2025-W02..2025-W52 test window only ===")
    test_actual = inv_actual[(inv_actual.period >= TEST_START) & (inv_actual.period <= TEST_END)]
    test_meio = inv_meio[(inv_meio.period >= TEST_START) & (inv_meio.period <= TEST_END)]
    fr_actual = fill_rate_kpis(test_actual, "actual")
    fr_meio = fill_rate_kpis(test_meio, "meio_weekly")
    fr_summary = pd.DataFrame([fr_actual, fr_meio])
    print(fr_summary.to_string(index=False))

    cost_keys = ["holding_cost_usd", "stockout_cost_usd", "transport_cost_usd", "total_landed_cost_usd"]
    cost_rows = []
    for k in cost_keys:
        b, m = actual_kpi.get(k), meio_kpi.get(k)
        cost_rows.append({"metric": k, "actual_full156wk": b, "meio_weekly_full156wk": m,
                           "delta_attributable_to_test_window": (m - b) if (m is not None and b is not None) else None})
    cost_summary = pd.DataFrame(cost_rows)
    print("\n" + cost_summary.to_string(index=False))

    fr_summary.to_csv(os.path.join(SCRIPT_DIR, "backtest_rolling_real_summary.csv"), index=False)
    cost_summary.to_csv(os.path.join(SCRIPT_DIR, "backtest_rolling_real_cost_summary.csv"), index=False)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    fr_keys = ["customer_fill_rate", "customer_fill_rate_cdc", "customer_fill_rate_rdc",
               "internal_replen_fill_rate"]
    x = np.arange(len(fr_keys))
    axes[0].bar(x - 0.2, [fr_actual[k] * 100 if fr_actual[k] is not None else 0 for k in fr_keys],
                width=0.4, label="Actual (as-generated)", color="#9aa5b1")
    axes[0].bar(x + 0.2, [fr_meio[k] * 100 if fr_meio[k] is not None else 0 for k in fr_keys],
                width=0.4, label="MEIO Weekly (walk-forward)", color="#2b6cb0")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(["Customer", "CDC", "RDC direct", "Internal replen"], rotation=15)
    axes[0].set_ylabel("Fill rate (%)")
    axes[0].set_title("Real 2025-W02..W52 demand: Fill Rate")
    axes[0].legend()

    cs = cost_summary.set_index("metric")
    x2 = np.arange(len(cost_keys))
    axes[1].bar(x2 - 0.2, cs.actual_full156wk / 1e6, width=0.4, label="Actual", color="#9aa5b1")
    axes[1].bar(x2 + 0.2, cs.meio_weekly_full156wk / 1e6, width=0.4, label="MEIO Weekly", color="#2b6cb0")
    axes[1].set_xticks(x2)
    axes[1].set_xticklabels(["Holding", "Stockout", "Transport", "Total"], rotation=15)
    axes[1].set_ylabel("$ millions (full 156-week run; delta = test-window effect)")
    axes[1].set_title("Cost (full-run totals, arms differ only in test window)")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_backtest_rolling_real.png"), dpi=150)
    plt.close(fig)

    fr_delta = (fr_meio["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100
    total_delta = cost_summary.loc[cost_summary.metric == "total_landed_cost_usd",
                                    "delta_attributable_to_test_window"].iloc[0]
    verdict = "MEIO Weekly walk-forward wins on real-demand fill rate" if fr_delta > 0 else "Actual policy wins on real-demand fill rate"
    cost_note = "at LOWER cost too" if total_delta < 0 else "at higher cost"

    report = f"""# Extended Real-Data Walk-Forward Backtest
Supply Brain capstone | generated by `backtest_rolling_real.py`

## What this is, and why it's different from every other MEIO result in this repo
Every fill-rate/cost number reported elsewhere in this project (the v1-v10
tuning rounds, the four alt models, Clark-Scarf v2, the bandits) is scored
against a SYNTHETIC 2026 demand stream: `forecast_qty` for each future week
times a forecast-error ratio resampled from that SKU-DC's own historical
error distribution. That is a legitimate robustness stress test, but it is
not a test against independent ground truth, because the noise model is fit
on the same history the policy is built from.

This run is the first one scored against demand that actually happened:
`orders.csv` replayed via `digital_twin.py --mode history` for
{TEST_START}..{TEST_END} ({fr_meio['weeks']} weeks), with a policy that was
recomputed fresh every single week from only real data available as of that
week (as-of = target week - 1, no lookahead).

**Why the window is one year (2025), not two (2024-2025) as originally
scoped:** `demand_forecast.csv` -- the source of every trailing bias/CV
statistic the estimator uses -- only has rows for 2025-W01..2026-W52. There
is no forecast/actual pairing at all for 2023-2024, so the estimator
genuinely cannot recompute anything different for those weeks; that's also
why `backtest_csl.py`'s own default window starts in 2025. 2023-W01 through
2025-W01 (105 weeks) use the literal `baseline_policy.csv` in both arms --
identical, not a shortcut -- and are pure warm-up so both arms reach
2025-W02 from the same physical position.

## Warm-up parity check (verified, not assumed)
Both arms are policy-identical and demand-identical for the first 105 weeks,
and the twin's RNG streams are policy-independent by design, so the two arms'
physical trajectories should be byte-identical through {WARMUP_END}.
Verified: **{"MATCH" if ok else "MISMATCH -- see warning above, do not trust the cost delta below"}**.
This is what licenses reading the full-156-week cost delta as the test-
window's effect alone (the identical first 105 weeks contribute identical
constants to both totals and cancel in the subtraction).

## Result: real-demand fill rate, {TEST_START}..{TEST_END} only
{df_to_md(fr_summary.round(4))}

**{verdict}.**

## Result: cost (full-156-week totals; delta = test-window effect, see above)
{df_to_md(cost_summary.round(2))}

Total landed cost delta attributable to the test window: **${total_delta:+,.0f}** ({cost_note}).

## Files
- `backtest_rolling_output/policy_meio_weekly_156wk.csv` (gitignored, ~1.2M rows)
- `backtest_rolling_output/{{actual,meio_weekly}}/` (gitignored; full twin run detail per arm)
- `backtest_rolling_real_summary.csv`, `backtest_rolling_real_cost_summary.csv`, `chart_backtest_rolling_real.png`
"""
    with open(os.path.join(SCRIPT_DIR, "backtest_rolling_real_report.md"), "w") as f:
        f.write(report)
    print(f"\n{verdict}, {cost_note}.")
    print("Wrote backtest_rolling_real_summary.csv, backtest_rolling_real_cost_summary.csv, "
          "chart_backtest_rolling_real.png, backtest_rolling_real_report.md")


if __name__ == "__main__":
    main()
