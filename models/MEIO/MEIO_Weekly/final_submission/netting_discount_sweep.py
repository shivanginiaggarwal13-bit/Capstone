"""
Clark-Scarf v2 — Netting-Discount Fix, Real-Data Test
===============================================================================

Tests the `netting_discount` parameter added to `clark_scarf_v2.py::solve`
-- see its module docstring and this folder's README "Known limitations:
internal replenishment fill" for the problem this is trying to fix.

The pooled RDC endpoint's safety stock is
    max(0, pooled_SS - netting_discount * downstream_CDC_SS)
The original model always used netting_discount=1.0 (net 100% of what the
CDCs already hold, assuming stock can move to wherever it's needed on no
notice). This sweep holds alpha fixed at the project's two reference points
-- 0.2 (headline, picked on the synthetic basis) and 0.6 (best real-data
customer fill rate from validate_real_data.py's alpha_sweep stage) -- and
varies netting_discount, to see whether discounting the netting recovers
internal replenishment fill without giving up the customer-fill win.

netting_discount=1.0 at each alpha should reproduce validate_real_data.py's
own stage_headline (alpha=0.2) / alpha_sweep alpha=0.6 row almost exactly
(same RNG stream position differences as any other re-run) -- a built-in
regression check.

    python netting_discount_sweep.py                  # both alphas, full sweep
    python netting_discount_sweep.py --alpha 0.2       # just one alpha
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate_real_data as vrd

RESULTS_DIR = vrd.RESULTS_DIR
RUN_OUT_DIR = vrd.RUN_OUT_DIR
TWIN_OUT_DIR = vrd.TWIN_OUT_DIR
TEST_START, TEST_END = vrd.TEST_START, vrd.TEST_END

NETTING_DISCOUNTS = (0.0, 0.25, 0.5, 0.75, 1.0)
TEST_ALPHAS = (0.2, 0.6)


def sweep_one_alpha(alpha):
    print(f"=== Netting-discount sweep at alpha={alpha} ===")
    actual_kpi, _ = vrd.stage_actual()
    rows = []
    for nd in NETTING_DISCOUNTS:
        tag = f"a{alpha:.1f}_nd{nd:.2f}".replace(".", "")
        print(f"  --- netting_discount={nd:.2f} ---")
        # Fresh, IDENTICAL seed per value (not a shared/advancing rng): netting_discount
        # only changes post-hoc arithmetic on the Monte Carlo draws, never the draws
        # themselves, so an identical seed isolates netting_discount as the only
        # varying factor instead of confounding it with RNG-stream-position drift
        # (internal_replen_fill_rate turned out to be far more sensitive to stream
        # position than customer_fill_rate -- a 4pp swing was observed from that alone).
        rng = np.random.default_rng(vrd.SEED)
        policy_csv = vrd.build_walkforward_policy_csv2(
            vrd.DEFAULT_DATA, os.path.join(RUN_OUT_DIR, f"policy_netting_{tag}.csv"),
            alpha=alpha, rng=rng, netting_discount=nd)
        kpi, inv = vrd.run_twin_history(f"netting_{tag}", policy_csv)
        test_inv = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)]
        fr = vrd.fill_rate_kpis(test_inv, f"nd={nd:.2f}")
        fr["alpha"] = alpha
        fr["netting_discount"] = nd
        fr["cost_delta_usd"] = kpi["total_landed_cost_usd"] - actual_kpi["total_landed_cost_usd"]
        rows.append(fr)
    summary = pd.DataFrame(rows)
    out = os.path.join(RESULTS_DIR, "netting_discount_sweep_alpha{:.1f}".format(alpha).replace(".", "_") + ".csv")
    summary.to_csv(out, index=False)
    print(summary.to_string(index=False))
    return summary


def multiseed_check(alpha=vrd.HEADLINE_ALPHA, discounts=(0.0, 1.0), seeds=vrd.MULTISEED_SEEDS):
    """5-seed robustness check of the sweep's best vs. original netting_discount
    (mirrors validate_real_data.py's stage_multiseed, but for netting_discount
    instead of alpha)."""
    print(f"=== Multiseed check: netting_discount in {discounts} at alpha={alpha} ===")
    rows = []
    for nd in discounts:
        for seed in seeds:
            tag = f"nd{nd:.2f}_s{seed}".replace(".", "")
            actual_run = f"ms_actual_s{seed}"
            actual_out = os.path.join(TWIN_OUT_DIR, actual_run)
            if os.path.exists(os.path.join(actual_out, "run_kpis.json")):
                import json
                with open(os.path.join(actual_out, "run_kpis.json")) as f:
                    actual_kpi = json.load(f)
                inv_actual = pd.read_csv(os.path.join(actual_out, "inventory.csv"))
            else:
                actual_kpi, inv_actual = vrd.run_twin_history(actual_run, vrd.BASELINE_POLICY_CSV, seed=seed)

            rng = np.random.default_rng(seed)
            policy_csv = vrd.build_walkforward_policy_csv2(
                vrd.DEFAULT_DATA, os.path.join(RUN_OUT_DIR, f"policy_netting_ms_{tag}.csv"),
                alpha=alpha, rng=rng, netting_discount=nd)
            kpi, inv = vrd.run_twin_history(f"netting_ms_{tag}", policy_csv, seed=seed)

            test_actual = inv_actual[(inv_actual.period >= TEST_START) & (inv_actual.period <= TEST_END)]
            test_csv2 = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)]
            fr_actual = vrd.fill_rate_kpis(test_actual, "actual")
            fr_csv2 = vrd.fill_rate_kpis(test_csv2, "csv2")
            rows.append({
                "netting_discount": nd, "seed": seed,
                "fill_delta_pp": (fr_csv2["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100,
                "internal_replen_fill": fr_csv2["internal_replen_fill_rate"],
                "cost_delta_usd": kpi["total_landed_cost_usd"] - actual_kpi["total_landed_cost_usd"],
            })
    summary = pd.DataFrame(rows)
    out = os.path.join(RESULTS_DIR, "netting_discount_multiseed.csv")
    summary.to_csv(out, index=False)
    agg = summary.groupby("netting_discount").agg(
        n_wins=("fill_delta_pp", lambda s: (s > 0).sum()), n_seeds=("fill_delta_pp", "size"),
        fill_delta_mean=("fill_delta_pp", "mean"), fill_delta_std=("fill_delta_pp", "std"),
        internal_replen_mean=("internal_replen_fill", "mean"), internal_replen_std=("internal_replen_fill", "std"),
        cost_delta_mean=("cost_delta_usd", "mean"), cost_delta_std=("cost_delta_usd", "std"))
    print(agg.to_string())
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alpha", type=float, default=None, choices=list(TEST_ALPHAS),
                     help="Run just one alpha; omit to run both.")
    ap.add_argument("--multiseed", action="store_true",
                     help="Run the 5-seed robustness check of netting_discount in {0.0, 1.0} at alpha=0.2 instead.")
    args = ap.parse_args()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    if args.multiseed:
        multiseed_check()
        return
    alphas = [args.alpha] if args.alpha is not None else list(TEST_ALPHAS)
    for a in alphas:
        sweep_one_alpha(a)


if __name__ == "__main__":
    main()
