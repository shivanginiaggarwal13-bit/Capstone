# Mockup button -> repository resource mapping

Source: `supply_brain_meio_mockup.html` (not in this repository). App: `App/streamlit_app.py`.
Run it from the repo root: `python -m streamlit run App/streamlit_app.py`.

Status key: **Mapped** = wired to a real script/output. **Partial** = wired, but the repo supports less than the
mockup shows. **Not mapped** = nothing in the repo backs it. **UI only** = no backend needed.

`<scenario>` below is the folder of the root `scenario_config.json`, today `SCENARIO_0002_MEIO_CLARK_SCARF_V2`.
Every run the app launches is a background job: its panel shows state, elapsed time, the log tail
(`App/runs/<job>_<time>.log`) and a **Stop** button that terminates the running step.

## Top bar

| Button | Status | Resource / note |
|---|---|---|
| Export | UI only | No export logic exists in the repo. App downloads the current page's table as CSV; disabled when the page has no table. |
| Refresh | UI only | Clears the app's data cache and re-reads output files. The repo has no "data refresh" job. |
| Run all | Partial | Runs `run_meio_then_twin.py --scenario-config scenario_config.json --yes` (MEIO policy via `final_submission_optimized/run_weekly_tools.py`, then `digital_twin_fast.py`). Segmentation and the alpha check are not part of it; they run from their own pages. Needs a confirmation tick ("I understand; rebuild the configured scenario outputs") and then **Start run all**, because it clears and rebuilds `output datasets/MEIO/<scenario>/` and rewrites the Twin run `output datasets/<scenario>/meio_policy/`. Does not run `run_all.py` (hours of research sweeps). |
| Status badge | Mapped | "Up to date", or "Re-run needed" when the rolling MEIO policy is older than `orders.parquet`, `sku_clusters.csv` or the multi-seed evidence, or the Twin run is older than the policy (`outputs_stale()`). On the alpha page it shows the flag count instead. |

## Sidebar

| Item | Status | Resource / note |
|---|---|---|
| Dashboard, SKU segmentation, Suggested service levels, Weekly MEIO allocation, Periodic alpha check, Simulation centre | UI only | Page navigation (six pages). |
| Suggested service levels | Mapped | Its own page now, not a sub-item: `output datasets/segmentation/cluster_service_levels.csv` plus recompute (below). |
| Footer "Connected" | Removed | Not part of the Capstone project; removed from the app. |

## Alert banner and summary tiles

| Element | Status | Resource / note |
|---|---|---|
| Alert "3 SKUs flagged" | Partial | Shown as "Alpha review due - N cluster(s)...". Derived: clusters whose best own-fill alpha in `output datasets/MEIO artifacts/<scenario>/results/segment_alpha_v5_real_data_summary.csv` differs from the alpha adopted for that cluster (`alpha_by_cluster` in `meio_parameters.json`, else the portfolio alpha) by a fill gain above 0.3 pp. Cluster-level, not SKU-level. Today no cluster is flagged, so the banner is hidden. |
| Dismiss (x) | UI only | |
| Total SKUs 1,284 "across 4 segments" | Partial | Shown as a caption under the tiles: 500 SKUs in 6 clusters (`sku_clusters.csv`), 4 plant DCs, 8 regional DCs, 15 customer DCs, the alpha range in use, and the last Twin run time. There are no A/B/C/D segments. |
| Avg service level 96.4% | Partial | Shown as the customer fill rate of the forward plan (`output datasets/<scenario>/meio_policy/run_kpis.json`), with the delta vs the baseline plan (`output datasets/SCENARIO_0001_Baseline_Forward_Plan/baseline/run_kpis.json`). The repo has no "service level" KPI as such. |
| Inventory turns 8.2x | Not mapped | `run_kpis.json` has no turns KPI (the Agents' `compare_runs` derives one; the app does not show it). The tile row shows customer fill rate, total landed cost, lost-sales cost and average inventory value, each against the baseline plan. |
| Sim iterations 10,000 / "2h ago" | Not mapped | No tile. Closest is `meio.parameters.n_sims` = 4,000 in `scenario_config.json`, used only by Monte Carlo research scripts; the pipeline uses deterministic quadrature. The allocation page's "Parameters used" shows it. "Last run" times appear in the captions. |
| (extra) Latest what-if scenario | Mapped | The newest `output datasets/SCENARIO_*` folder with finished arms: customer fill, lost-sales cost, average inventory and total landed cost per arm (do nothing / plan ahead / react weekly), the best-service and lowest-cost arm, and **Open in Simulation centre**. |

## Module cards

| Button | Status | Resource / note |
|---|---|---|
| Run segmentation | Mapped | `Clustering & Segmentation/segmentation_v5/segmentation_v5.py --input App/runs/csv_input --outdir <out>`, after `App/stage_csv.py` stages CSV copies of the nine parquet tables it reads (reused while newer than the parquet). Writes to a sandbox (`App/runs/segmentation_<time>/`) unless "Overwrite canonical output" is on (then `output datasets/segmentation/`). The dashboard button always runs the sandbox. The page compares the latest sandbox `cluster_service_levels.csv` with the canonical one and says whether they are identical. |
| Suggested service levels (pill) | Mapped / Partial | See next section. |
| Run allocation | Mapped | `models/MEIO/MEIO_Weekly/final_submission_optimized/run_weekly_tools.py --scenario-config scenario_config.json` (the runner `scenario_config.json` names), or on the allocation page "MEIO + Digital Twin" to run `run_meio_then_twin.py --yes` instead. Overwrites the scenario's MEIO folder, so a confirmation tick is required. |
| View last output (allocation) | Mapped | `output datasets/MEIO/<scenario>/2026-W01/meio_summary_by_dc_weekly.csv`, `meio_recommendations_weekly.parquet`, `meio_parameters.json`, and the weekly policy viewer over `rolling_2026/meio_policy_rolling.parquet` (filter by week, DC, category, SKU). |
| Run alpha check | Partial | `App/alpha_check.py` calls `final_submission_optimized/meio_parameters.resolve()` on the root config, with the evidence restricted to the `output datasets` folders: re-selects the portfolio alpha, netting discount and per-cluster alphas from the multi-seed evidence. Writes only `App/runs/alpha_receipt.json`. This is the Clark-Scarf pooling alpha, not the demand-smoothing alpha the mockup text describes. |
| (extra) Run alpha sweep | Mapped | Alpha page, "Re-tune alpha": after a tick and typing SWEEP, runs `App/alpha_sweep_prep.py` (backs up the evidence to `results/_backup/`; `--fresh` also clears partial results), `final_submission_optimized/segment_alpha_v5_real_data.py --lean-twin-outputs` (6 clusters x 6 alphas x 5 seeds), then the alpha check. Rewrites the evidence the next MEIO run reads. |
| Review flags (3) | Partial | Cluster-level table described above (`alpha_flags()`); the button shows the current count. No per-SKU drift or tolerance thresholds exist. |
| Configure & launch (simulation) | Partial | See the simulation section below. The dashboard's **Run default simulation** launches it with the MEIO rolling policy, 52 weeks and the config's seed. |
| Last results (simulation) | Mapped | `output datasets/<scenario folder>/<run>/run_kpis.json` and `service_by_node.csv`; compare any run, including agent scenarios (`prepared` / `unprepared` / `reactive`), the no-scenario counterfactuals under `output datasets/<scenario>/counterfactual_*`, and sandbox runs. |
| Progress bar "Running - 68%" | Partial | Scripts emit no progress. App shows job state, elapsed time, log tail and an indicative bar ("Indicative progress only"). |

## Suggested service levels (page; the mockup's modal)

| Control | Status | Resource / note |
|---|---|---|
| Method: Newsvendor (critical ratio) | Mapped | Re-implements `segmentation_v5.service_ladder()` + `assign_csl()` over `sku_master`, `cost_parameters` and `sku_clusters`. Default inputs reproduce `cluster_service_levels.csv`. |
| Method: Cost-minimisation | Partial | No cost-min optimiser exists. Lookup: CSL with the lowest mean landed-cost delta in `service_level_v5_real_data_summary.csv` (the v5 clusters). |
| Method: Fill-rate target | Partial | No such optimiser. Lookup: lowest CSL in the same sweep that reaches the entered own-fill target (0.85-0.99, default 0.95). |
| Holding cost rate (% of value/yr) | Partial | Applied only when "Override holding cost" is ticked (5-60, default 25), and only by the newsvendor method (as % of `unit_cost_usd`, replacing `holding_cost_per_unit_year_usd`). The sweep was run at fixed costs. |
| Shortage penalty multiplier | Partial | Applied only when "Override shortage multiplier" is ticked (1-20, default 3.5); newsvendor only (replaces `stockout_cost_multiplier`). |
| Compute service levels | Mapped | Result table. The page also shows the current ladder and the Twin-validated CSL sweep charts. |
| (extra) Apply to MEIO pipeline | Not mapped | Disabled in the app. MEIO already sizes to the cluster ladder in `sku_clusters.csv` (`safety_factor_z`); `meio_parameters.json` records the sweep-picked ladder as "not used". |

## Simulation centre (the mockup's simulation configuration modal)

| Control | Status | Resource / note |
|---|---|---|
| Iterations 10,000 | Not mapped | The Digital Twin is one seeded discrete-event replay, not a Monte Carlo loop. App offers a **Seed** instead (default: the config's seed). |
| Horizon (weeks) | Mapped | `digital_twin_fast.py --horizon-weeks` (4-104, default 52). Forward data is 52 weeks (2026). |
| Segment scope (All / A / B / A+B) | Not mapped | No Twin flag. Override engine can scope by cluster, but not from this form, and there are no A/B segments. |
| Demand scenario (Baseline, +20%, -15%, CV 0.4) | Partial | Expressible as `demand.volume` (scale 1.2 / 0.85) and `demand.volatility` overrides, but only through the Agents flow (draft -> human approval -> `run_scenario`). The form has no demand control; use the scenario agents section below. |
| (extra) Policy | Mapped | "MEIO rolling policy (Clark-Scarf v2)" = the first `meio_policy_rolling.parquet` under `output datasets/MEIO/<scenario>/`, or "Baseline forward policy" = the config's `digital_twin.policy` (`input datasets/baseline_policy.csv`). |
| Launch simulation | Mapped | `models/MEIO/MEIO_Weekly/final_submission_optimized/digital_twin_fast.py --mode forward --run-name ui_sandbox --skip-output-documentation` on a sandbox copy of `scenario_config.json` (scenario number 9000+, every output root under `App/runs/twin_<time>/`). |
| Cancel / close (x) / Esc | UI only | |
| (extra) Compare runs + Scenario impact | Mapped | KPI table of the selected runs, then the differences a scenario answers: impact if ignored, value of re-planning in advance, value of re-planning weekly (split with the reactive control when it is selected), net position. For a windowed override it can recompute over the window from each run's `inventory.parquet`. |

## Scenario agents (Simulation centre)

| Control | Status | Resource / note |
|---|---|---|
| Model profile | Mapped | "Desktop bridge (no API key)" = profile `desktop`; "Claude (needs an Anthropic API key)" = profile `claude` (`claude-opus-5` orchestrator, `claude-haiku-4-5` specialists). The default `nvidia` profile is not offered here. |
| Anthropic API key | Mapped | Optional; kept in process memory and set as `ANTHROPIC_API_KEY` for the session. Blank uses the server's own key. |
| New session / chat input | Mapped | One in-process `Agents.runtime.graph.Session`, kept across turns, so clarifying questions work. Shows live progress from the orchestrator's checkpoint. |
| Approve / Reject (in the chat) | Mapped | Answers the `request_human_approval` interrupt for every profile; Approve needs a name, which goes into `approval.json`. |
| Agent activity feed | Mapped | Desktop profile only: every bridge model call of this session (agent, model, time, tool calls) from `Agents/workspace/bridge/<session>/`. |
| Scenarios from the Agents' override engine | Mapped | Lists `output datasets/overrides/*` with run status (`status.json`), approval state (SHA-256 check) and the card. **Approve** runs `python -m Agents.tools approve <scenario> --by <name> --yes`; it does not start the run. |

## Not mapped after this exercise (summary)

1. Footer "Connected" (removed from the app).
2. Tile "Inventory turns".
3. Simulation "Iterations" input and its "Sim iterations" tile (nearest: `n_sims`, research-only).
4. Simulation "Segment scope" (A / B / A+B); the A/B/C/D segment model itself (data has 6 clusters, plus 3 `segment` values in `sku_master`).
5. Per-SKU alpha-drift flags and tolerance thresholds ("3 SKUs flagged", "Review flags (3)"); only a cluster-level proxy exists.
6. Real progress percentage for runs.
7. A launch button for a free-form demand scenario on the simulation form. Scenarios are created through the scenario agents chat, which needs a person's approval before `run_scenario`.
8. "Apply service levels to MEIO".

Partial: Cost-minimisation and Fill-rate-target methods, holding/shortage inputs for non-newsvendor methods, average service level, Run all (MEIO + Twin only; excludes segmentation, the alpha check and the research sweeps).

## Repo inconsistencies found while mapping

Resolved since the first mapping:
- `scenario_config.json` (`meio.parameters.clusters`) now reads the v5 output, `output datasets/segmentation/sku_clusters.csv`, and the app reads the v5 evidence (`segment_alpha_v5_real_data_summary.csv`, `service_level_v5_real_data_summary.csv`), so the Flagship Promo-Driven -> Flagship Core alias is gone.
- The config and the app both use `final_submission_optimized/` now, so the app needs `polars`.

Still open:
- `polars` is not in `App/requirements.txt` (or `Agents/requirements.txt`), and the scenario agents chat also needs the packages in `Agents/requirements.txt`.
- `input datasets/` now holds CSV copies of the tables `segmentation_v5.py` reads, so the `App/stage_csv.py` staging step is no longer needed (it still runs).
- `run_meio_then_twin.py` clears `output datasets/projections/<scenario>/<run>`, but `digital_twin_fast.py` writes the Twin run to `output datasets/<scenario>/<run>`. "Run all" and "MEIO + Digital Twin" therefore clear only the MEIO folder; the Twin run is overwritten in place.
- The mockup file `supply_brain_meio_mockup.html` is not in the repository.
