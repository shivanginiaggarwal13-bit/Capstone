"""Supply Brain -- MEIO Optimiser front end (Streamlit build of supply_brain_meio_mockup.html).

Every button in the mockup is wired to a real resource in this repository, or is shown
as "not mapped" with the reason. See BUTTON_MAPPING.md for the full table.

    python -m streamlit run App/streamlit_app.py

Safety: nothing here overwrites canonical outputs unless you tick the matching box.
Segmentation and Simulation launches write to App/runs/ (a sandbox) by default.
"""
from __future__ import annotations

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

st.set_page_config(page_title="Supply Brain - MEIO Optimiser", page_icon=":material/psychology:", layout="wide")

# --------------------------------------------------------------------------- styling
st.markdown("""
<style>
.block-container {padding-top: 1.2rem;}
.sb-badge {display:inline-block; font-size:12px; font-weight:500; padding:2px 10px; border-radius:20px; border:1px solid;}
.sb-green {background:#E8F5E2; color:#2E6E12; border-color:#2E6E12;}
.sb-amber {background:#FAF0D8; color:#7A4C0A; border-color:#7A4C0A;}
.sb-gap {background:#FDECEC; color:#9B2828; border-color:#9B2828;}
.sb-muted {color:#8C8C86; font-size:12px;}
.sb-title {font-size:20px; font-weight:600; margin-right:10px;}
div[data-testid="stMetric"] {background:#FFFFFF; border:1px solid rgba(0,0,0,.08); border-radius:10px; padding:10px 14px;}
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
        "twin_dir": OUT / "projections" / name / cfg["digital_twin"].get("run_name", "meio_policy"),
        "baseline_dir": OUT / "projections" / "SCENARIO_0001_Baseline_Forward_Plan" / "baseline",
        "evidence": OUT / "MEIO artifacts" / name / "results",
    }


# --------------------------------------------------------------------------- background jobs
RUNS.mkdir(exist_ok=True)


@st.cache_resource
def job_registry() -> dict:
    return {}


def _run_chain(job: dict):
    for label, cmd in job["steps"]:
        job["current"] = label
        with open(job["log"], "a", encoding="utf-8") as log:
            log.write(f"\n$ {' '.join(map(str, cmd))}\n")
            log.flush()
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            proc = subprocess.Popen([str(c) for c in cmd], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
            job["proc"] = proc
            code = proc.wait()
        if code != 0:
            job.update(state="failed", current=f"{label} (exit {code})", finished=time.time())
            return
    job.update(state="succeeded", current="done", finished=time.time())


def launch(key: str, title: str, steps: list[tuple[str, list]]) -> None:
    reg = job_registry()
    if reg.get(key, {}).get("state") == "running":
        st.warning(f"'{title}' is already running.")
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job = {"key": key, "title": title, "steps": steps, "state": "running", "current": steps[0][0],
           "started": time.time(), "log": RUNS / f"{key}_{stamp}.log"}
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
                            text="Estimated: no true progress is emitted by the scripts; bar is indicative only.")
                if st.button("Stop", key=f"stop_{j['key']}"):
                    proc = j.get("proc")
                    if proc:
                        proc.terminate()
            tail = Path(j["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
            st.code("\n".join(tail) or "(no output yet)", language="text")


# --------------------------------------------------------------------------- shared data
def kpis(path: Path) -> dict | None:
    return read_json(str(path / "run_kpis.json"))


def alpha_flags() -> pd.DataFrame:
    """Proxy for the mockup's '3 SKUs flagged': clusters whose own-fill-best alpha differs
    from the alpha the pipeline adopted. The repo has cluster-level evidence only."""
    p = paths()
    ev = read_csv(str(p["evidence"] / "segment_alpha_real_data_summary.csv"))
    par = read_json(str(p["weekly_dir"] / "meio_parameters.json")) or {}
    adopted = par.get("alpha")
    if ev is None or adopted is None:
        return pd.DataFrame()
    best = ev.loc[ev.groupby("cluster")["own_fill_mean"].idxmax(), ["cluster", "alpha", "own_fill_mean"]]
    best = best.rename(columns={"alpha": "best_alpha_own_fill", "own_fill_mean": "own_fill_at_best"})
    at_adopted = ev[np.isclose(ev["alpha"], adopted)][["cluster", "own_fill_mean"]].rename(
        columns={"own_fill_mean": "own_fill_at_adopted"})
    out = best.merge(at_adopted, on="cluster", how="left")
    out["adopted_alpha"] = adopted
    out["gain_pp"] = (out["own_fill_at_best"] - out["own_fill_at_adopted"]) * 100
    out["flagged"] = ~np.isclose(out["best_alpha_own_fill"], adopted)
    return out


# --------------------------------------------------------------------------- top bar
def topbar():
    page = st.session_state["page"]
    flags = alpha_flags()
    n_flag = int(flags["flagged"].sum()) if len(flags) else 0
    badge = ("sb-amber", f"{n_flag} flags") if page == "alpha" and n_flag else ("sb-green", "Up to date")
    c1, c2, c3, c4 = st.columns([6, 1, 1, 1.2], vertical_alignment="center")
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
             [PY, "models/MEIO/MEIO_Weekly/final_submission/run_weekly_tools.py", "--scenario-config", CFG_PATH])]


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
    return str(root), [("digital_twin.py", [PY, "models/twin/digital_twin.py", "--scenario-config", cfg_file,
                                             "--mode", "forward", "--policy", policy, "--seed", seed,
                                             "--horizon-weeks", horizon, "--run-name", "ui_sandbox",
                                             "--skip-output-documentation"])]


# --------------------------------------------------------------------------- pages
def page_dashboard(slot: dict, run_all_clicked: bool):
    p = paths()
    sku = read_csv(str(p["clusters"]))
    k_meio, k_base = kpis(p["twin_dir"]), kpis(p["baseline_dir"])
    flags = alpha_flags()
    n_flag = int(flags["flagged"].sum()) if len(flags) else 0

    if n_flag and not st.session_state.get("alert_dismissed"):
        a, b = st.columns([30, 1], vertical_alignment="center")
        a.warning(f"Alpha review due - {n_flag} cluster(s) have a better own-fill alpha than the adopted one "
                  f"(cluster-level proxy; the repo has no SKU-level alpha drift).", icon=":material/warning:")
        b.button("", icon=":material/close:", key="dismiss", help="Dismiss",
                 on_click=lambda: st.session_state.update(alert_dismissed=True))

    st.caption("SUMMARY")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total SKUs", f"{len(sku):,}" if sku is not None else "n/a",
              f"across {sku['cluster'].nunique()} clusters" if sku is not None else None, delta_color="off")
    if k_meio and k_base:
        m2.metric("Customer fill rate (forward plan)", f"{k_meio['customer_fill_rate']:.1%}",
                  f"{(k_meio['customer_fill_rate'] - k_base['customer_fill_rate']) * 100:+.2f} pp vs baseline plan")
        m3.metric("Avg inventory value", f"${k_meio['avg_inventory_value_usd'] / 1e6:.2f}M",
                  f"{(k_meio['avg_inventory_value_usd'] / k_base['avg_inventory_value_usd'] - 1) * 100:+.1f}% vs baseline",
                  delta_color="inverse")
    else:
        m2.metric("Customer fill rate", "n/a")
        m3.metric("Avg inventory value", "n/a")
    n_sims = p["cfg"]["meio"].get("parameters", {}).get("n_sims")
    m4.metric("MEIO sim draws (n_sims)", f"{n_sims:,}" if n_sims else "n/a",
              f"last twin run: {mtime(p['twin_dir'] / 'run_kpis.json')}", delta_color="off")
    st.caption("Mockup tile 'Inventory turns' has no pipeline output, so it is replaced by average inventory value. "
               "Mockup 'Sim iterations 10,000' maps to config n_sims (4,000; the pipeline itself uses deterministic quadrature).")

    st.caption("MODULES")
    g1, g2 = st.columns(2)
    with g1.container(border=True):
        st.markdown("**:material/pie_chart: SKU segmentation**")
        st.write("Cluster SKUs by value, variability, forecastability and supply risk into archetypes with a service ladder.")
        a, b, _ = st.columns([1.4, 1.8, 1])
        a.button("Run segmentation", icon=":material/play_arrow:", key="d_seg", on_click=goto, args=("segmentation",))
        b.button("Suggested service levels", icon=":material/percent:", key="d_sl", on_click=goto, args=("service-levels",))
        st.caption(f"Last run: {mtime(p['clusters'])}")
    with g1.container(border=True):
        st.markdown("**:material/monitoring: Simulation centre**")
        st.write("Digital Twin replay of a policy over the forward year (discrete-event, seeded) with scenario overrides.")
        a, b, _ = st.columns([1.6, 1.2, 1])
        a.button("Configure & launch", icon=":material/settings:", key="d_sim", on_click=goto, args=("simulation",))
        b.button("Last results", key="d_simres", on_click=goto, args=("simulation",))
        reg = job_registry()
        running = [j for j in reg.values() if j["key"] == "sim" and j["state"] == "running"]
        st.caption("Running" if running else f"Last run: {mtime(p['twin_dir'] / 'run_kpis.json')}")
    with g2.container(border=True):
        st.markdown("**:material/calendar_view_week: Weekly MEIO allocation**")
        st.write("Clark-Scarf v2 multi-echelon safety stock, reorder point and order-up-to per DC x SKU, rolled forward weekly.")
        a, b, _ = st.columns([1.4, 1.4, 1])
        a.button("Run allocation", icon=":material/play_arrow:", key="d_alloc", on_click=goto, args=("allocation",))
        b.button("View last output", key="d_allocview", on_click=goto, args=("allocation",))
        st.caption(f"Last run: {mtime(p['weekly_dir'] / 'meio_policy_weekly.parquet')}")
    with g2.container(border=True):
        st.markdown("**:material/refresh_alert: Periodic alpha check**")
        st.write("Re-resolve the pooling alpha (and netting discount) from the multi-seed evidence and review clusters that prefer another alpha.")
        a, b, _ = st.columns([1.4, 1.6, 1])
        a.button("Run alpha check", icon=":material/play_arrow:", key="d_alpha", on_click=goto, args=("alpha",))
        b.button(f"Review flags ({n_flag})", key="d_flags", on_click=goto, args=("alpha",))
        st.caption(f"{n_flag} cluster(s) flagged")

    st.divider()
    run_all_panel(run_all_clicked)
    slot["df"] = pd.DataFrame([{"metric": "total_skus", "value": len(sku) if sku is not None else None},
                               *({"metric": k, "value": v} for k, v in (k_meio or {}).items())])


def run_all_panel(clicked: bool):
    if clicked:
        st.session_state["show_run_all"] = True
    if st.session_state.get("show_run_all"):
        with st.container(border=True):
            st.markdown("**Run all** - executes, in order: segmentation (sandbox) -> alpha check -> "
                        "MEIO policy -> Digital Twin")
            st.warning("The MEIO + Twin step runs `run_meio_then_twin.py --yes`, which CLEARS and rebuilds "
                       f"`output datasets/MEIO/{paths()['name']}` and its Twin run. This takes a long time.")
            ok = confirm_box("I understand; rebuild the configured scenario outputs", "ra_ok")
            if st.button("Start run all", type="primary", disabled=not ok):
                steps = seg_steps(False) + alpha_steps() + meio_steps(True)
                launch("runall", "Run all", steps)
    jobs_panel(("runall",))


def page_segmentation(slot: dict):
    p = paths()
    sku, cs = read_csv(str(p["clusters"])), read_csv(str(p["cluster_sl"]))
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
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
    cols = ["cluster", "archetype", "n_skus", "revenue_share", "net_margin_share", "forecast_baseline_wmape",
            "recommended_csl", "target_case_fill_rate", "safety_factor_z"]
    st.subheader("Segment portfolio")
    st.dataframe(cs[cols].sort_values("recommended_csl", ascending=False), hide_index=True, width="stretch",
                 column_config={"revenue_share": st.column_config.NumberColumn(format="percent"),
                                "net_margin_share": st.column_config.NumberColumn(format="percent"),
                                "recommended_csl": st.column_config.NumberColumn(format="percent"),
                                "target_case_fill_rate": st.column_config.NumberColumn(format="percent")})
    st.caption(f"{tot} SKUs in {len(cs)} clusters. Mockup shows 4 segments A/B/C/D; the pipeline produces {len(cs)} named archetypes.")
    art = SEG / "report_artifacts"
    imgs = [("chart_fingerprints.png", "Cluster fingerprints"), ("chart_pca.png", "PCA map"),
            ("chart_service_ladder.png", "Service ladder"), ("chart_failure_modes.png", "Failure modes")]
    t1, t2, t3 = st.tabs(["Charts", "SKU table", "Report"])
    with t1:
        c = st.columns(2)
        for i, (f, cap) in enumerate(imgs):
            if (art / f).exists():
                c[i % 2].image(str(art / f), caption=cap)
    with t2:
        if sku is not None:
            pick = st.multiselect("Archetype", sorted(sku["archetype"].unique()))
            view = sku if not pick else sku[sku["archetype"].isin(pick)]
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


SL_ALIAS = {"Flagship Core": "Flagship Promo-Driven"}  # evidence was swept on v4 cluster names


def page_service_levels(slot: dict):
    p = paths()
    cs = read_csv(str(p["cluster_sl"]))
    ev = read_csv(str(p["evidence"] / "service_level_real_data_summary.csv"))
    st.write("Compute target service levels per cluster. The mockup's three methods map as follows.")
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
            st.error("service_level_real_data_summary.csv not found.")
        else:
            e = ev.copy()
            rows = []
            for arche in cs["archetype"]:
                name = SL_ALIAS.get(arche, arche)
                sub = e[e["cluster"] == name]
                if sub.empty:
                    rows.append({"archetype": arche, "evidence_cluster": None, "pick_csl": None,
                                 "note": "no twin-validated sweep for this cluster (sweep covers 4 v4 clusters)"})
                    continue
                if method == "Cost-minimisation":
                    r = sub.loc[sub["cost_delta_mean"].idxmin()]
                    note = "lowest mean landed-cost delta vs actual (5 seeds)"
                else:
                    ok = sub[sub["own_fill_mean"] >= fill_target]
                    r = ok.loc[ok["csl"].idxmin()] if len(ok) else sub.loc[sub["own_fill_mean"].idxmax()]
                    note = "lowest CSL reaching the target own fill" if len(ok) else "target not reached; highest-fill CSL shown"
                rows.append({"archetype": arche, "evidence_cluster": name, "pick_csl": r["csl"],
                             "own_fill": r["own_fill_mean"], "cost_delta_usd": r["cost_delta_mean"], "note": note})
            res = pd.DataFrame(rows)
            st.subheader(f"{method} - lookup in Twin-validated sweep")
            st.dataframe(res, hide_index=True, width="stretch")
            st.caption("Holding / shortage inputs do not apply here: the sweep was run at fixed cost parameters. "
                       "Sweep used v4 cluster names (Flagship Promo-Driven = v5 Flagship Core).")
            slot["df"] = res
    st.subheader("Current recommendation (output datasets/segmentation/cluster_service_levels.csv)")
    if cs is not None:
        st.dataframe(cs[["cluster", "archetype", "n_skus", "median_newsvendor_cr", "recommended_csl",
                         "target_case_fill_rate", "safety_factor_z"]], hide_index=True, width="stretch")
        if slot.get("df") is None:
            slot["df"] = cs
    if ev is not None:
        st.subheader("Twin-validated CSL sweep (own-cluster fill vs CSL, 5 seeds)")
        st.line_chart(ev.pivot(index="csl", columns="cluster", values="own_fill_mean"))
        with st.expander("Evidence table"):
            st.dataframe(ev, hide_index=True, width="stretch")
    if st.button("Apply to MEIO pipeline", icon=":material/send:", disabled=True,
                 help="Not mapped: MEIO reads per-cluster z only through meio_parameters when the sweep is complete "
                      "and beats the segment ladder (currently 'not used')."):
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
    slot["df"] = summ


def page_alpha(slot: dict):
    p = paths()
    if st.button("Run alpha check", icon=":material/play_arrow:", type="primary"):
        launch("alpha", "Periodic alpha check", alpha_steps())
    jobs_panel(("alpha",))
    par = read_json(str(RUNS / "alpha_receipt.json")) or read_json(str(p["weekly_dir"] / "meio_parameters.json"))
    if par:
        a, b, c = st.columns(3)
        a.metric("Adopted alpha (pooling)", par.get("alpha"))
        b.metric("Netting discount", par.get("netting_discount"))
        c.metric("Model", par.get("model"))
        for d in par.get("decisions", []):
            st.markdown(f"- **{d['parameter']}** = `{d['value']}` - {d['reason']}")
    st.info("Note: in this repo alpha is the Clark-Scarf v2 pooling factor, not the exponential-smoothing alpha "
            "the mockup's description implies.")
    st.subheader("Review flags (cluster-level proxy)")
    flags = alpha_flags()
    if len(flags):
        st.dataframe(flags, hide_index=True, width="stretch",
                     column_config={"own_fill_at_best": st.column_config.NumberColumn(format="percent"),
                                    "own_fill_at_adopted": st.column_config.NumberColumn(format="percent")})
        slot["df"] = flags
    ev = read_csv(str(p["evidence"] / "segment_alpha_real_data_summary.csv"))
    if ev is not None:
        st.subheader("Own-cluster fill vs alpha (5 seeds, real replayed demand)")
        st.line_chart(ev.pivot(index="alpha", columns="cluster", values="own_fill_mean"))
    sw = read_csv(str(p["evidence"] / "alpha_sweep_real_data.csv"))
    if sw is not None:
        with st.expander("Portfolio alpha sweep"):
            st.dataframe(sw, hide_index=True, width="stretch")


def list_scenarios() -> list[str]:
    root = OUT / "overrides"
    return sorted(d.name for d in root.iterdir() if (d / "scenario_config.json").exists()) if root.exists() else []


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
        st.caption("Not mapped from the mockup: Iterations (the Twin is a single seeded replay), Segment scope, and the "
                   "free demand-scenario list. Scenarios come from the Agents' override engine - see below.")
        go = st.form_submit_button("Launch simulation", icon=":material/play_arrow:", type="primary")
    if go:
        policy = (next(iter(p["meio_dir"].rglob("meio_policy_rolling.parquet")), None)
                  if policy_opt.startswith("MEIO") else ROOT / p["cfg"]["digital_twin"]["policy"])
        if policy is None or not Path(policy).exists():
            st.error("Policy file not found. Run Weekly MEIO allocation first.")
        else:
            root, steps = sim_steps(str(policy), int(horizon), int(seed))
            st.session_state["sim_root"] = root
            launch("sim", "Simulation (sandbox)", steps)
    jobs_panel(("sim",))

    st.subheader("Results")
    runs = {"Forward plan - MEIO policy": p["twin_dir"], "Forward plan - baseline policy": p["baseline_dir"]}
    for s in list_scenarios():
        for arm in ("prepared", "unprepared"):
            runs[f"{s} / {arm}"] = OUT / "projections" / s / arm
    for d in sorted(RUNS.glob("twin_*")):
        for k in d.rglob("run_kpis.json"):
            runs[f"Sandbox {d.name}"] = k.parent
    names = st.multiselect("Compare runs", list(runs), default=[n for n in list(runs)[:2]])
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
        st.dataframe(df[[c for c in keep if c in df.columns]], width="stretch")
        slot["df"] = df.reset_index(names="run")
        sv = runs[names[0]] / "service_by_node.csv"
        node = read_csv(str(sv))
        if node is not None:
            with st.expander(f"Service by node - {names[0]}"):
                st.dataframe(node, hide_index=True, width="stretch")
    st.subheader("Scenarios from the Agents' override engine")
    sc = list_scenarios()
    if sc:
        pick = st.selectbox("Scenario", sc)
        stt = read_json(str(OUT / "overrides" / pick / "status.json")) or {}
        card = OUT / "overrides" / pick / "scenario_card.md"
        st.caption(f"Status: {stt.get('state', 'unknown')}")
        if card.exists():
            with st.expander("Scenario card"):
                st.markdown(card.read_text(encoding="utf-8", errors="replace"))
        st.button("Launch approved scenario", disabled=True,
                  help="Not wired: approval is human-only (python -m Agents.tools approve ...), "
                       "then `run_scenario` launches it. Use the Agents CLI.")
    else:
        st.info("No scenarios yet. Create them with the Agents (python -m Agents.runtime ...).")


# --------------------------------------------------------------------------- shell
with st.sidebar:
    st.markdown("### :material/psychology: Supply Brain\nMEIO Optimiser")
    st.caption("OVERVIEW")
    st.radio("Navigation", list(PAGES), format_func=PAGES.get, key="page", label_visibility="collapsed")

slot: dict = {}
header = st.container()
run_all_clicked = False
with header:
    run_all_clicked, export_col = topbar()

page = st.session_state["page"]
body = {"dashboard": lambda: page_dashboard(slot, run_all_clicked), "segmentation": lambda: page_segmentation(slot),
        "service-levels": lambda: page_service_levels(slot), "allocation": lambda: page_allocation(slot),
        "alpha": lambda: page_alpha(slot), "simulation": lambda: page_simulation(slot)}[page]
if run_all_clicked and page != "dashboard":
    st.session_state.update(page="dashboard", show_run_all=True)
    st.rerun()
body()
with export_col:
    df = slot.get("df")
    if df is not None:
        st.download_button("Export", df.to_csv(index=False).encode(), file_name=f"{page}.csv", mime="text/csv",
                           icon=":material/upload:", width="stretch")
    else:
        st.button("Export", icon=":material/upload:", disabled=True, width="stretch",
                  help="No table on this page to export.")
