"""Shared scenario configuration and folder naming for model runs."""
from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA_VERSION = "1.0"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OVERRIDES_ROOT = "output datasets/overrides"
# Read by the agent tools (Agents/tools/engine.py) before they launch a scenario.
PIPELINE_CAPABILITIES = {"scenario_config_passthrough": True, "repo_root_paths": True}


MEIO_MODELS = ("meio_weekly", "clark_scarf_v2")


def meio_model(config: dict) -> str:
    """The sizing model a config's MEIO runs, from `meio.parameters.model`. A config with
    no `parameters` section predates the link to run_all.py's tuning and keeps MEIO
    Weekly, so it rebuilds exactly the policy it built before."""
    params = (config.get("meio") or {}).get("parameters") or {}
    model = params.get("model", "meio_weekly")
    if model not in MEIO_MODELS:
        raise ValueError(f"meio.parameters.model must be one of {MEIO_MODELS}, got {model!r}")
    return model


def scenario_folder_name(config: dict) -> str:
    number = config["scenario_number"]
    slug = re.sub(r"[^A-Za-z0-9]+", "_", config["scenario_name"]).strip("_") or "Unnamed"
    return f"SCENARIO_{number:04d}_{slug}"


def load_config(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("scenario config must contain a JSON object")
    name = config.get("scenario_name")
    changes = config.get("changes")
    number = config.get("scenario_number")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("scenario config requires a non-empty 'scenario_name'")
    if not isinstance(changes, dict):
        raise ValueError("scenario config requires 'changes' as a JSON object")
    if isinstance(number, bool) or not isinstance(number, int) or not 0 < number <= 9999:
        raise ValueError("scenario_number must be an integer from 1 to 9999")
    normalized = dict(config)
    normalized.update({
        "schema_version": config.get("schema_version", SCHEMA_VERSION),
        "scenario_number": number,
        "scenario_name": name.strip(),
        "changes": changes,
        "details": config.get("details", {}),
    })
    if not isinstance(normalized["details"], dict):
        raise ValueError("scenario config 'details' must be a JSON object")
    return normalized


def scenario_dir(config_path: Path, output_root: Path) -> Path:
    return output_root / scenario_folder_name(load_config(config_path))


def resolve_path(config_path: Path, config: dict, section: str, key: str,
                 default: Path | None = None) -> Path:
    """Resolve a repository-relative path stored in a scenario section. Relative paths
    resolve from the repository root, not the config's folder, so a scenario config
    saved anywhere (output datasets/overrides/..., an output folder's audit copy) points
    at the same data as the root config."""
    value = config.get(section, {}).get(key) if isinstance(config.get(section), dict) else None
    path = Path(value) if isinstance(value, str) and value else default
    if path is None:
        raise ValueError(f"scenario config requires '{section}.{key}'")
    return path if path.is_absolute() else REPO_ROOT / path


def overrides_dir(config_path: Path, config: dict) -> Path:
    """The scenario's override folder: <overrides.output_root>/SCENARIO_####_Name. Holds the
    generated config, card and approval, and everything the override engine writes."""
    root = resolve_path(config_path, config, "overrides", "output_root", default=Path(DEFAULT_OVERRIDES_ROOT))
    return root / scenario_folder_name(config)


def resolve_scenario_path(config_path: Path, config: dict, section: str, key: str) -> Path:
    path = resolve_path(config_path, config, section, key)
    expected = scenario_folder_name(config)
    if path.name != expected:
        raise ValueError(
            f"{section}.{key} must end with the configured scenario folder {expected!r}")
    return path


def copy_config(config_path: Path, destination: Path, config: dict | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    payload = config if config is not None else load_config(config_path)
    (destination / "scenario_config.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8")
