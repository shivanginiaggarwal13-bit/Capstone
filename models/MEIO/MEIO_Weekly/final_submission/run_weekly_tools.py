"""
MEIO Weekly — Run the Production Tools, Once Each
===============================================================================

Runs the configured `meio_optimizer_weekly.py` and `roll_forward_policy.py` --
the weekly-cadence operational tools, NOT the research/validation stages in
`run_all.py`. These two
are meant to produce a single current snapshot each time, not loop or
sweep, so this file just calls each once and reports what it wrote,
instead of repeating them.

Both read the same scenario config -- the shared root `scenario_config.json` by
default, or the one passed with --scenario-config (an agent-built scenario under
`output datasets/overrides/`) -- and write into `output datasets/MEIO/<scenario>/`
(see each script's own docstring for the exact files). The as-of week comes from
--as-of-week, else the config's `meio.as_of_week`, else each tool's own default.

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
                     help="Passed to both tools; default: the config's meio.as_of_week.")
    ap.add_argument("--scenario-config", type=Path, default=SCENARIO_CONFIG,
                     help="Scenario JSON both tools read (default: the shared root config).")
    args = ap.parse_args()
    config_path = args.scenario_config.resolve()
    config = scenario.load_config(config_path)
    weekly_script = scenario.resolve_path(config_path, config, "meio", "weekly_entrypoint")
    rolling_script = scenario.resolve_path(config_path, config, "meio", "rolling_entrypoint")
    os.makedirs(LOG_DIR, exist_ok=True)

    as_of_week = args.as_of_week or config.get("meio", {}).get("as_of_week")
    extra = (["--as-of-week", as_of_week] if as_of_week else []) + ["--scenario-config", str(config_path)]
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
    print(f"\nBoth weekly tools completed. Output: output datasets/MEIO/{scenario.scenario_folder_name(config)}/")


if __name__ == "__main__":
    main()
