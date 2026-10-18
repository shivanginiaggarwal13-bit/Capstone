"""
Override application for the Digital Twin -- the ACTUAL side of a scenario.

digital_twin.build_inputs calls these at fixed seams. Each returns new data without
mutating its input, and each is skipped entirely when the set holds no override of its
measure, so an empty set leaves every array the Twin builds byte-identical.

  seam in digital_twin.py           function             measures
  build_inputs, after table load    economics()          economics.*
  _build_demand, forward rows       forecast_rows()      demand.volume (before the error draw)
  _build_demand, error ratios       volatility()         demand.volatility
  _load_policy, opening state       opening_state()      inventory.on_hand / in_transit
  build_inputs, lane parameters     lane_weeks()         supply.lead_time_mean / _std (by dispatch week)
                                    lane_blocks()        supply.lane_outage (hold until the lane reopens)
  build_inputs, plant capacity      capacity_weeks()     supply.plant_capacity (by production week)
                                    adherence_weeks()    supply.schedule_adherence

Demand changes act on the forecast BEFORE the forecast-error ratio is drawn, and the
error spread and attainment multipliers act on draws that are made anyway, so no override
changes how many random numbers are consumed: two runs at one seed stay comparable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .compile import OverrideSet, apply_op

DAYS_PER_WEEK = 7
ECONOMICS = ("economics.unit_cost", "economics.margin", "economics.holding_rate", "economics.storage_cost",
             "economics.logistics_cost")


def _in(frame: pd.DataFrame, keys: list[str], rows: pd.DataFrame) -> np.ndarray:
    return pd.MultiIndex.from_frame(frame[keys]).isin(pd.MultiIndex.from_frame(rows[keys].drop_duplicates()))


def _weeks(o, periods) -> list[int]:
    return [w for w, p in enumerate(periods) if o.active(p)]


def _span(weeks: list[int], periods) -> str:
    return f"{periods[weeks[0]]}..{periods[weeks[-1]]}" if weeks else "none"


# ---------------------------------------------------------------- economics --
def economics(ovs: OverrideSet, sku_master: pd.DataFrame, cost_p: pd.DataFrame, lanes: pd.DataFrame):
    ops = ovs.of(*ECONOMICS)
    if not ops:
        return sku_master, cost_p, lanes
    sku_master, cost_p, lanes = sku_master.copy(), cost_p.copy(), lanes.copy()
    for o in ops:
        rows = ovs.rows(o)
        if o.measure in ("economics.unit_cost", "economics.margin"):
            table, mask = sku_master, sku_master.sku_id.isin(rows.sku_id).to_numpy()
            col = "unit_cost_usd" if o.measure == "economics.unit_cost" else "margin_usd"
        elif o.measure == "economics.logistics_cost":
            table, mask, col = lanes, lanes.lane_id.isin(rows.lane_id).to_numpy(), "logistics_cost_per_unit_usd"
        else:
            table, mask = cost_p, _in(cost_p, ["dc_id", "subcategory"], rows)
            col = ("inventory_carrying_pct_annual" if o.measure == "economics.holding_rate"
                   else "storage_cost_per_unit_year_usd")
        before = table.loc[mask, col].to_numpy(dtype=float)
        after = np.maximum(apply_op(before, o.op, o.value), 0.0)
        table[col] = table[col].astype(float)
        table.loc[mask, col] = after
        facts = {"rows": int(mask.sum()), "column": col, "mean_before": float(before.mean()),
                 "mean_after": float(after.mean())}
        if o.measure == "economics.unit_cost" and o.options.get("hold_list_price", True):
            table.loc[mask, "margin_usd"] = table.loc[mask, "margin_usd"].to_numpy(dtype=float) - (after - before)
            facts["margin_reduced_by_cost_increase"] = True
        ovs.receipts.add(o, **facts)
    return sku_master, cost_p, lanes


# ------------------------------------------------------------------- demand --
def forecast_rows(ovs: OverrideSet, rows: pd.DataFrame, record: bool = True) -> pd.DataFrame:
    """demand.volume on forecast lines (period, dc, sku, customer), in list order. The
    Twin realises demand from these rows; MEIO's plan-side factors use the same rule."""
    ops = ovs.of("demand.volume")
    if not ops:
        return rows
    out = rows.copy()
    qty = out.forecast_qty.to_numpy(dtype=float).copy()
    keys = ["dc_id", "sku_id", "b2b_customer_id"]
    line_index = pd.MultiIndex.from_frame(out[keys])
    for o in ops:
        lines = ovs.rows(o)
        mask = line_index.isin(pd.MultiIndex.from_frame(lines[keys]))
        if o.periods is not None:
            mask &= out.period.isin(o.periods).to_numpy()
        before = float(qty[mask].sum())
        qty[mask] = np.maximum(apply_op(qty[mask], o.op, o.value), 0.0)
        if record:
            ovs.receipts.add(o, demand_lines=int(len(lines)), forecast_rows=int(mask.sum()),
                             weeks=int(out.period[mask].nunique()), forecast_units_before=before,
                             forecast_units_after=float(qty[mask].sum()))
    out["forecast_qty"] = qty
    return out


def volatility(ovs: OverrideSet, node_idx: dict, sku_idx: dict, periods) -> np.ndarray | None:
    """Multiplier k on the forecast-error spread per (node, sku, week): r -> 1 + k (r - 1)."""
    ops = ovs.of("demand.volatility")
    if not ops:
        return None
    k = np.ones((len(node_idx), len(sku_idx), len(periods)))
    for o in ops:
        rows = ovs.rows(o)
        rows = rows[rows.dc_id.isin(node_idx) & rows.sku_id.isin(sku_idx)]
        ni, si = rows.dc_id.map(node_idx).to_numpy(), rows.sku_id.map(sku_idx).to_numpy()
        weeks = _weeks(o, periods)
        for w in weeks:
            k[ni, si, w] *= o.value
        ovs.receipts.add(o, pairs=int(len(rows)), weeks=len(weeks), window=_span(weeks, periods),
                         spread_multiplier=o.value)
    return k


# ---------------------------------------------------------------- inventory --
def opening_state(ovs: OverrideSet, state: pd.DataFrame) -> pd.DataFrame:
    ops = ovs.of("inventory.on_hand", "inventory.in_transit")
    if not ops:
        return state
    out = state.copy()
    for o in ops:
        col = "opening_stock" if o.measure == "inventory.on_hand" else "in_transit_qty"
        out[col] = out[col].astype(float)
        mask = _in(out, ["dc_id", "sku_id"], ovs.rows(o))
        base = out.loc[mask, col].to_numpy(dtype=float)
        value = o.value
        if o.unit == "weeks_of_cover":
            idx = pd.MultiIndex.from_frame(out.loc[mask, ["dc_id", "sku_id"]])
            value = o.value * ovs.master.cover_basis().reindex(idx).fillna(0.0).to_numpy()
        after = np.maximum(apply_op(base, o.op, value), 0.0)
        out.loc[mask, col] = after
        ovs.receipts.add(o, pairs=int(mask.sum()), column=col, units_before=float(base.sum()),
                         units_after=float(after.sum()))
    return out


# ------------------------------------------------------------------- supply --
def _lane_keys(rows: pd.DataFrame, node_idx: dict) -> list[tuple[int, int]]:
    """Twin lane keys: (origin node index, or -1 for a plant; destination node index)."""
    return [(node_idx.get(r.origin_node_id, -1), node_idx[r.destination_node_id])
            for r in rows.itertuples(index=False) if r.destination_node_id in node_idx]


def lane_weeks(ovs: OverrideSet, node_idx: dict, lane_lt: dict, periods, promise_z: float):
    """(week, lane) -> (mean, std) lead-time days, and the committed lead time quoted for
    shipments dispatched that week. Lanes and weeks without an override are absent."""
    ops = ovs.of("supply.lead_time_mean", "supply.lead_time_std")
    lane_week: dict = {}
    for o in ops:
        keys = [k for k in _lane_keys(ovs.rows(o), node_idx) if k in lane_lt]
        weeks = _weeks(o, periods)
        before = after = 0.0
        for w in weeks:
            for key in keys:
                m, s = lane_week.get((w, key), lane_lt[key])
                if o.measure == "supply.lead_time_mean":
                    new = (max(0.5, float(apply_op(m, o.op, o.value))), s)
                    before, after = before + m, after + new[0]
                else:
                    new = (m, max(0.0, float(apply_op(s, o.op, o.value))))
                    before, after = before + s, after + new[1]
                lane_week[(w, key)] = new
        n = max(len(keys) * len(weeks), 1)
        ovs.receipts.add(o, lanes=len(keys), weeks=len(weeks), window=_span(weeks, periods),
                         avg_days_before=before / n, avg_days_after=after / n)
    promise_week = {k: m + promise_z * s for k, (m, s) in lane_week.items()}
    return lane_week, promise_week


def lane_blocks(ovs: OverrideSet, node_idx: dict, periods) -> dict:
    """lane -> [(first day, last day)] the lane is closed; dispatches inside wait for it."""
    blocks: dict = {}
    for o in ovs.of("supply.lane_outage"):
        weeks = _weeks(o, periods)
        keys = _lane_keys(ovs.rows(o), node_idx)
        if weeks:
            span = (weeks[0] * DAYS_PER_WEEK, (weeks[-1] + 1) * DAYS_PER_WEEK - 1)
            for key in keys:
                blocks.setdefault(key, []).append(span)
        ovs.receipts.add(o, lanes=len(keys), weeks=len(weeks), window=_span(weeks, periods),
                         rule="dispatches during the outage leave when the lane reopens")
    return blocks


def capacity_weeks(ovs: OverrideSet, plant_i: dict, cap_plant: np.ndarray, cap_family: dict, periods):
    """(week, plant) and (week, plant, family) -> units/week. A plant-only scope changes the
    plant-wide limit and every family line at the plant; a family scope only those lines."""
    plant_week: dict = {}
    family_week: dict = {}
    for o in ovs.of("supply.plant_capacity"):
        rows = ovs.rows(o)
        plant_wide = "production_family" not in o.scope
        weeks = _weeks(o, periods)
        before = after = 0.0
        for w in weeks:
            if plant_wide:
                for plant in rows.plant_id.unique():
                    p = plant_i[plant]
                    base = plant_week.get((w, p), float(cap_plant[p]))
                    plant_week[(w, p)] = max(0.0, float(apply_op(base, o.op, o.value)))
            for r in rows.itertuples(index=False):
                key = (plant_i[r.plant_id], r.production_family)
                base = family_week.get((w, *key), cap_family.get(key))
                if base is None:
                    continue
                family_week[(w, *key)] = max(0.0, float(apply_op(base, o.op, o.value)))
                before, after = before + base, after + family_week[(w, *key)]
        n = max(len(weeks), 1)
        ovs.receipts.add(o, plants=int(rows.plant_id.nunique()), family_lines=int(len(rows)), weeks=len(weeks),
                         window=_span(weeks, periods), plant_wide=plant_wide,
                         line_capacity_per_week_before=before / n, line_capacity_per_week_after=after / n)
    return plant_week, family_week


def adherence_weeks(ovs: OverrideSet, plant_i: dict, periods) -> dict:
    """(week, plant) -> {family or None (whole plant): attainment multiplier}."""
    out: dict = {}
    for o in ovs.of("supply.schedule_adherence"):
        rows = ovs.rows(o)
        plant_wide = "production_family" not in o.scope
        weeks = _weeks(o, periods)
        for w in weeks:
            for plant in rows.plant_id.unique():
                d = out.setdefault((w, plant_i[plant]), {})
                fams = [None] if plant_wide else rows[rows.plant_id == plant].production_family.tolist()
                for fam in fams:
                    d[fam] = d.get(fam, 1.0) * o.value
        ovs.receipts.add(o, plants=int(rows.plant_id.nunique()), weeks=len(weeks), window=_span(weeks, periods),
                         multiplier=o.value, plant_wide=plant_wide)
    return out
