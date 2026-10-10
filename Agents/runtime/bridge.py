"""
Claude Desktop bridge: the `desktop` provider, for running the LangGraph agents with no
API key.

The graph is unchanged -- only the chat model differs. Every model call is written to a
mailbox as a request file; an operator session in Claude Desktop answers it by giving
the request to a subagent on the profile's model (Sonnet for the orchestrator, Haiku for
the workers), which writes the next assistant turn back; the graph then continues exactly
as it would on the API. The human approval interrupt uses the same mailbox, and the
operator relays the person's decision.

    Agents/workspace/bridge/current.json             -> the active session folder
    Agents/workspace/bridge/<session>/
      req_0001.json        seq, agent, model, conversation, system, tools, messages
      res_0001.json        {"text": "...", "tool_calls": [{"name": ..., "args": {...}}],
                            "model_id": "<model that answered>"}
      approval_request.json / approval_response.json
      done.json            written when the session ends

Operator commands:

    python -m Agents.runtime.bridge wait [--timeout 600]   next pending request (exit 0),
                                                            approval (4), session done (3)
    python -m Agents.runtime.bridge status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from Agents.tools import drafts, paths

BRIDGE_DIR = paths.WORKSPACE / "bridge"
_session: Path | None = None


# ----------------------------------------------------------------- session --
def start_session() -> Path:
    global _session
    _session = BRIDGE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    _session.mkdir(parents=True, exist_ok=True)
    _write(BRIDGE_DIR / "current.json", {"session": str(_session), "started_at": drafts.now()})
    return _session


def end_session(summary: str = "") -> None:
    _write(session_dir() / "done.json", {"finished_at": drafts.now(), "summary": summary})


def session_dir() -> Path:
    if _session is not None:
        return _session
    current = BRIDGE_DIR / "current.json"
    if not current.exists():
        raise RuntimeError("no bridge session; start the runtime with --profile desktop")
    return Path(json.loads(current.read_text(encoding="utf-8"))["session"])


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _await(path: Path, timeout: float, poll: float) -> dict:
    """Wait for an answer file; tolerate one caught mid-write."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        time.sleep(poll)
    raise TimeoutError(f"no answer at {path} after {timeout:.0f}s")


# -------------------------------------------------------------- wire format --
def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _to_wire(message) -> dict:
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": _text(message.content)}
    if isinstance(message, AIMessage):
        return {"role": "assistant", "text": _text(message.content),
                "tool_calls": [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in message.tool_calls]}
    if isinstance(message, ToolMessage):
        return {"role": "tool", "tool_call_id": message.tool_call_id, "name": message.name,
                "content": _text(message.content)}
    return {"role": message.type, "content": _text(message.content)}


def _pretty(content: str) -> str:
    try:
        return json.dumps(json.loads(content), indent=1)
    except (TypeError, ValueError):
        return content


def render(request: dict) -> str:
    """The request as readable markdown -- what the answering session reads, and an
    audit record of exactly what each model call saw."""
    out = [f"# Model call {request['seq']} -- agent `{request['agent']}` (model: {request['model']})", "",
           f"Write your reply to: {request['response_file']}", "", "## SYSTEM PROMPT", "", request["system"], "",
           f"## TOOLS ({len(request['tools'])})", ""]
    for t in request["tools"]:
        out += [f"### {t['name']}", t["description"], "input_schema:", json.dumps(t["input_schema"], indent=1), ""]
    out += [f"## CONVERSATION ({len(request['messages'])} messages)", ""]
    for i, m in enumerate(request["messages"], 1):
        if m["role"] == "assistant":
            out += [f"### [{i}] assistant (you, earlier)", m["text"] or "(no text)"]
            out += [f"- tool call `{c['name']}` (id {c['id']}): {json.dumps(c['args'])}" for c in m["tool_calls"]]
        elif m["role"] == "tool":
            out += [f"### [{i}] tool result for `{m['name']}` (id {m['tool_call_id']})", _pretty(m["content"])]
        else:
            out += [f"### [{i}] {m['role']}", m["content"]]
        out.append("")
    return "\n".join(out)


class DesktopBridgeChatModel(BaseChatModel):
    """A chat model whose every call is answered through the bridge mailbox."""

    agent: str
    model_alias: str
    timeout_seconds: float = 3600.0
    poll_seconds: float = 0.5

    @property
    def _llm_type(self) -> str:
        return "claude-desktop-bridge"

    def bind_tools(self, tools, **kwargs):
        specs = [t for t in tools if isinstance(t, dict) and {"name", "input_schema"} <= set(t)]
        if len(specs) != len(tools):
            raise TypeError("the desktop bridge takes Anthropic-format tool dicts (registry.tool_specs)")
        return self.bind(tools=specs, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = "\n\n".join(_text(m.content) for m in messages if isinstance(m, SystemMessage))
        convo = [m for m in messages if not isinstance(m, SystemMessage)]
        first = _text(convo[0].content) if convo else ""
        folder = session_dir()
        seq = 1 + len(list(folder.glob("req_*.json")))
        response = folder / f"res_{seq:04d}.json"
        request = {"seq": seq, "agent": self.agent, "model": self.model_alias,
                   "conversation": hashlib.sha1(f"{self.agent}|{first}".encode()).hexdigest()[:10],
                   "system": system, "tools": kwargs.get("tools") or [],
                   "messages": [_to_wire(m) for m in convo], "response_file": str(response)}
        (folder / f"req_{seq:04d}.md").write_text(render(request), encoding="utf-8")
        _write(folder / f"req_{seq:04d}.json", request)
        reply = _await(response, self.timeout_seconds, self.poll_seconds)
        calls = [{"name": c["name"], "args": c.get("args") or {}, "id": f"toolu_bridge_{seq:04d}_{i}",
                  "type": "tool_call"} for i, c in enumerate(reply.get("tool_calls") or [])]
        message = AIMessage(content=reply.get("text") or "", tool_calls=calls,
                            response_metadata={"model_name": reply.get("model_id"), "bridge_seq": seq})
        return ChatResult(generations=[ChatGeneration(message=message)])


# ---------------------------------------------------------- approval relay --
def await_approval(payload: dict) -> dict:
    """The approval interrupt, relayed: the operator shows the card to the person and
    writes their decision {"approved": bool, "approved_by": str, "feedback": str}."""
    folder = session_dir()
    response = folder / "approval_response.json"
    if response.exists():                       # a fresh decision for every request
        response.rename(folder / f"approval_response_{time.strftime('%H%M%S')}.json")
    _write(folder / "approval_request.json", {**payload, "response_file": str(response)})
    return _await(response, timeout=24 * 3600, poll=1.0)


# ---------------------------------------------------------------- operator --
def _pending(folder: Path) -> Path | None:
    for req in sorted(folder.glob("req_*.json")):
        if not (folder / req.name.replace("req_", "res_")).exists():
            return req
    return None


def _summary(req: Path) -> dict:
    r = json.loads(req.read_text(encoding="utf-8"))
    last = r["messages"][-1] if r["messages"] else {}
    preview = last.get("content") or last.get("text") or ""
    return {"pending": "model_call", "seq": r["seq"], "agent": r["agent"], "model": r["model"],
            "conversation": r["conversation"], "messages": len(r["messages"]),
            "tools": [t["name"] for t in r["tools"]], "last_message_role": last.get("role"),
            "last_message_preview": preview[:500], "request_markdown": str(req.with_suffix(".md")),
            "response_file": r["response_file"]}


def wait(timeout: float) -> int:
    folder = session_dir()
    deadline = time.time() + timeout
    while time.time() < deadline:
        req = _pending(folder)
        if req:
            print(json.dumps(_summary(req), indent=2))
            return 0
        approval = folder / "approval_request.json"
        if approval.exists() and not (folder / "approval_response.json").exists():
            a = json.loads(approval.read_text(encoding="utf-8"))
            print(json.dumps({"pending": "approval", "scenario_folder": a["scenario_folder"],
                              "request_file": str(approval), "response_file": a["response_file"]}, indent=2))
            return 4
        if (folder / "done.json").exists():
            print((folder / "done.json").read_text(encoding="utf-8"))
            return 3
        time.sleep(0.5)
    print(json.dumps({"pending": None, "detail": f"nothing new after {timeout:.0f}s"}))
    return 5


def status() -> int:
    folder = session_dir()
    reqs = sorted(folder.glob("req_*.json"))
    rows = []
    for req in reqs:
        r = json.loads(req.read_text(encoding="utf-8"))
        res = folder / req.name.replace("req_", "res_")
        answer = json.loads(res.read_text(encoding="utf-8")) if res.exists() else None
        rows.append({"seq": r["seq"], "agent": r["agent"], "model": r["model"],
                     "answered_by": answer.get("model_id") if answer else None,
                     "tool_calls": [c["name"] for c in (answer or {}).get("tool_calls") or []] if answer else "pending"})
    print(json.dumps({"session": str(folder), "calls": rows, "done": (folder / "done.json").exists()}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m Agents.runtime.bridge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("wait", help="block until the next model call or approval is pending")
    p.add_argument("--timeout", type=float, default=600)
    sub.add_parser("status", help="every model call in the session and who answered it")
    args = ap.parse_args(argv)
    return wait(args.timeout) if args.cmd == "wait" else status()


if __name__ == "__main__":
    sys.exit(main())
