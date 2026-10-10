"""
Clark-Scarf v2 — Run Everything, Overnight
===============================================================================

Runs this folder's four research/validation scripts back to back, instead of
invoking each by hand. Meant for an unattended overnight run: each step's
full stdout/stderr is captured to its own log file under `run_logs/`
(gitignored scratch, not the `results/` deliverable), and a failure in one
step does NOT stop the rest -- these scripts already handle most of their
own caching/resumability, so one broken step shouldn't waste the whole
night. A final summary prints what succeeded, what failed, and how long
each step took.

Order matters for cache reuse, not just correctness:
  1. validate_real_data.py   -- also builds the "actual" (single-seed) and
     "ms_actual_s{2026,7,42,123,2027}" (5-seed) baseline caches that steps
     2, 3, 4, 6 and 7 below reuse for free instead of rebuilding from scratch.
  2. netting_discount_sweep.py --alpha 0.2
  3. netting_discount_sweep.py --alpha 0.6     (not yet run as of this file)
  4. netting_discount_sweep.py --multiseed
  5. segment_alpha_explore.py                  (synthetic basis, independent
     of the real-data caches above)
  6. segment_alpha_real_data.py                (real data, parallel)
  7. service_level_real_data.py                (real data, parallel -- the
     Feature 1 service-level-by-cluster sweep; runs alongside step 6 since
     both are the longest steps, so a shorter night still finishes 1-5)
  8. scenario_test.py                           (Feature 2 demand-surge
     scenario test, ~10-15 min, fast enough to run last regardless)
  9. netting_discount_sweep.py --multiseed --alpha 0.6
                                                (5-seed netting check at the real-data
     fill-optimal alpha. Alpha 0.4/0.6 were only multi-seed tested at netting 1.0, so
     until this runs, meio_parameters.py keeps the pipeline at alpha 0.2.)

What the pipeline uses: meio_parameters.py reads these steps' results/ CSVs (from
`<artifacts.scenario>/results/`, falling back to the committed copy in this folder)
and picks the parameters roll_forward_policy.py builds the policy with. See the
"validated" rule in its docstring and meio_parameters.json next to each policy.

Resumable steps (segment_alpha_real_data.py, and anything reusing an
already-cached baseline) pick up where they left off if this is re-run, so
it's safe to stop (Ctrl-C) overnight and resume the next night.

    python run_all.py                  # everything, in order
    python run_all.py --from 6         # skip 1-5, go straight to step 6
    python run_all.py --steps 5,6,7    # run only selected steps
"""
import argparse
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "run_logs")
PY = sys.executable

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


def run_step(n, total, script, step_args):
    label = f"{script} {' '.join(step_args)}".strip()
    log_path = os.path.join(LOG_DIR, f"{n:02d}_{script.replace('.py', '')}.log")
    print(f"\n=== [{n}/{total}] {label}  --  log: {log_path} ===", flush=True)
    t0 = time.time()
    with open(log_path, "w") as log_f:
        proc = subprocess.run([PY, os.path.join(SCRIPT_DIR, script), *step_args],
                               cwd=SCRIPT_DIR, stdout=log_f, stderr=subprocess.STDOUT)
    elapsed_min = (time.time() - t0) / 60
    ok = proc.returncode == 0
    print(f"  {'OK' if ok else f'FAILED (exit {proc.returncode})'} in {elapsed_min:.1f} min", flush=True)
    return {"step": n, "label": label, "ok": ok, "elapsed_min": elapsed_min, "log": log_path}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", type=int, default=1,
                     help="1-indexed step to start from (skip earlier steps).")
    ap.add_argument("--steps", default=None,
                     help="Comma-separated 1-indexed steps to run, e.g. 5,6,7.")
    args = ap.parse_args()
    selected = {int(step) for step in args.steps.split(",")} if args.steps else None
    if selected and not selected.issubset(range(1, len(STEPS) + 1)):
        ap.error(f"--steps must contain values from 1 to {len(STEPS)}")
    os.makedirs(LOG_DIR, exist_ok=True)

    t0 = time.time()
    results = []
    for i, (script, step_args) in enumerate(STEPS, 1):
        if selected is not None and i not in selected:
            print(f"=== [{i}/{len(STEPS)}] {script} -- skipped (--steps {args.steps}) ===")
            continue
        if i < args.start:
            print(f"=== [{i}/{len(STEPS)}] {script} -- skipped (--from {args.start}) ===")
            continue
        results.append(run_step(i, len(STEPS), script, step_args))

    total_min = (time.time() - t0) / 60
    print(f"\n{'=' * 70}\nSUMMARY -- total wall time {total_min:.1f} min ({total_min / 60:.1f} hr)\n{'=' * 70}")
    for r in results:
        print(f"  [{r['step']}] {'OK  ' if r['ok'] else 'FAIL'}  {r['elapsed_min']:7.1f} min  {r['label']}")
    n_fail = sum(1 for r in results if not r["ok"])
    if n_fail:
        print(f"\n{n_fail} step(s) failed -- check the logs listed above for details.")
        sys.exit(1)
    print("\nAll steps completed successfully.")


if __name__ == "__main__":
    main()
