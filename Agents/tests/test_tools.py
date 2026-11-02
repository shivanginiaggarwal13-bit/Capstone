"""
Tests for the scenario-agent tools (the LangGraph runtime is covered by test_runtime.py).

    python -m pytest Agents/tests -q        (or: python -m unittest discover Agents/tests)

They read the real input datasets and existing scenario outputs, and write only
to a temporary agent workspace.
"""
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SUPPLY_BRAIN_AGENT_WORKSPACE", tempfile.mkdtemp(prefix="sb_agents_"))
os.environ.setdefault("SUPPLY_BRAIN_OVERRIDES_ROOT", tempfile.mkdtemp(prefix="sb_overrides_"))

from Agents.tools import catalog, context, drafts, engine, paths, registry, scenario  # noqa: E402

# run_scenario starts MEIO + the Twin in a detached process; tests record the launch
# instead, so no test ever writes a real run into output datasets/.
LAUNCHES = mock.patch.object(scenario.subprocess, "Popen")


def setUpModule():
    LAUNCHES.start()


def tearDownModule():
    LAUNCHES.stop()


def call(agent, tool, **args):
    return registry.dispatch(agent, tool, args)


def new_draft(name="Test scenario"):
    return drafts.create_draft(name, "test", "test request")["draft_id"]


class ContractTests(unittest.TestCase):
    def test_schema_enum_matches_catalog(self):
        schema = json.loads(paths.SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["$defs"]["override"]["properties"]["measure"]["enum"]), set(catalog.MEASURES))

    def test_scope_keys_exist_in_universe(self):
        for name, m in catalog.MEASURES.items():
            cols = set(context.universe(m.universe).columns)
            self.assertTrue(set(m.scope_keys) <= cols, f"{name}: {set(m.scope_keys) - cols}")

    def test_every_agent_has_instructions_and_known_tools(self):
        for agent, cfg in registry.AGENTS.items():
            self.assertTrue((paths.INSTRUCTIONS_DIR / f"{agent}.md").exists(), agent)
            for spec in registry.tool_specs(agent):
                self.assertIn(spec["name"], registry.TOOLS)
                self.assertEqual(spec["input_schema"]["type"], "object")
                self.assertTrue(spec["description"])

    def test_specialists_cannot_approve_or_run(self):
        for agent in registry.SPECIALISTS + ("validator_agent", "analyst_agent"):
            tools = set(registry.AGENTS[agent]["tools"])
            self.assertFalse(tools & {"run_scenario", "request_human_approval", "delegate", "build_scenario_config"})

    def test_propose_enum_is_domain_scoped(self):
        spec = next(s for s in registry.tool_specs("supply_agent") if s["name"] == "propose_override")
        self.assertTrue(all(m.startswith("supply.") for m in spec["input_schema"]["properties"]["measure"]["enum"]))


class ContextTests(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(context.resolve_window(month="2026-07")["window"],
                         {"start_period": "2026-W28", "end_period": "2026-W31"})
        self.assertEqual(context.resolve_window(start_date="2025-12-29")["window"]["start_period"], "2026-W01")
        self.assertEqual(context.resolve_window(start_period="2026-W50", weeks=3)["window"]["end_period"], "2026-W52")
        with self.assertRaises(ValueError):
            context.resolve_window(start_date="2027-03-01")

    def test_canonicalize(self):
        self.assertEqual(context.canonicalize("region", ["southeast"]), (["Southeast"], []))
        self.assertEqual(context.canonicalize("echelon", ["CUSTOMER_DC", "rdc"])[0], ["CDC", "RDC"])
        _vals, errors = context.canonicalize("plant_id", ["PLANT1"])
        self.assertIn("PLANT01", errors[0])

    def test_pdc_region_derived(self):
        self.assertEqual(context.nodes().set_index("dc_id").region["PDC01"], "Southeast")


class DraftTests(unittest.TestCase):
    def test_add_validate_and_overlap(self):
        did = new_draft()
        july = context.resolve_window(month="2026-07")["window"]
        res = call("demand_agent", "propose_override", draft_id=did, measure="demand.volume",
                   scope={"region": ["Southeast"], "category": ["Beverages"]}, window=july,
                   operation="scale", value=1.25, rationale="heatwave")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["override"]["id"], "OVR-0001")
        self.assertAlmostEqual(res["impact"]["delta_units"] / res["impact"]["baseline_units"], 0.25, places=3)
        res2 = call("demand_agent", "propose_override", draft_id=did, measure="demand.volume",
                    scope={"region": ["Southeast"], "subcategory": ["Energy Drink"]},
                    window={"start_period": "2026-W29", "end_period": "2026-W29"},
                    operation="scale", value=1.1, rationale="spike")
        self.assertTrue(any(w["code"] == "overlap" and "1.375" in w["message"] for w in res2["warnings"]))
        self.assertTrue(drafts.validate_draft(did)["ok"])

    def test_rejections_save_nothing(self):
        did = new_draft()
        cases = [
            ("demand_agent", dict(measure="supply.plant_capacity", scope={"plant_id": ["PLANT01"]},
                                  operation="scale", value=0.7, rationale="x"), "may not write"),
            ("demand_agent", dict(measure="demand.volume", scope={"region": ["Atlantis"]},
                                  operation="scale", value=1.2, rationale="bad region"), "unknown_value"),
            ("demand_agent", dict(measure="demand.volume", scope={"region": ["West"]},
                                  operation="scale", value=9, rationale="too big"), "value_out_of_bounds"),
            ("inventory_agent", dict(measure="inventory.on_hand", scope={"dc_id": ["RDC02"]}, operation="scale",
                                     value=0.5, window={"start_period": "2026-W05", "end_period": "2026-W06"},
                                     rationale="mid-year"), "window_not_allowed"),
            ("inventory_agent", dict(measure="inventory.on_hand", scope={"dc_id": ["RDC02"]}, operation="scale",
                                     value=0.5, applies_to=["plan"], rationale="plan side"), "bad_side"),
            ("demand_agent", dict(measure="demand.volume", scope={"dc_id": ["CDC01"], "sku_id": ["SKU0318"]},
                                  operation="scale", value=1.5, rationale="not carried"), "empty_match"),
        ]
        for agent, args, expected in cases:
            res = call(agent, "propose_override", draft_id=did, **args)
            self.assertFalse(res["ok"])
            self.assertIn(expected, json.dumps(res))
        self.assertEqual(drafts.load_draft(did)["overrides"], [])

    def test_pooling_alpha_follows_the_configured_model(self):
        raw = dict(measure="policy.pooling_alpha", scope={"cluster": ["0"]}, operation="set", value=0.5,
                   rationale="pool")
        _, errors, _ = drafts.normalize_override(dict(raw), meio_model="meio_weekly")
        self.assertIn("needs_clark_scarf", json.dumps(errors))
        self.assertEqual(drafts.default_baseline()["meio_model"], "clark_scarf_v2")
        res = call("service_policy_agent", "propose_override", draft_id=new_draft(), **raw)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["impact"]["alpha"], {"portfolio": context.meio_parameters().alpha, "override": 0.5})

    def test_weeks_of_cover_inventory(self):
        did = new_draft()
        res = call("inventory_agent", "propose_override", draft_id=did, measure="inventory.on_hand",
                   scope={"region": ["West"], "echelon": ["CDC"]}, operation="set", value=1,
                   unit="weeks_of_cover", rationale="lean start")
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(res["impact"]["weeks_of_cover_after"], 1.0, places=2)

    def test_plant_dc_cover_uses_throughput(self):
        did = new_draft()
        res = call("inventory_agent", "propose_override", draft_id=did, measure="inventory.on_hand",
                   scope={"dc_id": ["PDC01"], "production_family": ["CONFECTIONERY"]}, operation="add", value=2,
                   unit="weeks_of_cover", rationale="pre-build ahead of line work")
        self.assertTrue(res["ok"], res)
        self.assertGreater(res["impact"]["units_after"], res["impact"]["units_before"])
        self.assertAlmostEqual(res["impact"]["weeks_of_cover_after"] - res["impact"]["weeks_of_cover_before"],
                               2.0, places=2)


class MacroTests(unittest.TestCase):
    def test_library_translates_or_explains(self):
        did = new_draft()
        refused = []
        for row in context.library_rows():
            res = call("orchestrator", "apply_library_scenario", draft_id=did, scenario_id=row["scenario_id"])
            if not res["ok"]:
                self.assertIn("Alternatives", res["error"])
                refused.append(row["scenario_id"])
        self.assertEqual(refused, ["SCN0001", "SCN0003", "SCN0005", "SCN0006"])
        self.assertTrue(drafts.validate_draft(did)["ok"])

    def test_library_respects_domains(self):
        did = new_draft()
        self.assertFalse(call("demand_agent", "apply_library_scenario", draft_id=did, scenario_id="SCN0018")["ok"])

    def test_promo_matches_generator(self):
        did = new_draft()
        window = {"start_period": "2026-W20", "end_period": "2026-W21"}
        res = call("economics_agent", "add_price_event", draft_id=did, scope={"subcategory": ["Candy"]},
                   price_change_pct=-0.2, kind="promo", window=window, rationale="TPR")
        self.assertTrue(res["ok"], res)
        lift, dip1, dip2 = (a["value"] for a in res["added"])
        self.assertAlmostEqual(lift, 0.8 ** catalog.PROMO_ELASTICITY["Snacks & Confectionery"], places=3)
        dip = 0.9 * min(0.2 / 0.30, 1.0) * 0.20
        self.assertAlmostEqual(dip1, 1 - dip, places=3)
        self.assertAlmostEqual(dip2, 1 - 0.5 * dip, places=3)
        removed = call("economics_agent", "remove_override", draft_id=did, group=res["group"])
        self.assertEqual(len(removed["removed"]), 3)


class ScenarioGateTests(unittest.TestCase):
    def test_build_approve_and_refuse(self):
        did = new_draft("Gate test")
        call("demand_agent", "propose_override", draft_id=did, measure="demand.volume",
             scope={"region": ["West"]}, window={"start_period": "2026-W10", "end_period": "2026-W12"},
             operation="scale", value=1.1, rationale="test")
        built = call("orchestrator", "build_scenario_config", draft_id=did)
        self.assertTrue(built["ok"], built)
        folder = built["scenario"]
        self.assertGreaterEqual(int(folder.split("_")[1]), 3)
        cfg_path = scenario.scenario_dir(folder) / "scenario_config.json"
        self.assertEqual(cfg_path.parent.parent, paths.overrides_root())
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        root = json.loads(paths.ROOT_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(cfg["meio"], root["meio"])                     # MEIO exactly as the config defines
        self.assertEqual(cfg["overrides"]["output_root"], root["overrides"]["output_root"])
        self.assertTrue(cfg["artifacts"]["scenario"].endswith(folder))
        self.assertEqual(cfg["changes"]["override_schema_version"], catalog.OVERRIDE_SCHEMA_VERSION)
        self.assertIn("not approved", call("orchestrator", "run_scenario", scenario_folder=folder)["error"])
        scenario.approve(folder, "Tester")
        self.assertEqual(scenario.approval_status(folder)["state"], "approved")
        launched = call("orchestrator", "run_scenario", scenario_folder=folder)
        if engine.readiness()["ready_to_run"]:
            self.assertTrue(launched["ok"], launched)
            args = scenario.subprocess.Popen.call_args[0][0]
            self.assertEqual(args[-2:], ["run-job", folder])
        else:
            self.assertIn("cannot apply", launched["error"])
        cfg_path.write_text(cfg_path.read_text(encoding="utf-8").replace("1.1", "1.2", 1), encoding="utf-8")
        self.assertEqual(scenario.approval_status(folder)["state"], "stale")

    def test_arms(self):
        self.assertEqual(scenario.arms_for([{"applies_to": ["plan", "actual"]}]), ["prepared", "unprepared", "reactive"])
        self.assertEqual(scenario.arms_for([{"applies_to": ["plan"]}]), ["prepared"])
        self.assertEqual(scenario.arms_for([{"applies_to": ["actual"]}]), ["unprepared", "reactive"])


@unittest.skipUnless((paths.PROJECTIONS_DIR / "SCENARIO_0002_MEIO_CLARK_SCARF_V2" / "meio_policy").exists()
                     and (paths.PROJECTIONS_DIR / "SCENARIO_0001_Baseline_Forward_Plan" / "baseline").exists(),
                     "needs the committed SCENARIO_0001/0002 runs")
class AnalysisTests(unittest.TestCase):
    base = {"baseline_scenario": "SCENARIO_0001_Baseline_Forward_Plan", "baseline_run": "baseline"}

    def test_compare_reproduces_known_result(self):
        res = call("analyst_agent", "compare_runs", scenario_folder="SCENARIO_0002_MEIO_CLARK_SCARF_V2", **self.base)
        rows = {r["kpi"]: r for r in res["kpis"]}
        def kpi(scn, run):
            return json.loads((paths.PROJECTIONS_DIR / scn / run / "run_kpis.json").read_text())
        mine = kpi("SCENARIO_0002_MEIO_CLARK_SCARF_V2", "meio_policy")
        static = kpi("SCENARIO_0001_Baseline_Forward_Plan", "baseline")
        self.assertAlmostEqual(rows["customer_fill_rate"]["meio_policy_vs_baseline"],
                               round((mine["customer_fill_rate"] - static["customer_fill_rate"]) * 100, 2))
        self.assertGreater(rows["customer_fill_rate"]["meio_policy_vs_baseline"], 0)   # MEIO beats the static policy
        self.assertLess(rows["internal_replen_fill_rate"]["meio_policy_vs_baseline"], 0)  # ... at internal fill's cost

    def test_breakdowns(self):
        res = call("analyst_agent", "service_breakdown", scenario_folder="SCENARIO_0002_MEIO_CLARK_SCARF_V2",
                   run="meio_policy", by="segment", **self.base)
        self.assertEqual(res["rows_total"], 3)
        diff = call("analyst_agent", "policy_diff", scenario_folder="SCENARIO_0002_MEIO_CLARK_SCARF_V2",
                    baseline_scenario="SCENARIO_0001_Baseline_Forward_Plan", by="echelon")
        self.assertEqual({r["echelon"] for r in diff["rows"]}, {"PDC", "RDC", "CDC"})
        july = call("analyst_agent", "policy_diff", scenario_folder="SCENARIO_0002_MEIO_CLARK_SCARF_V2",
                    baseline_scenario="SCENARIO_0001_Baseline_Forward_Plan", by="echelon",
                    start_period="2026-W28", end_period="2026-W31")
        self.assertEqual(july["window"], "2026-W28..2026-W31")
        self.assertNotEqual(july["rows"], diff["rows"])

    def test_scope_service(self):
        """Fill inside an override's own scope and window, per arm, scope resolved by the engine."""
        folder = paths.overrides_root() / "SCENARIO_0002_MEIO_CLARK_SCARF_V2"
        folder.mkdir(parents=True, exist_ok=True)
        surge = {"id": "OVR-0001", "measure": "demand.volume", "scope": {"region": ["Southeast"], "category": ["Beverages"]},
                 "window": {"start_period": "2026-W28", "end_period": "2026-W31"}, "operation": "scale",
                 "value": 1.25, "unit": "multiplier", "applies_to": ["plan", "actual"]}
        cut = {"id": "OVR-0002", "measure": "supply.plant_capacity",
               "scope": {"plant_id": ["PLANT01"], "production_family": ["LIQUID_FILL"]}, "operation": "scale",
               "value": 0.7, "unit": "multiplier", "applies_to": ["plan", "actual"]}
        (folder / "scenario_config.json").write_text(json.dumps(
            {"changes": {"overrides": [surge, cut]}, "evaluation": {"arms": ["meio_policy"]}}), encoding="utf-8")
        res = call("analyst_agent", "scope_service", scenario_folder="SCENARIO_0002_MEIO_CLARK_SCARF_V2",
                   override_id="OVR-0001", **self.base)
        self.assertEqual((res["pairs"], res["window"]), (325, "2026-W28..2026-W31"))
        arms = res["arms"]
        self.assertEqual(set(arms), {"baseline", "meio_policy"})
        self.assertEqual(arms["baseline"]["customer_demand"], arms["meio_policy"]["customer_demand"])   # same draws
        self.assertIn("vs_baseline_pp", arms["meio_policy"])
        lanes = call("analyst_agent", "scope_service", scenario_folder="SCENARIO_0002_MEIO_CLARK_SCARF_V2",
                     override_id="OVR-0002", **self.base)
        self.assertFalse(lanes["ok"])


if __name__ == "__main__":
    unittest.main()
