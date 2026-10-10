"""
Tool registry: every tool an agent can call, in Claude tool-use format, plus the
per-agent allowlists and the dispatcher that enforces them.

Least privilege is enforced here, not in prompts:
  - an agent sees and may call only the tools in its AGENTS entry;
  - propose_override's `measure` enum is narrowed to the agent's own domains, and
    dispatch re-checks it (a specialist cannot write outside its lane);
  - removals are limited to overrides in the agent's domains or authored by it;
  - no agent tool can approve a scenario. `request_human_approval` and `delegate`
    are runtime tools: the LangGraph runtime handles them (Agents/runtime/graph.py),
    and approval is written only by a person through the CLI or the runtime prompt.

Every handler returns JSON-safe data; failures come back as {"ok": false, "error": ...}
so the model can correct itself instead of the loop crashing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from . import analysis, catalog, context, drafts, engine, macros, paths, scenario

SPECIALISTS = ("demand_agent", "supply_agent", "inventory_agent", "service_policy_agent", "economics_agent")


@dataclass(frozen=True)
class Tool:
    description: str
    input_schema: dict
    handler: Callable | None        # None = handled by the runtime


def _obj(properties: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}
_SCOPE = _obj({"all": {"type": "boolean", "description": "true for an explicit portfolio-wide change"},
               **{k: {**_STRS, "description": f"{k} values (OR within a key; keys AND together)"}
                  for k in dict.fromkeys(catalog.SKU_KEYS + catalog.NODE_KEYS + catalog.CUSTOMER_KEYS
                                         + catalog.PLANT_KEYS + catalog.LANE_KEYS)}})
_WINDOW = _obj({"start_period": {**_STR, "description": "first ISO week, e.g. 2026-W28"},
                "end_period": {**_STR, "description": "last ISO week, inclusive"}}, ("start_period", "end_period"))
_DRAFT = {**_STR, "description": "draft id, e.g. D0001"}
_SCENARIO = {**_STR, "description": "scenario folder, e.g. SCENARIO_0003_Southeast_Heatwave"}
_DIMS = list(analysis.SKU_DIMS + analysis.NODE_DIMS)


def _domain_ok(agent: str, measure: str) -> bool:
    return agent == "orchestrator" or catalog.MEASURES[measure].domain in catalog.AGENT_DOMAINS.get(agent, ())


# ----------------------------------------------------------------- handlers --
def _resolve_scope(agent, measure, scope):
    if measure not in catalog.MEASURES:
        raise ValueError(f"unknown measure {measure!r}")
    m = catalog.MEASURES[measure]
    canonical, problems = {}, []
    for key, values in scope.items():
        if key == "all":
            canonical["all"] = True
            continue
        if key not in m.scope_keys:
            problems.append(f"{measure} scopes by {list(m.scope_keys)}, not {key!r}")
            continue
        vals, errs = context.canonicalize(key, values if isinstance(values, list) else [values])
        problems += errs
        if vals:
            canonical[key] = vals
    if problems:
        return {"ok": False, "errors": problems}
    rows = context.match(measure, canonical)
    out = {"ok": True, "canonical_scope": canonical, **context.scope_summary(measure, rows)}
    if m.universe in ("demand_lines", "demand_pairs", "stock_pairs") and len(rows):
        by = ("dc_id", "sku_id", "b2b_customer_id") if m.universe == "demand_lines" else ("dc_id", "sku_id")
        weekly = context.weekly_demand(rows, by=by).groupby("period").forecast_qty.sum()
        out["forecast_units_per_week"] = {"mean": round(float(weekly.mean()), 0), "max": round(float(weekly.max()), 0),
                                          "peak_week": weekly.idxmax() if len(weekly) else None}
    return out


def _propose(agent, draft_id, measure, scope, operation, rationale, value=None, window=None, unit=None,
             applies_to=None, options=None):
    if measure in catalog.MEASURES and not _domain_ok(agent, measure):
        owners = [a for a, d in catalog.AGENT_DOMAINS.items() if catalog.MEASURES[measure].domain in d]
        return {"ok": False, "error": f"{agent} may not write {measure}; that belongs to {owners}"}
    raw = {"measure": measure, "scope": scope, "operation": operation, "value": value, "window": window,
           "unit": unit, "applies_to": applies_to, "options": options, "rationale": rationale}
    return drafts.add_override(draft_id, raw, agent)


def _remove(agent, draft_id, override_id=None, group=None):
    d = drafts.load_draft(draft_id)
    hits = [o for o in d["overrides"] if o["id"] == override_id or (group and o.get("group") == group)]
    blocked = [o["id"] for o in hits
               if not _domain_ok(agent, o["measure"]) and o.get("provenance", {}).get("agent") != agent]
    if blocked:
        return {"ok": False, "error": f"{agent} may not remove {blocked}: outside its measure domains and "
                                      f"authored by another agent"}
    return drafts.remove_override(draft_id, agent, override_id=override_id, group=group)


def _library(agent, draft_id, scenario_id, anticipated=True):
    planned = macros.library_overrides(scenario_id, anticipated)
    foreign = sorted({o["measure"] for o in planned if not _domain_ok(agent, o["measure"])})
    if foreign:
        return {"ok": False, "error": f"{scenario_id} produces {foreign}, outside {agent}'s domains; "
                                      f"ask the orchestrator to route it"}
    return macros.apply_library_scenario(draft_id, scenario_id, agent, anticipated)


def _card(agent, scenario_folder):
    path = scenario.scenario_dir(scenario_folder) / "scenario_card.md"
    if not path.exists():
        raise ValueError(f"no card for {scenario_folder}; build_scenario_config first")
    return {"card": path.read_text(encoding="utf-8"), "approval": scenario.approval_status(scenario_folder)["state"]}


def _measures(agent, measure=None):
    if measure:
        if measure not in catalog.MEASURES:
            raise ValueError(f"unknown measure {measure!r}; known: {sorted(catalog.MEASURES)}")
        return catalog.describe(measure)
    names = catalog.measures_for_agent(agent) or list(catalog.MEASURES)
    return {n: catalog.describe(n) for n in names}


def _baseline_kpis(agent, scenario_folder=None, run=None):
    base = drafts.default_baseline()
    scn, r = scenario_folder or base["scenario"], run or base["run"]
    return {"scenario": scn, "run": r, "kpis": analysis.kpis(scn, r)}


# ------------------------------------------------------------------- tools --
TOOLS: dict[str, Tool] = {
    "list_dimensions": Tool(
        "Master-data vocabulary: forward horizon, echelons, regions with their DCs, categories and "
        "subcategories, brands, segments with current service levels, clusters, plants with line capacities, "
        "customers, transport modes and every overridable measure. Call this before naming any ID.",
        _obj({}), lambda agent: context.dimensions()),
    "describe_measure": Tool(
        "Full contract for one measure (or all measures you may write): legal scope keys, operations with "
        "value bounds and units, which side it can touch (plan = MEIO sizing, actual = Twin simulation), "
        "whether it takes a window, and the exact MEIO/Twin hook it changes.",
        _obj({"measure": _STR}), _measures),
    "resolve_window": Tool(
        "Convert dates, a month (YYYY-MM: weeks whose Monday falls in it) or a start week plus duration "
        "into the inclusive ISO-week window an override stores. Never compute ISO weeks by hand.",
        _obj({"start_date": {**_STR, "description": "YYYY-MM-DD"}, "end_date": {**_STR, "description": "YYYY-MM-DD"},
              "month": {**_STR, "description": "YYYY-MM"}, "start_period": _STR, "end_period": _STR,
              "weeks": {"type": "integer"}}),
        lambda agent, **kw: context.resolve_window(**kw)),
    "resolve_scope": Tool(
        "Check a scope before proposing: canonicalises values (with did-you-mean hints), counts the rows it "
        "matches for the measure, and shows the forecast volume involved.",
        _obj({"measure": _STR, "scope": _SCOPE}, ("measure", "scope")), _resolve_scope),
    "get_entity_profile": Tool(
        "Profile of one SKU, DC, plant, lane or customer: attributes, forward demand, routing, capacity and "
        "load, opening stock. Use it to ground magnitudes and explain a scope.",
        _obj({"kind": {"type": "string", "enum": ["sku", "dc", "plant", "lane", "customer"]}, "id": _STR},
             ("kind", "id")),
        lambda agent, kind, id: context.entity_profile(kind, id)),
    "list_disruption_library": Tool(
        "The dataset's 30 labelled forward-year disruptions (demand surges, forecast misses, lane outages, "
        "lead-time spikes, plant shortfalls, regional events) with magnitude, window, recovery time and "
        "probability.",
        _obj({"scenario_type": {"type": "string", "enum": sorted(context.library().scenario_type.unique())}}),
        lambda agent, scenario_type=None: context.library_rows(scenario_type)),
    "get_baseline_kpis": Tool(
        "Headline KPIs of the baseline run (or any finished run): fill rates, in-full, lost sales, inventory "
        "value, turns, days of supply, holding/stockout/transport and total landed cost.",
        _obj({"scenario_folder": _STR, "run": _STR}), _baseline_kpis),
    "pipeline_readiness": Tool(
        "Which overrides the installed MEIO/Twin engine can actually apply, and what is missing. Scenarios "
        "using unsupported measures can be drafted and approved but will not run.",
        _obj({}), lambda agent: engine.readiness()),
    "create_draft": Tool(
        "Start a scenario draft. Records the planner's request verbatim and the hypothesis being tested. MEIO is "
        "whatever scenario_config.json defines (scenarios never choose it), and the baseline for comparison is "
        "the shared root-config run. Returns the draft_id that every specialist writes into.",
        _obj({"scenario_name": {**_STR, "description": "short title, becomes the folder name"},
              "description": _STR, "request_text": {**_STR, "description": "the planner's words, verbatim"},
              "hypothesis": _STR},
             ("scenario_name", "description", "request_text")),
        lambda agent, **kw: drafts.create_draft(created_by=agent, **kw)),
    "list_overrides": Tool(
        "The draft's metadata and every override in it, compactly (id, measure, scope, window, change, sides, "
        "rationale, authoring agent).",
        _obj({"draft_id": _DRAFT}, ("draft_id",)), lambda agent, draft_id: drafts.list_overrides(draft_id)),
    "propose_override": Tool(
        "Add one override to a draft. It is validated against the catalog and master data, canonicalised, "
        "given an OVR id and an impact preview; on any error nothing is saved and the errors explain the fix. "
        "Percentages become multipliers (+25% -> scale 1.25). Omit applies_to to use the measure default.",
        None, _propose),
    "remove_override": Tool(
        "Remove one override (override_id) or a macro's linked set (group) from a draft.",
        _obj({"draft_id": _DRAFT, "override_id": _STR, "group": _STR}, ("draft_id",)), _remove),
    "preview_impact": Tool(
        "Static size of the draft's changes in input terms (units, $, lanes, capacity vs load, weeks of "
        "cover) -- computed instantly, no simulation. Outcomes come only from a run.",
        _obj({"draft_id": _DRAFT, "override_id": _STR}, ("draft_id",)),
        lambda agent, draft_id, override_id=None: scenario.preview_draft(draft_id, override_id)),
    "validate_draft": Tool(
        "Full validation of a draft: per-override errors, overlapping overrides that compound (double "
        "counting), plan/actual coverage, whether the baseline on disk still matches the config, and engine "
        "support.",
        _obj({"draft_id": _DRAFT}, ("draft_id",)), lambda agent, draft_id: drafts.validate_draft(draft_id)),
    "apply_library_scenario": Tool(
        "Add a disruption-library scenario (e.g. SCN0018) as linked overrides. anticipated=true lets MEIO "
        "plan for it where it can; false makes it a surprise (Twin only).",
        _obj({"draft_id": _DRAFT, "scenario_id": _STR, "anticipated": {"type": "boolean"}},
             ("draft_id", "scenario_id")), _library),
    "add_price_event": Tool(
        "Translate a price move into demand overrides with the dataset's category elasticities: kind=promo is "
        "a temporary discount over a window (lift = (1-d)^e_promo, plus the post-promotion pantry-loading dip "
        "for two weeks); kind=everyday is a lasting list-price change ((1+p)^e_base). price_change_pct is a "
        "fraction: -0.20 for 20% off.",
        _obj({"draft_id": _DRAFT, "scope": _SCOPE, "price_change_pct": {"type": "number"},
              "kind": {"type": "string", "enum": ["promo", "everyday"]}, "window": _WINDOW,
              "b2b_customer_id": _STRS, "post_promo_dip": {"type": "boolean"}, "rationale": _STR},
             ("draft_id", "scope", "price_change_pct", "kind", "rationale")),
        lambda agent, draft_id, **kw: macros.add_price_event(draft_id, agent, **kw)),
    "apply_service_ladder": Tool(
        "Replace the 3-segment service levels with the segmentation deliverable's per-cluster CSL ladder "
        "(one service.target_csl override per cluster), optionally within a scope and window.",
        _obj({"draft_id": _DRAFT, "scope": _SCOPE, "window": _WINDOW}, ("draft_id",)),
        lambda agent, draft_id, **kw: macros.apply_service_ladder(draft_id, agent, **kw)),
    "build_scenario_config": Tool(
        "Validate the draft and write its scenario folder under output datasets/overrides/: scenario_config.json "
        "(the config's own MEIO, seed and opening state, overrides under changes.overrides) and the scenario card "
        "a person approves. Fails on validation errors. Rebuilding after edits invalidates any earlier approval.",
        _obj({"draft_id": _DRAFT}, ("draft_id",)), lambda agent, draft_id: scenario.build_config(draft_id)),
    "get_scenario_card": Tool(
        "The scenario card (what will run, and how big each change is) and its approval state.",
        _obj({"scenario_folder": _SCENARIO}, ("scenario_folder",)), _card),
    "run_scenario": Tool(
        "Launch an APPROVED scenario: MEIO re-plans and the Twin simulates each evaluation arm, in the "
        "background. Refuses if the config is unapproved, changed since approval, would overwrite outputs, "
        "or uses measures the engine cannot apply yet.",
        _obj({"scenario_folder": _SCENARIO}, ("scenario_folder",)),
        lambda agent, scenario_folder: scenario.run_scenario(scenario_folder)),
    "get_run_status": Tool(
        "State of a launched scenario run per arm, with the log tail.",
        _obj({"scenario_folder": _SCENARIO}, ("scenario_folder",)),
        lambda agent, scenario_folder: scenario.run_status(scenario_folder)),
    "compare_runs": Tool(
        "Headline KPIs for each finished arm against the baseline, with deltas (rates in percentage points) "
        "and, when both arms ran, the value of re-planning (prepared minus unprepared).",
        _obj({"scenario_folder": _SCENARIO, "runs": _STRS, "baseline_scenario": _STR, "baseline_run": _STR},
             ("scenario_folder",)),
        lambda agent, scenario_folder, **kw: analysis.compare_runs(scenario_folder, **kw)),
    "service_breakdown": Tool(
        "Customer fill rate of one arm by a business dimension (segment, category, cluster, region, echelon, "
        "DC...) against the baseline, worst delta first.",
        _obj({"scenario_folder": _SCENARIO, "run": _STR, "by": {"type": "string", "enum": _DIMS},
              "baseline_scenario": _STR, "baseline_run": _STR, "top": {"type": "integer"}},
             ("scenario_folder", "run", "by")),
        lambda agent, scenario_folder, run, by, **kw: analysis.service_breakdown(scenario_folder, run, by, **kw)),
    "scope_service": Tool(
        "Customer fill inside one override's own DC-SKU scope and window, for baseline and every arm, with "
        "the value of re-planning. Use it for each demand, inventory or service override: a local event is "
        "a small share of the network, so the headline fill rate dilutes it.",
        _obj({"scenario_folder": _SCENARIO, "override_id": _STR, "baseline_scenario": _STR, "baseline_run": _STR},
             ("scenario_folder", "override_id")),
        lambda agent, scenario_folder, override_id, **kw: analysis.scope_service(scenario_folder, override_id, **kw)),
    "policy_diff": Tool(
        "How the re-planned MEIO policy differs from the baseline policy: safety stock units and $, and "
        "order-up-to, summed by a dimension. Pass the overrides' window (start_period/end_period, "
        "ISO weeks) to see the change where it happens; without one, the policies are averaged over "
        "all 52 weeks and a short event looks tiny.",
        _obj({"scenario_folder": _SCENARIO, "by": {"type": "string", "enum": _DIMS}, "baseline_scenario": _STR,
              "start_period": _STR, "end_period": _STR},
             ("scenario_folder",)),
        lambda agent, scenario_folder, **kw: analysis.policy_diff(scenario_folder, **kw)),
    "get_application_receipts": Tool(
        "What MEIO and the Twin recorded as actually applied (matched rows per override). Check before "
        "reporting results: an override with no receipt did not happen.",
        _obj({"scenario_folder": _SCENARIO, "run": _STR}, ("scenario_folder", "run")),
        lambda agent, scenario_folder, run: analysis.application_receipts(scenario_folder, run)),
    # runtime tools -- the loop intercepts these
    "delegate": Tool(
        "Hand one specialist a self-contained brief (draft_id, the exact change wanted, magnitudes, windows, "
        "scope hints, anything ambiguous). Returns the specialist's report of what it added or why it could not.",
        _obj({"agent": {"type": "string", "enum": list(SPECIALISTS) + ["validator_agent", "analyst_agent"]},
              "task": _STR}, ("agent", "task")), None),
    "request_human_approval": Tool(
        "Show the planner the scenario card and ask them to approve the run. Only a person can approve; "
        "returns approved / declined / pending.",
        _obj({"scenario_folder": _SCENARIO}, ("scenario_folder",)), None),
}

_CONTEXT = ["list_dimensions", "describe_measure", "resolve_window", "resolve_scope", "get_entity_profile"]
_AUTHOR = ["list_overrides", "propose_override", "remove_override", "preview_impact"]

AGENTS: dict[str, dict] = {
    "orchestrator": {"effort": "high", "tools": _CONTEXT + [
        "list_disruption_library", "get_baseline_kpis", "pipeline_readiness", "create_draft", "list_overrides",
        "remove_override", "preview_impact", "validate_draft", "apply_library_scenario", "build_scenario_config",
        "get_scenario_card", "request_human_approval", "run_scenario", "get_run_status", "delegate"]},
    "demand_agent": {"effort": "medium", "tools": _CONTEXT + _AUTHOR + [
        "list_disruption_library", "apply_library_scenario"]},
    "supply_agent": {"effort": "medium", "tools": _CONTEXT + _AUTHOR + [
        "list_disruption_library", "apply_library_scenario"]},
    "inventory_agent": {"effort": "medium", "tools": _CONTEXT + _AUTHOR},
    "service_policy_agent": {"effort": "medium", "tools": _CONTEXT + _AUTHOR + ["apply_service_ladder"]},
    "economics_agent": {"effort": "medium", "tools": _CONTEXT + _AUTHOR + ["add_price_event"]},
    "validator_agent": {"effort": "high", "tools": _CONTEXT + [
        "list_overrides", "preview_impact", "validate_draft", "pipeline_readiness", "list_disruption_library"]},
    "analyst_agent": {"effort": "medium", "tools": [
        "get_baseline_kpis", "compare_runs", "service_breakdown", "scope_service", "policy_diff",
        "get_application_receipts",
        "get_scenario_card", "list_overrides", "get_entity_profile", "list_dimensions"]},
}


def _propose_schema(agent: str) -> dict:
    measures = catalog.measures_for_agent(agent)
    return _obj({
        "draft_id": _DRAFT,
        "measure": {"type": "string", "enum": measures, "description": "the measure to change (describe_measure)"},
        "scope": _SCOPE,
        "operation": {"type": "string", "enum": sorted({op for m in measures for op in catalog.MEASURES[m].ops})},
        "value": {"type": "number", "description": "multiplier for scale; amount for add/set/floor/cap; omit for block"},
        "unit": {"type": "string", "enum": sorted({u for m in measures for s in catalog.MEASURES[m].ops.values()
                                                   for u in s.units})},
        "window": _WINDOW,
        "applies_to": {"type": "array", "items": {"type": "string", "enum": list(catalog.SIDES)},
                       "description": "plan = MEIO plans for it; actual = the Twin simulates it"},
        "options": {"type": "object"},
        "rationale": {**_STR, "description": "why, in planner terms, traceable to the request"},
    }, ("draft_id", "measure", "scope", "operation", "rationale"))


def tool_specs(agent: str) -> list[dict]:
    """Claude tool definitions for one agent, in a fixed order (stable for prompt caching)."""
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}; known: {sorted(AGENTS)}")
    specs = []
    for name in AGENTS[agent]["tools"]:
        tool = TOOLS[name]
        schema = _propose_schema(agent) if name == "propose_override" else tool.input_schema
        specs.append({"name": name, "description": tool.description, "input_schema": schema})
    return specs


def to_jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if math.isnan(float(obj)) or math.isinf(float(obj)) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if obj is pd.NA or obj is pd.NaT:
        return None
    if hasattr(obj, "as_posix"):
        return paths.rel(obj)
    return obj


def dispatch(agent: str, name: str, args: dict | None) -> dict:
    """Run one tool call for one agent. Never raises: errors come back as data."""
    if agent not in AGENTS:
        return {"ok": False, "error": f"unknown agent {agent!r}"}
    if name not in AGENTS[agent]["tools"]:
        return {"ok": False, "error": f"{agent} is not allowed to call {name}; its tools: {AGENTS[agent]['tools']}"}
    tool = TOOLS[name]
    if tool.handler is None:
        return {"ok": False, "error": f"{name} is handled by the agent runtime, not the tool layer"}
    try:
        result = tool.handler(agent, **(args or {}))
    except TypeError as exc:
        return {"ok": False, "error": f"bad arguments for {name}: {exc}"}
    except (ValueError, KeyError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # surface, don't crash the loop
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result = to_jsonable(result)
    if isinstance(result, dict):
        return result if "ok" in result else {"ok": True, **result}
    return {"ok": True, "result": result}
