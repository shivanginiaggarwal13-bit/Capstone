"""
cleanup_research_artifacts.py -- Summarise and prune bulk artifacts after run_all
==================================================================================

Called automatically as step 10 of run_all.py once all nine research steps finish.
Also safe to run standalone:

    python cleanup_research_artifacts.py              # summary + interactive prompt
    python cleanup_research_artifacts.py --dry-run    # show plan, write summary, no deletes
    python cleanup_research_artifacts.py --yes        # skip confirmation (CI / unattended)
    python cleanup_research_artifacts.py --keep-logs  # exclude run_logs/ from deletion

What is deleted vs. kept
------------------------
Both weekly tools (meio_optimizer_weekly.py and roll_forward_policy.py) call
meio_parameters.resolve(), which reads four evidence CSV patterns from
<artifacts.scenario>/results/.  They also read all files under
output datasets/overrides/<scenario>/ (scenario config copy + override receipts
from step 8), and load their demand, lead-time and cost tables from
<meio.data_dir> (the raw "input datasets/" folder, never touched here).

KEPT -- everything meio_optimizer_weekly and roll_forward_policy actually need:
  <artifacts.scenario>/results/               -- all files (evidence CSVs + charts)
      netting_discount_multiseed*.csv         ← meio_parameters reads this
      multiseed_alpha_*.csv                   ← meio_parameters reads this
      segment_alpha_real_data_multiseed.csv   ← meio_parameters reads this
      service_level_real_data_multiseed.csv   ← meio_parameters reads this
      (all other results/ files kept too: small, not bulky)
  <artifacts.scenario>/scenario_config.json   -- scenario config copy
  output datasets/overrides/<scenario>/       -- override receipts (step 8)
  meio_weekly_history.csv                     -- appended on every weekly run
  chart_ss_trend.png                          -- regenerated on every weekly run

DELETED (research-only bulk, NOT read by the weekly tools):
  <artifacts.scenario>/validation_output/     -- walk-forward policies + twin run folders
      policy_*.parquet                        (research policies, not the pipeline's)
      actual/, csv2_headline/, sweep_*/,
      ms_*/, sreal_*/, svc_*/, netting_*/,
      segment_alpha_partial/, service_level_partial/  (twin run detail tables)
  <twin_output_root>/projections/<scenario>/  -- synthetic-basis twin runs
      *_A_*/, *_B_*/, *_C_*/                 (scenario_test arms)
      <cluster>*/                             (segment_alpha_explore twin runs)
      synthetic_baseline/                     (forward-mode baseline)
  run_logs/                                   -- step logs from run_all

Before any file is touched, a JSON summary (research_summary.json) is written to
<artifacts.scenario>/.  It records which files are being kept, which deleted, and
a compact statistical digest of every deleted Parquet/CSV file so the numbers are
not lost.
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Locate repo root (same logic as runtime.py: walk up for scenario_config.json
# or .git; fall back to the script's own parent).
# ---------------------------------------------------------------------------
def _find_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "scenario_config.json").exists() or (candidate / ".git").exists():
            return candidate
    return here


REPO_ROOT = _find_repo_root()


# ---------------------------------------------------------------------------
# Resolve paths from scenario_config.json exactly as the weekly tools do,
# without importing `scenario` (which is not importable without the full env).
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_path(config_path: Path, config: dict, *keys) -> Path | None:
    """Walk config[keys[0]][keys[1]]... and resolve relative to config_path.parent."""
    node = config
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return None
        node = node[k]
    if not isinstance(node, str):
        return None
    p = Path(node)
    return p if p.is_absolute() else (config_path.parent / p).resolve()


def _scenario_folder_name(config: dict) -> str:
    """Replicate scenario.scenario_folder_name(): SCENARIO_NNNN_Name."""
    num = str(config.get("scenario_number", "0000")).zfill(4)
    name = str(config.get("scenario_name", "")).replace(" ", "_")
    return f"SCENARIO_{num}_{name}" if name else f"SCENARIO_{num}"


def _resolve_paths(config_path: Path) -> dict:
    """Return all relevant directories, resolved from scenario_config.json."""
    config = _load_json(config_path)
    scenario_name = _scenario_folder_name(config)

    # artifacts.scenario  -- where results/, validation_output/, scenario_config.json live
    artifacts_scenario = _resolve_path(config_path, config, "artifacts", "scenario")
    if artifacts_scenario is None:
        # fall back: output datasets/MEIO artifacts/<scenario>
        artifacts_scenario = REPO_ROOT / "output datasets" / "MEIO artifacts" / scenario_name

    # artifacts.twin_output_root  -- where projections/<scenario>/ lives
    twin_output_root = _resolve_path(config_path, config, "artifacts", "twin_output_root")
    if twin_output_root is None:
        twin_output_root = REPO_ROOT / "output datasets"

    # meio.output_root  -- where roll_forward_policy writes rolling_2026/
    meio_output_root = _resolve_path(config_path, config, "meio", "output_root")
    if meio_output_root is None:
        meio_output_root = REPO_ROOT / "output datasets" / "MEIO"

    # overrides dir: output datasets/overrides/<scenario>
    overrides_dir = REPO_ROOT / "output datasets" / "overrides" / scenario_name

    twin_proj_dir = twin_output_root / "projections" / scenario_name

    return {
        "scenario_name": scenario_name,
        "artifacts_scenario": artifacts_scenario,
        "twin_output_root": twin_output_root,
        "meio_output_root": meio_output_root,
        "overrides_dir": overrides_dir,
        "twin_proj_dir": twin_proj_dir,
        "validation_output": artifacts_scenario / "validation_output",
        "results_dir": artifacts_scenario / "results",
    }


# ---------------------------------------------------------------------------
# Classify every path inside a directory as KEEP or DELETE
# ---------------------------------------------------------------------------

# Everything under results/ is kept (evidence CSVs + charts; all small).
# These are the four patterns meio_parameters.py actually reads:
EVIDENCE_PATTERNS = (
    "netting_discount_multiseed*.csv",
    "multiseed_alpha_*.csv",
    "segment_alpha_real_data_multiseed.csv",
    "service_level_real_data_multiseed.csv",
)


def _collect_delete_targets(paths: dict) -> list[dict]:
    """
    Return a list of {path, is_dir, label} for every research-only item.
    Nothing under results/ or overrides/ is ever included.
    """
    targets = []

    def _add(p: Path, *, is_dir: bool, label: str = None):
        if p.exists():
            targets.append({
                "path": p,
                "is_dir": is_dir,
                "label": label or str(p.relative_to(REPO_ROOT)),
            })

    # 1. validation_output/ -- walk-forward policies + all twin history run folders
    #    Everything in here is research-only.  The subdirectories are named after
    #    twin runs: actual/, csv2_headline/, sweep_*/, ms_*/, sreal_*/,
    #    netting_*/, svc_*/, {cluster}_*/, synthetic_baseline/, *_A_*/, *_B_*/, *_C_*/
    #    Also contains segment_alpha_partial/ and service_level_partial/ scratch dirs.
    vo = paths["validation_output"]
    if vo.exists() and vo.is_dir():
        _add(vo, is_dir=True)

    # 2. twin_output_root/projections/<scenario>/ -- synthetic-basis forward runs
    #    segment_alpha_explore writes here (TWIN_OUT_ROOT), not to validation_output.
    #    scenario_test's three arms also land here.
    tp = paths["twin_proj_dir"]
    if tp.exists() and tp.is_dir():
        _add(tp, is_dir=True)

    # 3. run_logs/ -- step logs produced by run_all (next to this script)
    log_dir = Path(__file__).resolve().parent / "run_logs"
    if log_dir.exists() and log_dir.is_dir():
        _add(log_dir, is_dir=True)

    return targets


# ---------------------------------------------------------------------------
# Statistical digest helpers
# ---------------------------------------------------------------------------

def _human(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1_024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1_024
    return f"{n_bytes:.1f} TB"


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _dir_count(path: Path) -> int:
    return sum(1 for f in path.rglob("*") if f.is_file())


def _parquet_digest(path: Path) -> dict:
    try:
        try:
            import polars as pl
            df = pl.read_parquet(path)
            numeric = [c for c in df.columns
                       if df[c].dtype in (pl.Float32, pl.Float64, pl.Int32, pl.Int64)]
            return {
                "rows": len(df),
                "cols": len(df.columns),
                "columns": df.columns,
                "numeric_summary": {
                    c: {"mean": round(df[c].mean() or 0, 6),
                        "std":  round(df[c].std() or 0, 6),
                        "min":  round(df[c].min() or 0, 6),
                        "max":  round(df[c].max() or 0, 6)}
                    for c in numeric[:20]
                },
            }
        except ImportError:
            import pandas as pd
            df = pd.read_parquet(path)
            return {"rows": len(df), "cols": len(df.columns), "columns": list(df.columns),
                    "numeric_summary": json.loads(df.describe().to_json())}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _csv_digest(path: Path) -> dict:
    try:
        try:
            import polars as pl
            df = pl.read_csv(path)
        except ImportError:
            import pandas as pd
            df = pd.read_csv(path)
        return {"rows": len(df), "cols": len(df.columns), "columns": list(df.columns)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Build the summary document
# ---------------------------------------------------------------------------

def _list_kept(paths: dict) -> list[dict]:
    """Every file under results/ and overrides/ that is being kept."""
    kept = []
    for d in (paths["results_dir"], paths["overrides_dir"]):
        if d.exists():
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    kept.append({
                        "file": str(f.relative_to(REPO_ROOT)),
                        "size_bytes": f.stat().st_size,
                        "size_human": _human(f.stat().st_size),
                        "weekly_tool_reads": any(
                            f.match(pat) for pat in EVIDENCE_PATTERNS
                        ) if d == paths["results_dir"] else True,
                    })
    # scenario_config.json copy
    sc = paths["artifacts_scenario"] / "scenario_config.json"
    if sc.exists():
        kept.append({
            "file": str(sc.relative_to(REPO_ROOT)),
            "size_bytes": sc.stat().st_size,
            "size_human": _human(sc.stat().st_size),
            "weekly_tool_reads": True,
        })
    return kept


def _group_entry(t: dict) -> dict:
    p: Path = t["path"]
    entry: dict = {"label": t["label"]}
    if t["is_dir"]:
        entry["type"] = "directory"
        n_files = _dir_count(p)
        n_bytes = _dir_bytes(p)
        entry["file_count"] = n_files
        entry["size_bytes"] = n_bytes
        entry["size_human"] = _human(n_bytes)
        # sample digests: up to 5 parquet + 5 csv files from inside the dir
        digests = []
        for child in sorted(p.rglob("*.parquet"))[:5]:
            digests.append({"file": str(child.relative_to(p)), **_parquet_digest(child)})
        for child in sorted(p.rglob("*.csv"))[:5]:
            digests.append({"file": str(child.relative_to(p)), **_csv_digest(child)})
        if digests:
            entry["sample_digests"] = digests
    else:
        entry["type"] = "file"
        entry["size_bytes"] = p.stat().st_size
        entry["size_human"] = _human(p.stat().st_size)
        sfx = p.suffix.lower()
        if sfx == ".parquet":
            entry["digest"] = _parquet_digest(p)
        elif sfx == ".csv":
            entry["digest"] = _csv_digest(p)
    return entry


def build_summary(paths: dict, targets: list[dict]) -> dict:
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scenario_name": paths["scenario_name"],
        "artifacts_scenario": str(paths["artifacts_scenario"].relative_to(REPO_ROOT)
                                  if paths["artifacts_scenario"].is_relative_to(REPO_ROOT)
                                  else paths["artifacts_scenario"]),
        "weekly_tool_inputs_kept": _list_kept(paths),
        "deleted_groups": [],
    }
    for t in targets:
        if t["path"].exists():
            summary["deleted_groups"].append(_group_entry(t))
    total = sum(g.get("size_bytes", 0) for g in summary["deleted_groups"])
    summary["total_deleted_bytes"] = total
    summary["total_deleted_human"] = _human(total)
    return summary


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def _confirm(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [yes/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans == "yes"


def _print(log, msg: str):
    print(msg, flush=True)
    if log is not None:
        try:
            log(msg)
        except Exception:  # noqa: BLE001
            pass


def run(ctx=None, log=None, *, dry_run: bool = False, yes: bool = False, keep_logs: bool = False):
    """
    Entry point for run_all._driver (step 10) and standalone CLI.
    ctx and log match the other steps' signature; ctx is unused here.
    """
    # ------------------------------------------------------------------
    # 1. Load scenario_config.json and resolve all relevant paths
    # ------------------------------------------------------------------
    config_path = REPO_ROOT / "scenario_config.json"
    if not config_path.exists():
        _print(log, f"[cleanup] scenario_config.json not found under {REPO_ROOT} -- nothing to do.")
        return

    try:
        paths = _resolve_paths(config_path)
    except Exception as exc:  # noqa: BLE001
        _print(log, f"[cleanup] Could not resolve paths from scenario_config.json: {exc}")
        return

    _print(log, f"[cleanup] Scenario: {paths['scenario_name']}")
    _print(log, f"[cleanup] Artifacts: {paths['artifacts_scenario']}")

    # ------------------------------------------------------------------
    # 2. Collect deletion targets
    # ------------------------------------------------------------------
    targets = _collect_delete_targets(paths)
    if keep_logs:
        targets = [t for t in targets
                   if t["path"] != Path(__file__).resolve().parent / "run_logs"]

    if not targets:
        _print(log, "[cleanup] No research artifacts found to delete -- nothing to do.")
        return

    # ------------------------------------------------------------------
    # 3. Write the summary BEFORE any deletion
    # ------------------------------------------------------------------
    _print(log, "[cleanup] Reading file stats and building summary...")
    summary = build_summary(paths, targets)
    summary_path = paths["artifacts_scenario"] / "research_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _print(log, f"[cleanup] Summary written -> {summary_path.relative_to(REPO_ROOT)}")
    _print(log, f"[cleanup] Total reclaimable: {summary['total_deleted_human']}")

    # ------------------------------------------------------------------
    # 4. Print the plan
    # ------------------------------------------------------------------
    _print(log, "\n[cleanup] Will DELETE (research-only, not read by run_weekly_tools):")
    for g in summary["deleted_groups"]:
        _print(log, f"    {g.get('size_human', '?'):>9}   {g['label']}")
    _print(log, f"    {'─' * 52}")
    _print(log, f"    {summary['total_deleted_human']:>9}   total")

    _print(log, "\n[cleanup] Will KEEP (read by meio_optimizer_weekly / roll_forward_policy):")
    evidence_kept = [k for k in summary["weekly_tool_inputs_kept"] if k["weekly_tool_reads"]]
    other_kept = [k for k in summary["weekly_tool_inputs_kept"] if not k["weekly_tool_reads"]]
    for k in evidence_kept:
        _print(log, f"    {k['size_human']:>9}   {k['file']}  ← weekly tool reads this")
    for k in other_kept:
        _print(log, f"    {k['size_human']:>9}   {k['file']}")

    if dry_run:
        _print(log, "\n[cleanup] --dry-run: no files deleted. Summary JSON preserved.")
        return

    # ------------------------------------------------------------------
    # 5. Confirm
    # ------------------------------------------------------------------
    if not yes:
        _print(log, "")
        confirmed = _confirm(
            f"[cleanup] Permanently delete {len(targets)} item(s) "
            f"({summary['total_deleted_human']}) listed above?"
        )
        if not confirmed:
            _print(log, "[cleanup] Aborted -- no files deleted. Summary JSON was still written.")
            return

    # ------------------------------------------------------------------
    # 6. Delete
    # ------------------------------------------------------------------
    freed = 0
    errors = []
    for t in targets:
        p: Path = t["path"]
        if not p.exists():
            continue
        try:
            if t["is_dir"]:
                sz = _dir_bytes(p)
                shutil.rmtree(p)
            else:
                sz = p.stat().st_size
                p.unlink()
            freed += sz
            _print(log, f"[cleanup] Deleted: {t['label']}")
        except OSError as exc:
            errors.append((t["label"], str(exc)))
            _print(log, f"[cleanup] ERROR deleting {t['label']}: {exc}")

    _print(log, f"\n[cleanup] Freed {_human(freed)}.")
    if errors:
        _print(log, f"[cleanup] {len(errors)} deletion error(s) -- see above for details.")
    else:
        _print(log, "[cleanup] Done. Evidence and override receipts preserved.")
        _print(log, f"          Summary: {summary_path.relative_to(REPO_ROOT)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Show the deletion plan and write the summary JSON, but do not delete anything.")
    ap.add_argument("--yes", action="store_true",
                    help="Skip the confirmation prompt and delete immediately. "
                         "Use in non-interactive / CI contexts.")
    ap.add_argument("--keep-logs", action="store_true",
                    help="Exclude run_logs/ from the deletion set.")
    args = ap.parse_args()
    run(dry_run=args.dry_run, yes=args.yes, keep_logs=args.keep_logs)


if __name__ == "__main__":
    main()