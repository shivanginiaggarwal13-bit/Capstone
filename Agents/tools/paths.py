"""Repository locations the agent tools read from and write to.

Everything is resolved from the repository root, never from the current working
directory, so the tools behave the same from a shell, a test, or an agent runtime.

Two areas are written:
  output datasets/overrides/SCENARIO_####_Name/   one folder per scenario, root taken from
      the config's `overrides.output_root`: the generated scenario_config.json, its card,
      the approval record, run status and log, and the override engine's receipts
  Agents/workspace/                                 scratch: drafts not yet numbered as
      scenarios, runtime transcripts, the Claude Desktop bridge mailbox

`SUPPLY_BRAIN_OVERRIDES_ROOT` and `SUPPLY_BRAIN_AGENT_WORKSPACE` relocate them (tests use
both to stay out of the real folders).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "Agents"

DATA_DIR = REPO_ROOT / "input datasets"
OUTPUT_DIR = REPO_ROOT / "output datasets"
ROOT_CONFIG = REPO_ROOT / "scenario_config.json"

OPENING_STATE = OUTPUT_DIR / "history" / "opening_position.parquet"
FORWARD_POLICY = OUTPUT_DIR / "history" / "forward_policy.parquet"
SKU_CLUSTERS = OUTPUT_DIR / "segmentation" / "sku_clusters.csv"
# NOT OUTPUT_DIR / "projections": that was models/twin/digital_twin.py's convention
# (run_dir = out_root / "projections" / run_name, digital_twin.py:1744), but the optimized
# engine this branch's scenario_config.json actually points at (digital_twin_fast.py) writes
# straight to out_root / scenario_folder_name / run_name, no "projections" segment -- confirmed
# against a real scenario run's output (SCENARIO_0003_Southeast_Beverage_Surge_July, 2026-10-19):
# scenario_config.json, run_kpis.json etc. all sit directly under OUTPUT_DIR/<scenario>/<arm>/.
PROJECTIONS_DIR = OUTPUT_DIR
MEIO_OUTPUT_DIR = OUTPUT_DIR / "MEIO"
MEIO_ARTIFACTS_DIR = OUTPUT_DIR / "MEIO artifacts"

SCHEMA_PATH = REPO_ROOT / "models" / "overrides" / "override.schema.json"
INSTRUCTIONS_DIR = AGENTS_DIR / "instructions"

WORKSPACE = Path(os.environ.get("SUPPLY_BRAIN_AGENT_WORKSPACE", AGENTS_DIR / "workspace"))
DRAFTS_DIR = WORKSPACE / "drafts"
DEFAULT_OVERRIDES_ROOT = "output datasets/overrides"


def overrides_root() -> Path:
    """Parent of the per-scenario override folders: the root config's
    `overrides.output_root`, repository-relative."""
    if os.environ.get("SUPPLY_BRAIN_OVERRIDES_ROOT"):
        return Path(os.environ["SUPPLY_BRAIN_OVERRIDES_ROOT"])
    config = json.loads(ROOT_CONFIG.read_text(encoding="utf-8"))
    root = Path(config.get("overrides", {}).get("output_root") or DEFAULT_OVERRIDES_ROOT)
    return root if root.is_absolute() else REPO_ROOT / root


def rel(path: Path) -> str:
    """Repository-relative POSIX path, the form scenario configs store."""
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)
