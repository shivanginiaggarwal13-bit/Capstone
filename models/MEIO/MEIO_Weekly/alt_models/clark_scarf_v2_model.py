"""
Alt Model 1, v2 — Clark-Scarf with a Continuous Pooling Factor + Monte Carlo DDLT
=====================================================================================

Two upgrades over `clark_scarf_model.py`, both aimed at the two gaps found
when comparing this project's work against the Accenture "FY27 Brainstorm"
slide (see README "vs Accenture"):

1. **Continuous pooling factor alpha**, not the all-or-nothing choice v1
   made (and not GSM's binary stock/no-stock heuristic either). Alpha
   blends RDC's LOCAL safety stock between two endpoints we already
   validated separately:
       alpha=0: no pooling at all (meio_weekly_lib.solve_policy's answer --
                RDC sized independently against its own direct demand)
       alpha=1: full Clark-Scarf pooling (v1's answer -- RDC's local SS =
                echelon-pooled SS netted against what CDCs already hold)
       RDC_local_SS(alpha) = alpha * pooled_netted_SS + (1-alpha) * independent_SS
   This blend is linear in alpha by construction, which would make a
   closed-form cost-formula optimization degenerate to a corner (0 or 1)
   -- not useful. Instead alpha is found the same way v3 (sim-optimize)
   found its parameters: grid search, twin as reward oracle. The twin's
   OWN stockout-cost/fill-rate response to alpha is genuinely nonlinear
   even though the safety-stock formula blend is linear, so this is the
   scientifically honest way to search it, not a shortcut.

2. **Monte Carlo demand-during-lead-time**, replacing every closed-form
   `z * sigma` safety-stock calculation in this model (and only this
   model -- meio_weekly_lib's solve_policy is untouched). Demand is drawn
   from a Gamma distribution fit to (target_mean, target_std) by moment
   matching -- Gamma is defined only for x >= 0 and right-skewed, which
   fits actual demand shape far better than a Normal (a Normal technically
   allows negative demand, silently clipped to 0 everywhere else in this
   project; more importantly its symmetric tails misprice the high
   percentiles -- 95-99% CSL -- that this project's z-based sizing lives
   at). Lead time is ALSO drawn from a Gamma fit to (lt_mean_wk,
   lt_std_wk), so each Monte Carlo trial samples a genuinely different
   protection interval, not just a fixed mean with a bolted-on variance
   correction term the way the closed form does. Reorder point = the
   target-CSL percentile of the simulated DDLT distribution;
   safety_stock = that percentile minus the simulated mean.

Honest scope: applies Monte Carlo + continuous alpha to RDC/CDC sizing
only; PDC still uses meio_weekly_lib.solve_pdc_policy's existing
closed-form push-ceiling logic unchanged (that formula isn't at stake
here). Alpha search runs on the frozen single-week basis (as-of 2025-W52,
target 2026-W01), matching v1 Clark-Scarf's scope, for speed.

Output: alt_output/clark_scarf_v2_policy.csv, alt_output/clark_scarf_v2_alpha_search.csv
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import meio_weekly_lib as lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
TWIN = os.path.join(REPO_ROOT, "models", "twin", "digital_twin.py")

N_SIMS = 4000


def _gamma_params(mean, std):
    """Method-of-moments Gamma(shape, scale) from (mean, std), vectorized.
    Falls back to a point mass at `mean` (shape huge, scale tiny) wherever
    std <= 0 or mean <= 0, so callers never see NaN/inf."""
    mean = np.asarray(mean, dtype=float)
    std = np.asarray(std, dtype=float)
    safe = (mean > 1e-9) & (std > 1e-9)
    shape = np.where(safe, (mean / np.maximum(std, 1e-9)) ** 2, 1e6)
    scale = np.where(safe, (std ** 2) / np.maximum(mean, 1e-9), np.maximum(mean, 1e-9) / 1e6)
    return shape, scale


def monte_carlo_ddlt(target_mean: np.ndarray, target_std: np.ndarray,
                      lt_mean_wk: np.ndarray, lt_std_wk: np.ndarray,
                      target_csl: np.ndarray, rng: np.random.Generator,
                      n_sims: int = N_SIMS):
    """Vectorized Monte Carlo demand-during-lead-time for every (sku, dc)
    line at once. Returns (reorder_point, safety_stock, cycle_stock) arrays,
    one value per input line.

    Two-stage draw per trial, per line:
      1. protection interval P_j = REVIEW_WEEKS + L_j,  L_j ~ Gamma(lt_mean_wk, lt_std_wk)
      2. DDLT_j ~ Gamma(mean = target_mean * P_j, std = target_std * sqrt(P_j))
    reorder_point = the target_csl-th percentile of DDLT_j across trials;
    safety_stock = reorder_point - mean(DDLT_j); cycle_stock = mean(DDLT_j).
    """
    n = len(target_mean)
    lt_shape, lt_scale = _gamma_params(lt_mean_wk, lt_std_wk)
    # [n_sims, n] lead-time draws
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
          n_sims: int = N_SIMS):
    demand = lib.target_week_demand(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                     target_week_num=target_week_num)

    # --- CDC base echelon: Monte Carlo DDLT instead of z*sigma ---
    cdc = demand[demand.dc_id.isin(active_cdc)].copy()
    cdc["lt_mean_wk"] = cdc.dc_id.map(lambda x: leadtime_by_dc[x][0] / 7.0)
    cdc["lt_std_wk"] = cdc.dc_id.map(lambda x: np.sqrt(leadtime_by_dc[x][1]) / 7.0)
    cdc = cdc.merge(cost, on=["sku_id", "dc_id"], how="left")
    cdc["z"] = cdc.segment.map(lib.Z_BY_SEGMENT).fillna(lib.Z_DEFAULT)
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
    rdc["z"] = rdc.segment.map(lib.Z_BY_SEGMENT).fillna(lib.Z_DEFAULT)
    rdc["target_csl"] = norm.cdf(rdc.z.clip(lib.Z_MIN, lib.Z_MAX))

    # endpoint A: independent (alpha=0) -- RDC sized on its own direct demand only
    rop_i, ss_i, cyc_i = monte_carlo_ddlt(
        rdc.own_mean.to_numpy(), (rdc.own_std * lib.SIGMA_UPLIFT["RDC"]).to_numpy(),
        rdc.lt_mean_wk.to_numpy(), rdc.lt_std_wk.to_numpy(), rdc.target_csl.to_numpy(), rng, n_sims=n_sims)

    # endpoint B: fully pooled (alpha=1) -- echelon variance, netted against downstream CDC SS
    mu_echelon = rdc.own_mean + rdc.down_mean
    sigma_echelon = np.sqrt((rdc.own_std * lib.SIGMA_UPLIFT["RDC"]) ** 2 + rdc.down_var)
    rop_p, ss_p_raw, cyc_p = monte_carlo_ddlt(
        mu_echelon.to_numpy(), sigma_echelon.to_numpy(),
        rdc.lt_mean_wk.to_numpy(), rdc.lt_std_wk.to_numpy(), rdc.target_csl.to_numpy(), rng, n_sims=n_sims)
    ss_p = np.maximum(0.0, ss_p_raw - rdc.down_ss.to_numpy())  # netted against downstream CDC SS

    rdc["safety_stock"] = alpha * ss_p + (1 - alpha) * ss_i
    rdc["cycle_stock"] = alpha * cyc_p + (1 - alpha) * cyc_i
    rdc["reorder_point"] = rdc.cycle_stock + rdc.safety_stock
    cover_wk = max(lib.REVIEW_WEEKS, lib.ORDER_COVER_WEEKS)
    s_endpoint_p = np.maximum(rdc.reorder_point + mu_echelon * cover_wk - rdc.down_S.to_numpy(), rdc.reorder_point)
    s_endpoint_i = np.maximum(rdc.reorder_point + rdc.own_mean * cover_wk, rdc.reorder_point * lib.MIN_S_MULTIPLE)
    rdc["order_up_to_level"] = alpha * s_endpoint_p + (1 - alpha) * s_endpoint_i

    keep = ["dc_id", "sku_id", "category", "subcategory", "segment", "safety_stock",
            "reorder_point", "order_up_to_level", "target_csl"]
    out = pd.concat([cdc[keep], rdc[keep]], ignore_index=True)
    for c in ["safety_stock", "reorder_point", "order_up_to_level"]:
        out[c] = out[c].round(1)
    return out


def build_full_policy(data_dir, as_of_week, alpha, seed=2026, n_sims=N_SIMS, rng=None,
                       _shared=None):
    """`_shared`: optional pre-loaded (dfc, period_index, seasonal_index, sku_subcat,
    active_rdc, active_cdc, leadtime_by_dc, cost, cdc_primary_rdc) tuple, so a
    caller looping over many weeks/alphas doesn't reload the same CSVs every
    time. `rng`: pass one in to share a single Generator across many calls
    (e.g. across weeks in a rolling build) instead of reseeding each time."""
    if rng is None:
        rng = np.random.default_rng(seed)
    if _shared is None:
        dfc = lib.load_demand_forecast(data_dir)
        period_index = lib.load_period_index(data_dir)
        seasonal_index = lib.build_seasonal_index(data_dir)
        sku_subcat = lib.load_sku_subcategory(data_dir)
        active_rdc, active_cdc = lib.active_nodes(dfc)
        rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
        leadtime_by_dc = {**rdc_lt, **cdc_lt}
        cost = lib.load_sku_cost(data_dir)
    else:
        (dfc, period_index, seasonal_index, sku_subcat, active_rdc, active_cdc,
         leadtime_by_dc, cost, cdc_primary_rdc) = _shared

    as_of_num = int(period_index.loc[period_index.period == as_of_week, "week_num"].iloc[0])
    target_num = as_of_num + 1
    policy = solve(dfc, as_of_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
                   cost, active_rdc, active_cdc, cdc_primary_rdc, target_num, alpha, rng,
                   n_sims=n_sims)

    rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, period_index, seasonal_index, sku_subcat,
                                                    active_rdc, target_num)
    pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost)
    if len(pdc_policy):
        policy = pd.concat([policy, pdc_policy[["dc_id", "sku_id", "category", "subcategory", "segment",
                                                  "safety_stock", "reorder_point", "order_up_to_level",
                                                  "target_csl"]]], ignore_index=True)
    return policy


def score(policy_csv, state_csv, run_name, out_root):
    cmd = [sys.executable, TWIN, "--mode", "forward", "--run-name", run_name,
           "--policy", policy_csv, "--state", state_csv, "--out-root", out_root, "--label", run_name]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT, capture_output=True, text=True)
    with open(os.path.join(out_root, run_name, "run_kpis.json")) as f:
        return json.load(f)


def main(as_of_week="2025-W52", data_dir=None, alphas=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0)):
    data_dir = data_dir or os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "input datasets"))
    out_dir = os.path.join(SCRIPT_DIR, "alt_output")
    os.makedirs(out_dir, exist_ok=True)
    trial_out_root = os.path.join(SCRIPT_DIR, "clark_scarf_v2_trials")
    state_csv = os.path.join(REPO_ROOT, "output datasets", "history", "opening_position.csv")

    results = []
    best = None
    for a in alphas:
        print(f"\n--- alpha={a:.1f} ---")
        policy = build_full_policy(data_dir, as_of_week, a)
        policy_cols = policy[["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]]
        trial_csv = os.path.join(trial_out_root, f"alpha_{a:.1f}_policy.csv")
        os.makedirs(trial_out_root, exist_ok=True)
        policy_cols.to_csv(trial_csv, index=False)
        run_name = f"clarkscarf_v2_alpha{a:.1f}".replace(".", "")
        kpi = score(trial_csv, state_csv, run_name, trial_out_root)
        row = {"alpha": a, "fill_rate": kpi["customer_fill_rate"],
               "total_landed_cost_usd": kpi["total_landed_cost_usd"],
               "internal_replen_fill_rate": kpi["internal_replen_fill_rate"]}
        results.append(row)
        print(f"  alpha={a:.1f}: fill {kpi['customer_fill_rate']:.2%}  cost ${kpi['total_landed_cost_usd']:,.0f}")
        if best is None or kpi["total_landed_cost_usd"] < best["total_landed_cost_usd"]:
            best = row
            policy_cols.to_csv(os.path.join(out_dir, "clark_scarf_v2_policy.csv"), index=False)
            policy.to_csv(os.path.join(out_dir, "clark_scarf_v2_recommendations.csv"), index=False)

    results_df = pd.DataFrame(results)
    results_df.to_csv(os.path.join(out_dir, "clark_scarf_v2_alpha_search.csv"), index=False)
    print(f"\nBest alpha: {best['alpha']:.1f}  (fill {best['fill_rate']:.2%}, "
          f"cost ${best['total_landed_cost_usd']:,.0f})")
    print(results_df.to_string(index=False))
    print(f"\nWrote {out_dir}/clark_scarf_v2_policy.csv, clark_scarf_v2_alpha_search.csv")
    return os.path.join(out_dir, "clark_scarf_v2_policy.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default="2025-W52")
    args = ap.parse_args()
    main(as_of_week=args.as_of_week)
