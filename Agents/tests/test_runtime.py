"""
Tests for the LangGraph runtime: graph wiring, allowlists, delegation, the approval
interrupt, model profiles, and the Claude Desktop bridge -- all offline, with a scripted
chat model standing in for Claude.

    python -m pytest Agents/tests -q
"""
import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock

os.environ.setdefault("SUPPLY_BRAIN_AGENT_WORKSPACE", tempfile.mkdtemp(prefix="sb_agents_"))
os.environ.setdefault("SUPPLY_BRAIN_OVERRIDES_ROOT", tempfile.mkdtemp(prefix="sb_overrides_"))
os.environ.setdefault("NVIDIA_API_KEY", "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")

from langchain_core.language_models import BaseChatModel        # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult    # noqa: E402
from pydantic import Field                                        # noqa: E402

from Agents.runtime import bridge, graph, llm                    # noqa: E402
from Agents.tools import drafts, engine, paths, registry, scenario  # noqa: E402

# run_scenario would start MEIO + the Twin; record the launch instead (see test_tools.py).
LAUNCHES = mock.patch.object(scenario.subprocess, "Popen")


def setUpModule():
    LAUNCHES.start()


def tearDownModule():
    LAUNCHES.stop()


class ScriptedChatModel(BaseChatModel):
    """Replays scripted replies; each reply is an AIMessage or a function of the messages."""
    replies: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append({"messages": list(messages), "tools": [t["name"] for t in kwargs.get("tools", [])]})
        reply = self.replies.pop(0)
        return ChatResult(generations=[ChatGeneration(message=reply(messages) if callable(reply) else reply)])


def ai(*calls, text=""):
    return AIMessage(content=text, tool_calls=[{"name": n, "args": a, "id": f"call_{uuid.uuid4().hex[:8]}",
                                                "type": "tool_call"} for n, a in calls])


def tool_result(messages, name):
    hit = [m for m in messages if isinstance(m, ToolMessage) and m.name == name]
    return json.loads(hit[-1].content)


def team_with(scripts: dict):
    models = {a: ScriptedChatModel(replies=list(scripts.get(a, []))) for a in registry.AGENTS}
    return graph.Team(model_factory=lambda agent: models[agent]), models


WEST = {"measure": "demand.volume", "scope": {"region": ["West"]}, "operation": "scale", "value": 1.1,
        "window": {"start_period": "2026-W10", "end_period": "2026-W13"}, "rationale": "March uplift in the West"}


def orchestrator_script(final_text):
    return [
        ai(("create_draft", {"scenario_name": "Runtime test", "description": "d", "request_text": "West +10% in March"})),
        lambda m: ai(("delegate", {"agent": "demand_agent",
                                   "task": f"draft_id {tool_result(m, 'create_draft')['draft_id']}: West +10% in March"})),
        lambda m: ai(("build_scenario_config", {"draft_id": tool_result(m, "create_draft")["draft_id"]})),
        lambda m: ai(("request_human_approval", {"scenario_folder": tool_result(m, "build_scenario_config")["scenario"]})),
        lambda m: ai(("run_scenario", {"scenario_folder": tool_result(m, "build_scenario_config")["scenario"]})),
        AIMessage(final_text),
    ]


DEMAND_SCRIPT = [
    lambda m: ai(("propose_override", {"draft_id": m[1].content.split()[1].rstrip(":"), **WEST})),
    AIMessage("Added OVR-0001: demand.volume x1.1 on the West, W10-W13."),
]


class GraphTests(unittest.TestCase):
    def test_full_flow_with_approval_interrupt(self):
        team, models = team_with({"orchestrator": orchestrator_script("done"), "demand_agent": DEMAND_SCRIPT})
        asked = []
        session = graph.Session(team, decide=lambda p: asked.append(p) or {"approved": True, "approved_by": "Tester"})
        drafts_before = len(list(paths.DRAFTS_DIR.glob("D*.json")))
        self.assertEqual(session.ask("Model a March uplift in the West"), "done")

        self.assertEqual(len(asked), 1)                                   # one pause, one person
        folder = asked[0]["scenario_folder"]
        self.assertIn("scenario card", asked[0]["card"])
        self.assertEqual(scenario.approval_status(folder)["state"], "approved")
        self.assertEqual(len(list(paths.DRAFTS_DIR.glob("D*.json"))), drafts_before + 1)  # no re-run on resume

        final = models["orchestrator"].seen[-1]["messages"]
        self.assertEqual(tool_result(final, "request_human_approval")["state"], "approved")
        self.assertEqual(tool_result(final, "delegate")["report"].split(":")[0], "Added OVR-0001")
        run = tool_result(final, "run_scenario")
        self.assertEqual(run["ok"], engine.readiness()["ready_to_run"])   # launched once the engine is in
        draft = drafts.load_draft(tool_result(final, "create_draft")["draft_id"])
        self.assertEqual([o["provenance"]["agent"] for o in draft["overrides"]], ["demand_agent"])
        self.assertTrue(session.transcript.exists())

    def test_allowlists_and_prompt(self):
        team, models = team_with({"orchestrator": orchestrator_script("done"), "demand_agent": DEMAND_SCRIPT})
        graph.Session(team, decide=lambda p: {"approved": False}).ask("go")
        orch_tools, demand_tools = models["orchestrator"].seen[0]["tools"], models["demand_agent"].seen[0]["tools"]
        self.assertIn("delegate", orch_tools)
        self.assertIn("request_human_approval", orch_tools)
        self.assertNotIn("propose_override", orch_tools)
        self.assertFalse({"delegate", "request_human_approval", "run_scenario"} & set(demand_tools))
        system = models["demand_agent"].seen[0]["messages"][0]
        self.assertIn("shared context", system.content[0]["text"])
        self.assertIn("cache_control", system.content[-1])
        self.assertEqual(models["demand_agent"].seen[0]["messages"][1].type, "human")   # brief only

    def test_decline_keeps_scenario_unapproved(self):
        team, models = team_with({"orchestrator": orchestrator_script("revising"), "demand_agent": DEMAND_SCRIPT})
        session = graph.Session(team, decide=lambda p: {"approved": False, "feedback": "make it 15%"})
        session.ask("go")
        final = models["orchestrator"].seen[-1]["messages"]
        gate = tool_result(final, "request_human_approval")
        self.assertEqual((gate["state"], gate["planner_feedback"]), ("declined", "make it 15%"))
        folder = tool_result(final, "build_scenario_config")["scenario"]
        self.assertEqual(scenario.approval_status(folder)["state"], "awaiting_approval")
        self.assertIn("not approved", tool_result(final, "run_scenario")["error"])


class EvidenceTests(unittest.TestCase):
    def test_validator_reviews_with_evidence_in_hand(self):
        did = drafts.create_draft("Evidence test", "d", "West +10% in March")["draft_id"]
        registry.dispatch("demand_agent", "propose_override", {"draft_id": did, **WEST})
        team, models = team_with({"validator_agent": [AIMessage("PASS")]})
        out = team.workers["validator_agent"].invoke({"messages": [HumanMessage(f"Review draft {did}")]})
        seen = models["validator_agent"].seen[0]["messages"]           # the validator's FIRST model call
        evidence = seen[2]
        self.assertEqual((seen[1].content, evidence.type), (f"Review draft {did}", "human"))
        self.assertIn('"validate_draft"', evidence.content)
        self.assertIn("OVR-0001", evidence.content)
        self.assertEqual(graph.text_of(out["messages"][-1]), "PASS")


class ProfileTests(unittest.TestCase):
    def test_nvidia_profile_uses_openai(self):
        from langchain_openai import ChatOpenAI
        model = llm.chat_model("orchestrator", "nvidia")
        self.assertIsInstance(model, ChatOpenAI)
        self.assertEqual(model.model_name, "z-ai/glm-5.3-flash")
        self.assertEqual(model.max_tokens, 4096)
        self.assertEqual(model.temperature, 0.5)
        self.assertEqual(model.openai_api_base, llm.NVIDIA_API_URL)

    def test_nvidia_profile_settings(self):
        settings = llm.agent_settings("orchestrator", "nvidia")
        self.assertEqual(settings["model"], "z-ai/glm-5.3-flash")
        self.assertEqual(settings["max_tokens"], 4096)
        self.assertEqual(settings["temperature"], 0.5)

    def test_production_profile_settings(self):
        settings = llm.agent_settings("orchestrator", "production")
        self.assertEqual(settings["model"], "z-ai/glm-5.3-flash")
        self.assertEqual(settings["max_tokens"], 2048)
        self.assertEqual(settings["temperature"], 0.3)

    def test_desktop_profile_uses_bridge(self):
        self.assertIsInstance(llm.chat_model("orchestrator", "desktop"), bridge.DesktopBridgeChatModel)
        self.assertEqual(llm.agent_settings("supply_agent", "desktop")["model"], "haiku")


class BridgeTests(unittest.TestCase):
    def test_round_trip_through_mailbox(self):
        folder = bridge.start_session()
        models = {a: bridge.DesktopBridgeChatModel(agent=a, model_alias="haiku", poll_seconds=0.05,
                                                   timeout_seconds=30) for a in registry.AGENTS}
        team = graph.Team(model_factory=lambda agent: models[agent])
        seen = []

        def operator():
            answered = 0
            while answered < 2:
                for req in sorted(folder.glob("req_*.json")):
                    res = folder / req.name.replace("req_", "res_")
                    if res.exists():
                        continue
                    r = json.loads(req.read_text(encoding="utf-8"))
                    seen.append(r)
                    reply = ({"text": "", "tool_calls": [{"name": "resolve_window", "args": {"month": "2026-07"}}]}
                             if len(r["messages"]) == 1 else {"text": "July is 2026-W28..W31", "tool_calls": []})
                    res.write_text(json.dumps({**reply, "model_id": "scripted-operator"}), encoding="utf-8")
                    answered += 1
                time.sleep(0.05)

        t = threading.Thread(target=operator, daemon=True)
        t.start()
        session = graph.Session(team, decide=lambda p: {"pending": True})
        self.assertEqual(session.ask("Which weeks are July 2026?"), "July is 2026-W28..W31")
        t.join(timeout=10)
        first, second = seen
        self.assertEqual((first["agent"], first["model"]), ("orchestrator", "haiku"))
        self.assertIn("shared context", first["system"])
        self.assertIn("delegate", [x["name"] for x in first["tools"]])
        self.assertEqual(second["messages"][-1]["role"], "tool")
        self.assertIn("2026-W28", second["messages"][-1]["content"])
        self.assertIn("scripted-operator", session.transcript.read_text(encoding="utf-8"))
        rendered = (folder / "req_0002.md").read_text(encoding="utf-8")
        self.assertIn("## SYSTEM PROMPT", rendered)
        self.assertIn("tool result for `resolve_window`", rendered)
        bridge.end_session("ok")
        self.assertEqual(bridge.wait(timeout=1), 3)


if __name__ == "__main__":
    unittest.main()
