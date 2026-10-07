"""
Clark-Scarf v2 — Continuous Pooling Factor + Monte Carlo DDLT
===============================================================================

The chosen model for the final submission. Two upgrades over a textbook
Clark-Scarf echelon model:

1. **Continuous pooling factor alpha**, not an all-or-nothing choice. Alpha
   blends an RDC's local safety stock between two endpoints:
       alpha=0: no pooling -- RDC sized independently against its own direct demand
       alpha=1: full pooling -- RDC's local SS = echelon-pooled SS, netted
                against what its CDCs already hold
       RDC_local_SS(alpha) = alpha * pooled_netted_SS + (1-alpha) * independent_SS
   Alpha is found by grid search with the digital twin as the reward oracle
   (the twin's own stockout-cost/fill-rate response to alpha is nonlinear
   even though the safety-stock blend is linear in alpha).

2. **Monte Carlo demand-during-lead-time**, replacing closed-form z*sigma.
   Demand and lead time are both drawn from a Gamma distribution (moment-
   matched to the target mean/std) -- Gamma is right-skewed and x>=0, which
   fits real demand better than a Normal at the 95-99% service levels this
   project sizes against. Reorder point = the target-CSL percentile of the
   simulated DDLT distribution.

3. **Netting discount** (`netting_discount`, default 1.0): the pooled RDC
   endpoint's safety stock is `max(0, pooled_SS - netting_discount *
   downstream_CDC_SS)`. At 1.0 (original behavior) the RDC is credited with
   100% of what its CDCs already hold, which assumes stock can move to
   wherever it's needed on no notice -- the twin's real weekly-review +
   lead-time physics don't support that, which is the leading hypothesis for
   why internal replenishment fill degrades under this model (see README).
   Lower values leave the RDC holding more of its own stock instead of
   crediting it away; 0.0 nets nothing. See `netting_discount_sweep.py` for
   the real-data test of this tradeoff.

Scope: Monte Carlo + continuous alpha apply to RDC/CDC sizing. PDC still
uses `meio_weekly_lib.solve_pdc_policy`'s closed-form push-ceiling logic
unchanged (not at stake here -- see README "Known limitation: internal
replenishment fill").

`ALPHA_OVERRIDE_BY_SKU` is the seam `segment_alpha_explore.py` uses to test
a different alpha per segmentation-deliverable cluster instead of one
portfolio-wide scalar -- empty by default, so every normal call here is
unaffected.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
import meio_weekly_lib as lib

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "twin")))
import scenario_engine

N_SIMS = 4000

ALPHA_OVERRIDE_BY_SKU: dict = {}


def resolve_alpha(df: pd.DataFrame, default_alpha: float) -> np.ndarray:
    """Per-row alpha: ALPHA_OVERRIDE_BY_SKU[sku_id] if set, else
    default_alpha (normal behavior when the override is empty)."""
    if ALPHA_OVERRIDE_BY_SKU:
        return df.sku_id.map(ALPHA_OVERRIDE_BY_SKU).fillna(default_alpha).to_numpy()
    return np.full(len(df), default_alpha)


def _gamma_params(mean, std):
    """Method-of-moments Gamma(shape, scale) from (mean, std), vectorized.
    Falls back to a point mass at `mean` wherever std<=0 or mean<=0."""
    mean = np.asarray(mean, dtype=float)
    std = np.asarray(std, dtype=float)
    safe = (mean > 1e-9) & (std > 1e-9)
    shape = np.where(safe, (mean / np.maximum(std, 1e-9)) ** 2, 1e6)
    scale = np.where(safe, (std ** 2) / np.maximum(mean, 1e-9), np.maximum(mean, 1e-9) / 1e6)
    return shape, scale


def monte_carlo_ddlt(target_mean, target_std, lt_mean_wk, lt_std_wk, target_csl,
                      rng: np.random.Generator, n_sims: int = N_SIMS):
    """Vectorized Monte Carlo DDLT for every (sku, dc) line at once.
    Returns (reorder_point, safety_stock, cycle_stock) arrays."""
    n = len(target_mean)
    lt_shape, lt_scale = _gamma_params(lt_mean_wk, lt_std_wk)
    L = rng.gamma(lt_shape[None, :], lt_scale[None, :], size=(n_sims, n))
    P = lib.REVIEW_WEEKS + L

    ddlt_mean = target_mean[None, :] * P
    ddlt_std = target_std[None, :] * np.sqrt(P)
    d_shape, d_scale = _gamma_params(ddlt_mean, ddlt_std)
    DDLT = rng.gamma(d_shape, d_scale)

    cycle_stock = DDLT.mean(axis=0)
    reorder_point = np.quantile(DDLT, target_csl, axis=0) if np.isscalar(target_csl) else \
        np.array([np.quantile(DDLT[:, i], target_csl[i]) for i in range(n)])
    safety_stock = np.maximum(0.0, reorder_point - cycle_stock)
    return reorder_point, safety_stock, cycle_stock


def solve(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
          cost, active_rdc, active_cdc, cdc_primary_rdc, target_week_num, alpha: float, rng,
          n_sims: int = N_SIMS, netting_discount: float = 1.0,
          scenario_changes: dict = None, scenario_affected_nodes: list = None):
    """One target week's RDC+CDC policy. Call directly for a walk-forward
    loop (advancing as_of_week_num each call); see `build_rolling_policy`
    for the fixed-as-of 52-week build.

    netting_discount in [0, 1]: how much of a CDC's own safety stock the
    pooled RDC endpoint nets against (1.0 = full netting, today's original
    behavior; 0.0 = no netting -- the RDC holds its full pooled SS on top of
    whatever the CDCs already hold). See the netting comment below.

    scenario_changes/scenario_affected_nodes (both None by default -- a
    complete no-op for every existing call site): a Feature-2 scenario's
    `changes` object and its already-resolved affected-node list (resolve
    once per build in `build_rolling_policy`, not once per target week --
    see scenario_engine.py). If given and `target_week_num` falls in the
    scenario's affected window, the demand this week's policy is SIZED
    against is bumped the same way the twin bumps what it SIMULATES, so a
    scenario-aware policy actually recommends different stocking."""
    demand = lib.target_week_demand(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                     target_week_num=target_week_num)
    if scenario_changes and scenario_affected_nodes:
        demand = scenario_engine.apply_demand_surge_to_target_demand(
            demand, scenario_affected_nodes, scenario_changes, as_of_week_num, target_week_num)

    # --- CDC base echelon: Monte Carlo DDLT instead of z*sigma ---
    cdc = demand[demand.dc_id.isin(active_cdc)].copy()
    cdc["lt_mean_wk"] = cdc.dc_id.map(lambda x: leadtime_by_dc[x][0] / 7.0)
    cdc["lt_std_wk"] = cdc.dc_id.map(lambda x: np.sqrt(leadtime_by_dc[x][1]) / 7.0)
    cdc = cdc.merge(cost, on=["sku_id", "dc_id"], how="left")
    cdc["z"] = lib.assign_z(cdc)
    cdc["target_csl"] = norm.cdf(cdc.z.clip(lib.Z_MIN, lib.Z_MAX))
    cdc["target_std_uplifted"] = cdc.target_std * lib.SIGMA_UPLIFT["CDC"]

    rop, ss, cyc = monte_carlo_ddlt(cdc.target_mean.to_numpy(), cdc.target_std_uplifted.to_numpy(),
                                     cdc.lt_mean_wk.to_numpy(), cdc.lt_std_wk.to_numpy(),
                                     cdc.target_csl.to_numpy(), rng, n_sims=n_sims)
    cdc["reorder_point"], cdc["safety_stock"], cdc["cycle_stock"] = rop, ss, cyc
    cover_wk = max(lib.REVIEW_WEEKS, lib.ORDER_COVER_WEEKS)
    cdc["order_up_to_level"] = np.maximum(cdc.reorder_point + cdc.target_mean * cover_wk,
                                           cdc.reorder_point * lib.MIN_S_MULTIPLE)

    # --- RDC: Monte Carlo at both the pooled and independent endpoints, blended by alpha ---
    rdc_rows = []
    for r in active_rdc:
        own = demand[demand.dc_id == r][["sku_id", "target_mean", "target_std"]].rename(
            columns={"target_mean": "own_mean", "target_std": "own_std"})
        served = [c for c, p in cdc_primary_rdc.items() if p == r]
        down_cdc = cdc[cdc.dc_id.isin(served)]
        down = down_cdc.groupby("sku_id").agg(
            down_mean=("target_mean", "sum"),
            down_var=("target_std", lambda s: (s ** 2).sum()),
            down_ss=("safety_stock", "sum"),
            down_S=("order_up_to_level", "sum"),
        ).reset_index() if served else pd.DataFrame(columns=["sku_id", "down_mean", "down_var", "down_ss", "down_S"])
        merged = pd.merge(own, down, on="sku_id", how="outer").fillna(0.0)
        merged["dc_id"] = r
        rdc_rows.append(merged)
    rdc = pd.concat(rdc_rows, ignore_index=True) if rdc_rows else pd.DataFrame()
    rdc["lt_mean_wk"] = rdc.dc_id.map(lambda x: leadtime_by_dc[x][0] / 7.0)
    rdc["lt_std_wk"] = rdc.dc_id.map(lambda x: np.sqrt(leadtime_by_dc[x][1]) / 7.0)
    rdc = rdc.merge(cost, on=["sku_id", "dc_id"], how="left")
    rdc["z"] = lib.assign_z(rdc)
    rdc["target_csl"] = norm.cdf(rdc.z.clip(lib.Z_MIN, lib.Z_MAX))

    # endpoint A: independent (alpha=0)
    rop_i, ss_i, cyc_i = monte_carlo_ddlt(
        rdc.own_mean.to_numpy(), (rdc.own_std * lib.SIGMA_UPLIFT["RDC"]).to_numpy(),
        rdc.lt_mean_wk.to_numpy(), rdc.lt_std_wk.to_numpy(), rdc.target_csl.to_numpy(), rng, n_sims=n_sims)

    # endpoint B: fully pooled (alpha=1) -- netted against downstream CDC SS,
    # discounted by netting_discount. Full netting (discount=1.0) assumes
    # pooled stock can move to wherever it's needed; the twin's real
    # weekly-review + lead-time physics don't support that instantly, which
    # is the leading hypothesis for why internal replenishment fill degrades
    # more under this model than under independent sizing (see README
    # "internal fill"). A lower discount leaves the RDC holding more of its
    # own stock instead of crediting it against what the CDCs already hold.
    mu_echelon = rdc.own_mean + rdc.down_mean
    sigma_echelon = np.sqrt((rdc.own_std * lib.SIGMA_UPLIFT["RDC"]) ** 2 + rdc.down_var)
    rop_p, ss_p_raw, cyc_p = monte_carlo_ddlt(
        mu_echelon.to_numpy(), sigma_echelon.to_numpy(),
        rdc.lt_mean_wk.to_numpy(), rdc.lt_std_wk.to_numpy(), rdc.target_csl.to_numpy(), rng, n_sims=n_sims)
    ss_p = np.maximum(0.0, ss_p_raw - netting_discount * rdc.down_ss.to_numpy())

    alpha_vec = resolve_alpha(rdc, alpha)
    rdc["safety_stock"] = alpha_vec * ss_p + (1 - alpha_vec) * ss_i
    rdc["cycle_stock"] = alpha_vec * cyc_p + (1 - alpha_vec) * cyc_i
    rdc["reorder_point"] = rdc.cycle_stock + rdc.safety_stock
    cover_wk = max(lib.REVIEW_WEEKS, lib.ORDER_COVER_WEEKS)
    s_endpoint_p = np.maximum(rdc.reorder_point + mu_echelon * cover_wk - rdc.down_S.to_numpy(), rdc.reorder_point)
    s_endpoint_i = np.maximum(rdc.reorder_point + rdc.own_mean * cover_wk, rdc.reorder_point * lib.MIN_S_MULTIPLE)
    rdc["order_up_to_level"] = alpha_vec * s_endpoint_p + (1 - alpha_vec) * s_endpoint_i

    keep = ["dc_id", "sku_id", "category", "subcategory", "segment", "safety_stock",
            "reorder_point", "order_up_to_level", "target_csl"]
    out = pd.concat([cdc[keep], rdc[keep]], ignore_index=True)
    for c in ["safety_stock", "reorder_point", "order_up_to_level"]:
        out[c] = out[c].round(1)
    return out


def build_rolling_policy(data_dir, as_of_week, alpha, n_sims, rng, out_path, netting_discount: float = 1.0,
                          scenario_changes: dict = None):
    """Full 52-week rolling policy from a SINGLE fixed as-of point (used for
    the synthetic-2026 basis -- there's no real future data past as_of_week
    to refit on). For the real-data walk-forward's week-by-week refit with
    an ADVANCING as-of, call `solve()` directly in a loop instead (see
    `validate_real_data.py`).

    scenario_changes: None by default (a complete no-op). If given, affected
    nodes are resolved ONCE here (not once per target week -- see
    scenario_engine.py) and threaded into every solve() call, so the
    recommended policy for weeks inside the scenario's window reflects it."""
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)
    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)

    scenario_affected_nodes = (scenario_engine.resolve_affected_nodes(Path(data_dir), scenario_changes)
                                if scenario_changes else None)

    as_of_num = int(period_index.loc[period_index.period == as_of_week, "week_num"].iloc[0])
    forward_weeks = sorted(period_index[period_index.week_num > as_of_num]["week_num"].tolist())

    all_weeks = []
    for wk_num in forward_weeks:
        target_period = period_index.loc[period_index.week_num == wk_num, "period"].iloc[0]
        policy = solve(dfc, as_of_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
                       cost, active_rdc, active_cdc, cdc_primary_rdc, wk_num, alpha, rng, n_sims=n_sims,
                       netting_discount=netting_discount, scenario_changes=scenario_changes,
                       scenario_affected_nodes=scenario_affected_nodes)
        rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                                        active_rdc, wk_num)
        pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
        if len(pdc_policy):
            policy = pd.concat([policy, pdc_policy], ignore_index=True)
        policy["period"] = target_period
        all_weeks.append(policy[["period", "dc_id", "sku_id", "safety_stock",
                                  "reorder_point", "order_up_to_level"]])
        if wk_num % 10 == 0 or wk_num == forward_weeks[-1]:
            print(f"  {target_period}: {len(policy):,} SKU x DC lines")

    rolling = pd.concat(all_weeks, ignore_index=True)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rolling.to_csv(out_path, index=False)
    print(f"Wrote {len(rolling):,} rows -> {out_path}")
    return out_path
