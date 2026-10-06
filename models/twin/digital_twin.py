"""
Supply Brain — Digital Twin (Layer 0)
=====================================

Single-file discrete-event simulation of the Supply Brain multi-echelon
network. It is the project's only physics engine: `supply_brain_generator_v5`
uses it to generate the historical dataset, and MEIO 2.0 uses it to score
candidate policies. Those two things sharing one engine is the point — if the
history and the reward come from different simulators, no measured improvement
can be trusted.

Design follows ISOMORPH (github.com/tuhinsahai/ISOMORPH):
  - one step is one day
  - fixed event ordering inside step()
  - explicit in-transit pipeline keyed by arrival day
  - a run directory is self-describing (manifest of every knob + assumption)

Network:  Plant --0.5d--> Plant DC --> Regional DC --> Customer DC
  - Plant is production-only and holds no stock. Plant DCs are *pushed* stock
    by production, not pulled by a reorder point.
  - RDCs and CDCs pull on a weekly (R,s,S) review.
  - RDCs carry both direct bulk-customer demand and downstream CDC demand.

THE OPTIMIZATION SEAM
---------------------
The only thing an optimizer changes is one standalone CSV:

    dc_id, sku_id, safety_stock, reorder_point, order_up_to_level

Extra columns are ignored, so an optimizer's native output can be passed
verbatim:

    --policy "output datasets/meio_v2/policy.csv"

Opening state comes from a separate --state file and is never touched by a
policy swap, so two runs start from an identical physical position and the
difference between them measures the decision. Demand, lead-time and
production RNG are independent streams, so changing the policy does not
move the demand draw.

Both are read from explicit paths, so the twin has no hardcoded route into
either folder. The split between them is TIME, not authorship:
`input datasets/` holds the past and the givens -- masters, economics, the
order history, the published forecast, and the inventory record of what
happened -- while `output datasets/` holds the forward view: the forward
policy, the position the horizon opens on, and every scenario run. So the
historical tables this engine writes belong in the input folder; only its
forward output is a run artefact.

Echelon positioning needs no structural input either — "push buffer to the
customer DCs" is just higher CDC S and lower RDC S in the same table.

Modes
-----
  history      the span the order history covers; demand replayed from
               orders.csv. Produces the dataset's inventory record.
  forward      the planning horizon; demand = forecast x resampled forecast
               error. The scenario mode, for baseline and optimized runs.

Usage
-----
  # forward scenario, opening from the end of history
  python digital_twin.py --mode forward --run-name baseline_2026 \
    --policy "output datasets/history/forward_policy.csv" \
    --state  "output datasets/history/opening_position.csv"

  # score an optimized policy against the same opening position
  python digital_twin.py --mode forward --run-name meio_v2 \
      --policy "output datasets/meio_v2/policy.csv" \
    --state  "output datasets/history/closing_position.csv"
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Conventions
# ---------------------------------------------------------------------------

DAYS_PER_WEEK = 7
REVIEW_DAY = 0                 # weekly review fires on the first day of a week
PIPELINE_PAD = 90              # in-transit buffer beyond the horizon
# Calendar anchor. Mirrors START_YEAR / week counts in the generator; the
# horizon is expressed in weeks, never in a named calendar year.
START_YEAR = 2023
WEEK1_MONDAY = pd.Timestamp(f"{START_YEAR}-01-02")
HISTORY_WEEKS = 156            # weeks the order history covers
SCHEMA_VERSION = "3.0"

# production planning
# Finished-goods cover the plant plans back to. Sized against the lumpiness
# that batched downstream ordering creates, not against average demand: at
# 1.5 weeks the plant DCs cannot absorb a week in which several RDCs reorder
# at once, and internal fill pins near 85% however much buffer sits below.
FG_COVER_WEEKS = 2.5
DEMAND_EMA_ALPHA = 0.25
ADHERENCE_RANGE = (0.90, 1.00)  # schedule adherence, healthy CPG band

# supplier lead-time quoting: committed lead time is the ~90th percentile,
# which is what makes on-time delivery land near 90% with a real late tail
PROMISE_Z = 1.2816             # 90th percentile of a normal lane


# ┌──────────────────────────────────────────────────────────────────────────┐
# │ MARGIN BASIS SWITCH                                                      │
# │                                                                          │
# │ Stockout cost is margin-per-unit x units short. Today that is the GROSS  │
# │ margin straight off sku_master.                                          │
# │                                                                          │
# │ To move to NET margin (after off-invoice, trade fund, volume rebate and  │
# │ logistics allowance — roughly 11-15% of list, see b2b_customer_terms),   │
# │ change the single string below to the net-margin column and make sure    │
# │ that column exists in sku_master.csv. A per-unit net margin can be       │
# │ derived from orders.csv as net_margin_usd.sum() / order_qty.sum()        │
# │ grouped by sku_id.                                                       │
# │                                                                          │
# │ Nothing else in this file needs to change.                               │
# └──────────────────────────────────────────────────────────────────────────┘
MARGIN_COLUMN = "margin_usd"


def period_label(i: int) -> str:
    """1-indexed week number from 2023-W01 -> 'YYYY-Www'."""
    return f"{START_YEAR + (i - 1) // 52}-W{(i - 1) % 52 + 1:02d}"


def week_monday(i: int) -> str:
    return (WEEK1_MONDAY + pd.Timedelta(days=7 * (i - 1))).strftime("%Y-%m-%d")


def echelon_of(node_id: str) -> int:
    return {"PDC": 0, "RDC": 1, "CDC": 2}[node_id[:3]]


NODE_TYPE = {"PDC": "PLANT_DC", "RDC": "REGIONAL_DC", "CDC": "CUSTOMER_DC"}


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
    week_index: List[int]                  # 1-indexed week number from 2023-W01

    node_idx: Dict[str, int]
    sku_idx: Dict[str, int]
    echelon: np.ndarray
    node_type: List[str]

    supplier_of: np.ndarray                # [n_nodes, n_skus] parent node, -1 = plant
    pdc_of_plant: np.ndarray               # [n_plants] node index of each plant's DC
    plant_share: np.ndarray                # [n_plants, n_skus] production allocation
    cap_plant: np.ndarray                  # [n_plants] units/week
    cap_family: Dict[Tuple[int, str], float]
    sku_family: List[str]

    reorder_point: np.ndarray              # [n_nodes, n_skus], or
    order_up_to: np.ndarray                # [n_weeks, n_nodes, n_skus] if
    safety_stock: np.ndarray               # the policy CSV has a `period`
                                            # column -- use _policy_at(inp, w)
    active: np.ndarray

    opening_stock: np.ndarray
    opening_in_transit: np.ndarray
    opening_in_transit_eta_day: np.ndarray

    daily_demand: np.ndarray               # [T, n_nodes, n_skus] int32
    lost_sale_node: np.ndarray             # [n_nodes] bool (CDC -> lost sale)

    lane_lt: Dict[Tuple[int, int], Tuple[float, float]]
    lt_promise: Dict[Tuple[int, int], float]
    cum_lt: Dict[int, Tuple[float, float]]

    holding_per_unit_week: np.ndarray
    margin_per_unit: np.ndarray
    unit_cost: np.ndarray
    logistics_cost: np.ndarray

    sku_category: List[str]
    horizon_days: int


def build_inputs(data_dir: Path, mode: str = "forward", seed: int = 2026,
                 horizon_weeks: Optional[int] = None,
                 policy_csv: Optional[Path] = None,
                 state_csv: Optional[Path] = None
                 ) -> Tuple[TwinInputs, dict]:
    """Assemble everything the engine needs, indexed and array-shaped."""
    # Independent RNG streams. Keeping them separate is what makes common
    # random numbers possible: changing the policy must not shift the demand
    # or lead-time draws, or two runs are not comparable.
    ss = np.random.SeedSequence(seed)
    rng_demand, rng_lt, rng_prod = [np.random.default_rng(s)
                                    for s in ss.spawn(3)]
    notes: Dict[str, object] = {"mode": mode, "seed": seed}

    sku_master = pd.read_csv(data_dir / "sku_master.csv")
    cost_p = pd.read_csv(data_dir / "cost_parameters.csv")
    lanes = pd.read_csv(data_dir / "network_lanes.csv")
    # sku_sourcing_map: routing — which parent serves each (node, SKU)
    # sku_plant_sourcing: production — how volume splits across the plants
    #                     qualified to make each SKU
    sourcing_map = pd.read_csv(data_dir / "sku_sourcing_map.csv")
    sourcing = pd.read_csv(data_dir / "sku_plant_sourcing.csv")
    plant_dc = pd.read_csv(data_dir / "plant_dc_master.csv")
    plant_master = pd.read_csv(data_dir / "plant_master.csv")
    plant_cap = pd.read_csv(data_dir / "plant_capability.csv")

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
    dc_ids = sorted(set(lanes.destination_node_id) |
                    set(lanes.origin_node_id) - set(plant_master.plant_id))
    dc_ids = [d for d in dc_ids if d[:3] in NODE_TYPE]
    nodes = sorted(dc_ids, key=lambda n: (echelon_of(n), n))
    skus = sorted(sku_master.sku_id.unique())
    plants = sorted(plant_master.plant_id.unique())
    node_idx = {n: i for i, n in enumerate(nodes)}
    sku_idx = {s: i for i, s in enumerate(skus)}
    plant_i = {p: i for i, p in enumerate(plants)}
    n_nodes, n_skus, n_plants = len(nodes), len(skus), len(plants)

    echelon = np.array([echelon_of(n) for n in nodes], dtype=np.int8)
    node_type = [NODE_TYPE[n[:3]] for n in nodes]
    lost_sale_node = echelon == 2          # CDC is DSD -> lost sale

    # ----------------------------------------------------------- structure --
    # Which parent serves each (node, SKU) is network structure, and arrives
    # as an input table. It is emphatically NOT mined from a previous
    # simulation's receipts: that would make the twin depend on a file the
    # twin itself produces, so a clean dataset could never be bootstrapped and
    # every run would inherit the previous one's routing.
    #
    # The lane a SKU travels matters a great deal -- PDC03->RDC04 is 0.5 days
    # while PDC04->RDC05 is 12.9 -- so this mapping is load-bearing rather
    # than a convenience.
    supplier_of = np.full((n_nodes, n_skus), -1, dtype=np.int16)
    resolved = 0
    for r in sourcing_map.itertuples(index=False):
        if (r.dc_id in node_idx and r.sku_id in sku_idx
                and r.origin_node_id in node_idx):
            supplier_of[node_idx[r.dc_id], sku_idx[r.sku_id]] = \
                node_idx[r.origin_node_id]
            resolved += 1

    plant_to_pdc = dict(zip(plant_dc.plant_id, plant_dc.plant_dc_id))
    min_lane = (lanes.sort_values("lead_time_mean_days")
                .drop_duplicates("destination_node_id")
                .set_index("destination_node_id").origin_node_id)
    n_fallback = 0
    for n in nodes:
        i, e = node_idx[n], echelon_of(n)
        if e == 0:
            continue
        gap = supplier_of[i] < 0
        if gap.any():
            n_fallback += int(gap.sum())
            supplier_of[i, gap] = node_idx[min_lane[n]]

    pdc_of_plant = np.array([node_idx[plant_to_pdc[p]] for p in plants],
                            dtype=np.int16)
    plant_share = np.zeros((n_plants, n_skus))
    for r in sourcing.itertuples(index=False):
        if r.plant_id in plant_i and r.sku_id in sku_idx:
            plant_share[plant_i[r.plant_id], sku_idx[r.sku_id]] = \
                r.allocation_share
    sku_family = (sku_master.set_index("sku_id").production_family
                  .reindex(skus).tolist())
    cap_plant = (plant_master.set_index("plant_id")
                 .production_capacity_units_per_week.reindex(plants)
                 .to_numpy(dtype=float))
    cap_family = {(plant_i[r.plant_id], r.production_family):
                  float(r.family_capacity_units_per_week)
                  for r in plant_cap.itertuples(index=False)
                  if r.plant_id in plant_i}

    notes["sourcing"] = {
        "rule": "sku_sourcing_map.csv (input table)",
        "pairs_resolved": resolved,
        "pairs_on_fallback": n_fallback,
    }

    # ---------------------------------------------------------- lead times --
    # Lead times are drawn per order line from each lane's own mean and std in
    # network_lanes.csv. An earlier version resampled the observed
    # actual_lead_days pool instead; measured per lane that pool reproduces
    # these same parameters almost exactly, so the empirical draw bought no
    # fidelity and cost a dependency on simulated output.
    #
    # The committed lead time quoted to the child is the lane's ~90th
    # percentile, which is what puts on-time delivery near 90% with a genuine
    # late tail rather than a coin flip.
    lane_lt, lt_promise = {}, {}
    for r in lanes.itertuples(index=False):
        d = node_idx.get(r.destination_node_id)
        if d is None:
            continue
        key = (node_idx.get(r.origin_node_id, -1), d)
        lane_lt[key] = (r.lead_time_mean_days, r.lead_time_std_days)
        lt_promise[key] = (r.lead_time_mean_days
                           + PROMISE_Z * r.lead_time_std_days)

    cum_lt = _cumulative_lead_times(nodes, node_idx, supplier_of, lane_lt)

    # -------------------------------------------------------------- policy --
    policy, opening, active, notes_pol = _load_policy(
        nodes, skus, node_idx, sku_idx, wk_dates[0], policy_csv, state_csv,
        periods=periods)
    notes.update(notes_pol)
    rop, oul, ssk = policy
    opening_stock, op_it, op_eta = opening

    # -------------------------------------------------------------- demand --
    daily_demand, dn = _build_demand(data_dir, mode, periods, week_index,
                                     node_idx, sku_idx, n_nodes, n_skus, T,
                                     rng_demand)
    notes.update(dn)

    # ----------------------------------------------------------- economics --
    # Holding = (storage + carrying% x unit cost) / 52, keyed on
    # (dc_id, subcategory). Reconstructs sku_master's annual holding cost to
    # within $0.02/unit-year and, unlike a flat SKU-level rate, makes holding
    # genuinely cheaper upstream — which is what gives echelon positioning
    # something to trade off.
    sm = sku_master.set_index("sku_id")
    subcat = sm.subcategory.reindex(skus).to_numpy()
    unit_cost = sm.unit_cost_usd.reindex(skus).to_numpy(dtype=float)
    margin = sm[MARGIN_COLUMN].reindex(skus).to_numpy(dtype=float)
    cp = cost_p.set_index(["dc_id", "subcategory"])
    holding = np.zeros((n_nodes, n_skus))
    for i, n in enumerate(nodes):
        sub = cp.loc[n]
        holding[i] = (sub.storage_cost_per_unit_year_usd.reindex(subcat)
                      .to_numpy()
                      + unit_cost * sub.inventory_carrying_pct_annual
                      .reindex(subcat).to_numpy() / 100.0) / 52.0

    logistics = np.zeros((n_nodes, n_nodes))
    for r in lanes.itertuples(index=False):
        o, d = node_idx.get(r.origin_node_id), node_idx.get(
            r.destination_node_id)
        if o is not None and d is not None:
            logistics[o, d] = r.logistics_cost_per_unit_usd

    inp = TwinInputs(
        nodes=nodes, skus=skus, plants=plants, periods=periods,
        week_start_dates=wk_dates, week_index=week_index,
        node_idx=node_idx, sku_idx=sku_idx, echelon=echelon,
        node_type=node_type, supplier_of=supplier_of,
        pdc_of_plant=pdc_of_plant, plant_share=plant_share,
        cap_plant=cap_plant, cap_family=cap_family, sku_family=sku_family,
        reorder_point=rop, order_up_to=oul, safety_stock=ssk, active=active,
        opening_stock=opening_stock, opening_in_transit=op_it,
        opening_in_transit_eta_day=op_eta,
        daily_demand=daily_demand, lost_sale_node=lost_sale_node,
        lane_lt=lane_lt, lt_promise=lt_promise,
        cum_lt=cum_lt,
        holding_per_unit_week=holding, margin_per_unit=margin,
        unit_cost=unit_cost, logistics_cost=logistics,
        sku_category=sm.category.reindex(skus).tolist(), horizon_days=T,
    )
    notes.update({
        "margin_basis": MARGIN_COLUMN, "n_nodes": n_nodes, "n_skus": n_skus,
        "n_plants": n_plants, "n_active_pairs": int(active.sum()),
        "horizon_weeks": n_weeks, "horizon_days": T,
        "first_period": periods[0], "last_period": periods[-1],
    })
    return inp, notes, rng_lt, rng_prod


def _cumulative_lead_times(nodes, node_idx, supplier_of, lane_lt):
    """Volume-agnostic plant-to-node cumulative (mean, std) days, for
    reporting parity with the dataset's cum_lead_time_* columns."""
    out = {}
    for n in nodes:
        i, e = node_idx[n], echelon_of(n)
        if e == 0:
            out[i] = lane_lt.get((-1, i), (0.5, 0.07))
            continue
        sup = supplier_of[i]
        parents = sup[sup >= 0]
        if len(parents) == 0:
            out[i] = (3.0, 1.0)
            continue
        mu, var = [], []
        for p in np.unique(parents):
            m, s = lane_lt.get((int(p), i), (3.0, 1.0))
            pm, ps = out.get(int(p), (0.5, 0.07))
            mu.append(m + pm)
            var.append(s ** 2 + ps ** 2)
        out[i] = (float(np.mean(mu)), float(np.sqrt(np.mean(var))))
    return out


# Policy and opening state are MODEL ARTEFACTS, not source data, so they
# live in output datasets/ and are passed in explicitly. `input datasets/`
# holds only the given world and is never written to by the twin.
POLICY_COLUMNS_REQUIRED = True

POLICY_COLUMNS = ["dc_id", "sku_id", "safety_stock", "reorder_point",
                  "order_up_to_level"]


def _load_policy(nodes, skus, node_idx, sku_idx, w0_date,
                 policy_csv, state_csv, periods=None):
    """Load the policy and the opening state from two SEPARATE files.

    Policy is a standalone table — `dc_id, sku_id, safety_stock,
    reorder_point, order_up_to_level` — and nothing else in it matters. That
    is deliberate: an optimizer's output is a drop-in replacement for this one
    file, either by overwriting it or by pointing `--policy` at it. Any extra
    columns are ignored, so a richer optimizer output can be passed verbatim.

    EXCEPT one: an optional `period` column switches the policy to
    time-varying — one row set per (period, dc_id, sku_id) instead of one
    flat table for the whole run, for an optimizer whose premise is
    periodic recompute (e.g. MEIO Weekly) rather than one static annual
    policy. `reorder_point`/`order_up_to`/`safety_stock` become
    [n_weeks, n_nodes, n_skus] instead of [n_nodes, n_skus] in that case; a
    (node, sku) with no row for a given week keeps its most recently seen
    week's values rather than dropping to zero. Without a `period` column,
    behavior is identical to before this was added — every existing
    single-policy call site is unaffected.

    Opening state (stock and in-transit) comes from a different file and is
    never taken from the policy table. Two runs of the same scenario therefore
    start from an identical physical position whatever policy is under test,
    and the difference between them measures the decision rather than the
    starting point.

    `opening_inventory_2026.csv` also carries policy columns, because it is a
    published dataset table and downstream consumers expect them. The twin
    ignores those columns and reads the standalone file instead, so there is
    exactly one source of truth for what a run executed.
    """
    n_nodes, n_skus = len(nodes), len(skus)
    notes = {}

    if policy_csv is None:
        raise ValueError(
            "a policy file is required: pass --policy <csv> with columns "
            + ", ".join(POLICY_COLUMNS))
    pol_path = Path(policy_csv)
    pol = pd.read_csv(pol_path)
    missing = [c for c in POLICY_COLUMNS if c not in pol.columns]
    if missing:
        raise ValueError(
            f"policy file {pol_path} is missing required columns {missing}; "
            f"expected at least {POLICY_COLUMNS}")
    notes["policy_source"] = str(pol_path)
    notes["policy_rows"] = len(pol)

    time_varying = "period" in pol.columns and periods is not None
    notes["policy_time_varying"] = time_varying
    active = np.zeros((n_nodes, n_skus), dtype=bool)

    if time_varying:
        n_weeks = len(periods)
        period_pos = {p: w for w, p in enumerate(periods)}
        rop, oul, ssk = (np.zeros((n_weeks, n_nodes, n_skus)) for _ in range(3))
        seen = np.zeros((n_weeks, n_nodes, n_skus), dtype=bool)
        unmatched = 0
        for r in pol[["period"] + POLICY_COLUMNS].itertuples(index=False):
            if r.sku_id not in sku_idx or r.dc_id not in node_idx:
                continue
            w = period_pos.get(r.period)
            if w is None:
                unmatched += 1
                continue
            i, j = node_idx[r.dc_id], sku_idx[r.sku_id]
            active[i, j] = True
            seen[w, i, j] = True
            ssk[w, i, j] = r.safety_stock
            rop[w, i, j] = r.reorder_point
            oul[w, i, j] = r.order_up_to_level
        if unmatched:
            notes["policy_rows_unmatched_period"] = unmatched
        # forward-fill: an active (node, sku) with no row this week keeps
        # last week's policy rather than silently dropping to zero
        for w in range(1, n_weeks):
            carry = active & ~seen[w]
            ssk[w] = np.where(carry, ssk[w - 1], ssk[w])
            rop[w] = np.where(carry, rop[w - 1], rop[w])
            oul[w] = np.where(carry, oul[w - 1], oul[w])
            seen[w] |= seen[w - 1]
    else:
        rop, oul, ssk = (np.zeros((n_nodes, n_skus)) for _ in range(3))
        for r in pol[POLICY_COLUMNS].itertuples(index=False):
            if r.sku_id not in sku_idx or r.dc_id not in node_idx:
                continue
            i, j = node_idx[r.dc_id], sku_idx[r.sku_id]
            active[i, j] = True
            ssk[i, j] = r.safety_stock
            rop[i, j] = r.reorder_point
            oul[i, j] = r.order_up_to_level

    opening_stock, op_it = (np.zeros((n_nodes, n_skus)) for _ in range(2))
    op_eta = np.zeros((n_nodes, n_skus), dtype=int)
    if state_csv is not None:
        w0 = pd.Timestamp(w0_date)
        state = pd.read_csv(state_csv)
        for r in state.itertuples(index=False):
            if r.sku_id not in sku_idx or r.dc_id not in node_idx:
                continue
            i, j = node_idx[r.dc_id], sku_idx[r.sku_id]
            opening_stock[i, j] = getattr(r, "opening_stock", 0.0) or 0.0
            it = getattr(r, "in_transit_qty", 0) or 0
            if it > 0:
                op_it[i, j] = it
                eta = getattr(r, "in_transit_eta_week_start", None)
                op_eta[i, j] = (0 if eta is None or pd.isna(eta)
                                else max(0, (pd.Timestamp(eta) - w0).days))
        notes["state_source"] = str(state_csv)
    else:
        notes["state_source"] = "cold start (zero stock)"

    return (rop, oul, ssk), (opening_stock, op_it, op_eta), active, notes


def _policy_at(inp, week_idx: int):
    """(safety_stock, reorder_point, order_up_to) 2D [n_nodes, n_skus]
    slices for week_idx. Transparently handles both a static policy
    ([n_nodes, n_skus], the common case) and a time-varying one
    ([n_weeks, n_nodes, n_skus], see _load_policy's `period` column
    handling) so every call site looks the same either way."""
    if inp.safety_stock.ndim == 3:
        w = min(week_idx, inp.safety_stock.shape[0] - 1)
        return inp.safety_stock[w], inp.reorder_point[w], inp.order_up_to[w]
    return inp.safety_stock, inp.reorder_point, inp.order_up_to


def _build_demand(data_dir, mode, periods, week_index, node_idx, sku_idx,
                  n_nodes, n_skus, T, rng):
    """Realised daily external demand tensor [T, n_nodes, n_skus]."""
    notes = {}
    daily = np.zeros((T, n_nodes, n_skus), dtype=np.int32)

    if mode == "history":
        # Replay the orders that actually happened, at their delivery day.
        o = pd.read_csv(data_dir / "orders.csv",
                        usecols=["delivery_date", "period", "sku_id", "dc_id",
                                 "order_qty"])
        o = o[o.period.isin(set(periods))]
        day0 = pd.Timestamp(week_monday(week_index[0]))
        dday = (pd.to_datetime(o.delivery_date) - day0).dt.days.to_numpy()
        ni = o.dc_id.map(node_idx).to_numpy()
        si = o.sku_id.map(sku_idx).to_numpy()
        # Orders placed near the end of the window can be scheduled to land
        # after it. Dropping them would silently lose demand and the replay
        # would no longer reconcile to orders.csv, so they are clamped onto
        # the last day instead.
        dday = np.clip(dday, 0, T - 1)
        keep = (~pd.isna(ni)) & (~pd.isna(si))
        np.add.at(daily, (dday[keep], ni[keep].astype(int),
                          si[keep].astype(int)),
                  o.order_qty.to_numpy()[keep].astype(np.int32))
        notes["demand_basis"] = "orders.csv replay at delivery_date"
        notes["realised_demand_units"] = float(daily.sum())
        return daily, notes

    # forward: forecast x a forecast-error ratio resampled from that SKU-DC's
    # own in-sample history. Weeks are drawn with probability proportional to
    # forecast volume so the aggregate bias reproduced is the volume-weighted
    # +8.5% the forecaster actually has; an unweighted draw over-weights
    # small-denominator weeks and inflates it to +14.7%.
    dfc = pd.read_csv(data_dir / "demand_forecast.csv")
    n_weeks = len(periods)
    pidx = {p: k for k, p in enumerate(periods)}
    fwd = (dfc[dfc.period.isin(set(periods))]
           .groupby(["period", "dc_id", "sku_id"], as_index=False)
           .forecast_qty.sum())
    fcst = np.zeros((n_nodes, n_skus, n_weeks))
    ni = fwd.dc_id.map(node_idx).to_numpy()
    si = fwd.sku_id.map(sku_idx).to_numpy()
    keep = (~pd.isna(ni)) & (~pd.isna(si))
    np.add.at(fcst, (ni[keep].astype(int), si[keep].astype(int),
                     fwd.period.map(pidx).to_numpy()[keep]),
              fwd.forecast_qty.to_numpy()[keep])

    ins = (dfc[dfc.horizon == "in_sample"]
           .groupby(["period", "dc_id", "sku_id"], as_index=False)
           .agg(forecast_qty=("forecast_qty", "sum"),
                actual_qty=("actual_qty", "sum")))
    v = ins[(ins.forecast_qty > 0) & ins.actual_qty.notna()].copy()
    v["ratio"] = (v.actual_qty / v.forecast_qty).clip(upper=10.0)

    max_obs = int(v.groupby(["dc_id", "sku_id"]).size().max())
    pool = np.ones((n_nodes, n_skus, max_obs))
    wts = np.zeros((n_nodes, n_skus, max_obs))
    for (dc, sk), g in v.groupby(["dc_id", "sku_id"]):
        if dc in node_idx and sk in sku_idx:
            i, j, k = node_idx[dc], sku_idx[sk], len(g)
            pool[i, j, :k] = g.ratio.to_numpy()
            wts[i, j, :k] = g.forecast_qty.to_numpy()

    tot = wts.sum(axis=2, keepdims=True)
    has = tot[:, :, 0] > 0
    cdf = np.cumsum(np.divide(wts, np.where(tot > 0, tot, 1.0)), axis=2)
    draw = rng.random((n_nodes, n_skus, n_weeks))
    pick = np.minimum((draw[:, :, :, None] > cdf[:, :, None, :]).sum(axis=3),
                      max_obs - 1)
    ratios = np.where(has[:, :, None],
                      np.take_along_axis(pool, pick, axis=2), 1.0)
    weekly = np.maximum(0.0, np.round(fcst * ratios))

    for w in range(n_weeks):
        remaining = weekly[:, :, w].astype(np.int64)
        for d in range(DAYS_PER_WEEK):
            take = (remaining if d == DAYS_PER_WEEK - 1
                    else rng.binomial(remaining, 1.0 / (DAYS_PER_WEEK - d)))
            daily[w * DAYS_PER_WEEK + d] = take
            remaining -= take

    notes.update({
        "demand_basis": "forecast_qty x volume-weighted resampled "
                        "forecast-error ratio",
        "forecast_units": float(fcst.sum()),
        "realised_demand_units": float(weekly.sum()),
        "realised_over_forecast": round(
            float(weekly.sum() / max(fcst.sum(), 1)), 4),
    })
    return daily, notes


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SupplyBrainTwin:

    def __init__(self, inp: TwinInputs, rng_lt, rng_prod,
                 reserve_own_demand: bool = True):
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

        for i, j in np.argwhere(inp.opening_in_transit > 0):
            d = int(np.clip(inp.opening_in_transit_eta_day[i, j], 0,
                            T + PIPELINE_PAD - 1))
            self.pipeline[d, i, j] += inp.opening_in_transit[i, j]
            self.on_order[i, j] += inp.opening_in_transit[i, j]

        # children served by each parent, for pro-rata allocation
        self._children: Dict[int, List[int]] = {}
        for dest in range(n):
            for origin in np.unique(inp.supplier_of[dest]):
                if origin >= 0:
                    self._children.setdefault(int(origin), [])
                    if dest not in self._children[int(origin)]:
                        self._children[int(origin)].append(dest)

        # plant-side state
        self.demand_ema = np.zeros((n, k))     # PDC outbound signal
        self.prod_rows: List[tuple] = []

        self._reset_week()
        self.weekly_rows: List[np.ndarray] = []
        self.receipt_rows: List[tuple] = []

        self.cum_holding = np.zeros((n, k))
        self.cum_stockout = np.zeros((n, k))
        self.cum_transport = np.zeros((n, k))
        self._seq = 0

    # -- weekly accumulators -------------------------------------------------

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

    # -- lead times ----------------------------------------------------------

    def _lead_times(self, origin: int, dest: int, n: int) -> np.ndarray:
        """One realised transit time per order line, drawn from the lane's own
        parameters. Each receipt arrives on its own draw, not the average."""
        m, sd = self.inp.lane_lt.get((origin, dest), (3.0, 1.0))
        return np.maximum(0.5, self.rng_lt.normal(m, max(sd, 0.05), size=n))

    # -- step ----------------------------------------------------------------

    def step(self, day: int) -> None:
        inp = self.inp

        # 1) receive
        arr = self.pipeline[day]
        if arr.any():
            self.on_hand += arr
            self.on_order = np.maximum(0.0, self.on_order - arr)
            self.wk_receipts += arr
            self.pipeline[day] = 0.0

        # 2) clear backlog (PDC/RDC backorder; CDC lost the sale already)
        if (self.backlog > 0).any():
            use = np.minimum(self.backlog, self.on_hand)
            self.on_hand -= use
            self.backlog -= use

        # 3) external customer demand
        dq = inp.daily_demand[day].astype(float)
        if dq.any():
            served = np.minimum(dq, self.on_hand)
            short = dq - served
            self.on_hand -= served
            self.backlog += np.where(inp.lost_sale_node[:, None], 0.0, short)
            self.wk_dem_cust += dq
            self.wk_unmet_cust += short
            lost = short * inp.margin_per_unit[None, :]
            self.wk_lost_margin += np.where(inp.lost_sale_node[:, None],
                                            lost, 0.0)
            self.cum_stockout += lost

        # 4) weekly review: children order, plants plan
        if day % DAYS_PER_WEEK == REVIEW_DAY:
            self._review(day)
            self._produce(day)

        # 5) work off the open order book (every day, not only review day)
        self._fulfil(day)

        # 6) snapshot
        self.wk_onhand_days += self.on_hand

    # -- replenishment -------------------------------------------------------

    def _review(self, day: int) -> None:
        """Weekly (R,s,S) review at RDC and CDC.

        Anything still open from last week was already cancelled and booked as
        internal shortfall when that week closed, so the book starts empty.
        """
        inp = self.inp
        ip = self.on_hand - self.backlog + self.on_order
        pull = inp.echelon > 0                      # PDCs are pushed, not pulled
        _ssk, rop, oul = _policy_at(inp, day // DAYS_PER_WEEK)
        need = (ip <= rop) & inp.active & pull[:, None]
        want = np.where(need, np.maximum(0.0, oul - ip), 0.0)
        self.open_orders = want
        self.wk_dem_rep += self._at_parent(want)

    def _at_parent(self, qty: np.ndarray) -> np.ndarray:
        """Re-index a [dest, sku] quantity onto the parent that must serve it,
        so internal service is measured where it is actually delivered."""
        out = np.zeros_like(qty)
        sup = self.inp.supplier_of
        for dest in range(self.n_nodes):
            row = qty[dest]
            if not row.any():
                continue
            m = (sup[dest] >= 0) & (row > 0)
            if m.any():
                np.add.at(out, (sup[dest][m], np.where(m)[0]), row[m])
        return out

    def _fulfil(self, day: int) -> None:
        """Ship against the open order book, upstream echelon first.

        Fulfilment runs every day. A parent picks and ships across the week as
        stock becomes available; if every node both ordered and shipped on the
        same morning, each parent would be drained at exactly its weekly low
        point and the network would starve for reasons that are an artefact of
        the time step rather than the policy.

        When a parent cannot cover everything its children have asked for, the
        remaining stock is split PRO RATA across them, per the dataset's stated
        rule. Serving children in node-index order instead would systematically
        starve whichever customer DC happens to sort last under each regional
        DC — measured at ~10pp of internal fill on the RDC->CDC leg.
        """
        inp = self.inp
        for ech in (0, 1):                       # origins: PDC then RDC
            for origin in np.where(inp.echelon == ech)[0]:
                kids = self._children.get(int(origin))
                if not kids:
                    continue
                req = {}
                for dest in kids:
                    w = self.open_orders[dest]
                    r = np.where((inp.supplier_of[dest] == origin) & (w > 0),
                                 w, 0.0)
                    if r.any():
                        req[dest] = r
                if not req:
                    continue

                total = np.sum(list(req.values()), axis=0)
                avail = self._available(int(origin), day)
                with np.errstate(divide="ignore", invalid="ignore"):
                    ratio = np.where(total > 0,
                                     np.minimum(1.0, avail / total), 0.0)
                for dest, r in req.items():
                    grant = np.floor(r * ratio)
                    if grant.sum() <= 0:
                        continue
                    self.on_hand[origin] -= grant
                    self.open_orders[dest] -= grant
                    self._ship(day, int(origin), int(dest), grant, r)

    def _available(self, origin: int, day: int) -> np.ndarray:
        """Stock the parent may release downstream today.

        A regional DC serves its own bulk customers before its customer DCs.
        Realised demand is already served daily in step 3, so the only extra
        protection the parent needs is its own safety stock — it releases
        whatever sits above that line.

        Using safety stock rather than "a week of own demand" matters twice
        over. Reserving a full week double-counts the protection and strangles
        the RDC->CDC leg: RDC direct demand is ~4x total CDC demand, so the
        reserve blocks release even when the RDC is sitting on stock (measured:
        internal fill pinned at 70% with no correlation to on-hand). And it
        puts the allocation rule under the policy table, so raising an RDC's
        safety stock both buffers its own demand and holds stock back from the
        echelon below — which is precisely the trade-off MEIO 2.0 exists to
        explore.
        """
        avail = self.on_hand[origin].copy()
        if self.reserve_own_demand:
            ssk, _rop, _oul = _policy_at(self.inp, day // DAYS_PER_WEEK)
            avail = np.maximum(0.0, avail - ssk[origin])
        return avail

    def _ship(self, day: int, origin: int, dest: int, qty: np.ndarray,
              req: Optional[np.ndarray] = None) -> None:
        """Dispatch a per-SKU vector on a lane. Each order line gets its own
        realised lead time, resampled from that lane's observed receipts."""
        inp = self.inp
        idx = np.where(qty > 0)[0]
        if len(idx) == 0:
            return
        lts = self._lead_times(origin, dest, len(idx))
        arrd = np.minimum(day + np.maximum(1, np.ceil(lts)).astype(int),
                          inp.horizon_days + PIPELINE_PAD - 1)
        promise = inp.lt_promise.get((origin, dest),
                                     float(np.mean(lts) * 1.4))

        for k_, s in enumerate(idx):
            self.pipeline[arrd[k_], dest, s] += qty[s]
            self.on_order[dest, s] += qty[s]

        if origin >= 0:
            self.wk_ship[origin] += qty
            self.cum_transport[dest] += qty * inp.logistics_cost[origin, dest]

        o_name = "PLANT" if origin < 0 else inp.nodes[origin]
        wk = day // DAYS_PER_WEEK
        for k_, s in enumerate(idx):
            short = 0.0 if req is None else float(req[s] - qty[s])
            self._seq += 1
            self.receipt_rows.append((
                f"RCP{self._seq:07d}", inp.periods[wk],
                inp.week_start_dates[wk], inp.skus[s], inp.nodes[dest],
                o_name, float(qty[s] + short), float(qty[s]),
                round(promise, 2), round(float(lts[k_]), 2),
                int(day), int(arrd[k_]),
                int(lts[k_] <= promise), short,
            ))

    # -- production ----------------------------------------------------------

    def _produce(self, day: int) -> None:
        """Plants build to a rolling plan and push finished goods to their
        plant DC.

        Plan = the demand signal the plant DC is seeing, plus whatever restores
        about FG_COVER_WEEKS of finished-goods cover. The plan is then clipped
        by production-family line capacity and by plant-wide capacity, and the
        executed quantity is the plan scaled by schedule adherence. Capacity is
        what makes peak weeks compete for line time; adherence is the
        supply-side variability that is distinct from lead time.
        """
        inp = self.inp
        wk = day // DAYS_PER_WEEK

        # demand signal at each plant DC = internal orders placed on it
        signal = self._at_parent(self.open_orders)
        self.demand_ema = (DEMAND_EMA_ALPHA * signal
                           + (1 - DEMAND_EMA_ALPHA) * self.demand_ema)

        for p in range(len(inp.plants)):
            pdc = int(inp.pdc_of_plant[p])
            share = inp.plant_share[p]
            made = share > 0
            if not made.any():
                continue

            sig = self.demand_ema[pdc] * share
            pos = (self.on_hand[pdc] + self.on_order[pdc]) * share
            plan_unc = np.where(made,
                                np.maximum(0.0, sig + np.maximum(
                                    0.0, FG_COVER_WEEKS * sig - pos)), 0.0)
            planned = plan_unc.copy()

            # family line capacity
            for fam in set(np.array(inp.sku_family)[made]):
                cap = inp.cap_family.get((p, fam))
                if cap is None:
                    continue
                m = made & (np.array(inp.sku_family) == fam)
                tot = planned[m].sum()
                if tot > cap:
                    planned[m] *= cap / tot

            # plant-wide capacity
            tot = planned.sum()
            capped = tot > inp.cap_plant[p]
            if capped and tot > 0:
                planned *= inp.cap_plant[p] / tot

            adh = self.rng_prod.uniform(*ADHERENCE_RANGE)
            actual = np.floor(planned * adh)

            idx = np.where(actual > 0)[0]
            if len(idx):
                self._ship(day, -1, pdc, actual)
            for s in np.where(plan_unc > 0)[0]:
                self.prod_rows.append((
                    inp.periods[wk], inp.week_start_dates[wk], inp.plants[p],
                    inp.skus[s], inp.sku_family[s],
                    bool(share[s] >= inp.plant_share[:, s].max()),
                    float(np.round(plan_unc[s])), float(np.round(planned[s])),
                    float(actual[s]), round(adh, 4), int(capped),
                ))

    # -- run -----------------------------------------------------------------

    def run(self, verbose: bool = True):
        T = self.inp.horizon_days
        n_weeks = T // DAYS_PER_WEEK
        for day in range(T):
            self.step(day)
            if (day + 1) % DAYS_PER_WEEK == 0:
                w = day // DAYS_PER_WEEK
                self._close_week(w)
                if verbose and (w % 20 == 0 or w == n_weeks - 1):
                    d = self.wk_dem_cust.sum()
                    print(f"  {self.inp.periods[w]}  "
                          f"on-hand {self.on_hand.sum():>11,.0f} u",
                          flush=True)
        return self._collect()

    def _close_week(self, w: int) -> None:
        # An internal order the parent could not cover is cancelled and the
        # child re-orders at the next review, so an open order lives exactly
        # one week. Booking the residual here rather than at the next review
        # keeps it in the week the demand was raised, which is what makes
        # demand_qty = shipments + unmet_qty hold on every published row and
        # stops the final week's orders going unresolved.
        inp = self.inp
        self.wk_unmet_rep += self._at_parent(self.open_orders)
        self.open_orders[:] = 0.0
        avg = self.wk_onhand_days / DAYS_PER_WEEK
        hold = avg * inp.holding_per_unit_week
        self.cum_holding += hold

        ssk_w, rop_w, oul_w = _policy_at(inp, w)
        act = np.argwhere(inp.active)
        rec = np.empty((len(act), 21), dtype=object)
        for t, (n, s) in enumerate(act):
            dem = self.wk_dem_cust[n, s] + self.wk_dem_rep[n, s]
            unm = self.wk_unmet_cust[n, s] + self.wk_unmet_rep[n, s]
            shipped = dem - unm
            cm, cs = inp.cum_lt[n]
            lm, ls_ = inp.lane_lt.get(
                (int(inp.supplier_of[n, s]), int(n)), (cm, cs))
            rec[t] = (
                inp.periods[w], inp.week_start_dates[w], inp.skus[s],
                inp.nodes[n], self.wk_opening[n, s], self.wk_receipts[n, s],
                dem, self.wk_dem_cust[n, s], self.wk_dem_rep[n, s],
                shipped, unm, self.wk_unmet_cust[n, s],
                self.wk_unmet_rep[n, s], self.wk_lost_margin[n, s],
                "lost_sale" if inp.lost_sale_node[n] else "backorder",
                self.on_hand[n, s], ssk_w[n, s],
                rop_w[n, s], oul_w[n, s],
                avg[n, s], hold[n, s],
            )
        self.weekly_rows.append(rec)
        self._reset_week()

    def _collect(self):
        cols = ["period", "week_start_date", "sku_id", "dc_id",
                "opening_stock", "receipts", "demand_qty",
                "demand_customer_qty", "demand_replen_qty", "shipments",
                "unmet_qty", "unmet_customer_qty", "unmet_replen_qty",
                "lost_margin_usd", "fulfilment_type", "closing_stock",
                "safety_stock", "reorder_point", "order_up_to_level",
                "avg_on_hand", "holding_cost_usd"]
        inv = pd.DataFrame(np.vstack(self.weekly_rows), columns=cols)
        num = [c for c in cols if c not in
               ("period", "week_start_date", "sku_id", "dc_id",
                "fulfilment_type")]
        inv[num] = inv[num].astype(float)

        # lane lead-time columns, for parity with the dataset schema
        lt_rows = []
        for n, node in enumerate(self.inp.nodes):
            cm, cs = self.inp.cum_lt[n]
            for s, sku in enumerate(self.inp.skus):
                if self.inp.active[n, s]:
                    m, sd = self.inp.lane_lt.get(
                        (int(self.inp.supplier_of[n, s]), n), (cm, cs))
                    lt_rows.append((node, sku, m, sd, cm, cs))
        lt = pd.DataFrame(lt_rows, columns=[
            "dc_id", "sku_id", "lead_time_mean_days", "lead_time_std_days",
            "cum_lead_time_mean_days", "cum_lead_time_std_days"])
        inv = inv.merge(lt, on=["dc_id", "sku_id"], how="left")

        rcpt = pd.DataFrame(self.receipt_rows, columns=[
            "receipt_id", "period", "order_week_start", "sku_id", "dc_id",
            "origin_node_id", "qty_ordered", "qty_received",
            "promised_lead_days", "actual_lead_days", "ship_day",
            "arrival_day", "on_time_flag", "short_qty"])

        prod = pd.DataFrame(self.prod_rows, columns=[
            "period", "week_start_date", "plant_id", "sku_id",
            "production_family", "is_primary", "plan_uncapped_qty",
            "planned_qty", "actual_qty", "adherence_pct",
            "capacity_limited_flag"])

        return inv, rcpt, prod


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def summarise(inv, twin, inp):
    svc_sku = inv.groupby("sku_id", as_index=False).agg(
        total_demand=("demand_customer_qty", "sum"),
        unmet=("unmet_customer_qty", "sum"),
        replen_demand=("demand_replen_qty", "sum"),
        replen_unmet=("unmet_replen_qty", "sum"),
        avg_on_hand=("avg_on_hand", "mean"),
        holding_cost_usd=("holding_cost_usd", "sum"))
    svc_sku["served_from_stock"] = svc_sku.total_demand - svc_sku.unmet
    svc_sku["fill_rate_customer"] = np.where(
        svc_sku.total_demand > 0,
        svc_sku.served_from_stock / svc_sku.total_demand, np.nan)
    svc_sku["fill_rate_replen"] = np.where(
        svc_sku.replen_demand > 0,
        1 - svc_sku.replen_unmet / svc_sku.replen_demand, np.nan)
    svc_sku["category"] = svc_sku.sku_id.map(
        dict(zip(inp.skus, inp.sku_category)))

    inv = inv.assign(node_type=inv.dc_id.str[:3].map(NODE_TYPE))
    svc_node = inv.groupby(["dc_id", "node_type"], as_index=False).agg(
        skus=("sku_id", "nunique"),
        total_demand=("demand_customer_qty", "sum"),
        unmet=("unmet_customer_qty", "sum"),
        replen_demand=("demand_replen_qty", "sum"),
        replen_unmet=("unmet_replen_qty", "sum"),
        avg_on_hand=("avg_on_hand", "mean"),
        holding_cost_usd=("holding_cost_usd", "sum"))
    svc_node["fill_rate_customer"] = np.where(
        svc_node.total_demand > 0, 1 - svc_node.unmet / svc_node.total_demand,
        np.nan)
    svc_node["fill_rate_replen"] = np.where(
        svc_node.replen_demand > 0,
        1 - svc_node.replen_unmet / svc_node.replen_demand, np.nan)

    rows = []
    for i, n in enumerate(inp.nodes):
        sub = inv[inv.dc_id == n]
        rows.append({
            "dc_id": n, "node_type": inp.node_type[i],
            "avg_units_on_hand": sub.avg_on_hand.sum()
            / max(sub.period.nunique(), 1),
            "avg_inventory_value_usd": float(
                (sub.groupby("sku_id").avg_on_hand.mean().reindex(inp.skus)
                 .fillna(0).to_numpy() * inp.unit_cost).sum()),
            "holding_cost_usd": float(twin.cum_holding[i].sum()),
            "stockout_cost_usd": float(twin.cum_stockout[i].sum()),
            "transport_cost_usd": float(twin.cum_transport[i].sum()),
        })
    costs = pd.DataFrame(rows)
    costs["total_landed_cost_usd"] = (costs.holding_cost_usd
                                      + costs.stockout_cost_usd
                                      + costs.transport_cost_usd)
    return svc_sku, svc_node, costs.sort_values("total_landed_cost_usd",
                                                ascending=False)


def kpis(inv, twin, inp, costs) -> dict:
    cd, cu = inv.demand_customer_qty.sum(), inv.unmet_customer_qty.sum()
    rd, ru = inv.demand_replen_qty.sum(), inv.unmet_replen_qty.sum()
    inv_val = (inv.groupby(["period", "sku_id"]).avg_on_hand.sum().unstack(
        fill_value=0).reindex(columns=inp.skus, fill_value=0).to_numpy()
        * inp.unit_cost).sum(axis=1)
    # "in full": share of node-SKU-weeks carrying demand that shipped complete.
    # Unit fill rate alone hides partial shipments — a week served 90% counts
    # as 0.9 of a unit fill but is a failed order line to the customer.
    dw = inv[inv.demand_customer_qty > 0]
    in_full = (float((dw.unmet_customer_qty <= 0).mean())
               if len(dw) else None)
    cdc = dw[dw.dc_id.str.startswith("CDC")]
    rdc = dw[dw.dc_id.str.startswith("RDC")]

    return {
        "horizon_weeks": int(inv.period.nunique()),
        "first_period": inp.periods[0], "last_period": inp.periods[-1],
        "customer_demand_units": float(cd),
        "customer_fill_rate": round(float(1 - cu / cd), 4) if cd else None,
        "customer_fill_rate_cdc": round(
            float(1 - cdc.unmet_customer_qty.sum()
                  / cdc.demand_customer_qty.sum()), 4) if len(cdc) else None,
        "customer_fill_rate_rdc": round(
            float(1 - rdc.unmet_customer_qty.sum()
                  / rdc.demand_customer_qty.sum()), 4) if len(rdc) else None,
        "pct_demand_weeks_in_full": round(in_full, 4)
        if in_full is not None else None,
        "internal_replen_fill_rate": round(float(1 - ru / rd), 4)
        if rd else None,
        "avg_units_on_hand_network": round(
            float(inv.groupby("period").avg_on_hand.sum().mean()), 1),
        "avg_inventory_value_usd": round(float(inv_val.mean()), 2),
        "safety_stock_value_usd": round(float((
            (inp.safety_stock.mean(axis=0) if inp.safety_stock.ndim == 3
             else inp.safety_stock) * inp.unit_cost[None, :]).sum()), 2),
        "holding_cost_usd": round(float(twin.cum_holding.sum()), 2),
        "stockout_cost_usd": round(float(twin.cum_stockout.sum()), 2),
        "transport_cost_usd": round(float(twin.cum_transport.sum()), 2),
        "total_landed_cost_usd": round(
            float(costs.total_landed_cost_usd.sum()), 2),
        "pct_weeks_at_zero_stock": round(
            float((inv.closing_stock <= 0).mean()), 4),
        "margin_basis": MARGIN_COLUMN,
    }


ASSUMPTIONS = [
    "Plants build to a rolling plan (demand signal + restore ~1.5 weeks of "
    "finished-goods cover), clipped by production-family line capacity then "
    "plant-wide capacity; executed quantity = plan x schedule adherence "
    "drawn uniformly in [0.90, 1.00].",
    "Plant DCs are PUSHED stock by production; only RDCs and CDCs pull on a "
    "weekly (R,s,S) review with R = 1 week.",
    "Lead times are drawn per order line from each lane's own mean and std "
    "in network_lanes.csv, so every receipt arrives on its own realised "
    "transit rather than the average. The committed lead time quoted to the "
    "child is that lane's ~90th percentile.",
    "CDC shortfalls are lost sales; PDC/RDC shortfalls backorder, following "
    "the fulfilment_type split in the dataset, which is clean by node type.",
    "An internal replenishment order the parent cannot cover is cancelled, "
    "not backlogged; the child re-orders at the next weekly review.",
    "A parent holds back its own expected demand for the remainder of the "
    "review period before releasing stock downstream, implementing "
    "'customer orders served first'.",
    "Routing comes from sku_sourcing_map.csv, an input table derived from "
    "network structure: each RDC draws a SKU from the nearest plant DC whose "
    "plant is qualified to make it, and each CDC from its min-lead-time "
    "inbound RDC. It is not mined from simulated receipts.",
    "Holding cost = (storage_cost_per_unit_year + carrying_pct x unit_cost) "
    "/ 52, keyed on (dc_id, subcategory).",
    "No shelf life, obsolescence or write-offs; no warehouse or lane "
    "capacity limits. Production capacity DOES bind.",
    "Demand, lead-time and production RNG streams are independent, so two "
    "runs at the same seed differ only by the policy under test.",
]


def write_run(out: Path, inv, rcpt, prod, twin, inp, notes, args, t0,
              scenario_config: Optional[dict] = None):
    out.mkdir(parents=True, exist_ok=True)
    svc_sku, svc_node, costs = summarise(inv, twin, inp)
    k = kpis(inv, twin, inp, costs)

    inv.to_csv(out / "inventory.csv", index=False)
    rcpt.to_csv(out / "replenishment_receipts.csv", index=False)
    prod.to_csv(out / "production.csv", index=False)
    svc_sku.to_csv(out / "service_summary.csv", index=False)
    svc_node.to_csv(out / "service_by_node.csv", index=False)
    costs.to_csv(out / "cost_summary.csv", index=False)
    (out / "run_kpis.json").write_text(json.dumps(k, indent=2))

    # For a time-varying policy this is a first-week snapshot, not the full
    # picture -- the run's actual policy input (with its `period` column)
    # is already recorded verbatim at notes["policy_source"] in
    # run_manifest.json below.
    ssk0, rop0, oul0 = _policy_at(inp, 0)
    pol = pd.DataFrame([
        (inp.nodes[i], inp.skus[j], ssk0[i, j], rop0[i, j], oul0[i, j])
        for i, j in np.argwhere(inp.active)],
        columns=["dc_id", "sku_id", "safety_stock", "reorder_point",
                 "order_up_to_level"])
    pol.to_csv(out / "policy.csv", index=False)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_name": args.run_name, "label": args.label, "mode": args.mode,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seed": args.seed, "policy_override": args.policy,
        "margin_basis": MARGIN_COLUMN,
        "runtime_seconds": round(time.time() - t0, 1),
        "assumptions": ASSUMPTIONS,
        "build_notes": _jsonable(notes),
    }
    if scenario_config is not None:
        manifest["scenario"] = scenario_config
        (out / "scenario_config.json").write_text(
            json.dumps(scenario_config, indent=2) + "\n", encoding="utf-8")
    (out / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return k, svc_node, costs


def _jsonable(o):
    if isinstance(o, dict):
        return {str(kk): _jsonable(v) for kk, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.integer, np.floating)):
        return o.item()
    return o


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

def write_output_documentation(out_root: Path) -> int:
    """Emit output datasets/OUTPUT_DOCUMENTATION.md.

    Covers what this engine does and the schema of everything a run produces.
    The given data is documented separately by the generator, in
    `input datasets/DATASET_DOCUMENTATION.md`; this is the other half.

    Rewritten on every run so it cannot drift from the code, and so the list of
    run folders below is always current.
    """
    runs = []
    if out_root.is_dir():
        for d in sorted(x for x in out_root.iterdir() if x.is_dir()
                        and not x.name.startswith("_")):
            man = d / "run_manifest.json"
            label = mode = seed = "?"
            if man.exists():
                try:
                    m = json.loads(man.read_text())
                    label, mode = m.get("label") or d.name, m.get("mode", "?")
                    seed = m.get("seed", "?")
                except Exception:
                    pass
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            runs.append((d.name, mode, seed, label, size / 1e6))
    run_rows = "\n".join(
        f"| `{n}/` | {mo} | {sd} | {lb} | {sz:.2f} MB |" for n, mo, sd, lb, sz in runs
    ) or "| _(no runs yet)_ | | | | |"

    assum = "\n".join(f"{i}. {a}" for i, a in enumerate(ASSUMPTIONS, 1))

    doc = f"""# Supply Brain — output datasets

*Auto-generated {time.strftime('%Y-%m-%d %H:%M')} by
`models/twin/digital_twin.py`. Do not edit by hand.*

## Scope

This document covers **`output datasets/` only** — the forward view. Everything
here is produced by the digital twin: the position the planning horizon opens
on, the policy each run executed, and the inventory, production and service
that policy caused.

It does **not** cover the given data. That is documented in
`input datasets/DATASET_DOCUMENTATION.md`, written by the generator.
`SCHEMA.md` at the project root explains how the two folders divide.

The dividing line is **time**, not authorship: the twin also writes the
*historical* inventory record, and that lives in `input datasets/` because it
is the past. Only forward-looking output lands here.

---

## Part 1 — What the twin is

A discrete-event simulation of the four-echelon network:

```
Plant --0.5d--> Plant DC --> Regional DC --> Customer DC
```

The plant is production-only and holds no stock. Plant DCs are the first
stock-holding echelon and are **pushed** finished goods by production, not
pulled by a reorder point. Regional DCs carry both their own direct
bulk-customer demand and the demand of the customer DCs beneath them. Regional
and customer DCs **pull** on a weekly reorder-point review.

It is the project's only physics engine. The same code generates the historical
record and scores candidate policies, so the history a policy is measured
against and the outcome it is scored on come from identical mechanics. Two
separate implementations would disagree by a few points of fill rate, and every
optimization result would then carry an unanswerable question about whether the
gain was real.

Design follows ISOMORPH (github.com/tuhinsahai/ISOMORPH): one step is one day,
a fixed event order inside each step, an explicit in-transit pipeline keyed by
arrival day, and a run directory that describes itself.

### What happens in a day

Every simulated day runs the same six steps in the same order:

1. **Receive** — shipments whose arrival day is today land and become on-hand.
2. **Clear backlog** — outstanding backorders are filled from what just arrived.
3. **Serve customer demand** — from stock on hand. A shortfall at a customer DC
   is a **lost sale** (margin forgone); at a plant or regional DC it
   **backorders**.
4. **Review** (weekly) — every pulling node compares its inventory position
   against its reorder point and places an order up to its order-up-to level.
   Plants run their production plan in the same step.
5. **Fulfil** — parents ship against the open order book. This runs *every*
   day, not only on review day: a parent picks across the week as stock frees
   up. When it cannot cover everything its children asked for, the remainder is
   split **pro rata**.
6. **Snapshot** — on-hand is accumulated for the week's holding cost.

At week close, any internal order still open is cancelled and booked as
internal shortfall; the child re-orders at the next review. That is what makes
`demand_qty = shipments + unmet_qty` hold on every published row.

### Modes

| Mode | Horizon | Demand | Opening state |
|---|---|---|---|
| `history` | the span the order history covers | replayed from `orders.csv` | cold start, zero stock |
| `forward` | the planning horizon | forecast x a forecast-error ratio resampled from in-sample history | from `--state` |

### Assumptions

Every run records these in its own `run_manifest.json`.

{assum}

---

## Part 2 — The optimization seam

One standalone CSV, five required columns:

```
dc_id, sku_id, safety_stock, reorder_point, order_up_to_level
```

Those five are all the twin reads. **Extra columns are ignored**, so an
optimizer's native output — cluster id, target service level, arm label,
anything — can be handed over verbatim. A missing required column raises an
error rather than quietly defaulting to zero.

```bash
python models/twin/digital_twin.py --mode forward --run-name meio_v2 \
    --policy "output datasets/meio_v2/policy.csv" \
    --state  "output datasets/history/opening_position.csv"
```

**Comparison contract.** Two runs are comparable only when they share a seed
and an opening state and differ solely in the policy. `--policy` and `--state`
are separate arguments precisely so a policy swap cannot move the starting
position. Demand, lead-time and production random draws come from independent
streams, so changing the policy does not shift the demand the policy faces.

Echelon positioning needs no structural input: "push buffer to the customer
DCs" is simply a higher CDC order-up-to level and a lower RDC one, in the same
table.

Vary `--seed` for replicates of one policy. A bandit layer needs this — it has
to average reward over several demand and lead-time draws before it can tell
two arms apart.

### Standardized scenario runs

Pass `--scenario-config` to create a self-describing scenario folder named
`SCENARIO_####_Name`. The JSON file must contain `scenario_name` and a
machine-readable `changes` object. It may also contain `scenario_number`,
`description`, and a `details` object for ownership, hypotheses, assumptions,
or other relevant context. If `scenario_number` is omitted, the next available
number is assigned automatically.

```bash
python models/twin/digital_twin.py --mode forward \\
    --scenario-config models/twin/scenario_config.example.json \\
    --policy "output datasets/history/forward_policy.csv" \\
    --state "output datasets/history/opening_position.csv"
```

The run writes `scenario_config.json`, `run_manifest.json`, and all simulation
artefacts into the same standardized folder. The resolved execution settings
are added to the saved scenario config, while the original `--run-name` flow
continues to work for existing automation.

The policy table's full column-by-column dictionary is Part 5 of
`input datasets/DATASET_DOCUMENTATION.md`.

---

## Part 3 — Runs present

| Folder | Mode | Seed | Label | Size |
|---|---|---|---|---|
{run_rows}

A run folder is self-describing: `run_manifest.json` records the exact knobs,
seed, input paths and assumption set, so two folders can be diffed without
reference to the code that produced them.

---

## Part 4 — Artefact reference

### `opening_position.csv` — history runs only

The position the forward horizon opens on, which is the terminal state of
history. This is what a forward run consumes via `--state`.

| Column | Meaning |
|---|---|
| `period`, `week_start_date` | the first week of the forward horizon |
| `dc_id`, `sku_id` | location and product |
| `opening_stock` | units on hand at the cut-over |
| `in_transit_qty` | units already dispatched but not yet arrived |
| `in_transit_eta_week_start` | the week that pipeline stock lands |
| `inventory_position` | `opening_stock + in_transit_qty` — what a reorder point is compared against |
| `safety_stock`, `reorder_point`, `order_up_to_level` | policy, for reference |
| `lead_time_*`, `cum_lead_time_*` | inbound lane and plant-to-node totals |

Built off the terminal inventory rather than the forward policy, so a
(location, SKU) pair the forward policy does not cover still carries its real
stock across the cut-over instead of having it silently destroyed.

### `forward_policy.csv` — history runs only

The operating policy for the planning horizon, sized on the **forecast**. Same
five-column contract as above, plus `production_family`, `segment` and `z` for
reference. Its sibling `baseline_policy.csv` — sized on the order history and
used to simulate the past — lives in `input datasets/` because it ran the past.

### `policy.csv` — every run

Exactly the `(safety_stock, reorder_point, order_up_to_level)` that run
executed, normalised to the five-column contract. The audit trail: whatever was
fed in via `--policy`, this is what the engine actually used.

### `inventory.csv` — forward runs

Grain: **period x dc x sku**. The primary physics output.

| Column | Meaning |
|---|---|
| `opening_stock` | on hand at the start of the week |
| `receipts` | units received from the parent |
| `demand_qty` | total demand = customer + internal |
| `demand_customer_qty`, `demand_replen_qty` | external / internal split |
| `unmet_customer_qty` | external demand not served from stock |
| `unmet_replen_qty` | internal order balance still open at week close, then cancelled |
| `shipments` | units shipped to satisfy demand — customer served plus internal orders filled |
| `lost_margin_usd` | margin forgone on lost sales |
| `fulfilment_type` | `lost_sale` (customer DC) or `backorder` (plant / regional DC) |
| `closing_stock`, `backlog` | end-of-week position |
| `safety_stock`, `reorder_point`, `order_up_to_level` | the policy in force |
| `avg_on_hand` | mean daily on-hand — the holding-cost base |
| `holding_cost_usd` | `avg_on_hand x (storage + carrying% x unit_cost) / 52` |
| `lead_time_*`, `cum_lead_time_*` | inbound lane and plant-to-node |

`demand_qty = shipments + unmet_qty` holds on every row. Note that `demand_qty`
is an echelon total and counts a unit once per tier it passes through;
`demand_customer_qty` is the external figure that reconciles to real orders.

### `replenishment_receipts.csv` — forward runs

Grain: **one row per (ship day, lane, sku)**.

`receipt_id`, `period`, `order_week_start`, `sku_id`, `dc_id`,
`origin_node_id`, `qty_ordered`, `qty_received`, `promised_lead_days`,
`actual_lead_days`, `ship_day`, `arrival_day`, `on_time_flag`, `short_qty`.

Each order line gets its own realised transit time drawn from its lane's mean
and standard deviation — receipts arrive on their own draw, never the average.
`promised_lead_days` is the lane's ~90th percentile, the committed lead time
quoted to the child, which is what puts on-time delivery near 90% with a
genuine late tail rather than a coin flip. `short_qty` is the balance the parent
could not cover.

### `production.csv` — forward runs

Grain: **period x plant x sku**.

`plan_uncapped_qty` (what the rolling plan asked for) -> `planned_qty` (after
family line capacity, then plant-wide capacity) -> `actual_qty` (after schedule
adherence). `capacity_limited_flag` marks a clipped plant-week, and
`adherence_pct` records the attainment draw.

### `service_summary.csv` / `service_by_node.csv`

Per SKU and per node: demand, units served, unmet, customer fill rate, internal
fill rate, average on-hand and holding cost.

### `cost_summary.csv`

Per node: `avg_units_on_hand`, `avg_inventory_value_usd`, `holding_cost_usd`,
`stockout_cost_usd`, `transport_cost_usd`, `total_landed_cost_usd`.

`total_landed_cost_usd` is the scalar an optimizer minimises. Stockout cost is
margin-per-unit x units short, on the margin basis named in the manifest.

### `run_kpis.json`

Headline metrics for cross-run comparison: `customer_fill_rate` with its
customer-DC and regional-DC split, `pct_demand_weeks_in_full`,
`internal_replen_fill_rate`, inventory value, the three cost components, total
landed cost, and `margin_basis`.

**Read `pct_demand_weeks_in_full` alongside the fill rate.** Unit fill rate
counts a week served 90% as 0.9 of a unit fill; in-full counts it as a failed
order line. The two typically differ by about 5 points, and if the objective is
on-time-in-full then in-full is the honest number.

### `scenario_config.json`

The normalized scenario contract. Required fields are `scenario_name` and
`changes`; `details` is a JSON object for relevant scenario context. The
`execution` object records the mode, seed, policy, state, horizon, and output
folder actually used.

### `run_manifest.json`

`schema_version`, `run_name`, `label`, `mode`, `generated_at`, `seed`,
`policy_source`, `state_source`, `margin_basis`, `runtime_seconds`,
`assumptions` in plain language, `build_notes` (resolved routing counts,
demand basis, realised-to-forecast ratio), and the complete `scenario` object
when the run was launched with `--scenario-config`.

### `acceptance.csv` — history runs only

Each operating metric against its declared target band, with a pass flag. The
physics is fixed; when a metric misses, the policy knobs in the generator are
what move.

---

## Part 5 — Known limitations

- **No shelf life, obsolescence or write-offs.** Shelf life exceeds the
  simulated span by design, so perishability cannot bind and write-offs are nil.
  Any result here will not transfer directly to a chilled or frozen network.
- **No warehouse or lane capacity limits.** Production capacity *does* bind.
- **No lateral transshipment and no failover.** The lane table declares
  regional-DC-to-regional-DC lanes and second inbound lanes, but no flow is
  routed across them.
- **Single-sourcing per (node, SKU).** Routing is one parent per pair, taken
  from `sku_sourcing_map.csv`.
- **Disruption scenarios are not applied.** The library is generated but no run
  currently overlays it.
- **Demand is aggregated to (node, SKU, day).** Forward mode synthesises demand
  from the forecast, so there are no discrete order lines and no true
  order-level on-time-in-full; `pct_demand_weeks_in_full` is measured at
  node-SKU-week grain, which is coarser than an order line.
"""
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "OUTPUT_DOCUMENTATION.md").write_text(doc, encoding="utf-8")
    return doc.count("\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SCENARIO_CONFIG_SCHEMA_VERSION = "1.0"


def _scenario_folder_name(number: int, name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "Unnamed"
    return f"SCENARIO_{number:04d}_{slug}"


def _next_scenario_number(out_root: Path) -> int:
    numbers = []
    for folder in out_root.glob("SCENARIO_*_*"):
        match = re.match(r"^SCENARIO_(\d{4})_", folder.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def load_scenario_config(config_path: Path, out_root: Path) -> Tuple[dict, Path]:
    """Load and normalize scenario metadata and return its standardized folder."""
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"scenario config {config_path} is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("scenario config must contain a JSON object")

    name = config.get("scenario_name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("scenario config requires a non-empty 'scenario_name'")
    changes = config.get("changes")
    if not isinstance(changes, dict):
        raise ValueError("scenario config requires 'changes' as a JSON object")

    number = config.get("scenario_number")
    if number is None:
        number = _next_scenario_number(out_root)
    if isinstance(number, bool) or not isinstance(number, int) or not 0 < number <= 9999:
        raise ValueError("scenario_number must be an integer from 1 to 9999")

    normalized = dict(config)
    normalized.update({
        "schema_version": config.get("schema_version", SCENARIO_CONFIG_SCHEMA_VERSION),
        "scenario_number": number,
        "scenario_name": name.strip(),
        "changes": changes,
        "details": config.get("details", {}),
    })
    if not isinstance(normalized["details"], dict):
        raise ValueError("scenario config 'details' must be a JSON object")
    return normalized, out_root / _scenario_folder_name(number, name)


def _scenario_execution(config: dict, args, run_dir: Path) -> dict:
    resolved = dict(config)
    resolved["execution"] = {
        "mode": args.mode,
        "seed": args.seed,
        "horizon_weeks": args.horizon_weeks,
        "policy": args.policy,
        "state": args.state,
        "output_folder": str(run_dir),
    }
    return resolved


def run_twin(data_dir: Path, mode: str, seed: int,
             horizon_weeks: Optional[int] = None,
             policy_csv: Optional[Path] = None,
             state_csv: Optional[Path] = None, verbose: bool = True):
    inp, notes, rng_lt, rng_prod = build_inputs(
        data_dir, mode=mode, seed=seed, horizon_weeks=horizon_weeks,
        policy_csv=policy_csv, state_csv=state_csv)
    twin = SupplyBrainTwin(inp, rng_lt, rng_prod)
    inv, rcpt, prod = twin.run(verbose=verbose)
    return inp, notes, twin, inv, rcpt, prod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", default="baseline_asis")
    ap.add_argument("--mode", choices=["forward", "history"],
                    default="forward")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--horizon-weeks", type=int, default=None)
    ap.add_argument("--policy", required=True,
                    help="CSV of dc_id,sku_id,safety_stock,reorder_point,"
                         "order_up_to_level - the optimization seam")
    ap.add_argument("--state", default=None,
                    help="opening position CSV (opening_stock, "
                         "in_transit_qty). Omit for a cold start.")
    ap.add_argument("--label", default="")
    ap.add_argument("--scenario-config", default=None,
                    help="JSON metadata for a standardized SCENARIO_####_Name output folder")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--out-root", default=None)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent.parent
    data_dir = Path(args.data_dir) if args.data_dir else root / "input datasets"
    out_root = (Path(args.out_root) if args.out_root
                else root / "output datasets")

    scenario_config = None
    if args.scenario_config:
        scenario_config, run_dir = load_scenario_config(
            Path(args.scenario_config), out_root)
        scenario_config = _scenario_execution(scenario_config, args, run_dir)
    else:
        run_dir = (out_root / "projections" / args.run_name
                   if args.run_name == "baseline"
                   else out_root / args.run_name)

    t0 = time.time()
    print(f"Supply Brain digital twin — '{args.run_name}' [{args.mode}]")
    print(f"  margin basis: {MARGIN_COLUMN}")
    inp, notes, twin, inv, rcpt, prod = run_twin(
        data_dir, args.mode, args.seed, args.horizon_weeks,
        Path(args.policy),
        Path(args.state) if args.state else None)

    k, svc_node, costs = write_run(run_dir, inv, rcpt, prod,
                                   twin, inp, notes, args, t0,
                                   scenario_config)
    _n = write_output_documentation(out_root)
    print(f"  wrote OUTPUT_DOCUMENTATION.md ({_n:,} lines)")
    print("\n" + "=" * 62)
    print(f"{args.label or args.run_name}")
    print("=" * 62)
    print(f"  Customer fill rate      {k['customer_fill_rate']:>12.2%}")
    print(f"    - CDC                 "
          f"{(k['customer_fill_rate_cdc'] or 0):>12.2%}")
    print(f"    - RDC direct          "
          f"{(k['customer_fill_rate_rdc'] or 0):>12.2%}")
    print(f"  Demand-weeks in full    "
          f"{(k['pct_demand_weeks_in_full'] or 0):>12.2%}")
    print(f"  Internal replen fill    "
          f"{(k['internal_replen_fill_rate'] or 0):>12.2%}")
    print(f"  Avg inventory value     "
          f"${k['avg_inventory_value_usd']:>11,.0f}")
    print(f"  Holding                 ${k['holding_cost_usd']:>11,.0f}")
    print(f"  Stockout                ${k['stockout_cost_usd']:>11,.0f}")
    print(f"  Transport               ${k['transport_cost_usd']:>11,.0f}")
    print(f"  TOTAL LANDED COST       "
          f"${k['total_landed_cost_usd']:>11,.0f}")
    print("=" * 62)
    print(f"Wrote {run_dir}  "
          f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()

