"""Before an alpha sweep from the front end: back up the current alpha evidence, and
optionally clear the sweep's per-combo partial results so every combo is re-run.

    python App/alpha_sweep_prep.py            # back up only (sweep resumes from existing partials)
    python App/alpha_sweep_prep.py --fresh    # back up, then clear partials
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "models" / "MEIO" / "MEIO_Weekly" / "final_submission_optimized"))
import segment_alpha_v5_real_data as sweep  # noqa: E402

results = Path(sweep.RESULTS_DIR)
backup = results / "_backup" / f"alpha_sweep_{datetime.now():%Y%m%d_%H%M%S}"
saved = [f for f in (Path(sweep.OUT_CSV), Path(sweep.SUMMARY_CSV)) if f.exists()]
if saved:
    backup.mkdir(parents=True, exist_ok=True)
    for f in saved:
        shutil.copy2(f, backup / f.name)
    print(f"backed up {len(saved)} evidence file(s) to {backup}")
else:
    print("no existing alpha evidence to back up")

partials = Path(sweep.PARTIAL_DIR)
if "--fresh" in sys.argv and partials.exists():
    n = len(list(partials.glob("*.json")))
    shutil.rmtree(partials)
    print(f"cleared {n} partial result(s): every combo will be re-run")
else:
    n = len(list(partials.glob("*.json"))) if partials.exists() else 0
    print(f"{n} partial result(s) on disk will be reused; only missing combos run")
