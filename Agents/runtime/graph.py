"""
LangGraph runtime for the scenario agents.

Every agent is the same two-node loop -- `model` (Claude, bound to that agent's tools)
and `tools` (registry.dispatch, which enforces the agent's allowlist) -- compiled as its
own graph. The orchestrator's graph adds two things:

  delegate                a tool whose execution runs a specialist's graph to completion
                          on a self-contained brief and returns the specialist's report
  request_human_approval  routed to its own `approval` node, which pauses the graph with
                          interrupt(); only a person resumes it (Command(resume=...)), and
                          only then is approval.json written

The validator's graph starts with an `evidence` node that runs list_overrides and
validate_draft for the draft named in its brief, so its review always rests on the
tool results rather than on the brief's summary (see EVIDENCE).

Specialist graphs are compiled with checkpointer=False: they are pure functions of their
brief, can be invoked any number of times inside one orchestrator step, and never pause.
The orchestrator graph is checkpointed so a session can stop at the approval gate and
resume. The approval node is separate from the tools node because LangGraph re-runs an
interrupted node from its start on resume -- tools that already ran (draft writes) must
not run twice.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Annotated, Callable, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.types import Command, interrupt

from Agents.tools import paths, registry, scenario

from . import llm

APPROVAL_TOOL = "request_human_approval"
DELEGATE_TOOL = "delegate"
ORCHESTRATOR_RECURSION = 200
WORKER_RECURSION = 80
MAX_RESULT_CHARS = 60_000
# Evidence the runtime gathers before an agent's first model call. The live test showed
# a small model reviewing a draft from the brief alone, skipping the tool calls its
# instructions require; a review that never saw validate_draft cannot be trusted, so the
# graph guarantees the evidence instead of hoping for it.
EVIDENCE = {"validator_agent": ("list_overrides", "validate_draft")}


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def system_message(agent: str) -> SystemMessage:
    """Shared domain primer, then the agent's own instructions; byte-stable per agent so
    the prefix (tools + system) caches across the agent's turns."""
    shared = (paths.INSTRUCTIONS_DIR / "_shared_context.md").read_text(encoding="utf-8")
    own = (paths.INSTRUCTIONS_DIR / f"{agent}.md").read_text(encoding="utf-8")
    return SystemMessage(content=[{"type": "text", "text": shared},
                                  {"type": "text", "text": own, "cache_control": {"type": "ephemeral"}}])


def text_of(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text").strip()


def _last_ai(messages) -> AIMessage:
    return next(m for m in reversed(messages) if isinstance(m, AIMessage))


def _tool_message(call: dict, result: dict) -> ToolMessage:
    content = json.dumps(registry.to_jsonable(result), default=str)
    if len(content) > MAX_RESULT_CHARS:
        content = content[:MAX_RESULT_CHARS] + " ... [truncated; narrow the request]"
    return ToolMessage(content=content, tool_call_id=call["id"], name=call["name"],
                       status="error" if result.get("ok") is False else "success")


class Team:
    """Compiled graphs for every agent, built from one model profile."""

    def __init__(self, profile: str | None = None, model_factory: Callable | None = None,
                 checkpointer=None):
        if model_factory is None:
            profile = llm.profile_name(profile)
            model_factory = lambda agent: llm.chat_model(agent, profile)  # noqa: E731
        self.profile = profile
        self._factory = model_factory
        self.workers = {a: self._compile(a, checkpointer=False) for a in registry.AGENTS if a != "orchestrator"}
        self.orchestrator = self._compile("orchestrator", checkpointer=checkpointer or InMemorySaver())

    # ------------------------------------------------------------- graphs --
    def _compile(self, agent: str, checkpointer):
        model = self._factory(agent).bind_tools(registry.tool_specs(agent))
        system = system_message(agent)
        gated = APPROVAL_TOOL in registry.AGENTS[agent]["tools"]

        def call_model(state: AgentState):
            return {"messages": [model.invoke([system, *state["messages"]])]}

        def run_tools(state: AgentState):
            calls = _last_ai(state["messages"]).tool_calls
            return {"messages": [_tool_message(c, self._execute(agent, c))
                                 for c in calls if not (gated and c["name"] == APPROVAL_TOOL)]}

        def approval(state: AgentState):
            calls = _last_ai(state["messages"]).tool_calls
            return {"messages": [_tool_message(c, self._approval_gate(c["args"]))
                                 for c in calls if c["name"] == APPROVAL_TOOL]}

        def after_model(state: AgentState):
            return "tools" if _last_ai(state["messages"]).tool_calls else END

        def after_tools(state: AgentState):
            calls = _last_ai(state["messages"]).tool_calls
            return "approval" if any(c["name"] == APPROVAL_TOOL for c in calls) else "model"

        def gather_evidence(state: AgentState):
            draft = re.search(r"\bD\d{4}\b", text_of(state["messages"][0]))
            if not draft:
                return {"messages": []}
            results = {name: registry.dispatch(agent, name, {"draft_id": draft.group(0)})
                       for name in EVIDENCE[agent]}
            return {"messages": [HumanMessage(
                f"Evidence the runtime gathered for draft {draft.group(0)} before your review -- the results "
                f"of {', '.join(EVIDENCE[agent])} (validate_draft includes every override's impact preview):\n"
                + json.dumps(registry.to_jsonable(results), default=str))]}

        g = StateGraph(AgentState)
        g.add_node("model", call_model)
        g.add_node("tools", run_tools)
        if agent in EVIDENCE:
            g.add_node("evidence", gather_evidence)
            g.add_edge(START, "evidence")
            g.add_edge("evidence", "model")
        else:
            g.add_edge(START, "model")
        g.add_conditional_edges("model", after_model, ["tools", END])
        if gated:
            g.add_node("approval", approval)
            g.add_conditional_edges("tools", after_tools, ["approval", "model"])
            g.add_edge("approval", "model")
        else:
            g.add_edge("tools", "model")
        return g.compile(checkpointer=checkpointer, name=agent)

    # -------------------------------------------------------------- tools --
    def _execute(self, agent: str, call: dict) -> dict:
        if call["name"] == DELEGATE_TOOL and DELEGATE_TOOL in registry.AGENTS[agent]["tools"]:
            return self._delegate(call["args"])
        return registry.dispatch(agent, call["name"], call["args"])

    def _delegate(self, args: dict) -> dict:
        target, task = args.get("agent"), args.get("task") or ""
        if target not in self.workers:
            return {"ok": False, "error": f"cannot delegate to {target!r}; agents: {sorted(self.workers)}"}
        try:
            final = self.workers[target].invoke({"messages": [HumanMessage(task)]},
                                                {"recursion_limit": WORKER_RECURSION})
        except GraphRecursionError:
            return {"ok": False, "agent": target, "error": f"{target} did not finish in {WORKER_RECURSION} steps"}
        return {"ok": True, "agent": target, "report": text_of(final["messages"][-1])}

    @staticmethod
    def _approval_gate(args: dict) -> dict:
        folder = args.get("scenario_folder", "")
        try:
            card = scenario.scenario_dir(folder) / "scenario_card.md"
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not card.exists():
            return {"ok": False, "error": f"no scenario card for {folder}; call build_scenario_config first"}
        command = f'python -m Agents.tools approve {folder} --by "<name>"'
        decision = interrupt({"type": "approval", "scenario_folder": folder,
                              "card": card.read_text(encoding="utf-8"), "approve_command": command})
        if decision.get("approved"):
            record = scenario.approve(folder, decision.get("approved_by") or "planner", note=decision.get("note", ""))
            return {"ok": True, "state": "approved", "approved_by": record["approved_by"],
                    "config_sha256": record["config_sha256"]}
        if decision.get("pending"):
            return {"ok": True, "state": "pending", "detail": f"no person available; approve later with: {command}"}
        return {"ok": True, "state": "declined", "planner_feedback": decision.get("feedback", "")}


class Session:
    """One planner conversation with the orchestrator."""

    def __init__(self, team: Team, decide: Callable[[dict], dict], thread_id: str | None = None):
        self.team, self.decide = team, decide
        self.thread_id = thread_id or uuid.uuid4().hex[:12]
        self.config = {"configurable": {"thread_id": self.thread_id}, "recursion_limit": ORCHESTRATOR_RECURSION}
        self.transcript = paths.WORKSPACE / "transcripts" / f"{self.thread_id}.jsonl"

    def ask(self, text: str) -> str:
        result = self.team.orchestrator.invoke({"messages": [HumanMessage(text)]}, self.config)
        while result.get("__interrupt__"):
            decision = self.decide(result["__interrupt__"][0].value)
            result = self.team.orchestrator.invoke(Command(resume=decision), self.config)
        self._save(result["messages"])
        return text_of(result["messages"][-1])

    def _save(self, messages) -> None:
        from .bridge import _to_wire
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        with open(self.transcript, "w", encoding="utf-8") as f:
            for m in messages:
                row = _to_wire(m)
                if isinstance(m, AIMessage) and m.response_metadata.get("model_name"):
                    row["model"] = m.response_metadata["model_name"]
                f.write(json.dumps(row, default=str) + "\n")
