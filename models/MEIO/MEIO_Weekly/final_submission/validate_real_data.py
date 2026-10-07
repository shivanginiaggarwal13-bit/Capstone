"""
Clark-Scarf v2 — Real-Data Validation Suite
===============================================================================

Every number elsewhere in this project's synthetic-basis work is scored
against resampled forecast-error noise, not real demand. This suite re-tests
against ACTUAL replayed 2025 orders (`orders.csv`, via `digital_twin.py
--mode history`), with the policy genuinely refit every week from only real
data available as of that week -- no lookahead.

Why the window is 2025 only, not 2023-2025: `demand_forecast.csv` -- the
source of every trailing bias/CV statistic the estimator uses -- only has
rows for 2025-2026, even though real orders go back to 2023. 2023-W01
through 2025-W01 (105 weeks) is identical, unavoidable warm-up in every
arm; the genuinely refit test window is 2025-W02 through 2025-W52 (51
weeks). Warm-up parity (byte-identical demand/cost through the warm-up) is
verified before trusting any comparison below.

Stages (run via --stage, or all in order if omitted):
  actual       Build the "actual" (as-generated) baseline arm. Every other
               stage depends on this being run first (cached after).
  meio_weekly  MEIO Weekly walk-forward vs actual -- context for why
               Clark-Scarf v2 was chosen over the hand-tuned tool (the
               synthetic-basis win nearly vanishes on real demand: +0.15pp).
  headline     Clark-Scarf v2 at alpha=0.2 walk-forward vs actual -- THE
               validated real-data result (+1.19pp fill, holds up where
               MEIO Weekly didn't, at a +2.3% cost and -11.22pp internal
               replenishment fill tradeoff -- see README).
  alpha_sweep  Clark-Scarf v2 swept across alpha 0.0-1.0 on real demand.
               alpha=0.2 (picked from synthetic data) is second-worst here;
               fill peaks near alpha=0.6, cost is lowest near alpha=0.4.
  multiseed    5-seed robustness check of alpha=0.4 and alpha=0.6 (both
               hold up 5/5 seeds -- this is what upgrades the sweep from
               "promising" to "validated, modulo one real year").
  segments     Fast-Moving/Seasonal/Intermediate breakdown of the headline
               result -- the sku_master.csv 3-way label, NOT the
               segmentation deliverable's clusters (see segment_alpha_explore.py
               for that).

    python validate_real_data.py                  # run every stage in order
    python validate_real_data.py --stage headline  # just one stage
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
import meio_weekly_lib as lib
import clark_scarf_v2 as csv2

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
DEFAULT_DATA = os.path.normpath(os.path.join(REPO_ROOT, "input datasets"))
BASELINE_POLICY_CSV = os.path.join(DEFAULT_DATA, "baseline_policy.csv")
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")
RUN_OUT_DIR = os.path.join(SCRIPT_DIR, "validation_output")   # gitignored; full twin run detail
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")              # repo-tracked summaries/charts/reports
STATE_CSV = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")

WARMUP_START = "2023-W01"
WARMUP_END = "2025-W01"
TEST_START = "2025-W02"
TEST_END = "2025-W52"
HISTORY_WEEKS = 156
HEADLINE_ALPHA = 0.2
SEED = 2026
MULTISEED_SEEDS = (2026, 7, 42, 123, 2027)
SWEEP_ALPHAS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

POLICY_COLS = ["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]


def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def _all_periods(data_dir):
    return sorted(pd.read_csv(os.path.join(data_dir, "orders.csv"), usecols=["period"]).period.unique())


def run_twin_history(run_name, policy_csv, seed=SEED):
    """Run the twin in --mode history over the full 156-week horizon
    (105-week warm-up + 51-week real test window)."""
    out_dir = os.path.join(RUN_OUT_DIR, run_name)
    cmd = [sys.executable, TWIN, "--mode", "history", "--horizon-weeks", str(HISTORY_WEEKS),
           "--run-name", run_name, "--policy", policy_csv, "--seed", str(seed),
           "--data-dir", DEFAULT_DATA, "--out-root", RUN_OUT_DIR, "--label", run_name]
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
        "arm": label, "weeks": int(inv.period.nunique()),
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


def warmup_parity_check(inv_a, inv_b):
    a = inv_a[inv_a.period <= WARMUP_END]
    b = inv_b[inv_b.period <= WARMUP_END]
    cols = ["demand_customer_qty", "unmet_customer_qty", "avg_on_hand", "holding_cost_usd"]
    diff = (a[cols].sum() - b[cols].sum()).abs()
    return bool((diff < 1e-6).all())


# --------------------------------------------------------------------- MEIO Weekly walk-forward policy
def build_walkforward_policy_meio(data_dir, out_path):
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)
    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)

    baseline_pol = pd.read_csv(BASELINE_POLICY_CSV)[POLICY_COLS]
    warmup_periods = [p for p in _all_periods(data_dir) if WARMUP_START <= p <= WARMUP_END]

    def wk_num(period):
        return int(period_index.loc[period_index.period == period, "week_num"].iloc[0])

    test_start_num, test_end_num = wk_num(TEST_START), wk_num(TEST_END)
    frames = [baseline_pol.assign(period=p)[["period"] + POLICY_COLS] for p in warmup_periods]
    print(f"[meio_weekly] warm-up: {len(warmup_periods)} weeks ({WARMUP_START}..{WARMUP_END})")

    for wn in range(test_start_num, test_end_num + 1):
        as_of_num = wn - 1
        target_period = period_index.loc[period_index.week_num == wn, "period"].iloc[0]
        demand = lib.target_week_demand(dfc, as_of_num, period_index, seasonal_index, sku_subcat)
        policy = lib.solve_policy(demand, leadtime_by_dc, cost, active_rdc, active_cdc, cdc_primary_rdc)
        rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                                        active_rdc, wn)
        pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
        if len(pdc_policy):
            policy = pd.concat([policy, pdc_policy], ignore_index=True)
        policy["period"] = target_period
        frames.append(policy[["period"] + POLICY_COLS])

    rolling = pd.concat(frames, ignore_index=True)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    rolling.to_csv(out_path, index=False)
    print(f"[meio_weekly] wrote {len(rolling):,} rows -> {out_path}")
    return out_path


# --------------------------------------------------------- Clark-Scarf v2 walk-forward policy
def build_walkforward_policy_csv2(data_dir, out_path, alpha=HEADLINE_ALPHA, rng=None, alpha_override=None):
    """alpha_override: optional {sku_id: alpha} dict for a per-segment sweep
    (see segment_alpha_explore.py); leave None for the single-alpha case."""
    rng = rng if rng is not None else np.random.default_rng(SEED)
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)
    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)

    baseline_pol = pd.read_csv(BASELINE_POLICY_CSV)[POLICY_COLS]
    warmup_periods = [p for p in _all_periods(data_dir) if WARMUP_START <= p <= WARMUP_END]

    def wk_num(period):
        return int(period_index.loc[period_index.period == period, "week_num"].iloc[0])

    test_start_num, test_end_num = wk_num(TEST_START), wk_num(TEST_END)
    frames = [baseline_pol.assign(period=p)[["period"] + POLICY_COLS] for p in warmup_periods]

    csv2.ALPHA_OVERRIDE_BY_SKU.clear()
    if alpha_override:
        csv2.ALPHA_OVERRIDE_BY_SKU.update(alpha_override)
    try:
        for wn in range(test_start_num, test_end_num + 1):
            as_of_num = wn - 1
            target_period = period_index.loc[period_index.week_num == wn, "period"].iloc[0]
            policy = csv2.solve(dfc, as_of_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
                                 cost, active_rdc, active_cdc, cdc_primary_rdc, wn, alpha, rng, n_sims=1200)
            rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                                            active_rdc, wn)
            pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
            if len(pdc_policy):
                policy = pd.concat([policy, pdc_policy], ignore_index=True)
            policy["period"] = target_period
            frames.append(policy[["period"] + POLICY_COLS])
    finally:
        csv2.ALPHA_OVERRIDE_BY_SKU.clear()

    rolling = pd.concat(frames, ignore_index=True)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    rolling.to_csv(out_path, index=False)
    print(f"[clark_scarf_v2 alpha={alpha}] wrote {len(rolling):,} rows -> {out_path}")
    return out_path


# --------------------------------------------------------------------------------- stages
def stage_actual(seed=SEED):
    """Cached after first run -- every other stage reuses this arm."""
    out_dir = os.path.join(RUN_OUT_DIR, "actual")
    if os.path.exists(os.path.join(out_dir, "run_kpis.json")):
        print("[actual] already built, reusing cache")
        with open(os.path.join(out_dir, "run_kpis.json")) as f:
            kpi = json.load(f)
        inv = pd.read_csv(os.path.join(out_dir, "inventory.csv"))
        return kpi, inv
    print("=== Stage: actual (baseline, 156-week real replay) ===")
    return run_twin_history("actual", BASELINE_POLICY_CSV, seed=seed)


def stage_meio_weekly():
    print("=== Stage: meio_weekly ===")
    actual_kpi, inv_actual = stage_actual()
    policy_csv = build_walkforward_policy_meio(DEFAULT_DATA, os.path.join(RUN_OUT_DIR, "policy_meio_weekly.csv"))
    kpi, inv = run_twin_history("meio_weekly", policy_csv)
    ok = warmup_parity_check(inv_actual, inv)
    print(f"  warm-up parity: {ok}")

    test_actual = inv_actual[(inv_actual.period >= TEST_START) & (inv_actual.period <= TEST_END)]
    test_meio = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)]
    summary = pd.DataFrame([fill_rate_kpis(test_actual, "actual"), fill_rate_kpis(test_meio, "meio_weekly")])
    summary.to_csv(os.path.join(RESULTS_DIR, "meio_weekly_vs_actual.csv"), index=False)
    print(summary.to_string(index=False))
    return summary


def stage_headline():
    print("=== Stage: headline (Clark-Scarf v2, alpha=0.2, THE validated real-data result) ===")
    actual_kpi, inv_actual = stage_actual()
    policy_csv = build_walkforward_policy_csv2(DEFAULT_DATA, os.path.join(RUN_OUT_DIR, "policy_csv2_headline.csv"),
                                                alpha=HEADLINE_ALPHA)
    kpi, inv = run_twin_history("csv2_headline", policy_csv)
    ok = warmup_parity_check(inv_actual, inv)
    print(f"  warm-up parity: {ok}")

    test_actual = inv_actual[(inv_actual.period >= TEST_START) & (inv_actual.period <= TEST_END)]
    test_csv2 = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)]
    fr_actual, fr_csv2 = fill_rate_kpis(test_actual, "actual"), fill_rate_kpis(test_csv2, "clark_scarf_v2")
    summary = pd.DataFrame([fr_actual, fr_csv2])
    summary.to_csv(os.path.join(RESULTS_DIR, "headline_clark_scarf_v2_vs_actual.csv"), index=False)

    cost_rows = []
    for k in ["holding_cost_usd", "stockout_cost_usd", "transport_cost_usd", "total_landed_cost_usd"]:
        cost_rows.append({"metric": k, "actual": actual_kpi.get(k), "clark_scarf_v2": kpi.get(k),
                           "delta_attributable_to_test_window": kpi.get(k) - actual_kpi.get(k)})
    cost_summary = pd.DataFrame(cost_rows)
    cost_summary.to_csv(os.path.join(RESULTS_DIR, "headline_cost_summary.csv"), index=False)
    print(summary.to_string(index=False))
    print(cost_summary.to_string(index=False))
    return summary, cost_summary


def stage_alpha_sweep():
    print("=== Stage: alpha_sweep (Clark-Scarf v2 across alpha 0.0-1.0, real demand) ===")
    actual_kpi, _ = stage_actual()
    rng = np.random.default_rng(SEED)
    rows = []
    for a in SWEEP_ALPHAS:
        tag = f"a{a:.1f}".replace(".", "")
        print(f"  --- alpha={a:.1f} ---")
        policy_csv = build_walkforward_policy_csv2(
            DEFAULT_DATA, os.path.join(RUN_OUT_DIR, f"policy_sweep_{tag}.csv"), alpha=a, rng=rng)
        kpi, inv = run_twin_history(f"sweep_{tag}", policy_csv)
        test_inv = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)]
        fr = fill_rate_kpis(test_inv, f"alpha={a:.1f}")
        fr["alpha"] = a
        fr["cost_delta_usd"] = kpi["total_landed_cost_usd"] - actual_kpi["total_landed_cost_usd"]
        rows.append(fr)
    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(RESULTS_DIR, "alpha_sweep_real_data.csv"), index=False)
    print(summary.to_string(index=False))
    return summary


def stage_multiseed():
    print("=== Stage: multiseed (5-seed check of alpha=0.4 and alpha=0.6) ===")
    rows = []
    for alpha in (0.4, 0.6):
        for seed in MULTISEED_SEEDS:
            print(f"  --- alpha={alpha} seed={seed} ---")
            rng = np.random.default_rng(seed)
            tag = f"a{alpha:.1f}_s{seed}".replace(".", "")
            actual_run = f"ms_actual_s{seed}"
            actual_out = os.path.join(RUN_OUT_DIR, actual_run)
            if os.path.exists(os.path.join(actual_out, "run_kpis.json")):
                with open(os.path.join(actual_out, "run_kpis.json")) as f:
                    actual_kpi = json.load(f)
                inv_actual = pd.read_csv(os.path.join(actual_out, "inventory.csv"))
            else:
                actual_kpi, inv_actual = run_twin_history(actual_run, BASELINE_POLICY_CSV, seed=seed)

            policy_csv = build_walkforward_policy_csv2(
                DEFAULT_DATA, os.path.join(RUN_OUT_DIR, f"policy_ms_{tag}.csv"), alpha=alpha, rng=rng)
            kpi, inv = run_twin_history(f"ms_{tag}", policy_csv, seed=seed)

            test_actual = inv_actual[(inv_actual.period >= TEST_START) & (inv_actual.period <= TEST_END)]
            test_csv2 = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)]
            fr_actual = fill_rate_kpis(test_actual, "actual")
            fr_csv2 = fill_rate_kpis(test_csv2, "csv2")
            rows.append({
                "alpha": alpha, "seed": seed,
                "actual_fill": fr_actual["customer_fill_rate"], "csv2_fill": fr_csv2["customer_fill_rate"],
                "fill_delta_pp": (fr_csv2["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100,
                "cost_delta_usd": kpi["total_landed_cost_usd"] - actual_kpi["total_landed_cost_usd"],
            })
    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(RESULTS_DIR, "multiseed_alpha_04_06.csv"), index=False)
    agg = summary.groupby("alpha").agg(
        n_wins=("fill_delta_pp", lambda s: (s > 0).sum()), n_seeds=("fill_delta_pp", "size"),
        fill_delta_mean=("fill_delta_pp", "mean"), fill_delta_std=("fill_delta_pp", "std"),
        cost_delta_mean=("cost_delta_usd", "mean"), cost_delta_std=("cost_delta_usd", "std"))
    print(agg.to_string())
    return summary


def stage_segments():
    print("=== Stage: segments (Fast-Moving/Seasonal/Intermediate breakdown of the headline result) ===")
    actual_kpi, inv_actual = stage_actual()
    headline_dir = os.path.join(RUN_OUT_DIR, "csv2_headline")
    if not os.path.exists(os.path.join(headline_dir, "inventory.csv")):
        print("  headline stage hasn't run yet -- running it now")
        stage_headline()
    inv_csv2 = pd.read_csv(os.path.join(headline_dir, "inventory.csv"))

    seg_map = pd.read_csv(os.path.join(DEFAULT_DATA, "sku_master.csv"), usecols=["sku_id", "segment"])
    seg_map = dict(zip(seg_map.sku_id, seg_map.segment))

    rows = []
    for label, inv in [("actual", inv_actual), ("clark_scarf_v2", inv_csv2)]:
        t = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)].copy()
        t["segment"] = t.sku_id.map(seg_map)
        for seg, g in t.groupby("segment"):
            cd, cu = g.demand_customer_qty.sum(), g.unmet_customer_qty.sum()
            rows.append({"run": label, "segment": seg,
                         "customer_fill_rate": round(1 - cu / cd, 4) if cd else None})
    summary = pd.DataFrame(rows)
    piv = summary.pivot(index="run", columns="segment", values="customer_fill_rate")
    piv.to_csv(os.path.join(RESULTS_DIR, "segment_breakdown_real_data.csv"))
    print(piv.to_string())
    return piv


STAGES = {
    "actual": stage_actual, "meio_weekly": stage_meio_weekly, "headline": stage_headline,
    "alpha_sweep": stage_alpha_sweep, "multiseed": stage_multiseed, "segments": stage_segments,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=list(STAGES), default=None,
                     help="Run just one stage; omit to run all in order.")
    args = ap.parse_args()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    if args.stage:
        STAGES[args.stage]()
    else:
        for name, fn in STAGES.items():
            fn()


if __name__ == "__main__":
    main()
