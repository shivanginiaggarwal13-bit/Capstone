"""
Clark-Scarf v2 -- Real-Data Validation Suite (optimized)
===============================================================================

The same suite as final_submission/validate_real_data.py: re-test against ACTUAL
replayed 2025 orders (`digital_twin --mode history`), the policy refit every week from
only the data available as of that week. 2023-W01..2025-W01 is an identical warm-up in
every arm; 2025-W02..2025-W52 is the genuinely refit test window.

Stages (via --stage, or all in order if omitted) and their outputs are unchanged:
  actual       the as-generated baseline arm (cached after the first run)
  meio_weekly  MEIO Weekly walk-forward vs actual
  headline     Clark-Scarf v2 at alpha=0.2 vs actual -- the validated real-data result
  alpha_sweep  Clark-Scarf v2 across alpha 0.0-1.0 on real demand
  multiseed    5-seed robustness check of alpha=0.4 and alpha=0.6
  segments     Fast-Moving/Seasonal/Intermediate breakdown of the headline result

What changed is how the work is scheduled, not what is computed:
  * The stages run together. Builds that drew from the same fresh generator in the
    original (headline, multiseed at seed 2026, the alpha sweep's first alpha) share one
    pass of Monte Carlo draws; their policies are identical to separate builds.
  * The alpha sweep's one advancing generator is a chain of builds, each handed the
    state the previous one ended on, so its policies reproduce exactly too.
  * Every twin run is the in-process optimized twin on the shared process pool, and
    returns the window/segment summaries the reports need instead of re-reading a
    200 MB inventory.csv.

    python validate_real_data.py                   # every stage
    python validate_real_data.py --stage headline  # just one stage
    python validate_real_data.py --workers 8
"""
import argparse
import json
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
BASELINE_POLICY_CSV = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "policy"))
SCENARIO_OUT_DIR = scenario.resolve_scenario_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "artifacts", "scenario")
TWIN_OUT_ROOT = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "output_root"))
TWIN_OUT_DIR = os.path.join(TWIN_OUT_ROOT, "projections", SCENARIO_NAME)
RUN_OUT_DIR = os.path.join(SCENARIO_OUT_DIR, "validation_output")  # full validation detail
RESULTS_DIR = os.path.join(SCENARIO_OUT_DIR, "results")             # summaries/charts/reports
STATE_CSV = str(scenario.resolve_path(SCENARIO_CONFIG, SCENARIO_SETTINGS, "digital_twin", "state"))
CLUSTER_CSV = os.path.join(REPO_ROOT, "Clustering & Segmentation", "segmentation_v4", "sku_clusters.csv")

WARMUP_START = "2023-W01"
WARMUP_END = "2025-W01"
TEST_START = "2025-W02"
TEST_END = "2025-W52"
HISTORY_WEEKS = 156
HEADLINE_ALPHA = 0.2
SEED = 2026
MULTISEED_SEEDS = (2026, 7, 42, 123, 2027)
SWEEP_ALPHAS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
N_SIMS = 1200                     # build_walkforward_policy_csv2's n_sims

POLICY_COLS = ["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]
WINDOWS = {"test": (TEST_START, TEST_END), "warmup": (None, WARMUP_END)}

# task priorities on the shared pool: lower runs first
P_CHAIN, P_BASELINE, P_BUILD, P_TWIN = 0, 1, 2, 3
CHAIN_THREADS = 4

_PREPARED = threading.Lock()
_prepared = False


def prepare():
    """The original did this at import; here once, before the first stage runs."""
    global _prepared
    with _PREPARED:
        if not _prepared:
            os.makedirs(SCENARIO_OUT_DIR, exist_ok=True)
            scenario.copy_config(SCENARIO_CONFIG, Path(SCENARIO_OUT_DIR))
            os.makedirs(RESULTS_DIR, exist_ok=True)
            os.makedirs(RUN_OUT_DIR, exist_ok=True)
            _prepared = True


# ------------------------------------------------------------------ groups --
def segment_groups() -> dict:
    sm = rt.read_csv(os.path.join(DEFAULT_DATA, "sku_master.csv"), columns=["sku_id", "segment"])
    out: dict = {}
    for s, g in zip(sm["sku_id"].to_list(), sm["segment"].to_list()):
        if g is not None:
            out.setdefault(g, []).append(s)
    return out


def cluster_groups() -> dict:
    c = rt.read_csv(CLUSTER_CSV, columns=["sku_id", "archetype"])
    out: dict = {}
    for s, a in zip(c["sku_id"].to_list(), c["archetype"].to_list()):
        out.setdefault(a, []).append(s)
    return out


def summary_request(groups: dict | None = None) -> dict:
    """Every history run reports both windows; customer demand/unmet per segment and per
    cluster in the test window (the baselines are shared, so they carry all groups)."""
    g = {f"seg:{k}": v for k, v in segment_groups().items()}
    g.update({f"cl:{k}": v for k, v in cluster_groups().items()})
    g.update(groups or {})
    return {"windows": WINDOWS, "groups": g, "group_windows": ["test"]}


# -------------------------------------------------------------- twin jobs --
# runs whose inventory.csv is read back later (baseline cache reuse, --stage segments): never lean
KEEP_INVENTORY = ("actual", "csv2_headline")


def history_job(run_name, policy_csv, seed=SEED, use_cache=False, groups=None) -> dict:
    """digital_twin --mode history over the full 156-week horizon, as run_twin_history."""
    keep = use_cache or run_name in KEEP_INVENTORY or run_name.startswith("ms_actual_s")
    return {"run_name": run_name, "mode": "history", "horizon_weeks": HISTORY_WEEKS, "policy": str(policy_csv),
            "seed": seed, "data_dir": DEFAULT_DATA, "out_root": TWIN_OUT_ROOT, "scenario_config": str(SCENARIO_CONFIG),
            "label": run_name, "summary": summary_request(groups), "use_cache": use_cache,
            "lean": rt.LEAN_TWIN_OUTPUTS and not keep}


def run_twin_history(ctx, run_name, policy_csv, seed=SEED, groups=None, priority=P_TWIN):
    return ctx.submit(twin.simulate_task, history_job(run_name, policy_csv, seed, groups=groups), priority=priority)


def actual_arm(ctx):
    """The "actual" baseline: cached on disk after the first run, built once per run_all."""
    return ctx.once(("twin", "actual"), twin.simulate_task,
                    history_job("actual", BASELINE_POLICY_CSV, SEED, use_cache=True), priority=P_BASELINE)


def ms_actual_arm(ctx, seed):
    """ms_actual_s{seed}: the per-seed baseline the multi-seed checks share."""
    run = f"ms_actual_s{seed}"
    return ctx.once(("twin", run), twin.simulate_task, history_job(run, BASELINE_POLICY_CSV, seed, use_cache=True),
                    priority=P_BASELINE)


def fill(res: dict, label: str, window: str = "test") -> dict:
    return {"arm": label, **res["summary"]["fill"][window]}


def group_fill(res: dict, group: str, window: str = "test"):
    cd, cu = res["summary"]["groups"][f"{window}|{group}"]
    return round(float(1 - cu / cd), 4) if cd else None


def warmup_parity_check(res_a: dict, res_b: dict) -> bool:
    a, b = res_a["summary"]["sums"]["warmup"], res_b["summary"]["sums"]["warmup"]
    return all(abs(a[c] - b[c]) < 1e-6 for c in a)


def walkforward_job(variants: dict, rng, model="clark_scarf_v2", threads=1) -> dict:
    return {"data_dir": DEFAULT_DATA, "variants": variants, "rng": rng, "n_sims": N_SIMS, "model": model,
            "baseline_policy_csv": BASELINE_POLICY_CSV, "warmup": (WARMUP_START, WARMUP_END),
            "test": (TEST_START, TEST_END), "threads": threads, "lean": rt.LEAN_TWIN_OUTPUTS}


def build(ctx, variants: dict, rng, model="clark_scarf_v2", priority=P_BUILD, threads=1, lane=None):
    """One walk-forward stream: every variant in `variants` on generator `rng`. A `lane`
    pins it to that lane's dedicated process (RNG chains)."""
    return ctx.submit(pb.walkforward_task, walkforward_job(variants, rng, model, threads), priority=priority,
                      lane=lane)


def policy_path(name: str) -> str:
    return os.path.join(RUN_OUT_DIR, name)


def log_build(log, res: dict):
    for line in res.get("log", []):
        log(line)


def write_csv(df: pl.DataFrame, name: str) -> str:
    path = os.path.join(RESULTS_DIR, name)
    rt.write_csv(df, path)
    return path


# ------------------------------------------------------------------ stages --
def run(ctx, log, stages=None):
    """Every stage in `stages` (default all), scheduled together on ctx's pool."""
    prepare()
    stages = list(stages or STAGES)
    if "segments" in stages and "headline" not in stages and \
            not os.path.exists(os.path.join(TWIN_OUT_DIR, "csv2_headline", "inventory.csv")):
        log("  headline stage hasn't run yet -- running it now")
        stages.append("headline")
    want = set(stages)
    actual = actual_arm(ctx)
    if "multiseed" in want:
        for seed in MULTISEED_SEEDS:
            ms_actual_arm(ctx, seed)

    # --- seed-2026 fresh stream: headline, alpha sweep's first link, multiseed @2026
    v26 = {}
    if "headline" in want:
        v26["headline"] = pb.variant_spec(policy_path("policy_csv2_headline.csv"), HEADLINE_ALPHA, 1.0)
    if "alpha_sweep" in want:
        v26["sweep_0"] = pb.variant_spec(policy_path(_sweep_name(SWEEP_ALPHAS[0])), SWEEP_ALPHAS[0], 1.0)
    if "multiseed" in want:
        for a in (0.4, 0.6):
            v26[f"ms_{a}"] = pb.variant_spec(policy_path(f"policy_ms_{_ms_tag(a, SEED)}.csv"), a, 1.0)
    builds = {}
    if v26:
        chained = "alpha_sweep" in want         # the alpha sweep continues this stream's generator
        builds[SEED] = build(ctx, v26, SEED, priority=P_CHAIN if chained else P_BUILD,
                             threads=CHAIN_THREADS if chained else 1, lane="alpha_sweep" if chained else None)
    if "multiseed" in want:
        for seed in MULTISEED_SEEDS:
            if seed != SEED:
                builds[seed] = build(ctx, {f"ms_{a}": pb.variant_spec(policy_path(f"policy_ms_{_ms_tag(a, seed)}.csv"),
                                                                      a, 1.0) for a in (0.4, 0.6)}, seed)
    jobs = {}
    if "meio_weekly" in want:
        jobs["meio_weekly"] = ctx.spawn(_stage_meio_weekly, ctx, log, actual)
    if "headline" in want:
        jobs["headline"] = ctx.spawn(_stage_headline, ctx, log, actual, builds[SEED])
    if "alpha_sweep" in want:
        jobs["alpha_sweep"] = ctx.spawn(_stage_alpha_sweep, ctx, log, actual, builds[SEED])
    if "multiseed" in want:
        jobs["multiseed"] = ctx.spawn(_stage_multiseed, ctx, log, builds)
    if "segments" in want:
        jobs["segments"] = ctx.spawn(_stage_segments, ctx, log, actual, jobs.get("headline"))

    out = {}
    for name in STAGES:                      # report in the original stage order
        if name == "actual" and name in want:
            res = actual.result()
            log("[actual] " + ("already built, reusing cache" if res.get("cached") else
                               f"built in {res['seconds']:.0f}s"))
            out[name] = res
        elif name in jobs:
            out[name] = jobs[name].result()
    return out


def _sweep_name(a):
    return f"policy_sweep_{_alpha_tag(a)}.csv"


def _alpha_tag(a):
    return f"a{a:.1f}".replace(".", "")


def _ms_tag(alpha, seed):
    return f"a{alpha:.1f}_s{seed}".replace(".", "")


def _stage_meio_weekly(ctx, log, actual):
    log("=== Stage: meio_weekly ===")
    res_b = build(ctx, {"meio_weekly": pb.variant_spec(policy_path("policy_meio_weekly.csv"))}, None,
                  model="meio_weekly").result()
    log_build(log, res_b)
    res = run_twin_history(ctx, "meio_weekly", res_b["paths"]["meio_weekly"]).result()
    act = actual.result()
    log(f"  warm-up parity: {warmup_parity_check(act, res)}")
    summary = pl.DataFrame([fill(act, "actual"), fill(res, "meio_weekly")])
    write_csv(summary, "meio_weekly_vs_actual.csv")
    log(rt.show(summary))
    return summary


def _stage_headline(ctx, log, actual, stream):
    res_b = stream.result()
    log("=== Stage: headline (Clark-Scarf v2, alpha=0.2, THE validated real-data result) ===")
    log_build(log, res_b)
    res = run_twin_history(ctx, "csv2_headline", res_b["paths"]["headline"]).result()
    act = actual.result()
    log(f"  warm-up parity: {warmup_parity_check(act, res)}")
    summary = pl.DataFrame([fill(act, "actual"), fill(res, "clark_scarf_v2")])
    write_csv(summary, "headline_clark_scarf_v2_vs_actual.csv")
    cost_rows = [{"metric": k, "actual": act["kpis"].get(k), "clark_scarf_v2": res["kpis"].get(k),
                  "delta_attributable_to_test_window": res["kpis"].get(k) - act["kpis"].get(k)}
                 for k in ["holding_cost_usd", "stockout_cost_usd", "transport_cost_usd", "total_landed_cost_usd"]]
    cost_summary = pl.DataFrame(cost_rows)
    write_csv(cost_summary, "headline_cost_summary.csv")
    log(rt.show(summary))
    log(rt.show(cost_summary))
    return summary, cost_summary, res


def _stage_alpha_sweep(ctx, log, actual, stream):
    """One advancing generator across the six alphas, as the original: link 0 is the
    seed-2026 stream, every later link starts from the state the previous one ended on."""
    res_b = stream.result()
    twins = {SWEEP_ALPHAS[0]: run_twin_history(ctx, f"sweep_{_alpha_tag(SWEEP_ALPHAS[0])}",
                                               res_b["paths"]["sweep_0"])}
    state = res_b["rng_state"]
    for a in SWEEP_ALPHAS[1:]:
        link = build(ctx, {"sweep": pb.variant_spec(policy_path(_sweep_name(a)), a, 1.0)}, state,
                     priority=P_CHAIN, threads=CHAIN_THREADS, lane="alpha_sweep").result()
        state = link["rng_state"]
        twins[a] = run_twin_history(ctx, f"sweep_{_alpha_tag(a)}", link["paths"]["sweep"])
    act = actual.result()
    log("=== Stage: alpha_sweep (Clark-Scarf v2 across alpha 0.0-1.0, real demand) ===")
    rows = []
    for a in SWEEP_ALPHAS:
        res = twins[a].result()
        log(f"  --- alpha={a:.1f} --- ({res['seconds']:.0f}s twin)")
        fr = fill(res, f"alpha={a:.1f}")
        fr["alpha"] = a
        fr["cost_delta_usd"] = res["kpis"]["total_landed_cost_usd"] - act["kpis"]["total_landed_cost_usd"]
        rows.append(fr)
    summary = pl.DataFrame(rows)
    write_csv(summary, "alpha_sweep_real_data.csv")
    log(rt.show(summary))
    return summary


def _stage_multiseed(ctx, log, builds):
    twins = {}
    for seed in MULTISEED_SEEDS:
        res_b = builds[seed].result()
        for a in (0.4, 0.6):
            twins[(a, seed)] = run_twin_history(ctx, f"ms_{_ms_tag(a, seed)}", res_b["paths"][f"ms_{a}"], seed=seed)
    log("=== Stage: multiseed (5-seed check of alpha=0.4 and alpha=0.6) ===")
    rows = []
    for alpha in (0.4, 0.6):
        for seed in MULTISEED_SEEDS:
            log(f"  --- alpha={alpha} seed={seed} ---")
            act = ms_actual_arm(ctx, seed).result()
            res = twins[(alpha, seed)].result()
            fr_actual, fr_csv2 = fill(act, "actual"), fill(res, "csv2")
            rows.append({
                "alpha": alpha, "seed": seed, "netting_discount": 1.0,   # walk-forward default
                "actual_fill": fr_actual["customer_fill_rate"], "csv2_fill": fr_csv2["customer_fill_rate"],
                "fill_delta_pp": (fr_csv2["customer_fill_rate"] - fr_actual["customer_fill_rate"]) * 100,
                "cost_delta_usd": res["kpis"]["total_landed_cost_usd"] - act["kpis"]["total_landed_cost_usd"],
            })
    summary = pl.DataFrame(rows)
    write_csv(summary, "multiseed_alpha_04_06.csv")
    log(rt.show(seed_agg(summary, "alpha")))
    return summary


def seed_agg(summary: pl.DataFrame, key: str, extra: tuple = ()) -> pl.DataFrame:
    """The original's per-setting n_wins / mean / std table (pandas std: ddof=1)."""
    rows = []
    for k in sorted(set(summary[key].to_list())):
        g = summary.filter(pl.col(key) == k)
        f, c = g["fill_delta_pp"].to_numpy(), g["cost_delta_usd"].to_numpy()
        row = {key: k, "n_wins": int((f > 0).sum()), "n_seeds": len(f),
               "fill_delta_mean": rt.nanmean_pandas(f), "fill_delta_std": rt.nanstd_pandas(f)}
        for col, name in extra:
            v = g[col].to_numpy()
            row[f"{name}_mean"], row[f"{name}_std"] = rt.nanmean_pandas(v), rt.nanstd_pandas(v)
        row["cost_delta_mean"], row["cost_delta_std"] = rt.nanmean_pandas(c), rt.nanstd_pandas(c)
        rows.append(row)
    return pl.DataFrame(rows)


def _stage_segments(ctx, log, actual, headline_job):
    if headline_job is not None:
        res = headline_job.result()[2]
    else:
        res = ctx.submit(twin.simulate_task, {**history_job("csv2_headline", policy_path("policy_csv2_headline.csv")),
                                              "use_cache": True}, priority=P_TWIN).result()
    act = actual.result()
    log("=== Stage: segments (Fast-Moving/Seasonal/Intermediate breakdown of the headline result) ===")
    segs = sorted(segment_groups())
    rows = [{"run": label, **{s: group_fill(r, f"seg:{s}") for s in segs}}
            for label, r in [("actual", act), ("clark_scarf_v2", res)]]
    piv = pl.DataFrame(rows).sort("run")
    write_csv(piv, "segment_breakdown_real_data.csv")
    log(rt.show(piv))
    return piv


STAGES = ["actual", "meio_weekly", "headline", "alpha_sweep", "multiseed", "segments"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=STAGES, default=None, help="Run just one stage; omit to run all in order.")
    ap.add_argument("--workers", type=int, default=None, help="Process-pool size (default: CPUs - 2).")
    ap.add_argument("--lean-twin-outputs", action="store_true",
                    help="Skip the large per-run detail CSVs (see runtime.LEAN_TWIN_OUTPUTS).")
    args = ap.parse_args(argv)
    rt.LEAN_TWIN_OUTPUTS = args.lean_twin_outputs
    ctx = rt.Context(args.workers)
    log = rt.StepLog()
    try:
        run(ctx, log, [args.stage] if args.stage else None)
        twin.write_output_documentation(Path(TWIN_OUT_ROOT))
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
