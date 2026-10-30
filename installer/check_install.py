"""Post-install check: Python version, packages, project files and writable output folders.

    python installer/check_install.py
Exits with status 1 if anything required is missing.
"""
import importlib
import importlib.metadata as md
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MODULES = ["numpy", "pandas", "polars", "pyarrow", "scipy", "sklearn", "matplotlib", "streamlit", "altair",
                    "langgraph", "langchain_anthropic", "langchain_openai", "requests", "jsonschema", "yaml"]
OPTIONAL_MODULES = ["xgboost", "shap"]
REQUIRED_FILES = ["App/streamlit_app.py", "scenario_config.json", "input datasets/orders.parquet",
                  "input datasets/sku_master.parquet", "output datasets/segmentation/sku_clusters.csv",
                  "models/MEIO/MEIO_Weekly/final_submission_optimized/digital_twin_fast.py",
                  "models/MEIO/MEIO_Weekly/final_submission_optimized/roll_forward_policy.py",
                  "Agents/runtime/profiles.yaml"]
WRITABLE_DIRS = ["App/runs", "output datasets"]

problems = []


def line(ok, label, detail=""):
    print(f"  [{'OK' if ok else '!!'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


print("Supply Brain install check\n")
print("Python")
line(sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}", "3.11 or newer required")

print("\nPackages")
for m in REQUIRED_MODULES:
    try:
        importlib.import_module(m)
        dist = {"sklearn": "scikit-learn", "yaml": "pyyaml"}.get(m, m.replace("_", "-"))
        try:
            ver = md.version(dist)
        except md.PackageNotFoundError:
            ver = ""
        line(True, m, ver)
    except Exception as e:  # noqa: BLE001
        line(False, m, f"{type(e).__name__}: {e}")
for m in OPTIONAL_MODULES:
    try:
        importlib.import_module(m)
        print(f"  [OK] {m} (optional)")
    except Exception:  # noqa: BLE001
        print(f"  [--] {m} (optional, only for the SHAP script)")

print("\nProject files")
for f in REQUIRED_FILES:
    line((ROOT / f).exists(), f)

print("\nWritable folders")
for d in WRITABLE_DIRS:
    p = ROOT / d
    p.mkdir(parents=True, exist_ok=True)
    line(os.access(p, os.W_OK), d)

print("\nAPI keys (optional)")
env = ROOT / ".env"
keys = {}
if env.exists():
    for raw in env.read_text(encoding="utf-8").splitlines():
        if "=" in raw and not raw.lstrip().startswith("#"):
            k, v = raw.split("=", 1)
            keys[k.strip()] = v.strip()
for k in ("ANTHROPIC_API_KEY", "NVIDIA_API_KEY"):
    print(f"  [{'OK' if keys.get(k) or os.environ.get(k) else '--'}] {k}"
          f"{'' if keys.get(k) or os.environ.get(k) else ' not set (that agent profile stays unavailable)'}")

print()
if problems:
    print(f"{len(problems)} problem(s): " + ", ".join(problems))
    sys.exit(1)
print("All required checks passed.")
