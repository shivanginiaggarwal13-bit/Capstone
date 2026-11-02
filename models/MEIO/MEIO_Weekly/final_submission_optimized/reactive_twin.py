"""
Supply Brain -- Reactive arm: MEIO re-plans every week from what the twin has simulated
========================================================================================

The scenario's `prepared` arm sizes all 52 weeks up front, as of the last real week, with
the scenario known in advance; `unprepared` keeps today's policy. Neither ever sees what
happens during the simulated year. This arm closes the loop:

    week 1        today's policy (the unprepared arm's week-1 plan, so the arms start equal)
    week w >= 2   before the week's review, MEIO re-sizes week w as of week w-1, having
                  1. appended every simulated week so far to its demand history -- the
                     trailing bias factor (actual / forecast, 13 weeks), the blended CV and
                     the de-seasonalised level all update from simulated demand; and
                  2. compared the lead times of shipments that have ARRIVED (dispatched in the
                     last RECENT_WEEKS weeks) with the published lane times, and scaled the
                     planning lane table by that ratio where it moved beyond DEADBAND.

The scenario is a SURPRISE to the planner: its plan-side overrides are not applied --
MEIO learns about the dairy decline and the slower lanes only from what it observes. The
twin applies the actual side exactly as for the other arms, with the same seed, so demand,
lead-time and production draws are identical and the difference is the policy.

    python reactive_twin.py --scenario-config "output datasets/overrides/<SCENARIO>/scenario_config.json"

Writes output datasets/<SCENARIO>/reactive/ (the same files as any twin run) plus
reactive_policy.parquet (every week's plan) and reactive_log.csv (what MEIO saw and learned
each week), and the override receipt twin_reactive.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                         # noqa: E402
import meio_weekly_lib as lib                # noqa: E402
import meio_optimizer_weekly as weekly       # noqa: E402
import meio_parameters                       # noqa: E402
import digital_twin_fast as tw               # noqa: E402
import scenario                              # noqa: E402
import overrides as override_engine          # noqa: E402

RECENT_WEEKS = 2       # lead-time evidence: shipments dispatched in the last two weeks that have arrived
MIN_OBS = 30           # per lane, before a ratio is trusted
DEADBAND = 0.05        # ignore lead-time ratios within +-5% of the published lane
Z_SIGNIFICANT = 3.0    # ...and any shift within 3 standard errors of it (sampling noise, not a disruption)
STD_MIN_MEAN_DAYS = 1.5  # only re-estimate the std on lanes long enough for the 0.5-day floor not to bias it
POL = ["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]


def _expected_draw(m: float, s: float, floor: float = 0.5) -> tuple[float, float]:
    """Mean and std of the twin's lane draw max(floor, N(m, max(s, 0.05))), so a lane that
    is running exactly to its published time reads as ratio 1.0 even near the floor."""
    s = max(s, 0.05)
    a = (floor - m) / s
    pa, fa = norm.cdf(a), norm.pdf(a)
    mean = floor * pa + m * (1 - pa) + s * fa
    ex2 = floor ** 2 * pa + (m ** 2 + s ** 2) * (1 - pa) + s * (m + floor) * fa
    return mean, float(np.sqrt(max(ex2 - mean ** 2, 1e-9)))


class LeadTimeLearner:
    """Turns arrived shipments into a planning lane table (or None when nothing moved)."""

    def __init__(self, lanes: pl.DataFrame, node_idx: dict, plant_pdc: dict):
        self.lanes = lanes
        self.keys = []                                     # twin (origin, dest) key per lane row
        for o, d in zip(lanes["origin_node_id"].to_list(), lanes["destination_node_id"].to_list()):
            dn = node_idx.get(d)
            self.keys.append(None if dn is None else (node_idx.get(o, -1), dn))
        self.base_m = lanes["lead_time_mean_days"].cast(pl.Float64).to_numpy()
        self.base_s = lanes["lead_time_std_days"].cast(pl.Float64).to_numpy()
        self.expect = [_expected_draw(m, s) for m, s in zip(self.base_m, self.base_s)]
        self.settle = {k: int(np.ceil(m + 3 * max(s, 0.05))) + 1
                       for k, m, s in zip(self.keys, self.base_m, self.base_s) if k is not None}

    def table(self, rcpt: list, today: int):
        """Judge each lane only on shipments dispatched long enough ago that all of them
        should have arrived (mean + 3 std of the published lane before today). Counting the
        arrived part of more recent dispatches would keep only the fast draws -- a censored,
        downward-biased reading that flags normal long lanes as early."""
        obs: dict = {}
        for (_seq, _wk, idx, dest, origin, _q, _short, _prom, lts, day, arrd) in rcpt:
            key = (int(origin), int(dest))
            settle = self.settle.get(key)
            if settle is None:
                continue
            hi = today - settle
            if day > hi or day < hi - RECENT_WEEKS * tw.DAYS_PER_WEEK:
                continue
            arrived = lts[np.asarray(arrd) < today]       # delivered before today's review
            if len(arrived):
                obs.setdefault(key, []).append(np.asarray(arrived, dtype=float))
        m_new, s_new = self.base_m.copy(), self.base_s.copy()
        changed, ratios = 0, []
        for i, key in enumerate(self.keys):
            if key is None or key not in obs:
                continue
            x = np.concatenate(obs[key])
            if len(x) < MIN_OBS:
                continue
            e_mean, e_std = self.expect[i]
            r = float(x.mean() / e_mean)
            se = e_std / np.sqrt(len(x))
            if abs(r - 1.0) <= DEADBAND or abs(x.mean() - e_mean) <= Z_SIGNIFICANT * se:
                continue
            r = float(np.clip(r, 0.5, 3.0))
            m_new[i] = max(0.5, self.base_m[i] * r)
            if self.base_m[i] >= STD_MIN_MEAN_DAYS and len(x) > 1:
                rs = float(np.clip(x.std(ddof=1) / e_std, 0.5, 3.0))
                s_new[i] = self.base_s[i] * rs
            changed += 1
            ratios.append(r)
        if not changed:
            return None, 0, 1.0
        out = self.lanes.with_columns(pl.Series("lead_time_mean_days", m_new),
                                      pl.Series("lead_time_std_days", s_new))
        return out, changed, float(np.mean(ratios))


def _week_arrays(policy: pl.DataFrame, inp, prev):
    """This week's (ss, rop, oul) arrays: the new plan where it has a row for an active
    pair, last week's value everywhere else (the twin's own carry-forward rule)."""
    ssk, rop, oul = (a.copy() for a in prev)
    ni = tw._index(policy["dc_id"], inp.node_idx)
    si = tw._index(policy["sku_id"], inp.sku_idx)
    ok = (ni >= 0) & (si >= 0)
    ok &= inp.active[np.where(ni >= 0, ni, 0), np.where(si >= 0, si, 0)]
    i, j = ni[ok], si[ok]
    ssk[i, j] = policy["safety_stock"].cast(pl.Float64).to_numpy()[ok]
    rop[i, j] = policy["reorder_point"].cast(pl.Float64).to_numpy()[ok]
    oul[i, j] = policy["order_up_to_level"].cast(pl.Float64).to_numpy()[ok]
    return ssk, rop, oul


def run(scenario_config: str, policy: str | None = None, seed: int | None = None, run_name: str = "reactive",
        verbose: bool = True) -> dict:
    t0 = time.time()
    args, config_path, config = tw.resolve_args(run_name=run_name, mode="forward", seed=seed, policy=policy,
                                                scenario_config=scenario_config)
    data_dir, out_root = Path(args.data_dir), Path(args.out_root)
    meio_data = str(scenario.resolve_path(config_path, config, "meio", "data_dir"))
    scenario_obj, run_dir = tw.load_scenario_config(Path(args.scenario_config), out_root)
    run_dir = run_dir / args.run_name
    scenario_obj = tw._scenario_execution(scenario_obj, args, run_dir)

    full = override_engine.load(config, data_dir=data_dir, state_csv=args.state)
    actual = full.side("actual", f"twin_{args.run_name}") if full else None
    if verbose:
        n_ov = len(full) if full else 0
        print(f"Reactive arm -- '{args.run_name}': twin applies {len(actual) if actual else 0} actual-side "
              f"override(s) of {n_ov}; MEIO is told none of them and re-plans weekly from what it observes")

    # ---- twin, built exactly as simulate() builds it
    inp, notes, rng_lt, rng_prod = tw.build_inputs(data_dir, mode="forward", seed=args.seed, policy_csv=Path(args.policy),
                                                   state_csv=Path(args.state) if args.state else None,
                                                   overrides=actual or None)
    if inp.safety_stock.ndim == 2:                       # a static policy: give it a week axis to rewrite
        W = len(inp.periods)
        inp.safety_stock, inp.reorder_point, inp.order_up_to = (np.repeat(a[None], W, axis=0) for a in
                                                                (inp.safety_stock, inp.reorder_point, inp.order_up_to))
    twin = tw.SupplyBrainTwin(inp, rng_lt, rng_prod)

    # ---- MEIO, built as roll_forward_policy builds it, minus the scenario's plan side
    params = meio_parameters.resolve(config)
    rng = np.random.default_rng(params.seed)
    dfc = lib.load_demand_forecast(meio_data)
    period_index = dfc.period_index
    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(meio_data, active_rdc, active_cdc)
    base_leadtime = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(meio_data)
    seasonal_index = lib.build_seasonal_index(meio_data)
    sku_subcat = lib.load_sku_subcategory(meio_data)
    lanes = rt.read_table(f"{meio_data}/network_lanes.parquet")
    plant_dc = rt.read_table(f"{meio_data}/plant_dc_master.parquet")
    learner = LeadTimeLearner(lanes, inp.node_idx, dict(zip(plant_dc["plant_id"].to_list(),
                                                            plant_dc["plant_dc_id"].to_list())))

    # panel pair -> twin cell, for writing simulated demand back as history
    p_ni = tw._index(pl.Series(dfc.pair_dc.tolist(), dtype=pl.String), inp.node_idx)
    p_si = tw._index(pl.Series(dfc.pair_sku.tolist(), dtype=pl.String), inp.sku_idx)
    p_ok = (p_ni >= 0) & (p_si >= 0)
    dairy = np.array([sku_subcat.get(s) in ("Fresh Dairy", "Powdered Milk") for s in dfc.pair_sku])

    T, n_weeks = inp.horizon_days, len(inp.periods)
    plans, log = [], []
    a0 = inp.safety_stock[0], inp.reorder_point[0], inp.order_up_to[0]
    plans.append(pl.DataFrame({"period": [inp.periods[0]] * len(twin._ai),
                               "dc_id": tw._gather(inp.nodes, twin._ai), "sku_id": tw._gather(inp.skus, twin._aj),
                               "safety_stock": a0[0][twin._ai, twin._aj], "reorder_point": a0[1][twin._ai, twin._aj],
                               "order_up_to_level": a0[2][twin._ai, twin._aj]}))
    log.append({"period": inp.periods[0], "source": "today's policy (as unprepared)", "lanes_adjusted": 0,
                "mean_lane_ratio": 1.0, "dairy_bias_factor": None, "planned_safety_stock_units":
                float(a0[0][twin._ai, twin._aj].sum()), "seconds": 0.0})

    for day in range(T):
        w = day // tw.DAYS_PER_WEEK
        if day % tw.DAYS_PER_WEEK == tw.REVIEW_DAY and w >= 1:
            t_w = time.time()
            as_of = dfc.week_of[inp.periods[w - 1]]
            target = as_of + 1
            lane_tab, n_adj, mean_ratio = learner.table(twin._rcpt, day)
            week_lt = base_leadtime
            if lane_tab is not None:
                r_lt, c_lt, _ = lib.build_leadtime_graph(meio_data, active_rdc, active_cdc, lane_tab)
                week_lt = {**r_lt, **c_lt}
            pol = weekly.size_rdc_cdc(params, dfc, as_of, period_index, seasonal_index, sku_subcat, week_lt, cost,
                                      active_rdc, active_cdc, cdc_primary_rdc, target, None, rng)
            rdc_sm = lib.smoothed_rdc_demand_for_pdc(dfc, as_of, period_index, seasonal_index, sku_subcat,
                                                     active_rdc, target)
            pdc = lib.solve_pdc_policy(rdc_sm, meio_data, cost, period=inp.periods[w], lanes=lane_tab,
                                       z_by_sku=params.z_by_sku or None)
            if pdc.height:
                pol = pl.concat([pol, pdc], how="diagonal_relaxed")
            pol = pol.select(POL)
            prev = inp.safety_stock[w - 1], inp.reorder_point[w - 1], inp.order_up_to[w - 1]
            inp.safety_stock[w], inp.reorder_point[w], inp.order_up_to[w] = _week_arrays(pol, inp, prev)
            plans.append(pol.with_columns(pl.lit(inp.periods[w]).alias("period")).select(["period"] + POL))
            bf = dfc.trailing(as_of, seasonal_index, sku_subcat)[0]
            log.append({"period": inp.periods[w], "source": f"re-planned as of {inp.periods[w - 1]}",
                        "lanes_adjusted": n_adj, "mean_lane_ratio": round(mean_ratio, 4),
                        "dairy_bias_factor": round(float(np.nanmean(bf[dairy])), 4),
                        "planned_safety_stock_units": float(inp.safety_stock[w][twin._ai, twin._aj].sum()),
                        "seconds": round(time.time() - t_w, 1)})
            if verbose and (w % 4 == 0 or n_adj):
                print(f"  {inp.periods[w]}: re-planned ({log[-1]['seconds']}s)  lanes adjusted {n_adj}"
                      f"{f' (mean ratio {mean_ratio:.3f})' if n_adj else ''}  dairy bias "
                      f"{log[-1]['dairy_bias_factor']}", flush=True)
        twin.step(day)
        if (day + 1) % tw.DAYS_PER_WEEK == 0:
            pw = dfc.week_of[inp.periods[w]]              # this simulated week becomes history
            dem = twin.wk_dem_cust
            dfc.actual[p_ok, pw] = dem[p_ni[p_ok], p_si[p_ok]]
            dfc.in_sample[pw] = True
            dfc.horizon[pw] = "in_sample"
            dfc._trailing.clear()
            dfc._weeks.clear()
            twin._close_week(w)

    inv, rcpt, prod, arrays = twin._collect()
    k, _svc, _costs = tw.write_run(run_dir, inv, rcpt, prod, twin, inp, notes, args, t0, scenario_obj, arrays)
    rt.write_table(pl.concat(plans, how="vertical_relaxed"), run_dir / "reactive_policy.parquet")
    rt.write_table(pl.DataFrame(log), run_dir / "reactive_log.csv")
    if full:
        receipt = override_engine.write_receipts(scenario.overrides_dir(config_path, config),
                                                 f"twin_{args.run_name}", actual, full)
        if verbose:
            print(f"  override receipts: {receipt}")
    secs = round(time.time() - t0, 1)
    if verbose:
        tw.print_kpis(k, f"{args.run_name} (MEIO re-plans weekly from simulated history)", run_dir, secs)
    return {"kpis": k, "run_dir": str(run_dir), "seconds": secs}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario-config", required=True)
    ap.add_argument("--policy", default=None, help="week-1 policy (default: the config's digital_twin.policy)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--run-name", default="reactive")
    a = ap.parse_args()
    run(a.scenario_config, a.policy, a.seed, a.run_name)


if __name__ == "__main__":
    main()
