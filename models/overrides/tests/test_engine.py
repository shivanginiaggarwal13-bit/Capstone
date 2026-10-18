"""
Override engine tests: strict compilation, then each override observed where it lands --
in the Digital Twin's simulated physics (8-week forward runs, same seed, with and without
overrides) and in MEIO's weekly sizing.

    python -m pytest models/overrides/tests -q
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

REPO = Path(__file__).resolve().parents[3]
for p in (REPO, REPO / "models", REPO / "models" / "twin", REPO / "models" / "MEIO" / "MEIO_Weekly" / "final_submission"):
    sys.path.insert(0, str(p))

import digital_twin                                      # noqa: E402
import meio_weekly_lib as lib                            # noqa: E402
from models import overrides                             # noqa: E402

DATA = REPO / "input datasets"
STATE = REPO / "output datasets" / "history" / "opening_position.parquet"
POLICY = REPO / "output datasets" / "history" / "forward_policy.parquet"
SEED, WEEKS = 2026, 8


def config(*ovs):
    return {"changes": {"override_schema_version": "1.0", "overrides": list(ovs)},
            "digital_twin": {"data_dir": "input datasets", "state": "output datasets/history/opening_position.parquet"}}


def ovr(oid, measure, scope, operation, value=None, window=None, sides=None, unit=None):
    o = {"id": oid, "measure": measure, "scope": scope, "operation": operation, "value": value,
         "rationale": "test"}
    if window:
        o["window"] = {"start_period": window[0], "end_period": window[1]}
    if sides:
        o["applies_to"] = sides
    if unit:
        o["unit"] = unit
    return o


SE_BEV = {"region": ["Southeast"], "category": ["Beverages"]}
TWIN_SET = config(
    ovr("OVR-0001", "demand.volume", SE_BEV, "scale", 1.25, ("2026-W02", "2026-W04")),
    ovr("OVR-0002", "inventory.on_hand", {"dc_id": ["RDC03"]}, "scale", 0.7),
    ovr("OVR-0003", "supply.plant_capacity", {"plant_id": ["PLANT01"], "production_family": ["LIQUID_FILL"]},
        "scale", 0.0, ("2026-W03", "2026-W03")),
    ovr("OVR-0004", "supply.lead_time_mean", {"destination_node_id": ["RDC02"]}, "add", 10.0, ("2026-W02", "2026-W02")),
    ovr("OVR-0005", "supply.lane_outage", {"lane_id": ["LANE051"]}, "block", window=("2026-W05", "2026-W05")),
    ovr("OVR-0006", "economics.holding_rate", {"all": True}, "scale", 2.0),
)


def setUpModule():
    global BASE, SCEN, FULL
    BASE = digital_twin.run_twin(DATA, "forward", SEED, WEEKS, POLICY, STATE, verbose=False)
    FULL = overrides.load(TWIN_SET)
    SCEN = digital_twin.run_twin(DATA, "forward", SEED, WEEKS, POLICY, STATE, verbose=False,
                                 overrides=FULL.side("actual", "twin_test"))


class CompileTests(unittest.TestCase):
    def test_empty_legacy_and_unknown(self):
        self.assertFalse(overrides.load({"changes": {}}))
        self.assertIsNone(overrides.load({"changes": {"demand_surge_pct": 25}}))
        with self.assertRaises(overrides.OverrideError):
            overrides.load({"changes": {"lead_time_shock": 5}})       # silently ignored before; refused now

    def test_strict_rejections(self):
        bad = [ovr("OVR-0001", "policy.pooling_alpha", {"cluster": ["0"]}, "set", 0.5),        # unsupported
               ovr("OVR-0001", "demand.volume", {"region": ["Atlantis"]}, "scale", 1.1),      # unknown value
               ovr("OVR-0001", "demand.volume", {"dc_id": ["CDC01"], "sku_id": ["SKU0318"]}, "scale", 1.5),  # no rows
               ovr("OVR-0001", "demand.volume", SE_BEV, "scale", 1.1, ("2027-W01", "2027-W02")),            # horizon
               ovr("OVR-0001", "inventory.on_hand", {"dc_id": ["RDC03"]}, "scale", 0.7, sides=["plan"])]     # side
        for o in bad:
            with self.assertRaises(overrides.OverrideError, msg=o["measure"]):
                overrides.load(config(o))

    def test_sides(self):
        self.assertEqual(len(FULL.side("plan")), 3)      # demand, capacity, lead time
        self.assertEqual(len(FULL.side("actual")), 6)


class TwinTests(unittest.TestCase):
    @staticmethod
    def demand(run):
        inv = run[3]
        return inv.groupby(["period", "dc_id", "sku_id"]).demand_customer_qty.sum()

    def test_demand_scaled_in_scope_only(self):
        base, scen = self.demand(BASE), self.demand(SCEN)
        rows = FULL.rows(FULL.of("demand.volume")[0])
        pairs = set(zip(rows.dc_id, rows.sku_id))
        idx = base.index.to_frame(index=False)
        in_scope = np.array([(d, s) in pairs for d, s in zip(idx.dc_id, idx.sku_id)])
        in_window = idx.period.isin(["2026-W02", "2026-W03", "2026-W04"]).to_numpy()
        hit = in_scope & in_window
        self.assertAlmostEqual(scen[hit].sum() / base[hit].sum(), 1.25, delta=0.01)
        np.testing.assert_array_equal(scen[~hit].to_numpy(), base[~hit].to_numpy())   # common random numbers

    def test_opening_stock_written_down(self):
        inv_b, inv_s = BASE[3], SCEN[3]
        w1 = lambda inv: inv[(inv.period == "2026-W01") & (inv.dc_id == "RDC03")].opening_stock.sum()  # noqa: E731
        self.assertAlmostEqual(w1(inv_s), 0.7 * w1(inv_b), delta=1.0)

    def test_line_capacity_cut_stops_production(self):
        prod = SCEN[5]
        cut = prod[(prod.plant_id == "PLANT01") & (prod.production_family == "LIQUID_FILL") & (prod.period == "2026-W03")]
        self.assertGreater(len(cut), 0)
        self.assertEqual(cut.planned_qty.sum(), 0.0)
        base = BASE[5]
        before = base[(base.plant_id == "PLANT01") & (base.production_family == "LIQUID_FILL") & (base.period == "2026-W03")]
        self.assertGreater(before.planned_qty.sum(), 0.0)

    def test_lead_time_and_outage(self):
        rc = SCEN[4]
        late = rc[(rc.dc_id == "RDC02") & (rc.period == "2026-W02") & (rc.origin_node_id.str.startswith("PDC"))]
        base_rc = BASE[4]
        ref = base_rc[(base_rc.dc_id == "RDC02") & (base_rc.period == "2026-W02") & (base_rc.origin_node_id.str.startswith("PDC"))]
        self.assertGreater(late.actual_lead_days.mean(), ref.actual_lead_days.mean() + 8)
        lane = digital_twin_lane("LANE051")
        held = rc[(rc.origin_node_id == lane[0]) & (rc.dc_id == lane[1]) & (rc.period == "2026-W05")]
        self.assertTrue(len(held) and (held.arrival_day >= 35).all())   # W05 = days 28..34; leaves day 35

    def test_holding_rate_doubles_carrying_cost(self):
        self.assertGreater(SCEN[2].cum_holding.sum(), BASE[2].cum_holding.sum() * 1.3)

    def test_receipts(self):
        applied = {r["override_id"]: r for r in SCEN[1]["overrides_applied"]}
        self.assertEqual(set(applied), {f"OVR-000{i}" for i in range(1, 7)})
        self.assertAlmostEqual(applied["OVR-0001"]["forecast_units_after"] /
                               applied["OVR-0001"]["forecast_units_before"], 1.25, places=6)
        self.assertEqual(applied["OVR-0002"]["pairs"], 425)


def digital_twin_lane(lane_id):
    ln = pd.read_parquet(DATA / "network_lanes.parquet").set_index("lane_id")
    return ln.origin_node_id[lane_id], ln.destination_node_id[lane_id]


class MeioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dfc = lib.load_demand_forecast(str(DATA))
        cls.pidx = lib.load_period_index(str(DATA))
        cls.si = lib.build_seasonal_index(str(DATA))
        cls.sub = lib.load_sku_subcategory(str(DATA))
        cls.asof = int(cls.pidx.loc[cls.pidx.period == "2025-W52", "week_num"].iloc[0])
        cls.week = int(cls.pidx.loc[cls.pidx.period == "2026-W28", "week_num"].iloc[0])
        cls.plan = overrides.PlanHooks(overrides.load(config(
            ovr("OVR-0001", "demand.volume", SE_BEV, "scale", 1.25, ("2026-W28", "2026-W31")),
            ovr("OVR-0002", "service.target_csl", {"segment": ["Fast-Moving"]}, "set", 0.985, ("2026-W28", "2026-W31")),
            ovr("OVR-0003", "policy.safety_stock", {"echelon": ["CDC"]}, "floor", 2.0, ("2026-W28", "2026-W28"),
                unit="weeks_of_cover"),
            ovr("OVR-0004", "supply.lead_time_mean", {"destination_node_id": ["RDC02"]}, "add", 7.0,
                ("2026-W28", "2026-W28")),
            ovr("OVR-0005", "supply.plant_capacity", {"plant_id": ["PLANT01"]}, "scale", 0.5, ("2026-W28", "2026-W28")),
        )).side("plan", "meio_test"))

    def demand(self, plan):
        return lib.target_week_demand(self.dfc, self.asof, self.pidx, self.si, self.sub, self.week, overrides=plan)

    def test_planned_demand_is_scaled_fully(self):
        base, scen = self.demand(None), self.demand(self.plan)
        m = base.merge(scen, on=["sku_id", "dc_id"], suffixes=("_b", "_s"))
        rows = self.plan.ovs.rows(self.plan.ovs.of("demand.volume")[0])
        hit = pd.MultiIndex.from_frame(m[["dc_id", "sku_id"]]).isin(pd.MultiIndex.from_frame(rows[["dc_id", "sku_id"]]))
        np.testing.assert_allclose(m.target_mean_s[hit], 1.25 * m.target_mean_b[hit], rtol=1e-9)
        np.testing.assert_allclose(m.target_std_s[hit], 1.25 * m.target_std_b[hit], rtol=1e-9)
        np.testing.assert_array_equal(m.target_mean_s[~hit], m.target_mean_b[~hit])

    def test_service_level_sets_z(self):
        d = self.demand(self.plan)
        d["segment"] = d.sku_id.map(pd.read_parquet(DATA / "sku_master.parquet").set_index("sku_id").segment)
        z = lib.assign_z(d, self.plan, "2026-W28").to_numpy()
        fast = (d.segment == "Fast-Moving").to_numpy()
        np.testing.assert_allclose(z[fast], norm.ppf(0.985))             # 2.17 instead of the segment's 2.05
        self.assertTrue((z[~fast] < 2.0).all())
        np.testing.assert_array_equal(lib.assign_z(d, self.plan, "2026-W32").to_numpy()[fast], 2.05)

    def test_policy_floor_carries_rop_and_s(self):
        pol = pd.DataFrame({"dc_id": ["CDC01", "RDC01"], "sku_id": ["SKU0001", "SKU0001"], "safety_stock": [1.0, 1.0],
                            "reorder_point": [5.0, 5.0], "order_up_to_level": [9.0, 9.0], "target_mean": [10.0, 10.0]})
        out = self.plan.policy(pol, "2026-W28")
        self.assertEqual(out.safety_stock.tolist(), [20.0, 1.0])          # floor of 2 weeks x 10 at the CDC only
        self.assertEqual(out.reorder_point.tolist(), [24.0, 5.0])         # s moves with SS
        self.assertEqual(out.order_up_to_level.tolist(), [28.0, 9.0])     # and S with s
        self.assertIs(self.plan.policy(pol, "2026-W29"), pol)              # outside the window: untouched

    def test_lead_time_and_capacity_per_week(self):
        rdc = ["RDC01", "RDC02", "RDC03", "RDC04", "RDC05", "RDC06", "RDC07", "RDC08"]
        base, _, _ = lib.build_leadtime_graph(str(DATA), rdc, [])
        lanes = self.plan.lanes("2026-W28")
        week, _, _ = lib.build_leadtime_graph(str(DATA), rdc, [], lanes)
        self.assertAlmostEqual(week["RDC02"][0], base["RDC02"][0] + 7.0)
        self.assertEqual(week["RDC01"], base["RDC01"])
        self.assertIsNone(self.plan.lanes("2026-W29"))
        fam, plant, _ = lib.load_plant_capacity(str(DATA))
        fam2, plant2 = self.plan.capacity(fam, plant, "2026-W28")
        self.assertEqual(plant2["PLANT01"], plant["PLANT01"] * 0.5)
        self.assertEqual(fam2[("PLANT01", "LIQUID_FILL")], fam[("PLANT01", "LIQUID_FILL")] * 0.5)
        self.assertEqual(plant2["PLANT02"], plant["PLANT02"])


if __name__ == "__main__":
    unittest.main()
