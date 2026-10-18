"""
Clark-Scarf v2 -- Per-Segment Alpha Exploration, REAL DATA + Multi-Seed, v5 Clusters
=====================================================================================

The v5 counterpart to segment_alpha_real_data.py, for the same reason
service_level_v5_real_data.py exists for CSL: every alpha-by-cluster check this
project has was built against v4's 4-cluster scheme, and separately, against
REFERENCE_ALPHA=0.2 -- the portfolio alpha that was live when that evidence was
built. Neither holds any more. meio_parameters.py's "validated" gate now resolves
alpha=0.6 (see Periodic alpha check), and canonical segmentation is v5's 6 clusters.
Evidence built at the wrong reference alpha doesn't count for that gate (its own
decision log says so explicitly: "not used; portfolio alpha everywhere: run around
alpha 0.2; selected 0.6") -- so this isn't just "more clusters," it's the only way
per-cluster alpha evidence could ever become usable again.

What's swept: for each of v5's 6 clusters, its own alpha over {0.0 .. 1.0} with every
other cluster held at REFERENCE_ALPHA=0.6 (today's live portfolio value, not v4's
stale 0.2). z/CSL is left at its live per-cluster default throughout (no z_by_sku
override passed) -- this sweep varies alpha only, against the service levels already
adopted this session.

v4's "cl:<archetype>" twin-summary groups (segment_alpha_explore.CLUSTER_CSV, frozen
to segmentation_v4) would silently mismatch v5's archetype names and SKU membership,
so this script passes its OWN "v5cl:<archetype>" groups explicitly into every
run_twin_history() call, read back the same way via vrd.group_fill(res, f"v5cl:...").
Nothing in validate_real_data.py or segment_alpha_explore.py needs to change for
this -- groups is an additive seam (summary_request() merges caller-supplied groups
on top of the built-in segment/v4-cluster ones), not a shared global to fight over.

Same shared-draws-per-seed design as segment_alpha_real_data.py: one Monte Carlo
pass per seed serves the reference build (all clusters at 0.6) plus every (cluster,
alpha) combo for that seed, via policy_builds.rolling/walkforward sharing. Resumable:
a combo whose partial file exists is skipped.

    python segment_alpha_v5_real_data.py                                  # all combos
    python segment_alpha_v5_real_data.py --workers 6
    python segment_alpha_v5_real_data.py --cluster "Long-Tail Erratic 2"
    python segment_alpha_v5_real_data.py --summarize-only                 # just re-aggregate
"""
import argparse
import json
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                       # noqa: E402
import policy_builds as pb                 # noqa: E402
import validate_real_data as vrd           # noqa: E402

RESULTS_DIR = vrd.RESULTS_DIR
RUN_OUT_DIR = vrd.RUN_OUT_DIR
SEEDS = vrd.MULTISEED_SEEDS

CLUSTER_CSV = os.path.join(rt.REPO_ROOT, "output datasets", "segmentation", "sku_clusters.csv")   # canonical, live
CLUSTERS = ["Flagship Core", "Mid-Tier Core", "Long-Tail Erratic", "Long-Tail Erratic 2",
            "Long-Tail Erratic 3", "Long-Tail Supply-Risk"]
ALPHAS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
REFERENCE_ALPHA = 0.6   # today's live portfolio value (meio_parameters.resolve()), NOT v4's stale 0.2
DEFAULT_WORKERS = None  # the shared pool's size (CPUs - 2); --workers overrides

PARTIAL_DIR = os.path.join(RUN_OUT_DIR, "segment_alpha_v5_partial")   # gitignored scratch, one file/combo
OUT_CSV = os.path.join(RESULTS_DIR, "segment_alpha_v5_real_data_multiseed.csv")
SUMMARY_CSV = os.path.join(RESULTS_DIR, "segment_alpha_v5_real_data_summary.csv")


def load_cluster_map() -> dict:
    """sku_id -> archetype, from the CANONICAL segmentation output -- the same file
    meio_weekly_lib.py::z_values() reads live for z/CSL."""
    c = rt.read_table(CLUSTER_CSV, columns=["sku_id", "archetype"])
    return dict(zip(c["sku_id"].to_list(), c["archetype"].to_list()))


def v5_groups(cluster_map: dict) -> dict:
    out: dict = {}
    for s, a in cluster_map.items():
        out.setdefault(f"v5cl:{a}", []).append(s)
    return out


def _tag(cluster, alpha, seed):
    return (cluster.replace(" ", "") + f"_a{alpha:.1f}_s{seed}").replace(".", "")


def _partial_path(cluster, alpha, seed):
    return os.path.join(PARTIAL_DIR, f"{_tag(cluster, alpha, seed)}.json")


def _partial_done(cluster, alpha, seed):
    return os.path.exists(_partial_path(cluster, alpha, seed))


def _write_partial(row):
    os.makedirs(PARTIAL_DIR, exist_ok=True)
    with open(_partial_path(row["cluster"], row["alpha"], row["seed"]), "w") as f:
        json.dump(row, f)


def _aggregate_partials() -> pl.DataFrame:
    rows = []
    if os.path.isdir(PARTIAL_DIR):
        for fn in sorted(os.listdir(PARTIAL_DIR)):
            if fn.endswith(".json"):
                with open(os.path.join(PARTIAL_DIR, fn)) as f:
                    rows.append(json.load(f))
    out = pl.DataFrame(rows) if rows else pl.DataFrame()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    rt.write_table(out, OUT_CSV)
    return out


def cluster_stats(res, act, cluster):
    fr_actual, fr = vrd.fill(act, "actual"), vrd.fill(res, "csv2")
    return {
        "portfolio_fill_rate": fr["customer_fill_rate"],
        "own_fill_rate": vrd.group_fill(res, f"v5cl:{cluster}"),
        "internal_replen_fill_rate": fr["internal_replen_fill_rate"],
        "fill_delta_vs_actual_pp": round((fr["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100, 3),
        "cost_delta_usd": res["kpis"]["total_landed_cost_usd"] - act["kpis"]["total_landed_cost_usd"],
    }


def _seed_jobs(clusters_to_run, seed):
    """(reference needed?, [(cluster, alpha)] still to run) for one seed."""
    ref = not all(_partial_done(c, REFERENCE_ALPHA, seed) for c in CLUSTERS)
    combos = [(c, a) for c in clusters_to_run for a in ALPHAS
             if a != REFERENCE_ALPHA and not _partial_done(c, a, seed)]
    return ref, combos


def _run_seed(ctx, log, clusters_to_run, seed, cluster_map, groups):
    ref, combos = _seed_jobs(clusters_to_run, seed)
    if not ref:
        log(f"  [skip, already done] reference seed={seed}")
    if not ref and not combos:
        return 0
    actual = vrd.ms_actual_arm(ctx, seed)
    variants = {}
    if ref:
        variants["reference"] = pb.variant_spec(
            os.path.join(RUN_OUT_DIR, f"policy_sreal5_reference_s{seed}.parquet"), REFERENCE_ALPHA, 0.0,
            alpha_by_sku={})
    for c, a in combos:
        skus = [s for s, arch in cluster_map.items() if arch == c]
        variants[(c, a)] = pb.variant_spec(
            os.path.join(RUN_OUT_DIR, f"policy_sreal5_{_tag(c, a, seed)}.parquet"), REFERENCE_ALPHA, 0.0,
            alpha_by_sku={s: a for s in skus})
    res_b = vrd.build(ctx, variants, seed).result()
    vrd.log_build(log, res_b)
    twins = {}
    if ref:
        twins["reference"] = vrd.run_twin_history(ctx, f"sreal5_reference_s{seed}", res_b["paths"]["reference"],
                                                  seed=seed, groups=groups)
    for c, a in combos:
        twins[(c, a)] = vrd.run_twin_history(ctx, f"sreal5_{_tag(c, a, seed)}", res_b["paths"][(c, a)], seed=seed,
                                             groups=groups)
    act = actual.result()
    done = 0
    if ref:
        res = twins["reference"].result()
        for cluster in CLUSTERS:
            if not _partial_done(cluster, REFERENCE_ALPHA, seed):
                _write_partial({"cluster": cluster, "alpha": REFERENCE_ALPHA, "seed": seed, "netting_discount": 0.0,
                                **cluster_stats(res, act, cluster)})
        log(f"--- reference (all clusters at {REFERENCE_ALPHA}) seed={seed} --- done")
    for c, a in combos:
        res = twins[(c, a)].result()
        row = {"cluster": c, "alpha": a, "seed": seed, "netting_discount": 0.0, **cluster_stats(res, act, c)}
        _write_partial(row)
        done += 1
        log(f"[done] {c} alpha={a:.1f} seed={seed}  own_fill={row['own_fill_rate']}")
    return done


def run_all(ctx, log, clusters_to_run):
    cluster_map = load_cluster_map()
    groups = v5_groups(cluster_map)
    pending = sum(len(_seed_jobs(clusters_to_run, s)[1]) for s in SEEDS)
    log(f"\n{pending} non-reference combos to run, {ctx.workers} worker(s), one Monte Carlo pass per seed")
    jobs = [ctx.spawn(_run_seed, ctx, log, clusters_to_run, seed, cluster_map, groups) for seed in SEEDS]
    for j in jobs:
        j.result()


def summarize(log):
    final = _aggregate_partials()
    if not final.height:
        log("Nothing to summarize yet.")
        return final
    rows = []
    for cluster in CLUSTERS:
        for alpha in ALPHAS:
            g = final.filter((pl.col("cluster") == cluster) & (pl.col("alpha") == alpha))
            if not g.height:
                continue
            own, cost = g["own_fill_rate"].cast(pl.Float64).to_numpy(), g["cost_delta_usd"].to_numpy()
            rows.append({
                "cluster": cluster, "alpha": alpha, "n_seeds": g.height,
                "own_fill_mean": rt.nanmean_pandas(own), "own_fill_std": rt.nanstd_pandas(own),
                "portfolio_fill_mean": rt.nanmean_pandas(g["portfolio_fill_rate"].to_numpy()),
                "internal_replen_mean": rt.nanmean_pandas(g["internal_replen_fill_rate"].to_numpy()),
                "cost_delta_mean": rt.nanmean_pandas(cost), "cost_delta_std": rt.nanstd_pandas(cost),
            })
    summary = pl.DataFrame(rows)
    rt.write_table(summary, SUMMARY_CSV)
    log(rt.show(summary))
    log("\n=== Best alpha per cluster, by OWN fill rate (mean across seeds so far) ===")
    for cluster in CLUSTERS:
        sub = summary.filter(pl.col("cluster") == cluster)
        if not sub.height:
            continue
        best = sub.sort("own_fill_mean", descending=True, maintain_order=True, nulls_last=True).row(0, named=True)
        spread = (sub["own_fill_mean"].max() - sub["own_fill_mean"].min()) * 100
        ref_row = sub.filter(pl.col("alpha") == REFERENCE_ALPHA)
        ref_fill = ref_row["own_fill_mean"][0] if ref_row.height else None
        log(f"  {cluster:24s}: best alpha={best['alpha']:.1f} (own_fill={best['own_fill_mean']:.4f}), "
            f"vs. reference alpha={REFERENCE_ALPHA:g} (own_fill={ref_fill}), spread {spread:.2f}pp across the sweep")
    return summary


def run(ctx, log, cluster=None, summarize_only=False):
    vrd.prepare()
    if not summarize_only:
        run_all(ctx, log, [cluster] if cluster else CLUSTERS)
    log("\n=== Aggregating ===")
    return summarize(log)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cluster", default=None, choices=CLUSTERS, help="Run just one cluster; omit for all 6.")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help="Process-pool size (default: CPUs - 2). 1 = serial.")
    ap.add_argument("--summarize-only", action="store_true",
                    help="Skip running; just (re)aggregate whatever's already on disk.")
    ap.add_argument("--lean-twin-outputs", action="store_true",
                    help="Skip the large per-run detail CSVs (see runtime.LEAN_TWIN_OUTPUTS).")
    args = ap.parse_args(argv)
    rt.LEAN_TWIN_OUTPUTS = args.lean_twin_outputs
    ctx = rt.Context(args.workers)
    try:
        run(ctx, rt.StepLog(), args.cluster, args.summarize_only)
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
