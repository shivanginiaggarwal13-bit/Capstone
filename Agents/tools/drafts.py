"""
Scenario drafts: the working set of overrides agents assemble before a scenario
config exists, and the validation every override passes through on the way in.

A draft is one JSON file in Agents/workspace/drafts/. Agents never edit it
directly; every change goes through add_override / remove_override, which
validate, canonicalise scope values against master data, assign the OVR-#### id,
stamp provenance, and keep a history -- so the config that is eventually approved
can be traced line by line back to the agent and request that produced it.

Validation layers, cheapest first:
  1. catalog    measure, operation, value bounds, units, sides, window, options
  2. master data  every scope value exists (with did-you-mean hints); scope matches rows
  3. schema     the stored override satisfies override.schema.json
  4. set level  overlaps and compounding, plan/actual coverage, whether the baseline on
                disk still matches the config, engine support (validate_draft)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache

import jsonschema

from . import catalog, context, engine, impact, paths

MAX_OVERRIDES = 60
_DRAFT_ID = re.compile(r"D\d{4}")
_FIELD_ORDER = ("id", "group", "measure", "scope", "window", "operation", "value", "unit", "applies_to",
                 "options", "rationale", "provenance")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _issue(level: str, code: str, message: str, override_id: str | None = None) -> dict:
    out = {"level": level, "code": code, "message": message}
    if override_id:
        out["override_id"] = override_id
    return out


# ---------------------------------------------------------------- baseline --
def _root_config() -> dict:
    import sys
    if str(paths.REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(paths.REPO_ROOT))
    from models import scenario
    return scenario.load_config(paths.ROOT_CONFIG)


def default_baseline() -> dict:
    """The run the shared root scenario_config.json describes is the baseline every
    agent scenario is compared against: same MEIO tools, seed and opening state.
    Scenarios never choose their own MEIO -- they run whatever the config defines."""
    cfg = _root_config()
    from models import scenario
    return {"scenario": scenario.scenario_folder_name(cfg),
            "run": cfg["digital_twin"].get("run_name", "meio_policy"),
            "seed": int(cfg["digital_twin"].get("seed", 2026)),
            "meio_model": meio_model_of(cfg),
            "meio_entrypoint": cfg["meio"].get("rolling_entrypoint")}


def meio_model_of(config: dict) -> str:
    """Which sizing model the config's MEIO runs: `meio.parameters.model`
    (models/scenario.meio_model). A config without that section runs MEIO Weekly."""
    _root_config()                      # puts the repository on sys.path
    from models import scenario
    return scenario.meio_model(config)


def _normalised_source(path) -> str | None:
    try:
        return "\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return None


def baseline_comparability(baseline_scenario: str) -> list[dict]:
    """Warn when the baseline on disk was produced under different conditions than the
    config defines now -- a different rolling-policy script, seed or opening state --
    because the scenario deltas would then mix that difference in."""
    saved = paths.PROJECTIONS_DIR / baseline_scenario / "scenario_config.json"
    if not saved.exists():
        return [_issue("warning", "baseline_missing", f"no saved config for baseline {baseline_scenario}; "
                                                      f"comparisons cannot be checked for consistency")]
    then, now_cfg = json.loads(saved.read_text(encoding="utf-8")), _root_config()
    issues = []
    old, new = then.get("meio", {}).get("rolling_entrypoint"), now_cfg["meio"].get("rolling_entrypoint")
    if old != new and (_normalised_source(paths.REPO_ROOT / str(old)) is None
                       or _normalised_source(paths.REPO_ROOT / str(old)) != _normalised_source(paths.REPO_ROOT / str(new))):
        issues.append(_issue("warning", "baseline_stale",
                             f"the baseline policy was built by {old}, but the config now runs {new} and the code "
                             f"differs (or no longer exists); regenerate the baseline before trusting deltas"))
    if then.get("meio", {}).get("parameters") != now_cfg["meio"].get("parameters"):
        issues.append(_issue("warning", "baseline_stale",
                             "the baseline policy was built with different meio.parameters (model, tuning rule or "
                             "evidence) than the config defines now; regenerate the baseline before trusting deltas"))
    for key in ("seed", "state", "policy"):
        if then.get("digital_twin", {}).get(key) != now_cfg["digital_twin"].get(key):
            issues.append(_issue("warning", "baseline_stale",
                                 f"baseline digital_twin.{key} was {then['digital_twin'].get(key)!r}; the config now "
                                 f"says {now_cfg['digital_twin'].get(key)!r}"))
    return issues


# ------------------------------------------------------------------- store --
def _path(draft_id: str):
    if not isinstance(draft_id, str) or not _DRAFT_ID.fullmatch(draft_id):
        raise ValueError(f"draft_id must look like D0001, got {draft_id!r}")
    return paths.DRAFTS_DIR / f"{draft_id}.json"


def load_draft(draft_id: str) -> dict:
    path = _path(draft_id)
    if not path.exists():
        known = sorted(p.stem for p in paths.DRAFTS_DIR.glob("D*.json"))
        raise ValueError(f"no draft {draft_id}; existing drafts: {known or 'none'}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_draft(draft: dict) -> None:
    draft["updated_at"] = now()
    paths.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    _path(draft["draft_id"]).write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")


def create_draft(scenario_name: str, description: str, request_text: str, hypothesis: str = "",
                 baseline_scenario: str | None = None, baseline_run: str | None = None,
                 created_by: str = "orchestrator") -> dict:
    if not scenario_name or not scenario_name.strip():
        raise ValueError("scenario_name is required")
    base = default_baseline()
    paths.DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    seq = 1 + max((int(p.stem[1:]) for p in paths.DRAFTS_DIR.glob("D[0-9][0-9][0-9][0-9].json")), default=0)
    draft = {
        "draft_id": f"D{seq:04d}", "created_at": now(), "created_by": created_by,
        "scenario_name": scenario_name.strip(), "description": description.strip(),
        "hypothesis": hypothesis.strip(), "request_text": request_text.strip(),
        "meio": {"model": base["meio_model"], "rolling_entrypoint": base["meio_entrypoint"],
                 "source": "scenario_config.json"},
        "evaluation": {"baseline_scenario": baseline_scenario or base["scenario"],
                       "baseline_run": baseline_run or base["run"], "seed": base["seed"]},
        "overrides": [], "next_seq": 1, "scenario_number": None, "history": [],
    }
    save_draft(draft)
    return {"draft_id": draft["draft_id"], "scenario_name": draft["scenario_name"],
            "meio": draft["meio"], "evaluation": draft["evaluation"]}


# -------------------------------------------------------------- validation --
@lru_cache(maxsize=1)
def _schema_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(paths.SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(
        {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/override"})


def normalize_override(raw: dict, meio_model: str = "meio_weekly") -> tuple[dict, list[dict], dict]:
    """Validate and canonicalise one override. Returns (override, issues, scope summary)."""
    issues: list[dict] = []
    err = lambda code, msg: issues.append(_issue("error", code, msg))      # noqa: E731
    warn = lambda code, msg: issues.append(_issue("warning", code, msg))   # noqa: E731
    info = lambda code, msg: issues.append(_issue("info", code, msg))      # noqa: E731

    o = {k: v for k, v in dict(raw).items() if v is not None and v != {}}
    m = catalog.MEASURES.get(o.get("measure"))
    if m is None:
        err("unknown_measure", f"measure {o.get('measure')!r} is not in the catalog: {sorted(catalog.MEASURES)}")
        return o, issues, {}
    spec = m.ops.get(o.get("operation"))
    if spec is None:
        err("bad_operation", f"{m.name} accepts operations {sorted(m.ops)}, not {o.get('operation')!r}")
        return o, issues, {}

    # value and unit
    if o["operation"] == "block":
        o["value"], o["unit"] = None, "none"
    else:
        value = o.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            err("value_not_number", f"{m.name} {o['operation']} needs a numeric value")
        else:
            o["value"] = float(value)
            if (spec.lo is not None and value < spec.lo) or (spec.hi is not None and value > spec.hi):
                err("value_out_of_bounds", f"{m.name} {o['operation']} value {value} outside [{spec.lo}, {spec.hi}]")
            elif spec.soft_lo is not None and not (spec.soft_lo <= value <= spec.soft_hi):
                warn("value_unusual", f"{value} is outside the typical range [{spec.soft_lo}, {spec.soft_hi}] "
                                      f"for {m.name} {o['operation']} -- confirm it is intended")
        unit = o.get("unit") or spec.units[0]
        if unit not in spec.units:
            err("bad_unit", f"{m.name} {o['operation']} takes unit {list(spec.units)}, not {unit!r}")
        o["unit"] = unit

    # scope
    scope = o.get("scope") or {}
    canonical: dict = {}
    if not isinstance(scope, dict) or not scope:
        err("empty_scope", 'scope needs at least one filter, or {"all": true} for a portfolio-wide change')
    elif scope.get("all") is not None and len(scope) > 1:
        err("all_with_filters", 'use either {"all": true} or filters, not both')
    else:
        for key, values in scope.items():
            if key == "all":
                if values is not True:
                    err("bad_all", '"all" may only be true')
                canonical["all"] = True
                continue
            if key not in m.scope_keys:
                err("bad_scope_key", f"{m.name} scopes by {list(m.scope_keys)}; {key!r} is not one of them")
                continue
            values = [values] if isinstance(values, (str, int, float)) else list(values or [])
            vals, problems = context.canonicalize(key, values)
            for p in problems:
                err("unknown_value", p)
            if vals:
                canonical[key] = vals
    o["scope"] = canonical

    # window
    window = o.get("window")
    if window:
        if not m.windowed:
            err("window_not_allowed", f"{m.name} is not time-windowed (opening position or a static "
                                      f"rate); remove the window")
        else:
            periods = context.forward_periods()
            s, e = window.get("start_period"), window.get("end_period") or window.get("start_period")
            if s not in periods or e not in periods:
                err("window_outside_horizon", f"window must use forward periods {periods[0]}..{periods[-1]}; "
                                              f"got {s}..{e} (use resolve_window to convert dates)")
            elif periods.index(s) > periods.index(e):
                err("window_reversed", f"window starts ({s}) after it ends ({e})")
            else:
                o["window"] = {"start_period": s, "end_period": e}
    elif m.windowed:
        info("whole_horizon", "no window given: the change applies to every forward week")
        o.pop("window", None)
    if m.name == "supply.lane_outage" and not window:
        err("window_required", "a lane outage needs a window")

    # sides
    sides = o.get("applies_to") or list(m.default_applies_to)
    sides = [sides] if isinstance(sides, str) else list(sides)
    bad = [s for s in sides if s not in m.applies_to]
    if bad:
        err("bad_side", f"{m.name} can only apply to {list(m.applies_to)} (MEIO hook: {m.meio_hook}; "
                        f"Twin hook: {m.twin_hook}); got {bad}")
    o["applies_to"] = [s for s in catalog.SIDES if s in sides]

    # options
    opts = o.get("options") or {}
    for key, val in opts.items():
        if key not in m.options:
            err("bad_option", f"{m.name} accepts options {list(m.options) or 'none'}; got {key!r}")
        elif m.options[key][0] == "bool" and not isinstance(val, bool):
            err("bad_option", f"option {key} must be true or false")
    if m.options:
        o["options"] = {k: opts.get(k, default) for k, (_t, default, _s) in m.options.items()}
    else:
        o.pop("options", None)

    if len(str(o.get("rationale", "")).strip()) < 3:
        err("missing_rationale", "every override needs a rationale in planner terms (why this change)")

    # measure-specific rules
    if m.name == "policy.pooling_alpha" and meio_model != "clark_scarf_v2":
        err("needs_clark_scarf", f"policy.pooling_alpha only exists in Clark-Scarf v2, and the MEIO defined in "
                                 f"scenario_config.json is {meio_model}; drop this override (switching MEIO is "
                                 f"a config change for the team, not a scenario setting)")
    if m.name == "supply.plant_capacity" and o["operation"] == "set" and "production_family" not in canonical:
        err("set_needs_family", "'set' capacity is per production-family line; add production_family "
                                "to the scope or use 'scale'")
    if m.name == "demand.volume" and o["applies_to"] == ["plan"]:
        info("forecast_error", "plan-only demand change: MEIO sizes for demand that never arrives "
                               "(an over- or under-forecast)")
    if m.name == "demand.volume" and o["applies_to"] == ["actual"]:
        info("surprise", "actual-only demand change: MEIO does not anticipate it, so the prepared and "
                         "unprepared arms see it identically")

    summary: dict = {}
    if not any(i["level"] == "error" for i in issues):
        rows = context.match(m.name, canonical)
        if rows.empty:
            err("empty_match", f"scope {canonical} matches no {m.universe} rows (e.g. the SKU is not "
                               f"stocked at that DC); check it with resolve_scope")
        else:
            summary = context.scope_summary(m.name, rows)
    return o, issues, summary


def _windows_overlap(a: dict, b: dict) -> list[str]:
    return sorted(set(context.window_periods(a.get("window"))) & set(context.window_periods(b.get("window"))))


def overlap_issues(overrides: list[dict], new: dict | None = None) -> list[dict]:
    """Same-measure overrides whose rows and weeks intersect: they compound in list
    order, which is usually double counting (a promotion on top of a surge) or an
    edit that should have replaced the earlier override."""
    pairs = [(a, new) for a in overrides] if new else \
        [(a, b) for i, a in enumerate(overrides) for b in overrides[i + 1:]]
    out = []
    for a, b in pairs:
        if a["measure"] != b["measure"] or not set(a["applies_to"]) & set(b["applies_to"]):
            continue
        weeks = _windows_overlap(a, b)
        if not weeks:
            continue
        rows_a = context.match(a["measure"], a["scope"]).index
        rows_b = context.match(b["measure"], b["scope"]).index
        shared = rows_a.intersection(rows_b)
        if shared.empty:
            continue
        if a["operation"] == b["operation"] == "scale":
            effect = f"multipliers compound to x{a['value'] * b['value']:.3f}"
            if (a["value"] - 1) * (b["value"] - 1) < 0:
                effect += " and push in opposite directions"
        elif "set" in (a["operation"], b["operation"]):
            effect = "the later 'set' overwrites the earlier change on shared rows"
        else:
            effect = "they apply in list order"
        out.append(_issue("warning", "overlap",
                          f"{a['id']} and {b.get('id', 'the new override')} both change {a['measure']} on "
                          f"{len(shared)} {catalog.MEASURES[a['measure']].universe} rows over {len(weeks)} "
                          f"week(s) ({weeks[0]}..{weeks[-1]}): {effect}. Confirm this is not double counting.",
                          b.get("id")))
    return out


# ------------------------------------------------------------ operations --
def add_override(draft_id: str, raw: dict, agent: str) -> dict:
    draft = load_draft(draft_id)
    if len(draft["overrides"]) >= MAX_OVERRIDES:
        return {"ok": False, "errors": [_issue("error", "too_many", f"a draft holds at most {MAX_OVERRIDES} overrides")]}
    o, issues, summary = normalize_override(raw, draft["meio"]["model"])
    errors = [i for i in issues if i["level"] == "error"]
    if errors:
        return {"ok": False, "saved": False, "errors": errors,
                "warnings": [i for i in issues if i["level"] != "error"],
                "hint": "nothing was saved; fix the errors and call again"}
    o["id"] = f"OVR-{draft['next_seq']:04d}"
    o["provenance"] = {**(raw.get("provenance") or {}), "agent": agent, "created_at": now()}
    o = {k: o[k] for k in _FIELD_ORDER if k in o}
    schema_errors = sorted(_schema_validator().iter_errors(o), key=lambda e: list(e.path))
    if schema_errors:
        return {"ok": False, "saved": False,
                "errors": [_issue("error", "schema", f"{'/'.join(map(str, e.path)) or 'override'}: {e.message}")
                           for e in schema_errors]}
    issues += overlap_issues(draft["overrides"], o)
    draft["next_seq"] += 1
    draft["overrides"].append(o)
    draft["history"].append({"at": now(), "agent": agent, "action": "add", "override_id": o["id"]})
    save_draft(draft)
    return {"ok": True, "saved": True, "override": o, "scope": summary, "impact": impact.preview(o),
            "warnings": [i for i in issues if i["level"] == "warning"],
            "info": [i["message"] for i in issues if i["level"] == "info"]}


def remove_override(draft_id: str, agent: str, override_id: str | None = None,
                    group: str | None = None) -> dict:
    if not override_id and not group:
        raise ValueError("give override_id or group")
    draft = load_draft(draft_id)
    keep, gone = [], []
    for o in draft["overrides"]:
        hit = (override_id and o["id"] == override_id) or (group and o.get("group") == group)
        (gone if hit else keep).append(o)
    if not gone:
        raise ValueError(f"nothing matched override_id={override_id} group={group}; "
                         f"current ids: {[o['id'] for o in draft['overrides']]}")
    draft["overrides"] = keep
    for o in gone:
        draft["history"].append({"at": now(), "agent": agent, "action": "remove", "override_id": o["id"]})
    save_draft(draft)
    return {"removed": [o["id"] for o in gone], "remaining": len(keep)}


def compact(o: dict) -> dict:
    keep = ("id", "group", "measure", "scope", "window", "operation", "value", "unit", "applies_to",
            "options", "rationale")
    out = {k: o[k] for k in keep if k in o}
    out["agent"] = o.get("provenance", {}).get("agent")
    return out


def list_overrides(draft_id: str) -> dict:
    d = load_draft(draft_id)
    return {"draft_id": d["draft_id"], "scenario_name": d["scenario_name"], "description": d["description"],
            "hypothesis": d["hypothesis"], "request_text": d["request_text"], "meio": d["meio"],
            "evaluation": d["evaluation"], "scenario_number": d["scenario_number"],
            "overrides": [compact(o) for o in d["overrides"]]}


def validate_draft(draft_id: str) -> dict:
    d = load_draft(draft_id)
    issues: list[dict] = []
    if not d["overrides"]:
        issues.append(_issue("error", "no_overrides", "the draft has no overrides yet"))
    for o in d["overrides"]:
        _norm, found, _summary = normalize_override(o, d["meio"]["model"])
        issues += [{**i, "override_id": o["id"]} for i in found if i["level"] != "info"]
    issues += overlap_issues(d["overrides"])

    sides = {s for o in d["overrides"] for s in o["applies_to"]}
    if d["overrides"] and "actual" not in sides:
        issues.append(_issue("info", "plan_only", "every override is plan-side: only the prepared arm "
                                                  "differs from baseline (the unprepared arm is skipped)"))
    if d["overrides"] and "plan" not in sides:
        issues.append(_issue("info", "actual_only", "every override is actual-side: MEIO re-plans nothing, "
                                                    "so prepared and unprepared are the same run"))
    issues += baseline_comparability(d["evaluation"]["baseline_scenario"])
    if any(o["measure"] == "supply.lane_outage" for o in d["overrides"]) and not any(
            o["measure"] == "supply.lead_time_mean" and "plan" in o["applies_to"] for o in d["overrides"]):
        issues.append(_issue("info", "outage_foresight", "MEIO cannot see a lane outage; add a plan-side "
                                                         "supply.lead_time_mean override if the outage is "
                                                         "known in advance"))
    gaps = engine.unsupported(o["measure"] for o in d["overrides"])
    if gaps:
        issues.append(_issue("warning", "engine_support",
                             f"the installed pipeline does not yet apply {gaps}; run_scenario will refuse "
                             f"until those engine phases land (see pipeline_readiness)"))
    errors = [i for i in issues if i["level"] == "error"]
    return {"draft_id": draft_id, "ok": not errors, "errors": errors,
            "warnings": [i for i in issues if i["level"] == "warning"],
            "info": [i["message"] for i in issues if i["level"] == "info"],
            "impact": impact.preview_all(d["overrides"]) if d["overrides"] else {}}
