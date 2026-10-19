"""
Static impact previews: what an override changes in the INPUT data, computed in
well under a second and without running MEIO or the Twin. The planner sees the
size of a change before approving a run ("+25% is 41,000 units, 0.3% of network
demand"), and the validator uses the same numbers to catch magnitudes that are off
by an order of magnitude.

Previews describe inputs, never outcomes -- fill rate, lost sales and cost come
only from a run. Service-level and policy previews scale the static forward policy
(output datasets/history/forward_policy.parquet), so they are first-order estimates of
the safety-stock investment, labelled as such.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.stats import norm

from models.overrides import apply_op as _apply   # the engine's own operation rule

from . import catalog, context

ADHERENCE_MEAN = 0.95


def _r(x, nd=0):
    return round(float(x), nd) if nd else int(round(float(x)))


def preview(o: dict) -> dict:
    fn = _PREVIEWS.get(o["measure"])
    if fn is None:
        return {}
    try:
        return fn(o)
    except Exception as exc:  # a preview must never block authoring
        return {"note": f"preview unavailable: {exc}"}


def preview_all(overrides: list[dict]) -> dict:
    per = {o["id"]: preview(o) for o in overrides}
    demand = [o for o in overrides if o["measure"] == "demand.volume"]
    totals = {}
    for side in catalog.SIDES:
        delta = sum(per[o["id"]].get("delta_units", 0) for o in demand if side in o["applies_to"])
        if delta:
            totals[f"demand_delta_units_{side}"] = delta
    return {"per_override": per, "totals": totals,
            "note": "Overlapping demand overrides compound, so totals are an upper-level guide."}


# --------------------------------------------------------------- demand ----
def _demand_volume(o: dict) -> dict:
    lines = context.match(o["measure"], o["scope"])
    periods = context.window_periods(o.get("window"))
    fd = context.weekly_demand(lines, periods, by=("dc_id", "sku_id", "b2b_customer_id"))
    base = fd.forecast_qty.to_numpy(float)
    delta = np.maximum(_apply(base, o["operation"], o["value"]), 0.0) - base
    sk = context.skus().set_index("sku_id")
    fwd = context.forward_demand()
    in_window = fwd[fwd.period.isin(set(periods))].forecast_qty.sum()
    return {
        "basis": "published forward forecast; the Twin realises ~1.07x it on average",
        "weeks": len(periods), "demand_lines": int(len(lines)),
        "skus": int(lines.sku_id.nunique()), "dcs": int(lines.dc_id.nunique()),
        "baseline_units": _r(base.sum()), "delta_units": _r(delta.sum()),
        "delta_pct_of_scope": _r(delta.sum() / base.sum(), 4) if base.sum() else None,
        "delta_pct_of_network_in_window": _r(delta.sum() / in_window, 4) if in_window else None,
        "delta_list_revenue_usd": _r((delta * fd.sku_id.map(sk.list_price_usd).to_numpy()).sum()),
        "delta_gross_margin_usd": _r((delta * fd.sku_id.map(sk.margin_usd).to_numpy()).sum()),
        "planned_promo_lift_units_in_scope": _r(fd.forecast_promo_lift_qty.sum()),
    }


def _demand_volatility(o: dict) -> dict:
    pairs = context.match(o["measure"], o["scope"])
    periods = context.window_periods(o.get("window"))
    units = context.weekly_demand(pairs, periods).forecast_qty.sum()
    return {"weeks": len(periods), "pairs": int(len(pairs)), "baseline_units_in_scope": _r(units),
            "effect": f"forecast-error spread x{o['value']}; the demand-variability term of safety "
                      f"stock scales by about the same factor"}


# --------------------------------------------------------------- supply ----
def _routed_units(lanes: pd.DataFrame) -> float:
    """Forward customer demand that physically moves over these lanes: pairs routed
    on the lane, plus -- for a lane into an RDC -- the CDC pairs that RDC re-supplies."""
    smap = context.sourcing_map()
    routed = smap.merge(lanes[["origin_node_id", "destination_node_id"]],
                        left_on=["origin_node_id", "dc_id"],
                        right_on=["origin_node_id", "destination_node_id"])[["dc_id", "sku_id"]]
    rdc = routed[routed.dc_id.str.startswith("RDC")]
    downstream = smap.merge(rdc.rename(columns={"dc_id": "origin_node_id"}),
                            on=["origin_node_id", "sku_id"])[["dc_id", "sku_id"]]
    pairs = pd.concat([routed, downstream]).drop_duplicates()
    return float(context.weekly_demand(pairs).forecast_qty.sum())


def _lane(o: dict) -> dict:
    lanes = context.match(o["measure"], o["scope"])
    periods = context.window_periods(o.get("window"))
    field = {"supply.lead_time_mean": "lead_time_mean_days", "supply.lead_time_std": "lead_time_std_days",
             "economics.logistics_cost": "logistics_cost_per_unit_usd"}.get(o["measure"])
    table = lanes[["lane_id", "origin_node_id", "destination_node_id", "transport_mode"]].copy()
    if field:
        after = _apply(lanes[field], o["operation"], o["value"])
        if field == "lead_time_mean_days":
            after = np.maximum(after, 0.5)
        table["before"], table["after"] = lanes[field].to_numpy(), np.round(after, 2)
    units = _routed_units(lanes)
    total = context.forward_demand().forecast_qty.sum()
    out = {"lanes": table.head(15).to_dict("records"), "lanes_matched": int(len(lanes)),
           "lanes_carrying_flow": int(lanes.lane_id.isin(_flowing_lanes()).sum()),
           "forward_units_through_lanes_full_horizon": _r(units),
           "share_of_network_demand": _r(units / total, 4) if total else None}
    if o["measure"] != "economics.logistics_cost":
        out["weeks"] = len(periods)
    if o["measure"] == "supply.lane_outage":
        out["effect"] = (f"dispatches on these lanes wait until the window ends ({len(periods)} weeks); "
                         "no failover routing until Phase 3")
    if o["measure"] == "economics.logistics_cost":
        avg_delta = float((table["after"] - table["before"]).mean()) if len(table) else 0.0
        out["approx_delta_transport_usd_per_year"] = _r(units * avg_delta)
    if out["lanes_carrying_flow"] < out["lanes_matched"]:
        out["warning"] = "some matched lanes carry no simulated flow (failover/lateral lanes)"
    return out


@lru_cache(maxsize=None)
def _flowing_lanes() -> frozenset:
    smap = context.sourcing_map()
    used = smap[["origin_node_id", "dc_id"]].drop_duplicates()
    ln = context.lanes().merge(used, left_on=["origin_node_id", "destination_node_id"],
                               right_on=["origin_node_id", "dc_id"], suffixes=("", "_"))
    plant_lanes = context.lanes()[context.lanes().origin_node_type == "PLANT"]
    return frozenset(ln.lane_id) | frozenset(plant_lanes.lane_id)


def _plant(o: dict) -> dict:
    rows = context.match(o["measure"], o["scope"])
    periods = context.window_periods(o.get("window"))
    load = context.plant_family_load()
    load = load[load.period.isin(set(periods))]
    whole_plant = "production_family" not in o["scope"]
    families = []
    for r in rows.itertuples(index=False):
        weekly = load[(load.plant_id == r.plant_id) & (load.production_family == r.production_family)].load_units
        cap = float(r.family_capacity_units_per_week)
        entry = {"plant_id": r.plant_id, "production_family": r.production_family,
                 "avg_load_per_week": _r(weekly.mean()) if len(weekly) else 0,
                 "peak_load_per_week": _r(weekly.max()) if len(weekly) else 0}
        if o["measure"] == "supply.plant_capacity":
            after = float(_apply(cap, o["operation"], o["value"]))
            entry.update({"capacity_before": _r(cap), "capacity_after": _r(after),
                          "weeks_load_exceeds_capacity_after": int((weekly > after).sum())})
        else:
            entry["approx_lost_units_per_week"] = _r(weekly.mean() * ADHERENCE_MEAN * (1 - o["value"])) \
                if len(weekly) else 0
        families.append(entry)
    out = {"weeks": len(periods), "families": families,
           "note": "load = forward customer demand x allocation share -- the plant also builds "
                   "buffer stock, so real requirements run higher"}
    if o["measure"] == "supply.plant_capacity" and whole_plant:
        pl = context.plants().set_index("plant_id")
        out["plant_wide"] = []
        for plant in rows.plant_id.unique():
            weekly = load[load.plant_id == plant].groupby("period").load_units.sum()
            cap = float(pl.production_capacity_units_per_week[plant])
            after = float(_apply(cap, o["operation"], o["value"]))
            out["plant_wide"].append({"plant_id": plant, "capacity_before": _r(cap), "capacity_after": _r(after),
                                      "peak_load_per_week": _r(weekly.max()) if len(weekly) else 0,
                                      "weeks_load_exceeds_capacity_after": int((weekly > after).sum())})
    return out


# ------------------------------------------------------------ inventory ----
def _stock(o: dict) -> dict:
    pairs = context.match(o["measure"], o["scope"])
    field = "opening_stock" if o["measure"] == "inventory.on_hand" else "in_transit_qty"
    op_ = context.opening_position().merge(pairs[["dc_id", "sku_id"]], on=["dc_id", "sku_id"])
    cover = context.cover_basis().reindex(pd.MultiIndex.from_frame(op_[["dc_id", "sku_id"]])).fillna(0).to_numpy()
    value = o["value"] * cover if o.get("unit") == "weeks_of_cover" else o["value"]
    base = op_[field].to_numpy(float)
    after = np.maximum(_apply(base, o["operation"], value), 0.0)
    cost = op_.sku_id.map(context.skus().set_index("sku_id").unit_cost_usd).to_numpy()
    weekly = cover.sum()
    return {"pairs": int(len(op_)), "units_before": _r(base.sum()), "units_after": _r(after.sum()),
            "value_before_usd": _r((base * cost).sum()), "value_after_usd": _r((after * cost).sum()),
            "weeks_of_cover_before": _r(base.sum() / weekly, 2) if weekly else None,
            "weeks_of_cover_after": _r(after.sum() / weekly, 2) if weekly else None,
            "cover_basis": f"weekly planning demand over the first {context.FIRST_WEEKS_FOR_COVER} forward weeks "
                           f"(own demand at CDCs/RDCs, throughput at plant DCs)"}


# ------------------------------------------------------ service / policy ----
def _baseline_policy(pairs: pd.DataFrame) -> pd.DataFrame:
    fp = context.forward_policy().merge(pairs[["dc_id", "sku_id"]], on=["dc_id", "sku_id"])
    return fp.merge(context.skus()[["sku_id", "unit_cost_usd", "segment"]], on="sku_id", suffixes=("", "_sku"))


def _service(o: dict) -> dict:
    fp = _baseline_policy(context.match(o["measure"], o["scope"]))
    z_old = fp.z.fillna(fp.segment.map(catalog.SEGMENT_Z)).fillna(catalog.Z_DEFAULT).to_numpy(float)
    csl_old = norm.cdf(z_old)
    csl_new = (np.full_like(csl_old, o["value"]) if o["operation"] == "set"
               else np.clip(csl_old + o["value"], 0.5, 0.999))
    z_new = norm.ppf(csl_new)
    ss = fp.safety_stock.to_numpy(float)
    ss_new = np.where(z_old > 0, ss * z_new / np.maximum(z_old, 1e-9), ss)
    cost = fp.unit_cost_usd.to_numpy(float)
    moves = (pd.DataFrame({"from": np.round(csl_old, 3), "to": np.round(csl_new, 3)})
             .value_counts().rename("pairs").reset_index().to_dict("records"))
    return {"pairs": int(len(fp)), "csl_changes": moves,
            "approx_safety_stock_units_delta": _r((ss_new - ss).sum()),
            "approx_safety_stock_value_delta_usd": _r(((ss_new - ss) * cost).sum()),
            "basis": "static forward policy scaled by z_new / z_old; MEIO re-sizes per week"}


def _policy(o: dict) -> dict:
    fp = _baseline_policy(context.match(o["measure"], o["scope"]))
    field = o["measure"].split(".", 1)[1]
    value = o["value"]
    if o.get("unit") == "weeks_of_cover":
        idx = pd.MultiIndex.from_frame(fp[["dc_id", "sku_id"]])
        value = o["value"] * context.avg_weekly_demand().reindex(idx).fillna(0).to_numpy()
    base = fp[field].to_numpy(float)
    after = np.maximum(_apply(base, o["operation"], value), 0.0)
    cost = fp.unit_cost_usd.to_numpy(float)
    return {"pairs": int(len(fp)), f"{field}_units_before": _r(base.sum()), f"{field}_units_after": _r(after.sum()),
            "value_delta_usd": _r(((after - base) * cost).sum()),
            "pairs_changed": int((np.abs(after - base) > 1e-9).sum()),
            "basis": "static forward policy; MEIO applies the override to every period it covers"}


def _alpha(o: dict) -> dict:
    rows = context.match(o["measure"], o["scope"])
    return {"skus": int(len(rows)), "alpha": {"portfolio": context.meio_parameters().alpha, "override": o["value"]},
            "effect": "higher alpha pools more RDC safety stock with its CDCs (cheaper, riskier internal fill)"}


# ------------------------------------------------------------ economics ----
def _unit_cost(o: dict) -> dict:
    rows = context.match(o["measure"], o["scope"])
    sk = context.skus().set_index("sku_id").loc[rows.sku_id]
    after = _apply(sk.unit_cost_usd, o["operation"], o["value"])
    delta = pd.Series(after - sk.unit_cost_usd.to_numpy(), index=sk.index)
    stock = context.opening_position().groupby("sku_id").opening_stock.sum().reindex(sk.index).fillna(0)
    out = {"skus": int(len(sk)), "avg_unit_cost_before": _r(sk.unit_cost_usd.mean(), 2),
           "avg_unit_cost_after": _r(after.mean(), 2),
           "opening_inventory_value_delta_usd": _r((stock * delta).sum())}
    if (o.get("options") or {}).get("hold_list_price", True):
        out["avg_margin_after"] = _r((sk.margin_usd - delta).mean(), 2)
        out["note"] = "list price held: margin per unit falls by the cost increase"
    return out


def _margin(o: dict) -> dict:
    rows = context.match(o["measure"], o["scope"])
    sk = context.skus().set_index("sku_id").loc[rows.sku_id]
    after = _apply(sk.margin_usd, o["operation"], o["value"])
    return {"skus": int(len(sk)), "avg_margin_before": _r(sk.margin_usd.mean(), 2),
            "avg_margin_after": _r(after.mean(), 2),
            "effect": "re-values every lost sale at a customer DC; does not change sizing"}


def _holding(o: dict) -> dict:
    rows = context.match(o["measure"], o["scope"])
    field = ("inventory_carrying_pct_annual" if o["measure"] == "economics.holding_rate"
             else "storage_cost_per_unit_year_usd")
    after = _apply(rows[field], o["operation"], o["value"])
    stock = (context.opening_position().merge(context.skus()[["sku_id", "subcategory", "unit_cost_usd"]], on="sku_id")
             .merge(rows[["dc_id", "subcategory"]].assign(delta=after - rows[field].to_numpy()),
                    on=["dc_id", "subcategory"]))
    per_unit = stock.delta * (stock.unit_cost_usd / 100.0 if field.startswith("inventory") else 1.0)
    return {"rows": int(len(rows)), "avg_before": _r(rows[field].mean(), 3), "avg_after": _r(after.mean(), 3),
            "approx_delta_holding_usd_per_year_on_opening_stock": _r((stock.opening_stock * per_unit).sum())}


_PREVIEWS = {
    "demand.volume": _demand_volume, "demand.volatility": _demand_volatility,
    "supply.lead_time_mean": _lane, "supply.lead_time_std": _lane, "supply.lane_outage": _lane,
    "supply.plant_capacity": _plant, "supply.schedule_adherence": _plant,
    "inventory.on_hand": _stock, "inventory.in_transit": _stock,
    "service.target_csl": _service,
    "policy.safety_stock": _policy, "policy.reorder_point": _policy, "policy.order_up_to_level": _policy,
    "policy.pooling_alpha": _alpha,
    "economics.unit_cost": _unit_cost, "economics.margin": _margin,
    "economics.holding_rate": _holding, "economics.storage_cost": _holding,
    "economics.logistics_cost": _lane,
}
