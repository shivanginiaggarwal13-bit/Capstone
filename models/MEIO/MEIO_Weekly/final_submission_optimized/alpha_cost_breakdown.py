"""
Holding/stockout/transport cost breakdown per alpha, from the headline alpha sweep's
own twin runs -- for the "Periodic alpha check" page's cost-tradeoff chart.

validate_real_data.py --stage alpha_sweep already builds and twin-simulates one policy
per alpha (0.0-1.0, 51-week real-demand backtest) and writes alpha_sweep_real_data.csv,
but that file only carries a net cost_delta_usd -- it doesn't separate holding, stockout
and transport, so it can't show that pooling trades stockout cost for transport cost
(see README "Cluster-based service level adopted as default", and the 2026-10-18
investigation that found this). This script re-reads each alpha's run_kpis.json
(already on disk from that sweep -- no new simulation) and writes the three components
out explicitly.

    python alpha_cost_breakdown.py                 # current scenario's alpha sweep
"""
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                     # noqa: E402
import scenario                          # noqa: E402

import polars as pl

REPO_ROOT = str(rt.REPO_ROOT)
SCENARIO_CONFIG = os.path.join(REPO_ROOT, "scenario_config.json")

COLUMNS = ["holding_cost_usd", "stockout_cost_usd", "transport_cost_usd", "total_landed_cost_usd",
          "customer_fill_rate", "internal_replen_fill_rate"]


def run(scenario_config: str = SCENARIO_CONFIG) -> pl.DataFrame:
    config = scenario.load_config(Path(scenario_config))
    name = scenario.scenario_folder_name(config)
    twin_root = str(scenario.resolve_path(Path(scenario_config), config, "artifacts", "twin_output_root"))
    results_dir = str(scenario.resolve_scenario_path(Path(scenario_config), config, "artifacts", "scenario")) \
        + "/results"

    rows = []
    for run_dir in sorted(glob.glob(os.path.join(twin_root, name, "sweep_a*"))):
        m = re.match(r"sweep_a(\d\d)$", os.path.basename(run_dir))
        if not m:
            continue
        alpha = int(m.group(1)) / 10.0
        kpi_path = os.path.join(run_dir, "run_kpis.json")
        if not os.path.exists(kpi_path):
            continue
        with open(kpi_path) as f:
            kpi = json.load(f)
        rows.append({"alpha": alpha, **{c: kpi[c] for c in COLUMNS}})

    if not rows:
        raise SystemExit(f"No sweep_a* twin runs found under {twin_root}/{name}/ -- run "
                         f"validate_real_data.py --stage alpha_sweep first.")
    table = pl.DataFrame(rows).sort("alpha")
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, "alpha_cost_breakdown.csv")
    rt.write_table(table, out_path)
    print(rt.show(table))
    print(f"\nWrote {out_path}")
    return table


if __name__ == "__main__":
    run()
