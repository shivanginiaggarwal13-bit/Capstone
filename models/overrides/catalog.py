"""
Override measure catalog -- the vocabulary scenarios may change, and what each
measure means to MEIO (the plan side) and the Digital Twin (the actual side).

Owned by the override engine and shared with the agent tools (Agents/tools/catalog.py
re-exports it), so what an agent validates and previews and what the models apply are
defined in one place. `override.schema.json` beside this file checks an override's
SHAPE; this table checks its MEANING: which scope keys a measure accepts, which
operations and value ranges are legal, which side of the model it may touch, and which
engine phase implements it.

`applies_to` is the visibility switch that makes scenario work honest:
  plan    -- MEIO sizes the policy knowing about the change (an anticipated event)
  actual  -- the Twin simulates the change happening (what the network lives through)
A surge both sides see is a planned-for event; actual-only is a surprise; plan-only
is a forecast that turns out wrong. Each measure allows only the sides on which it
has a real effect -- e.g. MEIO never reads the opening position, so inventory
measures are actual-only, and the Twin has no notion of a service target, so
service measures are plan-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

OVERRIDE_SCHEMA_VERSION = "1.0"

SKU_KEYS = ("sku_id", "category", "subcategory", "brand", "segment", "price_tier",
            "production_family", "cluster", "archetype")
NODE_KEYS = ("dc_id", "region", "echelon")
CUSTOMER_KEYS = ("b2b_customer_id",)
LANE_KEYS = ("lane_id", "origin_node_id", "destination_node_id", "transport_mode")
PLANT_KEYS = ("plant_id", "production_family")
COST_KEYS = ("dc_id", "region", "echelon", "category", "subcategory")

SIDES = ("plan", "actual")


@dataclass(frozen=True)
class Op:
    """One legal operation: hard bounds reject, soft bounds warn."""
    lo: float | None
    hi: float | None
    units: tuple[str, ...]
    soft_lo: float | None = None
    soft_hi: float | None = None


@dataclass(frozen=True)
class Measure:
    name: str
    summary: str
    universe: str              # the rows scope resolves against -- see master.MasterData.universe
    scope_keys: tuple[str, ...]
    ops: dict
    applies_to: tuple[str, ...]
    default_applies_to: tuple[str, ...]
    windowed: bool
    meio_hook: str
    twin_hook: str
    phase: int
    options: dict = field(default_factory=dict)   # name -> (type, default, meaning)
    notes: str = ""

    @property
    def domain(self) -> str:
        return self.name.split(".")[0]


_POLICY_OPS = {
    "scale": Op(0.0, 10.0, ("multiplier",), 0.5, 3.0),
    "add": Op(-1e6, 1e6, ("units", "weeks_of_cover")),
    "set": Op(0.0, 1e7, ("units", "weeks_of_cover")),
    "floor": Op(0.0, 1e7, ("units", "weeks_of_cover")),
    "cap": Op(0.0, 1e7, ("units", "weeks_of_cover")),
}


def _policy(field_name: str, label: str) -> Measure:
    return Measure(
        name=f"policy.{field_name}",
        summary=f"Planner override on the MEIO {label} after optimisation (manual policy override).",
        universe="stock_pairs", scope_keys=SKU_KEYS + NODE_KEYS, ops=_POLICY_OPS,
        applies_to=("plan",), default_applies_to=("plan",), windowed=True,
        meio_hook="post-solve, per target period, before the policy CSV is written "
                  "(roll_forward_policy.run / clark_scarf_v2.build_rolling_policy)",
        twin_hook="none -- reaches the Twin through the policy CSV",
        phase=1,
        notes="weeks_of_cover multiplies the period's target_mean. The policy keeps its shape: "
              "a safety-stock change moves the reorder point and order-up-to by the same units "
              "(s = cycle + SS, S = s + cover), a reorder-point change moves order-up-to with it, "
              "and order_up_to_level >= reorder_point is re-imposed last.")


MEASURES: dict[str, Measure] = {m.name: m for m in [
    # ------------------------------------------------------------------ demand
    Measure(
        name="demand.volume",
        summary="Customer demand volume: the forecast MEIO sizes against and/or the demand the Twin realises.",
        universe="demand_lines", scope_keys=SKU_KEYS + NODE_KEYS + CUSTOMER_KEYS,
        ops={"scale": Op(0.0, 5.0, ("multiplier",), 0.5, 2.0),
             "add": Op(-1e5, 1e5, ("units_per_week",))},
        applies_to=SIDES, default_applies_to=SIDES, windowed=True,
        meio_hook="meio_weekly_lib.target_week_demand output (target_mean, target_std; CV held)",
        twin_hook="digital_twin._build_demand: forward forecast rows before the error-ratio draw",
        phase=1,
        notes="'add' is units per week per matched demand line (dc x sku x customer); results "
              "floor at zero. Customer-scoped changes reach MEIO as a forecast-weighted multiplier "
              "on the (dc, sku) estimate."),
    Measure(
        name="demand.volatility",
        summary="Demand uncertainty: spread of forecast error around the forecast.",
        universe="demand_pairs", scope_keys=SKU_KEYS + NODE_KEYS,
        ops={"scale": Op(0.25, 4.0, ("multiplier",), 0.5, 2.0)},
        applies_to=SIDES, default_applies_to=SIDES, windowed=True,
        meio_hook="meio_weekly_lib.target_week_demand output (target_std)",
        twin_hook="digital_twin._build_demand: error ratio r -> 1 + k (r - 1)",
        phase=1),
    # ------------------------------------------------------------------ supply
    Measure(
        name="supply.lead_time_mean",
        summary="Mean transit lead time on matched lanes (carrier delay, port congestion, mode shift).",
        universe="lanes", scope_keys=LANE_KEYS + NODE_KEYS,
        ops={"add": Op(-14.0, 90.0, ("days",), -3.0, 30.0),
             "scale": Op(0.25, 5.0, ("multiplier",), 0.5, 3.0)},
        applies_to=SIDES, default_applies_to=SIDES, windowed=True,
        meio_hook="meio_weekly_lib.build_leadtime_graph / build_pdc_leadtime, per target period",
        twin_hook="SupplyBrainTwin._lead_times + lt_promise: lane parameters by dispatch week",
        phase=2,
        notes="Node keys (dc_id, region, echelon) filter on the lane's DESTINATION. Applies to "
              "shipments dispatched inside the window. Result floors at 0.5 days."),
    Measure(
        name="supply.lead_time_std",
        summary="Lead-time variability (standard deviation) on matched lanes.",
        universe="lanes", scope_keys=LANE_KEYS + NODE_KEYS,
        ops={"scale": Op(0.0, 5.0, ("multiplier",), 0.5, 3.0),
             "add": Op(0.0, 30.0, ("days",))},
        applies_to=SIDES, default_applies_to=SIDES, windowed=True,
        meio_hook="meio_weekly_lib.build_leadtime_graph (lane variance), per target period",
        twin_hook="SupplyBrainTwin._lead_times: lane std by dispatch week",
        phase=2,
        notes="Node keys filter on the lane's destination."),
    Measure(
        name="supply.lane_outage",
        summary="Lane unavailable for the window (strike, closure, node offline).",
        universe="lanes", scope_keys=LANE_KEYS + NODE_KEYS,
        ops={"block": Op(None, None, ("none",))},
        applies_to=("actual",), default_applies_to=("actual",), windowed=True,
        meio_hook="none -- pair with a plan-side supply.lead_time_mean override to model foresight",
        twin_hook="SupplyBrainTwin._ship: v1 holds dispatches until the lane reopens; "
                  "v2 (Phase 3) re-routes to the alternate inbound lane",
        phase=3,
        notes="The network declares failover and lateral lanes but simulates no flow on them."),
    Measure(
        name="supply.plant_capacity",
        summary="Weekly production capacity of a plant (all lines) or of named production-family lines.",
        universe="plant_families", scope_keys=PLANT_KEYS,
        ops={"scale": Op(0.0, 2.0, ("multiplier",), 0.3, 1.5),
             "set": Op(0.0, 1e7, ("units_per_week",))},
        applies_to=SIDES, default_applies_to=SIDES, windowed=True,
        meio_hook="meio_weekly_lib.load_plant_capacity / clip_to_plant_capacity, per target period",
        twin_hook="SupplyBrainTwin._produce: cap_plant / cap_family by production week",
        phase=2,
        notes="Scope with plant_id only changes the plant-wide limit AND every family line at that "
              "plant; adding production_family changes only those lines. 'set' requires a "
              "production_family scope."),
    Measure(
        name="supply.schedule_adherence",
        summary="Production schedule attainment: share of the capped plan the plant actually builds.",
        universe="plant_families", scope_keys=PLANT_KEYS,
        ops={"scale": Op(0.1, 1.1, ("multiplier",), 0.5, 1.05)},
        applies_to=("actual",), default_applies_to=("actual",), windowed=True,
        meio_hook="none -- MEIO does not model attainment",
        twin_hook="SupplyBrainTwin._produce: executed = planned x adherence x multiplier (capped at plan)",
        phase=2,
        notes="Baseline attainment is drawn uniformly in [0.90, 1.00], mean 0.95. A target of 80% "
              "attainment is a multiplier of 0.80 / 0.95 = 0.84."),
    # --------------------------------------------------------------- inventory
    Measure(
        name="inventory.on_hand",
        summary="Opening on-hand stock at 2026-W01 (write-off, damage, recall, injection, pre-build).",
        universe="stock_pairs", scope_keys=SKU_KEYS + NODE_KEYS,
        ops={"scale": Op(0.0, 10.0, ("multiplier",), 0.0, 3.0),
             "add": Op(-1e6, 1e6, ("units", "weeks_of_cover")),
             "set": Op(0.0, 1e7, ("units", "weeks_of_cover"))},
        applies_to=("actual",), default_applies_to=("actual",), windowed=False,
        meio_hook="none -- MEIO sizes policy parameters, not orders, so the opening position "
                  "does not enter it",
        twin_hook="digital_twin._load_policy: opening state after the --state CSV is read",
        phase=1,
        notes="weeks_of_cover = units / the weekly demand MEIO plans the node against, averaged over "
              "the first four forward weeks (own demand at CDCs and RDCs, throughput at plant DCs). "
              "Results floor at zero. Mid-horizon shocks are Phase 3."),
    Measure(
        name="inventory.in_transit",
        summary="Opening pipeline stock already dispatched at 2026-W01.",
        universe="stock_pairs", scope_keys=SKU_KEYS + NODE_KEYS,
        ops={"scale": Op(0.0, 10.0, ("multiplier",), 0.0, 3.0),
             "set": Op(0.0, 1e7, ("units",))},
        applies_to=("actual",), default_applies_to=("actual",), windowed=False,
        meio_hook="none",
        twin_hook="digital_twin._load_policy: opening in-transit quantity (arrival day unchanged)",
        phase=1),
    # ----------------------------------------------------------------- service
    Measure(
        name="service.target_csl",
        summary="Target cycle service level MEIO sizes safety stock to (replaces the cluster-ladder z).",
        universe="stock_pairs", scope_keys=SKU_KEYS + NODE_KEYS,
        ops={"set": Op(0.5, 0.999, ("probability",), 0.80, 0.995),
             "add": Op(-0.2, 0.2, ("probability",))},
        applies_to=("plan",), default_applies_to=("plan",), windowed=True,
        meio_hook="meio_weekly_lib.assign_z: z = Phi^-1(CSL) per (sku, dc) and target period",
        twin_hook="none -- reaches the Twin through the policy it sizes",
        phase=1,
        notes="Live default: the segmentation cluster ladder (sku_clusters.csv) -- Flagship Core and "
              "Mid-Tier Core 0.97 (z 1.88), Long-Tail Erratic 2 0.957 (z 1.72), other long-tail clusters "
              "0.90 (z 1.28); the old segment ladder (Fast-Moving 0.98, Seasonal 0.95, Intermediate 0.90) "
              "applies only to a SKU with no cluster. Clark-Scarf v2 clips z to [0.84, 3.10] (CSL 0.80-0.999). "
              "'add' moves the current CSL by that many probability points."),
    _policy("safety_stock", "safety stock"),
    _policy("reorder_point", "reorder point (s)"),
    _policy("order_up_to_level", "order-up-to level (S)"),
    Measure(
        name="policy.pooling_alpha",
        summary="Clark-Scarf v2 pooling factor per SKU: 0 = RDC sized alone, 1 = fully pooled with its CDCs.",
        universe="skus", scope_keys=SKU_KEYS,
        ops={"set": Op(0.0, 1.0, ("fraction",))},
        applies_to=("plan",), default_applies_to=("plan",), windowed=False,
        meio_hook="clark_scarf_v2.solve's RDC pooling blend (PlanHooks.alpha) -- only when "
                  "scenario_config.json's meio.parameters.model is clark_scarf_v2",
        twin_hook="none",
        phase=2,
        notes="Portfolio default alpha is 0.2 (final_submission/README.md). Core clusters favour "
              "0.4-0.6, long-tail clusters 0.0-0.2 on fill rate -- direction, not decimals."),
    # --------------------------------------------------------------- economics
    Measure(
        name="economics.unit_cost",
        summary="Unit cost of goods (COGS inflation or deflation): inventory value and carrying cost.",
        universe="skus", scope_keys=SKU_KEYS,
        ops={"scale": Op(0.5, 2.0, ("multiplier",), 0.8, 1.3),
             "add": Op(-1000.0, 1000.0, ("usd_per_unit",))},
        applies_to=("actual",), default_applies_to=("actual",), windowed=False,
        meio_hook="none -- MEIO sizing is service-level driven; cost enters only reported $",
        twin_hook="digital_twin.build_inputs: unit_cost (inventory value, carrying cost)",
        phase=1,
        options={"hold_list_price": ("bool", True,
                                     "list price is held, so margin falls by the cost increase")}),
    Measure(
        name="economics.margin",
        summary="Gross margin per unit: what a lost sale costs in the Twin's stockout valuation.",
        universe="skus", scope_keys=SKU_KEYS,
        ops={"scale": Op(0.0, 3.0, ("multiplier",), 0.5, 1.5),
             "add": Op(-1000.0, 1000.0, ("usd_per_unit",))},
        applies_to=("actual",), default_applies_to=("actual",), windowed=False,
        meio_hook="none",
        twin_hook="digital_twin.build_inputs: margin_per_unit (lost-sale cost)",
        phase=1),
    Measure(
        name="economics.holding_rate",
        summary="Inventory carrying rate, % of unit cost per year (cost of capital).",
        universe="dc_subcategories", scope_keys=COST_KEYS,
        ops={"scale": Op(0.25, 4.0, ("multiplier",), 0.5, 2.0),
             "set": Op(0.0, 60.0, ("pct_annual",), 5.0, 30.0)},
        applies_to=("actual",), default_applies_to=("actual",), windowed=False,
        meio_hook="none",
        twin_hook="digital_twin.build_inputs: inventory_carrying_pct_annual in holding cost",
        phase=1),
    Measure(
        name="economics.storage_cost",
        summary="Warehouse storage cost per unit-year (space, labour, energy).",
        universe="dc_subcategories", scope_keys=COST_KEYS,
        ops={"scale": Op(0.25, 4.0, ("multiplier",), 0.5, 2.0)},
        applies_to=("actual",), default_applies_to=("actual",), windowed=False,
        meio_hook="none",
        twin_hook="digital_twin.build_inputs: storage_cost_per_unit_year_usd in holding cost",
        phase=1),
    Measure(
        name="economics.logistics_cost",
        summary="Freight cost per unit on matched lanes (fuel, rates, mode).",
        universe="lanes", scope_keys=LANE_KEYS + NODE_KEYS,
        ops={"scale": Op(0.25, 4.0, ("multiplier",), 0.7, 1.5)},
        applies_to=("actual",), default_applies_to=("actual",), windowed=False,
        meio_hook="none",
        twin_hook="digital_twin.build_inputs: logistics_cost_per_unit_usd (transport cost)",
        phase=1,
        notes="Node keys filter on the lane's destination."),
]}

# Which measure domains each specialist may write through propose_override. Macro
# tools (promotions, library scenarios, service ladders) are authorised by tool, not
# by domain -- see registry.AGENTS.
AGENT_DOMAINS = {
    "demand_agent": ("demand",),
    "supply_agent": ("supply",),
    "inventory_agent": ("inventory",),
    "service_policy_agent": ("service", "policy"),
    "economics_agent": ("economics",),
}

# Published category elasticities recovered from orders.parquet
# (input datasets/DATASET_DOCUMENTATION.md, section 6.8). The generator draws
# per-SKU elasticities from these groups and applies lift = (1 - discount) ** e_promo
# for deals and (price / list) ** e_base for everyday prices.
BASE_ELASTICITY = {"Beverages": -1.12, "Dairy & Bakery": -1.99, "Frozen Foods": -3.77,
                   "Grocery": -1.99, "Snacks & Confectionery": -3.56}
PROMO_ELASTICITY = {"Beverages": -1.35, "Dairy & Bakery": -2.45, "Frozen Foods": -3.18,
                    "Grocery": -2.56, "Snacks & Confectionery": -3.32}
# Pantry-loading payback after a promotion, as the generator models it:
# dip = storability x min(depth / 0.30, 1) x 0.20 in week +1, half that in week +2.
STORABILITY = {"Beverages": 0.9, "Snacks & Confectionery": 0.9, "Dairy & Bakery": 0.2,
               "Frozen Foods": 1.0, "Grocery": 0.9}
PROMO_LIFT_CAP = 12.0

SEGMENT_Z = {"Fast-Moving": 2.05, "Seasonal": 1.64, "Intermediate": 1.28}
Z_DEFAULT = 1.28


def measures_for_agent(agent: str) -> list[str]:
    domains = AGENT_DOMAINS.get(agent, ())
    return [name for name, m in MEASURES.items() if m.domain in domains]


def describe(name: str) -> dict:
    m = MEASURES[name]
    return {
        "measure": m.name,
        "summary": m.summary,
        "scope_keys": list(m.scope_keys),
        "operations": {op: {"min": spec.lo, "max": spec.hi, "units": list(spec.units),
                            "typical_range": [spec.soft_lo, spec.soft_hi]
                            if spec.soft_lo is not None else None}
                       for op, spec in m.ops.items()},
        "applies_to_allowed": list(m.applies_to),
        "applies_to_default": list(m.default_applies_to),
        "windowed": m.windowed,
        "options": {k: {"type": t, "default": d, "meaning": s} for k, (t, d, s) in m.options.items()},
        "meio_hook": m.meio_hook,
        "twin_hook": m.twin_hook,
        "engine_phase": m.phase,
        "notes": m.notes,
    }
