"""
Command line for people: inspect tools, call any tool as an agent would, read a
scenario card, and -- the one thing no agent can do -- approve a scenario run.

    python -m Agents.tools tools --agent demand_agent
    python -m Agents.tools call resolve_window --agent orchestrator --args '{"month": "2026-07"}'
    python -m Agents.tools card SCENARIO_0003_Southeast_Heatwave
    python -m Agents.tools approve SCENARIO_0003_Southeast_Heatwave --by "Planner Name"
    python -m Agents.tools status SCENARIO_0003_Southeast_Heatwave
    python -m Agents.tools readiness
"""
from __future__ import annotations

import argparse
import json
import sys

from . import engine, registry, scenario


def _print(obj) -> None:
    print(json.dumps(registry.to_jsonable(obj), indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m Agents.tools", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("tools", help="list the tools an agent can call")
    p.add_argument("--agent", default="orchestrator", choices=sorted(registry.AGENTS))
    p.add_argument("--schemas", action="store_true", help="print full Claude tool definitions")
    p = sub.add_parser("call", help="call one tool as an agent")
    p.add_argument("tool")
    p.add_argument("--agent", default="orchestrator", choices=sorted(registry.AGENTS))
    p.add_argument("--args", default="{}", help="JSON object of tool arguments")
    p = sub.add_parser("card", help="print a scenario card")
    p.add_argument("scenario")
    p = sub.add_parser("approve", help="approve a scenario run (people only)")
    p.add_argument("scenario")
    p.add_argument("--by", required=True, help="name of the person approving")
    p.add_argument("--allow-overwrite", action="store_true", help="permit replacing existing outputs")
    p.add_argument("--note", default="")
    p.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    p = sub.add_parser("status", help="run status of a scenario")
    p.add_argument("scenario")
    sub.add_parser("readiness", help="what the installed MEIO/Twin pipeline can apply")
    p = sub.add_parser("run-job", help=argparse.SUPPRESS)
    p.add_argument("scenario")
    args = ap.parse_args(argv)

    if args.cmd == "tools":
        specs = registry.tool_specs(args.agent)
        if args.schemas:
            _print(specs)
        else:
            for s in specs:
                print(f"{s['name']:<26} {s['description'][:100]}")
        return 0
    if args.cmd == "call":
        result = registry.dispatch(args.agent, args.tool, json.loads(args.args))
        _print(result)
        return 0 if result.get("ok", True) else 1
    if args.cmd == "card":
        print((scenario.scenario_dir(args.scenario) / "scenario_card.md").read_text(encoding="utf-8"))
        return 0
    if args.cmd == "approve":
        card = scenario.scenario_dir(args.scenario) / "scenario_card.md"
        if card.exists():
            print(card.read_text(encoding="utf-8"))
        if not args.yes:
            answer = input(f"Approve running {args.scenario} as {args.by}? Type the scenario number to confirm: ")
            if answer.strip().lstrip("0") != args.scenario.split("_")[1].lstrip("0"):
                print("Not approved.")
                return 1
        _print(scenario.approve(args.scenario, args.by, args.allow_overwrite, args.note))
        return 0
    if args.cmd == "status":
        _print(scenario.run_status(args.scenario))
        return 0
    if args.cmd == "readiness":
        _print(engine.readiness())
        return 0
    if args.cmd == "run-job":
        return scenario.run_job(args.scenario)
    return 2


if __name__ == "__main__":
    sys.exit(main())
