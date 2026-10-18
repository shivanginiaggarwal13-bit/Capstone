"""Periodic alpha check: re-resolve the MEIO parameters from the tuning evidence.

Runs the same resolver the weekly MEIO pipeline uses (meio_parameters.resolve) against the
shared scenario_config.json and prints the decisions. Read-only: it writes nothing to the
scenario folders; the JSON receipt goes to the path given as argv[1].
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "models"))
sys.path.insert(0, str(ROOT / "models" / "MEIO" / "MEIO_Weekly" / "final_submission_optimized"))
import meio_parameters  # noqa: E402

config = json.loads((ROOT / "scenario_config.json").read_text(encoding="utf-8"))
# Evidence only from 'output datasets' (the config also lists a models/ fallback folder).
config["meio"]["parameters"]["evidence"] = [e for e in config["meio"]["parameters"]["evidence"]
                                            if e.startswith("output datasets")]
params = meio_parameters.resolve(config)
print(params.summary())
if len(sys.argv) > 1:
    params.write(Path(sys.argv[1]))
    print(f"receipt: {sys.argv[1]}")
