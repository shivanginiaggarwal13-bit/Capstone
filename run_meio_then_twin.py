"""Run the configured MEIO policy build, then score it in the Digital Twin."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from models import scenario


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "scenario_config.json"


def scenario_paths(config_path: Path, config: dict) -> tuple[Path, Path]:
    """What this runner writes, and so what it clears: the scenario's MEIO policy folder
    and its one Twin run. It leaves alone the MEIO artifacts (run_all.py's tuning
    evidence, which meio_parameters.py reads to build the policy, and its cached Twin
    runs under `artifacts.twin_output_root`) and the other runs under
    projections/<scenario>/ (a scenario's other arm)."""
    name = scenario.scenario_folder_name(config)
    policy_dir = scenario.resolve_path(config_path, config, "meio", "output_root") / name
    twin_run = (scenario.resolve_path(config_path, config, "digital_twin", "output_root") / "projections" / name
                / str(config["digital_twin"].get("run_name", "meio_policy")))
    return policy_dir, twin_run


def clear_existing(config_path: Path, config: dict, approve: bool) -> None:
    paths = scenario_paths(config_path, config)
    existing = [path for path in paths if path.exists()]
    if not existing:
        return
    name = scenario.scenario_folder_name(config)
    if not approve:
        answer = input(
            f"Scenario {name} already exists. Clear its MEIO policy and this Twin run "
            f"({', '.join(str(p) for p in existing)})? [y/N]: "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            raise SystemExit("Cancelled; existing scenario outputs were preserved.")
    for path in existing:
        shutil.rmtree(path)
        print(f"Cleared: {path}", flush=True)


def latest_policy(config_path: Path, config: dict) -> Path:
    policy_root = scenario.resolve_path(config_path, config, "meio", "output_root")
    policy_dir = policy_root / scenario.scenario_folder_name(config)
    filename = config.get("meio", {}).get("policy_filename", "meio_policy_rolling.csv")
    policies = list(policy_dir.rglob(filename))
    if not policies:
        raise FileNotFoundError(f"MEIO completed but no {filename} was found under {policy_dir}")
    return max(policies, key=lambda path: path.stat().st_mtime)


def run(config_path: Path, approve: bool = False) -> Path:
    config = scenario.load_config(config_path)
    meio_script = scenario.resolve_path(config_path, config, "meio", "runner_entrypoint")
    twin_script = scenario.resolve_path(config_path, config, "digital_twin", "entrypoint")
    twin_config = config["digital_twin"]

    clear_existing(config_path, config, approve)

    print("=== 1/2 MEIO policy runner ===", flush=True)
    subprocess.run(
        [sys.executable, str(meio_script), "--scenario-config", str(config_path)],
        cwd=ROOT,
        check=True,
    )
    policy_path = latest_policy(config_path, config)
    print(f"MEIO policy: {policy_path}", flush=True)

    print("\n=== 2/2 Digital Twin ===", flush=True)
    subprocess.run(
        [
            sys.executable,
            str(twin_script),
            "--scenario-config",
            str(config_path),
            "--policy",
            str(policy_path),
            "--run-name",
            str(twin_config.get("run_name", "meio_policy")),
            "--mode",
            str(twin_config.get("mode", "forward")),
            "--seed",
            str(twin_config.get("seed", 2026)),
        ],
        cwd=ROOT,
        check=True,
    )
    return policy_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--yes", action="store_true",
                        help="Approve clearing existing outputs for this scenario.")
    args = parser.parse_args()
    run(args.scenario_config.resolve(), approve=args.yes)


if __name__ == "__main__":
    main()
