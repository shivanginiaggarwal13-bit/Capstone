"""
Clark-Scarf v2 -- Per-Segment Alpha Exploration (Diagnostic, Not Yet a Recommendation)
=========================================================================== (optimized)

The same diagnostic as final_submission/segment_alpha_explore.py: for each of the four
Clustering & Segmentation clusters, sweep its own alpha over {0.0, ..., 1.0} with the
other three at 0.2, on the synthetic rolling-2026 basis (`digital_twin --mode forward`),
single seed. Read it as a diagnostic, not a production recommendation (see the
original's docstring for the two caveats).

**The RNG chain.** The original passes ONE generator through all 24 rolling builds, so
build k starts where build k-1's draws ended. That is reproduced exactly: the 24 builds
are a chain of pool tasks, each handed the previous one's generator state, run at top
priority with 4 threads each. Because each link's draws depend on the one before, this
chain -- about 24 x 52 weeks of gamma draws -- is the longest path through run_all.

`--fresh-rng` is an opt-in alternative: every (cluster, alpha) build starts from a fresh
generator at the seed, which is how the real-data version (segment_alpha_real_data.py)
was fixed after shared-stream drift was found to contaminate a sweep. All 24 variants
then share one pass of draws and the step takes a couple of minutes, but its numbers no
longer reproduce the published segment_alpha_sweep_*.csv (they move by seed noise).

    python segment_alpha_explore.py               # full sweep + own-fill correction
    python segment_alpha_explore.py --stage sweep # portfolio-level sweep only
    python segment_alpha_explore.py --fresh-rng   # opt-in: one stream for all builds
"""
import argparse
import os
import sys
import threading
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                       # noqa: E402
import policy_builds as pb                 # noqa: E402
import digital_twin_fast as twin           # noqa: E402
import scenario                            # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = str(rt.REPO_ROOT)
SCENARIO_CONFIG = Path(REPO_ROOT) / "scenario_config.json"
SCENARIO_SETTINGS = scenario.load_config(SCENARIO_CONFIG)
SCENARIO_NAME = scenario.scenario_folder_name(SCENARIO_SETTINGS)
DEFAULT_DATA = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "meio", "data_dir"))
CLUSTER_CSV = os.path.join(REPO_ROOT, "Clustering & Segmentation", "segmentation_v4", "sku_clusters.csv")
TWIN = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "entrypoint"))
SCENARIO_OUT_DIR = str(scenario.resolve_scenario_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "artifacts", "scenario"))
TWIN_OUT_ROOT = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "artifacts", "twin_output_root"))
RUN_OUT_DIR = os.path.join(SCENARIO_OUT_DIR, "validation_output")
RESULTS_DIR = os.path.join(SCENARIO_OUT_DIR, "results")
STATE_CSV = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "state"))
BASELINE_POLICY_CSV = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "policy"))

CLUSTERS = ["Flagship Promo-Driven", "Mid-Tier Core", "Long-Tail Erratic", "Long-Tail Supply-Risk"]
ALPHAS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
REFERENCE_ALPHA = 0.2
N_SIMS = 1200
SEED = 2026
AS_OF_WEEK = "2025-W52"

P_CHAIN, P_BASELINE, P_BUILD, P_TWIN = 0, 1, 2, 3
ROLLING_WEEKS = None          # None: every forward week (tests shorten it)
CHAIN_THREADS = 4

_PREP = threading.Lock()


def prepare():
    with _PREP:
        os.makedirs(SCENARIO_OUT_DIR, exist_ok=True)
        scenario.copy_config(SCENARIO_CONFIG, Path(SCENARIO_OUT_DIR))
        os.makedirs(RESULTS_DIR, exist_ok=True)
        os.makedirs(RUN_OUT_DIR, exist_ok=True)


def load_cluster_map():
    c = rt.read_table(CLUSTER_CSV, columns=["sku_id", "archetype"])
    return dict(zip(c["sku_id"].to_list(), c["archetype"].to_list()))


def forward_job(run_name, policy_csv, seed=SEED, use_cache=False) -> dict:
    """Synthetic 2026 rolling basis (--mode forward, default 52-week horizon)."""
    return {"run_name": run_name, "mode": "forward", "policy": str(policy_csv), "state": STATE_CSV, "seed": seed,
            "out_root": TWIN_OUT_ROOT, "scenario_config": str(SCENARIO_CONFIG), "label": run_name,
            "use_cache": use_cache}


def run_dir(run_name) -> str:
    return os.path.join(TWIN_OUT_ROOT, "projections", SCENARIO_NAME, run_name)


def stage_baseline(ctx):
    """Synthetic-basis baseline (the dataset's own forward_policy.parquet), cached on disk."""
    forward_policy_csv = os.path.join(REPO_ROOT, "output datasets", "history", "forward_policy.parquet")
    return ctx.once(("twin", "synthetic_baseline"), twin.simulate_task,
                    forward_job("synthetic_baseline", forward_policy_csv, use_cache=True), priority=P_BASELINE)


def _tag(cluster, alpha):
    return f"{cluster.replace(' ', '')}_a{alpha:.1f}".replace(".", "")


def _override(cluster_map, cluster, alpha):
    return {sku: (alpha if arch == cluster else REFERENCE_ALPHA) for sku, arch in cluster_map.items()}


def rolling_job(variants, rng, threads=1) -> dict:
    return {"data_dir": DEFAULT_DATA, "as_of_week": AS_OF_WEEK, "variants": variants, "rng": rng, "n_sims": N_SIMS,
            "threads": threads, "max_weeks": ROLLING_WEEKS}


def stage_sweep(ctx, log, seed=SEED, fresh_rng=False):
    """Portfolio-level fill rate per (cluster, alpha): 24 rolling-policy builds + twin runs."""
    log("=== Stage: sweep (portfolio-level) ===")
    prepare()
    cluster_map = load_cluster_map()
    baseline = stage_baseline(ctx)
    combos = [(c, a) for c in CLUSTERS for a in ALPHAS]
    specs = {(c, a): pb.variant_spec(os.path.join(RUN_OUT_DIR, f"policy_{_tag(c, a)}.parquet"), REFERENCE_ALPHA, 1.0,
                                     alpha_by_sku=_override(cluster_map, c, a)) for c, a in combos}
    twins = {}
    if fresh_rng:
        log("  --fresh-rng: every build from a fresh generator at the seed (one shared pass of draws)")
        res = ctx.submit(pb.rolling_task, rolling_job(specs, seed, threads=CHAIN_THREADS), priority=P_CHAIN,
                         lane="explore").result()
        for (c, a) in combos:
            twins[(c, a)] = ctx.submit(twin.simulate_task, forward_job(_tag(c, a), res["paths"][(c, a)], seed),
                                       priority=P_TWIN)
    else:
        state = seed                        # one generator through all 24 builds, as the original
        for n, (c, a) in enumerate(combos, 1):
            res = ctx.submit(pb.rolling_task, rolling_job({(c, a): specs[(c, a)]}, state, CHAIN_THREADS),
                             priority=P_CHAIN, lane="explore").result()
            state = res["rng_state"]
            log(f"--- [{n}/{len(combos)}] cluster={c} alpha={a:.1f} --- policy built")
            twins[(c, a)] = ctx.submit(twin.simulate_task, forward_job(_tag(c, a), res["paths"][(c, a)], seed),
                                       priority=P_TWIN)
    baseline_kpi = baseline.result()["kpis"]
    rows = [{"cluster": "ALL (baseline)", "alpha": None, "fill_rate": baseline_kpi["customer_fill_rate"],
             "internal_replen_fill_rate": baseline_kpi["internal_replen_fill_rate"],
             "total_landed_cost_usd": baseline_kpi["total_landed_cost_usd"], "cost_delta_usd": 0.0}]
    for c, a in combos:
        kpi = twins[(c, a)].result()["kpis"]
        cost_delta = kpi["total_landed_cost_usd"] - baseline_kpi["total_landed_cost_usd"]
        rows.append({"cluster": c, "alpha": a, "fill_rate": kpi["customer_fill_rate"],
                     "internal_replen_fill_rate": kpi["internal_replen_fill_rate"],
                     "total_landed_cost_usd": kpi["total_landed_cost_usd"], "cost_delta_usd": cost_delta})
        log(f"  {c} alpha={a:.1f}: fill {kpi['customer_fill_rate']:.4f}  cost_delta ${cost_delta:+,.0f}")
    summary = pl.DataFrame(rows, schema_overrides={"alpha": pl.Float64})
    rt.write_table(summary, os.path.join(RESULTS_DIR, "segment_alpha_sweep_portfolio.csv"))
    log(rt.show(summary))
    return summary


def stage_own_fill(ctx, log):
    """Re-slice each sweep run's own inventory.parquet down to that cluster's SKUs (corrects
    the portfolio-level dilution). Reads the sweep's run folders; no new twin runs."""
    log("=== Stage: own_fill (corrects portfolio-level dilution) ===")
    cluster_map = load_cluster_map()
    stage_baseline(ctx).result()
    rows = []
    for cluster in CLUSTERS:
        skus = [s for s, a in cluster_map.items() if a == cluster]
        for alpha in ALPHAS:
            inv_path = os.path.join(run_dir(_tag(cluster, alpha)), "inventory.parquet")
            if not os.path.exists(inv_path):
                log(f"  SKIP {_tag(cluster, alpha)}: run the 'sweep' stage first")
                continue
            inv = rt.read_table(inv_path, columns=["sku_id", "demand_customer_qty", "unmet_customer_qty"], cache=False)
            g = inv.filter(pl.col("sku_id").is_in(skus))
            cd = float(np.add.reduce(g["demand_customer_qty"].cast(pl.Float64).to_numpy()))
            cu = float(np.add.reduce(g["unmet_customer_qty"].cast(pl.Float64).to_numpy()))
            rows.append({"cluster": cluster, "alpha": alpha, "own_fill_rate": round(1 - cu / cd, 4) if cd else None})
    out = pl.DataFrame(rows)
    piv = out.pivot(on="cluster", index="alpha", values="own_fill_rate").sort("alpha")
    piv = piv.select(["alpha"] + [c for c in CLUSTERS if c in piv.columns])
    rt.write_table(piv, os.path.join(RESULTS_DIR, "segment_alpha_sweep_own_fill.csv"))
    log(rt.show(piv))
    log("\n=== Spread across alpha, OWN cluster fill (diagnostic, not a recommendation) ===")
    for cl in CLUSTERS:
        if cl not in piv.columns:
            continue
        s = piv.select(["alpha", cl]).drop_nulls()
        best = s.sort(cl, descending=True, maintain_order=True).row(0)
        log(f"  {cl:24s}: spread {((s[cl].max() - s[cl].min()) * 100):.2f}pp  best alpha={best[0]} ({best[1]:.4f})")
    return piv


STAGES = {"baseline": None, "sweep": stage_sweep, "own_fill": stage_own_fill}


def run(ctx, log, stage=None, seed=SEED, fresh_rng=False):
    prepare()
    if stage == "baseline":
        out = stage_baseline(ctx).result()
    elif stage == "sweep":
        out = stage_sweep(ctx, log, seed, fresh_rng)
    elif stage == "own_fill":
        out = stage_own_fill(ctx, log)
    else:
        stage_sweep(ctx, log, seed, fresh_rng)
        out = stage_own_fill(ctx, log)
    # The original's twin runs here don't pass --skip-output-documentation, so each one
    # rewrote <twin output root>/OUTPUT_DOCUMENTATION.md. Written once, if any run simulated.
    if stage in (None, "sweep") or not stage_baseline(ctx).result().get("cached"):
        twin.write_output_documentation(Path(TWIN_OUT_ROOT))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=list(STAGES), default=None,
                    help="Run just one stage; omit to run sweep then own_fill.")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--fresh-rng", action="store_true",
                    help="Opt-in: a fresh generator per build (one shared pass of draws). Faster, but does not "
                         "reproduce the published sweep.")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)
    ctx = rt.Context(args.workers)
    try:
        run(ctx, rt.StepLog(), args.stage, args.seed, args.fresh_rng)
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
