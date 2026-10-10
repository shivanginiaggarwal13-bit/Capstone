"""
Clark-Scarf v2 -- Run Everything (optimized)
===============================================================================

Runs the same nine research/validation steps as final_submission/run_all.py and writes
the same results files, in roughly half an hour instead of ~10 hours. Each step's
output goes to its own log under `run_logs/`, a failure in one step does not stop the
rest, and the summary reports what succeeded, what failed and how long each step took.

    1. validate_real_data.py
    2. netting_discount_sweep.py --alpha 0.2
    3. netting_discount_sweep.py --alpha 0.6
    4. netting_discount_sweep.py --multiseed
    5. segment_alpha_explore.py
    6. segment_alpha_real_data.py
    7. service_level_real_data.py
    8. scenario_test.py --scenario ../../../twin/scenario_config.example.json
    9. netting_discount_sweep.py --multiseed --alpha 0.6

Where the time went, and what replaced it:
  * Every policy build re-derived the same demand estimates (~300 pandas filter/groupby
    passes over 739k rows per build) -> a dense demand panel, cached per process.
  * Every variant of a sweep redrew the same Monte Carlo variates -> one pass of draws
    per RNG stream, every variant read off it (about 40 builds instead of ~230).
  * Every twin run was a fresh interpreter that re-read the inputs, looped over 1.2M
    rows in Python and wrote ~250 MB of CSV with pandas -> an in-process, vectorised
    twin writing with polars (~5 s per 156-week run).
  * Steps ran one after another, each with its own small pool -> all nine steps share
    one process pool and run at once, the RNG chains at top priority in their own warm
    worker processes so they never queue behind twin runs.
Results are bit-identical to the original pipeline (see OPTIMIZATION.md for what was
verified and the one step that needed a fix to run at all).

The longest path is step 5: the original passes one advancing generator through its 24
rolling builds, so they must run in sequence (~24 x 52 weeks of gamma draws).
`--explore-fresh-rng` gives each of those builds a fresh generator instead (the fix
segment_alpha_real_data already uses); that takes several minutes off but means step 5
no longer reproduces the published segment_alpha_sweep_*.csv.

    python run_all.py                  # everything
    python run_all.py --from 6         # skip 1-5
    python run_all.py --steps 5,6,7    # only selected steps
    python run_all.py --workers 8      # pool size (default: CPUs - 2)
    python run_all.py --lean-twin-outputs   # ~5 GB on disk instead of ~70 GB

Disk: like the original, a full run writes every twin run's detail tables -- about
60 GB of twin outputs plus ~11 GB of walk-forward policy files. Check free space first,
or pass --lean-twin-outputs.
"""
import argparse
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                       # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "run_logs")

STEPS = [
    ("validate_real_data.py", []),
    ("netting_discount_sweep.py", ["--alpha", "0.2"]),
    ("netting_discount_sweep.py", ["--alpha", "0.6"]),
    ("netting_discount_sweep.py", ["--multiseed"]),
    ("segment_alpha_explore.py", []),
    ("segment_alpha_real_data.py", []),
    ("service_level_real_data.py", []),
    ("scenario_test.py", ["--scenario", "../../../twin/scenario_config.example.json"]),
    ("netting_discount_sweep.py", ["--multiseed", "--alpha", "0.6"]),
]
# Steps 4 and 9 name their policy files and twin runs netting_ms_nd{..}_s{seed} without
# the alpha, so step 9 overwrites step 4's. Run in sequence, as the original did.
AFTER = {9: 4}
# Submission order: the long RNG chains first so their first links start at once.
ORDER = [5, 1, 6, 7, 4, 2, 3, 8, 9]


def _driver(n, ctx, log, explore_fresh_rng):
    import validate_real_data as vrd
    import netting_discount_sweep as nds
    import segment_alpha_explore as sae
    import segment_alpha_real_data as sard
    import service_level_real_data as slrd
    import scenario_test as st
    if n == 1:
        vrd.run(ctx, log)
    elif n in (2, 3):
        nds.run(ctx, log, alpha=float(STEPS[n - 1][1][1]))
    elif n == 4:
        nds.run(ctx, log, multiseed=True)
    elif n == 5:
        sae.run(ctx, log, fresh_rng=explore_fresh_rng)
    elif n == 6:
        sard.run(ctx, log)
    elif n == 7:
        slrd.run(ctx, log)
    elif n == 8:
        st.run(ctx, log, os.path.normpath(os.path.join(SCRIPT_DIR, STEPS[7][1][1])))
    elif n == 9:
        nds.run(ctx, log, alpha=0.6, multiseed=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", type=int, default=1, help="1-indexed step to start from.")
    ap.add_argument("--steps", default=None, help="Comma-separated 1-indexed steps to run, e.g. 5,6,7.")
    ap.add_argument("--workers", type=int, default=None, help="Process-pool size (default: CPUs - 2).")
    ap.add_argument("--lean-twin-outputs", action="store_true",
                    help="Research twin runs skip inventory.csv / replenishment_receipts.csv / production.csv "
                         "(~250 MB each, ~60 GB per run_all) unless a later step reads them back. Default: write "
                         "them, as the original did.")
    ap.add_argument("--explore-fresh-rng", action="store_true",
                    help="Step 5 with a fresh generator per build: faster, but does not reproduce the "
                         "published segment_alpha_sweep_*.csv (see segment_alpha_explore.py).")
    args = ap.parse_args()
    selected = {int(step) for step in args.steps.split(",")} if args.steps else None
    if selected and not selected.issubset(range(1, len(STEPS) + 1)):
        ap.error(f"--steps must contain values from 1 to {len(STEPS)}")
    os.makedirs(LOG_DIR, exist_ok=True)
    rt.LEAN_TWIN_OUTPUTS = args.lean_twin_outputs

    run = []
    for i, (script, step_args) in enumerate(STEPS, 1):
        if selected is not None and i not in selected:
            print(f"=== [{i}/{len(STEPS)}] {script} -- skipped (--steps {args.steps}) ===")
        elif i < args.start:
            print(f"=== [{i}/{len(STEPS)}] {script} -- skipped (--from {args.start}) ===")
        else:
            run.append(i)

    ctx = rt.Context(args.workers)
    print(f"\nRunning {len(run)} step(s) concurrently on a {ctx.workers}-worker pool", flush=True)
    t0 = time.time()
    results, done = {}, {i: threading.Event() for i in run}
    lock = threading.Lock()

    def step(n):
        script, step_args = STEPS[n - 1]
        label = f"{script} {' '.join(step_args)}".strip()
        dep = AFTER.get(n)
        if dep in done:
            done[dep].wait()
        log_path = os.path.join(LOG_DIR, f"{n:02d}_{script.replace('.py', '')}.log")
        log = rt.StepLog(log_path, echo=False)
        with lock:
            print(f"=== [{n}/{len(STEPS)}] {label}  --  started, log: {log_path}", flush=True)
        t1 = time.time()
        ok = True
        try:
            _driver(n, ctx, log, args.explore_fresh_rng)
        except BaseException as e:             # noqa: BLE001 -- one step's failure must not stop the rest
            ok = False
            log.exception(e)
        finally:
            log.close()
            done[n].set()
        elapsed_min = (time.time() - t1) / 60
        with lock:
            print(f"  [{n}] {'OK' if ok else 'FAILED'} in {elapsed_min:.1f} min  {label}", flush=True)
        results[n] = {"step": n, "label": label, "ok": ok, "elapsed_min": elapsed_min, "log": log_path}

    try:
        threads = [threading.Thread(target=step, args=(n,), name=f"step{n}") for n in ORDER if n in done]
        for t in threads:
            t.start()
            time.sleep(0.2)                    # keep the submission order: chains first
        for t in threads:
            t.join()
        import digital_twin_fast as twin
        import validate_real_data as vrd
        twin.write_output_documentation(Path(vrd.TWIN_OUT_ROOT))
    finally:
        ctx.close()

    total_min = (time.time() - t0) / 60
    print(f"\n{'=' * 70}\nSUMMARY -- total wall time {total_min:.1f} min ({total_min / 60:.2f} hr)\n{'=' * 70}")
    for n in sorted(results):
        r = results[n]
        print(f"  [{r['step']}] {'OK  ' if r['ok'] else 'FAIL'}  {r['elapsed_min']:7.1f} min  {r['label']}")
    n_fail = sum(1 for r in results.values() if not r["ok"])
    if n_fail:
        print(f"\n{n_fail} step(s) failed -- check the logs listed above for details.")
        sys.exit(1)
    print("\nAll steps completed successfully.")


if __name__ == "__main__":
    main()
