"""
End-to-end smoke run of run_all.py on a shrunken configuration, into a temp folder.

Every step, the shared pool, the RNG chains, the once-only baselines and every report
writer run for real, but over 3 test weeks (2025-W02..W04) and 4-week rolling builds,
with every output folder redirected to a temporary directory -- nothing under
`output datasets/` is touched. Takes a few minutes. It checks that the pipeline runs
and writes every results file; numbers are only sanity-checked (the bit-for-bit checks
are tests/test_equivalence.py and tests/test_twin_equivalence.py).

    python tests/smoke_run_all.py [--keep]
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import run_all                              # noqa: E402
import validate_real_data as vrd            # noqa: E402
import netting_discount_sweep as nds        # noqa: E402
import segment_alpha_explore as sae         # noqa: E402
import segment_alpha_real_data as sard      # noqa: E402
import service_level_real_data as slrd      # noqa: E402
import scenario_test as st                  # noqa: E402

EXPECTED = ["meio_weekly_vs_actual.csv", "headline_clark_scarf_v2_vs_actual.csv", "headline_cost_summary.csv",
            "alpha_sweep_real_data.csv", "multiseed_alpha_04_06.csv", "segment_breakdown_real_data.csv",
            "netting_discount_sweep_alpha0_2.csv", "netting_discount_sweep_alpha0_6.csv",
            "netting_discount_multiseed.csv", "netting_discount_multiseed_alpha0_6.csv",
            "segment_alpha_sweep_portfolio.csv", "segment_alpha_sweep_own_fill.csv",
            "segment_alpha_real_data_multiseed.csv", "segment_alpha_real_data_summary.csv",
            "service_level_real_data_multiseed.csv", "service_level_real_data_summary.csv",
            "scenario_DemandSurgeEast_comparison.csv", "scenario_DemandSurgeEast_stocking_by_dc.csv"]


def shrink(root: Path):
    scen = root / "MEIO artifacts" / vrd.SCENARIO_NAME
    for m in (vrd, sae):
        m.SCENARIO_OUT_DIR = str(scen)
        m.RUN_OUT_DIR = str(scen / "validation_output")
        m.RESULTS_DIR = str(scen / "results")
        m.TWIN_OUT_ROOT = str(root)
    vrd.TWIN_OUT_DIR = str(root / "projections" / vrd.SCENARIO_NAME)
    vrd.TEST_END = "2025-W04"
    for m in (nds, sard, slrd, st):
        m.RUN_OUT_DIR, m.RESULTS_DIR = vrd.RUN_OUT_DIR, vrd.RESULTS_DIR
    for m in (nds, sard, slrd):
        m.TWIN_OUT_DIR = vrd.TWIN_OUT_DIR
    sard.PARTIAL_DIR = os.path.join(vrd.RUN_OUT_DIR, "segment_alpha_partial")
    sard.OUT_CSV = os.path.join(vrd.RESULTS_DIR, "segment_alpha_real_data_multiseed.csv")
    sard.SUMMARY_CSV = os.path.join(vrd.RESULTS_DIR, "segment_alpha_real_data_summary.csv")
    slrd.PARTIAL_DIR = os.path.join(vrd.RUN_OUT_DIR, "service_level_partial")
    slrd.OUT_CSV = os.path.join(vrd.RESULTS_DIR, "service_level_real_data_multiseed.csv")
    slrd.SUMMARY_CSV = os.path.join(vrd.RESULTS_DIR, "service_level_real_data_summary.csv")
    sae.ROLLING_WEEKS = 4
    run_all.LOG_DIR = str(root / "run_logs")


def main():
    root = Path(tempfile.mkdtemp(prefix="meio_smoke_"))
    shrink(root)
    print(f"smoke root: {root}")
    sys.argv = ["run_all.py"] + [a for a in sys.argv[1:] if a != "--keep"]
    code = 0
    try:
        run_all.main()
    except SystemExit as e:
        code = e.code or 0
    results = Path(vrd.RESULTS_DIR)
    missing = [f for f in EXPECTED if not (results / f).exists()]
    print(f"\nresults files: {len(EXPECTED) - len(missing)}/{len(EXPECTED)} written" +
          (f"; missing {missing}" if missing else ""))
    print(f"outputs kept at {root}")
    sys.exit(1 if (code or missing) else 0)


if __name__ == "__main__":
    main()
