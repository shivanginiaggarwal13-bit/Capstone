"""
Macro tools: one planner-level concept in, several linked primitive overrides out.

  apply_library_scenario  a row of the disruption library (input datasets/scenarios.csv)
  add_price_event         a temporary promotion or an everyday price change -> demand lift
                          (+ the post-promotion pantry-loading dip), using the dataset's
                          own elasticities and dip formula
  apply_service_ladder    the segmentation deliverable's per-cluster CSL ladder

Every override a macro emits shares one `group` id, so it is reviewed, overlap-checked
and removed as a unit, and carries the macro's assumptions in its rationale. Macros
write through drafts.add_override like everything else -- no validation is skipped --
and are all-or-nothing: if one part fails, the parts already added are rolled back.
"""
from __future__ import annotations

from scipy.stats import norm

from . import catalog, context, drafts


def _add_all(draft_id: str, overrides: list[dict], agent: str) -> dict:
    group = overrides[0]["group"]
    added, failed = [], []
    for o in overrides:
        res = drafts.add_override(draft_id, o, agent)
        if res["ok"]:
            added.append({"id": res["override"]["id"], "measure": o["measure"],
                          "window": res["override"].get("window"), "value": o.get("value"),
                          "impact": res["impact"], "warnings": res["warnings"]})
        else:
            failed.append({"measure": o["measure"], "errors": res["errors"]})
    if failed:
        if added:
            drafts.remove_override(draft_id, agent, group=group)
        return {"ok": False, "group": group, "errors": failed,
                "note": f"nothing saved ({len(added)} part(s) rolled back)" if added else "nothing saved"}
    return {"ok": True, "group": group, "added": added}


def _group_id(draft_id: str, prefix: str) -> str:
    return f"{prefix}-{drafts.load_draft(draft_id)['next_seq']:04d}"


# ------------------------------------------------------------ library ----
def library_overrides(scenario_id: str, anticipated: bool = True) -> list[dict]:
    """Translate one disruption-library row into overrides. The library's end_date
    runs one week past start + duration_weeks on every row; duration_weeks is the
    stated magnitude, so the window follows it and end_date is kept in provenance."""
    lib = context.library()
    hit = lib[lib.scenario_id.str.casefold() == str(scenario_id).casefold()]
    if hit.empty:
        raise ValueError(f"unknown library scenario {scenario_id!r}; see list_disruption_library")
    r = hit.iloc[0]
    window = context.resolve_window(start_date=r.start_date, weeks=int(r.duration_weeks))["window"]
    sides = ["plan", "actual"] if anticipated else ["actual"]
    base = {"group": r.scenario_id, "window": window,
            "provenance": {"library_ref": r.scenario_id, "library_end_date": r.end_date,
                           "probability": float(r.probability), "ttr_days": float(r.ttr_days),
                           "tts_days": float(r.tts_days)}}
    t, mag = r.scenario_type, float(r.magnitude_value)
    note = f"{r.scenario_id} ({t}): {r.notes}."

    if t == "demand_surge":
        _require_demand(r.scenario_id, r.affected_node_id, r.affected_sku_id)
        return [{**base, "measure": "demand.volume", "operation": "scale", "value": round(1 + mag, 4),
                 "scope": {"dc_id": [r.affected_node_id], "sku_id": [r.affected_sku_id]}, "applies_to": sides,
                 "rationale": f"{note} +{mag:.0%} demand for {r.affected_sku_id} at {r.affected_node_id}."}]
    if t == "forecast_miss":
        # "seasonal peak under-called by the baseline forecast": the plan never knew
        return [{**base, "measure": "demand.volume", "operation": "scale", "value": round(1 + mag, 4),
                 "scope": {"sku_id": [r.affected_sku_id]}, "applies_to": ["actual"],
                 "rationale": f"{note} Actual demand runs {mag:.0%} above the published forecast at every "
                              f"DC; MEIO keeps sizing on the forecast."}]
    if t == "leadtime_spike":
        return [{**base, "measure": "supply.lead_time_mean", "operation": "add", "value": round(mag, 3),
                 "scope": {"destination_node_id": [r.affected_node_id]}, "applies_to": sides,
                 "rationale": f"{note} +{mag:.1f} days on every inbound lane to {r.affected_node_id}."}]
    if t == "lane_outage":
        lane = _primary_inbound_lane(r.affected_node_id)
        out = [{**base, "measure": "supply.lane_outage", "operation": "block", "scope": {"lane_id": [lane]},
                "applies_to": ["actual"],
                "rationale": f"{note} Primary inbound lane {lane} to {r.affected_node_id} is down; "
                             f"recovery takes {r.ttr_days:.0f} days."}]
        if anticipated:
            days = 7 * len(context.window_periods(window))
            out.append({**base, "measure": "supply.lead_time_mean", "operation": "add", "value": float(days),
                        "scope": {"lane_id": [lane]}, "applies_to": ["plan"],
                        "rationale": f"{note} Known in advance: MEIO sizes {r.affected_node_id} for {days} "
                                     f"extra days of lead time while the lane is down."})
        return out
    if t == "plant_shortfall":
        return [{**base, "measure": "supply.schedule_adherence", "operation": "scale", "value": round(1 - mag, 4),
                 "scope": {"plant_id": [r.affected_node_id], "production_family": [r.affected_family]},
                 "applies_to": ["actual"],
                 "rationale": f"{note} {r.affected_family} attainment at {r.affected_node_id} falls {mag:.0%}; "
                              f"recovery takes {r.ttr_days:.0f} days."}]
    if t == "geographic_disruption":
        plants = context.plants()
        local = plants[plants.region == r.region].plant_id.tolist()
        out = [{**base, "measure": "supply.lead_time_mean", "operation": "add", "value": float(r.ttr_days),
                "scope": {"region": [r.region], "echelon": ["RDC", "CDC"]}, "applies_to": sides,
                "rationale": f"{note} Lanes into {r.region} DCs slow by the {r.ttr_days:.0f}-day recovery time."}]
        if local:
            out.append({**base, "measure": "supply.schedule_adherence", "operation": "scale",
                        "value": round(1 - mag, 4), "scope": {"plant_id": local}, "applies_to": ["actual"],
                        "rationale": f"{note} Plants in {r.region} ({', '.join(local)}) lose {mag:.0%} of output."})
        return out
    raise ValueError(f"no template for scenario_type {t!r}")


def _require_demand(scenario_id: str, dc_id: str, sku_id: str) -> None:
    """A surge multiplies demand, and several library rows name a (DC, SKU) pair with
    no forecast, history or stock -- the SKU is not carried there, so the surge would
    change nothing. Refuse with the alternatives instead of silently re-scoping."""
    fd = context.forward_demand()
    if ((fd.dc_id == dc_id) & (fd.sku_id == sku_id)).any():
        return
    region = context.nodes().set_index("dc_id").region.get(dc_id)
    carriers = fd[fd.sku_id == sku_id].dc_id.unique()
    near = sorted(d for d in carriers if context.nodes().set_index("dc_id").region.get(d) == region)
    raise ValueError(
        f"{scenario_id} surges {sku_id} at {dc_id}, but {sku_id} has no forecast, history or opening stock at "
        f"{dc_id} -- the surge would change nothing. Alternatives: surge {sku_id} at the {region} DCs that carry "
        f"it ({near or 'none'}), at every DC that carries it ({len(carriers)} DCs), or treat it as new "
        f"distribution at {dc_id} (needs forecast rows and a policy line -- Phase 3).")


def _primary_inbound_lane(dc_id: str) -> str:
    """The lane a node is actually supplied on: from the parent most of its SKUs
    source from (sku_sourcing_map), fastest lane first."""
    smap = context.sourcing_map()
    parents = smap[smap.dc_id == dc_id].origin_node_id.value_counts()
    ln = context.lanes()
    ln = ln[(ln.destination_node_id == dc_id) & ln.origin_node_id.isin(parents.index)]
    if ln.empty:
        raise ValueError(f"no routed inbound lane to {dc_id}")
    ln = ln.assign(skus=ln.origin_node_id.map(parents)).sort_values(
        ["skus", "lead_time_mean_days"], ascending=[False, True])
    return ln.lane_id.iloc[0]


def apply_library_scenario(draft_id: str, scenario_id: str, agent: str, anticipated: bool = True) -> dict:
    return _add_all(draft_id, library_overrides(scenario_id, anticipated), agent)


# --------------------------------------------------------- price events ----
def price_event_overrides(scope: dict, window: dict | None, price_change_pct: float, kind: str,
                          group: str, b2b_customer_id: list[str] | None = None,
                          post_promo_dip: bool = True, rationale: str = "") -> list[dict]:
    """Demand response to a price move, per category, mirroring the generator:
    promo lift = (1 - discount) ** e_promo (capped at 12x); everyday change =
    (1 + change) ** e_base; post-promo dip = storability x min(depth/0.30, 1) x 0.20
    in week +1 and half that in week +2. Category elasticities are the published
    recovered values, so individual SKUs will scatter around these responses."""
    if kind not in ("promo", "everyday"):
        raise ValueError("kind must be 'promo' or 'everyday'")
    if not -0.9 <= price_change_pct <= 0.5:
        raise ValueError("price_change_pct is a fraction, e.g. -0.20 for a 20% discount")
    if kind == "promo" and (price_change_pct >= 0 or not window):
        raise ValueError("a promotion is a discount (negative price_change_pct) over a window")
    allowed = catalog.SKU_KEYS + catalog.NODE_KEYS
    canonical = {}
    for key, values in (scope or {}).items():
        if key not in allowed:
            raise ValueError(f"price-event scope keys are {list(allowed)}; got {key!r}")
        vals, problems = context.canonicalize(key, values if isinstance(values, list) else [values])
        if problems:
            raise ValueError("; ".join(problems))
        canonical[key] = vals
    if b2b_customer_id:
        customers, problems = context.canonicalize("b2b_customer_id", b2b_customer_id)
        if problems:
            raise ValueError("; ".join(problems))
        canonical["b2b_customer_id"] = customers
    cats = sorted(context.match("demand.volume", canonical or {"all": True}).category.unique())
    if not cats:
        raise ValueError(f"scope {scope} matches no demand lines")

    periods = context.forward_periods()
    depth = -price_change_pct
    out = []
    for cat in cats:
        cat_scope = {**canonical, "category": [cat]}
        if kind == "promo":
            lift = min((1 - depth) ** catalog.PROMO_ELASTICITY[cat], catalog.PROMO_LIFT_CAP)
            why = (f"{depth:.0%} temporary discount on {cat}: lift = (1 - {depth:.2f}) ** "
                   f"{catalog.PROMO_ELASTICITY[cat]} = x{lift:.3f} (category promo elasticity).")
        else:
            lift = (1 + price_change_pct) ** catalog.BASE_ELASTICITY[cat]
            why = (f"{price_change_pct:+.0%} everyday price change on {cat}: demand x{lift:.3f} "
                   f"(category base elasticity {catalog.BASE_ELASTICITY[cat]}).")
        out.append({"group": group, "measure": "demand.volume", "operation": "scale", "value": round(lift, 4),
                    "scope": cat_scope, "window": window, "applies_to": ["plan", "actual"],
                    "rationale": f"{rationale} {why}".strip(),
                    "provenance": {"macro": "add_price_event", "kind": kind, "price_change_pct": price_change_pct}})
        if kind != "promo" or not post_promo_dip:
            continue
        dip = catalog.STORABILITY[cat] * min(depth / 0.30, 1.0) * 0.20
        end = periods.index(window["end_period"])
        for k, scale in ((1, 1 - dip), (2, 1 - 0.5 * dip)):
            if end + k < len(periods) and dip > 0.001:
                week = periods[end + k]
                out.append({"group": group, "measure": "demand.volume", "operation": "scale",
                            "value": round(scale, 4), "scope": cat_scope,
                            "window": {"start_period": week, "end_period": week}, "applies_to": ["plan", "actual"],
                            "rationale": f"Post-promotion pantry-loading dip, week +{k} after the {cat} promotion: "
                                         f"storability {catalog.STORABILITY[cat]} x min({depth:.2f}/0.30, 1) x 0.20 "
                                         f"-> x{scale:.3f}.",
                            "provenance": {"macro": "add_price_event", "kind": "post_promo_dip"}})
    return out


def add_price_event(draft_id: str, agent: str, scope: dict, price_change_pct: float, kind: str = "promo",
                    window: dict | None = None, b2b_customer_id: list[str] | None = None,
                    post_promo_dip: bool = True, rationale: str = "") -> dict:
    overrides = price_event_overrides(scope, window, price_change_pct, kind, _group_id(draft_id, "PRICE"),
                                      b2b_customer_id, post_promo_dip, rationale)
    result = _add_all(draft_id, overrides, agent)
    if result["ok"] and kind == "promo":
        planned = 0.0
        for o in overrides:
            if o["provenance"]["kind"] == "promo":
                lines = context.match("demand.volume", o["scope"])
                planned += float(context.weekly_demand(lines, context.window_periods(window),
                                                       by=("dc_id", "sku_id", "b2b_customer_id"))
                                 .forecast_promo_lift_qty.sum())
        if planned > 0:
            result["warning"] = (f"the published forecast already carries {planned:,.0f} units of planned promo "
                                 f"lift in this scope and window; this event stacks on top -- remove it if it "
                                 f"is the same promotion")
        result["note"] = "margin during the promotion is not re-valued (economics overrides are static until Phase 3)"
    return result


# -------------------------------------------------------- service ladder ----
def apply_service_ladder(draft_id: str, agent: str, scope: dict | None = None,
                         window: dict | None = None) -> dict:
    """Per-cluster CSL from the segmentation deliverable (sku_clusters.recommended_csl)
    -- the rerun_with_cluster_csl.py experiment, expressed as overrides."""
    ladder = context.skus().groupby(["cluster", "archetype"], as_index=False).recommended_csl.first()
    group = _group_id(draft_id, "LADDER")
    overrides = []
    for r in ladder.dropna(subset=["recommended_csl"]).itertuples(index=False):
        overrides.append({"group": group, "measure": "service.target_csl", "operation": "set",
                          "value": float(r.recommended_csl), "scope": {**(scope or {}), "cluster": [r.cluster]},
                          "window": window, "applies_to": ["plan"],
                          "rationale": f"Segmentation ladder: cluster {r.cluster} ({r.archetype}) at CSL "
                                       f"{r.recommended_csl:.3f} (z {norm.ppf(r.recommended_csl):.2f}) instead of "
                                       f"the 3-segment default.",
                          "provenance": {"macro": "apply_service_ladder", "ladder": "segmentation_clusters"}})
    return _add_all(draft_id, overrides, agent)
