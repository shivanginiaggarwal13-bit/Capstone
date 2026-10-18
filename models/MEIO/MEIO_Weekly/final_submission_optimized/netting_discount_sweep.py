"""
Clark-Scarf v2 -- Netting-Discount Fix, Real-Data Test (optimized)
===============================================================================

The same test as final_submission/netting_discount_sweep.py: hold alpha at 0.2 or 0.6
and vary netting_discount (the share of downstream CDC safety stock the pooled RDC
endpoint nets against), on real replayed 2025 demand.

The original built each netting_discount value from a fresh generator at the same seed
so that only netting_discount varied. That also means all five builds consumed exactly
the same Monte Carlo draws -- netting_discount only rescales them afterwards -- so here
the five values are read off one pass of draws. The policy files are identical; the
twin runs then go to the shared pool in parallel.

    python netting_discount_sweep.py                   # both alphas, full sweep
    python netting_discount_sweep.py --alpha 0.2       # just one alpha
    python netting_discount_sweep.py --multiseed --alpha 0.6
"""
import argparse
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                       # noqa: E402
import policy_builds as pb                 # noqa: E402
import validate_real_data as vrd           # noqa: E402

RESULTS_DIR = vrd.RESULTS_DIR
RUN_OUT_DIR = vrd.RUN_OUT_DIR
TWIN_OUT_DIR = vrd.TWIN_OUT_DIR
TEST_START, TEST_END = vrd.TEST_START, vrd.TEST_END

NETTING_DISCOUNTS = (0.0, 0.25, 0.5, 0.75, 1.0)
TEST_ALPHAS = (0.2, 0.6)


def sweep_one_alpha(ctx, log, alpha):
    vrd.prepare()
    actual = vrd.actual_arm(ctx)
    tags = {nd: f"a{alpha:.1f}_nd{nd:.2f}".replace(".", "") for nd in NETTING_DISCOUNTS}
    res_b = vrd.build(ctx, {nd: pb.variant_spec(os.path.join(RUN_OUT_DIR, f"policy_netting_{tags[nd]}.parquet"), alpha, nd)
                            for nd in NETTING_DISCOUNTS}, vrd.SEED).result()
    vrd.log_build(log, res_b)
    twins = {nd: vrd.run_twin_history(ctx, f"netting_{tags[nd]}", res_b["paths"][nd]) for nd in NETTING_DISCOUNTS}
    act = actual.result()
    log(f"=== Netting-discount sweep at alpha={alpha} ===")
    rows = []
    for nd in NETTING_DISCOUNTS:
        log(f"  --- netting_discount={nd:.2f} ---")
        res = twins[nd].result()
        fr = vrd.fill(res, f"nd={nd:.2f}")
        fr["alpha"] = alpha
        fr["netting_discount"] = nd
        fr["cost_delta_usd"] = res["kpis"]["total_landed_cost_usd"] - act["kpis"]["total_landed_cost_usd"]
        rows.append(fr)
    summary = pl.DataFrame(rows)
    vrd.write_csv(summary, "netting_discount_sweep_alpha{:.1f}".format(alpha).replace(".", "_") + ".csv")
    log(rt.show(summary))
    return summary


def multiseed_check(ctx, log, alpha=vrd.HEADLINE_ALPHA, discounts=(0.0, 1.0), seeds=vrd.MULTISEED_SEEDS):
    """5-seed robustness check of netting_discount in `discounts` at `alpha`."""
    vrd.prepare()
    actuals = {seed: vrd.ms_actual_arm(ctx, seed) for seed in seeds}
    tag = {(nd, s): f"nd{nd:.2f}_s{s}".replace(".", "") for nd in discounts for s in seeds}
    builds = {s: vrd.build(ctx, {nd: pb.variant_spec(os.path.join(RUN_OUT_DIR, f"policy_netting_ms_{tag[(nd, s)]}.parquet"),
                                                     alpha, nd) for nd in discounts}, s) for s in seeds}
    twins = {}
    for s in seeds:
        res_b = builds[s].result()
        vrd.log_build(log, res_b)
        for nd in discounts:
            twins[(nd, s)] = vrd.run_twin_history(ctx, f"netting_ms_{tag[(nd, s)]}", res_b["paths"][nd], seed=s)
    log(f"=== Multiseed check: netting_discount in {discounts} at alpha={alpha} ===")
    rows = []
    for nd in discounts:
        for s in seeds:
            act, res = actuals[s].result(), twins[(nd, s)].result()
            fr_actual, fr_csv2 = vrd.fill(act, "actual"), vrd.fill(res, "csv2")
            rows.append({
                "netting_discount": nd, "seed": s, "alpha": alpha,
                "fill_delta_pp": (fr_csv2["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100,
                "internal_replen_fill": fr_csv2["internal_replen_fill_rate"],
                "cost_delta_usd": res["kpis"]["total_landed_cost_usd"] - act["kpis"]["total_landed_cost_usd"],
            })
    summary = pl.DataFrame(rows)
    # alpha=0.2 keeps the original file name; meio_parameters.py reads netting_discount_multiseed*.csv
    suffix = "" if alpha == vrd.HEADLINE_ALPHA else "_alpha{:.1f}".format(alpha).replace(".", "_")
    vrd.write_csv(summary, f"netting_discount_multiseed{suffix}.csv")
    log(rt.show(vrd.seed_agg(summary, "netting_discount", (("internal_replen_fill", "internal_replen"),))))
    return summary


def run(ctx, log, alpha=None, multiseed=False):
    if multiseed:
        return multiseed_check(ctx, log, alpha=alpha if alpha is not None else vrd.HEADLINE_ALPHA)
    alphas = [alpha] if alpha is not None else list(TEST_ALPHAS)
    jobs = [ctx.spawn(sweep_one_alpha, ctx, log, a) for a in alphas]
    return [j.result() for j in jobs]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alpha", type=float, default=None, choices=list(TEST_ALPHAS),
                    help="Run just one alpha; omit to run both.")
    ap.add_argument("--multiseed", action="store_true",
                    help="Run the 5-seed robustness check of netting_discount in {0.0, 1.0} instead, at "
                         "--alpha (default 0.2).")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--lean-twin-outputs", action="store_true",
                    help="Skip the large per-run detail CSVs (see runtime.LEAN_TWIN_OUTPUTS).")
    args = ap.parse_args(argv)
    rt.LEAN_TWIN_OUTPUTS = args.lean_twin_outputs
    ctx = rt.Context(args.workers)
    try:
        run(ctx, rt.StepLog(), args.alpha, args.multiseed)
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
