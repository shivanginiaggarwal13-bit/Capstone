"""
Holding/stockout/transport cost breakdown per alpha, from the headline alpha sweep's
own twin runs -- for the "Periodic alpha check" page's cost-tradeoff chart.

validate_real_data.py --stage alpha_sweep already builds and twin-simulates one policy
per alpha (0.0-1.0) over the FULL 156-week horizon (105-week warm-up, 2023-W01..2025-W01,
replaying real recorded history identically across every alpha, plus the 51-week
2025-W02..2025-W52 test window where the swept alpha actually governs the policy) and
writes alpha_sweep_real_data.csv -- but two things keep that file from being the direct
source here:

1. It only carries a net cost_delta_usd, not the holding/stockout/transport breakdown
   this chart exists to show (pooling trades stockout cost for more transport cost --
   see README "Cluster-based service level adopted as default"). The twin's cost
   tracking (digital_twin_fast.py::summarise(), twin.cum_holding/cum_stockout/
   cum_transport) is cumulative over the whole run, not sliced by window, so holding
   cost has no test-window-only figure available either -- only run_kpis.json's
   run-total holding/stockout/transport/total_landed_cost_usd exist, which is this
   script's actual source for the dollar columns, re-read from disk (no new
   simulation). That means the dollar figures here are the full 156-week (3-year)
   cumulative total, not a 51-week number -- label them that way, and use the
   week/month/year/all selector in the UI rather than presenting a bare "cumulative"
   total a planner has no intuition for.
2. alpha_sweep_real_data.csv's own customer_fill_rate / internal_replen_fill_rate ARE
   already correctly sliced to the 51-week test window (validate_real_data.py's
   fill_rate_kpis(), window="test") -- but run_kpis.json's top-level fill fields are
   NOT: they're blended across the full 156 weeks, including 105 weeks of warm-up that
   are nearly identical across every alpha by construction (warm-up parity). That
   dilutes the real test-window signal (confirmed: 93.65% blended vs. 95.70%
   test-window-only for alpha=0.6, alone a ~2pp understatement). So fill rates here are
   pulled from alpha_sweep_real_data.csv instead, not from run_kpis.json.

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

COST_COLUMNS = ["holding_cost_usd", "stockout_cost_usd", "transport_cost_usd", "total_landed_cost_usd"]
WINDOW_WEEKS = 156   # the full simulation horizon these cost totals are cumulative over (105-week
                    # warm-up + 51-week test), NOT the 51-week test window alone -- see module docstring.


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
        rows.append({"alpha": alpha, **{c: kpi[c] for c in COST_COLUMNS}})

    if not rows:
        raise SystemExit(f"No sweep_a* twin runs found under {twin_root}/{name}/ -- run "
                         f"validate_real_data.py --stage alpha_sweep first.")
    table = pl.DataFrame(rows).sort("alpha")
    table = table.with_columns(pl.lit(WINDOW_WEEKS).alias("window_weeks"))

    fill_path = os.path.join(results_dir, "alpha_sweep_real_data.csv")
    if os.path.exists(fill_path):
        fill = pl.read_csv(fill_path, columns=["alpha", "customer_fill_rate", "internal_replen_fill_rate"])
        table = table.join(fill, on="alpha", how="left")
    else:
        table = table.with_columns(pl.lit(None).alias("customer_fill_rate"),
                                   pl.lit(None).alias("internal_replen_fill_rate"))

    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, "alpha_cost_breakdown.csv")
    rt.write_table(table, out_path)
    print(rt.show(table))
    print(f"\nWrote {out_path}")
    return table


if __name__ == "__main__":
    run()
