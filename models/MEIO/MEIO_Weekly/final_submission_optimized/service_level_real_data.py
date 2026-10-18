"""
Clark-Scarf v2 -- Real-Data, Multi-Seed Service-Level (CSL/z) Sweep by Cluster (optimized)
===============================================================================

The same sweep as final_submission/service_level_real_data.py: for each of the four
clusters, its own target CSL over {90%, 95%, 97%, 99%} with the other three at the
segmentation deliverable's own recommended z, on real replayed 2025 demand, 5 seeds,
alpha=0.2, netting_discount=0.0. The unmodified ladder is built once per seed and sliced
into every cluster's "at its own recommendation" data point, as before.

A z override only changes which quantile of the Monte Carlo draws becomes the reorder
point, so the 13 builds of a seed share one pass of draws; the quantiles for SKUs whose
z did not change are reused between combos. The policy files and results are identical
to building each combo on its own. Resumable via the same partial files.

    python service_level_real_data.py                             # all combos
    python service_level_real_data.py --cluster "Mid-Tier Core"   # one cluster only
    python service_level_real_data.py --summarize-only            # just re-aggregate
"""
import argparse
import json
import os
import sys

import polars as pl
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                       # noqa: E402
import policy_builds as pb                 # noqa: E402
import validate_real_data as vrd           # noqa: E402

RESULTS_DIR = vrd.RESULTS_DIR
RUN_OUT_DIR = vrd.RUN_OUT_DIR
TWIN_OUT_DIR = vrd.TWIN_OUT_DIR
TEST_START, TEST_END = vrd.TEST_START, vrd.TEST_END
SEEDS = vrd.MULTISEED_SEEDS
HEADLINE_ALPHA = vrd.HEADLINE_ALPHA
NETTING_DISCOUNT = 0.0   # the fix validated this session; current recommended config (see README)

REPO_ROOT = str(rt.REPO_ROOT)
CLUSTER_CSV = os.path.join(REPO_ROOT, "Clustering & Segmentation", "segmentation_v4", "sku_clusters.csv")

CLUSTERS = ["Flagship Promo-Driven", "Mid-Tier Core", "Long-Tail Erratic", "Long-Tail Supply-Risk"]
CANDIDATE_CSL = [0.90, 0.95, 0.97, 0.99]
DEFAULT_WORKERS = None

PARTIAL_DIR = os.path.join(RUN_OUT_DIR, "service_level_partial")   # gitignored scratch, one file/combo
OUT_CSV = os.path.join(RESULTS_DIR, "service_level_real_data_multiseed.csv")   # tracked, aggregated
SUMMARY_CSV = os.path.join(RESULTS_DIR, "service_level_real_data_summary.csv")


def candidate_z(csl):
    return float(norm.ppf(csl))


def load_baseline():
    """sku->archetype, sku->baseline z, archetype->recommended CSL, straight from the
    segmentation deliverable's output."""
    c = rt.read_table(CLUSTER_CSV, columns=["sku_id", "archetype", "safety_factor_z", "recommended_csl"])
    arch_map = dict(zip(c["sku_id"].to_list(), c["archetype"].to_list()))
    z_map = dict(zip(c["sku_id"].to_list(), c["safety_factor_z"].cast(pl.Float64).to_list()))
    csl_by_cluster = dict(zip(c["archetype"].to_list(), c["recommended_csl"].cast(pl.Float64).to_list()))
    return arch_map, z_map, csl_by_cluster


def cluster_skus(arch_map, cluster):
    return [sku for sku, a in arch_map.items() if a == cluster]


def _tag(cluster, csl, seed):
    return cluster.replace(" ", "") + f"_csl{round(csl * 100)}_s{seed}"


def _partial_path(cluster, csl, seed):
    return os.path.join(PARTIAL_DIR, f"{_tag(cluster, csl, seed)}.json")


def _partial_done(cluster, csl, seed):
    return os.path.exists(_partial_path(cluster, csl, seed))


def _write_partial(row):
    os.makedirs(PARTIAL_DIR, exist_ok=True)
    with open(_partial_path(row["cluster"], row["csl"], row["seed"]), "w") as f:
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
        "own_fill_rate": vrd.group_fill(res, f"cl:{cluster}"),
        "internal_replen_fill_rate": fr["internal_replen_fill_rate"],
        "fill_delta_vs_actual_pp": round((fr["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100, 3),
        "cost_delta_usd": res["kpis"]["total_landed_cost_usd"] - act["kpis"]["total_landed_cost_usd"],
    }


def _run_seed(ctx, log, clusters_to_run, seed, baseline):
    arch_map, z_map, csl_by_cluster = baseline
    ladder = not all(_partial_done(c, csl_by_cluster[c], seed) for c in CLUSTERS)
    combos = [(c, csl) for c in clusters_to_run for csl in CANDIDATE_CSL
              if abs(csl - csl_by_cluster[c]) > 1e-9 and not _partial_done(c, csl, seed)]
    if not ladder:
        log(f"  [skip, already done] baseline ladder seed={seed}")
    if not ladder and not combos:
        return
    actual = vrd.ms_actual_arm(ctx, seed)
    variants = {}
    if ladder:
        variants["ladder"] = pb.variant_spec(os.path.join(RUN_OUT_DIR, f"policy_svc_baseline_ladder_s{seed}.parquet"),
                                             HEADLINE_ALPHA, NETTING_DISCOUNT, z_by_sku=dict(z_map))
    for c, csl in combos:
        override = dict(z_map)
        cz = candidate_z(csl)
        override.update({sku: cz for sku in cluster_skus(arch_map, c)})
        variants[(c, csl)] = pb.variant_spec(os.path.join(RUN_OUT_DIR, f"policy_svc_{_tag(c, csl, seed)}.parquet"),
                                             HEADLINE_ALPHA, NETTING_DISCOUNT, z_by_sku=override)
    res_b = vrd.build(ctx, variants, seed).result()
    vrd.log_build(log, res_b)
    twins = {}
    if ladder:
        twins["ladder"] = vrd.run_twin_history(ctx, f"svc_baseline_ladder_s{seed}", res_b["paths"]["ladder"], seed=seed)
    for c, csl in combos:
        twins[(c, csl)] = vrd.run_twin_history(ctx, f"svc_{_tag(c, csl, seed)}", res_b["paths"][(c, csl)], seed=seed)
    act = actual.result()
    if ladder:
        res = twins["ladder"].result()
        for cluster in CLUSTERS:
            csl = csl_by_cluster[cluster]
            if not _partial_done(cluster, csl, seed):
                _write_partial({"cluster": cluster, "csl": csl, "z": round(candidate_z(csl), 4), "seed": seed,
                                "alpha": HEADLINE_ALPHA, "netting_discount": NETTING_DISCOUNT,
                                **cluster_stats(res, act, cluster)})
        log(f"--- baseline ladder (segmentation team's own z, all clusters) seed={seed} --- done")
    for c, csl in combos:
        res = twins[(c, csl)].result()
        row = {"cluster": c, "csl": csl, "z": round(candidate_z(csl), 4), "seed": seed, "alpha": HEADLINE_ALPHA,
               "netting_discount": NETTING_DISCOUNT, **cluster_stats(res, act, c)}
        _write_partial(row)
        log(f"[done] {c} csl={csl:.2f} seed={seed}  own_fill={row['own_fill_rate']}")


def run_all(ctx, log, clusters_to_run):
    baseline = load_baseline()
    _, _, csl_by_cluster = baseline
    pending = sum(1 for s in SEEDS for c in clusters_to_run for csl in CANDIDATE_CSL
                  if abs(csl - csl_by_cluster[c]) > 1e-9 and not _partial_done(c, csl, s))
    log(f"\n{pending} non-baseline combos to run, {ctx.workers} worker(s), one Monte Carlo pass per seed")
    jobs = [ctx.spawn(_run_seed, ctx, log, clusters_to_run, seed, baseline) for seed in SEEDS]
    for j in jobs:
        j.result()


def summarize(log):
    final = _aggregate_partials()
    if not final.height:
        log("Nothing to summarize yet.")
        return final
    rows = []
    for cluster in CLUSTERS:
        for csl in CANDIDATE_CSL:
            g = final.filter((pl.col("cluster") == cluster) & (pl.col("csl") == csl))
            if not g.height:
                continue
            own, cost = g["own_fill_rate"].cast(pl.Float64).to_numpy(), g["cost_delta_usd"].to_numpy()
            rows.append({
                "cluster": cluster, "csl": csl, "n_seeds": g.height,
                "own_fill_mean": rt.nanmean_pandas(own), "own_fill_std": rt.nanstd_pandas(own),
                "portfolio_fill_mean": rt.nanmean_pandas(g["portfolio_fill_rate"].to_numpy()),
                "internal_replen_mean": rt.nanmean_pandas(g["internal_replen_fill_rate"].to_numpy()),
                "cost_delta_mean": rt.nanmean_pandas(cost), "cost_delta_std": rt.nanstd_pandas(cost),
            })
    summary = pl.DataFrame(rows)
    rt.write_table(summary, SUMMARY_CSV)
    log(rt.show(summary))
    log("\n=== Best CSL per cluster, by OWN fill rate (mean across seeds so far) ===")
    _, _, csl_by_cluster = load_baseline()
    for cluster in CLUSTERS:
        sub = summary.filter(pl.col("cluster") == cluster)
        if not sub.height:
            continue
        best = sub.sort("own_fill_mean", descending=True, maintain_order=True, nulls_last=True).row(0, named=True)
        log(f"  {cluster:24s}: best CSL={best['csl']:.0%} (own_fill={best['own_fill_mean']:.4f}) "
            f"vs. segmentation-recommended {csl_by_cluster[cluster]:.0%}")
    return summary


def run(ctx, log, cluster=None, summarize_only=False):
    vrd.prepare()
    if not summarize_only:
        run_all(ctx, log, [cluster] if cluster else CLUSTERS)
    log("\n=== Aggregating ===")
    return summarize(log)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cluster", default=None, choices=CLUSTERS, help="Run just one cluster; omit for all 4.")
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
