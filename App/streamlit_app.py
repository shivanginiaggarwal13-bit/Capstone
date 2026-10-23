"""Supply Brain -- MEIO Optimiser front end (Streamlit app).

Every button is wired to a real resource in this repository, or is shown as "not mapped" with the reason. See BUTTON_MAPPING.md for the full table.

    python -m streamlit run App/streamlit_app.py

Safety: nothing here overwrites canonical outputs unless you tick the matching box.
Segmentation and Simulation launches write to App/runs/ (a sandbox) by default.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
APP = Path(__file__).resolve().parent
RUNS = APP / "runs"
OUT = ROOT / "output datasets"
SEG = OUT / "segmentation"
CFG_PATH = ROOT / "scenario_config.json"
PY = sys.executable
AGENT_BRIDGE_DIR = ROOT / "Agents" / "workspace" / "bridge"

st.set_page_config(page_title="Supply Brain - MEIO Optimiser", page_icon=":material/psychology:", layout="wide")

# --------------------------------------------------------------------------- styling
# Design tokens live in App/.streamlit/config.toml (theme.*: color, font, radius,
# chart palette). This block covers what the theme API doesn't reach: text-wrapping
# fixes, the sidebar nav's selected-state treatment, module header bands, and the
# small badge/metric/card polish that makes the chrome read as one product.
st.markdown("""
<style>
.block-container {padding-top: 3.25rem; max-width: 1360px;}
[data-testid="stMetricLabel"] p, [data-testid="stMetricValue"], [data-testid="stWidgetLabel"] p {white-space: normal !important; overflow: visible !important; text-overflow: clip !important;}
button, button p, label p, [data-testid="stCaptionContainer"] p, [data-testid="stMarkdownContainer"] p {text-overflow: clip !important;}
button p {white-space: normal !important; overflow: visible !important; line-height: 1.25;}
button[kind] {height: auto !important; min-height: 2.4rem; padding: .4rem .9rem; font-weight: 500 !important;}
button[kind="primary"] {box-shadow: 0 1px 2px rgba(23,65,122,.18), 0 1px 1px rgba(23,65,122,.08);}
[data-testid="stMetricValue"] > div {white-space: normal !important; overflow: visible !important; text-overflow: clip !important; overflow-wrap: anywhere; line-height: 1.15;}
[data-testid="stMetricDelta"] {font-weight: 500;}

/* ---- sidebar: brand mark + nav ---- */
[data-testid="stSidebar"] {border-right: 1px solid #E6E7EC;}
[data-testid="stSidebarUserContent"] {padding-top: 1.5rem;}
.sb-brand {display: flex; align-items: center; gap: 10px; padding: 0 2px 14px 2px; margin-bottom: 14px; border-bottom: 1px solid #E6E7EC;}
.sb-brand-mark {display: flex; align-items: center; justify-content: center; width: 34px; height: 34px; border-radius: 9px;
  background: linear-gradient(145deg, #2374CC, #1A4F8C); color: #fff; font-size: 17px; flex-shrink: 0;
  box-shadow: 0 2px 5px rgba(23,65,122,.28);}
.sb-brand-text {line-height: 1.2;}
.sb-brand-name {font-weight: 700; font-size: 15.5px; color: #1C1E26; letter-spacing: -.01em;}
.sb-brand-sub {font-size: 11.5px; color: #878B98; font-weight: 500; letter-spacing: .03em; text-transform: uppercase;}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {font-size: 11px; font-weight: 600; letter-spacing: .06em;
  color: #9A9DA8; margin: 4px 2px 8px 2px;}
[data-testid="stSidebar"] [data-testid="stRadio"] label {width: 100%; padding: 8px 12px; margin-bottom: 2px; border-radius: 8px;
  border: 1px solid transparent; background: transparent; cursor: pointer; transition: background .12s, border-color .12s;}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {background: #ECEEF3;}
[data-testid="stSidebar"] [data-testid="stRadio"] label > div:first-child {display: none;}
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {background: #E5F0FB; border-color: #CBE0F7;
  box-shadow: inset 3px 0 0 #2374CC;}
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) p {color: #1558A0; font-weight: 600;}
[data-testid="stSidebar"] [data-testid="stRadio"] label p {font-size: 13.5px; color: #42454F;}

/* ---- module header bands (per-page section headers) ---- */
.mod-head {border-radius: 9px 9px 0 0; padding: 10px 14px; font-weight: 600; font-size: 15px; margin: -4px -4px 10px -4px;
  letter-spacing: -.005em;}
.mod-teal {background:#E0F5EE; color:#0A6650; border-top:3px solid #14956E;}
.mod-blue {background:#E5F0FB; color:#1558A0; border-top:3px solid #2374CC;}
.mod-amber {background:#FAF0D8; color:#7A4C0A; border-top:3px solid #D68D15;}
.mod-purple {background:#EEEDF9; color:#4A42AC; border-top:3px solid #6860D1;}
.mod-steps {font-size:13px; color:#6B6E79; margin:0 0 6px 0; padding-left:18px; line-height: 1.6;}

/* ---- status badges ---- */
.sb-badge {display:inline-block; font-size:11.5px; font-weight:600; padding:3px 11px; border-radius:20px; border:1px solid;
  letter-spacing: .01em; vertical-align: middle;}
.sb-green {background:#E8F5E2; color:#2E6E12; border-color:#CFE9C3;}
.sb-amber {background:#FAF0D8; color:#7A4C0A; border-color:#F0DBA8;}
.sb-gap {background:#FDECEC; color:#9B2828; border-color:#F6CFCF;}
.sb-muted {color:#8C8F99; font-size:12px;}
.sb-title {font-size:21px; font-weight:700; margin-right:10px; letter-spacing: -.015em; color: #1C1E26;}

/* ---- metric & content cards ---- */
div[data-testid="stMetric"] {background:#FFFFFF; border:1px solid #E6E7EC; border-radius:10px; padding:14px 16px 12px 16px;
  box-shadow: 0 1px 2px rgba(16,24,40,.04);}
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {border-radius: 10px;}
hr {margin: 1.1rem 0; border-color: #ECEDF1;}
</style>
""", unsafe_allow_html=True)

PAGES = {
    "dashboard": "Dashboard",
    "segmentation": "SKU segmentation",
    "service-levels": "Suggested service levels",
    "allocation": "Weekly MEIO allocation",
    "alpha": "Periodic alpha check",
    "simulation": "Simulation centre",
}
PAGE_ICONS = {"dashboard": ":material/dashboard:", "segmentation": ":material/pie_chart:",
              "service-levels": ":material/percent:", "allocation": ":material/calendar_view_week:",
              "alpha": ":material/autorenew:", "simulation": ":material/monitoring:"}
st.session_state.setdefault("page", "dashboard")


def goto(page: str):
    st.session_state["page"] = page


# --------------------------------------------------------------------------- repo readers
def scenario_folder(cfg: dict) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", cfg["scenario_name"]).strip("_") or "Unnamed"
    return f"SCENARIO_{cfg['scenario_number']:04d}_{slug}"


@st.cache_data(show_spinner=False)
def load_cfg() -> dict:
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def read_csv(path: str) -> pd.DataFrame | None:
    p = Path(path)
    return pd.read_csv(p) if p.exists() else None


@st.cache_data(show_spinner=False)
def read_parquet(path: str) -> pd.DataFrame | None:
    p = Path(path)
    return pd.read_parquet(p) if p.exists() else None


@st.cache_data(show_spinner=False)
def read_json(path: str) -> dict | None:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%d %b %Y, %H:%M") if path.exists() else "never"


def paths() -> dict:
    cfg = load_cfg()
    name = scenario_folder(cfg)
    return {
        "cfg": cfg, "name": name,
        "clusters": SEG / "sku_clusters.csv",
        "cluster_sl": SEG / "cluster_service_levels.csv",
        "meio_dir": OUT / "MEIO" / name,
        "weekly_dir": OUT / "MEIO" / name / "2026-W01",
        # NOT OUT / "projections" / ...: that was models/twin/digital_twin.py's (the
        # original engine's) output convention. scenario_config.json's digital_twin.entrypoint
        # points at the optimized port (digital_twin_fast.py), which writes straight to
        # OUT / scenario_folder_name / run_name instead -- confirmed against real output
        # (Agents/tools/paths.py's PROJECTIONS_DIR had the same stale assumption, fixed
        # 2026-10-19; this call site was missed then and is fixed now).
        "twin_dir": OUT / name / cfg["digital_twin"].get("run_name", "meio_policy"),
        "baseline_dir": OUT / "SCENARIO_0001_Baseline_Forward_Plan" / "baseline",
        "evidence": OUT / "MEIO artifacts" / name / "results",
    }


# --------------------------------------------------------------------------- background jobs
RUNS.mkdir(exist_ok=True)


@st.cache_resource
def job_registry() -> dict:
    return {}


@st.cache_resource
def agent_chat_registry() -> dict:
    """Separate from job_registry(): that dict's values are all launch()-built subprocess
    job records with a uniform {"key", "title", "steps", ...} shape that other code (e.g.
    page_simulation's `sims = [j for j in job_registry().values() if j["key"] == "sim"]`)
    iterates assuming every entry has it. The agent chat object has a different shape
    entirely, so it gets its own registry rather than violating that assumption."""
    return {}


def _run_chain(job: dict):
    for label, cmd in job["steps"]:
        job["current"] = label
        with open(job["log"], "a", encoding="utf-8") as log:
            log.write(f"\n$ {' '.join(map(str, cmd))}\n")
            log.flush()
            # Secrets (e.g. an API key typed into the UI) go here, in the child's env, never
            # appended to cmd -- the "$ ..." line above logs cmd verbatim, so anything in cmd
            # would leak into App/runs/*.log.
            env = {**os.environ, **(job.get("extra_env") or {}), "PYTHONIOENCODING": "utf-8",
                  "PYTHONUNBUFFERED": "1"}
            proc = subprocess.Popen([str(c) for c in cmd], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
            job["proc"] = proc
            code = proc.wait()
        if code != 0:
            job.update(state="failed", current=f"{label} (exit {code})", finished=time.time())
            return
    job.update(state="succeeded", current="done", finished=time.time())


def launch(key: str, title: str, steps: list[tuple[str, list]], extra_env: dict | None = None) -> None:
    reg = job_registry()
    if reg.get(key, {}).get("state") == "running":
        st.warning(f"'{title}' is already running.")
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job = {"key": key, "title": title, "steps": steps, "state": "running", "current": steps[0][0],
           "started": time.time(), "log": RUNS / f"{key}_{stamp}.log", "extra_env": extra_env or {}}
    reg[key] = job
    threading.Thread(target=_run_chain, args=(job,), daemon=True).start()
    st.toast(f"Started: {title}")


@st.fragment(run_every=3)
def jobs_panel(keys: tuple[str, ...] | None = None):
    reg = job_registry()
    jobs = [j for k, j in reg.items() if keys is None or k in keys]
    if not jobs:
        return
    for j in jobs:
        icon = {"running": ":material/progress_activity:", "succeeded": ":material/check_circle:",
                "failed": ":material/error:"}[j["state"]]
        elapsed = (j.get("finished") or time.time()) - j["started"]
        with st.expander(f"{icon} {j['title']} - {j['state']} - {j['current']} ({elapsed:,.0f}s)",
                         expanded=j["state"] == "running"):
            if j["state"] == "running":
                st.progress(min(0.95, 0.05 + 0.9 * (1 - 1 / (1 + elapsed / 240))),
                            text="Indicative progress only.")
                if st.button("Stop", key=f"stop_{j['key']}"):
                    proc = j.get("proc")
                    if proc:
                        proc.terminate()
            log_file = Path(j["log"])
            tail = (log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
                    if log_file.exists() else [])
            st.code("\n".join(tail) or "(no output yet)", language="text")


def _read_json_live(path: Path) -> dict | None:
    """Uncached read -- the bridge's current.json / approval_request.json / done.json are
    rewritten in place as a run progresses, so the cached read_json() would keep serving
    the first version it ever saw for that path."""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@st.fragment(run_every=3)
def agent_bridge_panel():
    """What a `--profile desktop` Agents.runtime session is blocked on right now: the
    next model call to relay, or the human-approval gate -- the two things the README's
    manual operator loop (`python -m Agents.runtime.bridge wait`, by hand, at a terminal)
    exists to surface. Approval is answered here directly; a model call still needs an
    actual Claude session (this one, if asked, or another) to read it and write the reply."""
    chat = agent_chat_registry().get("chat") or {}
    if not chat.get("bridge_session"):
        return
    session = Path(chat["bridge_session"])
    if not session.exists():
        return
    st.markdown("**Agent session status**")
    pending_req = None
    for req in sorted(session.glob("req_*.json")):
        if not (session / req.name.replace("req_", "res_")).exists():
            pending_req = req
            break
    approval_req_path = session / "approval_request.json"
    approval_res_path = session / "approval_response.json"
    done = _read_json_live(session / "done.json")

    if pending_req is not None:
        r = _read_json_live(pending_req)
        st.info(f"Waiting on a model call: **{r['agent']}** (model alias `{r['model']}`, call #{r['seq']})")
        with st.expander("Full request"):
            st.markdown(pending_req.with_suffix(".md").read_text(encoding="utf-8", errors="replace"))
        st.caption("The desktop model session answers this request through the bridge. A reply can also be "
                   f"written manually to `{r['response_file']}` in the form "
                   '`{"text": "...", "tool_calls": [...], "model_id": "..."}`.')
    elif approval_req_path.exists() and not approval_res_path.exists():
        a = _read_json_live(approval_req_path)
        st.warning(f"Waiting on your approval: **{a['scenario_folder']}**")
        with st.expander("Scenario card", expanded=True):
            st.markdown(a["card"])
        name = st.text_input("Your name (recorded in approval.json)", key="agent_approver_name")
        feedback = st.text_input("Feedback (only used if you reject)", key="agent_reject_feedback")
        c1, c2 = st.columns(2)
        if c1.button("Approve", type="primary", key="agent_approve_btn", disabled=not name.strip()):
            Path(a["response_file"]).write_text(
                json.dumps({"approved": True, "approved_by": name.strip()}), encoding="utf-8")
            st.rerun()
        if c2.button("Reject", key="agent_reject_btn"):
            Path(a["response_file"]).write_text(
                json.dumps({"approved": False, "feedback": feedback}), encoding="utf-8")
            st.rerun()
    elif done:
        st.success(f"Session finished: {done.get('summary') or '(no summary recorded)'}")
    else:
        st.caption("Idle. Nothing pending.")


# ------------------------------------------------------------------ agent chat (in-process) --
# One shared LangGraph Session kept alive across turns (agent_chat_registry()["chat"]), instead
# of spawning a brand-new `Agents.runtime` subprocess per message. A subprocess has no way to
# carry conversation state between invocations -- graph.Team compiles the orchestrator with a
# fresh in-memory checkpointer every time, so a one-shot launch() per prompt is structurally a
# dead end the moment the orchestrator asks a clarifying question back. graph.Session.ask() is
# already built to be called repeatedly on one object (see the CLI's own `planner>` REPL loop
# in runtime/__main__.py); this just drives that loop from Streamlit instead of a terminal.


def _agent_decide(chat: dict):
    """The approval interrupt, answered in the chat UI instead of a terminal prompt or the
    file-based bridge: blocks the background turn thread until agent_chat_panel()'s Approve/
    Reject button sets chat['approval_event']. Used for every profile -- unlike model calls,
    approval is for a person regardless of which model is answering the rest of the graph."""
    def decide(payload: dict) -> dict:
        chat["pending_approval"] = payload
        event = threading.Event()
        chat["approval_event"] = event
        event.wait()
        return chat.pop("approval_decision", {"pending": True})
    return decide


def _run_agent_turn(chat: dict, text: str):
    from Agents.runtime import graph
    try:
        if chat["session"] is None:
            if chat["profile"] == "claude" and chat.get("api_key"):
                # This is a single-local-user tool (see CLAUDE.md), not a multi-tenant server,
                # so setting the process env here is fine; the Anthropic client reads it per model.
                os.environ["ANTHROPIC_API_KEY"] = chat["api_key"]
            team = graph.Team(chat["profile"], streaming=False)
            chat["session"] = graph.Session(team, decide=_agent_decide(chat))
            if chat["profile"] == "desktop":
                from Agents.runtime import bridge
                chat["bridge_session"] = str(bridge.start_session())
        reply = chat["session"].ask(text)
        chat["turns"].append({"role": "assistant",
                              "text": reply or "_(no reply text back -- check the scenario list below; the "
                                               "agents may have acted without saying anything)_"})
    except Exception as e:
        chat["turns"].append({"role": "assistant", "text": f"Error: {e}"})
    finally:
        chat["state"] = "idle"
        chat["pending_approval"] = None


def start_agent_chat(profile: str, api_key: str = "") -> dict:
    chat = {"profile": profile, "api_key": api_key, "turns": [], "state": "idle",
           "session": None, "pending_approval": None}
    agent_chat_registry()["chat"] = chat
    return chat


def send_agent_message(chat: dict, text: str):
    chat["turns"].append({"role": "user", "text": text})
    chat["state"] = "thinking"
    chat["turn_started"] = time.time()
    threading.Thread(target=_run_agent_turn, args=(chat, text), daemon=True).start()


def _agent_progress_text(chat: dict) -> str | None:
    """Peek at the orchestrator's checkpointed state WHILE a turn is still in flight in its
    background thread -- LangGraph saves state after every superstep, so this is safe to read
    concurrently and gives real mid-turn progress (which tool is running, which agent just
    reported back) instead of a static "thinking..." for the whole multi-step orchestration
    (create_draft -> delegate x N -> validate -> review -> build -> approval), which can
    legitimately take minutes and otherwise looks identical to a hang the whole time."""
    session = chat.get("session")
    if session is None:
        return None
    try:
        snap = session.team.orchestrator.get_state(session.config)
        messages = (snap.values or {}).get("messages", [])
    except Exception:
        return None
    if not messages:
        return None
    last = messages[-1]
    kind = getattr(last, "type", None)
    if kind == "ai":
        calls = getattr(last, "tool_calls", None) or []
        if calls:
            return "Calling: " + ", ".join(c["name"] for c in calls)
        return "Composing a reply..."
    if kind == "tool":
        return f"Got a result back from: {getattr(last, 'name', 'a tool')}"
    return None


AGENT_LABELS = {"orchestrator": ("Orchestrator", ":material/account_tree:"),
                "demand_agent": ("Demand agent", ":material/trending_down:"),
                "supply_agent": ("Supply agent", ":material/local_shipping:"),
                "inventory_agent": ("Inventory agent", ":material/inventory_2:"),
                "service_policy_agent": ("Service & policy agent", ":material/tune:"),
                "economics_agent": ("Economics agent", ":material/payments:"),
                "validator_agent": ("Validator agent", ":material/fact_check:"),
                "analyst_agent": ("Analyst agent", ":material/analytics:")}


@st.cache_data(show_spinner=False, max_entries=1024)
def _bridge_call(req_path: str, req_mtime: float, res_mtime: float | None) -> dict | None:
    """One model call as the feed shows it. The mtimes are in the signature so a newly written
    reply (or rewritten request) invalidates the cached entry."""
    try:
        req = json.loads(Path(req_path).read_text(encoding="utf-8"))
        res_p = Path(req["response_file"])
        res = json.loads(res_p.read_text(encoding="utf-8")) if res_mtime is not None and res_p.exists() else None
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    last = req["messages"][-1] if req.get("messages") else {}
    if last.get("role") == "tool":
        incoming = f"Result of `{last.get('name', 'tool')}`"
    elif last.get("role") == "user":
        incoming = "Brief from the planner" if req["agent"] == "orchestrator" else "Brief from the orchestrator"
    else:
        incoming = "Continuing"
    return {"seq": req["seq"], "agent": req["agent"], "model": req["model"],
            "incoming": incoming, "incoming_body": str(last.get("content") or last.get("text") or ""),
            "answered": res is not None, "model_id": (res or {}).get("model_id"),
            "text": (res or {}).get("text") or "", "tool_calls": (res or {}).get("tool_calls") or [],
            "seconds": (res_mtime - req_mtime) if res is not None else None}


def _call_summary(c: dict) -> str:
    calls = []
    for t in c["tool_calls"]:
        args = t.get("args") or {}
        target = args.get("agent") or args.get("scenario_folder") or args.get("draft_id") or args.get("measure")
        calls.append(f"`{t['name']}`" + (f" -> {target}" if target else ""))
    return ", ".join(calls) if calls else "final reply (no tool calls)"


def agent_activity_feed(chat: dict):
    """Every model call of the current desktop-bridge session, live: which agent, which model, how
    long it took, what came in, what it decided and which tools it called -- the same trail an
    operator sees relaying the bridge, so a planner can watch the agents actually work."""
    # This chat's own bridge session only -- current.json still points at the previous chat's
    # session until this chat sends its first message, which would replay old history here.
    if not chat.get("bridge_session"):
        return
    session = Path(chat["bridge_session"])
    reqs = sorted(session.glob("req_*.json")) if session.exists() else []
    if not reqs:
        return
    feed = []
    for req in reqs:
        res = session / req.name.replace("req_", "res_")
        c = _bridge_call(str(req), req.stat().st_mtime, res.stat().st_mtime if res.exists() else None)
        if c:
            feed.append(c)
    done = sum(c["answered"] for c in feed)
    total_s = sum(c["seconds"] or 0 for c in feed)
    st.markdown(f"**:material/monitoring: Agent activity** · {done}/{len(feed)} model calls answered, "
                f"{total_s / 60:,.1f} min of model time")
    with st.container(height=460, border=True):
        for c in feed:
            label, icon = AGENT_LABELS.get(c["agent"], (c["agent"], ":material/smart_toy:"))
            with st.chat_message(label, avatar=icon):
                if c["answered"]:
                    st.markdown(f"**#{c['seq']} {label}** · {c['model_id'] or c['model']} · "
                                f"{c['seconds']:,.0f}s  \n:material/arrow_forward: {c['incoming']}  \n"
                                f":material/check_circle: {_call_summary(c)}")
                    with st.expander("What it said and did"):
                        if c["text"]:
                            st.markdown(c["text"])
                        for t in c["tool_calls"]:
                            st.code(f"{t['name']}({json.dumps(t.get('args') or {}, indent=1)[:1500]})", language="json")
                        if c["incoming_body"]:
                            st.caption("Input it was answering:")
                            st.code(c["incoming_body"][:1500], language="json")
                else:
                    st.markdown(f"**#{c['seq']} {label}** · {c['model']}  \n:material/arrow_forward: {c['incoming']}  \n"
                                ":material/hourglass_top: *waiting for the model reply...*")


@st.fragment(run_every=2)
def agent_chat_panel():
    chat = agent_chat_registry().get("chat")
    if not chat:
        st.caption('No active session. Select "New session" to start one.')
        return
    for turn in chat["turns"]:
        with st.chat_message(turn["role"]):
            st.markdown(turn["text"])
    if chat.get("profile") == "desktop":
        agent_activity_feed(chat)
    if chat["state"] == "thinking" and not chat.get("pending_approval"):
        with st.chat_message("assistant"):
            elapsed = time.time() - chat.get("turn_started", time.time())
            progress = _agent_progress_text(chat) or "thinking..."
            st.caption(f":material/progress_activity: {progress} ({elapsed:,.0f}s)")
            if chat["profile"] == "claude" and elapsed > 300:
                st.caption("Still running. Each model call times out after 300 s (up to 2 retries). "
                           "If it does not finish, start a new session.")
            elif chat["profile"] == "desktop" and elapsed > 150:
                st.caption("Still waiting for the desktop model session to answer the bridge request "
                           "below. Desktop calls have no timeout.")
    if chat.get("pending_approval"):
        a = chat["pending_approval"]
        st.warning(f"Waiting on your approval: **{a['scenario_folder']}**")
        with st.expander("Scenario card", expanded=True):
            st.markdown(a["card"])
        name = st.text_input("Your name (recorded in approval.json)", key="chat_approver_name")
        feedback = st.text_input("Feedback (only used if you reject)", key="chat_reject_feedback")
        c1, c2 = st.columns(2)
        if c1.button("Approve", type="primary", key="chat_approve_btn", disabled=not name.strip()):
            chat["approval_decision"] = {"approved": True, "approved_by": name.strip()}
            chat["pending_approval"] = None
            chat["approval_event"].set()
            st.rerun()
        if c2.button("Reject", key="chat_reject_btn"):
            chat["approval_decision"] = {"approved": False, "feedback": feedback}
            chat["pending_approval"] = None
            chat["approval_event"].set()
            st.rerun()
    msg = st.chat_input(f"Message the {chat['profile']} agents...", disabled=chat["state"] != "idle")
    if msg:
        send_agent_message(chat, msg)
        st.rerun()


# --------------------------------------------------------------------------- shared data
def kpis(path: Path) -> dict | None:
    # Uncached on purpose: a scenario's arms write run_kpis.json minutes after the page first
    # renders, and the cached read_json() would keep serving the "no file yet" None it saw then.
    return _read_json_live(path / "run_kpis.json")


def alpha_by_cluster_table(par: dict) -> pd.DataFrame:
    """Alpha actually used per cluster: the per-cluster decision in meio_parameters.json,
    falling back to the portfolio default for clusters without their own value."""
    by_cluster = next((d["value"] for d in par.get("decisions", []) if d.get("parameter") == "alpha_by_cluster"
                       and isinstance(d.get("value"), dict)), {})
    sku = read_csv(str(paths()["clusters"]))
    counts = sku["archetype"].value_counts() if sku is not None else pd.Series(dtype=int)
    names = list(counts.index) or list(by_cluster)
    return pd.DataFrame([{"Cluster": c, "SKUs": int(counts.get(c, 0)),
                          "Alpha used": by_cluster.get(c, par.get("alpha")),
                          "Source": "own cluster test" if c in by_cluster and by_cluster[c] != par.get("alpha")
                          else "portfolio default (own test didn't beat it)"} for c in names])


def alpha_flags() -> pd.DataFrame:
    """Proxy for per-SKU alpha flags: clusters whose own-fill-best alpha differs
    from the alpha the pipeline adopted. The repo has cluster-level evidence only.

    Reads the v5 evidence (segment_alpha_v5_real_data_summary.csv: v5's 6 clusters,
    built at today's live reference alpha=0.6) -- NOT segment_alpha_real_data_summary.csv,
    which is v4's 4 clusters built at the stale reference alpha=0.2. That file doesn't
    even cover the adopted alpha (0.6) at all, so comparing "own-fill at adopted" against
    it was silently comparing against the wrong configuration everywhere this function
    is used (this table, and the sidebar/topbar flag badge on every page)."""
    p = paths()
    ev = read_csv(str(p["evidence"] / "segment_alpha_v5_real_data_summary.csv"))
    par = read_json(str(p["weekly_dir"] / "meio_parameters.json")) or {}
    adopted = par.get("alpha")
    if ev is None or adopted is None:
        return pd.DataFrame()
    best = ev.loc[ev.groupby("cluster")["own_fill_mean"].idxmax(), ["cluster", "alpha", "own_fill_mean"]]
    best = best.rename(columns={"alpha": "best_alpha_own_fill", "own_fill_mean": "own_fill_at_best"})
    # Compare each cluster against the alpha the pipeline actually adopted FOR THAT CLUSTER
    # (meio_parameters.json alpha_by_cluster), not the portfolio alpha -- otherwise clusters
    # already moved to their best alpha get flagged as "review due". A cluster is flagged only
    # when its best alpha differs AND the fill gain clears single-seed noise (0.3pp).
    by_cluster = next((d["value"] for d in par.get("decisions", []) if d.get("parameter") == "alpha_by_cluster"
                       and isinstance(d.get("value"), dict)), {})
    out = best.copy()
    out["adopted_alpha"] = out["cluster"].map(lambda c: by_cluster.get(c, adopted))
    own = ev.set_index(["cluster", "alpha"])["own_fill_mean"]
    out["own_fill_at_adopted"] = [own.get((c, a), np.nan) for c, a in zip(out["cluster"], out["adopted_alpha"])]
    out["gain_pp"] = (out["own_fill_at_best"] - out["own_fill_at_adopted"]) * 100
    out["flagged"] = ~np.isclose(out["best_alpha_own_fill"], out["adopted_alpha"]) & (out["gain_pp"] > 0.3)
    return out


# --------------------------------------------------------------------------- top bar
def outputs_stale() -> str | None:
    """Why the MEIO plan / Twin run is older than what it was built from, or None. Inputs ->
    segmentation + tuning evidence -> rolling MEIO policy -> Twin run."""
    p = paths()
    def t(path: Path) -> float:
        return path.stat().st_mtime if path.exists() else 0.0
    policy = t(p["meio_dir"] / "rolling_2026" / "meio_policy_rolling.parquet")
    twin = t(p["twin_dir"] / "run_kpis.json")
    ev = max((t(f) for f in p["evidence"].glob("*multiseed*.csv")), default=0.0)
    upstream = max(t(ROOT / "input datasets" / "orders.parquet"), t(p["clusters"]), ev)
    if policy and upstream > policy:
        return "inputs, segmentation or tuning evidence changed after the last MEIO run"
    if twin and policy > twin:
        return "the MEIO policy changed after the last Twin run"
    return None


def topbar():
    page = st.session_state["page"]
    flags = alpha_flags()
    n_flag = int(flags["flagged"].sum()) if len(flags) else 0
    stale = outputs_stale()
    badge = (("sb-amber", f"{n_flag} flags") if page == "alpha" and n_flag else
             ("sb-amber", "Re-run needed") if stale else ("sb-green", "Up to date"))
    c1, c2, c3, c4 = st.columns([4, 1.3, 1.3, 1.5], vertical_alignment="center")
    c1.markdown(f"<span class='sb-title'>{PAGES[page]}</span><span class='sb-badge {badge[0]}'>{badge[1]}</span>",
                unsafe_allow_html=True)
    export_col = c2
    with c3:
        if st.button("Refresh", icon=":material/refresh:", width="stretch",
                     help="Re-read every output file from disk."):
            st.cache_data.clear()
            st.rerun()
    with c4:
        run_all_clicked = st.button("Run all", icon=":material/play_arrow:", type="primary", width="stretch")
    return run_all_clicked, export_col


def confirm_box(label: str, key: str) -> bool:
    return st.checkbox(label, key=key)


# --------------------------------------------------------------------------- actions
def seg_steps(canonical: bool) -> list[tuple[str, list]]:
    out = SEG if canonical else RUNS / f"segmentation_{datetime.now():%Y%m%d_%H%M%S}"
    stage = RUNS / "csv_input"
    return [("stage_csv.py (parquet -> CSV for segmentation_v5)", [PY, "App/stage_csv.py", stage]),
            ("segmentation_v5.py", [PY, "Clustering & Segmentation/segmentation_v5/segmentation_v5.py",
                                    "--input", stage, "--outdir", out])]


def meio_steps(with_twin: bool) -> list[tuple[str, list]]:
    if with_twin:
        return [("run_meio_then_twin.py (MEIO policy, then Digital Twin)",
                 [PY, "run_meio_then_twin.py", "--scenario-config", CFG_PATH, "--yes"])]
    return [("run_weekly_tools.py (weekly + rolling MEIO policy)",
             [PY, "models/MEIO/MEIO_Weekly/final_submission_optimized/run_weekly_tools.py",
              "--scenario-config", CFG_PATH])]


def alpha_steps() -> list[tuple[str, list]]:
    return [("alpha_check.py (meio_parameters.resolve)", [PY, "App/alpha_check.py", RUNS / "alpha_receipt.json"])]


def sim_steps(policy: str, horizon: int, seed: int) -> tuple[str, list]:
    """A sandbox Digital Twin run: copy of the root config pointing at App/runs, so nothing canonical is touched."""
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = RUNS / f"twin_{stamp}"
    cfg["scenario_number"] = 9000 + int(stamp[-2:])
    cfg["scenario_name"] = f"UI_Sandbox_{stamp}"
    cfg["digital_twin"]["output_root"] = str(root)
    cfg["overrides"]["output_root"] = str(root / "overrides")
    root.mkdir(parents=True, exist_ok=True)
    cfg_file = root / "scenario_config.json"
    cfg_file.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    # The MEIO policy option resolves to meio_policy_rolling.parquet (the optimized MEIO
    # tools write parquet, not CSV) -- models/twin/digital_twin.py only reads CSV
    # (pd.read_csv(pol_path)), so it would crash on that file. digital_twin_fast.py reads
    # either format and is what scenario_config.json's digital_twin.entrypoint uses too.
    return str(root), [("digital_twin_fast.py",
                        [PY, "models/MEIO/MEIO_Weekly/final_submission_optimized/digital_twin_fast.py",
                         "--scenario-config", cfg_file, "--mode", "forward", "--policy", policy, "--seed", seed,
                         "--horizon-weeks", horizon, "--run-name", "ui_sandbox",
                         "--skip-output-documentation"])]


# --------------------------------------------------------------------------- pages
def start_sim(policy_opt: str, horizon: int, seed: int) -> None:
    p = paths()
    policy = (next(iter(p["meio_dir"].rglob("meio_policy_rolling.parquet")), None)
              if policy_opt.startswith("MEIO") else ROOT / p["cfg"]["digital_twin"]["policy"])
    if policy is None or not Path(policy).exists():
        st.error("Policy file not found. Run Weekly MEIO allocation first.")
        return
    root, steps = sim_steps(str(policy), int(horizon), int(seed))
    st.session_state["sim_root"] = root
    launch("sim", "Simulation (sandbox)", steps)


def module_head(css: str, title: str):
    st.markdown(f"<div class='mod-head mod-{css}'>{title}</div>", unsafe_allow_html=True)


def steps_html(items: list[str]):
    st.markdown("<ol class='mod-steps'>" + "".join(f"<li>{i}</li>" for i in items) + "</ol>", unsafe_allow_html=True)


def latest_scenario_card():
    """The newest what-if scenario with finished arms: one row per arm and the two answers a
    planner wants - which response serves customers best, and which is cheapest."""
    arms = ("unprepared", "prepared", "reactive")
    found = sorted(d for d in OUT.glob("SCENARIO_*") if any((d / a / "run_kpis.json").exists() for a in arms))
    if not found:
        return
    d = found[-1]
    ks = {a: k for a in arms if (k := kpis(d / a))}
    title = re.sub(r"^SCENARIO_\d+_", "", d.name).replace("_", " ")
    st.caption(f"LATEST WHAT-IF SCENARIO - {title}")
    names = {"unprepared": "Do nothing (today's policy)", "prepared": "Plan ahead (re-plan in advance)",
             "reactive": "React weekly (re-plan every week)"}
    tab = pd.DataFrame({names[a]: {"Customer fill": f"{k['customer_fill_rate']:.2%}",
                                   "Lost-sales cost": f"${k['stockout_cost_usd'] / 1e6:.2f}M",
                                   "Avg inventory": f"${k['avg_inventory_value_usd'] / 1e6:.2f}M",
                                   "Total landed cost": f"${k['total_landed_cost_usd'] / 1e6:.2f}M"}
                        for a, k in ks.items()}).T
    left, right = st.columns([3, 2], vertical_alignment="center")
    left.dataframe(tab, width="stretch")
    with right:
        best = max(ks, key=lambda a: ks[a]["customer_fill_rate"])
        cheap = min(ks, key=lambda a: ks[a]["total_landed_cost_usd"])
        base = ks.get("unprepared")
        st.markdown(f"**Best service:** {names[best].split(' (')[0]} - "
                    f"{ks[best]['customer_fill_rate']:.2%} customer fill"
                    + (f" ({(ks[best]['customer_fill_rate'] - base['customer_fill_rate']) * 100:+.1f} pp vs doing nothing)"
                       if base and best != "unprepared" else ""))
        st.markdown(f"**Lowest cost:** {names[cheap].split(' (')[0]} - "
                    f"${ks[cheap]['total_landed_cost_usd'] / 1e6:.2f}M"
                    + (f" (${(base['total_landed_cost_usd'] - ks[cheap]['total_landed_cost_usd']) / 1e3:,.0f}K "
                       f"saved vs doing nothing)" if base and cheap != "unprepared" else ""))
        st.button("Open in Simulation centre", icon=":material/open_in_new:", key="d_scen", width="stretch",
                  on_click=goto, args=("simulation",))


def page_dashboard(slot: dict):
    p = paths()
    sku = read_csv(str(p["clusters"]))
    k_meio, k_base = kpis(p["twin_dir"]), kpis(p["baseline_dir"])
    flags = alpha_flags()
    n_flag = int(flags["flagged"].sum()) if len(flags) else 0

    if n_flag and not st.session_state.get("alert_dismissed"):
        a, b = st.columns([30, 1], vertical_alignment="center")
        a.warning(f"Alpha review due - {n_flag} cluster(s) would fill noticeably better (>0.3 pp) at a different "
                  f"alpha than the one adopted for them.", icon=":material/warning:")
        b.button("", icon=":material/close:", key="dismiss", help="Dismiss",
                 on_click=lambda: st.session_state.update(alert_dismissed=True))

    st.caption("WHAT THE MEIO PLAN DELIVERS - full year 2026, vs today's planning policy")
    if k_meio and k_base:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Customer fill rate", f"{k_meio['customer_fill_rate']:.1%}",
                  f"{(k_meio['customer_fill_rate'] - k_base['customer_fill_rate']) * 100:+.1f} pp", border=True)
        m2.metric("Total landed cost", f"${k_meio['total_landed_cost_usd'] / 1e6:.2f}M",
                  f"{(k_meio['total_landed_cost_usd'] - k_base['total_landed_cost_usd']) / 1e6:+.2f}M",
                  delta_color="inverse", border=True)
        m3.metric("Lost-sales cost", f"${k_meio['stockout_cost_usd'] / 1e6:.2f}M",
                  f"{(k_meio['stockout_cost_usd'] - k_base['stockout_cost_usd']) / 1e6:+.2f}M",
                  delta_color="inverse", border=True)
        m4.metric("Avg inventory value", f"${k_meio['avg_inventory_value_usd'] / 1e6:.2f}M",
                  f"{(k_meio['avg_inventory_value_usd'] - k_base['avg_inventory_value_usd']) / 1e6:+.2f}M",
                  delta_color="off", border=True)
        inv = (k_meio["avg_inventory_value_usd"] - k_base["avg_inventory_value_usd"]) / 1e6
        lost = (k_base["stockout_cost_usd"] - k_meio["stockout_cost_usd"]) / 1e6
        net = (k_base["total_landed_cost_usd"] - k_meio["total_landed_cost_usd"]) / 1e6
        st.success(f"The MEIO plan holds **${inv:.1f}M more stock** in the right places and in return cuts lost "
                   f"sales by **${lost:.1f}M** - the network is **${net:.1f}M a year cheaper to run** while filling "
                   f"{k_meio['customer_fill_rate']:.0%} of customer demand from stock "
                   f"(up from {k_base['customer_fill_rate']:.0%}).", icon=":material/trending_up:")
    else:
        st.info("No finished Twin runs yet - use Run all to produce the forward plan.")

    latest_scenario_card()
    par0 = read_json(str(p["weekly_dir"] / "meio_parameters.json")) or {}
    alphas = sorted(set(alpha_by_cluster_table(par0)["Alpha used"].dropna())) if par0 else []
    alpha_txt = (f" · alpha {alphas[0]:.1f}-{alphas[-1]:.1f} by cluster" if len(alphas) > 1
                 else f" · alpha {alphas[0]:g}" if alphas else "")
    st.caption(f"Network: {len(sku) if sku is not None else 500:,} SKUs in "
               f"{sku['cluster'].nunique() if sku is not None else 6} clusters · 4 plant DCs · 8 regional DCs · "
               f"15 customer DCs{alpha_txt} · last Twin run {mtime(p['twin_dir'] / 'run_kpis.json')}")

    st.caption("MODULES")
    cs = read_csv(str(p["cluster_sl"]))
    summ = read_csv(str(p["weekly_dir"] / "meio_summary_by_dc_weekly.csv"))
    par = read_json(str(p["weekly_dir"] / "meio_parameters.json")) or {}
    tabs = st.tabs([":material/pie_chart: SKU segmentation", ":material/calendar_view_week: Weekly MEIO allocation",
                    ":material/autorenew: Periodic alpha check", ":material/monitoring: Simulation centre"])

    with tabs[0]:
        if cs is not None:
            st.caption("Latest output: SKUs per archetype")
            st.bar_chart(cs.set_index("archetype")["n_skus"], height=170, color="#14956E")
        a, b = st.columns(2)
        if a.button("Run segmentation (sandbox)", icon=":material/play_arrow:", key="d_seg", width="stretch"):
            launch("seg", "SKU segmentation", seg_steps(False))
        b.button("Open details & charts", icon=":material/open_in_new:", key="d_sl", width="stretch",
                 on_click=goto, args=("segmentation",))
        st.caption(f"Last run: {mtime(p['clusters'])}")
        jobs_panel(("seg",))

    with tabs[1]:
        if summ is not None:
            st.caption("Latest output: safety stock value by DC (USD)")
            st.bar_chart(summ.set_index("dc_id")["total_safety_stock_usd"], height=170, color="#2374CC")
        ok = st.checkbox(f"Allow overwrite of output datasets/MEIO/{p['name']}", key="d_alloc_ok")
        a, b = st.columns(2)
        if a.button("Run allocation", icon=":material/play_arrow:", key="d_alloc", width="stretch", disabled=not ok):
            launch("alloc", "Weekly MEIO allocation", meio_steps(False))
        b.button("View weekly policy", icon=":material/open_in_new:", key="d_allocview", width="stretch",
                 on_click=goto, args=("allocation",))
        st.caption(f"Last run: {mtime(p['weekly_dir'] / 'meio_policy_weekly.parquet')}")
        jobs_panel(("alloc",))

    with tabs[3]:
        rows = {n: kpis(d) for n, d in (("Baseline plan", p["baseline_dir"]), ("MEIO plan", p["twin_dir"]))}
        if all(rows.values()):
            st.caption("Latest output: baseline vs MEIO plan")
            st.dataframe(pd.DataFrame({
                n: {"Fill rate": f"{k['customer_fill_rate']:.1%}",
                    "Avg inventory value": f"${k['avg_inventory_value_usd'] / 1e6:.2f}M",
                    "Total landed cost": f"${k['total_landed_cost_usd'] / 1e6:.2f}M"} for n, k in rows.items()}),
                width="stretch")
        a, b = st.columns(2)
        if a.button("Run default simulation", icon=":material/play_arrow:", key="d_sim", width="stretch"):
            start_sim("MEIO rolling policy (Clark-Scarf v2)", 52, int(p["cfg"]["digital_twin"].get("seed", 2026)))
        b.button("Configure & compare", icon=":material/settings:", key="d_simres", width="stretch",
                 on_click=goto, args=("simulation",))
        st.caption(f"Last run: {mtime(p['twin_dir'] / 'run_kpis.json')}. Default = MEIO policy, 52 weeks, sandbox output.")
        jobs_panel(("sim",))

    with tabs[2]:
        if par:
            st.caption(f"Alpha used per cluster (portfolio default {par.get('alpha')}, netting discount "
                       f"{par.get('netting_discount')})")
            st.dataframe(alpha_by_cluster_table(par), hide_index=True, width="stretch")
        if len(flags):
            st.caption("Latest output: best alpha per cluster")
            st.dataframe(flags[["cluster", "best_alpha_own_fill", "adopted_alpha", "gain_pp"]].rename(columns={
                "cluster": "Cluster", "best_alpha_own_fill": "Best alpha", "adopted_alpha": "Adopted",
                "gain_pp": "Fill gain (pp)"}), hide_index=True, width="stretch")
        a, b = st.columns(2)
        if a.button("Run alpha check", icon=":material/play_arrow:", key="d_alpha", width="stretch"):
            launch("alpha", "Periodic alpha check", alpha_steps())
        b.button(f"Review flags ({n_flag})", icon=":material/open_in_new:", key="d_flags", width="stretch",
                 on_click=goto, args=("alpha",))
        st.caption(f"{n_flag} cluster(s) flagged")
        jobs_panel(("alpha",))

    slot["df"] = pd.DataFrame([{"metric": "total_skus", "value": len(sku) if sku is not None else None},
                               *({"metric": k, "value": v} for k, v in (k_meio or {}).items())])


def run_all_panel(clicked: bool):
    if clicked:
        st.session_state["show_run_all"] = True
    if st.session_state.get("show_run_all"):
        with st.container(border=True):
            st.markdown("**Run all** runs the weekly cycle: MEIO policy, then the Digital Twin. "
                        "Segmentation and the alpha check run separately from their own pages, on their "
                        "periodic review cycle.")
            st.warning("This runs `run_meio_then_twin.py --yes`, which clears and rebuilds "
                       f"`output datasets/MEIO/{paths()['name']}` and its Twin run. This takes a long time.")
            ok = confirm_box("I understand; rebuild the configured scenario outputs", "ra_ok")
            if st.button("Start run all", type="primary", disabled=not ok):
                launch("runall", "Run all", meio_steps(True))
    jobs_panel(("runall",))


FEATURES = {"log_annual_net_margin": "Log net margin", "net_margin_pct": "Net margin %",
            "demand_cv_base": "Demand CV", "promo_depth_share": "Promo share",
            "forecast_baseline_wmape": "Baseline WMAPE", "forecast_lift_wmape": "Promo-lift WMAPE",
            "lead_time_mean_days": "Lead time (days)", "lead_time_cv": "Lead-time CV"}


def seg_charts(sku: pd.DataFrame, cs: pd.DataFrame, art: Path, imgs: list):
    import altair as alt
    if sku is None:
        st.info("No SKU data.")
        return
    f1, f2, f3 = st.columns(3)
    arch = f1.multiselect("Archetype", sorted(sku["archetype"].unique()), key="ch_arch")
    cat = f2.multiselect("Category", sorted(sku["category"].unique()), key="ch_cat")
    ids = f3.multiselect("SKU", sorted(sku["sku_id"].unique()), key="ch_sku")
    v = sku
    if arch:
        v = v[v["archetype"].isin(arch)]
    if cat:
        v = v[v["category"].isin(cat)]
    if ids:
        v = v[v["sku_id"].isin(ids)]
    st.caption(f"Filters apply to all four charts below. {len(v):,} of {len(sku):,} SKUs selected.")
    if v.empty:
        st.warning("No SKUs match the filters.")
        return
    order = list(cs.sort_values("recommended_csl", ascending=False)["archetype"])
    color = alt.Color("archetype:N", sort=order, title="Archetype")

    st.subheader("1. Cluster fingerprints")
    z = (sku[list(FEATURES)] - sku[list(FEATURES)].mean()) / sku[list(FEATURES)].std()
    z["archetype"] = sku["archetype"]
    fp = z.loc[v.index].groupby("archetype").mean().rename(columns=FEATURES).reset_index().melt(
        "archetype", var_name="feature", value_name="z")
    st.altair_chart(alt.Chart(fp).mark_rect().encode(
        x=alt.X("feature:N", title=None, axis=alt.Axis(labelAngle=-30)), y=alt.Y("archetype:N", sort=order, title=None),
        color=alt.Color("z:Q", scale=alt.Scale(scheme="redblue", domain=[-2, 2], reverse=True), title="Std. dev."),
        tooltip=["archetype", "feature", alt.Tooltip("z:Q", format=".2f")]).properties(height=260),
        width="stretch")
    st.caption("Each cell is how far an archetype's average sits from the all-SKU average on that feature, in standard "
               "deviations (red = above average, blue = below). It shows what makes each archetype distinct, for "
               "example high forecast error for the long-tail groups.")

    st.subheader("2. Cluster separation (PCA map)")
    from sklearn.decomposition import PCA
    zz = z[list(FEATURES)].fillna(0)
    pca = PCA(n_components=2, random_state=0)
    xy = pd.DataFrame(pca.fit_transform(zz.values), index=sku.index, columns=["PC1", "PC2"])
    xy = xy.loc[v.index].assign(archetype=v["archetype"], sku_id=v["sku_id"], category=v["category"])
    st.altair_chart(alt.Chart(xy).mark_circle(size=45, opacity=.7).encode(
        x="PC1:Q", y="PC2:Q", color=color, tooltip=["sku_id", "category", "archetype"]).properties(height=360).interactive(),
        width="stretch")
    st.caption(f"Each dot is a SKU, projected from the 8 clustering features onto two axes "
               f"({pca.explained_variance_ratio_.sum():.0%} of variance). Dots of one colour sitting together mean a tight "
               "group; overlap marks boundary SKUs for business review. Recomputed with standardised "
               "features, so it approximates the saved static PCA chart.")

    st.subheader("3. Recommended vs economically implied service level")
    sl = v.groupby("archetype").agg(skus=("sku_id", "count"), newsvendor_cr=("newsvendor_cr", "median")).reset_index()
    sl = sl.merge(cs[["archetype", "recommended_csl"]], on="archetype")
    sl = sl.melt(["archetype", "skus"], value_vars=["newsvendor_cr", "recommended_csl"], var_name="measure", value_name="level")
    sl["measure"] = sl["measure"].map({"newsvendor_cr": "Economic (newsvendor) CR", "recommended_csl": "Recommended CSL"})
    st.altair_chart(alt.Chart(sl).mark_bar(clip=True).encode(
        x=alt.X("archetype:N", sort=order, title=None, axis=alt.Axis(labelAngle=-25, labelLimit=240, labelPadding=8)),
        y=alt.Y("level:Q", scale=alt.Scale(domain=[0.85, 1.0]), axis=alt.Axis(format="%", labelLimit=100), title="Service level"),
        xOffset="measure:N", color=alt.Color("measure:N", title=None),
        tooltip=["archetype", "measure", "skus", alt.Tooltip("level:Q", format=".1%")]).properties(
            height=320, padding={"left": 10, "right": 10, "top": 10, "bottom": 30}),
        width="stretch")
    st.caption("For each archetype: the service level the cost trade-off implies (median newsvendor critical ratio of the "
               "selected SKUs) next to the level the ladder recommends. Core groups are lifted to 97% because of their "
               "revenue share; low-value groups are capped at 90%. Axis starts at 85%.")

    st.subheader("4. Forecast error: baseline vs promo-lift")
    lim = float(max(v["forecast_baseline_wmape"].max(), v["forecast_lift_wmape"].max())) * 1.05
    pts = alt.Chart(v).mark_circle(size=45, opacity=.65).encode(
        x=alt.X("forecast_baseline_wmape:Q", title="Baseline WMAPE", scale=alt.Scale(domain=[0, lim])),
        y=alt.Y("forecast_lift_wmape:Q", title="Promo-lift WMAPE", scale=alt.Scale(domain=[0, lim])),
        color=color, tooltip=["sku_id", "category", "archetype", alt.Tooltip("forecast_baseline_wmape:Q", format=".2f"),
                              alt.Tooltip("forecast_lift_wmape:Q", format=".2f")])
    diag = alt.Chart(pd.DataFrame({"x": [0, lim], "y": [0, lim]})).mark_line(strokeDash=[4, 4], color="grey").encode(x="x", y="y")
    st.altair_chart((pts + diag).properties(height=360).interactive(), width="stretch")
    st.caption("WMAPE is the weighted forecast error (lower is better). Dots above the dashed line forecast worse once "
               "promotions are modelled than with the plain baseline; dots below benefit from the promo-lift model. "
               "Far-right dots are the hard-to-forecast long-tail SKUs.")
    with st.expander("Original static charts saved by the segmentation run"):
        c = st.columns(2)
        for i, (f, cap) in enumerate(imgs):
            if (art / f).exists():
                c[i % 2].image(str(art / f), caption=cap)


def page_segmentation(slot: dict):
    p = paths()
    sku, cs = read_csv(str(p["clusters"])), read_csv(str(p["cluster_sl"]))
    c1, c2 = st.columns([2, 3], vertical_alignment="center")
    with c1:
        canonical = st.toggle("Overwrite canonical output", value=False,
                              help="Off: write to App/runs/. On: rewrite output datasets/segmentation/ (used by MEIO).")
    with c2:
        if st.button("Run segmentation", icon=":material/play_arrow:", type="primary"):
            launch("seg", "SKU segmentation", seg_steps(canonical))
    jobs_panel(("seg",))
    sandboxes = sorted(RUNS.glob("segmentation_*/cluster_service_levels.csv"))
    if sandboxes and cs is not None:
        new = pd.read_csv(sandboxes[-1])
        same = new[["cluster", "n_skus", "recommended_csl"]].round(4).equals(
            cs[["cluster", "n_skus", "recommended_csl"]].round(4))
        with st.expander(f"Latest sandbox run: {sandboxes[-1].parent.name} - "
                         f"{'identical to canonical' if same else 'DIFFERS from canonical'}"):
            st.dataframe(new, hide_index=True, width="stretch")
    if cs is None:
        st.error("No segmentation output found.")
        return
    tot = cs["n_skus"].sum()
    cols = ["archetype", "n_skus", "revenue_share", "net_margin_share", "forecast_baseline_wmape",
            "recommended_csl", "target_case_fill_rate", "safety_factor_z"]
    st.subheader("Segment portfolio")
    st.dataframe(cs[cols].sort_values("recommended_csl", ascending=False), hide_index=True, width="stretch",
                 column_config={"archetype": st.column_config.TextColumn("Archetype", width="medium"),
                                "n_skus": st.column_config.NumberColumn("SKUs", width="small"),
                                "revenue_share": st.column_config.NumberColumn("Revenue %", format="percent", width="small"),
                                "net_margin_share": st.column_config.NumberColumn("Margin %", format="percent", width="small"),
                                "forecast_baseline_wmape": st.column_config.NumberColumn("WMAPE", format="%.2f", width="small"),
                                "recommended_csl": st.column_config.NumberColumn("CSL", format="percent", width="small"),
                                "target_case_fill_rate": st.column_config.NumberColumn("Case fill", format="percent", width="small"),
                                "safety_factor_z": st.column_config.NumberColumn("z", format="%.3f", width="small")})
    st.caption(f"{tot} SKUs in {len(cs)} clusters.")
    art = SEG / "report_artifacts"
    imgs = [("chart_fingerprints.png", "Cluster fingerprints"), ("chart_pca.png", "PCA map"),
            ("chart_service_ladder.png", "Service ladder"), ("chart_failure_modes.png", "Failure modes")]
    t1, t2, t3 = st.tabs(["Charts", "SKU table", "Report"])
    with t1:
        seg_charts(sku, cs, art, imgs)
    with t2:
        if sku is not None:
            f1, f2, f3 = st.columns(3)
            arch = f1.multiselect("Archetype", sorted(sku["archetype"].unique()))
            cat = f2.multiselect("Category", sorted(sku["category"].unique()))
            ids = f3.multiselect("SKU", sorted(sku["sku_id"].unique()))
            view = sku
            if arch:
                view = view[view["archetype"].isin(arch)]
            if cat:
                view = view[view["category"].isin(cat)]
            if ids:
                view = view[view["sku_id"].isin(ids)]
            st.caption(f"{len(view):,} of {len(sku):,} SKUs")
            st.dataframe(view[["sku_id", "sku_code", "category", "subcategory", "segment", "archetype", "recommended_csl",
                               "annual_net_margin_usd", "demand_cv_base", "forecast_baseline_wmape", "lead_time_cv"]],
                         hide_index=True, width="stretch")
            slot["df"] = view
    with t3:
        rep = SEG / "segmentation_report.md"
        st.markdown(rep.read_text(encoding="utf-8", errors="replace") if rep.exists() else "No report.")
    if slot.get("df") is None:
        slot["df"] = cs


def newsvendor_recompute(holding_pct: float | None, shortage_mult: float | None) -> pd.DataFrame:
    """Re-run segmentation_v5's service ladder (service_ladder + assign_csl) with optional overrides."""
    from scipy.stats import norm
    sku = pd.read_csv(paths()["clusters"])
    sm = pd.read_parquet(ROOT / "input datasets" / "sku_master.parquet")
    cp = pd.read_parquet(ROOT / "input datasets" / "cost_parameters.parquet")
    cp_agg = cp.groupby("subcategory", as_index=False)[["stockout_cost_multiplier"]].mean()
    m = sku[["sku_id", "cluster", "archetype", "subcategory", "revenue_usd_total"]].merge(
        sm[["sku_id", "margin_usd", "unit_cost_usd", "holding_cost_per_unit_year_usd"]], on="sku_id").merge(
        cp_agg, on="subcategory", how="left")
    mult = m["stockout_cost_multiplier"] if shortage_mult is None else shortage_mult
    hold = m["holding_cost_per_unit_year_usd"] if holding_pct is None else m["unit_cost_usd"] * holding_pct / 100
    under = m["margin_usd"] * mult
    over = hold * 0.5
    m["cr"] = under / (under + over)
    g = m.groupby(["cluster", "archetype"]).agg(n_skus=("sku_id", "count"), rev=("revenue_usd_total", "sum"),
                                                 median_cr=("cr", "median")).reset_index()
    g["revenue_share"] = g["rev"] / g["rev"].sum()
    base = g["median_cr"].clip(0.85, 0.995)
    csl = np.where(g["revenue_share"] >= 0.30, np.maximum(base, 0.97),
                   np.where(g["revenue_share"] <= 0.03, np.minimum(base, 0.90), base))
    g["computed_csl"] = np.round(csl, 3)
    g["safety_factor_z"] = np.round(norm.ppf(g["computed_csl"]), 3)
    g["target_case_fill_rate"] = np.round(g["computed_csl"] - 0.02, 3)
    return g.drop(columns="rev")


def csl_sweep_charts(ev: pd.DataFrame, cs: pd.DataFrame | None):
    """CSL sweep as small multiples, one panel per cluster: own fill vs tested CSL (zoomed
    axis, so the rise is visible), then cost vs CSL. The orange dot is the CSL MEIO uses today
    (segmentation ladder); the green ring in the cost row is each cluster's cheapest CSL."""
    import altair as alt
    d = ev.copy()
    used = dict(zip(cs["archetype"], cs["recommended_csl"])) if cs is not None else {}
    d["used_today"] = [np.isclose(c, used.get(k, -1), atol=5e-4) for k, c in zip(d["cluster"], d["csl"])]
    d["cost_m"] = d["cost_delta_mean"] / 1e6
    d["cheapest"] = d["cost_m"] == d.groupby("cluster")["cost_m"].transform("min")
    order = [c for c in (cs["archetype"].tolist() if cs is not None else []) if c in set(d["cluster"])] or \
        sorted(d["cluster"].unique())
    d["csl_label"] = d["csl"].map(lambda v: f"{v * 100:.1f}%".replace(".0%", "%"))
    ticks = [f"{v * 100:.1f}%".replace(".0%", "%") for v in sorted(d["csl"].unique())]
    x = alt.X("csl_label:O", title="Target service level (tested values)", sort=ticks,
              axis=alt.Axis(labelAngle=0))
    tip = [alt.Tooltip("cluster:N", title="Cluster"), alt.Tooltip("csl:Q", title="CSL", format=".1%"),
           alt.Tooltip("own_fill_mean:Q", title="Own fill", format=".2%"),
           alt.Tooltip("cost_m:Q", title="Cost vs actual ($M)", format=",.2f")]

    def panel(y, color_flag, flag_color, flag_shape, y_title, y_fmt):
        base = alt.Chart().encode(x=x)
        line = base.mark_line(color="#2374CC", strokeWidth=2).encode(
            y=alt.Y(f"{y}:Q", title=y_title, scale=alt.Scale(zero=False), axis=alt.Axis(format=y_fmt)))
        pts = base.mark_point(filled=True, size=55, color="#2374CC").encode(y=f"{y}:Q", tooltip=tip)
        flag = base.transform_filter(alt.datum[color_flag]).mark_point(
            size=230, strokeWidth=3, color=flag_color, shape=flag_shape, filled=flag_shape == "circle").encode(
            y=f"{y}:Q", tooltip=tip)
        return alt.layer(line, pts, flag, data=d).properties(width=230, height=170).facet(
            facet=alt.Facet("cluster:N", title=None, sort=order, header=alt.Header(labelFontWeight="bold")),
            columns=3).resolve_scale(y="independent")

    st.markdown("**Own-cluster fill rises with the service-level target** "
                "<span style='color:#E8710A'>●</span> = CSL MEIO uses today", unsafe_allow_html=True)
    st.altair_chart(panel("own_fill_mean", "used_today", "#E8710A", "circle", "Own fill", ".1%"))
    st.markdown("**...and what it costs** (5-seed mean total landed cost above the actual 2025 policy; "
                "lower is better) "
                "<span style='color:#14956E'>●</span> = cheapest tested CSL", unsafe_allow_html=True)
    st.altair_chart(panel("cost_m", "cheapest", "#14956E", "circle", "Cost vs actual ($M)", ",.2f"))


def page_service_levels(slot: dict):
    p = paths()
    cs = read_csv(str(p["cluster_sl"]))
    # v5 evidence (service_level_v5_real_data_summary.csv): v5's 6 clusters, same archetype
    # names cs["archetype"] already uses -- no alias needed (the old v4 evidence,
    # service_level_real_data_summary.csv, only covered 4 of v5's 6 clusters and needed a
    # name translation for the one shared-but-renamed cluster; this replaces it outright).
    ev = read_csv(str(p["evidence"] / "service_level_v5_real_data_summary.csv"))
    st.write("Compute target service levels per cluster.")
    with st.form("sl"):
        method = st.selectbox("Optimisation method", ["Newsvendor (critical ratio)", "Cost-minimisation",
                                                      "Fill-rate target"])
        c1, c2 = st.columns(2)
        use_hold = c1.checkbox("Override holding cost", value=False,
                               help="Off: dataset holding_cost_per_unit_year_usd (segmentation_v5 default).")
        hold = c1.number_input("Holding cost rate (% of unit cost / yr)", 5, 60, 25, 1)
        use_short = c2.checkbox("Override shortage multiplier", value=False,
                                help="Off: dataset cost_parameters.stockout_cost_multiplier.")
        short = c2.number_input("Shortage penalty multiplier", 1.0, 20.0, 3.5, 0.5)
        fill_target = st.number_input("Fill-rate target (own fill, used by 'Fill-rate target' only)", 0.85, 0.99, 0.95, 0.01)
        go = st.form_submit_button("Compute service levels", icon=":material/calculate:", type="primary")
    if go:
        if method.startswith("Newsvendor"):
            res = newsvendor_recompute(hold if use_hold else None, short if use_short else None)
            if cs is not None:
                res = res.merge(cs[["cluster", "recommended_csl"]].rename(columns={"recommended_csl": "current_csl"}),
                                on="cluster")
            st.subheader("Newsvendor recompute (segmentation_v5 ladder logic)")
            st.dataframe(res, hide_index=True, width="stretch")
            slot["df"] = res
        elif ev is None:
            st.error("service_level_v5_real_data_summary.csv not found.")
        else:
            e = ev.copy()
            rows = []
            for arche in cs["archetype"]:
                sub = e[e["cluster"] == arche]
                if sub.empty:
                    rows.append({"archetype": arche, "evidence_cluster": None, "pick_csl": None,
                                 "note": "no twin-validated sweep for this cluster"})
                    continue
                if method == "Cost-minimisation":
                    r = sub.loc[sub["cost_delta_mean"].idxmin()]
                    note = "lowest mean landed-cost delta vs actual (5 seeds)"
                else:
                    ok = sub[sub["own_fill_mean"] >= fill_target]
                    r = ok.loc[ok["csl"].idxmin()] if len(ok) else sub.loc[sub["own_fill_mean"].idxmax()]
                    note = "lowest CSL reaching the target own fill" if len(ok) else "target not reached; highest-fill CSL shown"
                rows.append({"archetype": arche, "evidence_cluster": arche, "pick_csl": r["csl"],
                             "own_fill": r["own_fill_mean"], "cost_delta_usd": r["cost_delta_mean"], "note": note})
            res = pd.DataFrame(rows)
            st.subheader(f"{method} - lookup in Twin-validated sweep")
            st.dataframe(res, hide_index=True, width="stretch")
            st.caption("Holding / shortage inputs do not apply here: the sweep was run at fixed cost parameters.")
            slot["df"] = res
    st.subheader("Current recommendation - used by MEIO today")
    st.caption("The weekly MEIO takes each SKU's safety factor z from this cluster ladder (segmentation output, "
               "sku_clusters.csv). The calculator above is a what-if: its results are not applied.")
    if cs is not None:
        st.dataframe(cs[["cluster", "archetype", "n_skus", "median_newsvendor_cr", "recommended_csl",
                         "target_case_fill_rate", "safety_factor_z"]], hide_index=True, width="stretch")
        if slot.get("df") is None:
            slot["df"] = cs
    if ev is not None:
        st.subheader("Twin-validated CSL sweep (own-cluster fill vs CSL, 5 seeds)")
        st.caption("Built at the earlier portfolio alpha of 0.2, before alpha moved to 0.6 and per-cluster values, "
                   "so read the shape (how fill rises with service level), not the exact percentages. "
                   "This is why MEIO keeps the segmentation ladder rather than picking CSLs from this sweep.")
        csl_sweep_charts(ev, cs)
        with st.expander("Evidence table"):
            st.dataframe(ev, hide_index=True, width="stretch")
    if st.button("Apply to MEIO pipeline", icon=":material/send:", disabled=True,
                 help="Disabled: MEIO already uses the cluster ladder above. Replacing it with sweep-picked CSLs "
                      "needs a sweep re-run at today's alpha that beats the ladder on paired seeds."):
        pass


def page_allocation(slot: dict):
    p = paths()
    c1, c2 = st.columns([2, 5], vertical_alignment="center")
    with c1:
        mode = st.radio("Run", ["MEIO policy only", "MEIO + Digital Twin"], horizontal=True, label_visibility="collapsed")
    with c2:
        ok = confirm_box(f"Rebuild `output datasets/MEIO/{p['name']}` (overwrites)", "alloc_ok")
    if st.button("Run allocation", icon=":material/play_arrow:", type="primary", disabled=not ok):
        launch("alloc", "Weekly MEIO allocation", meio_steps(mode.endswith("Twin")))
    jobs_panel(("alloc",))
    st.subheader("Last output")
    summ = read_csv(str(p["weekly_dir"] / "meio_summary_by_dc_weekly.csv"))
    rec = read_parquet(str(p["weekly_dir"] / "meio_recommendations_weekly.parquet"))
    par = read_json(str(p["weekly_dir"] / "meio_parameters.json"))
    if summ is None:
        st.info("No weekly MEIO output yet. Run allocation.")
        return
    st.caption(f"{p['name']} / 2026-W01 - written {mtime(p['weekly_dir'] / 'meio_policy_weekly.parquet')}")
    a, b, c = st.columns(3)
    a.metric("DCs", len(summ))
    b.metric("Total safety stock (units)", f"{summ['total_safety_stock'].sum():,.0f}")
    c.metric("Safety stock value", f"${summ['total_safety_stock_usd'].sum() / 1e6:.2f}M")
    st.dataframe(summ, hide_index=True, width="stretch")
    pol = read_parquet(str(p["meio_dir"] / "rolling_2026" / "meio_policy_rolling.parquet"))
    sku_cat = read_csv(str(p["clusters"]))
    if pol is not None:
        st.subheader("Weekly policy (rolling 2026)")
        if sku_cat is not None:
            pol = pol.merge(sku_cat[["sku_id", "category"]], on="sku_id", how="left")
        weeks = sorted(pol["period"].unique())
        w1, w2 = st.select_slider("Weeks", options=weeks, value=(weeks[0], weeks[-1]))
        g1, g2, g3 = st.columns(3)
        dcs = g1.multiselect("DC", sorted(pol["dc_id"].unique()), key="pol_dc")
        cats = g2.multiselect("Category", sorted(pol["category"].dropna().unique()), key="pol_cat")
        ids = g3.multiselect("SKU", sorted(pol["sku_id"].unique()), key="pol_sku")
        v = pol[(pol["period"] >= w1) & (pol["period"] <= w2)]
        if dcs:
            v = v[v["dc_id"].isin(dcs)]
        if cats:
            v = v[v["category"].isin(cats)]
        if ids:
            v = v[v["sku_id"].isin(ids)]
        st.caption(f"{len(v):,} rows, {v['period'].nunique()} week(s)")
        if len(v):
            st.line_chart(v.groupby("period")[["safety_stock", "reorder_point", "order_up_to_level"]].sum())
        st.dataframe(v.head(5000), hide_index=True, width="stretch")
        if len(v) > 5000:
            st.caption("Showing the first 5,000 rows; use Export for the full filtered table.")
        slot["df"] = v
    if rec is not None:
        with st.expander(f"Recommendations by DC x SKU ({len(rec):,} rows)"):
            dc = st.multiselect("DC", sorted(rec["dc_id"].unique()))
            cat = st.multiselect("Category", sorted(rec["category"].unique()))
            v = rec
            if dc:
                v = v[v["dc_id"].isin(dc)]
            if cat:
                v = v[v["category"].isin(cat)]
            st.dataframe(v, hide_index=True, width="stretch")
    if par:
        with st.expander("Parameters used (meio_parameters.json)"):
            st.json({k: par[k] for k in ("model", "selection", "alpha", "netting_discount", "ddlt", "n_sims", "seed")})
    if slot.get("df") is None:
        slot["df"] = summ


def alpha_sweep_panel():
    """Re-tune alpha from the front end: the multi-seed per-cluster sweep
    (segment_alpha_v5_real_data.py) behind two confirmations, then the alpha check, so the
    page shows what the weekly MEIO gate would now pick."""
    with st.expander("Re-tune alpha: run the full alpha sweep", icon=":material/science:"):
        st.warning(
            "**Before running**\n\n"
            "- **Run time:** 6 clusters x 6 alphas x 5 seeds of the Digital Twin on replayed 2025 demand, "
            "about 30-60 minutes on most CPU cores. Other jobs in the app run slower meanwhile; schedule it "
            "outside active use.\n"
            "- **Effect on plans:** it rewrites the alpha evidence read by the weekly MEIO "
            "(`segment_alpha_v5_real_data_*.csv`). The next *Run allocation* or *Run all* adopts any per-cluster "
            "alpha that passes the 5-seed test (fill gain > 2 SE, cost not worse).\n"
            "- **Cadence:** alpha is reviewed quarterly or yearly, not weekly.\n"
            "- The current evidence is backed up first to `results/_backup/`.",
            icon=":material/warning:")
        fresh = st.checkbox("Start fresh (discard earlier sweep partial results and re-run every combo)",
                            key="sweep_fresh")
        ack = st.checkbox("I understand this takes a long time and can change the alpha used by future MEIO runs",
                          key="sweep_ack")
        word = st.text_input("Type SWEEP to enable the button", key="sweep_word", disabled=not ack)
        armed = ack and word.strip().upper() == "SWEEP"
        if st.button("Run alpha sweep", icon=":material/play_arrow:", type="primary", disabled=not armed,
                     key="sweep_go"):
            sweep = ROOT / "models" / "MEIO" / "MEIO_Weekly" / "final_submission_optimized" / \
                "segment_alpha_v5_real_data.py"
            launch("alpha_sweep", "Alpha sweep (re-tune)", [
                ("back up evidence", [PY, APP / "alpha_sweep_prep.py", *(["--fresh"] if fresh else [])]),
                ("alpha sweep (6 clusters x 6 alphas x 5 seeds)", [PY, sweep, "--lean-twin-outputs"]),
                ("alpha check (what the gate now picks)", [PY, APP / "alpha_check.py", RUNS / "alpha_receipt.json"]),
            ])
        jobs_panel(("alpha_sweep",))


def page_alpha(slot: dict):
    p = paths()
    if st.button("Run alpha check", icon=":material/play_arrow:", type="primary"):
        launch("alpha", "Periodic alpha check", alpha_steps())
    jobs_panel(("alpha",))
    alpha_sweep_panel()
    par = read_json(str(RUNS / "alpha_receipt.json")) or read_json(str(p["weekly_dir"] / "meio_parameters.json"))
    if par:
        a, b, c = st.columns(3)
        a.metric("Portfolio default alpha", par.get("alpha"),
                 help="Used for any cluster whose own test didn't beat it. Each cluster's actual alpha is below.")
        b.metric("Netting discount", par.get("netting_discount"))
        c.metric("Model", par.get("model"))
        st.markdown("**Alpha (pooling) used per cluster**")
        st.dataframe(alpha_by_cluster_table(par), hide_index=True, width="stretch")
        for d in par.get("decisions", []):
            st.markdown(f"- **{d['parameter']}** = `{d['value']}` - {d['reason']}")
    st.info("Note: alpha here is the Clark-Scarf v2 pooling factor (how much each RDC buffers for its CDCs' "
            "combined demand), not the smoothing alpha of a demand forecast.")
    st.subheader("Review flags (cluster-level proxy)")
    flags = alpha_flags()
    if len(flags):
        st.dataframe(flags, hide_index=True, width="stretch",
                     column_config={"own_fill_at_best": st.column_config.NumberColumn(format="percent"),
                                    "own_fill_at_adopted": st.column_config.NumberColumn(format="percent")})
        slot["df"] = flags

    ev5 = read_csv(str(p["evidence"] / "segment_alpha_v5_real_data_summary.csv"))
    if ev5 is not None:
        import altair as alt
        st.subheader("Own-fill vs alpha, by segment (5 seeds, real replayed demand)")
        st.caption("Each cluster's own fill rate as its alpha varies, with every other cluster held at the "
                   "reference alpha (0.6). The vertical line marks the portfolio default. The y-axis is zoomed "
                   "to show the spread (0.9 pp for Mid-Tier Core, 11.6 pp for Long-Tail Erratic 2).")
        adopted_alpha = (par or {}).get("alpha")
        line = alt.Chart(ev5).mark_line(point=True, strokeWidth=2).encode(
            x=alt.X("alpha:Q", title="Alpha (pooling factor)"),
            y=alt.Y("own_fill_mean:Q", title="Own-cluster fill rate", axis=alt.Axis(format="%"),
                   scale=alt.Scale(zero=False)),
            color=alt.Color("cluster:N", title=None),
            tooltip=["cluster", "alpha", alt.Tooltip("own_fill_mean:Q", format=".2%")],
        ).properties(height=380)
        if adopted_alpha is not None:
            rule = alt.Chart(pd.DataFrame({"alpha": [adopted_alpha]})).mark_rule(
                color="#8C8F99", strokeDash=[4, 4]).encode(x="alpha:Q")
            line = line + rule
        st.altair_chart(line, width="stretch")

        st.markdown("**Explore by segment**")
        st.caption("Select a cluster and an alpha; every other cluster stays at the reference alpha (0.6). "
                   "Own fill rate is specific to the selected cluster; the other measures are portfolio-wide. "
                   "Cost is the total landed cost difference against actual history. Fill rates cover the "
                   "51-week test window; the time-period selector applies to cost only.")
        SEG_WINDOW_WEEKS = 156   # same full-run cumulative convention as alpha_cost_breakdown.py's WINDOW_WEEKS
        alphas5 = sorted(ev5["alpha"].unique().tolist())
        clusters5 = ev5["cluster"].unique().tolist()
        default5 = 0.6 if 0.6 in alphas5 else alphas5[len(alphas5) // 2]
        c1, c2, c3 = st.columns([2, 2, 1])
        with c1:
            cluster5 = st.selectbox("Cluster", clusters5, key="alpha_explore_seg_cluster")
        with c2:
            chosen5 = st.select_slider("Alpha", options=alphas5, value=default5, key="alpha_explore_seg_alpha")
        with c3:
            period5 = st.radio("Time period", ["Week", "Month", "Year", "All"], index=3,
                               key="alpha_explore_seg_period")
        per_week5 = 1.0 / SEG_WINDOW_WEEKS
        divisor5 = {"Week": per_week5, "Month": per_week5 * 52 / 12, "Year": per_week5 * 52, "All": 1.0}[period5]
        suffix5 = {"Week": " / week", "Month": " / month", "Year": " / year", "All": " (3-yr total)"}[period5]
        row5 = ev5[(ev5["cluster"] == cluster5) & (ev5["alpha"] == chosen5)].iloc[0]
        portfolio_help = ("Portfolio-wide measure. It is the same for every cluster at the 0.6 reference and "
                          "changes only when the selected cluster's alpha moves away from 0.6.")
        mm1, mm2 = st.columns(2)
        mm1.metric("Own fill rate", f"{row5['own_fill_mean']:.1%}")
        mm2.metric("Portfolio fill rate", f"{row5['portfolio_fill_mean']:.1%}", help=portfolio_help)
        mm3, mm4 = st.columns(2)
        mm3.metric("Internal replen fill", f"{row5['internal_replen_mean']:.1%}", help=portfolio_help)
        mm4.metric(f"Cost delta vs actual{suffix5}", f"${row5['cost_delta_mean'] * divisor5:,.0f}",
                  help=portfolio_help)

    sw_fill = read_csv(str(p["evidence"] / "alpha_sweep_real_data.csv"))
    if sw_fill is not None:
        import altair as alt
        st.subheader("Portfolio totals vs alpha (51-week real-demand backtest)")
        st.caption("Portfolio customer fill and internal replenishment fill at each tested alpha.")
        tot = sw_fill[["alpha", "customer_fill_rate", "internal_replen_fill_rate"]].melt(
            id_vars="alpha", var_name="metric", value_name="rate")
        tot["metric"] = tot["metric"].map({"customer_fill_rate": "Customer fill rate",
                                           "internal_replen_fill_rate": "Internal replen fill rate"})
        bar = alt.Chart(tot).mark_bar().encode(
            x=alt.X("metric:N", title=None, axis=None),
            y=alt.Y("rate:Q", title="Fill rate", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
            color=alt.Color("metric:N", title=None, scale=alt.Scale(range=["#2374CC", "#14956E"])),
            column=alt.Column("alpha:O", title="Alpha (pooling factor)", header=alt.Header(labelOrient="bottom")),
            tooltip=["alpha", "metric", alt.Tooltip("rate:Q", format=".1%")],
        ).properties(width=70, height=280)
        st.altair_chart(bar, width="content")

    cb = read_csv(str(p["evidence"] / "alpha_cost_breakdown.csv"))
    if cb is not None:
        import altair as alt
        st.subheader("Cost breakdown across alpha (156-week real-demand simulation)")
        st.caption("Higher alpha trades stockout cost for transport cost; holding cost stays nearly flat. "
                   "Totals cover the full 156-week (3-year) simulation. Use the selector below for weekly, "
                   "monthly or yearly figures.")
        long = cb.melt(id_vars="alpha", value_vars=["holding_cost_usd", "stockout_cost_usd", "transport_cost_usd"],
                       var_name="component", value_name="usd")
        long["component"] = long["component"].str.replace("_cost_usd", "", regex=False).str.title()
        # Three independent y-scales, not one shared axis: holding (~$2M), stockout (~$8M)
        # and transport (~$79M) are different orders of magnitude, and a shared axis
        # flattens exactly the trends (stockout falling, transport rising) this chart
        # exists to show.
        colors = {"Holding": "#2374CC", "Stockout": "#9B2828", "Transport": "#D68D15"}
        charts = []
        for comp in ["Holding", "Stockout", "Transport"]:
            sub = long[long["component"] == comp]
            charts.append(
                alt.Chart(sub).mark_line(point=True, strokeWidth=2.5, color=colors[comp]).encode(
                    x=alt.X("alpha:Q", title="Alpha (pooling factor)"),
                    y=alt.Y("usd:Q", title=f"{comp} cost ($)", scale=alt.Scale(zero=False),
                           axis=alt.Axis(format="$,.2s")),
                    tooltip=["alpha", alt.Tooltip("usd:Q", format="$,.0f", title=f"{comp} cost")],
                ).properties(height=180, title=comp)
            )
        st.altair_chart(alt.vconcat(*charts).resolve_scale(y="independent"), width="stretch")

        st.markdown("**Explore a specific alpha (portfolio-wide)**")
        st.caption("Applies one alpha to the whole portfolio. For the per-cluster view, see "
                   "\"Explore by segment\" above.")
        alphas_sorted = sorted(cb["alpha"].unique().tolist())
        c1, c2 = st.columns([3, 1])
        with c1:
            chosen = st.select_slider("Alpha (pooling factor)", options=alphas_sorted,
                                      value=alphas_sorted[len(alphas_sorted) // 2], key="alpha_cost_slider")
        with c2:
            period = st.radio("Time period", ["Week", "Month", "Year", "All"], index=3, horizontal=True,
                              key="alpha_cost_period")
        row = cb[cb["alpha"] == chosen].iloc[0]
        window_weeks = float(row["window_weeks"])
        # Cumulative dollars over the full 156-week simulation aren't an intuitive unit for a
        # planner -- divide down to a weekly rate, then scale to the chosen cadence. 52 weeks/
        # year and 12 months/year (so 52/12 weeks/month) are the standard conventions; "All"
        # shows the raw cumulative total, unscaled.
        per_week = 1.0 / window_weeks
        divisor = {"Week": per_week, "Month": per_week * 52 / 12, "Year": per_week * 52, "All": 1.0}[period]
        suffix = {"Week": " / week", "Month": " / month", "Year": " / year", "All": " (3-yr total)"}[period]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"Holding cost{suffix}", f"${row['holding_cost_usd'] * divisor:,.0f}")
        m2.metric(f"Stockout cost{suffix}", f"${row['stockout_cost_usd'] * divisor:,.0f}")
        m3.metric(f"Transport cost{suffix}", f"${row['transport_cost_usd'] * divisor:,.0f}")
        m4.metric(f"Total landed cost{suffix}", f"${row['total_landed_cost_usd'] * divisor:,.0f}")
        st.caption("Fill rates cover the 51-week test window (2025-W02 to 2025-W52) and are not affected by "
                   "the time-period selector.")
        f1, f2 = st.columns(2)
        f1.metric("Customer fill rate", f"{row['customer_fill_rate']:.1%}")
        f2.metric("Internal replen fill rate", f"{row['internal_replen_fill_rate']:.1%}")
    else:
        st.info("No cost breakdown yet. Run the alpha sweep (`validate_real_data.py --stage alpha_sweep` in "
                "final_submission_optimized/) to generate `alpha_cost_breakdown.csv`.")

    sw = read_csv(str(p["evidence"] / "alpha_sweep_real_data.csv"))
    if sw is not None:
        with st.expander("Portfolio alpha sweep (raw)"):
            st.dataframe(sw, hide_index=True, width="stretch")


def list_scenarios() -> list[str]:
    root = OUT / "overrides"
    return sorted(d.name for d in root.iterdir() if (d / "scenario_config.json").exists()) if root.exists() else []


IMPACT_METRICS = [("customer_fill_rate", "Customer fill (pp)", 100), ("customer_fill_rate_cdc", "CDC fill (pp)", 100),
                  ("internal_replen_fill_rate", "Internal replen fill (pp)", 100),
                  ("stockout_cost_usd", "Lost-sales cost ($)", 1), ("holding_cost_usd", "Holding cost ($)", 1),
                  ("total_landed_cost_usd", "Total landed cost ($)", 1)]


def _week_no(period: str) -> int:
    return int(str(period).split("-W")[1])


def scenario_windows(scenario: str) -> list[tuple[int, int, str]]:
    """Distinct (start_week, end_week, label) windows of a scenario's windowed overrides, so a short
    shock (e.g. 6 weeks of lead-time disruption) can be read on its own instead of averaged into 52."""
    cfg = _read_json_live(OUT / "overrides" / scenario / "scenario_config.json") or {}
    seen = {}
    for o in (cfg.get("changes") or {}).get("overrides") or []:
        w = o.get("window")
        if w and w.get("start_period") and w.get("end_period"):
            a, b = _week_no(w["start_period"]), _week_no(w["end_period"])
            if a <= 1 and b >= 52:                 # the whole year: same as "Full horizon"
                continue
            seen.setdefault((a, b), []).append(o.get("measure", o.get("id", "")))
    return [(a, b, f"W{a:02d}-W{b:02d} ({', '.join(m)})") for (a, b), m in sorted(seen.items())]


@st.cache_data(show_spinner=False, max_entries=64)
def _window_kpis(inv_path: str, mtime: float, weeks: tuple[int, int] | None, inside: bool) -> dict | None:
    """Fill rates and costs recomputed from a run's weekly inventory table for a week window.
    `mtime` is only in the signature so a rewritten run invalidates the cache."""
    p = Path(inv_path)
    if not p.exists():
        return None
    inv = pd.read_parquet(p, columns=["period", "dc_id", "demand_customer_qty", "unmet_customer_qty",
                                       "demand_replen_qty", "unmet_replen_qty", "lost_margin_usd",
                                       "holding_cost_usd"])
    wk = inv["period"].map(_week_no)
    if weeks:
        m = wk.between(*weeks)
        inv = inv[m if inside else ~m]
    if inv.empty:
        return None
    cdc = inv[inv["dc_id"].str.startswith("CDC")]

    def fill(d, dem, un):
        tot = d[dem].sum()
        return float(1 - d[un].sum() / tot) if tot else None

    return {"customer_fill_rate": fill(inv, "demand_customer_qty", "unmet_customer_qty"),
            "customer_fill_rate_cdc": fill(cdc, "demand_customer_qty", "unmet_customer_qty"),
            "internal_replen_fill_rate": fill(inv, "demand_replen_qty", "unmet_replen_qty"),
            "lost_margin_usd": float(inv["lost_margin_usd"].sum()),
            "holding_cost_usd": float(inv["holding_cost_usd"].sum())}


WINDOW_METRICS = [("customer_fill_rate", "Customer fill (pp)", 100), ("customer_fill_rate_cdc", "CDC fill (pp)", 100),
                  ("internal_replen_fill_rate", "Internal replen fill (pp)", 100),
                  ("lost_margin_usd", "Lost margin ($)", 1), ("holding_cost_usd", "Holding cost ($)", 1)]


REACTIVE_CONTROL = "counterfactual_reactive_no_scenario"


def _run_label(n: str) -> str:
    if " / " in n:
        return n.split(" / ")[-1]
    return "no scenario + reactive" if REACTIVE_CONTROL in n else "no scenario"


def scenario_impact_table(rows: dict, run_paths: dict):
    """The comparisons a scenario exists to answer, from whichever of its runs are selected:
    what the scenario costs if ignored (unprepared vs no scenario), what re-planning in advance
    buys (prepared vs unprepared), what re-planning every week buys (reactive vs unprepared) --
    split, when the reactive control is selected, into the normal-year effect of weekly
    re-planning and the reaction to this scenario -- and where preparing leaves you (prepared vs
    no scenario). Full horizon, or the weeks a windowed override is active."""
    prep = next((n for n in rows if n.endswith(" / prepared")), None)
    unprep = next((n for n in rows if n.endswith(" / unprepared")), None)
    react = next((n for n in rows if n.endswith(" / reactive")), None)
    base = next((n for n in rows if n.startswith("No scenario (") and REACTIVE_CONTROL not in n), None)
    rbase = next((n for n in rows if n.startswith("No scenario (") and REACTIVE_CONTROL in n), None)
    REACT, NORMAL, SCEN = ("Value of re-planning weekly (reactive - unprepared)",
                           "   of which: weekly re-planning in a normal year (reactive control - no scenario)",
                           "   of which: reacting to this scenario (difference of the two rows above)")
    pairs = [("Scenario impact if ignored (unprepared - no scenario)", unprep, base),
             ("Value of re-planning in advance (prepared - unprepared)", prep, unprep),
             (REACT, react, unprep), (NORMAL, rbase, base),
             ("Net position after preparing (prepared - no scenario)", prep, base)]
    if not any(a and b for _, a, b in pairs):
        return
    arm = prep or unprep or react
    scenario = arm.split(" / ")[0] if arm else None     # None: only the no-scenario runs have results yet
    options = {"Full horizon": None}
    for a, b, label in (scenario_windows(scenario) if scenario else []):
        options[f"During {label}"] = ((a, b), True)
        options[f"Outside W{a:02d}-W{b:02d}"] = ((a, b), False)
    st.markdown("**Scenario impact** (differences between the selected runs)")
    period = st.radio("Period", list(options), horizontal=True, key="impact_period",
                      help="A short disruption is diluted in full-year averages; view it over its own window.")
    choice = options[period]
    if choice is None:
        vals, metrics = rows, IMPACT_METRICS
    else:
        vals, metrics = {}, WINDOW_METRICS
        for n in [x for x in (prep, unprep, react, base, rbase) if x]:
            inv = Path(run_paths[n]) / "inventory.parquet"
            k = _window_kpis(str(inv), inv.stat().st_mtime if inv.exists() else 0.0, choice[0], choice[1])
            if k:
                vals[n] = k
    out = {}
    for label, a, b in pairs:
        if a in vals and b in vals:
            out[label] = {name: round((float(vals[a][k]) - float(vals[b][k])) * scale, 2 if scale == 100 else 0)
                          for k, name, scale in metrics
                          if vals[a].get(k) is not None and vals[b].get(k) is not None}
    if REACT in out and NORMAL in out:          # keep the scenario split next to the weekly-re-planning row
        out[SCEN] = {k: round(out[REACT][k] - out[NORMAL].get(k, 0.0), 2) for k in out[REACT]}
        order = [l for l, _, _ in pairs if l in out]
        order.insert(order.index(NORMAL) + 1, SCEN)
        out = {l: out[l] for l in order}
    if out:
        st.dataframe(pd.DataFrame(out).T, width="stretch")
        if react and not rbase:
            st.caption("Select the run 'No scenario (counterfactual_reactive_no_scenario)' too, to split weekly "
                       "re-planning into its normal-year effect and the reaction to this scenario.")
        if choice is not None:
            levels = {(run_display_name(n).split(" · ")[0] if " / " in n else run_display_name(n)): vals[n]
                      for n in vals}
            st.dataframe(pd.DataFrame({n: {name: round(v[k] * 100, 2) if scale == 100 else round(v[k])
                                           for k, name, scale in WINDOW_METRICS if v.get(k) is not None}
                                       for n, v in levels.items()}).T.rename(
                columns=lambda c: c.replace(" (pp)", " (%)")), width="stretch")
    st.caption("Fill-rate differences are percentage points; costs are USD over the selected period "
               "(windowed views recompute from each run's weekly inventory table; lost margin there "
               "counts lost-sale accounts only). Single seeded run each: small differences can be noise.")


ARM_TITLES = {"prepared": "Prepared", "unprepared": "Unprepared", "reactive": "Reactive"}


def run_display_name(n: str) -> str:
    """Run type first, so it stays readable when a column or chip is narrowed: the internal
    keys ("<scenario> / <arm>", "No scenario (<folder>)") are unchanged everywhere else."""
    if " / " in n:
        scn, arm = n.rsplit(" / ", 1)
        return f"{ARM_TITLES.get(arm, arm.title())} · {scn.replace('_', ' ')}"
    if n.startswith("No scenario ("):
        return "No scenario · reactive" if REACTIVE_CONTROL in n else "No scenario · today's policy"
    return n


def page_simulation(slot: dict):
    p = paths()
    sims = [j for j in job_registry().values() if j["key"] == "sim"]
    st.subheader("Configure & launch (sandbox Digital Twin run)")
    with st.form("sim"):
        c1, c2, c3 = st.columns(3)
        policy_opt = c1.selectbox("Policy to simulate", ["MEIO rolling policy (Clark-Scarf v2)", "Baseline forward policy"])
        horizon = c2.number_input("Horizon (weeks)", 4, 104, 52, 1,
                                  help="Forward data covers 2026 (52 weeks); longer values are untested.")
        seed = c3.number_input("Seed", 0, 99999, int(p["cfg"]["digital_twin"].get("seed", 2026)), 1)
        st.caption("Each run is a single seeded replay. Demand and supply scenarios are created with the "
                   "scenario agents below.")
        go = st.form_submit_button("Launch simulation", icon=":material/play_arrow:", type="primary")
    if go:
        start_sim(policy_opt, int(horizon), int(seed))
    jobs_panel(("sim",))

    st.subheader("Results")
    runs = {"Forward plan - MEIO policy": p["twin_dir"], "Forward plan - baseline policy": p["baseline_dir"]}
    for s in list_scenarios():
        for arm in ("prepared", "unprepared", "reactive"):
            if (OUT / s / arm / "run_kpis.json").exists() or arm != "reactive":
                runs[f"{s} / {arm}"] = OUT / s / arm
    # No-scenario counterfactuals: today's policy, no overrides, same seed -- ad hoc runs
    # (python digital_twin_fast.py --scenario-config scenario_config.json --policy <baseline>
    # --run-name counterfactual_<label>), not launched from this UI, but worth surfacing here
    # since they're the "what if we hadn't hit this scenario at all" comparison a scenario's
    # own prepared/unprepared arms can't provide by themselves.
    for d in sorted((OUT / p["name"]).glob("counterfactual_*")) if (OUT / p["name"]).exists() else []:
        if (d / "run_kpis.json").exists():
            runs[f"No scenario ({d.name})"] = d
    for d in sorted(RUNS.glob("twin_*")):
        for k in d.rglob("run_kpis.json"):
            runs[f"Sandbox {d.name}"] = k.parent
    # Default to the most recently built scenario's own arms plus a no-scenario
    # counterfactual, when either exists -- otherwise nothing useful shows up without the
    # user first knowing to go find the right entries in a dropdown themselves.
    scenario_names = [n for n in runs if " / " in n and n.split(" / ")[0] in list_scenarios()]
    counterfactual_names = [n for n in runs if n.startswith("No scenario (")]
    newest = max((n.split(" / ")[0] for n in scenario_names), default=None)
    smart_default = [n for n in scenario_names if n.split(" / ")[0] == newest] + counterfactual_names
    names = st.multiselect("Compare runs", list(runs), format_func=run_display_name,
                           default=smart_default or [n for n in list(runs)[:2]])
    rows = {}
    for n in names:
        k = kpis(runs[n])
        if k:
            rows[n] = k
    if rows:
        df = pd.DataFrame(rows).T
        keep = ["customer_fill_rate", "customer_fill_rate_cdc", "customer_fill_rate_rdc", "pct_demand_weeks_in_full",
                "internal_replen_fill_rate", "avg_inventory_value_usd", "safety_stock_value_usd", "holding_cost_usd",
                "stockout_cost_usd", "transport_cost_usd", "total_landed_cost_usd", "horizon_weeks"]
        st.dataframe(df[[c for c in keep if c in df.columns]].rename(index=run_display_name), width="stretch")
        slot["df"] = df.reset_index(names="run")
        scenario_impact_table(rows, {n: runs[n] for n in rows})
        sv = runs[names[0]] / "service_by_node.csv"
        node = read_csv(str(sv))
        if node is not None:
            with st.expander(f"Service by node - {run_display_name(names[0])}"):
                st.dataframe(node, hide_index=True, width="stretch")
    st.subheader("Scenario agents")
    st.caption("Describe a what-if in plain English. If the agents ask a clarifying question, reply below; "
               "the session keeps its context.")
    server_has_claude_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    active_chat = agent_chat_registry().get("chat")
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        profile_label = st.radio(
            "Model profile", ["Desktop bridge (no API key)", "Claude (needs an Anthropic API key)"],
            index=1 if server_has_claude_key else 0, horizontal=True, key="agent_profile_choice")
    profile = "claude" if profile_label.startswith("Claude") else "desktop"
    typed_claude_key = ""
    with c2:
        if profile == "claude":
            typed_claude_key = st.text_input(
                "Anthropic API key", type="password", key="claude_api_key_input",
                placeholder="Already set on the server" if server_has_claude_key else "sk-ant-...",
                help="Kept only in this process's memory for this session, never written to disk or logged. "
                     "Leave blank to use the server's own ANTHROPIC_API_KEY, if it has one.")
    effective_claude_key = typed_claude_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "")
    has_claude_key = bool(effective_claude_key)
    with c3:
        st.markdown("<div style='height: 1.9rem'></div>", unsafe_allow_html=True)
        new_session_disabled = profile == "claude" and not has_claude_key
        if st.button("New session", key="agent_new_session", disabled=new_session_disabled,
                     help="Starting a new session replaces the current conversation." if active_chat else None):
            start_agent_chat(profile, effective_claude_key)
            st.rerun()
    if profile == "desktop":
        st.caption("Desktop bridge: no API key needed. Each model call is relayed through a local mailbox "
                   "and answered by the desktop model session; the panel below the chat shows the pending call. "
                   "Approvals happen in the chat.")
    elif not has_claude_key:
        st.warning("No Anthropic API key available. Enter one above, or set ANTHROPIC_API_KEY on the server and "
                   "restart it.")
    else:
        st.caption("Claude profile (Opus 5 orchestrator, Haiku 4.5 specialists): replies, including "
                   "clarifying questions, appear in the chat below.")
    agent_chat_panel()
    if active_chat and active_chat.get("profile") == "desktop":
        agent_bridge_panel()

    st.subheader("Scenarios from the Agents' override engine")
    sc = list_scenarios()
    if sc:
        # Newest first by default: folder names carry a zero-padded scenario number, so the last
        # sorted entry is the one just built -- the one a planner almost always wants to look at.
        pick = st.selectbox("Scenario", sc, index=len(sc) - 1)
        base = OUT / "overrides" / pick
        stt = _read_json_live(base / "status.json") or {}
        card = base / "scenario_card.md"
        cfg_path = base / "scenario_config.json"
        approval = _read_json_live(base / "approval.json")
        if approval and cfg_path.exists():
            current_sha = hashlib.sha256(cfg_path.read_bytes()).hexdigest()
            approval_state = "approved" if approval.get("config_sha256") == current_sha else "stale"
        else:
            approval_state = "awaiting_approval"
        st.caption(f"Run status: {stt.get('state', 'unknown')} | Approval: {approval_state}")
        if card.exists():
            with st.expander("Scenario card"):
                st.markdown(card.read_text(encoding="utf-8", errors="replace"))
        if approval_state == "approved":
            st.success(f"Approved by {approval['approved_by']} at {approval['approved_at']}")
        else:
            if approval_state == "stale":
                st.warning("The configuration changed after approval. Re-approval required.")
            name = st.text_input("Your name (recorded in approval.json)", key="scenario_approver_name")
            if st.button("Approve", type="primary", key="scenario_approve_btn", disabled=not name.strip()):
                res = subprocess.run([PY, "-m", "Agents.tools", "approve", pick, "--by", name.strip(), "--yes"],
                                     cwd=ROOT, capture_output=True, text=True)
                if res.returncode == 0:
                    st.success("Approved.")
                    st.rerun()
                else:
                    st.error((res.stdout or "") + (res.stderr or ""))
    else:
        st.info("No scenarios yet. Create them with the Agents (python -m Agents.runtime ...).")


# --------------------------------------------------------------------------- shell
with st.sidebar:
    st.markdown(
        "<div class='sb-brand'><div class='sb-brand-mark'><svg width='22' height='22' viewBox='0 0 24 24' fill='none' stroke='#fff' stroke-width='1.6' stroke-linecap='round'><path d='M12 4.5 6 12M12 4.5 18 12M6 12l-2.5 7M6 12l3 7M18 12l-3 7M18 12l2.5 7'/><circle cx='12' cy='4.5' r='2.3' fill='#fff'/><circle cx='6' cy='12' r='1.9' fill='#fff'/><circle cx='18' cy='12' r='1.9' fill='#fff'/><circle cx='3.5' cy='19' r='1.4' fill='#fff'/><circle cx='9' cy='19' r='1.4' fill='#fff'/><circle cx='15' cy='19' r='1.4' fill='#fff'/><circle cx='20.5' cy='19' r='1.4' fill='#fff'/></svg></div>"
        "<div class='sb-brand-text'><div class='sb-brand-name'>Supply Brain</div>"
        "<div class='sb-brand-sub'>MEIO Optimiser</div></div></div>",
        unsafe_allow_html=True)
    st.caption("OVERVIEW")
    st.radio("Navigation", list(PAGES), format_func=lambda k: f"{PAGE_ICONS[k]} {PAGES[k]}", key="page",
             label_visibility="collapsed")

slot: dict = {}
header = st.container()
run_all_clicked = False
with header:
    run_all_clicked, export_col = topbar()

page = st.session_state["page"]
body = {"dashboard": lambda: page_dashboard(slot), "segmentation": lambda: page_segmentation(slot),
        "service-levels": lambda: page_service_levels(slot), "allocation": lambda: page_allocation(slot),
        "alpha": lambda: page_alpha(slot), "simulation": lambda: page_simulation(slot)}[page]
# "Run all" shows its confirmation panel right here, on whichever page you're on --
# it never navigates you away. page_dashboard() does NOT render its own copy of this
# (see its definition): this is the only place it's rendered, so there's no duplicate
# on the dashboard page itself.
run_all_panel(run_all_clicked)
body()
with export_col:
    df = slot.get("df")
    if df is not None:
        st.download_button("Export", df.to_csv(index=False).encode(), file_name=f"{page}.csv", mime="text/csv",
                           icon=":material/upload:", width="stretch")
    else:
        st.button("Export", icon=":material/upload:", disabled=True, width="stretch",
                  help="No table on this page to export.")
