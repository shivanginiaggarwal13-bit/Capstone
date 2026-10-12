"""
Compiling a scenario's `changes` into the OverrideSet MEIO and the Digital Twin apply.

The engine is strict where the Twin used to be permissive: an override it cannot apply
exactly -- unknown measure, unsupported measure, scope that matches nothing, window
outside the horizon -- raises OverrideError and the run stops. A silently skipped
override yields results for a scenario that never ran, which is worse than no result.

Each model takes one side of the set (`side("plan")` for MEIO, `side("actual")` for the
Twin) and records a receipt for every override it applies: which rows matched, which
weeks, and the before/after magnitude. Receipts are written to the scenario's override
folder (receipts/meio.json, receipts/twin_<run>.json) so a result can always be traced
back to the change that produced it.

Configs written before the override layer use the legacy keys of
models/twin/scenario_engine.py (`demand_surge_pct` & co.). load() returns None for
those so both models keep their exact legacy behaviour; any other unknown key raises.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import catalog
from .master import MasterData

ENGINE_VERSION = "1.0"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLUSTERS = REPO_ROOT / "output datasets" / "segmentation" / "sku_clusters.csv"
LEGACY_KEYS = {"demand_surge_pct", "affected_region", "affected_nodes", "duration_weeks", "start_week_offset"}
# Every catalog measure. policy.pooling_alpha needs a config whose MEIO is Clark-Scarf v2
# (meio.parameters.model); _compile refuses it otherwise.
SUPPORTED_MEASURES = frozenset(catalog.MEASURES)
NEEDS_CLARK_SCARF = frozenset({"policy.pooling_alpha"})


def meio_model(config: dict) -> str:
    """models/scenario.meio_model, restated so the engine imports from nothing outside it."""
    return ((config.get("meio") or {}).get("parameters") or {}).get("model", "meio_weekly")


class OverrideError(ValueError):
    """A scenario the engine cannot apply exactly."""


def apply_op(base, op: str, value):
    """One override operation on an array of base values."""
    base = np.asarray(base, dtype=float)
    if op == "scale":
        return base * value
    if op == "add":
        return base + value
    if op == "set":
        return np.broadcast_to(np.asarray(value, dtype=float), base.shape).astype(float)
    if op == "floor":
        return np.maximum(base, value)
    if op == "cap":
        return np.minimum(base, value)
    raise OverrideError(f"operation {op!r} has no numeric form")


@dataclass(frozen=True)
class Override:
    raw: dict
    periods: frozenset | None            # None = every forward week

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def measure(self) -> str:
        return self.raw["measure"]

    @property
    def scope(self) -> dict:
        return self.raw["scope"]

    @property
    def op(self) -> str:
        return self.raw["operation"]

    @property
    def value(self):
        return self.raw.get("value")

    @property
    def unit(self) -> str:
        return self.raw["unit"]

    @property
    def sides(self) -> tuple[str, ...]:
        return tuple(self.raw["applies_to"])

    @property
    def options(self) -> dict:
        return self.raw.get("options") or {}

    def active(self, period: str) -> bool:
        return self.periods is None or period in self.periods


class Receipts:
    """What one model applied, grouped by override.

    Per-period facts combine across periods by name: `pair_weeks` and `*units_before` /
    `*units_after` are totals and add up; `*_mean` facts are averages weighted by
    `pair_weeks`; anything else (a field name, a lane count) is the same every week
    and keeps the latest value."""

    def __init__(self, model: str):
        self.model = model
        self.entries: dict[str, dict] = {}
        self._weight: dict[tuple[str, str], float] = {}

    def add(self, o: Override, period: str | None = None, **facts) -> None:
        entry = self.entries.setdefault(o.id, {"override_id": o.id, "measure": o.measure, "applications": 0})
        entry["applications"] += 1
        if period is None:
            entry.update(facts)
            return
        entry.setdefault("periods", [])
        if period not in entry["periods"]:
            entry["periods"].append(period)
        w = float(facts.get("pair_weeks", 1))
        for k, v in facts.items():
            numeric = isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)
            if numeric and (k == "pair_weeks" or k.endswith(("units_before", "units_after"))):
                entry[k] = round(float(entry.get(k, 0.0)) + float(v), 4)
            elif numeric and k.endswith("_mean"):
                n = self._weight.get((o.id, k), 0.0)
                entry[k] = round((float(entry.get(k, 0.0)) * n + float(v) * w) / (n + w), 4) if n + w else float(v)
                self._weight[(o.id, k)] = n + w
            else:
                entry[k] = v

    def as_dict(self) -> dict:
        return {"model": self.model, "engine_version": ENGINE_VERSION, "overrides": list(self.entries.values())}

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_jsonable(self.as_dict()), indent=2) + "\n", encoding="utf-8")


class OverrideSet:
    """A compiled, validated list of overrides plus the master data they resolve against."""

    def __init__(self, overrides: list[Override], master: MasterData | None, model: str = "engine"):
        self.overrides = overrides
        self.master = master
        self.receipts = Receipts(model)

    def __bool__(self) -> bool:
        return bool(self.overrides)

    def __len__(self) -> int:
        return len(self.overrides)

    def side(self, side: str, model: str | None = None) -> "OverrideSet":
        return OverrideSet([o for o in self.overrides if side in o.sides], self.master, model or side)

    def of(self, *measures: str) -> list[Override]:
        return [o for o in self.overrides if o.measure in measures]

    def active(self, period: str, *measures: str) -> list[Override]:
        return [o for o in self.of(*measures) if o.active(period)]

    def rows(self, o: Override):
        return self.master.match(o.measure, o.scope)

    def resolved(self) -> list[dict]:
        out = []
        for o in self.overrides:
            out.append({**o.raw, "matched_rows": int(len(self.rows(o))),
                        "universe": catalog.MEASURES[o.measure].universe,
                        "weeks": sorted(o.periods) if o.periods is not None else "all"})
        return out


def empty() -> OverrideSet:
    return OverrideSet([], None)


def _path(value) -> Path:
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


def load(config: dict, *, data_dir=None, state_csv=None, clusters_csv=None) -> OverrideSet | None:
    """Compile config["changes"]. Returns an empty set for no changes, None for a legacy
    `changes` object (the caller keeps its legacy path), and raises on anything else the
    engine cannot apply exactly."""
    changes = config.get("changes") or {}
    if "overrides" not in changes:
        unknown = sorted(set(changes) - LEGACY_KEYS)
        if unknown:
            raise OverrideError(f"unknown changes keys {unknown}: put overrides under changes.overrides "
                                f"(see models/overrides/override.schema.json)")
        return None if changes else empty()
    extra = sorted(set(changes) - {"override_schema_version", "overrides"})
    if extra:
        raise OverrideError(f"changes may hold only override_schema_version and overrides, not {extra}")
    if changes.get("override_schema_version") != catalog.OVERRIDE_SCHEMA_VERSION:
        raise OverrideError(f"override_schema_version must be {catalog.OVERRIDE_SCHEMA_VERSION!r}")
    if not changes["overrides"]:
        return empty()
    twin = config.get("digital_twin", {})
    master = MasterData(_path(data_dir or twin.get("data_dir", "input datasets")),
                        _path(clusters_csv) if clusters_csv else DEFAULT_CLUSTERS,
                        _path(state_csv or twin.get("state", "output datasets/history/opening_position.parquet")))
    compiled = [_compile(raw, master, meio_model(config)) for raw in changes["overrides"]]
    ids = [o.id for o in compiled]
    if len(ids) != len(set(ids)):
        raise OverrideError(f"duplicate override ids in {ids}")
    return OverrideSet(compiled, master)


def _compile(raw: dict, master: MasterData, model: str = "meio_weekly") -> Override:
    oid = raw.get("id", "?")

    def fail(msg):
        raise OverrideError(f"{oid}: {msg}")

    measure = raw.get("measure")
    m = catalog.MEASURES.get(measure)
    if m is None:
        fail(f"unknown measure {measure!r}")
    if measure not in SUPPORTED_MEASURES:
        fail(f"engine {ENGINE_VERSION} does not apply {measure}")
    if measure in NEEDS_CLARK_SCARF and model != "clark_scarf_v2":
        fail(f"{measure} only exists in Clark-Scarf v2; this config's MEIO is {model}")
    spec = m.ops.get(raw.get("operation"))
    if spec is None:
        fail(f"{measure} has no operation {raw.get('operation')!r}; allowed {sorted(m.ops)}")
    value = raw.get("value")
    if raw["operation"] != "block":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
            fail("value must be a number")
        if (spec.lo is not None and value < spec.lo) or (spec.hi is not None and value > spec.hi):
            fail(f"value {value} outside [{spec.lo}, {spec.hi}]")
    unit = raw.get("unit") or spec.units[0]
    if unit not in spec.units:
        fail(f"unit {unit!r} not in {list(spec.units)}")
    sides = list(raw.get("applies_to") or m.default_applies_to)
    if not sides or not set(sides) <= set(m.applies_to):
        fail(f"applies_to {sides} not within {list(m.applies_to)}")

    window, periods = raw.get("window"), None
    if window:
        if not m.windowed:
            fail(f"{measure} takes no window")
        fwd = master.forward_periods
        s, e = window.get("start_period"), window.get("end_period")
        if s not in fwd or e not in fwd or fwd.index(s) > fwd.index(e):
            fail(f"window {s}..{e} is not inside the forward horizon {fwd[0]}..{fwd[-1]}")
        periods = frozenset(master.window_periods(window))
    elif measure == "supply.lane_outage":
        fail("a lane outage needs a window")

    scope = raw.get("scope") or {}
    if not scope:
        fail('scope is empty; use {"all": true} for a portfolio-wide change')
    if scope.get("all"):
        if len(scope) > 1:
            fail('scope mixes "all" with filters')
    else:
        for key, values in scope.items():
            if key not in m.scope_keys:
                fail(f"{measure} cannot be scoped by {key!r}")
            unknown = sorted(set(map(str, values)) - set(master.key_domains[key]))
            if unknown:
                fail(f"unknown {key} values {unknown}")
    if measure == "supply.plant_capacity" and raw["operation"] == "set" and "production_family" not in scope:
        fail("'set' capacity needs a production_family scope")
    options = raw.get("options") or {}
    for key in options:
        if key not in m.options:
            fail(f"unknown option {key!r}")
    if m.options:
        options = {k: options.get(k, default) for k, (_t, default, _s) in m.options.items()}

    o = Override({**raw, "unit": unit, "applies_to": sides, "options": options}, periods)
    if master.match(measure, scope).empty:
        fail(f"scope {scope} matches no {m.universe} rows")
    return o


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if math.isnan(float(obj)) else round(float(obj), 4)
    return obj
