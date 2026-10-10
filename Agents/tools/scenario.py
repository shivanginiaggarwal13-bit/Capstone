"""
From a validated draft to a running scenario: config assembly, the scenario card a
planner approves, the approval record, and the gated run.

Everything for one scenario lives in output datasets/overrides/SCENARIO_####_Name/
(root from the config's `overrides.output_root`, folder from scenario_number and
scenario_name, exactly like the MEIO and Twin folders):
  scenario_config.json   the generated config both models run
  scenario_card.md       what a person reads before approving
  approval.json          who approved which exact config (SHA-256)
  status.json, run.log   the gated run, per evaluation arm
  receipts/              the override engine's record of what it applied (Phase 1)

The human gate is a file, not a conversation. `approve()` -- reachable only from
the CLI (`python -m Agents.tools approve ...`) or the runtime's interactive prompt,
never from an agent tool -- writes approval.json holding the SHA-256 of the exact
config bytes that were approved. run_scenario refuses unless that hash still
matches, so a config edited after approval cannot run and an agent cannot argue
itself into a run.

Evaluation arms (the protocol of models/MEIO/MEIO_Weekly/final_submission/scenario_test.py):
  baseline    the root-config run, already on disk -- no overrides
  unprepared  the baseline policy, Twin simulates the actual-side overrides
              ("the scenario hits and we do nothing")
  prepared    MEIO re-plans with the plan-side overrides, Twin simulates the actual side
              ("we see it coming and re-plan")
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

from . import catalog, drafts, engine, impact, paths

CONFIG_SCHEMA_VERSION = "1.1"


def _models_scenario():
    if str(paths.REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(paths.REPO_ROOT))
    from models import scenario
    return scenario


def next_scenario_number() -> int:
    numbers = []
    roots = [paths.MEIO_OUTPUT_DIR, paths.MEIO_OUTPUT_DIR / "Scenario", paths.PROJECTIONS_DIR,
             paths.MEIO_ARTIFACTS_DIR, paths.overrides_root()]
    for root in roots:
        if root.is_dir():
            for p in root.iterdir():
                m = re.match(r"^SCENARIO_(\d{4})_", p.name)
                if m:
                    numbers.append(int(m.group(1)))
    return max(numbers, default=0) + 1


def arms_for(overrides: list[dict]) -> list[str]:
    sides = {s for o in overrides for s in o["applies_to"]}
    if "plan" in sides and "actual" in sides:
        return ["prepared", "unprepared"]
    return ["prepared"] if "plan" in sides else ["unprepared"]


def scenario_dir(folder: str):
    """output datasets/overrides/SCENARIO_####_Name -- everything this layer and the
    override engine write for one scenario."""
    if not re.fullmatch(r"SCENARIO_\d{4}_[A-Za-z0-9_]+", folder or ""):
        raise ValueError(f"expected a scenario folder name like SCENARIO_0003_Name, got {folder!r}")
    return paths.overrides_root() / folder


def config_sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ build --
def build_config(draft_id: str) -> dict:
    d = drafts.load_draft(draft_id)
    report = drafts.validate_draft(draft_id)
    if not report["ok"]:
        return {"ok": False, "errors": report["errors"], "warnings": report["warnings"],
                "hint": "fix the errors (via the owning specialist) and build again"}
    sc = _models_scenario()
    template = json.loads(paths.ROOT_CONFIG.read_text(encoding="utf-8"))
    number = d["scenario_number"] or next_scenario_number()
    probe = {"scenario_number": number, "scenario_name": d["scenario_name"]}
    folder = sc.scenario_folder_name(probe)
    arms = arms_for(d["overrides"])
    config = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "scenario_number": number,
        "scenario_name": d["scenario_name"],
        "description": d["description"],
        "changes": {"override_schema_version": catalog.OVERRIDE_SCHEMA_VERSION, "overrides": d["overrides"]},
        "digital_twin": {**template["digital_twin"], "run_name": "prepared", "seed": d["evaluation"]["seed"]},
        "meio": dict(template["meio"]),                      # whatever MEIO the config defines
        "artifacts": {**template.get("artifacts", {}), "scenario": f"output datasets/MEIO artifacts/{folder}"},
        "overrides": {"engine": "models/overrides", "output_root": paths.DEFAULT_OVERRIDES_ROOT,
                      **template.get("overrides", {})},
        "evaluation": {"baseline_scenario": d["evaluation"]["baseline_scenario"],
                       "baseline_run": d["evaluation"]["baseline_run"], "arms": arms},
        "details": {"owner": "Supply Planning", "hypothesis": d["hypothesis"], "request": d["request_text"],
                    "created_by": f"Agents draft {d['draft_id']}", "created_at": drafts.now(),
                    "notes": "Generated by the scenario agents; overrides apply only through models/overrides."},
    }
    out_dir = scenario_dir(folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = out_dir / "scenario_config.json"
    cfg_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    sc.load_config(cfg_path)                                     # the loader both models use
    sc.resolve_scenario_path(cfg_path, config, "artifacts", "scenario")

    d["scenario_number"], d["scenario_folder"] = number, folder
    drafts.save_draft(d)
    sha = config_sha256(cfg_path)
    card = render_card(config, folder, report, sha)
    (out_dir / "scenario_card.md").write_text(card, encoding="utf-8")
    approval = approval_status(folder)
    return {"ok": True, "scenario": folder, "config_path": paths.rel(cfg_path),
            "card_path": paths.rel(out_dir / "scenario_card.md"), "config_sha256": sha, "arms": arms,
            "approval": approval["state"], "card": card}


def _scope_text(scope: dict) -> str:
    if scope.get("all"):
        return "all"
    parts = []
    for k, v in scope.items():
        shown = ", ".join(v[:5]) + (f" (+{len(v) - 5})" if len(v) > 5 else "")
        parts.append(f"{k}={shown}")
    return "; ".join(parts)


def _change_text(o: dict) -> str:
    unit = {"days": " days", "units": " units", "units_per_week": " units/wk", "weeks_of_cover": " wks cover",
            "pct_annual": "%/yr", "usd_per_unit": " $/unit"}.get(o.get("unit"), "")
    v = o.get("value")
    return {"scale": f"x{v:g}", "add": f"{v:+g}{unit}", "set": f"= {v:g}{unit}", "floor": f">= {v:g}{unit}",
            "cap": f"<= {v:g}{unit}", "block": "blocked"}[o["operation"]]


def _impact_line(o: dict, p: dict) -> str:
    if "delta_units" in p:
        return (f"{p['delta_units']:+,} forecast units ({(p.get('delta_pct_of_scope') or 0):+.1%} of scope, "
                f"{(p.get('delta_pct_of_network_in_window') or 0):+.2%} of network demand in window); "
                f"{p['delta_list_revenue_usd']:+,} USD at list")
    if "approx_safety_stock_value_delta_usd" in p:
        return f"~{p['approx_safety_stock_value_delta_usd']:+,} USD safety stock ({p['pairs']} DC-SKU pairs)"
    if "value_before_usd" in p:
        return (f"opening stock {p['units_before']:,} -> {p['units_after']:,} units "
                f"({p['value_after_usd'] - p['value_before_usd']:+,} USD)")
    if "families" in p and o["measure"] == "supply.schedule_adherence":
        lost = sum(f.get("approx_lost_units_per_week", 0) for f in p["families"])
        return f"{len(p['families'])} line(s); about {lost:,} units/week not built"
    if "families" in p:
        tight = [f"{f['plant_id']}/{f['production_family']}" for f in p["families"]
                 if f.get("weeks_load_exceeds_capacity_after")]
        return (f"{len(p['families'])} line(s); demand-equivalent load exceeds the new capacity on "
                f"{tight or 'none (the cut may not bind)'}")
    if "lanes_matched" in p:
        return (f"{p['lanes_matched']} lane(s), {(p.get('share_of_network_demand') or 0):.1%} of network "
                f"demand moves over them")
    if "value_delta_usd" in p:
        return f"{p['value_delta_usd']:+,} USD at baseline policy ({p['pairs_changed']} pairs changed)"
    return p.get("effect") or p.get("note") or ""


def render_card(config: dict, folder: str, report: dict, sha: str) -> str:
    overrides = config["changes"]["overrides"]
    previews = report.get("impact", {}).get("per_override", {})
    ev = config["evaluation"]
    lines = [f"# {folder} -- scenario card", "",
             f"**Request:** {config['details']['request'] or '(none recorded)'}", "",
             f"**Hypothesis:** {config['details']['hypothesis'] or '(none stated)'}", "",
             f"**Compared against:** `{ev['baseline_scenario']}/{ev['baseline_run']}` "
             f"(seed {config['digital_twin']['seed']}; MEIO as defined in the config: "
             f"`{config['meio'].get('rolling_entrypoint')}`)", "",
             f"**Runs:** {', '.join(ev['arms'])} -- prepared = MEIO re-plans with the plan-side changes; "
             f"unprepared = today's policy meets the actual-side changes.", "",
             "## Overrides", "",
             "| ID | Measure | Change | Scope | Weeks | MEIO plans for it | Twin simulates it | Why |",
             "|---|---|---|---|---|---|---|---|"]
    for o in overrides:
        w = o.get("window")
        weeks = f"{w['start_period']}..{w['end_period']}" if w else "all"
        why = o["rationale"].replace("|", "/")
        lines.append(f"| {o['id']} | `{o['measure']}` | {_change_text(o)} | {_scope_text(o['scope'])} | {weeks} | "
                     f"{'yes' if 'plan' in o['applies_to'] else 'no'} | "
                     f"{'yes' if 'actual' in o['applies_to'] else 'no'} | {why} |")
    lines += ["", "## Size of each change (inputs, not outcomes)", ""]
    lines += [f"- **{o['id']}** {_impact_line(o, previews.get(o['id'], {}))}" for o in overrides]
    if report["warnings"] or report["info"]:
        lines += ["", "## Checks", ""]
        lines += [f"- WARNING ({w.get('override_id', 'set')}): {w['message']}" for w in report["warnings"]]
        lines += [f"- {i}" for i in report["info"]]
    ready = engine.readiness()
    lines += ["", "## Engine support", ""]
    if ready["ready_to_run"] and not engine.unsupported(o["measure"] for o in overrides):
        lines.append(f"Ready: engine {ready['engine_version']} applies every measure in this scenario.")
    else:
        lines += [f"- NOT READY: {m}" for m in ready["missing"]]
        gaps = engine.unsupported(o["measure"] for o in overrides)
        if gaps:
            lines.append(f"- Measures awaiting engine support: {', '.join(gaps)}")
    lines += ["", "## Approval", "",
              f"config sha256 `{sha}`", "",
              "Approving runs MEIO and the Digital Twin for this scenario (minutes of compute) and writes new "
              "scenario folders under `output datasets/`. To approve:", "",
              "```bash", f'python -m Agents.tools approve {folder} --by "<your name>"', "```", ""]
    return "\n".join(lines)


# --------------------------------------------------------------- approval --
def approve(folder: str, approved_by: str, allow_overwrite: bool = False, note: str = "") -> dict:
    cfg_path = scenario_dir(folder) / "scenario_config.json"
    if not cfg_path.exists():
        raise ValueError(f"no generated config for {folder}; build it first")
    if not approved_by.strip():
        raise ValueError("approved_by is required")
    record = {"scenario": folder, "config_sha256": config_sha256(cfg_path), "approved_by": approved_by.strip(),
              "approved_at": drafts.now(), "allow_overwrite": bool(allow_overwrite), "note": note}
    (scenario_dir(folder) / "approval.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def approval_status(folder: str) -> dict:
    base = scenario_dir(folder)
    cfg_path, rec_path = base / "scenario_config.json", base / "approval.json"
    if not cfg_path.exists():
        return {"state": "no_config"}
    if not rec_path.exists():
        return {"state": "awaiting_approval"}
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    if rec.get("config_sha256") != config_sha256(cfg_path):
        return {"state": "stale", "detail": "config changed after approval -- re-approval required", **rec}
    return {"state": "approved", **rec}


# -------------------------------------------------------------------- run --
def _outputs(folder: str) -> list[str]:
    candidates = [paths.PROJECTIONS_DIR / folder, paths.MEIO_OUTPUT_DIR / folder, paths.MEIO_ARTIFACTS_DIR / folder]
    return [paths.rel(p) for p in candidates if p.exists()]


def run_scenario(folder: str) -> dict:
    base = scenario_dir(folder)
    cfg_path = base / "scenario_config.json"
    if not cfg_path.exists():
        return {"ok": False, "error": f"no generated config for {folder}"}
    approval = approval_status(folder)
    if approval["state"] != "approved":
        return {"ok": False, "error": f"not approved ({approval['state']}); a person must run "
                                      f"`python -m Agents.tools approve {folder} --by <name>` after reading "
                                      f"{paths.rel(base / 'scenario_card.md')}"}
    config = json.loads(cfg_path.read_text(encoding="utf-8"))
    ready = engine.readiness()
    gaps = engine.unsupported(o["measure"] for o in config["changes"]["overrides"])
    if not ready["ready_to_run"] or gaps:
        return {"ok": False, "error": "the MEIO/Twin pipeline cannot apply this scenario yet",
                "missing": ready["missing"], "unsupported_measures": gaps}
    mismatch = _baseline_meio_mismatch(config)
    if mismatch:
        return {"ok": False, "error": mismatch}
    existing = _outputs(folder)
    if existing and not approval.get("allow_overwrite"):
        return {"ok": False, "error": f"outputs already exist: {existing}; re-approve with --allow-overwrite "
                                      f"to replace them"}
    status_path, log_path = base / "status.json", base / "run.log"
    _write_status(status_path, {"scenario": folder, "state": "queued", "queued_at": drafts.now(),
                                "arms": {a: {"state": "pending"} for a in config["evaluation"]["arms"]}})
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS} \
        if os.name == "nt" else {"start_new_session": True}
    with open(log_path, "w", encoding="utf-8") as log:
        subprocess.Popen([sys.executable, "-m", "Agents.tools", "run-job", folder], cwd=paths.REPO_ROOT,
                         stdout=log, stderr=subprocess.STDOUT, **flags)
    return {"ok": True, "started": True, "scenario": folder, "arms": config["evaluation"]["arms"],
            "log": paths.rel(log_path), "status": paths.rel(status_path),
            "note": "MEIO + Twin take minutes; poll get_run_status"}


def _baseline_meio_mismatch(config: dict) -> str | None:
    """The unprepared arm runs the baseline's policy and the prepared arm re-plans with the
    scenario's own `meio` section. Both must come from the same MEIO model and parameters,
    or prepared-vs-unprepared would measure a model change, not the re-plan."""
    ev = config["evaluation"]
    saved = paths.PROJECTIONS_DIR / ev["baseline_scenario"] / "scenario_config.json"
    if not saved.exists() or "unprepared" not in ev["arms"]:
        return None
    then = json.loads(saved.read_text(encoding="utf-8")).get("meio", {}).get("parameters")
    if then != config["meio"].get("parameters"):
        return (f"the baseline {ev['baseline_scenario']} was built with meio.parameters {then!r}, but this "
                f"scenario's config has {config['meio'].get('parameters')!r}; rebuild the scenario from the "
                f"current config (build_scenario_config) and have it approved again")
    return None


def _write_status(path, status: dict) -> None:
    path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")


def baseline_policy_path(config: dict):
    """The policy the baseline run executed -- what 'unprepared' keeps using."""
    ev = config["evaluation"]
    sc = _models_scenario()
    meio_root = paths.REPO_ROOT / config["meio"].get("output_root", "output datasets/MEIO")
    filename = config["meio"].get("policy_filename", "meio_policy_rolling.csv")
    found = sorted((meio_root / ev["baseline_scenario"]).rglob(filename), key=lambda p: p.stat().st_mtime)
    if found:
        return found[-1]
    manifest = paths.PROJECTIONS_DIR / ev["baseline_scenario"] / ev["baseline_run"] / "run_manifest.json"
    if manifest.exists():
        source = json.loads(manifest.read_text(encoding="utf-8")).get("policy_override")
        if source:
            return paths.REPO_ROOT / source if not os.path.isabs(source) else source
    return sc.resolve_path(paths.ROOT_CONFIG, config, "digital_twin", "policy")


def run_job(folder: str) -> int:
    """Executed in a detached process by run_scenario. Prepared runs first because the
    MEIO-then-Twin runner clears the scenario's projection folder before it starts."""
    base = scenario_dir(folder)
    cfg_path, status_path = base / "scenario_config.json", base / "status.json"
    config = json.loads(cfg_path.read_text(encoding="utf-8"))
    approval = approval_status(folder)
    status = {"scenario": folder, "state": "running", "started_at": drafts.now(), "arms": {}}
    if approval["state"] != "approved":
        status.update(state="refused", detail=approval)
        _write_status(status_path, status)
        return 2
    order = [a for a in ("prepared", "unprepared") if a in config["evaluation"]["arms"]]
    twin_entry = paths.REPO_ROOT / config["digital_twin"]["entrypoint"]
    rc = 0
    for arm in order:
        status["arms"][arm] = {"state": "running", "started_at": drafts.now()}
        _write_status(status_path, status)
        if arm == "prepared":
            cmd = [sys.executable, str(paths.REPO_ROOT / "run_meio_then_twin.py"), "--scenario-config", str(cfg_path)]
            if approval.get("allow_overwrite"):
                cmd.append("--yes")
        else:
            cmd = [sys.executable, str(twin_entry), "--scenario-config", str(cfg_path),
                   "--policy", str(baseline_policy_path(config)), "--run-name", "unprepared",
                   "--mode", "forward", "--seed", str(config["digital_twin"]["seed"])]
        print(f"=== {arm}: {' '.join(cmd)}", flush=True)
        rc = subprocess.run(cmd, cwd=paths.REPO_ROOT).returncode
        status["arms"][arm].update(state="succeeded" if rc == 0 else "failed", returncode=rc,
                                   finished_at=drafts.now(),
                                   output=paths.rel(paths.PROJECTIONS_DIR / folder / arm))
        _write_status(status_path, status)
        if rc != 0:
            break
    status.update(state="succeeded" if rc == 0 else "failed", finished_at=drafts.now())
    _write_status(status_path, status)
    return rc


def run_status(folder: str) -> dict:
    base = scenario_dir(folder)
    status_path, log_path = base / "status.json", base / "run.log"
    out = {"scenario": folder, "approval": approval_status(folder)["state"]}
    out.update(json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {"state": "not_started"})
    if log_path.exists():
        out["log_tail"] = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
    return out


def preview_draft(draft_id: str, override_id: str | None = None) -> dict:
    d = drafts.load_draft(draft_id)
    chosen = [o for o in d["overrides"] if override_id in (None, o["id"])]
    if override_id and not chosen:
        raise ValueError(f"no override {override_id} in {draft_id}")
    return impact.preview_all(chosen)
