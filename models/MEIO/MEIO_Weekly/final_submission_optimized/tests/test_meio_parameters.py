"""
Tests for the link between run_all.py's tuning evidence and the policy the pipeline builds.

    python -m pytest models/MEIO/MEIO_Weekly/final_submission_optimized/tests -q

(The optimized folder's copy: the same tests, against the polars port. Solve outputs are
polars frames, converted to pandas here for the merge-based checks.)

They read the committed results/ CSVs and the real input datasets, write only to
temporary folders, and solve single weeks (no full rolling build).
"""
import ast
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parents[3]
sys.path[:0] = [str(HERE), str(REPO / "models")]

import clark_scarf_v2 as csv2          # noqa: E402
import meio_parameters as mp            # noqa: E402
import meio_weekly_lib as lib           # noqa: E402
import overrides as engine              # noqa: E402

DATA = str(REPO / "input datasets")
ROOT_CONFIG = json.loads((REPO / "scenario_config.json").read_text(encoding="utf-8"))
COMMITTED = "models/MEIO/MEIO_Weekly/final_submission_optimized/results"
SEEDS = (2026, 7, 42, 123, 2027)


def cs_config(**params):
    cfg = copy.deepcopy(ROOT_CONFIG)
    cfg["meio"]["parameters"] = {"model": "clark_scarf_v2", "selection": "validated", "evidence": [COMMITTED],
                                 **params}
    return cfg


def decision(p, name):
    return next(d for d in p.decisions if d["parameter"] == name)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="meio_params_"))
        for f in (REPO / COMMITTED).glob("*.csv"):
            shutil.copy(f, self.tmp / f.name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_committed_evidence_gives_the_validated_config(self):
        p = mp.resolve(cs_config())
        self.assertEqual((p.alpha, p.netting_discount, p.ddlt), (0.2, 0.0, "quadrature"))
        self.assertEqual((p.alpha_by_sku, p.z_by_sku), ({}, {}))
        self.assertTrue(decision(p, "netting_discount")["tests"]["0.0"]["passes"])
        self.assertEqual(set(decision(p, "alpha")["not_counted"]), {"0.4", "0.6"})
        self.assertIn("0 of 16", decision(p, "service_level_by_cluster")["reason"])
        self.assertIn("netting_discount [1.0]", decision(p, "alpha_by_cluster")["reason"])
        self.assertEqual({s["kind"] for s in p.sources},
                         {"netting_multiseed", "alpha_multiseed", "cluster_alpha", "cluster_csl"})

    def test_no_parameters_section_keeps_meio_weekly(self):
        p = mp.resolve(copy.deepcopy(ROOT_CONFIG) | {"meio": {k: v for k, v in ROOT_CONFIG["meio"].items()
                                                              if k != "parameters"}})
        self.assertEqual(p.model, "meio_weekly")

    def test_grids_match_the_sweep_scripts(self):
        def const(script, name):
            tree = ast.parse((HERE / script).read_text(encoding="utf-8"))
            return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                        and any(getattr(t, "id", None) == name for t in n.targets))
        self.assertEqual(tuple(const("segment_alpha_explore.py", "ALPHAS")), mp.CLUSTER_ALPHAS)
        self.assertEqual(tuple(const("segment_alpha_explore.py", "CLUSTERS")), mp.CLUSTERS)
        self.assertEqual(const("segment_alpha_explore.py", "REFERENCE_ALPHA"), mp.REFERENCE_ALPHA)
        self.assertEqual(tuple(const("service_level_real_data.py", "CANDIDATE_CSL")), mp.CLUSTER_CSLS)
        self.assertEqual(const("validate_real_data.py", "HEADLINE_ALPHA"), mp.REFERENCE_ALPHA)

    def _alpha06(self, fill_gain, cost_gain, noise=0.02):
        """A 5-seed netting run at alpha 0.6, relative to alpha 0.2 / netting 0.0 on the same seeds."""
        base = pd.read_csv(self.tmp / "netting_discount_multiseed.csv")
        base = base[base.netting_discount == 0.0].set_index("seed")
        rng = np.random.default_rng(0)
        rows = [{"netting_discount": 0.0, "seed": s, "alpha": 0.6,
                 "fill_delta_pp": base.fill_delta_pp[s] + fill_gain + rng.normal(0, noise),
                 "internal_replen_fill": 0.82, "cost_delta_usd": base.cost_delta_usd[s] + cost_gain}
                for s in SEEDS]
        pd.DataFrame(rows).to_csv(self.tmp / "netting_discount_multiseed_alpha0_6.csv", index=False)

    def test_alpha_moves_only_on_validated_evidence(self):
        self._alpha06(fill_gain=0.3, cost_gain=0.0)
        p = mp.resolve(cs_config(evidence=[str(self.tmp)]))
        self.assertEqual(p.alpha, 0.6)
        self._alpha06(fill_gain=0.3, cost_gain=150_000.0)          # better fill, but dearer on every seed
        self.assertEqual(mp.resolve(cs_config(evidence=[str(self.tmp)])).alpha, 0.2)
        self._alpha06(fill_gain=0.005, cost_gain=0.0, noise=0.05)  # within seed noise
        self.assertEqual(mp.resolve(cs_config(evidence=[str(self.tmp)])).alpha, 0.2)

    def test_first_evidence_folder_wins_file_by_file(self):
        first = self.tmp / "first"
        first.mkdir()
        pd.read_csv(self.tmp / "netting_discount_multiseed.csv").assign(fill_delta_pp=0.0).to_csv(
            first / "netting_discount_multiseed.csv", index=False)
        p = mp.resolve(cs_config(evidence=[str(first), str(self.tmp)]))
        self.assertEqual(p.netting_discount, 1.0)                  # the first folder's flat evidence
        files = {s["kind"]: s["file"] for s in p.sources}
        self.assertIn("first", files["netting_multiseed"])
        self.assertNotIn("first", files["alpha_multiseed"])

    def test_complete_service_level_sweep_builds_the_cluster_ladder(self):
        clusters = pd.read_csv(REPO / "Clustering & Segmentation/segmentation_v4/sku_clusters.csv")
        rec = dict(zip(clusters.archetype, clusters.recommended_csl))
        seg = pd.read_csv(self.tmp / "netting_discount_multiseed.csv")
        seg = seg[seg.netting_discount == 0.0].set_index("seed")
        rows = []
        for c in mp.CLUSTERS:
            for csl in mp.CLUSTER_CSLS:
                for s in SEEDS:
                    gain = 0.02 if (c == "Mid-Tier Core" and csl == 0.99) else (0.0 if csl == rec[c] else -0.01)
                    rows.append({"cluster": c, "csl": csl, "z": norm.ppf(csl), "seed": s,
                                 "portfolio_fill_rate": 0.948, "own_fill_rate": 0.94 + gain + 1e-4 * (s % 7),
                                 "internal_replen_fill_rate": 0.82,
                                 "fill_delta_vs_actual_pp": seg.fill_delta_pp[s] + 0.2,
                                 "cost_delta_usd": seg.cost_delta_usd[s] - 50_000})
        pd.DataFrame(rows).to_csv(self.tmp / "service_level_real_data_multiseed.csv", index=False)
        p = mp.resolve(cs_config(evidence=[str(self.tmp)]))
        self.assertEqual(decision(p, "service_level[Mid-Tier Core]")["value"], 0.99)
        self.assertEqual(decision(p, "service_level[Long-Tail Erratic]")["value"], rec["Long-Tail Erratic"])
        z = clusters.set_index("sku_id")
        mid = z[z.archetype == "Mid-Tier Core"].index[0]
        tail = z[z.archetype == "Long-Tail Erratic"].index[0]
        self.assertAlmostEqual(p.z_by_sku[mid], norm.ppf(0.99))
        self.assertAlmostEqual(p.z_by_sku[tail], z.safety_factor_z[tail])
        self.assertEqual(len(p.z_by_sku), len(clusters))

    def test_a_file_with_the_wrong_schema_is_an_error(self):
        pd.DataFrame({"netting_discount": [0.0], "seed": [1]}).to_csv(
            self.tmp / "netting_discount_multiseed.csv", index=False)
        with self.assertRaises(mp.ParameterError):
            mp.resolve(cs_config(evidence=[str(self.tmp)]))


class QuadratureTests(unittest.TestCase):
    def test_matches_a_large_monte_carlo(self):
        rng = np.random.default_rng(1)
        n = 300
        mean, cv = rng.gamma(2, 50, n), rng.uniform(0.2, 1.2, n)
        ltm = rng.uniform(0.1, 3, n)
        lts, csl = ltm * rng.uniform(0, 0.6, n), rng.choice([0.90, 0.95, 0.98], n)
        rq, sq, cq = csv2.quadrature_ddlt(mean, mean * cv, ltm, lts, csl)
        rt, st, ct = csv2.monte_carlo_ddlt(mean, mean * cv, ltm, lts, csl, np.random.default_rng(3), 400_000)
        self.assertLess(np.median(np.abs(sq - st) / np.maximum(st, 1)), 0.005)
        self.assertLess(np.median(np.abs(rq - rt) / np.maximum(rt, 1)), 0.003)
        np.testing.assert_allclose(cq, mean * (lib.REVIEW_WEEKS + ltm))


class SolveTests(unittest.TestCase):
    """One Clark-Scarf v2 week on the real data, with and without scenario overrides."""

    @classmethod
    def setUpClass(cls):
        dfc = lib.load_demand_forecast(DATA)
        pi = lib.load_period_index(DATA)
        ar, ac = lib.active_nodes(dfc)
        r, c, prim = lib.build_leadtime_graph(DATA, ar, ac)
        cls.asof = lib.week_num_of(pi, "2025-W52")
        cls.args = (dfc, cls.asof, pi, lib.build_seasonal_index(DATA), lib.load_sku_subcategory(DATA),
                    {**r, **c}, lib.load_sku_cost(DATA), ar, ac, prim)
        cls.week = cls.asof + 28                                    # 2026-W28
        cls.base = cls.solve()

    @classmethod
    def solve(cls, plan=None, **kw):
        return csv2.solve(*cls.args, cls.week, 0.2, None, netting_discount=0.0, overrides=plan,
                          ddlt="quadrature", **kw).to_pandas()

    def hooks(self, *overrides, model="clark_scarf_v2"):
        cfg = copy.deepcopy(ROOT_CONFIG)
        cfg["meio"]["parameters"] = {"model": model}
        cfg["changes"] = {"override_schema_version": "1.0", "overrides": list(overrides)}
        return engine.PlanHooks(engine.load(cfg, data_dir=DATA).side("plan", "meio"))

    def test_deterministic_and_untouched_rows_stay_identical(self):
        self.assertTrue(self.solve().equals(self.base))
        plan = self.hooks({"id": "OVR-0001", "measure": "demand.volume",
                           "scope": {"region": ["Southeast"], "category": ["Beverages"]},
                           "window": {"start_period": "2026-W28", "end_period": "2026-W31"},
                           "operation": "scale", "value": 1.25, "applies_to": ["plan"], "rationale": "test"})
        out = self.solve(plan)
        m = self.base.merge(out, on=["dc_id", "sku_id"], suffixes=("_b", "_s"))
        changed = (m.safety_stock_b != m.safety_stock_s) | (m.order_up_to_level_b != m.order_up_to_level_s)
        rows = plan.ovs.rows(plan.ovs.overrides[0])[["dc_id", "sku_id"]].drop_duplicates()
        in_scope = pd.MultiIndex.from_frame(m[["dc_id", "sku_id"]]).isin(pd.MultiIndex.from_frame(rows))
        # Southeast beverage CDC rows change, and so do the RDCs that pool them; nothing else may
        rdc_pool = m.dc_id.str.startswith("RDC") & m.sku_id.isin(rows.sku_id)
        self.assertTrue(changed[in_scope].any())
        self.assertFalse(changed[~in_scope & ~rdc_pool].any())
        np.testing.assert_allclose(m.target_mean_s[in_scope], 1.25 * m.target_mean_b[in_scope], rtol=1e-9)

    def test_pooling_alpha_override(self):
        sku = "SKU0001"
        plan = self.hooks({"id": "OVR-0001", "measure": "policy.pooling_alpha", "scope": {"sku_id": [sku]},
                           "operation": "set", "value": 1.0, "applies_to": ["plan"], "rationale": "test"})
        out = self.solve(plan)
        m = self.base.merge(out, on=["dc_id", "sku_id"], suffixes=("_b", "_s"))
        changed = m.safety_stock_b != m.safety_stock_s
        self.assertTrue(changed[(m.sku_id == sku) & m.dc_id.str.startswith("RDC")].any())
        self.assertFalse(changed[(m.sku_id != sku) | ~m.dc_id.str.startswith("RDC")].any())
        with self.assertRaises(engine.OverrideError):
            self.hooks({"id": "OVR-0001", "measure": "policy.pooling_alpha", "scope": {"sku_id": [sku]},
                        "operation": "set", "value": 1.0, "applies_to": ["plan"], "rationale": "test"},
                       model="meio_weekly")

    def test_tuned_maps_apply_per_sku(self):
        out = self.solve(alpha_by_sku={"SKU0001": 1.0}, z_by_sku={"SKU0002": 2.5})
        m = self.base.merge(out, on=["dc_id", "sku_id"], suffixes=("_b", "_s"))
        self.assertTrue((m[m.sku_id == "SKU0002"].z_s == 2.5).all())
        other = ~m.sku_id.isin(["SKU0001", "SKU0002"])
        self.assertTrue((m.safety_stock_b[other] == m.safety_stock_s[other]).all())


if __name__ == "__main__":
    unittest.main()
