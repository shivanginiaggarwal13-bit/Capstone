"""
Alt MEIO Model 2 — Guaranteed Service Model (Graves-Willems, simplified)
============================================================================

The other classical academic MEIO paradigm, structurally different from
Clark-Scarf: instead of deciding HOW MUCH safety stock each node holds
against its own demand, GSM decides WHERE in the network stock sits at
all. Each node guarantees a service time S to its downstream customer
("you'll always have stock within S time of an order"); a node that
guarantees S=0 must hold safety stock to cover its own replenishment lead
time, and a node that guarantees S>0 can hold LESS (or zero) stock because
it's relying on its upstream node's own guarantee to cover part of the
wait. The decision is combinatorial across the network -- the real
Graves-Willems algorithm solves it with dynamic programming over the tree.

Honest scope note: exact Graves-Willems DP (handling continuous S with
piecewise-linear cost functions per node) is a substantial undertaking on
its own. This implements the SIMPLIFIED all-or-nothing variant used in
introductory GSM treatments: each node either fully stocks (S_i = 0) or
fully passes through (S_i = SI_i + T_i, no local safety stock at all),
decided by a single top-down greedy pass comparing local holding cost of
stocking against the cost of pushing that same protection downstream. This
is a real, if heuristic, GSM -- not the exact optimal DP.

Network (tree, single path per node -- verified via sku_sourcing_map.csv):
    Plant -> PDC (always stocks; root of the tree, entry point) -> RDC -> CDC

For each SKU, walk PDC -> RDC -> CDC:
    - PDC always stocks (S_PDC = 0) -- it's the network's only source of
      supply, has no upstream service guarantee to lean on.
    - RDC: incoming service time SI_RDC = S_PDC = 0. If RDC stocks,
      protection interval = T_RDC (lead time from PDC) and it guarantees
      S_RDC = 0 downstream. If RDC passes through, protection interval = 0
      locally (no stock) but S_RDC = T_RDC is pushed onto CDC. Choose
      whichever has the lower total holding-cost-weighted safety stock
      across RDC + its CDCs combined.
    - CDC: the demand-facing leaf must guarantee immediate availability to
      the actual customer (S_CDC = 0 always, by definition -- nothing
      downstream of a CDC to pass through to). Its protection interval is
      SI_CDC + T_CDC, where SI_CDC = S_RDC from the decision above.

Output: alt_output/gsm_policy.csv
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import meio_weekly_lib as lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _safety_stock(z, sigma, mu, prot_wk, lt_std_wk):
    """z/sigma/mu/etc. can be plain scalars (per-RDC path) or pandas Series
    (per-CDC-group path) -- np.maximum handles both uniformly, unlike
    .clip(lower=...) which is a pandas-only kwarg spelling."""
    sigma_lt = np.sqrt(prot_wk * sigma ** 2 + (mu ** 2) * lt_std_wk ** 2)
    return np.maximum(z * sigma_lt, 0.0)


def solve(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
          cost, active_rdc, active_cdc, cdc_primary_rdc, target_week_num):
    demand = lib.target_week_demand(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                     target_week_num=target_week_num)
    d = demand[demand.dc_id.isin(list(active_rdc) + list(active_cdc))].copy()
    d["lt_mean_wk"] = d.dc_id.map(lambda x: leadtime_by_dc[x][0] / 7.0)
    d["lt_std_wk"] = d.dc_id.map(lambda x: np.sqrt(leadtime_by_dc[x][1]) / 7.0)
    d = d.merge(cost, on=["sku_id", "dc_id"], how="left")
    d["z"] = d.segment.map(lib.Z_BY_SEGMENT).fillna(lib.Z_DEFAULT)
    d["sigma_uplift"] = d.dc_id.str[:3].map(lib.SIGMA_UPLIFT).fillna(1.0)

    rdc_d = d[d.dc_id.isin(active_rdc)].set_index(["sku_id", "dc_id"])
    cdc_d = d[d.dc_id.isin(active_cdc)].copy()
    cdc_d["rdc_id"] = cdc_d.dc_id.map(cdc_primary_rdc)

    rows = []
    for (sku, rdc), rrow in rdc_d.groupby(level=[0, 1]):
        rrow = rrow.iloc[0]
        served = cdc_d[(cdc_d.sku_id == sku) & (cdc_d.rdc_id == rdc)]
        if served.empty:
            continue

        # Option A: RDC stocks (S_RDC=0) -- RDC covers its own T_RDC, CDCs
        # each cover only their own T_CDC (SI_CDC=0).
        rdc_ss_A = _safety_stock(rrow.z, rrow.target_std * rrow.sigma_uplift, rrow.target_mean,
                                  rrow.lt_mean_wk, rrow.lt_std_wk)
        cdc_ss_A = _safety_stock(served.z, served.target_std * served.sigma_uplift, served.target_mean,
                                  served.lt_mean_wk, served.lt_std_wk)
        cost_A = rdc_ss_A * rrow.unit_cost_usd + (cdc_ss_A * served.unit_cost_usd).sum()

        # Option B: RDC passes through (S_RDC=T_RDC) -- RDC holds nothing,
        # each CDC's protection interval grows by T_RDC (SI_CDC=T_RDC).
        rdc_ss_B = 0.0
        cdc_ss_B = _safety_stock(served.z, served.target_std * served.sigma_uplift, served.target_mean,
                                  served.lt_mean_wk + rrow.lt_mean_wk, served.lt_std_wk)
        cost_B = (cdc_ss_B * served.unit_cost_usd).sum()

        stock_here = cost_A <= cost_B
        rdc_ss = rdc_ss_A if stock_here else rdc_ss_B
        cdc_ss = cdc_ss_A if stock_here else cdc_ss_B
        cdc_lt_prot = served.lt_mean_wk if stock_here else (served.lt_mean_wk + rrow.lt_mean_wk)

        rdc_cycle = rrow.target_mean * rrow.lt_mean_wk
        rdc_rop = rdc_cycle + rdc_ss
        cover_wk = max(lib.REVIEW_WEEKS, lib.ORDER_COVER_WEEKS)
        rows.append({"dc_id": rdc, "sku_id": sku, "category": rrow.category, "subcategory": rrow.subcategory,
                     "segment": rrow.segment, "safety_stock": round(rdc_ss, 1),
                     "reorder_point": round(rdc_rop, 1),
                     "order_up_to_level": round(max(rdc_rop + rrow.target_mean * cover_wk,
                                                     rdc_rop * lib.MIN_S_MULTIPLE), 1),
                     "gsm_stocks_here": bool(stock_here)})

        cdc_cycle = served.target_mean * cdc_lt_prot
        cdc_rop = cdc_cycle + cdc_ss
        cdc_S = np.maximum(cdc_rop + served.target_mean * cover_wk, cdc_rop * lib.MIN_S_MULTIPLE)
        for i, (_, crow) in enumerate(served.iterrows()):
            rows.append({"dc_id": crow.dc_id, "sku_id": sku, "category": crow.category,
                         "subcategory": crow.subcategory, "segment": crow.segment,
                         "safety_stock": round(cdc_ss.iloc[i], 1), "reorder_point": round(cdc_rop.iloc[i], 1),
                         "order_up_to_level": round(cdc_S.iloc[i], 1), "gsm_stocks_here": True})

    out = pd.DataFrame(rows)
    out["target_csl"] = np.nan
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

    rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                                    active_rdc, target_num)
    pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
    if len(pdc_policy):
        pdc_policy = pdc_policy.copy()
        pdc_policy["gsm_stocks_here"] = True
        policy = pd.concat([policy, pdc_policy[["dc_id", "sku_id", "category", "subcategory", "segment",
                                                  "safety_stock", "reorder_point", "order_up_to_level",
                                                  "target_csl", "gsm_stocks_here"]]], ignore_index=True)

    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    policy.to_csv(os.path.join(out_dir, "gsm_recommendations.csv"), index=False)
    policy[["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]].to_csv(
        os.path.join(out_dir, "gsm_policy.csv"), index=False)
    n_rdc_stock = policy[policy.dc_id.str.startswith("RDC")].gsm_stocks_here.sum()
    n_rdc_total = policy.dc_id.str.startswith("RDC").sum()
    print(f"GSM: {len(policy):,} rows -> {out_dir}/gsm_policy.csv  "
          f"RDC stocks locally in {n_rdc_stock}/{n_rdc_total} lines  "
          f"total SS {policy.safety_stock.sum():,.0f} u")
    return os.path.join(out_dir, "gsm_policy.csv")


if __name__ == "__main__":
    main()
