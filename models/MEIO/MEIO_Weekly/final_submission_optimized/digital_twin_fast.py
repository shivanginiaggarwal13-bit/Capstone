"""
Supply Brain -- Digital Twin, optimized engine (drop-in for models/twin/digital_twin.py)
========================================================================================

The same discrete-event simulation -- one step per day, the same six-step event order,
the same three independent RNG streams drawn in the same order -- so a run at a given
seed and policy produces the same inventory, receipts, production and KPIs as the
original. What changed is everything around the physics:

* **Inputs with polars, vectorised.** The policy (1.2M rows for a walk-forward policy),
  opening state and sourcing tables were loaded row by row with itertuples; here they
  are joined to index arrays and assigned in one step (last row wins, as before).
* **Cached per process.** Static tables, the replayed history demand tensor (identical
  for every history run) and the deterministic half of forward demand (forecast,
  error pools, CDFs) are built once per worker process, not once per run.
* **Hot loops vectorised.** `_at_parent` is one bincount (same summation order),
  `_ship` updates the pipeline with one fancy-indexed add and records receipts as
  arrays, the weekly close gathers all active pairs at once, and the output frames are
  assembled from those arrays at the end instead of from 1.2M Python tuples.
* **Outputs with polars, as Parquet.** Detail tables (inventory, receipts, production,
  policy) are Parquet -- a 156-week history inventory is ~20 MB instead of ~200 MB of
  CSV; the summaries (cost_summary, service_summary, service_by_node) stay CSV.
* **In-process.** `simulate()` resolves arguments exactly as `main()` does and returns
  the KPIs plus requested window/SKU-group summaries, so the research scripts run the
  twin inside a worker instead of a fresh interpreter that re-reads every input.

run_kpis.json and cost_summary.csv reproduce pandas' reduction order exactly.
service_summary.csv / service_by_node.csv are diagnostic tables summed with polars;
a few values there can differ from the original in the last floating-point digit.

Documentation, assumptions and scenario-folder conventions are imported from the
original module, so they cannot drift apart.

    python digital_twin_fast.py --mode forward --run-name baseline_2026 \\
        --policy "output datasets/history/forward_policy.parquet" \\
        --state  "output datasets/history/opening_position.parquet"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime as rt                              # noqa: E402  (paths: models/, models/twin/)
import scenario                                   # noqa: E402
import scenario_engine                            # noqa: E402
import overrides as override_engine               # noqa: E402
import digital_twin as _orig                      # noqa: E402  (docs, assumptions, folder naming)

DAYS_PER_WEEK = _orig.DAYS_PER_WEEK
REVIEW_DAY = _orig.REVIEW_DAY
PIPELINE_PAD = _orig.PIPELINE_PAD
HISTORY_WEEKS = _orig.HISTORY_WEEKS
SCHEMA_VERSION = _orig.SCHEMA_VERSION
FG_COVER_WEEKS = _orig.FG_COVER_WEEKS
DEMAND_EMA_ALPHA = _orig.DEMAND_EMA_ALPHA
ADHERENCE_RANGE = _orig.ADHERENCE_RANGE
PROMISE_Z = _orig.PROMISE_Z
MARGIN_COLUMN = _orig.MARGIN_COLUMN
NODE_TYPE = _orig.NODE_TYPE
ASSUMPTIONS = _orig.ASSUMPTIONS
POLICY_COLUMNS = _orig.POLICY_COLUMNS
period_label, week_monday, echelon_of = _orig.period_label, _orig.week_monday, _orig.echelon_of
write_output_documentation = _orig.write_output_documentation
load_scenario_config = _orig.load_scenario_config
_scenario_execution = _orig._scenario_execution
_jsonable = _orig._jsonable
_cumulative_lead_times = _orig._cumulative_lead_times

INV_COLS = ["period", "week_start_date", "sku_id", "dc_id", "opening_stock", "receipts", "demand_qty",
            "demand_customer_qty", "demand_replen_qty", "shipments", "unmet_qty", "unmet_customer_qty",
            "unmet_replen_qty", "lost_margin_usd", "fulfilment_type", "closing_stock", "safety_stock",
            "reorder_point", "order_up_to_level", "avg_on_hand", "holding_cost_usd"]
INV_NUM = [c for c in INV_COLS if c not in ("period", "week_start_date", "sku_id", "dc_id", "fulfilment_type")]
LT_COLS = ["lead_time_mean_days", "lead_time_std_days", "cum_lead_time_mean_days", "cum_lead_time_std_days"]


def _pd(df: pl.DataFrame):
    return df.to_pandas()


def _pl(df) -> pl.DataFrame:
    return df if isinstance(df, pl.DataFrame) else pl.from_pandas(df)


def _index(values, idx: dict) -> np.ndarray:
    """Position of each value in `idx`, -1 where absent (pandas .map(dict) -> NaN)."""
    ser = values if isinstance(values, pl.Series) else pl.Series(list(values), dtype=pl.String)
    if not len(ser):
        return np.zeros(0, dtype=np.int64)
    return ser.cast(pl.String).replace_strict({str(k): v for k, v in idx.items()}, default=-1,
                                              return_dtype=pl.Int64).to_numpy()


def _last_wins(*keys) -> np.ndarray:
    """Row positions keeping the last occurrence of each key tuple, in row order."""
    flat = np.zeros(len(keys[0][0]), dtype=np.int64)
    for k, size in keys:
        flat = flat * size + k
    rev = flat[::-1]
    _, first = np.unique(rev, return_index=True)
    return np.sort(len(flat) - 1 - first)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class TwinInputs:
    nodes: List[str]
    skus: List[str]
    plants: List[str]
    periods: List[str]
    week_start_dates: List[str]
    week_index: List[int]
    node_idx: Dict[str, int]
    sku_idx: Dict[str, int]
    echelon: np.ndarray
    node_type: List[str]
    supplier_of: np.ndarray
    pdc_of_plant: np.ndarray
    plant_share: np.ndarray
    cap_plant: np.ndarray
    cap_family: dict
    sku_family: list
    reorder_point: np.ndarray
    order_up_to: np.ndarray
    safety_stock: np.ndarray
    active: np.ndarray
    opening_stock: np.ndarray
    opening_in_transit: np.ndarray
    opening_in_transit_eta_day: np.ndarray
    daily_demand: np.ndarray
    lost_sale_node: np.ndarray
    lane_lt: dict
    lt_promise: dict
    cum_lt: dict
    holding_per_unit_week: np.ndarray
    margin_per_unit: np.ndarray
    unit_cost: np.ndarray
    logistics_cost: np.ndarray
    sku_category: list
    horizon_days: int
    lane_week: Dict = field(default_factory=dict)
    promise_week: Dict = field(default_factory=dict)
    lane_blocks: Dict = field(default_factory=dict)
    cap_plant_week: Dict = field(default_factory=dict)
    cap_family_week: Dict = field(default_factory=dict)
    adherence_week: Dict = field(default_factory=dict)


_DEMAND_CACHE: dict = {}


def _read(data_dir: Path, name: str) -> pl.DataFrame:
    return rt.read_table(data_dir / name)


def build_inputs(data_dir: Path, mode: str = "forward", seed: int = 2026, horizon_weeks: Optional[int] = None,
                 policy_csv=None, state_csv: Optional[Path] = None, scenario_changes: Optional[dict] = None,
                 overrides=None, policy_frame: Optional[pl.DataFrame] = None):
    """digital_twin.build_inputs, same arrays. `policy_frame` (optional) is an
    already-loaded policy table; `policy_csv` is still recorded as its source."""
    data_dir = Path(data_dir)
    ss = np.random.SeedSequence(seed)
    rng_demand, rng_lt, rng_prod = [np.random.default_rng(s) for s in ss.spawn(3)]
    notes: Dict[str, object] = {"mode": mode, "seed": seed}

    sku_master = _read(data_dir, "sku_master.parquet")
    cost_p = _read(data_dir, "cost_parameters.parquet")
    lanes = _read(data_dir, "network_lanes.parquet")
    sourcing_map = _read(data_dir, "sku_sourcing_map.parquet")
    sourcing = _read(data_dir, "sku_plant_sourcing.parquet")
    plant_dc = _read(data_dir, "plant_dc_master.parquet")
    plant_master = _read(data_dir, "plant_master.parquet")
    plant_cap = _read(data_dir, "plant_capability.parquet")
    if overrides:
        sm_pd, cp_pd, ln_pd = override_engine.twin.economics(overrides, _pd(sku_master), _pd(cost_p), _pd(lanes))
        sku_master, cost_p, lanes = _pl(sm_pd), _pl(cp_pd), _pl(ln_pd)

    # ------------------------------------------------------------- horizon --
    if mode == "history":
        n_weeks = horizon_weeks or HISTORY_WEEKS
        week_index = list(range(1, n_weeks + 1))
    else:
        n_weeks = horizon_weeks or 52
        week_index = list(range(HISTORY_WEEKS + 1, HISTORY_WEEKS + n_weeks + 1))
    periods = [period_label(i) for i in week_index]
    wk_dates = [week_monday(i) for i in week_index]
    T = n_weeks * DAYS_PER_WEEK

    # --------------------------------------------------------------- index --
    dest, orig = lanes["destination_node_id"].to_list(), lanes["origin_node_id"].to_list()
    dc_ids = sorted(set(dest) | set(orig) - set(plant_master["plant_id"].to_list()))
    dc_ids = [d for d in dc_ids if d[:3] in NODE_TYPE]
    nodes = sorted(dc_ids, key=lambda n: (echelon_of(n), n))
    skus = sorted(set(sku_master["sku_id"].to_list()))
    plants = sorted(set(plant_master["plant_id"].to_list()))
    node_idx = {n: i for i, n in enumerate(nodes)}
    sku_idx = {s: i for i, s in enumerate(skus)}
    plant_i = {p: i for i, p in enumerate(plants)}
    n_nodes, n_skus, n_plants = len(nodes), len(skus), len(plants)
    echelon = np.array([echelon_of(n) for n in nodes], dtype=np.int8)
    node_type = [NODE_TYPE[n[:3]] for n in nodes]
    lost_sale_node = echelon == 2

    # ----------------------------------------------------------- structure --
    supplier_of = np.full((n_nodes, n_skus), -1, dtype=np.int16)
    di = _index(sourcing_map["dc_id"], node_idx)
    si = _index(sourcing_map["sku_id"], sku_idx)
    oi = _index(sourcing_map["origin_node_id"], node_idx)
    ok = (di >= 0) & (si >= 0) & (oi >= 0)
    resolved = int(ok.sum())
    rows = np.flatnonzero(ok)
    rows = rows[_last_wins((di[rows], n_nodes), (si[rows], n_skus))]
    supplier_of[di[rows], si[rows]] = oi[rows]

    plant_to_pdc = dict(zip(plant_dc["plant_id"].to_list(), plant_dc["plant_dc_id"].to_list()))
    order = rt.pandas_argsort(lanes["lead_time_mean_days"].to_numpy())
    min_lane = {}
    for d, o in zip(np.array(dest, dtype=object)[order], np.array(orig, dtype=object)[order]):
        min_lane.setdefault(d, o)
    n_fallback = 0
    for n in nodes:
        i, e = node_idx[n], echelon_of(n)
        if e == 0:
            continue
        gap = supplier_of[i] < 0
        if gap.any():
            n_fallback += int(gap.sum())
            supplier_of[i, gap] = node_idx[min_lane[n]]

    pdc_of_plant = np.array([node_idx[plant_to_pdc[p]] for p in plants], dtype=np.int16)
    plant_share = np.zeros((n_plants, n_skus))
    pi = _index(sourcing["plant_id"], plant_i)
    si = _index(sourcing["sku_id"], sku_idx)
    ok = np.flatnonzero((pi >= 0) & (si >= 0))
    ok = ok[_last_wins((pi[ok], n_plants), (si[ok], n_skus))]
    plant_share[pi[ok], si[ok]] = sourcing["allocation_share"].cast(pl.Float64).to_numpy()[ok]
    fam_of = dict(zip(sku_master["sku_id"].to_list(), sku_master["production_family"].to_list()))
    sku_family = [fam_of.get(s, np.nan) if fam_of.get(s) is not None else np.nan for s in skus]
    cap_of = dict(zip(plant_master["plant_id"].to_list(),
                      plant_master["production_capacity_units_per_week"].cast(pl.Float64).to_list()))
    cap_plant = np.array([cap_of.get(p, np.nan) for p in plants], dtype=float)
    cap_family = {(plant_i[p], f): float(c) for p, f, c in plant_cap.select(
        ["plant_id", "production_family", "family_capacity_units_per_week"]).iter_rows() if p in plant_i}
    notes["sourcing"] = {"rule": "sku_sourcing_map.parquet (input table)", "pairs_resolved": resolved,
                         "pairs_on_fallback": n_fallback}

    # ---------------------------------------------------------- lead times --
    lane_lt, lt_promise = {}, {}
    for o, d, m, s in lanes.select(["origin_node_id", "destination_node_id", "lead_time_mean_days",
                                    "lead_time_std_days"]).iter_rows():
        dn = node_idx.get(d)
        if dn is None:
            continue
        key = (node_idx.get(o, -1), dn)
        lane_lt[key] = (m, s)
        lt_promise[key] = m + PROMISE_Z * s
    cum_lt = _cumulative_lead_times(nodes, node_idx, supplier_of, lane_lt)

    lane_week, promise_week, lane_blocks = {}, {}, {}
    cap_plant_week, cap_family_week, adherence_week = {}, {}, {}
    if overrides:
        lane_week, promise_week = override_engine.twin.lane_weeks(overrides, node_idx, lane_lt, periods, PROMISE_Z)
        lane_blocks = override_engine.twin.lane_blocks(overrides, node_idx, periods)
        cap_plant_week, cap_family_week = override_engine.twin.capacity_weeks(
            overrides, plant_i, cap_plant, cap_family, periods)
        adherence_week = override_engine.twin.adherence_weeks(overrides, plant_i, periods)

    # -------------------------------------------------------------- policy --
    (rop, oul, ssk), (opening_stock, op_it, op_eta), active, notes_pol = _load_policy(
        nodes, skus, node_idx, sku_idx, wk_dates[0], policy_csv, state_csv, periods, overrides, policy_frame)
    notes.update(notes_pol)

    # -------------------------------------------------------------- demand --
    daily_demand, dn = _build_demand(data_dir, mode, periods, week_index, node_idx, sku_idx, n_nodes, n_skus, T,
                                     rng_demand, overrides=overrides)
    notes.update(dn)
    if scenario_changes:
        affected = scenario_engine.resolve_affected_nodes(data_dir, scenario_changes)
        daily_demand = scenario_engine.apply_demand_surge_to_tensor(daily_demand, node_idx, affected,
                                                                    scenario_changes)
        notes["scenario_applied"] = {"affected_nodes": affected, **scenario_changes}

    # ----------------------------------------------------------- economics --
    sm_row = {s: i for i, s in enumerate(sku_master["sku_id"].to_list())}
    pick = np.array([sm_row.get(s, -1) for s in skus])

    def sm_col(c, dtype=float):
        v = np.array(sku_master[c].to_list(), dtype=object)[np.where(pick < 0, 0, pick)]
        v = np.where(pick < 0, None, v)
        return v.astype(dtype) if dtype is float else v
    subcat = sm_col("subcategory", object)
    unit_cost = np.array([np.nan if v is None else float(v) for v in sm_col("unit_cost_usd", object)])
    margin = np.array([np.nan if v is None else float(v) for v in sm_col(MARGIN_COLUMN, object)])
    cp = {(d, s): (st, ca) for d, s, st, ca in cost_p.select(
        ["dc_id", "subcategory", "storage_cost_per_unit_year_usd", "inventory_carrying_pct_annual"]).iter_rows()}
    holding = np.zeros((n_nodes, n_skus))
    cp_nodes = set(d for d, _ in cp)
    for i, n in enumerate(nodes):
        if n not in cp_nodes:
            raise KeyError(n)                       # cost_p.loc[n] in the original
        st = np.array([cp.get((n, s), (np.nan, np.nan))[0] for s in subcat], dtype=float)
        ca = np.array([cp.get((n, s), (np.nan, np.nan))[1] for s in subcat], dtype=float)
        holding[i] = (st + unit_cost * ca / 100.0) / 52.0
    logistics = np.zeros((n_nodes, n_nodes))
    for o, d, c in lanes.select(["origin_node_id", "destination_node_id", "logistics_cost_per_unit_usd"]).iter_rows():
        oo, dd = node_idx.get(o), node_idx.get(d)
        if oo is not None and dd is not None:
            logistics[oo, dd] = c
    category = sm_col("category", object).tolist()

    inp = TwinInputs(
        nodes=nodes, skus=skus, plants=plants, periods=periods, week_start_dates=wk_dates, week_index=week_index,
        node_idx=node_idx, sku_idx=sku_idx, echelon=echelon, node_type=node_type, supplier_of=supplier_of,
        pdc_of_plant=pdc_of_plant, plant_share=plant_share, cap_plant=cap_plant, cap_family=cap_family,
        sku_family=sku_family, reorder_point=rop, order_up_to=oul, safety_stock=ssk, active=active,
        opening_stock=opening_stock, opening_in_transit=op_it, opening_in_transit_eta_day=op_eta,
        daily_demand=daily_demand, lost_sale_node=lost_sale_node, lane_lt=lane_lt, lt_promise=lt_promise,
        cum_lt=cum_lt, holding_per_unit_week=holding, margin_per_unit=margin, unit_cost=unit_cost,
        logistics_cost=logistics, sku_category=category, horizon_days=T, lane_week=lane_week,
        promise_week=promise_week, lane_blocks=lane_blocks, cap_plant_week=cap_plant_week,
        cap_family_week=cap_family_week, adherence_week=adherence_week)
    if overrides:
        notes["overrides_applied"] = overrides.receipts.as_dict()["overrides"]
    notes.update({
        "margin_basis": MARGIN_COLUMN, "n_nodes": n_nodes, "n_skus": n_skus, "n_plants": n_plants,
        "n_active_pairs": int(active.sum()), "horizon_weeks": n_weeks, "horizon_days": T,
        "first_period": periods[0], "last_period": periods[-1],
    })
    return inp, notes, rng_lt, rng_prod


def _load_policy(nodes, skus, node_idx, sku_idx, w0_date, policy_csv, state_csv, periods=None, overrides=None,
                 policy_frame=None):
    """digital_twin._load_policy, vectorised: rows map to (week, node, sku) indices and
    the last row for a cell wins, as the row-by-row original did."""
    n_nodes, n_skus = len(nodes), len(skus)
    notes = {}
    if policy_csv is None and policy_frame is None:
        raise ValueError("a policy file is required: pass --policy <csv> with columns " + ", ".join(POLICY_COLUMNS))
    pol = policy_frame if policy_frame is not None else rt.read_policy(policy_csv)
    missing = [c for c in POLICY_COLUMNS if c not in pol.columns]
    if missing:
        raise ValueError(f"policy file {policy_csv} is missing required columns {missing}; "
                         f"expected at least {POLICY_COLUMNS}")
    notes["policy_source"] = str(Path(policy_csv)) if policy_csv is not None else "in-memory policy"
    notes["policy_rows"] = pol.height

    time_varying = "period" in pol.columns and periods is not None
    notes["policy_time_varying"] = time_varying
    active = np.zeros((n_nodes, n_skus), dtype=bool)
    ni = _index(pol["dc_id"], node_idx)
    si = _index(pol["sku_id"], sku_idx)
    vals = {c: pol[c].cast(pl.Float64).to_numpy() for c in ("safety_stock", "reorder_point", "order_up_to_level")}
    ok = (ni >= 0) & (si >= 0)

    if time_varying:
        n_weeks = len(periods)
        period_pos = {p: w for w, p in enumerate(periods)}
        wi = _index(pol["period"], period_pos)
        unmatched = int((ok & (wi < 0)).sum())
        use = np.flatnonzero(ok & (wi >= 0))
        rop, oul, ssk = (np.zeros((n_weeks, n_nodes, n_skus)) for _ in range(3))
        seen = np.zeros((n_weeks, n_nodes, n_skus), dtype=bool)
        active[ni[use], si[use]] = True
        use = use[_last_wins((wi[use], n_weeks), (ni[use], n_nodes), (si[use], n_skus))]
        w, i, j = wi[use], ni[use], si[use]
        seen[w, i, j] = True
        ssk[w, i, j] = vals["safety_stock"][use]
        rop[w, i, j] = vals["reorder_point"][use]
        oul[w, i, j] = vals["order_up_to_level"][use]
        if unmatched:
            notes["policy_rows_unmatched_period"] = unmatched
        for w in range(1, n_weeks):
            carry = active & ~seen[w]
            ssk[w] = np.where(carry, ssk[w - 1], ssk[w])
            rop[w] = np.where(carry, rop[w - 1], rop[w])
            oul[w] = np.where(carry, oul[w - 1], oul[w])
            seen[w] |= seen[w - 1]
    else:
        rop, oul, ssk = (np.zeros((n_nodes, n_skus)) for _ in range(3))
        use = np.flatnonzero(ok)
        active[ni[use], si[use]] = True
        use = use[_last_wins((ni[use], n_nodes), (si[use], n_skus))]
        i, j = ni[use], si[use]
        ssk[i, j] = vals["safety_stock"][use]
        rop[i, j] = vals["reorder_point"][use]
        oul[i, j] = vals["order_up_to_level"][use]

    opening_stock, op_it = (np.zeros((n_nodes, n_skus)) for _ in range(2))
    op_eta = np.zeros((n_nodes, n_skus), dtype=int)
    if state_csv is not None:
        state = rt.read_table(state_csv)
        if overrides:
            state = _pl(override_engine.twin.opening_state(overrides, _pd(state)))
        ni = _index(state["dc_id"], node_idx)
        si = _index(state["sku_id"], sku_idx)
        ok = np.flatnonzero((ni >= 0) & (si >= 0))
        n_rows = state.height
        # `value or 0.0` in the original: 0 -> 0.0, NaN stays NaN (it is truthy)
        stock = (state["opening_stock"].cast(pl.Float64).fill_null(np.nan).to_numpy()
                 if "opening_stock" in state.columns else np.zeros(n_rows))
        last = ok[_last_wins((ni[ok], n_nodes), (si[ok], n_skus))]
        opening_stock[ni[last], si[last]] = stock[last]
        if "in_transit_qty" in state.columns:
            it = state["in_transit_qty"].cast(pl.Float64).fill_null(np.nan).to_numpy()
            pos = ok[it[ok] > 0]
            pos = pos[_last_wins((ni[pos], n_nodes), (si[pos], n_skus))]
            op_it[ni[pos], si[pos]] = it[pos]
            if "in_transit_eta_week_start" in state.columns:
                w0 = np.datetime64(str(w0_date)[:10], "D")
                etas = state["in_transit_eta_week_start"].cast(pl.String).to_list()
                days = np.array([0 if (e is None or e == "" or e.lower() == "nan") else
                                 max(0, int((np.datetime64(e[:10], "D") - w0).astype(int))) for e in
                                 (etas[k] for k in pos)], dtype=int)
                op_eta[ni[pos], si[pos]] = days
        notes["state_source"] = str(state_csv)
    else:
        notes["state_source"] = "cold start (zero stock)"
    return (rop, oul, ssk), (opening_stock, op_it, op_eta), active, notes


def _policy_at(inp, week_idx: int):
    if inp.safety_stock.ndim == 3:
        w = min(week_idx, inp.safety_stock.shape[0] - 1)
        return inp.safety_stock[w], inp.reorder_point[w], inp.order_up_to[w]
    return inp.safety_stock, inp.reorder_point, inp.order_up_to


def _build_demand(data_dir, mode, periods, week_index, node_idx, sku_idx, n_nodes, n_skus, T, rng, overrides=None):
    """digital_twin._build_demand: same tensor, same draws in the same order."""
    notes = {}
    if mode == "history":
        key = ("history", str(Path(data_dir).resolve()), tuple(periods), T)
        if key not in _DEMAND_CACHE:
            o = rt.read_table(Path(data_dir) / "orders.parquet",
                            columns=["delivery_date", "period", "sku_id", "dc_id", "order_qty"])
            o = o.filter(pl.col("period").is_in(list(set(periods))))
            day0 = np.datetime64(week_monday(week_index[0]), "D")
            dday = (o["delivery_date"].str.slice(0, 10).str.to_date("%Y-%m-%d").to_numpy().astype("datetime64[D]")
                    - day0).astype(np.int64)
            ni = _index(o["dc_id"], node_idx)
            si = _index(o["sku_id"], sku_idx)
            dday = np.clip(dday, 0, T - 1)
            keep = (ni >= 0) & (si >= 0)
            flat = (dday[keep] * n_nodes + ni[keep]) * n_skus + si[keep]
            daily = np.bincount(flat, weights=o["order_qty"].to_numpy()[keep].astype(np.int32).astype(np.float64),
                                minlength=T * n_nodes * n_skus).astype(np.int32).reshape(T, n_nodes, n_skus)
            daily.setflags(write=False)
            _DEMAND_CACHE[key] = (daily, float(daily.sum()))
        daily, units = _DEMAND_CACHE[key]
        notes["demand_basis"] = "orders.parquet replay at delivery_date"
        notes["realised_demand_units"] = units
        return daily, notes

    n_weeks = len(periods)
    base = None if overrides else _DEMAND_CACHE.get(("forward", str(Path(data_dir).resolve()), tuple(periods)))
    if base is None:
        base = _forward_base(data_dir, periods, node_idx, sku_idx, n_nodes, n_skus, overrides)
        if not overrides:
            _DEMAND_CACHE[("forward", str(Path(data_dir).resolve()), tuple(periods))] = base
    fcst, pool, cdf, has, max_obs = base
    daily = np.zeros((T, n_nodes, n_skus), dtype=np.int32)
    draw = rng.random((n_nodes, n_skus, n_weeks))
    pick = np.minimum((draw[:, :, :, None] > cdf[:, :, None, :]).sum(axis=3), max_obs - 1)
    ratios = np.where(has[:, :, None], np.take_along_axis(pool, pick, axis=2), 1.0)
    if overrides:
        spread = override_engine.twin.volatility(overrides, node_idx, sku_idx, periods)
        if spread is not None:
            ratios = np.maximum(0.0, 1.0 + spread * (ratios - 1.0))
    weekly = np.maximum(0.0, np.round(fcst * ratios))
    for w in range(n_weeks):
        remaining = weekly[:, :, w].astype(np.int64)
        for d in range(DAYS_PER_WEEK):
            take = (remaining if d == DAYS_PER_WEEK - 1 else rng.binomial(remaining, 1.0 / (DAYS_PER_WEEK - d)))
            daily[w * DAYS_PER_WEEK + d] = take
            remaining -= take
    notes.update({
        "demand_basis": "forecast_qty x volume-weighted resampled forecast-error ratio",
        "forecast_units": float(fcst.sum()),
        "realised_demand_units": float(weekly.sum()),
        "realised_over_forecast": round(float(weekly.sum() / max(fcst.sum(), 1)), 4),
    })
    return daily, notes


def _forward_base(data_dir, periods, node_idx, sku_idx, n_nodes, n_skus, overrides):
    """The deterministic half of forward demand: forecast tensor, error-ratio pools and
    their volume-weighted CDFs."""
    dfc = rt.read_table(Path(data_dir) / "demand_forecast.parquet")
    n_weeks = len(periods)
    pidx = {p: k for k, p in enumerate(periods)}
    fwd_rows = dfc.filter(pl.col("period").is_in(list(set(periods))))
    if overrides:
        fwd_rows = _pl(override_engine.twin.forecast_rows(overrides, _pd(fwd_rows)))
    # groupby(period, dc, sku).forecast_qty.sum(): Kahan in row order (exact for integer
    # forecasts; a scenario's demand.volume override makes them fractional)
    ni = _index(fwd_rows["dc_id"], node_idx)
    si = _index(fwd_rows["sku_id"], sku_idx)
    wi = _index(fwd_rows["period"], pidx)
    keep = (ni >= 0) & (si >= 0) & (wi >= 0)
    flat = (ni[keep] * n_skus + si[keep]) * n_weeks + wi[keep]
    sums, nobs = rt.kahan_sum(fwd_rows["forecast_qty"].cast(pl.Float64).to_numpy()[keep], flat,
                              n_nodes * n_skus * n_weeks)
    fcst = np.where(nobs > 0, sums, 0.0).reshape(n_nodes, n_skus, n_weeks)

    ins = (dfc.filter(pl.col("horizon") == "in_sample")
           .group_by(["period", "dc_id", "sku_id"])
           .agg(pl.col("forecast_qty").sum(), pl.col("actual_qty").fill_nan(0.0).fill_null(0.0).sum())
           .sort(["period", "dc_id", "sku_id"]))
    v = ins.filter(pl.col("forecast_qty") > 0)
    fq = v["forecast_qty"].cast(pl.Float64).to_numpy()
    ratio = np.minimum(v["actual_qty"].cast(pl.Float64).to_numpy() / fq, 10.0)
    vi = _index(v["dc_id"], node_idx)
    vs = _index(v["sku_id"], sku_idx)
    pair_code: dict = {}
    codes = np.array([pair_code.setdefault(x, len(pair_code)) for x in zip(v["dc_id"].to_list(),
                                                                           v["sku_id"].to_list())], dtype=np.int64)
    counts = np.bincount(codes)
    max_obs = int(counts.max())
    order = np.argsort(codes, kind="stable")
    starts = np.zeros(len(counts), dtype=np.int64)
    np.cumsum(counts[:-1], out=starts[1:])
    rank = np.empty(len(codes), dtype=np.int64)
    rank[order] = np.arange(len(codes)) - np.repeat(starts, counts)
    pool = np.ones((n_nodes, n_skus, max_obs))
    wts = np.zeros((n_nodes, n_skus, max_obs))
    ok = (vi >= 0) & (vs >= 0)
    pool[vi[ok], vs[ok], rank[ok]] = ratio[ok]
    wts[vi[ok], vs[ok], rank[ok]] = fq[ok]
    tot = wts.sum(axis=2, keepdims=True)
    has = tot[:, :, 0] > 0
    cdf = np.cumsum(np.divide(wts, np.where(tot > 0, tot, 1.0)), axis=2)
    return fcst, pool, cdf, has, max_obs


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SupplyBrainTwin:
    """digital_twin.SupplyBrainTwin with the per-SKU Python loops vectorised. The
    simulation state, event order and RNG calls are unchanged."""

    def __init__(self, inp: TwinInputs, rng_lt, rng_prod, reserve_own_demand: bool = True):
        self.inp = inp
        self.rng_lt = rng_lt
        self.rng_prod = rng_prod
        self.reserve_own_demand = reserve_own_demand
        n, k = len(inp.nodes), len(inp.skus)
        self.n_nodes, self.n_skus = n, k
        T = inp.horizon_days
        self.on_hand = inp.opening_stock.astype(float).copy()
        self.backlog = np.zeros((n, k))
        self.on_order = np.zeros((n, k))
        self.open_orders = np.zeros((n, k))
        self.pipeline = np.zeros((T + PIPELINE_PAD, n, k))
        ii, jj = np.nonzero(inp.opening_in_transit > 0)
        if len(ii):
            d = np.clip(inp.opening_in_transit_eta_day[ii, jj], 0, T + PIPELINE_PAD - 1).astype(int)
            self.pipeline[d, ii, jj] += inp.opening_in_transit[ii, jj]
            self.on_order[ii, jj] += inp.opening_in_transit[ii, jj]

        self._children: Dict[int, List[int]] = {}
        for dest in range(n):
            for origin in np.unique(inp.supplier_of[dest]):
                if origin >= 0:
                    self._children.setdefault(int(origin), [])
                    if dest not in self._children[int(origin)]:
                        self._children[int(origin)].append(dest)
        self._kid_mask = {(o, d): inp.supplier_of[d] == o for o, kids in self._children.items() for d in kids}
        self._origins = [[int(o) for o in np.where(inp.echelon == ech)[0]] for ech in (0, 1)]
        sup = inp.supplier_of.astype(np.int64).ravel()
        self._sup_valid = sup >= 0
        self._sup_flat = np.where(self._sup_valid, sup * k + np.tile(np.arange(k), n), 0)
        self._lost = inp.lost_sale_node[:, None]
        self._fam = np.array(inp.sku_family)
        self._primary = inp.plant_share >= inp.plant_share.max(axis=0, keepdims=True)
        self._act = np.argwhere(inp.active)
        self._ai, self._aj = self._act[:, 0], self._act[:, 1]

        self.demand_ema = np.zeros((n, k))
        self._prod: list = []
        self._reset_week()
        self._weeks: list = []
        self._rcpt: list = []
        self.cum_holding = np.zeros((n, k))
        self.cum_stockout = np.zeros((n, k))
        self.cum_transport = np.zeros((n, k))
        self._seq = 0

    def _reset_week(self):
        sh = (self.n_nodes, self.n_skus)
        self.wk_opening = self.on_hand.copy()
        self.wk_receipts = np.zeros(sh)
        self.wk_dem_cust = np.zeros(sh)
        self.wk_dem_rep = np.zeros(sh)
        self.wk_unmet_cust = np.zeros(sh)
        self.wk_unmet_rep = np.zeros(sh)
        self.wk_ship = np.zeros(sh)
        self.wk_lost_margin = np.zeros(sh)
        self.wk_onhand_days = np.zeros(sh)

    def _lead_times(self, origin: int, dest: int, n: int, day: int = 0) -> np.ndarray:
        key = (origin, dest)
        m, sd = self.inp.lane_week.get((day // DAYS_PER_WEEK, key)) or self.inp.lane_lt.get(key, (3.0, 1.0))
        return np.maximum(0.5, self.rng_lt.normal(m, max(sd, 0.05), size=n))

    def step(self, day: int) -> None:
        inp = self.inp
        arr = self.pipeline[day]
        if arr.any():
            self.on_hand += arr
            self.on_order = np.maximum(0.0, self.on_order - arr)
            self.wk_receipts += arr
            self.pipeline[day] = 0.0
        if (self.backlog > 0).any():
            use = np.minimum(self.backlog, self.on_hand)
            self.on_hand -= use
            self.backlog -= use
        dq = inp.daily_demand[day].astype(float)
        if dq.any():
            served = np.minimum(dq, self.on_hand)
            short = dq - served
            self.on_hand -= served
            self.backlog += np.where(self._lost, 0.0, short)
            self.wk_dem_cust += dq
            self.wk_unmet_cust += short
            lost = short * inp.margin_per_unit[None, :]
            self.wk_lost_margin += np.where(self._lost, lost, 0.0)
            self.cum_stockout += lost
        if day % DAYS_PER_WEEK == REVIEW_DAY:
            self._review(day)
            self._produce(day)
        self._fulfil(day)
        self.wk_onhand_days += self.on_hand

    def _review(self, day: int) -> None:
        inp = self.inp
        ip = self.on_hand - self.backlog + self.on_order
        pull = inp.echelon > 0
        _ssk, rop, oul = _policy_at(inp, day // DAYS_PER_WEEK)
        need = (ip <= rop) & inp.active & pull[:, None]
        want = np.where(need, np.maximum(0.0, oul - ip), 0.0)
        self.open_orders = want
        self.wk_dem_rep += self._at_parent(want)

    def _at_parent(self, qty: np.ndarray) -> np.ndarray:
        """One bincount over (dest, sku) in row-major order: each parent cell receives
        its children's quantities in ascending dest order, as the original's per-dest
        np.add.at did."""
        q = qty.ravel()
        m = self._sup_valid & (q > 0)
        return np.bincount(self._sup_flat[m], weights=q[m], minlength=q.size).reshape(qty.shape)

    def _fulfil(self, day: int) -> None:
        if not self.open_orders.any():
            return                                  # every request is empty: nothing ships
        for origins in self._origins:
            for origin in origins:
                kids = self._children.get(origin)
                if not kids:
                    continue
                req = {}
                for dest in kids:
                    w = self.open_orders[dest]
                    r = np.where(self._kid_mask[(origin, dest)] & (w > 0), w, 0.0)
                    if r.any():
                        req[dest] = r
                if not req:
                    continue
                total = np.sum(list(req.values()), axis=0)
                avail = self._available(origin, day)
                with np.errstate(divide="ignore", invalid="ignore"):
                    ratio = np.where(total > 0, np.minimum(1.0, avail / total), 0.0)
                for dest, r in req.items():
                    grant = np.floor(r * ratio)
                    if grant.sum() <= 0:
                        continue
                    self.on_hand[origin] -= grant
                    self.open_orders[dest] -= grant
                    self._ship(day, origin, dest, grant, r)

    def _available(self, origin: int, day: int) -> np.ndarray:
        avail = self.on_hand[origin].copy()
        if self.reserve_own_demand:
            ssk, _rop, _oul = _policy_at(self.inp, day // DAYS_PER_WEEK)
            avail = np.maximum(0.0, avail - ssk[origin])
        return avail

    def _ship(self, day: int, origin: int, dest: int, qty: np.ndarray, req: Optional[np.ndarray] = None) -> None:
        inp = self.inp
        idx = np.where(qty > 0)[0]
        if len(idx) == 0:
            return
        lts = self._lead_times(origin, dest, len(idx), day)
        depart = day
        for first, last in inp.lane_blocks.get((origin, dest), ()):
            if first <= day <= last:
                depart = last + 1
        arrd = np.minimum(depart + np.maximum(1, np.ceil(lts)).astype(int), inp.horizon_days + PIPELINE_PAD - 1)
        promise = inp.promise_week.get((day // DAYS_PER_WEEK, (origin, dest))) or \
            inp.lt_promise.get((origin, dest), float(np.mean(lts) * 1.4))
        q = qty[idx]
        self.pipeline[arrd, dest, idx] += q             # (arrival day, sku) pairs are distinct
        self.on_order[dest, idx] += q
        if origin >= 0:
            self.wk_ship[origin] += qty
            self.cum_transport[dest] += qty * inp.logistics_cost[origin, dest]
        short = np.zeros(len(idx)) if req is None else req[idx] - q
        self._rcpt.append((self._seq, day // DAYS_PER_WEEK, idx, dest, origin, q, short, promise, lts, day, arrd))
        self._seq += len(idx)

    def _produce(self, day: int) -> None:
        inp = self.inp
        wk = day // DAYS_PER_WEEK
        signal = self._at_parent(self.open_orders)
        self.demand_ema = DEMAND_EMA_ALPHA * signal + (1 - DEMAND_EMA_ALPHA) * self.demand_ema
        for p in range(len(inp.plants)):
            pdc = int(inp.pdc_of_plant[p])
            share = inp.plant_share[p]
            made = share > 0
            if not made.any():
                continue
            sig = self.demand_ema[pdc] * share
            pos = (self.on_hand[pdc] + self.on_order[pdc]) * share
            plan_unc = np.where(made, np.maximum(0.0, sig + np.maximum(0.0, FG_COVER_WEEKS * sig - pos)), 0.0)
            planned = plan_unc.copy()
            for fam in set(self._fam[made]):
                cap = inp.cap_family_week.get((wk, p, fam), inp.cap_family.get((p, fam)))
                if cap is None:
                    continue
                m = made & (self._fam == fam)
                tot = planned[m].sum()
                if tot > cap:
                    planned[m] *= cap / tot
            cap_p = inp.cap_plant_week.get((wk, p), inp.cap_plant[p])
            tot = planned.sum()
            capped = tot > cap_p
            if capped and tot > 0:
                planned *= cap_p / tot
            adh = self.rng_prod.uniform(*ADHERENCE_RANGE)
            shortfall = inp.adherence_week.get((wk, p))
            if shortfall:
                mult = np.full(len(planned), shortfall.get(None, 1.0))
                for fam, k in shortfall.items():
                    if fam is not None:
                        mult[self._fam == fam] *= k
                actual = np.floor(planned * np.minimum(adh * mult, 1.0))
            else:
                actual = np.floor(planned * adh)
            if (actual > 0).any():
                self._ship(day, -1, pdc, actual)
            s = np.where(plan_unc > 0)[0]
            if len(s):
                self._prod.append((wk, p, s, np.round(plan_unc[s]), np.round(planned[s]), actual[s],
                                   round(adh, 4), int(capped)))

    def run(self, verbose: bool = True):
        T = self.inp.horizon_days
        n_weeks = T // DAYS_PER_WEEK
        for day in range(T):
            self.step(day)
            if (day + 1) % DAYS_PER_WEEK == 0:
                w = day // DAYS_PER_WEEK
                self._close_week(w)
                if verbose and (w % 20 == 0 or w == n_weeks - 1):
                    print(f"  {self.inp.periods[w]}  on-hand {self.on_hand.sum():>11,.0f} u", flush=True)
        return self._collect()

    def _close_week(self, w: int) -> None:
        inp = self.inp
        self.wk_unmet_rep += self._at_parent(self.open_orders)
        self.open_orders[:] = 0.0
        avg = self.wk_onhand_days / DAYS_PER_WEEK
        hold = avg * inp.holding_per_unit_week
        self.cum_holding += hold
        ssk_w, rop_w, oul_w = _policy_at(inp, w)
        a, b = self._ai, self._aj
        dem_c, dem_r = self.wk_dem_cust[a, b], self.wk_dem_rep[a, b]
        un_c, un_r = self.wk_unmet_cust[a, b], self.wk_unmet_rep[a, b]
        dem, unm = dem_c + dem_r, un_c + un_r
        self._weeks.append(np.stack([
            self.wk_opening[a, b], self.wk_receipts[a, b], dem, dem_c, dem_r, dem - unm, unm, un_c, un_r,
            self.wk_lost_margin[a, b], self.on_hand[a, b], ssk_w[a, b], rop_w[a, b], oul_w[a, b], avg[a, b],
            hold[a, b]], axis=1))
        self._reset_week()

    # ---------------------------------------------------------- outputs --
    def _collect(self):
        inp = self.inp
        n_act, n_weeks = len(self._ai), len(self._weeks)
        num = np.concatenate(self._weeks, axis=0) if self._weeks else np.zeros((0, len(INV_NUM)))
        wk = np.repeat(np.arange(n_weeks), n_act)
        node = np.tile(self._ai, n_weeks)
        sku = np.tile(self._aj, n_weeks)
        lt_m, lt_s, cm, cs = [], [], [], []
        for n, s in self._act:
            c_m, c_s = inp.cum_lt[n]
            m, sd = inp.lane_lt.get((int(inp.supplier_of[n, s]), int(n)), (c_m, c_s))
            lt_m.append(m)
            lt_s.append(sd)
            cm.append(c_m)
            cs.append(c_s)
        lt = np.array([lt_m, lt_s, cm, cs], dtype=float).T if n_act else np.zeros((0, 4))
        ftype = np.where(inp.lost_sale_node, "lost_sale", "backorder")
        cols = {"period": _gather(inp.periods, wk), "week_start_date": _gather(inp.week_start_dates, wk),
                "sku_id": _gather(inp.skus, sku), "dc_id": _gather(inp.nodes, node)}
        numeric = dict(zip(INV_NUM, num.T))
        inv = pl.DataFrame({**cols, **{c: numeric[c] for c in INV_NUM[:10]},
                            "fulfilment_type": _gather(ftype.tolist(), node),
                            **{c: numeric[c] for c in INV_NUM[10:]},
                            **{c: np.tile(lt[:, i], n_weeks) for i, c in enumerate(LT_COLS)}})
        inv = inv.select(INV_COLS + LT_COLS)
        arrays = {"node": node, "sku": sku, "week": wk, "num": numeric}
        return inv, self._receipts(), self._production(), arrays

    def _receipts(self) -> pl.DataFrame:
        inp = self.inp
        cols = ["receipt_id", "period", "order_week_start", "sku_id", "dc_id", "origin_node_id", "qty_ordered",
                "qty_received", "promised_lead_days", "actual_lead_days", "ship_day", "arrival_day",
                "on_time_flag", "short_qty"]
        if not self._rcpt:
            return pl.DataFrame({c: [] for c in cols})
        sizes = np.array([len(r[2]) for r in self._rcpt])
        rep = lambda i: np.repeat(np.array([r[i] for r in self._rcpt]), sizes)  # noqa: E731
        cat = lambda i: np.concatenate([r[i] for r in self._rcpt])              # noqa: E731
        seq = np.concatenate([np.arange(r[0] + 1, r[0] + 1 + len(r[2])) for r in self._rcpt])
        wk, dest, origin, day = rep(1), rep(3), rep(4), rep(9)
        qty, short, lts, arrd, sku = cat(5), cat(6), cat(8), cat(10), cat(2)
        promise = np.repeat(np.array([float(r[7]) for r in self._rcpt]), sizes)
        prom_round = np.repeat(np.array([round(r[7], 2) for r in self._rcpt], dtype=float), sizes)
        origin_names = np.array(["PLANT"] + list(inp.nodes), dtype=object)
        return pl.DataFrame({
            "receipt_id": [f"RCP{s:07d}" for s in seq.tolist()],
            "period": _gather(inp.periods, wk), "order_week_start": _gather(inp.week_start_dates, wk),
            "sku_id": _gather(inp.skus, sku), "dc_id": _gather(inp.nodes, dest),
            "origin_node_id": pl.Series(origin_names.tolist(), dtype=pl.String).gather(origin + 1),
            "qty_ordered": qty + short, "qty_received": qty, "promised_lead_days": prom_round,
            "actual_lead_days": np.array(rt.py_round(lts, 2), dtype=float), "ship_day": day.astype(np.int64),
            "arrival_day": arrd.astype(np.int64), "on_time_flag": (lts <= promise).astype(np.int64),
            "short_qty": short,
        })

    def _production(self) -> pl.DataFrame:
        inp = self.inp
        cols = ["period", "week_start_date", "plant_id", "sku_id", "production_family", "is_primary",
                "plan_uncapped_qty", "planned_qty", "actual_qty", "adherence_pct", "capacity_limited_flag"]
        if not self._prod:
            return pl.DataFrame({c: [] for c in cols})
        sizes = np.array([len(r[2]) for r in self._prod])
        wk = np.repeat([r[0] for r in self._prod], sizes)
        plant = np.repeat([r[1] for r in self._prod], sizes)
        sku = np.concatenate([r[2] for r in self._prod])
        fam = [None if (isinstance(f, float) and np.isnan(f)) else f for f in inp.sku_family]
        return pl.DataFrame({
            "period": _gather(inp.periods, wk), "week_start_date": _gather(inp.week_start_dates, wk),
            "plant_id": _gather(inp.plants, plant), "sku_id": _gather(inp.skus, sku),
            "production_family": pl.Series(fam, dtype=pl.String).gather(sku),
            "is_primary": self._primary[plant, sku],
            "plan_uncapped_qty": np.concatenate([r[3] for r in self._prod]),
            "planned_qty": np.concatenate([r[4] for r in self._prod]),
            "actual_qty": np.concatenate([r[5] for r in self._prod]),
            "adherence_pct": np.repeat(np.array([r[6] for r in self._prod], dtype=float), sizes),
            "capacity_limited_flag": np.repeat(np.array([r[7] for r in self._prod], dtype=np.int64), sizes),
        })


def _gather(labels: list, idx: np.ndarray) -> pl.Series:
    return pl.Series(list(labels), dtype=pl.String).gather(np.asarray(idx, dtype=np.int64))


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def summarise(inv: pl.DataFrame, twin: SupplyBrainTwin, inp: TwinInputs, arrays: dict):
    """(service_summary, service_by_node, cost_summary) as digital_twin.summarise."""
    cat = dict(zip(inp.skus, inp.sku_category))
    svc_sku = (inv.group_by("sku_id", maintain_order=False).agg(
        pl.col("demand_customer_qty").sum().alias("total_demand"), pl.col("unmet_customer_qty").sum().alias("unmet"),
        pl.col("demand_replen_qty").sum().alias("replen_demand"),
        pl.col("unmet_replen_qty").sum().alias("replen_unmet"), pl.col("avg_on_hand").mean().alias("avg_on_hand"),
        pl.col("holding_cost_usd").sum().alias("holding_cost_usd")).sort("sku_id"))
    svc_sku = svc_sku.with_columns((pl.col("total_demand") - pl.col("unmet")).alias("served_from_stock"))
    svc_sku = svc_sku.with_columns(
        pl.when(pl.col("total_demand") > 0).then(pl.col("served_from_stock") / pl.col("total_demand"))
        .otherwise(None).alias("fill_rate_customer"),
        pl.when(pl.col("replen_demand") > 0).then(1 - pl.col("replen_unmet") / pl.col("replen_demand"))
        .otherwise(None).alias("fill_rate_replen"),
        pl.col("sku_id").replace_strict(cat, default=None, return_dtype=pl.String).alias("category"))

    node_type = {n: inp.node_type[i] for i, n in enumerate(inp.nodes)}
    svc_node = (inv.with_columns(pl.col("dc_id").replace_strict(node_type, default=None, return_dtype=pl.String)
                                 .alias("node_type"))
                .group_by(["dc_id", "node_type"]).agg(
        pl.col("sku_id").n_unique().alias("skus"), pl.col("demand_customer_qty").sum().alias("total_demand"),
        pl.col("unmet_customer_qty").sum().alias("unmet"), pl.col("demand_replen_qty").sum().alias("replen_demand"),
        pl.col("unmet_replen_qty").sum().alias("replen_unmet"), pl.col("avg_on_hand").mean().alias("avg_on_hand"),
        pl.col("holding_cost_usd").sum().alias("holding_cost_usd")).sort(["dc_id", "node_type"]))
    svc_node = svc_node.with_columns(
        pl.when(pl.col("total_demand") > 0).then(1 - pl.col("unmet") / pl.col("total_demand"))
        .otherwise(None).alias("fill_rate_customer"),
        pl.when(pl.col("replen_demand") > 0).then(1 - pl.col("replen_unmet") / pl.col("replen_demand"))
        .otherwise(None).alias("fill_rate_replen"))

    node, sku, avg = arrays["node"], arrays["sku"], arrays["num"]["avg_on_hand"]
    n_weeks = int(arrays["week"].max()) + 1 if len(arrays["week"]) else 0
    k = len(inp.skus)
    pair_mean = rt.kahan_mean(avg, node * k + sku, len(inp.nodes) * k).reshape(len(inp.nodes), k)
    rows = []
    for i, n in enumerate(inp.nodes):
        mask = node == i
        sub_avg = avg[mask]
        weeks = n_weeks if mask.any() else 0
        per_sku = np.where(np.isnan(pair_mean[i]), 0.0, pair_mean[i])
        rows.append({
            "dc_id": n, "node_type": inp.node_type[i],
            "avg_units_on_hand": float(np.add.reduce(sub_avg)) / max(weeks, 1),
            "avg_inventory_value_usd": float((per_sku * inp.unit_cost).sum()),
            "holding_cost_usd": float(twin.cum_holding[i].sum()),
            "stockout_cost_usd": float(twin.cum_stockout[i].sum()),
            "transport_cost_usd": float(twin.cum_transport[i].sum()),
        })
    total = np.array([r["holding_cost_usd"] + r["stockout_cost_usd"] + r["transport_cost_usd"] for r in rows])
    for r, t in zip(rows, total):
        r["total_landed_cost_usd"] = float(t)
    order = rt.pandas_argsort(total, ascending=False)
    costs = pl.DataFrame([rows[i] for i in order])
    return svc_sku.select(["sku_id", "total_demand", "unmet", "replen_demand", "replen_unmet", "avg_on_hand",
                           "holding_cost_usd", "served_from_stock", "fill_rate_customer", "fill_rate_replen",
                           "category"]), svc_node, costs


def kpis(inv: pl.DataFrame, twin: SupplyBrainTwin, inp: TwinInputs, costs: pl.DataFrame, arrays: dict) -> dict:
    """digital_twin.kpis, same reduction order (pairwise column sums, Kahan groupbys)."""
    num, node, sku, week = arrays["num"], arrays["node"], arrays["sku"], arrays["week"]
    cd, cu = np.add.reduce(num["demand_customer_qty"]), np.add.reduce(num["unmet_customer_qty"])
    rd, ru = np.add.reduce(num["demand_replen_qty"]), np.add.reduce(num["unmet_replen_qty"])
    n_weeks = int(week.max()) + 1 if len(week) else 0
    k = len(inp.skus)
    avg = num["avg_on_hand"]
    by_ps, _ = rt.kahan_sum(avg, week * k + sku, n_weeks * k)
    present = np.bincount(week * k + sku, minlength=n_weeks * k) > 0
    by_ps = np.where(present, by_ps, 0.0).reshape(n_weeks, k)
    inv_val = (np.asfortranarray(by_ps) * inp.unit_cost).sum(axis=1)       # unstack().to_numpy() is F-ordered
    per_period, _ = rt.kahan_sum(avg, week, n_weeks)
    dem = num["demand_customer_qty"] > 0
    in_full = float((num["unmet_customer_qty"][dem] <= 0).mean()) if dem.any() else None
    is_cdc = inp.echelon[node] == 2
    is_rdc = inp.echelon[node] == 1
    c_m, r_m = dem & is_cdc, dem & is_rdc
    return {
        "horizon_weeks": n_weeks,
        "first_period": inp.periods[0], "last_period": inp.periods[-1],
        "customer_demand_units": float(cd),
        "customer_fill_rate": round(float(1 - cu / cd), 4) if cd else None,
        "customer_fill_rate_cdc": round(float(1 - np.add.reduce(num["unmet_customer_qty"][c_m])
                                              / np.add.reduce(num["demand_customer_qty"][c_m])), 4)
        if c_m.any() else None,
        "customer_fill_rate_rdc": round(float(1 - np.add.reduce(num["unmet_customer_qty"][r_m])
                                              / np.add.reduce(num["demand_customer_qty"][r_m])), 4)
        if r_m.any() else None,
        "pct_demand_weeks_in_full": round(in_full, 4) if in_full is not None else None,
        "internal_replen_fill_rate": round(float(1 - ru / rd), 4) if rd else None,
        "avg_units_on_hand_network": round(float(per_period.mean()), 1),
        "avg_inventory_value_usd": round(float(inv_val.mean()), 2),
        "safety_stock_value_usd": round(float(((inp.safety_stock.mean(axis=0) if inp.safety_stock.ndim == 3
                                                else inp.safety_stock) * inp.unit_cost[None, :]).sum()), 2),
        "holding_cost_usd": round(float(twin.cum_holding.sum()), 2),
        "stockout_cost_usd": round(float(twin.cum_stockout.sum()), 2),
        "transport_cost_usd": round(float(twin.cum_transport.sum()), 2),
        "total_landed_cost_usd": round(float(np.add.reduce(costs["total_landed_cost_usd"].to_numpy())), 2),
        "pct_weeks_at_zero_stock": round(float((num["closing_stock"] <= 0).mean()), 4),
        "margin_basis": MARGIN_COLUMN,
    }


LEAN_SKIPPED = ("inventory.parquet", "replenishment_receipts.parquet", "production.parquet")


def write_run(out: Path, inv, rcpt, prod, twin, inp, notes, args, t0, scenario_config=None, arrays=None,
              lean: bool = False):
    """digital_twin.write_run. `lean` skips the three large detail tables (LEAN_SKIPPED);
    the manifest records that they were not written."""
    out.mkdir(parents=True, exist_ok=True)
    svc_sku, svc_node, costs = summarise(inv, twin, inp, arrays)
    k = kpis(inv, twin, inp, costs, arrays)
    if lean:
        for name in LEAN_SKIPPED:              # a stale copy from an earlier full run must not pass for this one
            (out / name).unlink(missing_ok=True)
    else:
        rt.write_table(inv, out / "inventory.parquet")
        rt.write_table(rcpt, out / "replenishment_receipts.parquet")
        rt.write_table(prod, out / "production.parquet")
    rt.write_table(svc_sku, out / "service_summary.csv")
    rt.write_table(svc_node, out / "service_by_node.csv")
    rt.write_table(costs, out / "cost_summary.csv")
    (out / "run_kpis.json").write_text(json.dumps(k, indent=2))
    ssk0, rop0, oul0 = _policy_at(inp, 0)
    a, b = twin._ai, twin._aj
    rt.write_table(pl.DataFrame({"dc_id": _gather(inp.nodes, a), "sku_id": _gather(inp.skus, b),
                               "safety_stock": ssk0[a, b], "reorder_point": rop0[a, b],
                               "order_up_to_level": oul0[a, b]}), out / "policy.parquet")
    manifest = {
        "schema_version": SCHEMA_VERSION, "run_name": args.run_name, "label": args.label, "mode": args.mode,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": args.seed, "policy_override": args.policy,
        "margin_basis": MARGIN_COLUMN, "runtime_seconds": round(time.time() - t0, 1), "assumptions": ASSUMPTIONS,
        "build_notes": _jsonable(notes),
    }
    if lean:
        manifest["lean_outputs"] = {"not_written": list(LEAN_SKIPPED)}
    if scenario_config is not None:
        manifest["scenario"] = scenario_config
        (out / "scenario_config.json").write_text(json.dumps(scenario_config, indent=2) + "\n", encoding="utf-8")
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return k, svc_node, costs


def run_twin(data_dir: Path, mode: str, seed: int, horizon_weeks: Optional[int] = None, policy_csv=None,
             state_csv: Optional[Path] = None, verbose: bool = True, scenario_changes: Optional[dict] = None,
             overrides=None, policy_frame: Optional[pl.DataFrame] = None):
    inp, notes, rng_lt, rng_prod = build_inputs(data_dir, mode=mode, seed=seed, horizon_weeks=horizon_weeks,
                                                policy_csv=policy_csv, state_csv=state_csv,
                                                scenario_changes=scenario_changes, overrides=overrides,
                                                policy_frame=policy_frame)
    twin = SupplyBrainTwin(inp, rng_lt, rng_prod)
    inv, rcpt, prod, arrays = twin.run(verbose=verbose)
    return inp, notes, twin, inv, rcpt, prod, arrays


# ---------------------------------------------------------------------------
# Summaries for the research scripts
# ---------------------------------------------------------------------------

def fill_rate_kpis(cols: dict, mask: np.ndarray) -> dict:
    """validate_real_data.fill_rate_kpis on the rows in `mask`, in row order: the same
    sums (numpy pairwise, as pandas' Series.sum) on the same values."""
    g = {c: v[mask] for c, v in cols.items()}
    ech = g["echelon"]
    cd, cu = np.add.reduce(g["demand_customer_qty"]), np.add.reduce(g["unmet_customer_qty"])
    rd, ru = np.add.reduce(g["demand_replen_qty"]), np.add.reduce(g["unmet_replen_qty"])
    dc_d, dc_u = np.add.reduce(g["demand_customer_qty"][ech == 2]), np.add.reduce(g["unmet_customer_qty"][ech == 2])
    rc_d, rc_u = np.add.reduce(g["demand_customer_qty"][ech == 1]), np.add.reduce(g["unmet_customer_qty"][ech == 1])
    dw = g["demand_customer_qty"] > 0
    return {
        "weeks": int(len(np.unique(g["week"]))), "customer_demand_units": float(cd),
        "customer_fill_rate": round(float(1 - cu / cd), 4) if cd else None,
        "customer_fill_rate_cdc": round(float(1 - dc_u / dc_d), 4) if dc_d else None,
        "customer_fill_rate_rdc": round(float(1 - rc_u / rc_d), 4) if rc_d else None,
        "pct_demand_weeks_in_full": round(float((g["unmet_customer_qty"][dw] <= 0).mean()), 4) if dw.any() else None,
        "internal_replen_fill_rate": round(float(1 - ru / rd), 4) if rd else None,
        "pct_weeks_at_zero_stock": round(float((g["closing_stock"] <= 0).mean()), 4),
        "holding_cost_usd_window": round(float(np.add.reduce(g["holding_cost_usd"])), 2),
    }


SUMMARY_COLS = ["demand_customer_qty", "unmet_customer_qty", "demand_replen_qty", "unmet_replen_qty",
                "closing_stock", "holding_cost_usd", "avg_on_hand"]


def summarize(period: np.ndarray, sku: np.ndarray, dc: np.ndarray, cols: dict, request: dict | None) -> dict:
    """What the research scripts read off a run's inventory, computed where the run is:
    fill_rate_kpis per window, the warm-up parity sums, and customer demand / unmet per
    (window, SKU group). `request` = {"windows": {name: (lo, hi)}, "groups": {name: [sku]}}
    with inclusive period-string bounds (None = open)."""
    if not request:
        return {}
    cols = dict(cols)
    cols["echelon"] = np.array([{"PDC": 0, "RDC": 1, "CDC": 2}.get(str(d)[:3], -1) for d in dc])
    uniq, cols["week"] = np.unique(period, return_inverse=True)
    out = {"fill": {}, "sums": {}, "groups": {}}
    group_windows = request.get("group_windows")
    in_group = {g: np.isin(sku, list(skus)) for g, skus in (request.get("groups") or {}).items()}
    for name, (lo, hi) in (request.get("windows") or {}).items():
        mask = np.ones(len(period), dtype=bool)
        if lo is not None:
            mask &= period >= lo
        if hi is not None:
            mask &= period <= hi
        out["fill"][name] = fill_rate_kpis(cols, mask)
        out["sums"][name] = {c: float(np.add.reduce(cols[c][mask])) for c in
                             ("demand_customer_qty", "unmet_customer_qty", "avg_on_hand", "holding_cost_usd")}
        if group_windows is not None and name not in group_windows:
            continue
        for g, member in in_group.items():
            m = mask & member
            out["groups"][f"{name}|{g}"] = (float(np.add.reduce(cols["demand_customer_qty"][m])),
                                            float(np.add.reduce(cols["unmet_customer_qty"][m])))
    return out


def summarize_inventory_file(path, request) -> dict:
    """summarize() for a run already on disk (a cached baseline)."""
    inv = rt.read_table(path, columns=["period", "sku_id", "dc_id"] + SUMMARY_COLS, cache=False)
    cols = {c: inv[c].cast(pl.Float64).to_numpy() for c in SUMMARY_COLS}
    return summarize(np.array(inv["period"].to_list(), dtype=object), np.array(inv["sku_id"].to_list(), dtype=object),
                     np.array(inv["dc_id"].to_list(), dtype=object), cols, request)


# ---------------------------------------------------------------------------
# CLI and in-process entry
# ---------------------------------------------------------------------------

class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def resolve_args(run_name=None, mode=None, seed=None, horizon_weeks=None, policy=None, state=None, label="",
                 scenario_config=None, data_dir=None, out_root=None):
    """main()'s argument resolution: anything not given comes from the scenario config."""
    root = rt.REPO_ROOT
    config_path = Path(scenario_config) if scenario_config else root / "scenario_config.json"
    config = scenario.load_config(config_path)
    twin_config = config["digital_twin"]
    a = _Args(run_name=run_name or twin_config.get("run_name", "baseline"),
              mode=mode or twin_config.get("mode", "forward"),
              seed=seed if seed is not None else int(twin_config.get("seed", 2026)),
              horizon_weeks=horizon_weeks, label=label)
    a.data_dir = str(data_dir or scenario.resolve_path(config_path, config, "digital_twin", "data_dir"))
    a.policy = str(policy or scenario.resolve_path(config_path, config, "digital_twin", "policy"))
    a.state = str(state or scenario.resolve_path(config_path, config, "digital_twin", "state"))
    a.out_root = str(out_root or scenario.resolve_path(config_path, config, "digital_twin", "output_root"))
    a.scenario_config = str(config_path)
    return a, config_path, config


def run_dir_for(args, out_root: Path) -> Path:
    _, run_dir = load_scenario_config(Path(args.scenario_config), out_root)
    return run_dir / args.run_name


def simulate(*, run_name=None, mode=None, seed=None, horizon_weeks=None, policy=None, state=None, label="",
             scenario_config=None, data_dir=None, out_root=None, write_docs: bool = False, verbose: bool = False,
             summary: dict | None = None, policy_frame: pl.DataFrame | None = None, lean: bool = False) -> dict:
    """One twin run exactly as `python digital_twin.py ...` would do it, in this process.
    Writes the same run folder; returns {"kpis", "run_dir", "summary", "seconds"}.
    `write_docs` is the inverse of --skip-output-documentation (off: several runs in
    parallel would rewrite the one shared file; the caller writes it once at the end)."""
    t0 = time.time()
    args, config_path, config = resolve_args(run_name, mode, seed, horizon_weeks, policy, state, label,
                                             scenario_config, data_dir, out_root)
    data_dir, out_root = Path(args.data_dir), Path(args.out_root)
    scenario_config_obj, run_dir = load_scenario_config(Path(args.scenario_config), out_root)
    run_dir = run_dir / args.run_name
    rt.write_text_shared(run_dir.parent / "scenario_config.json",
                         Path(args.scenario_config).read_text(encoding="utf-8"))
    scenario_config_obj = _scenario_execution(scenario_config_obj, args, run_dir)
    full = override_engine.load(config, data_dir=data_dir, state_csv=args.state) if args.mode == "forward" else None
    actual = full.side("actual", f"twin_{args.run_name}") if full else None
    legacy = scenario_config_obj.get("changes") if (scenario_config_obj and full is None) else None
    if args.mode != "forward" and (config.get("changes") or {}):
        raise SystemExit("scenario overrides apply to forward runs only")
    if verbose:
        print(f"Supply Brain digital twin (optimized) -- '{args.run_name}' [{args.mode}]")
        print(f"  margin basis: {MARGIN_COLUMN}")
        if full:
            print(f"  override engine {override_engine.ENGINE_VERSION}: {len(actual)} actual-side override(s) "
                  f"of {len(full)}")
    inp, notes, twin, inv, rcpt, prod, arrays = run_twin(
        data_dir, args.mode, args.seed, args.horizon_weeks, Path(args.policy),
        Path(args.state) if args.state else None, verbose=verbose, scenario_changes=legacy,
        overrides=actual or None, policy_frame=policy_frame)
    k, _svc_node, _costs = write_run(run_dir, inv, rcpt, prod, twin, inp, notes, args, t0, scenario_config_obj,
                                     arrays, lean=lean)
    if full:
        receipt = override_engine.write_receipts(scenario.overrides_dir(config_path, config),
                                                 f"twin_{args.run_name}", actual, full)
        if verbose:
            print(f"  override receipts: {receipt}")
    if write_docs:
        n = write_output_documentation(out_root)
        if verbose:
            print(f"  wrote OUTPUT_DOCUMENTATION.md ({n:,} lines)")
    out = {"kpis": k, "run_dir": str(run_dir), "seconds": round(time.time() - t0, 1)}
    if summary:
        out["summary"] = summarize(inv["period"].to_numpy(), inv["sku_id"].to_numpy(), inv["dc_id"].to_numpy(),
                                   {c: arrays["num"][c] for c in SUMMARY_COLS}, summary)
    return out


def simulate_task(job: dict) -> dict:
    """Process-pool entry: job = simulate() keyword arguments, plus "use_cache": reuse a
    run already on disk (run_kpis.json + inventory.parquet), as the originals' baselines did."""
    job = dict(job)
    use_cache = job.pop("use_cache", False)
    if use_cache:
        args, _, _ = resolve_args(job.get("run_name"), job.get("mode"), job.get("seed"), job.get("horizon_weeks"),
                                  job.get("policy"), job.get("state"), job.get("label", ""),
                                  job.get("scenario_config"), job.get("data_dir"), job.get("out_root"))
        run_dir = run_dir_for(args, Path(args.out_root))
        if (run_dir / "run_kpis.json").exists() and (run_dir / "inventory.parquet").exists():
            out = {"kpis": json.loads((run_dir / "run_kpis.json").read_text()), "run_dir": str(run_dir),
                   "cached": True, "seconds": 0.0}
            if job.get("summary"):
                out["summary"] = summarize_inventory_file(run_dir / "inventory.parquet", job["summary"])
            return out
    return simulate(**job)


def print_kpis(k: dict, title: str, run_dir, seconds: float) -> None:
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)
    print(f"  Customer fill rate      {k['customer_fill_rate']:>12.2%}")
    print(f"    - CDC                 {(k['customer_fill_rate_cdc'] or 0):>12.2%}")
    print(f"    - RDC direct          {(k['customer_fill_rate_rdc'] or 0):>12.2%}")
    print(f"  Demand-weeks in full    {(k['pct_demand_weeks_in_full'] or 0):>12.2%}")
    print(f"  Internal replen fill    {(k['internal_replen_fill_rate'] or 0):>12.2%}")
    print(f"  Avg inventory value     ${k['avg_inventory_value_usd']:>11,.0f}")
    print(f"  Holding                 ${k['holding_cost_usd']:>11,.0f}")
    print(f"  Stockout                ${k['stockout_cost_usd']:>11,.0f}")
    print(f"  Transport               ${k['transport_cost_usd']:>11,.0f}")
    print(f"  TOTAL LANDED COST       ${k['total_landed_cost_usd']:>11,.0f}")
    print("=" * 62)
    print(f"Wrote {run_dir}  ({seconds:.0f}s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--mode", choices=["forward", "history"], default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--horizon-weeks", type=int, default=None)
    ap.add_argument("--policy", default=None)
    ap.add_argument("--state", default=None)
    ap.add_argument("--label", default="")
    ap.add_argument("--scenario-config", default=None)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--out-root", default=None)
    ap.add_argument("--skip-output-documentation", action="store_true",
                    help="Do not rewrite the shared OUTPUT_DOCUMENTATION.md (for parallel runs).")
    ap.add_argument("--lean-outputs", action="store_true",
                    help="Skip inventory.parquet, replenishment_receipts.parquet and production.parquet (~30 MB per "
                         "156-week run); KPIs, summaries, policy and manifest are still written.")
    a = ap.parse_args()
    res = simulate(run_name=a.run_name, mode=a.mode, seed=a.seed, horizon_weeks=a.horizon_weeks, policy=a.policy,
                   state=a.state, label=a.label, scenario_config=a.scenario_config, data_dir=a.data_dir,
                   out_root=a.out_root, write_docs=not a.skip_output_documentation, verbose=True,
                   lean=a.lean_outputs)
    print_kpis(res["kpis"], a.label or Path(res["run_dir"]).name, res["run_dir"], res["seconds"])


if __name__ == "__main__":
    main()
