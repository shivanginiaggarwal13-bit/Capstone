"""
Clark-Scarf v2 — Per-Segment Alpha Exploration, REAL DATA + Multi-Seed
===============================================================================

The real-data, multi-seed version of `segment_alpha_explore.py`'s diagnostic
-- flagged in that script's docstring as "the full real-data equivalent ...
would take ~9 hours and hasn't been run." This is that run.

Design: identical sweep to `segment_alpha_explore.py` (for each of the 4
Clustering & Segmentation deliverable's clusters -- Flagship Promo-Driven,
Mid-Tier Core, Long-Tail Erratic, Long-Tail Supply-Risk -- sweep its own
alpha over {0.0, 0.2, 0.4, 0.6, 0.8, 1.0} while holding the other three
clusters at REFERENCE_ALPHA=0.2), but:
  - against REAL replayed 2025 demand (`validate_real_data.py`'s
    walk-forward methodology: 105-week identical warm-up + 51-week
    genuinely-refit test window, no lookahead), not the synthetic
    rolling-2026 basis, and
  - across 5 seeds per (cluster, alpha) combo, not 1 -- the same bar the
    portfolio-wide alpha=0.4/0.6 finding needed before being upgraded from
    "promising" to "validated" in `validate_real_data.py::stage_multiseed`.

Fresh seed per combo, not one shared/advancing rng: a bug caught this
session (see `netting_discount_sweep.py`'s docstring) showed that sharing
one RNG sequentially across a sweep contaminates results with stream-
position drift, sometimes bigger than the effect being measured. Every
combo here gets `np.random.default_rng(seed)` fresh.

Scale and dedup: 4 clusters x 6 alphas x 5 seeds = 120 logical combos, but
alpha=REFERENCE_ALPHA is identical regardless of which cluster is nominally
being "tested" (every SKU sits at 0.2 either way) -- those are computed
ONCE per seed and sliced into all 4 clusters' own-fill numbers, not rebuilt
4 times. That's 4 clusters x 5 non-reference alphas x 5 seeds + 5 reference
builds = 105 actual walk-forward builds + twin runs.

PARALLEL: each combo is fully independent (own policy build, own twin run,
own output files), so this runs a process pool over the 100 non-reference
combos. At ~5-7 min/combo serially (~9-12 hours total), N workers gives
roughly an N-x speedup up to core/memory limits -- see `--workers`.
Three things had to be handled for this to be safe, not just fast:
  1. Concurrent workers can't all append to one shared results CSV (race:
     last writer wins, earlier rows silently lost). Each combo instead
     writes its own small file under `validation_output/segment_alpha_
     partial/`; a cheap serial aggregation step at the end (or via
     --summarize-only) merges them into the tracked results CSV.
  2. The 5 `ms_actual_s{seed}` baselines are shared across many combos --
     built serially in a pre-flight pass BEFORE the pool starts, so two
     workers never race to build the same one.
  3. Thread-limiting env vars (OMP/OPENBLAS/MKL/VECLIB/NUMEXPR_NUM_THREADS=1)
     are set before the pool is created, so numpy's BLAS backend (Accelerate
     on this machine) doesn't ALSO spawn its own threads per worker --
     without this, N processes x M internal threads can oversubscribe the
     8 physical cores and run slower than serial, not faster.

RESUMABLE either way: a combo already on disk (partial file present) is
skipped, so it's safe to Ctrl-C and re-run the same command later.

    python segment_alpha_real_data.py                            # all combos, default 4 workers
    python segment_alpha_real_data.py --workers 6                # more parallelism (watch memory)
    python segment_alpha_real_data.py --workers 1                # serial (original behavior)
    python segment_alpha_real_data.py --cluster "Flagship Promo-Driven"
    python segment_alpha_real_data.py --summarize-only           # just re-aggregate what's on disk
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate_real_data as vrd
import segment_alpha_explore as sae

RESULTS_DIR = vrd.RESULTS_DIR
RUN_OUT_DIR = vrd.RUN_OUT_DIR
TWIN_OUT_DIR = vrd.TWIN_OUT_DIR
TEST_START, TEST_END = vrd.TEST_START, vrd.TEST_END

CLUSTERS = sae.CLUSTERS
ALPHAS = sae.ALPHAS
REFERENCE_ALPHA = sae.REFERENCE_ALPHA
SEEDS = vrd.MULTISEED_SEEDS
DEFAULT_WORKERS = 4   # 8 physical cores / 8GB RAM measured on this machine; ~850MB-1GB observed per
                       # worker, so 4 leaves headroom for the OS and each worker's twin subprocess.

PARTIAL_DIR = os.path.join(RUN_OUT_DIR, "segment_alpha_partial")   # gitignored scratch, one file/combo
OUT_CSV = os.path.join(RESULTS_DIR, "segment_alpha_real_data_multiseed.csv")   # tracked, aggregated
SUMMARY_CSV = os.path.join(RESULTS_DIR, "segment_alpha_real_data_summary.csv")


def _partial_path(cluster, alpha, seed):
    tag = (cluster.replace(" ", "") + f"_a{alpha:.1f}_s{seed}").replace(".", "")
    return os.path.join(PARTIAL_DIR, f"{tag}.json")


def _partial_done(cluster, alpha, seed):
    return os.path.exists(_partial_path(cluster, alpha, seed))


def _write_partial(row):
    os.makedirs(PARTIAL_DIR, exist_ok=True)
    path = _partial_path(row["cluster"], row["alpha"], row["seed"])
    with open(path, "w") as f:
        json.dump(row, f)


def _aggregate_partials():
    rows = []
    if os.path.isdir(PARTIAL_DIR):
        for fn in sorted(os.listdir(PARTIAL_DIR)):
            if fn.endswith(".json"):
                with open(os.path.join(PARTIAL_DIR, fn)) as f:
                    rows.append(json.load(f))
    out = pd.DataFrame(rows)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    return out


def _actual_arm(seed):
    """Same ms_actual_s{seed} naming as validate_real_data.py/netting_discount_sweep.py's
    multiseed checks -- reuses their cached baselines when present. Called only
    from the serial pre-flight (see run_all) to avoid a build race between workers."""
    run_name = f"ms_actual_s{seed}"
    out_dir = os.path.join(TWIN_OUT_DIR, run_name)
    if os.path.exists(os.path.join(out_dir, "run_kpis.json")):
        with open(os.path.join(out_dir, "run_kpis.json")) as f:
            kpi = json.load(f)
        inv = pd.read_csv(os.path.join(out_dir, "inventory.csv"))
        return kpi, inv
    return vrd.run_twin_history(run_name, vrd.BASELINE_POLICY_CSV, seed=seed)


def simulate_combo(cluster_skus_or_none, alpha, seed, tag):
    """Builds + simulates ONE combo (per-cluster override, or the shared
    uniform-0.2 reference when cluster_skus_or_none is None). Returns raw
    kpi/inv pieces; caller extracts whatever cluster's stats it needs.
    Assumes ms_actual_s{seed} already exists on disk (see pre-flight)."""
    actual_kpi, inv_actual = _actual_arm(seed)
    rng = np.random.default_rng(seed)
    override = {} if cluster_skus_or_none is None else {sku: alpha for sku in cluster_skus_or_none}
    policy_csv = vrd.build_walkforward_policy_csv2(
        vrd.DEFAULT_DATA, os.path.join(RUN_OUT_DIR, f"policy_sreal_{tag}.csv"),
        alpha=REFERENCE_ALPHA, rng=rng, alpha_override=override)
    kpi, inv = vrd.run_twin_history(f"sreal_{tag}", policy_csv, seed=seed)
    test_actual = inv_actual[(inv_actual.period >= TEST_START) & (inv_actual.period <= TEST_END)]
    test_inv = inv[(inv.period >= TEST_START) & (inv.period <= TEST_END)]
    return kpi, actual_kpi, test_inv, test_actual


def cluster_stats(test_inv, test_actual, cluster_skus, kpi, actual_kpi):
    fr_actual = vrd.fill_rate_kpis(test_actual, "actual")
    fr = vrd.fill_rate_kpis(test_inv, "csv2")
    own = test_inv[test_inv.sku_id.isin(cluster_skus)]
    cd, cu = own.demand_customer_qty.sum(), own.unmet_customer_qty.sum()
    own_fill = round(float(1 - cu / cd), 4) if cd else None
    return {
        "portfolio_fill_rate": fr["customer_fill_rate"],
        "own_fill_rate": own_fill,
        "internal_replen_fill_rate": fr["internal_replen_fill_rate"],
        "fill_delta_vs_actual_pp": round((fr["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100, 3),
        "cost_delta_usd": kpi["total_landed_cost_usd"] - actual_kpi["total_landed_cost_usd"],
    }


def cluster_map_skus(cluster_map, cluster):
    return [sku for sku, arch in cluster_map.items() if arch == cluster]


def _worker(job):
    """Top-level (picklable) function for ProcessPoolExecutor. One job =
    one non-reference (cluster, alpha, seed) combo."""
    cluster, alpha, seed = job
    if _partial_done(cluster, alpha, seed):
        return f"[skip, already done] {cluster} alpha={alpha} seed={seed}"
    cluster_map = sae.load_cluster_map()
    cluster_skus = cluster_map_skus(cluster_map, cluster)
    tag = (cluster.replace(" ", "") + f"_a{alpha:.1f}_s{seed}").replace(".", "")
    kpi, actual_kpi, test_inv, test_actual = simulate_combo(cluster_skus, alpha, seed, tag)
    row = {"cluster": cluster, "alpha": alpha, "seed": seed, "netting_discount": 1.0,   # vrd's default
           **cluster_stats(test_inv, test_actual, cluster_skus, kpi, actual_kpi)}
    _write_partial(row)
    return f"[done] {cluster} alpha={alpha:.1f} seed={seed}  own_fill={row['own_fill_rate']}"


def _limit_blas_threads():
    """Call BEFORE creating the process pool (not inside a worker) --
    spawned children inherit the parent's environment at creation time and
    re-import numpy fresh, so this must land before ProcessPoolExecutor()."""
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
              "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[v] = "1"


def run_all(clusters_to_run, workers):
    cluster_map = sae.load_cluster_map()

    # --- Serial pre-flight: cache all 5 actual baselines + the 5 reference
    # combos (each sliced into all 4 clusters' own-fill). Must be serial --
    # these are shared across many later jobs, so two parallel workers
    # racing to build the same one would corrupt each other's output.
    for seed in SEEDS:
        if all(_partial_done(c, REFERENCE_ALPHA, seed) for c in CLUSTERS):
            print(f"  [skip, already done] reference seed={seed}")
            continue
        print(f"--- reference (all clusters at {REFERENCE_ALPHA}) seed={seed} ---")
        kpi, actual_kpi, test_inv, test_actual = simulate_combo(None, REFERENCE_ALPHA, seed, f"reference_s{seed}")
        for cluster in CLUSTERS:
            if _partial_done(cluster, REFERENCE_ALPHA, seed):
                continue
            row = {"cluster": cluster, "alpha": REFERENCE_ALPHA, "seed": seed, "netting_discount": 1.0,
                   **cluster_stats(test_inv, test_actual, cluster_map_skus(cluster_map, cluster), kpi, actual_kpi)}
            _write_partial(row)

    # --- Parallel: the 100 non-reference (cluster, alpha, seed) jobs.
    jobs = [(cluster, alpha, seed)
            for cluster in clusters_to_run
            for alpha in ALPHAS if alpha != REFERENCE_ALPHA
            for seed in SEEDS
            if not _partial_done(cluster, alpha, seed)]
    print(f"\n{len(jobs)} non-reference combos to run, {workers} worker(s)")
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
        for alpha in ALPHAS:
            g = final[(final.cluster == cluster) & (final.alpha == alpha)]
            if not len(g):
                continue
            rows.append({
                "cluster": cluster, "alpha": alpha, "n_seeds": len(g),
                "own_fill_mean": g.own_fill_rate.mean(), "own_fill_std": g.own_fill_rate.std(),
                "portfolio_fill_mean": g.portfolio_fill_rate.mean(),
                "internal_replen_mean": g.internal_replen_fill_rate.mean(),
                "cost_delta_mean": g.cost_delta_usd.mean(), "cost_delta_std": g.cost_delta_usd.std(),
            })
    summary = pd.DataFrame(rows)
    summary.to_csv(SUMMARY_CSV, index=False)
    print(summary.to_string(index=False))

    print("\n=== Best alpha per cluster, by OWN fill rate (mean across seeds so far) ===")
    for cluster in CLUSTERS:
        sub = summary[summary.cluster == cluster]
        if not len(sub):
            continue
        best = sub.loc[sub.own_fill_mean.idxmax()]
        spread = (sub.own_fill_mean.max() - sub.own_fill_mean.min()) * 100
        print(f"  {cluster:24s}: best alpha={best.alpha:.1f} (own_fill={best.own_fill_mean:.4f}), "
              f"spread {spread:.2f}pp across the sweep")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cluster", default=None, choices=CLUSTERS, help="Run just one cluster; omit for all 4.")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                     help=f"Parallel worker processes for the non-reference combos (default {DEFAULT_WORKERS}). "
                          f"1 = serial.")
    ap.add_argument("--summarize-only", action="store_true",
                     help="Skip running; just (re)aggregate whatever's already on disk.")
    args = ap.parse_args()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    if not args.summarize_only:
        clusters_to_run = [args.cluster] if args.cluster else CLUSTERS
        run_all(clusters_to_run, args.workers)

    print("\n=== Aggregating ===")
    summarize()


if __name__ == "__main__":
    main()
