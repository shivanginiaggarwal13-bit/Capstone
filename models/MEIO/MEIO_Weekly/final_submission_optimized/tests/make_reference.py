"""
Reference outputs from the ORIGINAL final_submission modules, on small slices.

Run in its own process (it imports the original meio_weekly_lib / clark_scarf_v2 under
their own names, which would clash with this folder's). tests/test_equivalence.py
starts it when the reference folder is missing, then compares the optimized modules
against what it wrote, bit for bit.

Nothing here runs a full pipeline step: a handful of single-week solves and a 3-week
walk-forward loop copied from validate_real_data.build_walkforward_policy_csv2, about a
minute in total.

    python tests/make_reference.py <out_dir>
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORIG = HERE.parents[1] / "final_submission"
REPO = HERE.parents[4]
sys.path[:0] = [str(ORIG), str(REPO / "models"), str(REPO / "models" / "twin")]

import numpy as np                    # noqa: E402
import pandas as pd                   # noqa: E402
import meio_weekly_lib as lib         # noqa: E402  (the original)
import clark_scarf_v2 as csv2         # noqa: E402  (the original)

DATA = str(REPO / "input datasets")


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    dfc = lib.load_demand_forecast(DATA)
    pi = lib.load_period_index(DATA)
    si = lib.build_seasonal_index(DATA)
    subcat = lib.load_sku_subcategory(DATA)
    ar, ac = lib.active_nodes(dfc)
    r, c, prim = lib.build_leadtime_graph(DATA, ar, ac)
    lt = {**r, **c}
    cost = lib.load_sku_cost(DATA)
    si.to_parquet(out / "seasonal_index.parquet")
    pd.DataFrame([{"dc": k, "mean": v[0], "var": v[1]} for k, v in lt.items()]).to_parquet(out / "leadtime.parquet")

    for a, t in [(0, 1), (10, 11), (50, 51), (51, 60), (51, 103), (20, 23)]:
        lib.target_week_demand(dfc, a, pi, si, subcat, target_week_num=t).to_parquet(out / f"demand_{a}_{t}.parquet")
    print(f"demand refs {time.time() - t0:.0f}s", flush=True)

    args = (dfc, 20, pi, si, subcat, lt, cost, ar, ac, prim)
    rng = np.random.default_rng(2026)
    csv2.solve(*args, 21, 0.2, rng, n_sims=1200).to_parquet(out / "solve_mc_a.parquet")
    csv2.solve(dfc, 21, pi, si, subcat, lt, cost, ar, ac, prim, 22, 0.6, rng, n_sims=1200,
               netting_discount=0.0).to_parquet(out / "solve_mc_b_continued.parquet")
    json.dump(rng.bit_generator.state, open(out / "rng_after_b.json", "w"))

    skus = sorted(set(dfc.sku_id))
    alpha_override = {s: 0.8 for s in skus[::7]}
    z_override = {s: 2.326 for s in skus[::5]}
    csv2.ALPHA_OVERRIDE_BY_SKU.update(alpha_override)
    lib.Z_OVERRIDE_BY_SKU.update(z_override)
    try:
        csv2.solve(*args, 21, 0.2, np.random.default_rng(7), n_sims=1200,
                   netting_discount=0.5).to_parquet(out / "solve_mc_overrides.parquet")
        sm = lib.smoothed_rdc_demand_for_pdc(dfc, 20, pi, si, subcat, ar, 21)
        lib.solve_pdc_policy(sm, DATA, cost).to_parquet(out / "pdc_overrides.parquet")
    finally:
        csv2.ALPHA_OVERRIDE_BY_SKU.clear()
        lib.Z_OVERRIDE_BY_SKU.clear()
    json.dump({"alpha": alpha_override, "z": z_override}, open(out / "overrides.json", "w"))

    sm = lib.smoothed_rdc_demand_for_pdc(dfc, 51, pi, si, subcat, ar, 52)
    sm.to_parquet(out / "smoothed_51_52.parquet")
    lib.solve_pdc_policy(sm, DATA, cost).to_parquet(out / "pdc_51_52.parquet")
    csv2.solve(dfc, 51, pi, si, subcat, lt, cost, ar, ac, prim, 60, 0.2, None, netting_discount=0.0,
               ddlt="quadrature").to_parquet(out / "solve_quad.parquet")
    d = lib.target_week_demand(dfc, 20, pi, si, subcat)
    lib.solve_policy(d, lt, cost, ar, ac, prim).to_parquet(out / "solve_policy_meio.parquet")
    print(f"solve refs {time.time() - t0:.0f}s", flush=True)

    # validate_real_data.build_walkforward_policy_csv2's loop body, test weeks 1..3 only
    rng = np.random.default_rng(2026)
    frames = []
    for wn in (1, 2, 3):
        as_of_num = wn - 1
        target_period = pi.loc[pi.week_num == wn, "period"].iloc[0]
        policy = csv2.solve(dfc, as_of_num, pi, si, subcat, lt, cost, ar, ac, prim, wn, 0.2, rng, n_sims=1200,
                            netting_discount=1.0)
        rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(dfc, as_of_num, pi, si, subcat, ar, wn)
        pdc_policy = lib.solve_pdc_policy(rdc_smoothed, DATA, cost)
        if len(pdc_policy):
            policy = pd.concat([policy, pdc_policy], ignore_index=True)
        policy["period"] = target_period
        frames.append(policy[["period", "dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]])
    pd.concat(frames, ignore_index=True).to_parquet(out / "walkforward_w1_3.parquet")
    print(f"walk-forward ref {time.time() - t0:.0f}s", flush=True)
    (out / "DONE").write_text("ok")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
