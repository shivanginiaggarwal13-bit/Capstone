"""Tools the scenario agents call: context, override authoring, validation, config
building, the gated runner and results analysis. Entry points for a runtime:

    from Agents.tools import registry
    registry.tool_specs("demand_agent")          # Claude tool definitions
    registry.dispatch("demand_agent", name, args)  # run one call, errors as data

Humans drive the same tools (and the approval gate) through `python -m Agents.tools`.
The measure catalog and scope resolution come from the override engine
(models/overrides), so previews here and application in MEIO / the Twin agree.
"""
import sys as _sys
from pathlib import Path as _Path

_REPO_ROOT = str(_Path(__file__).resolve().parents[2])
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
