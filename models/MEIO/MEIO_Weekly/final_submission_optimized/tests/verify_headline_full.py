"""
Full-length check of one real pipeline result: validate_real_data's headline arm.

  1. The 156-week walk-forward policy for Clark-Scarf v2 at alpha=0.2, seed 2026 (105
     warm-up weeks + all 51 refit weeks), built by the ORIGINAL modules -- the loop of
     final_submission/validate_real_data.build_walkforward_policy_csv2, in a child
     process -- and by policy_builds.walkforward_policies. Compared value for value.
  2. That policy simulated over the full 156-week history by models/twin/digital_twin.py
     and by digital_twin_fast.py, in memory. KPIs and the test-window fill KPIs must be
     identical.

Everything is written to a temporary folder. The original side takes several minutes
(the reason this is a script, not a unit test).

    python tests/verify_headline_full.py
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPT = HERE.parent
REPO = HERE.parents[4]

ORIGINAL_BUILD = r'''
import sys, numpy as np, pandas as pd
sys.path[:0] = [{orig!r}, {models!r}, {twin!r}]
import meio_weekly_lib as lib, clark_scarf_v2 as csv2
DATA = {data!r}
dfc = lib.load_demand_forecast(DATA); pi = lib.load_period_index(DATA)
ar, ac = lib.active_nodes(dfc); r, c, prim = lib.build_leadtime_graph(DATA, ar, ac); lt = {{**r, **c}}
cost = lib.load_sku_cost(DATA); si = lib.build_seasonal_index(DATA); sub = lib.load_sku_subcategory(DATA)
cols = ["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]
base = pd.read_parquet({policy!r})[cols]
periods = sorted(pd.read_parquet(DATA + "/orders.parquet", columns=["period"]).period.unique())
frames = [base.assign(period=p)[["period"] + cols] for p in periods if "2023-W01" <= p <= "2025-W01"]
wk = lambda p: int(pi.loc[pi.period == p, "week_num"].iloc[0])
rng = np.random.default_rng(2026)
for wn in range(wk("2025-W02"), wk("2025-W52") + 1):
    policy = csv2.solve(dfc, wn - 1, pi, si, sub, lt, cost, ar, ac, prim, wn, 0.2, rng, n_sims=1200, netting_discount=1.0)
    pdc = lib.solve_pdc_policy(lib.smoothed_rdc_demand_for_pdc(dfc, wn - 1, pi, si, sub, ar, wn), DATA, cost)
    if len(pdc):
        policy = pd.concat([policy, pdc], ignore_index=True)
    policy["period"] = pi.loc[pi.week_num == wn, "period"].iloc[0]
    frames.append(policy[["period"] + cols])
pd.concat(frames, ignore_index=True).to_parquet({out!r}, index=False)
'''


def main():
    tmp = Path(tempfile.mkdtemp(prefix="verify_headline_"))
    data = str(REPO / "input datasets")
    policy = str(REPO / "output datasets" / "history" / "forward_policy.parquet")
    orig_csv, fast_csv = tmp / "original_policy.parquet", tmp / "optimized_policy.parquet"

    t = time.time()
    code = ORIGINAL_BUILD.format(orig=str(REPO / "models/MEIO/MEIO_Weekly/final_submission"),
                                 models=str(REPO / "models"), twin=str(REPO / "models/twin"), data=data,
                                 policy=policy, out=str(orig_csv))
    subprocess.run([sys.executable, "-c", code], check=True)
    t_orig_build = time.time() - t

    sys.path.insert(0, str(OPT))
    import numpy as np
    import pandas as pd
    import polars as pl
    import policy_builds as pb
    import clark_scarf_v2 as csv2
    import digital_twin_fast as fast
    import digital_twin as orig

    t = time.time()
    pb.walkforward_policies(data, {"h": (csv2.Variant(0.2, 1.0), fast_csv)}, 2026, 1200, baseline_policy_csv=policy,
                            warmup=("2023-W01", "2025-W01"), test=("2025-W02", "2025-W52"))
    t_fast_build = time.time() - t
    a, b = pd.read_parquet(orig_csv), pl.read_parquet(fast_csv).to_pandas()
    policy_same = list(a.columns) == list(b.columns) and len(a) == len(b) and all(
        np.array_equal(a[c].to_numpy(), b[c].to_numpy()) for c in a.columns)
    print(f"policy: {len(a):,} rows, identical={policy_same}  (original build {t_orig_build:.0f}s, "
          f"optimized {t_fast_build:.0f}s)", flush=True)

    t = time.time()
    inp_a, _, twin_a, inv_a, *_ = orig.run_twin(Path(data), "history", 2026, 156, orig_csv, None, verbose=False)
    k_a = orig.kpis(inv_a, twin_a, inp_a, orig.summarise(inv_a, twin_a, inp_a)[2])
    t_orig_twin = time.time() - t
    t = time.time()
    inp_b, _, twin_b, inv_b, _, _, arrays = fast.run_twin(Path(data), "history", 2026, 156, fast_csv, None,
                                                          verbose=False)
    k_b = fast.kpis(inv_b, twin_b, inp_b, fast.summarise(inv_b, twin_b, inp_b, arrays)[2], arrays)
    t_fast_twin = time.time() - t
    req = {"windows": {"test": ("2025-W02", "2025-W52")}}
    s_b = fast.summarize(inv_b["period"].to_numpy(), inv_b["sku_id"].to_numpy(), inv_b["dc_id"].to_numpy(),
                         {c: arrays["num"][c] for c in fast.SUMMARY_COLS}, req)["fill"]["test"]
    test_a = inv_a[(inv_a.period >= "2025-W02") & (inv_a.period <= "2025-W52")]
    cols = {c: test_a[c].to_numpy() for c in fast.SUMMARY_COLS}
    s_a = fast.summarize(test_a.period.to_numpy(), test_a.sku_id.to_numpy(), test_a.dc_id.to_numpy(), cols,
                         req)["fill"]["test"]
    print(f"twin KPIs identical={k_a == k_b}, test-window fill identical={s_a == s_b}  "
          f"(original twin {t_orig_twin:.0f}s, optimized {t_fast_twin:.0f}s)")
    print(json.dumps({"original": k_a, "optimized": k_b} if k_a != k_b else k_b, indent=1))
    print(json.dumps(s_b, indent=1))
    sys.exit(0 if (policy_same and k_a == k_b and s_a == s_b) else 1)


if __name__ == "__main__":
    main()
