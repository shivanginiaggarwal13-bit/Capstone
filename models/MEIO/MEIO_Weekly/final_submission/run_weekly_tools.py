"""
MEIO Weekly — Run the Production Tools, Once Each
===============================================================================

Runs the configured `meio_optimizer_weekly.py` and `roll_forward_policy.py` --
the weekly-cadence operational tools, NOT the research/validation stages in
`run_all.py`. These two
are meant to produce a single current snapshot each time, not loop or
sweep, so this file just calls each once and reports what it wrote,
instead of repeating them.

Both write into the repo-tracked `output datasets/MEIO/` (see each script's
own docstring for the exact files).

    python run_weekly_tools.py
    python run_weekly_tools.py --as-of-week 2025-W52   # passed to both
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "models")))
import scenario

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
SCENARIO_CONFIG = Path(REPO_ROOT) / "scenario_config.json"
LOG_DIR = os.path.join(SCRIPT_DIR, "run_logs")
PY = sys.executable


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default=None,
                     help="Passed to both tools; omit to use each tool's own default.")
    args = ap.parse_args()
    config = scenario.load_config(SCENARIO_CONFIG)
    weekly_script = scenario.resolve_path(SCENARIO_CONFIG, config, "meio", "weekly_entrypoint")
    rolling_script = scenario.resolve_path(SCENARIO_CONFIG, config, "meio", "rolling_entrypoint")
    os.makedirs(LOG_DIR, exist_ok=True)

    extra = ["--as-of-week", args.as_of_week] if args.as_of_week else []
    steps = [(weekly_script, extra), (rolling_script, extra)]

    t0 = time.time()
    results = []
    for i, (script, step_args) in enumerate(steps, 1):
        label = f"{script} {' '.join(step_args)}".strip()
        log_path = os.path.join(LOG_DIR, f"{i:02d}_{Path(script).stem}.log")
        print(f"\n=== [{i}/{len(steps)}] {label}  --  log: {log_path} ===", flush=True)
        t1 = time.time()
        with open(log_path, "w") as log_f:
            proc = subprocess.run([PY, str(script), *step_args],
                                   cwd=REPO_ROOT, stdout=log_f, stderr=subprocess.STDOUT)
        elapsed = time.time() - t1
        ok = proc.returncode == 0
        print(f"  {'OK' if ok else f'FAILED (exit {proc.returncode})'} in {elapsed:.0f}s", flush=True)
        results.append((script, ok, elapsed))

    total = time.time() - t0
    print(f"\n{'=' * 60}\nSUMMARY -- total wall time {total:.0f}s\n{'=' * 60}")
    for script, ok, elapsed in results:
        print(f"  {'OK  ' if ok else 'FAIL'}  {elapsed:6.0f}s  {Path(script).name}")
    if any(not ok for _, ok, _ in results):
        sys.exit(1)
    print("\nBoth weekly tools completed. Output: output datasets/MEIO/")


if __name__ == "__main__":
    main()
