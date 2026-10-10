"""
Clark-Scarf v2 -- Scenario Testing: Re-Optimized Stocking vs. Baseline (optimized)
===============================================================================

The same three-arm test as final_submission/scenario_test.py, for a legacy demand-surge
scenario (models/twin/scenario_engine.py):

  A. baseline    -- today's policy (alpha=0.2, netting_discount=0.0), normal demand
  B. unprepared  -- the same policy under the scenario's demand
  C. prepared    -- a policy rebuilt with the scenario's demand bump in its sizing,
                    under the scenario's demand

B vs C is the value of the scenario-aware recommendation; A vs B the raw impact.

Two fixes relative to the original, which cannot finish against the current twin:
  * Arm A read `validation_output/<run>/run_kpis.json`, but digital_twin.py now always
    writes under `<out-root>/projections/SCENARIO_####_Name/<run>` (it falls back to the
    root scenario config). The arms here read the folder the twin actually wrote.
  * Arms B and C passed the example scenario JSON, which has no `digital_twin` section,
    as --scenario-config; the twin reads that section first and stopped with KeyError.
    The temporary config now carries the root config's `digital_twin` section next to
    the scenario's own name, number and `changes`.
The A and C policies are independent builds and run in parallel.

    python scenario_test.py --scenario ../../../twin/scenario_config.example.json
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                       # noqa: E402
import policy_builds as pb                 # noqa: E402
import digital_twin_fast as twin           # noqa: E402
import segment_alpha_explore as sae        # noqa: E402

REPO_ROOT = sae.REPO_ROOT
DEFAULT_DATA = sae.DEFAULT_DATA
STATE_CSV = sae.STATE_CSV
RUN_OUT_DIR = sae.RUN_OUT_DIR
RESULTS_DIR = sae.RESULTS_DIR

ALPHA = 0.2                 # current recommended config
NETTING_DISCOUNT = 0.0      # current recommended config
N_SIMS = 1200
SEED = 2026
AS_OF_WEEK = "2025-W52"

KPI_KEYS = ["customer_fill_rate", "internal_replen_fill_rate", "avg_inventory_value_usd",
            "holding_cost_usd", "stockout_cost_usd", "total_landed_cost_usd"]


def forward_job(run_name, policy_csv, scenario_config_path=None, seed=SEED) -> dict:
    """digital_twin --mode forward into RUN_OUT_DIR; with a scenario config when given
    (the twin then applies its legacy demand surge)."""
    return {"run_name": run_name, "mode": "forward", "policy": str(policy_csv), "state": STATE_CSV, "seed": seed,
            "out_root": RUN_OUT_DIR, "label": run_name,
            "scenario_config": str(scenario_config_path) if scenario_config_path else None}


def build_job(out_path, scenario_changes=None, seed=SEED) -> dict:
    return {"data_dir": DEFAULT_DATA, "as_of_week": AS_OF_WEEK, "rng": seed, "n_sims": N_SIMS,
            "scenario_changes": scenario_changes, "max_weeks": sae.ROLLING_WEEKS, "lean": rt.LEAN_TWIN_OUTPUTS,
            "variants": {"policy": pb.variant_spec(out_path, ALPHA, NETTING_DISCOUNT)}}


def run_scenario(ctx, log, scenario_path):
    sae.prepare()
    with open(scenario_path) as f:
        config = json.load(f)
    changes = config["changes"]
    name = config.get("scenario_name", "scenario")
    tag = name.replace(" ", "")
    log(f"=== Scenario: {name} ===\n  changes: {changes}")

    baseline_policy_csv = os.path.join(RUN_OUT_DIR, f"policy_{tag}_baseline.csv")
    scenario_policy_csv = os.path.join(RUN_OUT_DIR, f"policy_{tag}_scenario.csv")
    b_base = ctx.submit(pb.rolling_task, build_job(baseline_policy_csv), priority=2)
    b_scen = ctx.submit(pb.rolling_task, build_job(scenario_policy_csv, scenario_changes=changes), priority=2)

    # the twin needs the root config's digital_twin section to resolve its data folder
    root = json.loads(Path(sae.SCENARIO_CONFIG).read_text(encoding="utf-8"))
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({**config, "digital_twin": root["digital_twin"]}, tmp)
    tmp.close()
    try:
        res_base = b_base.result()
        baseline_policy_csv = res_base["paths"]["policy"]
        for line in res_base["log"]:
            log(line)
        log("\n--- A. baseline policy, normal demand ---")
        a = ctx.submit(twin.simulate_task, forward_job(f"{tag}_A_baseline", baseline_policy_csv), priority=3)
        log("--- B. baseline policy, SCENARIO demand (unprepared) ---")
        b = ctx.submit(twin.simulate_task, forward_job(f"{tag}_B_unprepared", baseline_policy_csv, tmp.name),
                       priority=3)
        res_scen = b_scen.result()
        scenario_policy_csv = res_scen["paths"]["policy"]
        for line in res_scen["log"]:
            log(line)
        log("--- C. scenario-reoptimized policy, SCENARIO demand (prepared) ---")
        c = ctx.submit(twin.simulate_task, forward_job(f"{tag}_C_prepared", scenario_policy_csv, tmp.name),
                       priority=3)
        kpi_a, kpi_b, kpi_c = a.result()["kpis"], b.result()["kpis"], c.result()["kpis"]
    finally:
        os.unlink(tmp.name)

    summary = pl.DataFrame([
        {"arm": "A: baseline (normal demand)", **{k: kpi_a[k] for k in KPI_KEYS}},
        {"arm": "B: unprepared (scenario demand, baseline policy)", **{k: kpi_b[k] for k in KPI_KEYS}},
        {"arm": "C: prepared (scenario demand, scenario-aware policy)", **{k: kpi_c[k] for k in KPI_KEYS}},
    ])
    out_csv = os.path.join(RESULTS_DIR, f"scenario_{tag}_comparison.csv")
    rt.write_csv(summary, out_csv)
    log("\n" + rt.show(summary))

    # DC-level stocking recommendation diff (inner merge, left order; groupby dc sums)
    base_pol = rt.read_policy(baseline_policy_csv).with_row_index("_r")
    scen_pol = rt.read_policy(scenario_policy_csv)
    merged = base_pol.join(scen_pol, on=["period", "dc_id", "sku_id"], how="inner", suffix="_scenario").sort("_r")
    dcs = sorted(set(merged["dc_id"].to_list()))
    code = {d: i for i, d in enumerate(dcs)}
    codes = np.array([code[d] for d in merged["dc_id"].to_list()], dtype=np.int64)
    base_ss, _ = rt.kahan_sum(merged["safety_stock"].cast(pl.Float64).to_numpy(), codes, len(dcs))
    scen_ss, _ = rt.kahan_sum(merged["safety_stock_scenario"].cast(pl.Float64).to_numpy(), codes, len(dcs))
    delta = scen_ss - base_ss
    with np.errstate(invalid="ignore", divide="ignore"):
        pct = np.round(delta / np.where(base_ss == 0, np.nan, base_ss) * 100, 2)
    by_dc = pl.DataFrame({"dc_id": dcs, "baseline_safety_stock": base_ss, "scenario_safety_stock": scen_ss,
                          "safety_stock_delta": delta, "safety_stock_delta_pct": pct})
    by_dc_csv = os.path.join(RESULTS_DIR, f"scenario_{tag}_stocking_by_dc.csv")
    rt.write_csv(by_dc, by_dc_csv)
    log(f"\nRecommended stocking change by DC (scenario-aware vs baseline):\n{rt.show(by_dc)}")

    a_vs_b_fill = (kpi_b["customer_fill_rate"] - kpi_a["customer_fill_rate"]) * 100
    b_vs_c_fill = (kpi_c["customer_fill_rate"] - kpi_b["customer_fill_rate"]) * 100
    log(f"\nScenario impact if unprepared (A -> B): {a_vs_b_fill:+.2f}pp customer fill rate")
    log(f"Value of the scenario-aware recommendation (B -> C): {b_vs_c_fill:+.2f}pp customer fill rate")
    log(f"\nWrote {out_csv}, {by_dc_csv}")
    return summary, by_dc


def run(ctx, log, scenario_path):
    return run_scenario(ctx, log, scenario_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True,
                    help="Path to a scenario-config JSON (see models/twin/scenario_config.example.json)")
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)
    ctx = rt.Context(args.workers)
    try:
        run(ctx, rt.StepLog(), args.scenario)
        twin.write_output_documentation(Path(RUN_OUT_DIR))
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
