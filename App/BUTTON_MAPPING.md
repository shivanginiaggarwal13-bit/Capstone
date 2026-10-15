# Mockup button -> repository resource mapping

Source: `supply_brain_meio_mockup.html`. App: `App/streamlit_app.py`.
Run it from the repo root: `python -m streamlit run App/streamlit_app.py`.

Status key: **Mapped** = wired to a real script/output. **Partial** = wired, but the repo supports less than the
mockup shows. **Not mapped** = nothing in the repo backs it. **UI only** = no backend needed.

## Top bar

| Button | Status | Resource / note |
|---|---|---|
| Export | UI only | No export logic exists in the repo. App downloads the current page's table as CSV. |
| Refresh | UI only | Clears the app's cache and re-reads output files. The repo has no "data refresh" job. |
| Run all | Partial | Runs segmentation (sandbox) -> alpha check -> `run_meio_then_twin.py --yes` (MEIO policy, then Digital Twin). The mockup's 4th step, Simulation centre, is the Twin stage of the same runner. Asks for confirmation because it clears and rebuilds `output datasets/MEIO/SCENARIO_0002_...` and its Twin run. Does not run `run_all.py` (hours of research sweeps). |

## Sidebar

| Item | Status | Resource / note |
|---|---|---|
| Dashboard, SKU segmentation, Weekly allocation, Periodic alpha check, Simulation centre | UI only | Page navigation. |
| Suggested service levels (sub-item) | Mapped | `output datasets/segmentation/cluster_service_levels.csv` plus recompute (below). |
| Footer "Connected" | Removed | Not part of the Capstone project; removed from the app. |

## Alert banner and summary tiles

| Element | Status | Resource / note |
|---|---|---|
| Alert "3 SKUs flagged" | Partial | Derived: clusters whose best own-fill alpha (`segment_alpha_real_data_summary.csv`) differs from the adopted alpha (`meio_parameters.json`). Cluster-level, not SKU-level. Currently 3 clusters. |
| Dismiss (x) | UI only | |
| Total SKUs 1,284 "across 4 segments" | Partial | Real value: 500 SKUs, 6 clusters (`sku_clusters.csv`). There are no A/B/C/D segments. |
| Avg service level 96.4% | Partial | Shown as customer fill rate of the forward plan (`projections/SCENARIO_0002.../meio_policy/run_kpis.json`), with delta vs the baseline plan. The repo has no "service level" KPI as such. |
| Inventory turns 8.2x | Not mapped | No turns KPI in any output. Tile replaced by average inventory value (`run_kpis.json`). |
| Sim iterations 10,000 / "2h ago" | Partial | Closest is `meio.parameters.n_sims` = 4,000 in `scenario_config.json`, used only by Monte Carlo research scripts. The pipeline uses deterministic quadrature. "Last run" reads the Twin run's file time. |

## Module cards

| Button | Status | Resource / note |
|---|---|---|
| Run segmentation | Mapped | `Clustering & Segmentation/segmentation_v5/segmentation_v5.py`. It reads CSV, but `input datasets/` is parquet, so `App/stage_csv.py` stages CSV copies first (one-off, ~400 MB in `App/runs/csv_input`). Writes to a sandbox unless "Overwrite canonical output" is on. Sandbox run was verified to reproduce the canonical cluster table. |
| Suggested service levels (pill) | Mapped / Partial | See next section. |
| Run allocation | Mapped | `models/MEIO/MEIO_Weekly/final_submission/run_weekly_tools.py` (the entrypoint `scenario_config.json` names); option to chain `run_meio_then_twin.py`. Overwrites the scenario's MEIO folder, so confirmation is required. |
| View last output (allocation) | Mapped | `output datasets/MEIO/<scenario>/2026-W01/meio_summary_by_dc_weekly.csv`, `meio_recommendations_weekly.parquet`, `meio_parameters.json`. |
| Run alpha check | Partial | `App/alpha_check.py` calls `meio_parameters.resolve()`: re-selects alpha / netting discount from the multi-seed evidence. Read-only. This is the Clark-Scarf pooling alpha, not the demand-smoothing alpha the mockup text describes. |
| Review flags (3) | Partial | Cluster-level table described above. No per-SKU drift or tolerance thresholds exist. |
| Configure & launch (simulation) | Partial | See next section. |
| Last results (simulation) | Mapped | `projections/*/run_kpis.json`, `service_by_node.csv`; compare any run, including agent scenarios (`prepared` / `unprepared`) and sandbox runs. |
| Progress bar "Running - 68%" | Partial | Scripts emit no progress. App shows job state, elapsed time, log tail and an indicative bar. |

## Suggested service levels modal

| Control | Status | Resource / note |
|---|---|---|
| Method: Newsvendor (critical ratio) | Mapped | Re-implements `segmentation_v5.service_ladder()` + `assign_csl()` over `sku_master`, `cost_parameters` and `sku_clusters`. Default inputs reproduce `cluster_service_levels.csv`. |
| Method: Cost-minimisation | Partial | No cost-min optimiser exists. Lookup: CSL with the lowest mean landed-cost delta in `service_level_real_data_summary.csv`. |
| Method: Fill-rate target | Partial | No such optimiser. Lookup: lowest CSL in the same sweep that reaches the entered own-fill target. |
| Holding cost rate (% of value/yr) | Partial | Used only by the newsvendor method (as % of `unit_cost_usd`, replacing `holding_cost_per_unit_year_usd`). The sweep was run at fixed costs. |
| Shortage penalty multiplier | Partial | Newsvendor only (replaces `stockout_cost_multiplier`). |
| Compute service levels | Mapped | Result table. |
| (extra) Apply to MEIO | Not mapped | Disabled in the app. MEIO uses per-cluster z only when the sweep is complete and beats the segment ladder; today `meio_parameters.json` says "not used". |

## Simulation configuration modal

| Control | Status | Resource / note |
|---|---|---|
| Iterations 10,000 | Not mapped | The Digital Twin is one seeded discrete-event replay, not a Monte Carlo loop. App offers a **Seed** instead. |
| Horizon (weeks) | Mapped | `digital_twin.py --horizon-weeks`. Forward data is 52 weeks (2026). |
| Segment scope (All / A / B / A+B) | Not mapped | No Twin flag. Override engine can scope by cluster, but not from this form, and there are no A/B segments. |
| Demand scenario (Baseline, +20%, -15%, CV 0.4) | Partial | Expressible as `demand.volume` (scale 1.2 / 0.85) and `demand.volatility` overrides, but only through the Agents flow (draft -> human approval -> `run_scenario`). The app lists existing scenarios and their cards; launching is disabled because approval is human-only. |
| (extra) Policy | Mapped | MEIO rolling policy (`meio_policy_rolling.parquet`) or the baseline `history/forward_policy.parquet`. |
| Launch simulation | Mapped | `models/twin/digital_twin.py` on a sandbox copy of `scenario_config.json` with output under `App/runs/`. A 4-week run takes about 20 s. |
| Cancel / close (x) / Esc | UI only | |

## Not mapped after this exercise (summary)

1. Footer "Connected" (removed from the app).
2. Tile "Inventory turns".
3. Simulation "Iterations" input and its "Sim iterations" tile (nearest: `n_sims`, research-only).
4. Simulation "Segment scope" (A / B / A+B); the A/B/C/D segment model itself (data has 6 clusters, plus 3 `segment` values in `sku_master`).
5. Per-SKU alpha-drift flags and tolerance thresholds ("3 SKUs flagged", "Review flags (3)"); only a cluster-level proxy exists.
6. Real progress percentage for runs.
7. A launch button for a free-form demand scenario (needs the Agents' approval gate).
8. "Apply service levels to MEIO".

Partial: Cost-minimisation and Fill-rate-target methods, holding/shortage inputs for non-newsvendor methods, average service level, Run all (excludes the research sweeps).

## Repo inconsistencies found while mapping

- `scenario_config.json` (`meio.parameters.clusters`) reads `segmentation_v4/sku_clusters.csv`; the current canonical segmentation is v5 in `output datasets/segmentation/`. The CSL/alpha evidence uses v4 cluster names (Flagship Promo-Driven, 4 clusters); v5 has 6 archetypes (Flagship Core, ...). The app aliases Flagship Promo-Driven to Flagship Core.
- `segmentation_v5.py` reads CSV inputs that no longer exist (parquet now).
- `final_submission_optimized` needs `polars` (not installed here); the app uses `final_submission`, as the config does.
