"""
Read scenario results back for the analyst agent: headline KPIs by arm with the
deltas planners ask about, service breakdowns by business dimension, policy
(stocking) changes, and the engine's own record of what it applied.

The decomposition the analyst reports comes straight from the arms:
  impact if unprepared  = unprepared - baseline
  value of re-planning  = prepared   - unprepared
  net position          = prepared   - baseline
  value of reacting     = reactive   - unprepared (MEIO re-plans weekly from simulated history)
    of which, scenario  = (reactive - unprepared) - (reactive control - baseline); the control
                          (counterfactual_reactive_no_scenario) is the same weekly re-planning
                          with no scenario, so subtracting it removes the general effect
Runs are single-seed; differences of a few tenths of a point in fill rate are
within seed-to-seed noise (the MEIO multi-seed studies saw ~0.1-0.3pp spreads).
"""
from __future__ import annotations

import json

import pandas as pd

from . import context, drafts, paths

KPI_ORDER = ["customer_fill_rate", "customer_fill_rate_cdc", "customer_fill_rate_rdc", "pct_demand_weeks_in_full",
             "internal_replen_fill_rate", "customer_demand_units", "lost_sales_units", "avg_inventory_value_usd",
             "safety_stock_value_usd", "inventory_turns", "days_of_supply", "holding_cost_usd", "stockout_cost_usd",
             "transport_cost_usd", "total_landed_cost_usd", "pct_weeks_at_zero_stock"]
RATE_KPIS = {"customer_fill_rate", "customer_fill_rate_cdc", "customer_fill_rate_rdc", "pct_demand_weeks_in_full",
             "internal_replen_fill_rate", "pct_weeks_at_zero_stock"}
SKU_DIMS = ("segment", "category", "subcategory", "brand", "cluster", "archetype", "price_tier", "production_family")
NODE_DIMS = ("dc_id", "region", "echelon")


def run_dir(scenario: str, run: str):
    path = paths.PROJECTIONS_DIR / scenario / run
    if not (path / "run_kpis.json").exists():
        runs = sorted(p.name for p in (paths.PROJECTIONS_DIR / scenario).glob("*") if (p / "run_kpis.json").exists()) \
            if (paths.PROJECTIONS_DIR / scenario).is_dir() else []
        raise ValueError(f"no results for {scenario}/{run}; runs on disk: {runs or 'none'}")
    return path


def kpis(scenario: str, run: str) -> dict:
    d = run_dir(scenario, run)
    k = json.loads((d / "run_kpis.json").read_text(encoding="utf-8"))
    nodes = pd.read_csv(d / "service_by_node.csv")
    k["lost_sales_units"] = float(nodes[nodes.node_type == "CUSTOMER_DC"].unmet.sum())
    svc = pd.read_csv(d / "service_summary.csv")
    cost = svc.sku_id.map(context.skus().set_index("sku_id").unit_cost_usd)
    annual_cogs = float((svc.served_from_stock * cost).sum()) * 52.0 / max(int(k.get("horizon_weeks", 52)), 1)
    inv = k.get("avg_inventory_value_usd") or 0.0
    k["inventory_turns"] = round(annual_cogs / inv, 2) if inv else None
    k["days_of_supply"] = round(365.0 / k["inventory_turns"], 1) if k["inventory_turns"] else None
    return k


def _baseline_of(scenario: str, baseline_scenario: str | None, baseline_run: str | None) -> tuple[str, str]:
    if baseline_scenario:
        return baseline_scenario, baseline_run or "meio_policy"
    cfg_path = paths.PROJECTIONS_DIR / scenario / "scenario_config.json"
    if cfg_path.exists():
        ev = json.loads(cfg_path.read_text(encoding="utf-8")).get("evaluation", {})
        if ev.get("baseline_scenario"):
            return ev["baseline_scenario"], ev.get("baseline_run", "meio_policy")
    base = drafts.default_baseline()
    return base["scenario"], base["run"]


def compare_runs(scenario: str, runs: list[str] | None = None, baseline_scenario: str | None = None,
                 baseline_run: str | None = None) -> dict:
    b_scn, b_run = _baseline_of(scenario, baseline_scenario, baseline_run)
    folder = paths.PROJECTIONS_DIR / scenario
    if runs is None:
        runs = [a for a in ("unprepared", "prepared", "reactive") if (folder / a / "run_kpis.json").exists()]
        runs += sorted(p.name for p in folder.glob("*") if (p / "run_kpis.json").exists() and p.name not in runs) \
            if folder.is_dir() else []
    if not runs:
        raise ValueError(f"{scenario} has no finished runs yet")
    base = kpis(b_scn, b_run)
    arms = {r: kpis(scenario, r) for r in runs}
    control_dir = paths.PROJECTIONS_DIR / b_scn / "counterfactual_reactive_no_scenario"
    control = kpis(b_scn, "counterfactual_reactive_no_scenario") if (control_dir / "run_kpis.json").exists() \
        and "reactive" in arms else None
    table = []
    for key in KPI_ORDER:
        if key not in base:
            continue
        row = {"kpi": key, "baseline": base[key]}
        for r, k in arms.items():
            row[r] = k.get(key)
            if isinstance(k.get(key), (int, float)) and isinstance(base[key], (int, float)):
                delta = k[key] - base[key]
                row[f"{r}_vs_baseline"] = round(delta * 100, 2) if key in RATE_KPIS else round(delta, 2)
        scale = 100 if key in RATE_KPIS else 1
        if "prepared" in arms and "unprepared" in arms and isinstance(arms["prepared"].get(key), (int, float)):
            delta = arms["prepared"][key] - arms["unprepared"][key]
            row["replan_value"] = round(delta * scale, 2)
        if "reactive" in arms and "unprepared" in arms and isinstance(arms["reactive"].get(key), (int, float)):
            delta = arms["reactive"][key] - arms["unprepared"][key]
            row["react_value"] = round(delta * scale, 2)
            if control and isinstance(control.get(key), (int, float)) and isinstance(base[key], (int, float)):
                row["react_value_normal_year"] = round((control[key] - base[key]) * scale, 2)
                row["react_value_scenario"] = round(row["react_value"] - row["react_value_normal_year"], 2)
        table.append(row)
    return {"scenario": scenario, "baseline": f"{b_scn}/{b_run}", "runs": runs, "kpis": table,
            "reactive_control": f"{b_scn}/counterfactual_reactive_no_scenario" if control else None,
            "units": "rate deltas are percentage points; money in USD; units are whole units",
            "caveat": "single seed -- fill-rate differences under ~0.3pp are within seed noise"}


def _service_frame(scenario: str, run: str, by: str) -> pd.DataFrame:
    d = run_dir(scenario, run)
    if by in SKU_DIMS:
        svc = pd.read_csv(d / "service_summary.csv").drop(columns=["category"], errors="ignore")
        svc = svc.merge(context.skus()[["sku_id", by]], on="sku_id", how="left")
        g = svc.groupby(by).agg(demand=("total_demand", "sum"), unmet=("unmet", "sum"))
    elif by in NODE_DIMS:
        svc = pd.read_csv(d / "service_by_node.csv").merge(context.nodes()[["dc_id", "region", "echelon"]],
                                                           on="dc_id", how="left")
        g = svc.groupby(by).agg(demand=("total_demand", "sum"), unmet=("unmet", "sum"),
                                replen_demand=("replen_demand", "sum"), replen_unmet=("replen_unmet", "sum"))
        g["internal_fill"] = (1 - g.replen_unmet / g.replen_demand.where(g.replen_demand > 0)).round(4)
        g = g.drop(columns=["replen_demand", "replen_unmet"])
    else:
        raise ValueError(f"by must be one of {SKU_DIMS + NODE_DIMS}")
    g["customer_fill"] = (1 - g.unmet / g.demand.where(g.demand > 0)).round(4)
    return g


def service_breakdown(scenario: str, run: str, by: str, baseline_scenario: str | None = None,
                      baseline_run: str | None = None, top: int = 15) -> dict:
    b_scn, b_run = _baseline_of(scenario, baseline_scenario, baseline_run)
    cur = _service_frame(scenario, run, by)
    base = _service_frame(b_scn, b_run, by)
    out = cur.join(base[["customer_fill"]].rename(columns={"customer_fill": "baseline_fill"}), how="left")
    out["delta_pp"] = ((out.customer_fill - out.baseline_fill) * 100).round(2)
    out = out.sort_values("delta_pp").reset_index()
    return {"scenario": f"{scenario}/{run}", "baseline": f"{b_scn}/{b_run}", "by": by,
            "rows": out.head(top).to_dict("records"), "rows_total": int(len(out)),
            "note": "sorted worst delta first; customer_fill = 1 - unmet / demand"}


def scope_service(scenario: str, override_id: str, baseline_scenario: str | None = None,
                  baseline_run: str | None = None) -> dict:
    """Customer fill inside one override's own (dc, sku) scope and window, for every arm and
    the baseline. A local event is a small share of network demand, so the headline dilutes
    it; this is the event's own result. Scope is resolved with the engine's matcher."""
    cfg_path = paths.overrides_root() / scenario / "scenario_config.json"
    if not cfg_path.exists():
        raise ValueError(f"no scenario config at {cfg_path}")
    config = json.loads(cfg_path.read_text(encoding="utf-8"))
    o = next((x for x in config["changes"].get("overrides", []) if x["id"] == override_id), None)
    if o is None:
        raise ValueError(f"{override_id} is not in {scenario}")
    rows = context.match(o["measure"], o["scope"])
    if not {"dc_id", "sku_id"} <= set(rows.columns):
        return {"ok": False, "error": f"{o['measure']} is scoped to lanes or plants, not DC-SKU pairs; "
                                      f"use service_breakdown by region or echelon instead"}
    pairs = rows[["dc_id", "sku_id"]].drop_duplicates()
    periods = context.window_periods(o.get("window"))
    b_scn, b_run = _baseline_of(scenario, baseline_scenario, baseline_run)
    names = list(dict.fromkeys(list(config["evaluation"]["arms"]) + ["reactive"]))
    arms = [("baseline", b_scn, b_run)] + [(a, scenario, a) for a in names
                                            if (paths.PROJECTIONS_DIR / scenario / a / "run_kpis.json").exists()]
    out = {}
    for label, scn, run in arms:
        inv = pd.read_parquet(run_dir(scn, run) / "inventory.parquet",
                          columns=["period", "dc_id", "sku_id", "demand_customer_qty", "unmet_customer_qty"])
        inv = inv[inv.period.isin(periods)].merge(pairs, on=["dc_id", "sku_id"])
        demand, unmet = float(inv.demand_customer_qty.sum()), float(inv.unmet_customer_qty.sum())
        out[label] = {"customer_demand": round(demand), "unmet": round(unmet),
                      "customer_fill": round(1 - unmet / demand, 4) if demand else None}
    for label in out:
        if label != "baseline" and out[label]["customer_fill"] is not None and out["baseline"]["customer_fill"]:
            out[label]["vs_baseline_pp"] = round((out[label]["customer_fill"] - out["baseline"]["customer_fill"]) * 100, 2)
    if "prepared" in out and "unprepared" in out and out["prepared"]["customer_fill"] is not None:
        out["replan_value_pp"] = round((out["prepared"]["customer_fill"] - out["unprepared"]["customer_fill"]) * 100, 2)
    if "reactive" in out and "unprepared" in out and out["reactive"]["customer_fill"] is not None:
        out["react_value_pp"] = round((out["reactive"]["customer_fill"] - out["unprepared"]["customer_fill"]) * 100, 2)
    return {"scenario": scenario, "override_id": override_id, "measure": o["measure"],
            "pairs": int(len(pairs)), "window": f"{periods[0]}..{periods[-1]}", "arms": out,
            "basis": "customer demand and unmet units at the scoped DC-SKU pairs in the window"}


def _policy_frame(scenario: str, periods: list[str] | None = None) -> pd.DataFrame:
    rolling = sorted((paths.MEIO_OUTPUT_DIR / scenario).rglob("meio_policy_rolling.parquet"),
                     key=lambda p: p.stat().st_mtime)
    if rolling:
        pol = pd.read_parquet(rolling[-1])
        if periods:
            pol = pol[pol.period.isin(periods)]
        return pol.groupby(["dc_id", "sku_id"], as_index=False)[
            ["safety_stock", "reorder_point", "order_up_to_level"]].mean()
    snapshots = sorted((paths.PROJECTIONS_DIR / scenario).glob("*/policy.parquet"))
    if snapshots:
        return pd.read_parquet(snapshots[0])
    raise ValueError(f"no MEIO policy or run policy found for {scenario}")


def policy_diff(scenario: str, baseline_scenario: str | None = None, by: str = "echelon",
                start_period: str | None = None, end_period: str | None = None) -> dict:
    """A window (start_period..end_period) compares the policies over just those weeks; a
    four-week event averaged over the whole year shows about a thirteenth of its size."""
    b_scn, _ = _baseline_of(scenario, baseline_scenario, None)
    if by not in SKU_DIMS + NODE_DIMS:
        raise ValueError(f"by must be one of {SKU_DIMS + NODE_DIMS}")
    periods = None
    if start_period or end_period:
        weeks = context.forward_periods()
        periods = context.window_periods({"start_period": start_period or weeks[0],
                                          "end_period": end_period or weeks[-1]})
    cur, base = _policy_frame(scenario, periods), _policy_frame(b_scn, periods)
    m = cur.merge(base, on=["dc_id", "sku_id"], how="outer", suffixes=("", "_base")).fillna(0.0)
    m = m.merge(context.skus()[["sku_id", "unit_cost_usd", *SKU_DIMS]], on="sku_id", how="left") \
        .merge(context.nodes()[["dc_id", "region", "echelon"]], on="dc_id", how="left")
    m["ss_usd"], m["ss_usd_base"] = m.safety_stock * m.unit_cost_usd, m.safety_stock_base * m.unit_cost_usd
    g = m.groupby(by).agg(safety_stock=("safety_stock", "sum"), safety_stock_base=("safety_stock_base", "sum"),
                          ss_usd=("ss_usd", "sum"), ss_usd_base=("ss_usd_base", "sum"),
                          order_up_to=("order_up_to_level", "sum"), order_up_to_base=("order_up_to_level_base", "sum"))
    g["ss_usd_delta"] = (g.ss_usd - g.ss_usd_base).round(0)
    g["ss_pct_delta"] = ((g.safety_stock / g.safety_stock_base.where(g.safety_stock_base > 0) - 1) * 100).round(1)
    return {"scenario": scenario, "baseline": b_scn, "by": by,
            "rows": g.round(1).sort_values("ss_usd_delta", ascending=False).reset_index().to_dict("records"),
            "window": f"{periods[0]}..{periods[-1]}" if periods else "all weeks",
            "basis": "time-varying MEIO policies averaged over the window's weeks, per DC-SKU, then summed"}


def application_receipts(scenario: str, run: str) -> dict:
    """What the engine says it applied -- the analyst's proof that a scenario result
    reflects the overrides, not a silent no-op. The engine writes one receipt per model
    run into the scenario's override folder: receipts/meio.json and receipts/twin_<run>.json
    (IMPLEMENTATION_PLAN.md 5.1); the Twin's run_manifest build notes are the fallback."""
    folder = paths.overrides_root() / scenario / "receipts"

    def read(name):
        path = folder / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    manifest = json.loads((run_dir(scenario, run) / "run_manifest.json").read_text(encoding="utf-8"))
    notes = manifest.get("build_notes", {})
    out = {"scenario": f"{scenario}/{run}", "receipts_folder": paths.rel(folder),
           "twin": read(f"twin_{run}.json") or notes.get("overrides_applied") or notes.get("scenario_applied"),
           "meio": read("meio.json")}
    if out["twin"] is None:
        out["warning"] = ("the run recorded no override application; treat its results as baseline physics "
                          "(engine predates Phase 1, or the scenario had no actual-side overrides)")
    return out
