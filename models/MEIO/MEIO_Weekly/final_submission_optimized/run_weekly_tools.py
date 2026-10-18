"""
MEIO Weekly -- Run the Production Tools, Once Each (optimized)
===============================================================================

Runs this folder's `meio_optimizer_weekly.py` and `roll_forward_policy.py` once each
and reports what they wrote, as final_submission/run_weekly_tools.py does. The scenario
config's `meio.weekly_entrypoint` / `rolling_entrypoint` name the original folder's
scripts; this runner always uses the optimized ones next to it, one after the other
(both write the scenario's config copy and override receipts), each logging to run_logs/.

    python run_weekly_tools.py
    python run_weekly_tools.py --scenario-config "output datasets/overrides/SCENARIO_0003_Name/scenario_config.json"
    python run_weekly_tools.py --as-of-week 2025-W52   # passed to both
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                     # noqa: E402
import scenario                          # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = str(rt.REPO_ROOT)
SCENARIO_CONFIG = Path(REPO_ROOT) / "scenario_config.json"
LOG_DIR = os.path.join(SCRIPT_DIR, "run_logs")
PY = sys.executable


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default=None, help="Passed to both tools; default: the config's meio.as_of_week.")
    ap.add_argument("--scenario-config", type=Path, default=SCENARIO_CONFIG,
                    help="Scenario JSON both tools read (default: the shared root config).")
    args = ap.parse_args()
    config_path = args.scenario_config.resolve()
    config = scenario.load_config(config_path)
    os.makedirs(LOG_DIR, exist_ok=True)

    as_of_week = args.as_of_week or config.get("meio", {}).get("as_of_week")
    extra = (["--as-of-week", as_of_week] if as_of_week else []) + ["--scenario-config", str(config_path)]
    steps = [(Path(SCRIPT_DIR) / "meio_optimizer_weekly.py", extra), (Path(SCRIPT_DIR) / "roll_forward_policy.py", extra)]

    t0 = time.time()
    results = []
    for i, (script, step_args) in enumerate(steps, 1):
        log_path = os.path.join(LOG_DIR, f"{i:02d}_{script.stem}.log")
        print(f"\n=== [{i}/{len(steps)}] {script} {' '.join(step_args)}  --  log: {log_path} ===", flush=True)
        t1 = time.time()
        with open(log_path, "w") as log_f:      # in sequence: both write the scenario's config copy and receipts
            code = subprocess.run([PY, str(script), *step_args], cwd=REPO_ROOT, stdout=log_f,
                                  stderr=subprocess.STDOUT).returncode
        elapsed = time.time() - t1
        print(f"  {'OK' if code == 0 else f'FAILED (exit {code})'} in {elapsed:.0f}s", flush=True)
        results.append((script, code == 0, elapsed))

    total = time.time() - t0
    print(f"\n{'=' * 60}\nSUMMARY -- total wall time {total:.0f}s\n{'=' * 60}")
    for script, ok, elapsed in results:
        print(f"  {'OK  ' if ok else 'FAIL'}  {elapsed:6.0f}s  {script.name}")
    if any(not ok for _, ok, _ in results):
        sys.exit(1)
    print(f"\nBoth weekly tools completed. Output: output datasets/MEIO/{scenario.scenario_folder_name(config)}/")


if __name__ == "__main__":
    main()
