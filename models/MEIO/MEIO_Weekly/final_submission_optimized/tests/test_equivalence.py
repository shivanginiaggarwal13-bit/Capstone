"""
Bit-for-bit equivalence of the optimized modules with the originals, on small slices.

    python -m pytest models/MEIO/MEIO_Weekly/final_submission_optimized/tests -q

References come from tests/make_reference.py (the ORIGINAL modules, run once in their
own process, about a minute) and are cached in tests/_reference/. Every float column is
compared with array_equal -- no tolerance -- because the policies feed a simulation in
which a one-ULP difference in a reorder point can flip a replenishment decision.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import runtime as rt                  # noqa: E402,F401  (paths)
import meio_weekly_lib as lib         # noqa: E402
import clark_scarf_v2 as csv2         # noqa: E402
import policy_builds as pb            # noqa: E402

REF = HERE / "_reference"
REPO = HERE.parents[4]
DATA = str(REPO / "input datasets")


def setUpModule():
    if not (REF / "DONE").exists():
        subprocess.run([sys.executable, str(HERE / "make_reference.py"), str(REF)], check=True)


def ref(name) -> pd.DataFrame:
    return pd.read_parquet(REF / f"{name}.parquet")


def assert_frames_equal(test, expected: pd.DataFrame, got, cols=None):
    got = got.to_pandas() if isinstance(got, pl.DataFrame) else got
    cols = cols or list(expected.columns)
    test.assertEqual(len(expected), len(got), "row count")
    for c in cols:
        a, b = expected[c].to_numpy(), got[c].to_numpy()
        if a.dtype.kind in "fiub" and b.dtype.kind in "fiub":
            a, b = a.astype(float), b.astype(float)
            same = np.array_equal(a, b, equal_nan=True)
            if not same:
                bad = np.flatnonzero(~((a == b) | (np.isnan(a) & np.isnan(b))))
                test.fail(f"column {c}: {len(bad)} rows differ, first at {bad[0]}: {a[bad[0]]!r} vs {b[bad[0]]!r}")
        else:
            na = [None if (v is None or (isinstance(v, float) and np.isnan(v))) else str(v) for v in a]
            nb = [None if (v is None or (isinstance(v, float) and np.isnan(v))) else str(v) for v in b]
            test.assertEqual(na, nb, f"column {c}")


class Shared:
    _inp = None

    @classmethod
    def inp(cls):
        if cls._inp is None:
            cls._inp = pb.inputs(DATA)
        return cls._inp


class DemandTests(unittest.TestCase):
    def test_seasonal_index(self):
        assert_frames_equal(self, ref("seasonal_index"), Shared.inp().seasonal_index)

    def test_leadtime_graph(self):
        r = ref("leadtime")
        lt = Shared.inp().leadtime_by_dc
        self.assertEqual(list(r.dc), list(lt))
        self.assertTrue(np.array_equal(r["mean"].to_numpy(), [lt[d][0] for d in r.dc]))
        self.assertTrue(np.array_equal(r["var"].to_numpy(), [lt[d][1] for d in r.dc]))

    def test_target_week_demand(self):
        inp = Shared.inp()
        for a, t in [(0, 1), (10, 11), (50, 51), (51, 60), (51, 103), (20, 23)]:
            with self.subTest(as_of=a, target=t):
                got = lib.target_week_demand(inp.dfc, a, None, inp.seasonal_index, inp.sku_subcat,
                                             target_week_num=t).frame()
                assert_frames_equal(self, ref(f"demand_{a}_{t}"), got)

    def test_smoothed_and_pdc(self):
        inp = Shared.inp()
        sm = lib.smoothed_rdc_demand_for_pdc(inp.dfc, 51, None, inp.seasonal_index, inp.sku_subcat,
                                             inp.active_rdc, 52)
        assert_frames_equal(self, ref("smoothed_51_52"), sm)
        assert_frames_equal(self, ref("pdc_51_52"), lib.solve_pdc_policy(sm, DATA, inp.cost))


class SolveTests(unittest.TestCase):
    def args(self, as_of):
        i = Shared.inp()
        return (i.dfc, as_of, None, i.seasonal_index, i.sku_subcat, i.leadtime_by_dc, i.cost, i.active_rdc,
                i.active_cdc, i.cdc_primary_rdc)

    def test_monte_carlo_and_stream_continuity(self):
        rng = np.random.default_rng(2026)
        assert_frames_equal(self, ref("solve_mc_a"), csv2.solve(*self.args(20), 21, 0.2, rng, n_sims=1200))
        assert_frames_equal(self, ref("solve_mc_b_continued"),
                            csv2.solve(*self.args(21), 22, 0.6, rng, n_sims=1200, netting_discount=0.0))
        self.assertEqual(json.load(open(REF / "rng_after_b.json")), rng.bit_generator.state)

    def test_alpha_and_z_maps(self):
        ov = json.load(open(REF / "overrides.json"))
        got = csv2.solve(*self.args(20), 21, 0.2, np.random.default_rng(7), n_sims=1200, netting_discount=0.5,
                         alpha_by_sku=ov["alpha"], z_by_sku=ov["z"])
        assert_frames_equal(self, ref("solve_mc_overrides"), got)
        i = Shared.inp()
        sm = lib.smoothed_rdc_demand_for_pdc(i.dfc, 20, None, i.seasonal_index, i.sku_subcat, i.active_rdc, 21)
        assert_frames_equal(self, ref("pdc_overrides"), lib.solve_pdc_policy(sm, DATA, i.cost, z_by_sku=ov["z"]))

    def test_quadrature(self):
        got = csv2.solve(*self.args(51), 60, 0.2, None, netting_discount=0.0, ddlt="quadrature")
        assert_frames_equal(self, ref("solve_quad"), got)

    def test_meio_weekly_closed_form(self):
        i = Shared.inp()
        d = lib.target_week_demand(i.dfc, 20, None, i.seasonal_index, i.sku_subcat)
        got = lib.solve_policy(d, i.leadtime_by_dc, i.cost, i.active_rdc, i.active_cdc, i.cdc_primary_rdc)
        expected = ref("solve_policy_meio")
        assert_frames_equal(self, expected, got, [c for c in expected.columns])


class WalkForwardTests(unittest.TestCase):
    def test_many_variants_on_one_stream_match_separate_builds(self):
        """Weeks 1-3 of validate_real_data's walk-forward, alpha 0.2 / netting 1.0 at seed
        2026, built alongside two other variants on the same stream."""
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="wf_eq_"))
        i = Shared.inp()
        variants = {"ref": (csv2.Variant(0.2, 1.0), tmp / "a.csv"), "nd0": (csv2.Variant(0.2, 0.0), tmp / "b.csv"),
                    "a6": (csv2.Variant(0.6, 1.0), tmp / "c.csv")}
        p = [i.dfc.periods[1], i.dfc.periods[3]]
        for threads in (1, 4):
            with self.subTest(threads=threads):
                res = pb.walkforward_policies(DATA, variants, 2026, 1200, baseline_policy_csv=str(
                    REPO / "output datasets" / "history" / "forward_policy.csv"), warmup=("9999", "9999"),
                    test=tuple(p), threads=threads)
                got = pl.read_csv(res["paths"]["ref"])
                assert_frames_equal(self, ref("walkforward_w1_3"), got)


class ParameterReceiptTests(unittest.TestCase):
    """meio_parameters.resolve() receipts, decision for decision, against the original."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = REPO / "models" / "MEIO" / "MEIO_Weekly" / "final_submission" / "meio_parameters.py"
        spec = importlib.util.spec_from_file_location("orig_meio_parameters", path)
        cls.orig = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.orig            # dataclasses resolve annotations through it
        spec.loader.exec_module(cls.orig)
        import meio_parameters
        cls.fast = meio_parameters

    def config(self, folders):
        import copy
        cfg = copy.deepcopy(json.loads((REPO / "scenario_config.json").read_text(encoding="utf-8")))
        cfg["meio"]["parameters"] = {"model": "clark_scarf_v2", "selection": "validated", "evidence": folders}
        return cfg

    def same(self, folders):
        a = json.dumps(self.orig.resolve(self.config(folders)).receipt(), indent=1, default=float)
        b = json.dumps(self.fast.resolve(self.config(folders)).receipt(), indent=1, default=float)
        self.assertEqual(a, b)

    def test_committed_evidence(self):
        self.same(["models/MEIO/MEIO_Weekly/final_submission/results"])

    def test_synthetic_complete_sweeps(self):
        import shutil
        import tempfile
        from scipy.stats import norm
        tmp = Path(tempfile.mkdtemp(prefix="mp_eq_"))
        for f in (REPO / "models/MEIO/MEIO_Weekly/final_submission/results").glob("*.csv"):
            shutil.copy(f, tmp / f.name)
        seeds = (2026, 7, 42, 123, 2027)
        base = pd.read_csv(tmp / "netting_discount_multiseed.csv")
        base = base[base.netting_discount == 0.0].set_index("seed")
        rng = np.random.default_rng(0)
        pd.DataFrame([{"netting_discount": 0.0, "seed": s, "alpha": 0.6,
                       "fill_delta_pp": base.fill_delta_pp[s] + 0.3 + rng.normal(0, 0.02),
                       "internal_replen_fill": 0.82, "cost_delta_usd": base.cost_delta_usd[s]} for s in seeds]
                     ).to_csv(tmp / "netting_discount_multiseed_alpha0_6.csv", index=False)
        self.same([str(tmp)])
        (tmp / "netting_discount_multiseed_alpha0_6.csv").unlink()
        clusters = pd.read_csv(REPO / "Clustering & Segmentation/segmentation_v4/sku_clusters.csv")
        rec = dict(zip(clusters.archetype, clusters.recommended_csl))
        rows = []
        for c in self.fast.CLUSTERS:
            for csl in self.fast.CLUSTER_CSLS:
                for s in seeds:
                    gain = 0.02 if (c == "Mid-Tier Core" and csl == 0.99) else (0.0 if csl == rec[c] else -0.01)
                    rows.append({"cluster": c, "csl": csl, "z": norm.ppf(csl), "seed": s,
                                 "portfolio_fill_rate": 0.948, "own_fill_rate": 0.94 + gain + 1e-4 * (s % 7),
                                 "internal_replen_fill_rate": 0.82, "fill_delta_vs_actual_pp": base.fill_delta_pp[s] + 0.2,
                                 "cost_delta_usd": base.cost_delta_usd[s] - 50_000})
        pd.DataFrame(rows).to_csv(tmp / "service_level_real_data_multiseed.csv", index=False)
        self.same([str(tmp)])


if __name__ == "__main__":
    unittest.main()
