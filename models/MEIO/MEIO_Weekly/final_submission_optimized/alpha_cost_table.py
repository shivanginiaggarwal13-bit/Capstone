"""
Weekly alpha cost/fill table -- for a planner's judgment call on pooling.

Pooling (higher alpha) isn't a free lunch: it trades stockout cost for more
transport cost (see final_submission/README.md, "Known limitations" and the
2026-10-18 real-data resweep). A single net "total landed cost" number hides
that trade. This script breaks it out, for the CURRENT as-of week specifically
-- same as_of_week resolution as meio_optimizer_weekly.py -- so a planner
reviewing this week's recommendation can see what each alpha actually costs
on each axis, not just which one nets out lowest.

Method: builds a rolling policy at each candidate alpha, sharing one Monte
Carlo stream (clark_scarf_v2's WeekPlan/Endpoints split -- the expensive part
is alpha-independent, so this is cheap: same trick netting_discount_sweep.py
and the research sweeps use), over a short forward horizon (default 4 weeks,
not the research track's full 52-week synthetic basis). Each alpha's
resulting policy is then run through the fast twin (forward mode, synthetic
demand) to get REAL simulated holding/stockout/transport costs -- not a
formula estimate, which can't see transport cost at all (it only emerges
from the twin's simulated replenishment behavior).

    python alpha_cost_table.py                        # current as-of week, 4-week horizon
    python alpha_cost_table.py --as-of-week 2025-W52 --horizon-weeks 4
    python alpha_cost_table.py --alphas 0,0.2,0.4,0.6,0.8,1.0
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                     # noqa: E402
import meio_weekly_lib as lib            # noqa: E402
import clark_scarf_v2 as csv2            # noqa: E402
import policy_builds as pb               # noqa: E402
import digital_twin_fast as twin         # noqa: E402
import meio_parameters                   # noqa: E402
import scenario                          # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = str(rt.REPO_ROOT)
DEFAULT_DATA = os.path.join(REPO_ROOT, "input datasets")
SCENARIO_CONFIG = os.path.join(REPO_ROOT, "scenario_config.json")

HORIZON_WEEKS_DEFAULT = 4
ALPHAS_DEFAULT = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def _tag(alpha: float) -> str:
    return f"a{alpha:.1f}".replace(".", "")


def run(as_of_week: str | None = None, horizon_weeks: int = HORIZON_WEEKS_DEFAULT,
       alphas: list[float] = ALPHAS_DEFAULT, data_dir: str = DEFAULT_DATA, seed: int | None = None) -> pl.DataFrame:
    config = scenario.load_config(Path(SCENARIO_CONFIG))
    params = meio_parameters.resolve(config)
    if params.model != "clark_scarf_v2":
        raise SystemExit(f"meio.parameters currently resolves to model={params.model!r}, not clark_scarf_v2 -- "
                         f"this table only makes sense for the alpha-pooling model. Nothing to compare.")
    seed = seed if seed is not None else params.seed

    dfc = lib.load_demand_forecast(data_dir)
    if as_of_week is None:
        has_actual = [p for p, h in zip(dfc.periods, dfc.horizon) if h == "in_sample"]
        as_of_week = has_actual[-1]

    out_dir = os.path.join(SCRIPT_DIR, "alpha_cost_table_runs", as_of_week.replace("-", "_"))
    os.makedirs(out_dir, exist_ok=True)

    variants = {_tag(a): (csv2.Variant(a, params.netting_discount),
                          os.path.join(out_dir, f"policy_{_tag(a)}.parquet")) for a in alphas}
    rng = np.random.default_rng(seed)
    res = pb.rolling_policies(data_dir, as_of_week, variants, rng, params.n_sims,
                              max_weeks=horizon_weeks, log=print)

    rows = []
    for a in alphas:
        tag = _tag(a)
        kpi = twin.simulate(run_name=f"alpha_cost_{tag}", mode="forward", seed=seed, horizon_weeks=horizon_weeks,
                            policy=res["paths"][tag], scenario_config=SCENARIO_CONFIG, data_dir=data_dir,
                            out_root=os.path.join(out_dir, "twin_runs"), lean=True)["kpis"]
        rows.append({
            "alpha": a,
            "holding_cost_usd": kpi["holding_cost_usd"],
            "stockout_cost_usd": kpi["stockout_cost_usd"],
            "transport_cost_usd": kpi["transport_cost_usd"],
            "total_landed_cost_usd": kpi["total_landed_cost_usd"],
            "customer_fill_rate": kpi["customer_fill_rate"],
            "internal_replen_fill_rate": kpi["internal_replen_fill_rate"],
        })
    table = pl.DataFrame(rows)
    out_csv = os.path.join(out_dir, "alpha_cost_table.csv")
    rt.write_table(table, out_csv)
    print(f"\nAs-of week: {as_of_week}  (projecting {horizon_weeks} week(s) forward, netting_discount="
         f"{params.netting_discount:g}, seed={seed})")
    print(rt.show(table))
    print(f"\nWrote {out_csv}")
    print("\nRead this as: holding stays flat, stockout cost falls and transport cost rises as alpha "
         "increases. The total column nets them against each other -- don't read it alone.")
    return table


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of-week", default=None, help="Default: the latest week with actual data (same as "
                    "meio_optimizer_weekly.py's own default).")
    ap.add_argument("--horizon-weeks", type=int, default=HORIZON_WEEKS_DEFAULT)
    ap.add_argument("--alphas", default=None, help="Comma-separated, e.g. 0,0.2,0.4,0.6,0.8,1.0")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    alphas = [float(a) for a in args.alphas.split(",")] if args.alphas else ALPHAS_DEFAULT
    run(as_of_week=args.as_of_week, horizon_weeks=args.horizon_weeks, alphas=alphas, seed=args.seed)


if __name__ == "__main__":
    main()
