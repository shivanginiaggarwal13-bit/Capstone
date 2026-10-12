"""
Supply Brain -- Weekly MEIO Optimizer (optimized)
=================================================

The same tool as final_submission/meio_optimizer_weekly.py: given everything known up
to the as-of week, how much stock should each DC carry to cover next week? Same
method, same outputs, on the polars/numpy port (meio_weekly_lib, clark_scarf_v2); the
quadrature DDLT runs its bisection across CPU threads.

    python meio_optimizer_weekly.py --as-of-week 2025-W52

Outputs (under `output datasets/MEIO/<scenario>/<target_week>/`):
    meio_recommendations_weekly.parquet   SKU x DC lines
    meio_policy_weekly.parquet            dc_id,sku_id,safety_stock,reorder_point,order_up_to_level
    meio_summary_by_dc_weekly.csv     "stock to maintain" rollup per DC
Also (next to this script): meio_weekly_history.csv, chart_ss_trend.png
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                     # noqa: E402
import meio_weekly_lib as lib            # noqa: E402
import clark_scarf_v2 as csv2            # noqa: E402
import meio_parameters                   # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = str(rt.REPO_ROOT)
DEFAULT_DATA = os.path.join(REPO_ROOT, "input datasets")
DEFAULT_SCENARIO_CONFIG = os.path.join(REPO_ROOT, "scenario_config.json")
import scenario                          # noqa: E402
import overrides as override_engine      # noqa: E402

RECO_COLS = ["dc_id", "sku_id", "category", "subcategory", "target_mean", "target_std",
             "lt_mean_wk", "lt_std_wk", "target_csl", "z", "cycle_stock", "safety_stock",
             "reorder_point", "order_up_to_level", "safety_stock_usd",
             "mu_requested", "capacity_constrained"]


def plan_overrides(config_path: Path, config: dict, data_dir: str, model: str):
    """(full override set, MEIO's plan-side hooks) for the scenario, or (None, None) when it
    has no plan-side override. Shared with roll_forward_policy.py."""
    ovs = override_engine.load(config, data_dir=data_dir,
                               state_csv=scenario.resolve_path(Path(config_path), config, "digital_twin", "state"))
    if not ovs:
        if ovs is None:
            print("Legacy `changes` keys: not applied by MEIO Weekly (see models/twin/scenario_engine.py)")
        return None, None
    plan = ovs.side("plan", model)
    print(f"Override engine {override_engine.ENGINE_VERSION}: {len(plan)} plan-side override(s) of {len(ovs)}")
    return ovs, (override_engine.PlanHooks(plan) if plan else None)


def size_rdc_cdc(params, dfc, as_of_week_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
                 cost, active_rdc, active_cdc, cdc_primary_rdc, target_week_num, plan, rng) -> pl.DataFrame:
    """One target week's RDC + CDC policy with the config's MEIO model."""
    if params.model == "clark_scarf_v2":
        return csv2.solve(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
                          cost, active_rdc, active_cdc, cdc_primary_rdc,
                          target_week_num if target_week_num is not None else as_of_week_num + 1,
                          params.alpha, rng, n_sims=params.n_sims, netting_discount=params.netting_discount,
                          overrides=plan, ddlt=params.ddlt, alpha_by_sku=params.alpha_by_sku,
                          z_by_sku=params.z_by_sku)
    demand = lib.target_week_demand(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                    target_week_num=target_week_num, overrides=plan)
    return lib.solve_policy(demand, leadtime_by_dc, cost, active_rdc, active_cdc, cdc_primary_rdc, overrides=plan)


def apply_policy_hook(plan, policy: pl.DataFrame, period: str) -> pl.DataFrame:
    """PlanHooks.policy (models/overrides, pandas) on a polars policy frame."""
    if not plan:
        return policy
    return pl.from_pandas(plan.policy(policy.to_pandas(), period))


def summary_by_dc(policy: pl.DataFrame) -> pl.DataFrame:
    """groupby(dc_id).agg(...) sorted by total_safety_stock_usd descending, pandas-exact."""
    dcs = sorted(set(policy["dc_id"].to_list()))
    code = {d: i for i, d in enumerate(dcs)}
    codes = np.array([code[d] for d in policy["dc_id"].to_list()], dtype=np.int64)
    n = len(dcs)

    def s(col):
        return rt.kahan_sum(policy[col].cast(pl.Float64).to_numpy(), codes, n)[0]
    skus = [len(set(policy.filter(pl.col("dc_id") == d)["sku_id"].to_list())) for d in dcs]
    lt = policy["lt_mean_wk"].cast(pl.Float64).to_numpy()
    first = np.full(n, np.nan)
    for i in range(len(lt) - 1, -1, -1):           # first non-null per group
        if not np.isnan(lt[i]):
            first[codes[i]] = lt[i]
    by_dc = pl.DataFrame({"dc_id": dcs, "skus": skus, "total_reorder_point": s("reorder_point"),
                          "total_safety_stock": s("safety_stock"), "total_order_up_to": s("order_up_to_level"),
                          "total_safety_stock_usd": s("safety_stock_usd"), "lt_mean_wk": first})
    return by_dc[rt.pandas_argsort(by_dc["total_safety_stock_usd"].to_numpy(), ascending=False).tolist()]


def _nansum(values) -> float:
    v = np.asarray(values, dtype=float)
    return float(np.add.reduce(np.where(np.isnan(v), 0.0, v)))


def run(data_dir: str | None, as_of_week: str | None, short_window: int, long_window: int,
        scenario_config_path: str = DEFAULT_SCENARIO_CONFIG, output_root: str | None = None):
    config_path = os.path.abspath(scenario_config_path)
    config = scenario.load_config(Path(config_path))
    data_dir = data_dir or str(scenario.resolve_path(Path(config_path), config, "meio", "data_dir"))
    ovs, plan = plan_overrides(Path(config_path), config, data_dir, "meio_weekly")
    params = meio_parameters.resolve(config)
    print(params.summary())
    dfc = lib.load_demand_forecast(data_dir)
    period_index = dfc.period_index

    if as_of_week is None:
        has_actual = [p for p, h in zip(dfc.periods, dfc.horizon) if h == "in_sample"]
        as_of_week = has_actual[-1]
    if as_of_week not in dfc.week_of:
        raise SystemExit(f"--as-of-week {as_of_week!r} not found in demand_forecast.parquet periods")
    as_of_week_num = dfc.week_of[as_of_week]

    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)
    target_period = dfc.periods[as_of_week_num + 1]
    print(f"As-of week: {as_of_week}  ->  target (next) week: {target_period}")

    active_rdc, active_cdc = lib.active_nodes(dfc)
    lanes = plan.lanes(target_period) if plan else None
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc, lanes)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)

    policy = size_rdc_cdc(params, dfc, as_of_week_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
                          cost, active_rdc, active_cdc, cdc_primary_rdc, None, plan,
                          np.random.default_rng(params.seed))
    rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                                   active_rdc, as_of_week_num + 1, overrides=plan)
    pdc_policy = lib.solve_pdc_policy(rdc_smoothed, data_dir, cost, overrides=plan, period=target_period,
                                      lanes=lanes, z_by_sku=params.z_by_sku or None)
    if pdc_policy.height:
        policy = pl.concat([policy, pdc_policy], how="diagonal_relaxed")
        print(f"PDC coverage: {pdc_policy.height:,} SKU x PDC rows added ({pdc_policy['dc_id'].n_unique()} PDCs)")
    policy = apply_policy_hook(plan, policy, target_period)
    policy = policy.sort(["dc_id", "sku_id"], maintain_order=True)

    output_root = output_root or str(scenario.resolve_path(Path(config_path), config, "meio", "output_root"))
    scenario_dir = os.path.join(output_root, scenario.scenario_folder_name(config))
    run_dir = os.path.join(scenario_dir, target_period)
    os.makedirs(run_dir, exist_ok=True)
    scenario.copy_config(Path(config_path), Path(scenario_dir), config)

    reco = policy.select([pl.col(c) if c in policy.columns else pl.lit(None).alias(c) for c in RECO_COLS])
    rt.write_table(reco, os.path.join(run_dir, "meio_recommendations_weekly.parquet"))
    if params.model != "meio_weekly":
        params.write(Path(run_dir) / "meio_parameters.json")
    rt.write_table(policy.select(["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]),
                 os.path.join(run_dir, "meio_policy_weekly.parquet"))
    by_dc = summary_by_dc(policy)
    rt.write_table(by_dc, os.path.join(run_dir, "meio_summary_by_dc_weekly.csv"))

    print(f"\nWrote {policy.height:,} SKU x DC recommendation rows to {run_dir}/")
    print(f"\nStock to maintain next week ({target_period}), by DC:")
    print(rt.show(by_dc))

    total_ss_usd = _nansum(policy["safety_stock_usd"].cast(pl.Float64).to_numpy())
    if plan:
        receipt = override_engine.write_receipts(scenario.overrides_dir(Path(config_path), config), "meio_weekly",
                                                 plan.ovs, ovs)
        print(f"Override receipts: {receipt}")
    if Path(config_path).resolve() != Path(DEFAULT_SCENARIO_CONFIG).resolve():
        print(f"Total recommended safety stock for {target_period}: ${total_ss_usd:,.0f}")
        return policy
    history_path = os.path.join(SCRIPT_DIR, "meio_weekly_history.csv")
    row = pl.DataFrame([{
        "run_generated_at": datetime.now(timezone.utc).isoformat(), "as_of_week": as_of_week,
        "target_week": target_period, "short_window_weeks": short_window, "long_window_weeks": long_window,
        "sku_dc_lines": policy.height,
        "total_safety_stock_units": _nansum(policy["safety_stock"].cast(pl.Float64).to_numpy()),
        "total_safety_stock_usd": total_ss_usd,
        "total_reorder_point_units": _nansum(policy["reorder_point"].cast(pl.Float64).to_numpy()),
        "avg_target_csl": rt.nanmean_pandas(policy["target_csl"].cast(pl.Float64).to_numpy()),
    }])
    if os.path.exists(history_path):
        history = pl.concat([pl.read_csv(history_path), row], how="vertical_relaxed")
        history = history.unique(subset=["target_week"], keep="last", maintain_order=True)
    else:
        history = row
    history = history.sort("target_week", maintain_order=True)
    rt.write_table(history, history_path)
    print(f"\nAppended run to {history_path}")

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(history["target_week"].to_list(), (history["total_safety_stock_usd"] / 1000).to_list(), marker="o",
            color="#2b6cb0")
    ax.set_ylabel("Recommended safety stock ($000)")
    ax.set_xlabel("Target week")
    ax.set_title("MEIO Weekly — Recommended Safety Stock Across Runs")
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_ss_trend.png"), dpi=150)
    plt.close(fig)
    print(f"Total recommended safety stock for {target_period}: ${total_ss_usd:,.0f}")
    return policy


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default=None,
                    help="e.g. 2025-W52. Recommendation is produced for the following week. "
                         "Default: latest week with actual demand on record.")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--short-window", type=int, default=lib.SHORT_WINDOW_WEEKS)
    ap.add_argument("--long-window", type=int, default=lib.LONG_WINDOW_WEEKS)
    ap.add_argument("--scenario-config", default=DEFAULT_SCENARIO_CONFIG,
                    help="Shared root scenario JSON used for this run.")
    args = ap.parse_args()
    run(args.data_dir, args.as_of_week, args.short_window, args.long_window, args.scenario_config)


if __name__ == "__main__":
    main()
