"""
Override engine: how MEIO and the Digital Twin apply a scenario's overrides.

A scenario config carries its changes as `changes.overrides` (contract:
override.schema.json; meaning: catalog.py). Both models compile the same list:

    ovs = overrides.load(config, data_dir=..., state_csv=...)   # None = legacy changes
    plan = overrides.PlanHooks(ovs.side("plan", "meio"))         # MEIO sizes for these
    actual = ovs.side("actual", "twin_prepared")                 # the Twin lives through these

and apply them at fixed seams (meio.py, twin.py). Everything the engine writes goes to
the scenario's override folder, `<overrides.output_root>/SCENARIO_####_Name/`:

    overrides_resolved.json        each override with its matched rows
    receipts/meio.json             what MEIO applied, per override and target week
    receipts/twin_<run>.json       what the Twin applied, per override

The agent tools read ENGINE_VERSION and SUPPORTED_MEASURES and refuse to launch a
scenario that uses anything else.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import catalog, twin
from .compile import (ENGINE_VERSION, SUPPORTED_MEASURES, Override, OverrideError, OverrideSet,
                      _jsonable, apply_op, empty, load)
from .master import MasterData
from .meio import PlanHooks

__all__ = ["catalog", "twin", "ENGINE_VERSION", "SUPPORTED_MEASURES", "Override", "OverrideError", "OverrideSet",
           "apply_op", "empty", "load", "MasterData", "PlanHooks", "write_receipts"]


def write_receipts(folder: Path, name: str, applied: OverrideSet, full: OverrideSet) -> Path:
    """receipts/<name>.json for the side one model applied, plus overrides_resolved.json for
    the whole set, in the scenario's override folder."""
    folder = Path(folder)
    path = folder / "receipts" / f"{name}.json"
    applied.receipts.write(path)
    resolved = folder / "overrides_resolved.json"
    resolved.write_text(json.dumps(_jsonable({"engine_version": ENGINE_VERSION, "overrides": full.resolved()}),
                                   indent=2) + "\n", encoding="utf-8")
    return path
