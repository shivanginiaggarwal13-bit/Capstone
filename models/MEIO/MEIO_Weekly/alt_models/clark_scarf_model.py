"""
Alt MEIO Model 1 — Clark-Scarf Echelon Stock Decomposition
==============================================================

The classical two-echelon inventory model (Clark & Scarf, 1960), extended
here across three levels (RDC -> CDC, with PDC left as-is via
`meio_weekly_lib.solve_pdc_policy`). Where the main tool
(`meio_weekly_lib.solve_policy`) deliberately does NOT pool RDC demand with
its downstream CDCs (see that module's docstring point 5 -- couldn't
confirm whether demand_forecast.csv's RDC rows already include downstream
pull, so pooling risked double-counting), this model tests the OTHER
answer to that same open question: pool correctly, with the echelon
machinery that's supposed to prevent double-counting.

Method
------
1. CDC (base echelon): ordinary local newsvendor, identical to
   `solve_policy()` -- this doesn't change; Clark-Scarf's decomposition
   only changes the level above the base.
2. RDC (echelon): its ECHELON inventory position is defined as "everything
   in the RDC-CDC subsystem" -- RDC's own stock + every downstream CDC's
   stock. Demand pooled via King's formula (sum of variances, sqrt):
       mu_echelon    = mu_RDC_own + sum(mu_CDC)
       sigma_echelon = sqrt(sigma_RDC_own^2 + sum(sigma_CDC^2))
   sized against RDC's OWN replenishment lead time (from PDC) -- the
   subsystem is protected as a single newsvendor over that lead time.
3. RDC's LOCAL target (what it itself physically holds) is the echelon
   target MINUS what's already downstream at the CDCs:
       local_safety_stock = max(0, echelon_safety_stock - sum(CDC safety_stock))
       local_order_up_to  = max(local_reorder_point, echelon_order_up_to - sum(CDC order_up_to))
   This is the mechanism that prevents double-counting: RDC only carries
   the INCREMENTAL buffer the CDCs don't already provide for themselves.

Honest scope note: full Clark-Scarf also charges each echelon only its
INCREMENTAL holding cost rate (echelon holding cost = local rate minus the
downstream rate, since downstream units are already costed once at the
CDC's own rate) via a nested newsvendor recursion with induced penalty
functions. That exact recursion is not implemented here -- this script
does the echelon STOCK decomposition (the double-counting fix) without the
full COST-minimizing recursion. Documented, not glossed over.

Output: alt_output/clark_scarf_policy.csv
    (dc_id, sku_id, safety_stock, reorder_point, order_up_to_level)
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import meio_weekly_lib as lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def solve(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
          cost, active_rdc, active_cdc, cdc_primary_rdc, target_week_num):
    demand = lib.target_week_demand(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                     target_week_num=target_week_num)

    # --- CDC base echelon: identical to the main tool's local newsvendor ---
    cdc = demand[demand.dc_id.isin(active_cdc)].copy()
    cdc["lt_mean_wk"] = cdc.dc_id.map(lambda x: leadtime_by_dc[x][0] / 7.0)
    cdc["lt_std_wk"] = cdc.dc_id.map(lambda x: np.sqrt(leadtime_by_dc[x][1]) / 7.0)
    cdc["sigma_uplift"] = lib.SIGMA_UPLIFT["CDC"]
    cdc = cdc.merge(cost, on=["sku_id", "dc_id"], how="left")
    cdc["z"] = cdc.segment.map(lib.Z_BY_SEGMENT).fillna(lib.Z_DEFAULT)
    prot_wk = lib.REVIEW_WEEKS + cdc.lt_mean_wk
    cdc["sigma_lt_demand"] = np.sqrt(prot_wk * (cdc.target_std * cdc.sigma_uplift) ** 2
                                      + (cdc.target_mean ** 2) * cdc.lt_std_wk ** 2)
    cdc["safety_stock"] = (cdc.z * cdc.sigma_lt_demand).clip(lower=0)
    cdc["cycle_stock"] = cdc.target_mean * prot_wk
    cdc["reorder_point"] = cdc.cycle_stock + cdc.safety_stock
    cover_wk = max(lib.REVIEW_WEEKS, lib.ORDER_COVER_WEEKS)
    cdc["order_up_to_level"] = np.maximum(cdc.reorder_point + cdc.target_mean * cover_wk,
                                           cdc.reorder_point * lib.MIN_S_MULTIPLE)

    # --- RDC echelon: pool own + all downstream CDCs, then net off what's already there ---
    rdc_rows = []
    for rdc in active_rdc:
        own = demand[demand.dc_id == rdc][["sku_id", "target_mean", "target_std"]].rename(
            columns={"target_mean": "own_mean", "target_std": "own_std"})
        served = [c for c, r in cdc_primary_rdc.items() if r == rdc]
        down_cdc = cdc[cdc.dc_id.isin(served)]
        down = down_cdc.groupby("sku_id").agg(
            down_mean=("target_mean", "sum"),
            down_var=("target_std", lambda s: (s ** 2).sum()),
            down_ss=("safety_stock", "sum"),
            down_S=("order_up_to_level", "sum"),
        ).reset_index() if served else pd.DataFrame(
            columns=["sku_id", "down_mean", "down_var", "down_ss", "down_S"])
        merged = pd.merge(own, down, on="sku_id", how="outer").fillna(0.0)
        merged["dc_id"] = rdc
        rdc_rows.append(merged)
    rdc = pd.concat(rdc_rows, ignore_index=True) if rdc_rows else pd.DataFrame()

    rdc["lt_mean_wk"] = rdc.dc_id.map(lambda x: leadtime_by_dc[x][0] / 7.0)
    rdc["lt_std_wk"] = rdc.dc_id.map(lambda x: np.sqrt(leadtime_by_dc[x][1]) / 7.0)
    rdc = rdc.merge(cost, on=["sku_id", "dc_id"], how="left")
    rdc["z"] = rdc.segment.map(lib.Z_BY_SEGMENT).fillna(lib.Z_DEFAULT)

    mu_echelon = rdc.own_mean + rdc.down_mean
    sigma_echelon = np.sqrt((rdc.own_std * lib.SIGMA_UPLIFT["RDC"]) ** 2 + rdc.down_var)
    prot_wk_rdc = lib.REVIEW_WEEKS + rdc.lt_mean_wk
    sigma_lt_echelon = np.sqrt(prot_wk_rdc * sigma_echelon ** 2 + (mu_echelon ** 2) * rdc.lt_std_wk ** 2)
    echelon_ss = (rdc.z * sigma_lt_echelon).clip(lower=0)
    echelon_cycle = mu_echelon * prot_wk_rdc
    echelon_rop = echelon_cycle + echelon_ss
    echelon_S = np.maximum(echelon_rop + mu_echelon * cover_wk, echelon_rop * lib.MIN_S_MULTIPLE)

    # net off what's already downstream -- this is the double-counting fix
    rdc["safety_stock"] = np.maximum(0.0, echelon_ss - rdc.down_ss)
    rdc["cycle_stock"] = rdc.own_mean * prot_wk_rdc  # RDC's own direct-demand cycle stock only
    rdc["reorder_point"] = rdc.cycle_stock + rdc.safety_stock
    rdc["order_up_to_level"] = np.maximum(rdc.reorder_point,
                                           echelon_S - rdc.down_S).clip(lower=rdc.reorder_point)
    rdc["target_csl"] = norm.cdf(rdc.z.clip(lib.Z_MIN, lib.Z_MAX))

    cdc["target_csl"] = norm.cdf(cdc.z.clip(lib.Z_MIN, lib.Z_MAX))
    keep = ["dc_id", "sku_id", "category", "subcategory", "segment", "safety_stock",
            "reorder_point", "order_up_to_level", "target_csl"]
    out = pd.concat([cdc[keep], rdc[keep]], ignore_index=True)
    for c in ["safety_stock", "reorder_point", "order_up_to_level"]:
        out[c] = out[c].round(1)
    return out


def main(as_of_week="2025-W52", data_dir=None):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)
    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)

    as_of_num = int(period_index.loc[period_index.period == as_of_week, "week_num"].iloc[0])
    target_num = as_of_num + 1
    policy = solve(dfc, as_of_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
                   cost, active_rdc, active_cdc, cdc_primary_rdc, target_num)

    # append PDC via the main tool's existing (unchanged) push-ceiling model
    rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                                    active_rdc, target_num)
    pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
    if len(pdc_policy):
        policy = pd.concat([policy, pdc_policy[["dc_id", "sku_id", "category", "subcategory", "segment",
                                                  "safety_stock", "reorder_point", "order_up_to_level",
                                                  "target_csl"]]], ignore_index=True)

    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    policy.to_csv(os.path.join(out_dir, "clark_scarf_recommendations.csv"), index=False)
    policy[["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]].to_csv(
        os.path.join(out_dir, "clark_scarf_policy.csv"), index=False)
    print(f"Clark-Scarf: {len(policy):,} rows -> {out_dir}/clark_scarf_policy.csv  "
          f"total SS {policy.safety_stock.sum():,.0f} u")
    return os.path.join(out_dir, "clark_scarf_policy.csv")


if __name__ == "__main__":
    main()
