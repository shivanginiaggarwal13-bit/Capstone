"""
Clark-Scarf v2 — Real-Data, Multi-Seed Service-Level (CSL/z) Sweep, v5 Clusters
===============================================================================

`meio_weekly_lib.py::assign_z()` now reads the LIVE, canonical segmentation
output (`output datasets/segmentation/sku_clusters.csv`, currently
segmentation_v5's 6 archetypes) instead of a frozen v4 snapshot -- see
README "Cluster-based service level adopted as default". That fix closed a
wiring gap but opened a validation one: every multi-seed real-data check
this project has (`service_level_real_data.py` included) was run against
v4's 4-cluster, 2-distinct-z scheme. v5's extra granularity -- and
specifically its new z=1.717 cluster, "Long-Tail Erratic 2" -- has never
been tested against real demand. This is that test.

Same design as `service_level_real_data.py` (read that docstring for the
full reasoning -- held-at-own-recommendation reference per cluster, fresh
identical seed per combo, dedup against the baseline ladder), adapted for
v5's clusters and reading the CANONICAL path directly (the same one
assign_z() now uses live) instead of a frozen version-specific file:

- 6 clusters (not 4): Flagship Core, Mid-Tier Core, Long-Tail Erratic,
  Long-Tail Erratic 2, Long-Tail Erratic 3, Long-Tail Supply-Risk.
- 5 CSL candidates (not 4): {90%, 95%, 95.7%, 97%, 99%} -- the extra 95.7%
  is Long-Tail Erratic 2's own exact recommendation (z=1.717), added so the
  sweep directly tests the specific untested value live in production,
  not just nearby round numbers.

Scale and dedup: 6 clusters x 5 CSL candidates x 5 seeds = 150 logical
combos, but every cluster's candidate matching its OWN recommended CSL is
identical to the full, unmodified status-quo ladder -- computed once per
seed and reused for that cluster's data point there. That's 5
baseline-ladder builds + (6 clusters x 4 remaining candidates x 5 seeds) =
125 actual walk-forward builds + twin runs (~2.5-3.5 hrs at the default 4
workers, based on this project's measured per-combo pace).

PARALLEL + RESUMABLE: identical infrastructure to service_level_real_data.py
and segment_alpha_real_data.py. Separate output files and partial-result
directory from the v4 version, so neither run's progress or evidence
overwrites the other's -- both are kept as distinct, comparable evidence.

    python service_level_v5_real_data.py                           # all combos, default 4 workers
    python service_level_v5_real_data.py --cluster "Long-Tail Erratic 2"
    python service_level_v5_real_data.py --summarize-only          # just re-aggregate
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate_real_data as vrd

lib = vrd.lib   # the exact meio_weekly_lib module instance validate_real_data.py (and clark_scarf_v2.py
                # via its own import) use -- mutating Z_OVERRIDE_BY_SKU here affects their z-sizing.

RUN_OUT_DIR = vrd.RUN_OUT_DIR
TEST_START, TEST_END = vrd.TEST_START, vrd.TEST_END
SEEDS = vrd.MULTISEED_SEEDS
HEADLINE_ALPHA = vrd.HEADLINE_ALPHA
NETTING_DISCOUNT = 0.0   # the fix validated this session; current recommended config (see README)

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
CLUSTER_CSV = os.path.join(REPO_ROOT, "output datasets", "segmentation", "sku_clusters.csv")   # canonical, live
AGENT_OUTPUT_DIR = os.path.join(REPO_ROOT, "output datasets", "agent output")

CLUSTERS = ["Flagship Core", "Mid-Tier Core", "Long-Tail Erratic", "Long-Tail Erratic 2",
            "Long-Tail Erratic 3", "Long-Tail Supply-Risk"]
CANDIDATE_CSL = [0.90, 0.95, 0.957, 0.97, 0.99]
DEFAULT_WORKERS = 4

PARTIAL_DIR = os.path.join(RUN_OUT_DIR, "service_level_v5_partial")   # gitignored scratch, one file/combo
OUT_CSV = os.path.join(AGENT_OUTPUT_DIR, "service_level_v5_real_data_multiseed.csv")   # tracked, agent-facing
SUMMARY_CSV = os.path.join(AGENT_OUTPUT_DIR, "service_level_v5_real_data_summary.csv")


def candidate_z(csl):
    return float(norm.ppf(csl))


def load_baseline():
    """sku->archetype, sku->baseline z, archetype->recommended CSL -- all
    straight from the CANONICAL segmentation output (the same file
    meio_weekly_lib.py::assign_z() reads live), nothing recomputed or
    assumed."""
    c = pd.read_csv(CLUSTER_CSV, usecols=["sku_id", "archetype", "safety_factor_z", "recommended_csl"])
    arch_map = dict(zip(c.sku_id, c.archetype))
    z_map = dict(zip(c.sku_id, c.safety_factor_z.astype(float)))
    csl_by_cluster = dict(zip(c.archetype, c.recommended_csl.astype(float)))
    return arch_map, z_map, csl_by_cluster


def cluster_skus(arch_map, cluster):
    return [sku for sku, a in arch_map.items() if a == cluster]


def _partial_path(cluster, csl, seed):
    tag = cluster.replace(" ", "") + f"_csl{round(csl * 1000)}_s{seed}"
    return os.path.join(PARTIAL_DIR, f"{tag}.json")


def _partial_done(cluster, csl, seed):
    return os.path.exists(_partial_path(cluster, csl, seed))


def _write_partial(row):
    os.makedirs(PARTIAL_DIR, exist_ok=True)
    with open(_partial_path(row["cluster"], row["csl"], row["seed"]), "w") as f:
        json.dump(row, f)


def _aggregate_partials():
    rows = []
    if os.path.isdir(PARTIAL_DIR):
        for fn in sorted(os.listdir(PARTIAL_DIR)):
            if fn.endswith(".json"):
                with open(os.path.join(PARTIAL_DIR, fn)) as f:
                    rows.append(json.load(f))
    out = pd.DataFrame(rows)
    os.makedirs(AGENT_OUTPUT_DIR, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    return out


def _actual_arm(seed):
    """ms_actual_s{seed} -- same naming/cache as netting_discount_sweep.py,
    segment_alpha_real_data.py and service_level_real_data.py's multiseed
    checks; reused for free."""
    run_name = f"ms_actual_s{seed}"
    out_dir = os.path.join(RUN_OUT_DIR, run_name)
    if os.path.exists(os.path.join(out_dir, "run_kpis.json")):
        with open(os.path.join(out_dir, "run_kpis.json")) as f:
            kpi = json.load(f)
        inv = pd.read_csv(os.path.join(out_dir, "inventory.csv"))
        return kpi, inv
    return vrd.run_twin_history(run_name, vrd.BASELINE_POLICY_CSV, seed=seed)


def simulate_combo(z_override, seed, tag):
    """Builds + simulates ONE combo with the given FULL per-SKU z override
    applied via meio_weekly_lib.Z_OVERRIDE_BY_SKU. There's no parameter seam
    for this in build_walkforward_policy_csv2 the way alpha_override is, so
    it's set/cleared directly here (each worker process has its own fresh
    module state under ProcessPoolExecutor's spawn, so no cross-worker
    contamination risk; the try/finally guards against leaking into any
    OTHER call within the same worker)."""
    actual_kpi, inv_actual = _actual_arm(seed)
    rng = np.random.default_rng(seed)
    lib.Z_OVERRIDE_BY_SKU.clear()
    lib.Z_OVERRIDE_BY_SKU.update(z_override)
    try:
        policy_csv = vrd.build_walkforward_policy_csv2(
            vrd.DEFAULT_DATA, os.path.join(RUN_OUT_DIR, f"policy_svc5_{tag}.csv"),
            alpha=HEADLINE_ALPHA, rng=rng, netting_discount=NETTING_DISCOUNT)
    finally:
        lib.Z_OVERRIDE_BY_SKU.clear()
    kpi, inv = vrd.run_twin_history(f"svc5_{tag}", policy_csv, seed=seed)
    test_actual = inv_actual[(inv_actual.period >= TEST_START) & (inv_actual.period <= TEST_END)]
    test_inv = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)]
    return kpi, actual_kpi, test_inv, test_actual


def cluster_stats(test_inv, test_actual, cluster_sku_list, kpi, actual_kpi):
    fr_actual = vrd.fill_rate_kpis(test_actual, "actual")
    fr = vrd.fill_rate_kpis(test_inv, "csv2")
    own = test_inv[test_inv.sku_id.isin(cluster_sku_list)]
    cd, cu = own.demand_customer_qty.sum(), own.unmet_customer_qty.sum()
    own_fill = round(float(1 - cu / cd), 4) if cd else None
    return {
        "portfolio_fill_rate": fr["customer_fill_rate"],
        "own_fill_rate": own_fill,
        "internal_replen_fill_rate": fr["internal_replen_fill_rate"],
        "fill_delta_vs_actual_pp": round((fr["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100, 3),
        "cost_delta_usd": kpi["total_landed_cost_usd"] - actual_kpi["total_landed_cost_usd"],
    }


def _worker(job):
    """Top-level (picklable) function for ProcessPoolExecutor. One job =
    one non-baseline (cluster, csl, seed) combo."""
    cluster, csl, seed = job
    if _partial_done(cluster, csl, seed):
        return f"[skip, already done] {cluster} csl={csl:.3f} seed={seed}"
    arch_map, z_map, _ = load_baseline()
    skus = cluster_skus(arch_map, cluster)
    override = dict(z_map)
    cz = candidate_z(csl)
    override.update({sku: cz for sku in skus})
    tag = cluster.replace(" ", "") + f"_csl{round(csl * 1000)}_s{seed}"
    kpi, actual_kpi, test_inv, test_actual = simulate_combo(override, seed, tag)
    row = {"cluster": cluster, "csl": csl, "z": round(cz, 4), "seed": seed,
           **cluster_stats(test_inv, test_actual, skus, kpi, actual_kpi)}
    _write_partial(row)
    return f"[done] {cluster} csl={csl:.3f} seed={seed}  own_fill={row['own_fill_rate']}"


def _limit_blas_threads():
    """Call BEFORE creating the process pool -- see segment_alpha_real_data.py's
    docstring for why (numpy's BLAS backend oversubscribing cores otherwise)."""
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[v] = "1"


def run_all(clusters_to_run, workers):
    arch_map, z_map, csl_by_cluster = load_baseline()

    # --- Serial pre-flight: the full, unmodified status-quo z-ladder, once
    # per seed, sliced into all 6 clusters' "at its own recommended CSL"
    # data point. Must be serial -- shared across many later jobs, and a
    # race building it would be as bad as the shared actual-baseline race
    # segment_alpha_real_data.py already guards against.
    for seed in SEEDS:
        if all(_partial_done(c, csl_by_cluster[c], seed) for c in CLUSTERS):
            print(f"  [skip, already done] baseline ladder seed={seed}")
            continue
        print(f"--- baseline ladder (canonical v5 z, all clusters) seed={seed} ---")
        kpi, actual_kpi, test_inv, test_actual = simulate_combo(z_map, seed, f"baseline_ladder_s{seed}")
        for cluster in CLUSTERS:
            csl = csl_by_cluster[cluster]
            if _partial_done(cluster, csl, seed):
                continue
            skus = cluster_skus(arch_map, cluster)
            row = {"cluster": cluster, "csl": csl, "z": round(candidate_z(csl), 4), "seed": seed,
                   **cluster_stats(test_inv, test_actual, skus, kpi, actual_kpi)}
            _write_partial(row)

    # --- Parallel: non-baseline (cluster, csl, seed) jobs.
    jobs = [(cluster, csl, seed)
            for cluster in clusters_to_run
            for csl in CANDIDATE_CSL if abs(csl - csl_by_cluster[cluster]) > 1e-9
            for seed in SEEDS
            if not _partial_done(cluster, csl, seed)]
    print(f"\n{len(jobs)} non-baseline combos to run, {workers} worker(s)")
    if not jobs:
        return

    if workers <= 1:
        for job in jobs:
            print(_worker(job))
        return

    _limit_blas_threads()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, job): job for job in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            print(f"[{i}/{len(jobs)}] {fut.result()}")


def summarize():
    final = _aggregate_partials()
    if not len(final):
        print("Nothing to summarize yet.")
        return final
    rows = []
    for cluster in CLUSTERS:
        for csl in CANDIDATE_CSL:
            g = final[(final.cluster == cluster) & (final.csl == csl)]
            if not len(g):
                continue
            rows.append({
                "cluster": cluster, "csl": csl, "n_seeds": len(g),
                "own_fill_mean": g.own_fill_rate.mean(), "own_fill_std": g.own_fill_rate.std(),
                "portfolio_fill_mean": g.portfolio_fill_rate.mean(),
                "internal_replen_mean": g.internal_replen_fill_rate.mean(),
                "cost_delta_mean": g.cost_delta_usd.mean(), "cost_delta_std": g.cost_delta_usd.std(),
            })
    summary = pd.DataFrame(rows)
    summary.to_csv(SUMMARY_CSV, index=False)
    print(summary.to_string(index=False))

    print("\n=== Best CSL per cluster, by OWN fill rate (mean across seeds so far) ===")
    _, _, csl_by_cluster = load_baseline()
    for cluster in CLUSTERS:
        sub = summary[summary.cluster == cluster]
        if not len(sub):
            continue
        best = sub.loc[sub.own_fill_mean.idxmax()]
        rec = csl_by_cluster[cluster]
        print(f"  {cluster:24s}: best CSL={best.csl:.1%} (own_fill={best.own_fill_mean:.4f}) "
              f"vs. segmentation-recommended {rec:.1%}")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cluster", default=None, choices=CLUSTERS, help="Run just one cluster; omit for all 6.")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                     help=f"Parallel worker processes (default {DEFAULT_WORKERS}). 1 = serial.")
    ap.add_argument("--summarize-only", action="store_true",
                     help="Skip running; just (re)aggregate whatever's already on disk.")
    args = ap.parse_args()
    os.makedirs(AGENT_OUTPUT_DIR, exist_ok=True)

    if not args.summarize_only:
        clusters_to_run = [args.cluster] if args.cluster else CLUSTERS
        run_all(clusters_to_run, args.workers)

    print("\n=== Aggregating ===")
    summarize()


if __name__ == "__main__":
    main()
