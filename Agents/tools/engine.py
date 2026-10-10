"""
What the installed MEIO / Twin pipeline can actually apply.

The Twin used to save unknown `changes` keys "for context" and simulate nothing,
which is exactly the failure an agent layer cannot afford: an agent would report a
scenario result for a change that never happened. So the tools never assume -- the
pipeline declares its capabilities and run_scenario refuses anything it does not
declare:

  Phase 0  models/scenario.py: PIPELINE_CAPABILITIES = {"scenario_config_passthrough": True,
                                                        "repo_root_paths": True}
  Phase 1+ models/overrides/__init__.py: ENGINE_VERSION = "1.x",
                                         SUPPORTED_MEASURES = frozenset({...})

See IMPLEMENTATION_PLAN.md section 5 for both markers.
"""
from __future__ import annotations

import importlib
import sys

from . import catalog, paths


def _import(name: str):
    if str(paths.REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(paths.REPO_ROOT))
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


def readiness() -> dict:
    scenario_mod = _import("models.scenario")
    caps = getattr(scenario_mod, "PIPELINE_CAPABILITIES", {}) if scenario_mod else {}
    phase0 = bool(caps.get("scenario_config_passthrough") and caps.get("repo_root_paths"))
    engine = _import("models.overrides")
    version = getattr(engine, "ENGINE_VERSION", None) if engine else None
    supported = sorted(getattr(engine, "SUPPORTED_MEASURES", ())) if engine else []
    missing = []
    if not phase0:
        missing.append("Phase 0: the MEIO runner must accept --scenario-config and scenario paths must "
                       "resolve from the repository root (IMPLEMENTATION_PLAN.md 5.0).")
    if version is None:
        missing.append("Phase 1: models/overrides is not installed, so MEIO and the Twin would apply "
                       "no override at all (IMPLEMENTATION_PLAN.md 5.1).")
    by_phase = {}
    for name, m in catalog.MEASURES.items():
        by_phase.setdefault(m.phase, []).append(name)
    return {"ready_to_run": phase0 and version is not None,
            "phase0_pipeline": phase0, "engine_version": version,
            "supported_measures": supported,
            "unsupported_measures": sorted(set(catalog.MEASURES) - set(supported)),
            "catalog_measures_by_phase": by_phase, "missing": missing}


def unsupported(measures) -> list[str]:
    supported = set(readiness()["supported_measures"])
    return sorted({m for m in measures if m not in supported})
