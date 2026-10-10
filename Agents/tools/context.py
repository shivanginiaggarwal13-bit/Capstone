"""
Read-only context for the scenario agents: master data, the forward horizon, scope
resolution, entity profiles and the disruption library.

Master data and scope resolution are the override engine's own
(models/overrides/master.py: universes, canonicalisation, matching), read from the
same folders MEIO and the Twin use -- so a scope an agent checks here selects exactly
the rows the models will change. This module adds the agent-facing views on top:
windows from dates, scope summaries, profiles, dimensions.
"""
from __future__ import annotations

import difflib
from functools import lru_cache

import pandas as pd

from models.overrides import master as _engine_master
from models.overrides.master import ECHELONS, MasterData

from . import catalog, paths

FIRST_WEEKS_FOR_COVER = _engine_master.FIRST_WEEKS_FOR_COVER


@lru_cache(maxsize=1)
def master() -> MasterData:
    return MasterData(paths.DATA_DIR, paths.SKU_CLUSTERS, paths.OPENING_STATE)


@lru_cache(maxsize=1)
def meio_parameters():
    """The parameters the shared config's MEIO builds the baseline policy with
    (final_submission/meio_parameters.py: model, alpha, netting_discount, decisions)."""
    import sys
    for p in (paths.REPO_ROOT / "models", paths.REPO_ROOT / "models" / "MEIO" / "MEIO_Weekly" / "final_submission"):
        if str(p) not in sys.path:
            sys.path.append(str(p))
    import json
    import meio_parameters as mp
    return mp.resolve(json.loads(paths.ROOT_CONFIG.read_text(encoding="utf-8")))


def _read(name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(paths.DATA_DIR / name, **kw)


# ----------------------------------------------------------------- masters --
def skus() -> pd.DataFrame:
    return master().skus


def nodes() -> pd.DataFrame:
    return master().nodes


def lanes() -> pd.DataFrame:
    return master().lanes


def plants() -> pd.DataFrame:
    return master().plants


def plant_families() -> pd.DataFrame:
    return master().plant_families


def customers() -> pd.DataFrame:
    return master().customers


def cost_parameters() -> pd.DataFrame:
    return master().cost_parameters


def sourcing_map() -> pd.DataFrame:
    return master().sourcing_map


def plant_sourcing() -> pd.DataFrame:
    return master().plant_sourcing


def opening_position() -> pd.DataFrame:
    return master().opening_position


@lru_cache(maxsize=None)
def library() -> pd.DataFrame:
    return _read("scenarios.csv")


@lru_cache(maxsize=None)
def forward_policy() -> pd.DataFrame:
    return pd.read_csv(paths.FORWARD_POLICY)


# ----------------------------------------------------------------- horizon --
def forward_demand() -> pd.DataFrame:
    """Forward-horizon forecast lines: (period, dc, sku, customer) with promo lift."""
    return master().forward_demand


def forward_periods() -> tuple[str, ...]:
    return master().forward_periods


@lru_cache(maxsize=None)
def period_mondays() -> pd.Series:
    p = forward_demand()[["period", "week_start_date"]].drop_duplicates()
    return pd.Series(pd.to_datetime(p.week_start_date).to_numpy(), index=p.period).sort_values()


def window_periods(window: dict | None) -> list[str]:
    periods = forward_periods()
    if not window:
        return list(periods)
    i, j = periods.index(window["start_period"]), periods.index(window["end_period"])
    return list(periods[i:j + 1])


def _period_of(date) -> str:
    day = pd.Timestamp(date)
    mondays = period_mondays()
    hit = mondays[(mondays <= day) & (day < mondays + pd.Timedelta(days=7))]
    if hit.empty:
        raise ValueError(f"{day.date()} is outside the forward horizon "
                         f"{mondays.iloc[0].date()} .. {(mondays.iloc[-1] + pd.Timedelta(days=6)).date()}")
    return hit.index[0]


def resolve_window(start_date: str | None = None, end_date: str | None = None,
                   month: str | None = None, start_period: str | None = None,
                   end_period: str | None = None, weeks: int | None = None) -> dict:
    """Turn dates, a month (YYYY-MM, weeks whose Monday falls in it) or a start week
    plus a duration into the inclusive ISO-week window an override stores."""
    periods = forward_periods()
    mondays = period_mondays()
    if month:
        start = pd.Timestamp(f"{month}-01")
        inside = mondays[(mondays >= start) & (mondays < start + pd.offsets.MonthBegin(1))]
        if inside.empty:
            raise ValueError(f"no forward week starts in {month}")
        start_period, end_period = inside.index[0], inside.index[-1]
    else:
        if start_date:
            start_period = _period_of(start_date)
        if end_date:
            end_period = _period_of(end_date)
        if not start_period:
            raise ValueError("give start_date, start_period or month")
        if start_period not in periods:
            raise ValueError(f"{start_period} is not a forward period ({periods[0]}..{periods[-1]})")
        if weeks and not end_period:
            i = periods.index(start_period) + int(weeks) - 1
            if i >= len(periods):
                raise ValueError(f"{weeks} weeks from {start_period} runs past {periods[-1]}")
            end_period = periods[i]
        end_period = end_period or start_period
    if end_period not in periods:
        raise ValueError(f"{end_period} is not a forward period ({periods[0]}..{periods[-1]})")
    covered = window_periods({"start_period": start_period, "end_period": end_period})
    if not covered:
        raise ValueError(f"window starts ({start_period}) after it ends ({end_period})")
    return {"window": {"start_period": start_period, "end_period": end_period},
            "weeks": len(covered),
            "first_day": str(mondays[start_period].date()),
            "last_day": str((mondays[end_period] + pd.Timedelta(days=6)).date())}


# ------------------------------------------------------------------- scope --
def universe(name: str) -> pd.DataFrame:
    return master().universe(name)


def key_domains() -> dict[str, tuple[str, ...]]:
    return master().key_domains


def canonicalize(key: str, values) -> tuple[list[str], list[str]]:
    """Map loosely-typed scope values ('southeast', 'cdc03', 'CUSTOMER_DC') to the
    canonical master-data value, with close-match hints for anything unknown."""
    return master().canonicalize(key, values)


def match(measure: str, scope: dict) -> pd.DataFrame:
    """Rows of the measure's universe selected by an (already canonical) scope -- the
    engine's own matching, so this is exactly what MEIO and the Twin will change."""
    return master().match(measure, scope)


def scope_summary(measure: str, rows: pd.DataFrame) -> dict:
    """Counts and a small sample -- what an agent shows a planner to confirm a scope."""
    summary = {"matched_rows": int(len(rows)), "universe": catalog.MEASURES[measure].universe}
    for col, label in [("sku_id", "skus"), ("dc_id", "dcs"), ("b2b_customer_id", "customers"),
                       ("lane_id", "lanes"), ("plant_id", "plants"), ("subcategory", "subcategories")]:
        if col in rows.columns:
            summary[label] = int(rows[col].nunique())
    if "echelon" in rows.columns and len(rows):
        summary["by_echelon"] = rows.echelon.value_counts().to_dict()
    if "dc_id" in rows.columns and rows.dc_id.nunique() <= 12:
        summary["dc_ids"] = sorted(rows.dc_id.unique().tolist())
    if "lane_id" in rows.columns and len(rows) <= 12:
        summary["lanes"] = rows[["lane_id", "origin_node_id", "destination_node_id", "transport_mode",
                                 "lead_time_mean_days"]].to_dict("records")
    if "plant_id" in rows.columns and "lane_id" not in rows.columns:
        summary["plant_families"] = rows[["plant_id", "production_family"]].to_dict("records")
    return summary


def weekly_demand(pairs: pd.DataFrame, periods: list[str] | None = None,
                  by: tuple[str, ...] = ("dc_id", "sku_id")) -> pd.DataFrame:
    """Forecast units per matched key over the given periods (all forward weeks by default)."""
    fd = forward_demand()
    if periods is not None:
        fd = fd[fd.period.isin(set(periods))]
    keys = list(by)
    return fd.merge(pairs[keys].drop_duplicates(), on=keys, how="inner")


def cover_basis() -> pd.Series:
    """Weekly planning demand over the first forward weeks -- the denominator for
    opening-position weeks of cover (the engine's own, master.MasterData.cover_basis)."""
    return master().cover_basis()


def avg_weekly_demand() -> pd.Series:
    """Weekly planning demand averaged over the whole forward horizon."""
    return master().planning_demand(forward_periods())


@lru_cache(maxsize=None)
def plant_family_load() -> pd.DataFrame:
    """Demand-equivalent weekly production load per (plant, family, period): forward
    customer demand of every SKU, split across plants by allocation_share. The plant
    also builds buffer stock, so treat this as a floor on what it must produce."""
    fd = forward_demand().groupby(["period", "sku_id"], as_index=False).forecast_qty.sum()
    ps = plant_sourcing()[["sku_id", "plant_id", "allocation_share", "production_family"]]
    load = fd.merge(ps, on="sku_id", how="inner")
    load["load_units"] = load.forecast_qty * load.allocation_share
    return load.groupby(["plant_id", "production_family", "period"], as_index=False).load_units.sum()


# ------------------------------------------------------------- dimensions --
def dimensions() -> dict:
    n, s, periods = nodes(), skus(), forward_periods()
    regions = {}
    for region, g in n.groupby("region"):
        regions[region] = {e: sorted(g[g.echelon == e].dc_id) for e in ECHELONS if (g.echelon == e).any()}
    clusters = (s.groupby(["cluster", "archetype"], as_index=False)
                .agg(n_skus=("sku_id", "size"), recommended_csl=("recommended_csl", "first")))
    pf = plant_families()
    return {
        "horizon": {"first_period": periods[0], "last_period": periods[-1], "weeks": len(periods),
                    "first_monday": str(period_mondays().iloc[0].date()),
                    "note": "ISO-style weeks; 2026-W01 starts on Monday 2025-12-29."},
        "echelons": {"PDC": "plant DC -- pushed by production, backorders",
                     "RDC": "regional DC -- serves bulk accounts and CDCs, backorders",
                     "CDC": "customer DC -- direct-store delivery, shortfalls are LOST sales"},
        "regions": regions,
        "categories": {c: sorted(g.subcategory.unique()) for c, g in s.groupby("category")},
        "brands": sorted(s.brand.unique()),
        "segments": {seg: {"z": z, "csl": round(float(_phi(z)), 3)} for seg, z in catalog.SEGMENT_Z.items()},
        "price_tiers": sorted(s.price_tier.unique()),
        "production_families": sorted(s.production_family.unique()),
        "clusters": clusters.to_dict("records"),
        "plants": [{"plant_id": p.plant_id, "city": p.city, "region": p.region,
                    "plant_dc_id": p.plant_dc_id,
                    "capacity_units_per_week": int(p.production_capacity_units_per_week),
                    "families": dict(zip(pf[pf.plant_id == p.plant_id].production_family,
                                         pf[pf.plant_id == p.plant_id].family_capacity_units_per_week.astype(int)))}
                   for p in plants().itertuples(index=False)],
        "customers": customers()[["b2b_customer_id", "customer_name", "channel_type", "delivery_mode",
                                  "pricing_strategy"]].to_dict("records"),
        "transport_modes": sorted(lanes().transport_mode.unique()),
        "measures": {name: f"phase {m.phase}: {m.summary}" for name, m in catalog.MEASURES.items()},
    }


def _phi(z: float) -> float:
    from scipy.stats import norm
    return float(norm.cdf(z))


def library_rows(scenario_type: str | None = None) -> list[dict]:
    lib = library()
    if scenario_type:
        lib = lib[lib.scenario_type == scenario_type]
    cols = ["scenario_id", "scenario_type", "affected_node_id", "affected_sku_id", "region",
            "affected_family", "start_date", "duration_weeks", "magnitude_value", "magnitude_unit",
            "ttr_days", "probability", "notes"]
    return lib[cols].astype(object).where(lib[cols].notna(), None).to_dict("records")


# ---------------------------------------------------------------- profiles --
def entity_profile(kind: str, entity_id: str) -> dict:
    kind = kind.lower()
    if kind == "sku":
        return _sku_profile(entity_id)
    if kind == "dc":
        return _dc_profile(entity_id)
    if kind == "plant":
        return _plant_profile(entity_id)
    if kind == "lane":
        return _lane_profile(entity_id)
    if kind == "customer":
        return _customer_profile(entity_id)
    raise ValueError("kind must be one of sku, dc, plant, lane, customer")


def _one(frame: pd.DataFrame, col: str, value: str) -> pd.Series:
    hit = frame[frame[col].astype(str).str.casefold() == str(value).casefold()]
    if hit.empty:
        hint = difflib.get_close_matches(str(value), frame[col].astype(str).tolist(), n=3)
        raise ValueError(f"unknown {col} {value!r}" + (f"; did you mean {hint}?" if hint else ""))
    return hit.iloc[0]


def _sku_profile(sku_id: str) -> dict:
    s = _one(skus(), "sku_id", sku_id)
    fd = forward_demand()
    mine = fd[fd.sku_id == s.sku_id]
    by_dc = mine.groupby("dc_id").forecast_qty.sum().sort_values(ascending=False)
    weekly = mine.groupby("period").forecast_qty.sum().reindex(forward_periods(), fill_value=0)
    op = opening_position()
    z = catalog.SEGMENT_Z.get(s.segment, catalog.Z_DEFAULT)
    return {
        "sku_id": s.sku_id, "sku_code": s.sku_code, "category": s.category, "subcategory": s.subcategory,
        "brand": s.brand, "price_tier": s.price_tier, "production_family": s.production_family,
        "segment": s.segment, "segment_z": z, "segment_csl": round(_phi(z), 3),
        "cluster": s.cluster, "archetype": s.archetype, "cluster_recommended_csl": s.recommended_csl,
        "list_price_usd": s.list_price_usd, "unit_cost_usd": s.unit_cost_usd, "margin_usd": s.margin_usd,
        "units_per_case": int(s.units_per_case),
        "forward_units_total": float(mine.forecast_qty.sum()),
        "forward_units_avg_week": round(float(weekly.mean()), 1),
        "forward_peak_week": weekly.idxmax() if weekly.max() > 0 else None,
        "top_dcs_by_forward_units": by_dc.head(5).round(0).to_dict(),
        "plants": plant_sourcing()[plant_sourcing().sku_id == s.sku_id][
            ["plant_id", "is_primary", "allocation_share"]].to_dict("records"),
        "opening_stock_units": float(op[op.sku_id == s.sku_id].opening_stock.sum()),
    }


def _dc_profile(dc_id: str) -> dict:
    n = _one(nodes(), "dc_id", dc_id)
    ln = lanes()
    inbound = ln[ln.destination_node_id == n.dc_id][["lane_id", "origin_node_id", "transport_mode",
                                                    "lead_time_mean_days", "lead_time_std_days",
                                                    "logistics_cost_per_unit_usd"]]
    smap = sourcing_map()
    used = smap[smap.dc_id == n.dc_id].origin_node_id.value_counts()
    inbound = inbound.assign(skus_routed=inbound.origin_node_id.map(used).fillna(0).astype(int))
    op = opening_position()
    mine = op[op.dc_id == n.dc_id].merge(skus()[["sku_id", "unit_cost_usd"]], on="sku_id")
    fd = forward_demand()
    served = smap[smap.origin_node_id == n.dc_id].dc_id.unique().tolist()
    return {
        "dc_id": n.dc_id, "echelon": n.echelon, "name": n.dc_name, "city": n.city, "state": n.state,
        "region": n.region, "fulfilment": "lost sale" if n.echelon == "CDC" else "backorder",
        "inbound_lanes": inbound.to_dict("records"),
        "serves_nodes": sorted(served),
        "skus_stocked": int(len(mine)),
        "opening_stock_units": float(mine.opening_stock.sum()),
        "opening_stock_value_usd": round(float((mine.opening_stock * mine.unit_cost_usd).sum()), 2),
        "forward_customer_units": float(fd[fd.dc_id == n.dc_id].forecast_qty.sum()),
        "customers": sorted(fd[fd.dc_id == n.dc_id].b2b_customer_id.unique().tolist()),
    }


def _plant_profile(plant_id: str) -> dict:
    p = _one(plants(), "plant_id", plant_id)
    pf = plant_families()
    load = plant_family_load()
    fams = []
    for r in pf[pf.plant_id == p.plant_id].itertuples(index=False):
        weekly = load[(load.plant_id == p.plant_id) & (load.production_family == r.production_family)].load_units
        fams.append({"production_family": r.production_family,
                     "capacity_units_per_week": int(r.family_capacity_units_per_week),
                     "avg_load_units_per_week": round(float(weekly.mean()), 0) if len(weekly) else 0.0,
                     "peak_load_units_per_week": round(float(weekly.max()), 0) if len(weekly) else 0.0})
    total = load[load.plant_id == p.plant_id].groupby("period").load_units.sum()
    return {"plant_id": p.plant_id, "name": p.plant_name, "city": p.city, "region": p.region,
            "plant_dc_id": p.plant_dc_id,
            "capacity_units_per_week": int(p.production_capacity_units_per_week),
            "avg_load_units_per_week": round(float(total.mean()), 0) if len(total) else 0.0,
            "peak_load_units_per_week": round(float(total.max()), 0) if len(total) else 0.0,
            "families": fams,
            "note": "load = forward customer demand x allocation share; the plant also builds buffer stock"}


def _lane_profile(lane_id: str) -> dict:
    lane = _one(lanes(), "lane_id", lane_id)
    smap = sourcing_map()
    routed = smap[(smap.origin_node_id == lane.origin_node_id) & (smap.dc_id == lane.destination_node_id)]
    fd = forward_demand()
    units = fd.merge(routed[["dc_id", "sku_id"]], on=["dc_id", "sku_id"]).forecast_qty.sum()
    cols = ["lane_id", "origin_node_id", "origin_node_type", "destination_node_id", "destination_node_type",
            "transport_mode", "lead_time_mean_days", "lead_time_std_days", "distance_miles",
            "logistics_cost_per_unit_usd", "capacity_units_per_week"]
    out = {c: (lane[c].item() if hasattr(lane[c], "item") else lane[c]) for c in cols}
    out.update({"skus_routed": int(len(routed)), "forward_customer_units_at_destination": float(units),
                "carries_flow": bool(len(routed)),
                "note": "" if len(routed) else "no SKU is routed on this lane (failover/lateral lanes carry no flow)"})
    return out


def _customer_profile(customer_id: str) -> dict:
    c = _one(customers(), "b2b_customer_id", customer_id)
    fd = forward_demand()
    mine = fd[fd.b2b_customer_id == c.b2b_customer_id]
    return {"b2b_customer_id": c.b2b_customer_id, "name": c.customer_name, "channel": c.channel_type,
            "delivery_mode": c.delivery_mode, "pricing_strategy": c.pricing_strategy,
            "total_trade_rate_pct": c.total_trade_rate_pct,
            "forward_units": float(mine.forecast_qty.sum()),
            "share_of_forward_units": round(float(mine.forecast_qty.sum() / fd.forecast_qty.sum()), 4),
            "dcs": mine.groupby("dc_id").forecast_qty.sum().round(0).to_dict()}
