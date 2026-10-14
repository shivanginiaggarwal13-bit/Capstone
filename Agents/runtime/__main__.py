"""
Talk to the scenario agents (LangGraph runtime).

    python -m Agents.runtime "What if Southeast beverage demand rises 25% in July?"
    python -m Agents.runtime                       # interactive session
    python -m Agents.runtime --profile desktop ...  # no API key: Claude Desktop answers
                                                    # through runtime/bridge.py

The approval gate pauses the graph: at a terminal you are shown the scenario card and
asked; under the desktop profile the decision is relayed through the bridge; with no
person available the scenario stays pending and can be approved later from the CLI.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import bridge, graph, llm
from .utils import StreamingCallbackHandler


def _terminal_decision(approver: str | None):
    def decide(payload: dict) -> dict:
        if not sys.stdin.isatty():
            return {"pending": True}
        print("\n" + "=" * 78 + "\n" + payload["card"] + "\n" + "=" * 78)
        if input(f"Approve running {payload['scenario_folder']}? [y/N] ").strip().lower() not in ("y", "yes"):
            return {"approved": False, "feedback": input("What should change? ").strip()}
        name = approver or input("Your name (recorded in approval.json): ").strip()
        return {"approved": True, "approved_by": name or os.environ.get("USERNAME", "planner")}
    return decide


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m Agents.runtime", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", nargs="*", help="a planner request; omit for an interactive session")
    ap.add_argument("--profile", default=None, help="model profile from runtime/profiles.yaml")
    ap.add_argument("--approver", default=None, help="name recorded when you approve at the terminal")
    ap.add_argument("--no-stream", action="store_true", help="disable streaming output")
    args = ap.parse_args(argv)

    profile = llm.profile_name(args.profile)
    desktop = llm.provider_of(profile) == "desktop"
    streaming = not args.no_stream and not desktop
    if desktop:
        print(f"Claude Desktop bridge session: {bridge.start_session()}", flush=True)
    session = graph.Session(graph.Team(profile, streaming=streaming), streaming=streaming,
                            decide=bridge.await_approval if desktop else _terminal_decision(args.approver))
    print(f"profile {profile}; transcript {session.transcript}", flush=True)
    reply = ""
    try:
        if args.request:
            reply = session.ask(" ".join(args.request))
            print("\n" + reply)
            return 0
        print("Supply Brain scenario agents. Describe a scenario; blank line to quit.")
        while True:
            text = input("\nplanner> ").strip()
            if not text:
                return 0
            reply = session.ask(text)
            print("\n" + reply)
    except (EOFError, KeyboardInterrupt):
        return 0
    finally:
        if desktop:
            bridge.end_session(reply[:2000])


if __name__ == "__main__":
    sys.exit(main())
