"""
The optimized twin against models/twin/digital_twin.py, run side by side in memory on
short horizons (no output folders written). Inventory, receipts, production and the
KPIs must match exactly; the two diagnostic service tables to 1e-12 relative.

    python -m pytest models/MEIO/MEIO_Weekly/final_submission_optimized/tests -q
"""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import runtime as rt                    # noqa: E402
import digital_twin_fast as fast        # noqa: E402
import digital_twin as orig             # noqa: E402  (models/twin, on sys.path via runtime)

REPO = rt.REPO_ROOT
DATA = REPO / "input datasets"
POLICY = REPO / "output datasets" / "history" / "forward_policy.csv"
STATE = REPO / "output datasets" / "history" / "opening_position.csv"


def time_varying_policy(periods, seed=0) -> Path:
    """forward_policy.csv per period, perturbed, with some rows dropped (forward-fill)."""
    base = pd.read_csv(POLICY)[["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]]
    rng = np.random.default_rng(seed)
    frames = []
    for k, p in enumerate(periods):
        f = base.copy()
        scale = np.round(rng.uniform(0.7, 1.4, len(f)), 2)
        for c in ("safety_stock", "reorder_point", "order_up_to_level"):
            f[c] = (f[c] * scale).round(1)
        if k:
            f = f[rng.random(len(f)) > 0.05]
        frames.append(f.assign(period=p))
    path = Path(tempfile.mkdtemp(prefix="twin_eq_")) / "policy.csv"
    pd.concat(frames, ignore_index=True)[["period", "dc_id", "sku_id", "safety_stock", "reorder_point",
                                          "order_up_to_level"]].to_csv(path, index=False)
    return path


class TwinEquivalence(unittest.TestCase):
    def compare(self, mode, weeks, policy, state):
        a = orig.run_twin(DATA, mode, 2026, weeks, Path(policy), state, verbose=False)
        b = fast.run_twin(DATA, mode, 2026, weeks, Path(policy), state, verbose=False)
        inp_a, notes_a, twin_a, inv_a, rcpt_a, prod_a = a
        inp_b, notes_b, twin_b, inv_b, rcpt_b, prod_b, arrays = b
        self.assertEqual(orig._jsonable(notes_a), orig._jsonable(notes_b))
        for name, x, y in (("inventory", inv_a, inv_b), ("receipts", rcpt_a, rcpt_b), ("production", prod_a, prod_b)):
            y = y.to_pandas()
            self.assertEqual(list(x.columns), list(y.columns), name)
            self.assertEqual(len(x), len(y), name)
            for c in x.columns:
                u, v = x[c].to_numpy(), y[c].to_numpy()
                if u.dtype.kind in "fiub":
                    same = np.array_equal(u.astype(float), v.astype(float), equal_nan=True)
                else:
                    same = [str(s) for s in u] == [str(s) for s in v]
                self.assertTrue(same, f"{name}.{c} differs")
        svc_sku_a, svc_node_a, costs_a = orig.summarise(inv_a, twin_a, inp_a)
        svc_sku_b, svc_node_b, costs_b = fast.summarise(inv_b, twin_b, inp_b, arrays)
        self.assertEqual(orig.kpis(inv_a, twin_a, inp_a, costs_a), fast.kpis(inv_b, twin_b, inp_b, costs_b, arrays))
        cb = costs_b.to_pandas()
        for c in costs_a.columns:
            u, v = costs_a[c].to_numpy(), cb[c].to_numpy()
            self.assertTrue(np.array_equal(u, v) if u.dtype.kind == "f" else list(u) == list(v), f"costs.{c}")
        for x, y in ((svc_sku_a, svc_sku_b), (svc_node_a, svc_node_b)):
            y = y.to_pandas()
            self.assertEqual(list(x.columns), list(y.columns))
            for c in x.columns:
                if x[c].dtype.kind == "f":
                    np.testing.assert_allclose(x[c].to_numpy(), y[c].to_numpy(), rtol=1e-12, equal_nan=True)
                else:
                    self.assertEqual([str(s) for s in x[c]], [str(s) for s in y[c]])

    def test_forward_static_policy(self):
        self.compare("forward", 10, POLICY, STATE)

    def test_forward_time_varying_policy(self):
        periods = [orig.period_label(i) for i in range(orig.HISTORY_WEEKS + 1, orig.HISTORY_WEEKS + 9)]
        self.compare("forward", 8, time_varying_policy(periods), STATE)

    def test_history_time_varying_policy(self):
        periods = [orig.period_label(i) for i in range(1, 13)]
        self.compare("history", 12, time_varying_policy(periods, seed=1), None)


if __name__ == "__main__":
    unittest.main()
