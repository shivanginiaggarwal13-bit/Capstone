"""
Scenario Engine — Shared Demand-Surge Application for the Twin and the Policy Generator
===============================================================================

Feature 2 scoping (see memory: meio-scenario-wiring-todo) found that
`digital_twin.py --scenario-config` was metadata-only: it saved a
self-describing `changes` object but never applied it. This module is the
first real scenario type wired end to end: a demand surge/shock, affecting
either named nodes or a named region, for a given number of weeks.

Shared by both halves of the feature, not duplicated:
  - `apply_demand_surge_to_tensor()` -- used by `digital_twin.py` to perturb
    the SIMULATED demand a policy is scored against.
  - `apply_demand_surge_to_target_demand()` -- used by the MEIO policy
    generator (`clark_scarf_v2.py::solve`) to perturb `target_mean`/
    `target_std`, i.e. the OUTPUT of `meio_weekly_lib.target_week_demand()`
    that a policy is SIZED against, so a scenario-aware run actually
    recommends different stocking, not just simulates a fixed policy
    differently. Applied to that function's output, not its raw forecast
    input, so it doesn't need to understand or re-derive the
    deseasonalization/bias-correction math inside it.

Both are no-ops (return the input unchanged) if `changes` doesn't contain
`demand_surge_pct` -- every existing call site that doesn't pass scenario
changes at all is completely unaffected; this is purely additive.

`changes` schema (matches `scenario_config.example.json`):
    {
      "demand_surge_pct": 25,          # required to trigger anything; +25 = 1.25x
      "affected_region": "Southeast",  # OR "affected_nodes": ["RDC02", "CDC03"]
      "duration_weeks": 4,             # default: the whole horizon
      "start_week_offset": 0           # weeks from the start of the simulated
                                        # horizon (twin) / target week (policy);
                                        # default 0 -- starts immediately
    }
"""
import numpy as np
import pandas as pd

DAYS_PER_WEEK = 7


def resolve_affected_nodes(data_dir, changes: dict) -> list:
    """'affected_region' (e.g. 'Southeast') -> CDC + RDC node ids in that
    region, from customer_dc_master.csv + regional_dc_master.csv (both have
    a `region` column). An explicit 'affected_nodes' list in `changes`
    takes precedence if given. Returns [] if neither is present -- callers
    should treat that as "nothing to do", not an error."""
    if "affected_nodes" in changes:
        return list(changes["affected_nodes"])
    region = changes.get("affected_region")
    if not region:
        return []
    cdc = pd.read_csv(data_dir / "customer_dc_master.csv", usecols=["customer_dc_id", "region"])
    rdc = pd.read_csv(data_dir / "regional_dc_master.csv", usecols=["regional_dc_id", "region"])
    nodes = list(cdc.loc[cdc.region == region, "customer_dc_id"])
    nodes += list(rdc.loc[rdc.region == region, "regional_dc_id"])
    return nodes


def apply_demand_surge_to_tensor(daily_demand: np.ndarray, node_idx: dict,
                                  affected_nodes: list, changes: dict) -> np.ndarray:
    """Scale digital_twin.py's [T, n_nodes, n_skus] daily demand tensor for
    affected_nodes, over `duration_weeks` weeks starting at
    `start_week_offset` weeks into the horizon. Returns a NEW array (does
    not mutate the input) -- no-op (returns the input unchanged, same
    object) if 'demand_surge_pct' isn't in changes or no node resolves."""
    if "demand_surge_pct" not in changes or not affected_nodes:
        return daily_demand
    node_ids = [node_idx[n] for n in affected_nodes if n in node_idx]
    if not node_ids:
        return daily_demand

    multiplier = 1.0 + float(changes["demand_surge_pct"]) / 100.0
    total_weeks = daily_demand.shape[0] // DAYS_PER_WEEK
    duration_weeks = int(changes.get("duration_weeks", total_weeks))
    start_week_offset = int(changes.get("start_week_offset", 0))
    day_start = max(0, start_week_offset * DAYS_PER_WEEK)
    day_end = min(daily_demand.shape[0], day_start + duration_weeks * DAYS_PER_WEEK)
    if day_start >= day_end:
        return daily_demand

    out = daily_demand.copy()
    out[day_start:day_end, node_ids, :] = np.round(
        out[day_start:day_end, node_ids, :] * multiplier).astype(out.dtype)
    return out


def apply_demand_surge_to_target_demand(demand: pd.DataFrame, affected_nodes: list,
                                         changes: dict, as_of_week_num: int,
                                         target_week_num: int) -> pd.DataFrame:
    """Scale `target_mean`/`target_std` in `meio_weekly_lib.target_week_
    demand()`'s output, for affected_nodes' rows, IF `target_week_num`
    falls in the scenario's affected window [as_of_week_num +
    start_week_offset + 1, as_of_week_num + start_week_offset +
    duration_weeks] -- the policy-sizing-side counterpart to
    apply_demand_surge_to_tensor: same multiplier, applied to the ESTIMATE
    a policy is sized against instead of the simulated realization.
    Returns a NEW dataframe (does not mutate the input) -- no-op if
    'demand_surge_pct' isn't in changes, no node resolves, or
    target_week_num falls outside the window."""
    if "demand_surge_pct" not in changes or not affected_nodes:
        return demand
    duration_weeks = int(changes.get("duration_weeks", 10 ** 6))
    start_week_offset = int(changes.get("start_week_offset", 0))
    wk_lo = as_of_week_num + start_week_offset + 1
    wk_hi = as_of_week_num + start_week_offset + duration_weeks
    if not (wk_lo <= target_week_num <= wk_hi):
        return demand
    if not set(affected_nodes) & set(demand.dc_id.unique()):
        return demand

    multiplier = 1.0 + float(changes["demand_surge_pct"]) / 100.0
    out = demand.copy()
    mask = out.dc_id.isin(affected_nodes)
    out.loc[mask, "target_mean"] = out.loc[mask, "target_mean"] * multiplier
    out.loc[mask, "target_std"] = out.loc[mask, "target_std"] * multiplier
    return out
