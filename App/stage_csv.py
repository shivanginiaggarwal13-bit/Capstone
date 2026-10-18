"""Stage CSV copies of the parquet input tables that segmentation_v5.py still reads as CSV.

segmentation_v5.py loads <input>/<table>.csv, but the repository's input datasets are
parquet now. This converts the tables it reads into App/runs/csv_input/ (reused when
newer than the parquet), so the segmentation run reads the real data without touching it.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEST = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "App" / "runs" / "csv_input"
TABLES = ["sku_master", "cost_parameters", "network_lanes", "orders", "demand_forecast",
          "sku_customer_service_targets", "inventory", "replenishment_receipts", "write_offs"]
DEST.mkdir(parents=True, exist_ok=True)
for t in TABLES:
    src, dst = ROOT / "input datasets" / f"{t}.parquet", DEST / f"{t}.csv"
    if not src.exists():
        print(f"skip {t}: no parquet")
    elif dst.exists() and dst.stat().st_mtime > src.stat().st_mtime:
        print(f"reuse {t}")
    else:
        pd.read_parquet(src).to_csv(dst, index=False)
        print(f"staged {t}")
