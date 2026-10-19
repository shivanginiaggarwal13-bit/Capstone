"""Supply Brain -- MEIO Optimiser front end (Streamlit app).

Every button is wired to a real resource in this repository, or is shown as "not mapped" with the reason. See BUTTON_MAPPING.md for the full table.

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
              "alpha": ":material/refresh_alert:", "simulation": ":material/monitoring:"}
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
            log_file = Path(j["log"])
            tail = (log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
                    if log_file.exists() else [])
            st.code("\n".join(tail) or "(no output yet)", language="text")


# --------------------------------------------------------------------------- shared data
def kpis(path: Path) -> dict | None:
    return read_json(str(path / "run_kpis.json"))


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


def page_dashboard(slot: dict):
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
    if sku is not None:
        m1.metric("Total SKUs", f"{len(sku):,}")
        m1.caption(f"across {sku['cluster'].nunique()} clusters")
    else:
        m1.metric("Total SKUs", "n/a")
    if k_meio and k_base:
        m2.metric("Fill rate (forward plan)", f"{k_meio['customer_fill_rate']:.1%}")
        m2.caption(f"Baseline plan: {k_base['customer_fill_rate']:.1%}. The MEIO plan is "
                   f"{(k_meio['customer_fill_rate'] - k_base['customer_fill_rate']) * 100:+.2f} percentage points higher "
                   f"(share of customer demand filled from stock).")
        m3.metric("Avg inventory value", f"${k_meio['avg_inventory_value_usd'] / 1e6:.2f}M")
        m3.caption(f"Baseline plan: ${k_base['avg_inventory_value_usd'] / 1e6:.2f}M. The MEIO plan holds "
                   f"{(k_meio['avg_inventory_value_usd'] / k_base['avg_inventory_value_usd'] - 1) * 100:+.1f}% more stock value.")
    else:
        m2.metric("Fill rate (forward plan)", "n/a")
        m3.metric("Avg inventory value", "n/a")
    n_sims = p["cfg"]["meio"].get("parameters", {}).get("n_sims")
    m4.metric("MEIO n_sims", f"{n_sims:,}" if n_sims else "n/a")
    m4.caption(f"last Twin run: {mtime(p['twin_dir'] / 'run_kpis.json')}")

    st.caption("MODULES")
    cs = read_csv(str(p["cluster_sl"]))
    summ = read_csv(str(p["weekly_dir"] / "meio_summary_by_dc_weekly.csv"))
    par = read_json(str(p["weekly_dir"] / "meio_parameters.json")) or {}
    tabs = st.tabs([":material/pie_chart: SKU segmentation", ":material/calendar_view_week: Weekly MEIO allocation",
                    ":material/refresh_alert: Periodic alpha check", ":material/monitoring: Simulation centre"])

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
            x, y = st.columns(2)
            x.metric("Adopted alpha", par.get("alpha"))
            y.metric("Netting discount", par.get("netting_discount"))
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
            st.markdown("**Run all** - executes, in order: segmentation (sandbox) -> alpha check -> "
                        "MEIO policy -> Digital Twin")
            st.warning("The MEIO + Twin step runs `run_meio_then_twin.py --yes`, which CLEARS and rebuilds "
                       f"`output datasets/MEIO/{paths()['name']}` and its Twin run. This takes a long time.")
            ok = confirm_box("I understand; rebuild the configured scenario outputs", "ra_ok")
            if st.button("Start run all", type="primary", disabled=not ok):
                steps = seg_steps(False) + alpha_steps() + meio_steps(True)
                launch("runall", "Run all", steps)
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
               "group; overlap marks boundary SKUs worth a business review. Recomputed here with plain standardised "
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
            "a demand-smoothing alpha.")
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
        st.caption("Each cluster's own fill rate as ITS alpha varies, with every other cluster held at the "
                   "live reference (0.6) -- not a portfolio-wide number. The vertical line marks the "
                   "adopted alpha. Y-axis is NOT forced to 0-100%: at that scale every line looks flat and "
                   "the real spread (0.9pp for Mid-Tier Core, 11.6pp for Long-Tail Erratic 2) disappears.")
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
        st.caption("One slider per cluster: each varies ONLY that cluster's own alpha while every other "
                   "cluster stays at the live reference (0.6) -- a one-at-a-time view, not a simultaneous "
                   "what-if across all six at once (that would take 6^6 twin runs, which don't exist). Cost "
                   "here is a single total-landed-cost delta vs. actual history -- not broken into "
                   "holding/stockout/transport like the portfolio view below. Fill rates are the 51-week "
                   "real-demand test window and don't scale with the time-period toggle below; cost delta does.")
        SEG_WINDOW_WEEKS = 156   # same full-run cumulative convention as alpha_cost_breakdown.py's WINDOW_WEEKS
        period5 = st.radio("Time period", ["Week", "Month", "Year", "All"], index=3, horizontal=True,
                           key="alpha_explore_seg_period")
        per_week5 = 1.0 / SEG_WINDOW_WEEKS
        divisor5 = {"Week": per_week5, "Month": per_week5 * 52 / 12, "Year": per_week5 * 52, "All": 1.0}[period5]
        suffix5 = {"Week": " / week", "Month": " / month", "Year": " / year", "All": " (3-yr total)"}[period5]
        alphas5 = sorted(ev5["alpha"].unique().tolist())
        clusters5 = ev5["cluster"].unique().tolist()
        default5 = 0.6 if 0.6 in alphas5 else alphas5[len(alphas5) // 2]
        cols5 = st.columns(2)
        for i, cluster in enumerate(clusters5):
            with cols5[i % 2]:
                with st.container(border=True):
                    st.markdown(f"**{cluster}**")
                    slug = re.sub(r"[^a-z0-9]+", "_", cluster.lower()).strip("_")
                    chosen5 = st.select_slider("Alpha", options=alphas5, value=default5,
                                               key=f"alpha_explore_seg_{slug}")
                    row5 = ev5[(ev5["cluster"] == cluster) & (ev5["alpha"] == chosen5)].iloc[0]
                    portfolio_help = ("Portfolio-wide, not per-segment -- identical across all 6 cards "
                                      "whenever this slider sits at the 0.6 reference, since they all read "
                                      "the same underlying twin run. Only diverges once THIS cluster's alpha "
                                      "moves away from 0.6.")
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
        st.caption("Total customer and internal-replenishment fill rate at each tested alpha -- "
                   "the portfolio-wide number a judgment call actually trades off, not a per-cluster view.")
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
        st.caption("Pooling (higher alpha) isn't free: it trades stockout cost for more transport cost. "
                   "Holding stays essentially flat. A single net total hides this trade-off, so it's broken "
                   "out below. These are cumulative totals over the full 156-week (3-year) simulation -- "
                   "use the slider below to read off any tested alpha's numbers per week, month or year instead.")
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
        st.caption("This slider applies ONE alpha to the whole portfolio -- for the per-cluster view, see "
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
        st.caption("Fill rates below are a rate, not a sum, so the time-period selector doesn't apply to them -- "
                   "they're the 51-week real-demand test window specifically (2025-W02..2025-W52, where the "
                   "swept alpha actually governs the policy), not blended with the 105-week warm-up.")
        f1, f2 = st.columns(2)
        f1.metric("Customer fill rate", f"{row['customer_fill_rate']:.1%}")
        f2.metric("Internal replen fill rate", f"{row['internal_replen_fill_rate']:.1%}")
    else:
        st.info("No cost breakdown yet -- run the alpha sweep (`validate_real_data.py --stage alpha_sweep` in "
                "final_submission_optimized/) to generate `alpha_cost_breakdown.csv`.")

    sw = read_csv(str(p["evidence"] / "alpha_sweep_real_data.csv"))
    if sw is not None:
        with st.expander("Portfolio alpha sweep (raw)"):
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
        st.caption("Not available: Iterations (the Twin is a single seeded replay), Segment scope, and the "
                   "free demand-scenario list. Scenarios come from the Agents' override engine - see below.")
        go = st.form_submit_button("Launch simulation", icon=":material/play_arrow:", type="primary")
    if go:
        start_sim(policy_opt, int(horizon), int(seed))
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
    st.markdown(
        "<div class='sb-brand'><div class='sb-brand-mark'>:material/psychology:</div>"
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
