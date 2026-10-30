# Supply Brain

Supply Brain is a synthetic consumer packaged goods supply-chain decision system. It combines a multi-echelon digital twin, SKU segmentation, and multi-echelon inventory optimization (MEIO) so inventory policies can be tested against the same network physics that generated the historical data.

## Quick start

You need Python 3.11 or newer ([python.org/downloads](https://www.python.org/downloads/)). One command downloads
the project (about 500 MB) into a `SupplyBrain` folder in your home directory and installs everything: the front end,
models, Digital Twin and scenario agents.

**macOS / Linux** (Terminal):

```bash
curl -fsSL https://raw.githubusercontent.com/shivanginiaggarwal13-bit/Capstone/installer/installer/bootstrap.sh | bash
```

**Windows** (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/shivanginiaggarwal13-bit/Capstone/installer/installer/bootstrap.ps1 | iex"
```

Then start the app (it opens in your browser):

```bash
~/SupplyBrain/installer/run_app.sh          # Windows: %USERPROFILE%\SupplyBrain\installer\run_app.bat
```

Already have the code (cloned or downloaded)? Run `./installer/install.sh` (Windows: `installer\install.bat`) from the
project folder instead.

Full instructions, options and troubleshooting: [INSTALL.md](INSTALL.md).

The project is designed around one practical question:

> Where should inventory buffers sit, and how much protection should each SKU receive, when value, demand uncertainty, forecast quality, and replenishment risk differ across the portfolio?

The dataset is synthetic. Its structure and operating tensions are intended to be realistic; its exact numbers are not claims about a real company.

## Project map

```text
supply_brain_generator_v5.py            Canonical dataset generator (v3/v4 kept for history)
run_meio_then_twin.py                   Pipeline runner: MEIO policy build, then Digital Twin scoring
scenario_config.json                    Shared scenario config the pipeline, App and agents read

input datasets/                         Past and givens (26 tables, CSV + parquet copies)
  masters, orders, forecasts, inventory, receipts, production, scenarios
  DATASET_DOCUMENTATION.md              Table dictionary and generation assumptions

models/
  twin/digital_twin.py                  Canonical discrete-event digital twin (CSV in/out)
  twin/scenario_engine.py               Demand-surge scenario hook for the canonical twin
  scenario.py                           Scenario-config loading and SCENARIO_####_Name folder naming
  overrides/                            Override engine (19 measures) that MEIO and the fast twin apply
  MEIO/MEIO_Weekly/
    final_submission_optimized/         FINAL MEIO model (Clark-Scarf v2) + digital_twin_fast.py,
                                          the twin the pipeline actually runs
    final_submission/                   Original (unoptimized) copy of the final model and its results/
    alt_models/                         Alternative models explored (Clark-Scarf v1, GSM, bandits, sim-optimize)
    meio_optimizer_weekly.py ...        Earlier MEIO Weekly tool and its backtests
  MEIO/meio_optimizer.py                Legacy annual MEIO prototype (superseded)

Clustering & Segmentation/segmentation_v5/
  segmentation_v5.py                    Canonical segmentation runner (v2-v4 kept for history)
  shap_analysis_v5.py                   Cluster-assignment explainability

App/streamlit_app.py                    Streamlit front end (see App/BUTTON_MAPPING.md)
Agents/                                 LangGraph scenario agents that author overrides (see Agents/README.md)

output datasets/
  history/                              Historical replay outputs, forward_policy and opening state
  projections/baseline/                 Forward baseline projection (canonical twin)
  segmentation/                         Canonical segmentation reports and artifacts
  MEIO/SCENARIO_####_Name/              MEIO policies per scenario (2026-W01 weekly, rolling_2026),
                                          each with its meio_parameters.json receipt
  MEIO/2026-W01/, MEIO/rolling_2026/    Older MEIO Weekly outputs (pre-scenario folder layout)
  MEIO artifacts/SCENARIO_0002_.../results/
                                        Tuning evidence (run_all.py) that sets the live MEIO parameters
  SCENARIO_####_Name/<run>/             Twin runs per scenario (e.g. SCENARIO_0002_.../meio_policy)
  overrides/SCENARIO_####_Name/         Agent-built scenario configs, cards, approvals, override receipts
  agent output/                         Result subsets written by final_submission/ research scripts
  OUTPUT_DOCUMENTATION.md               Forward-output schema

Docs/, Model Documentation/             Charter, early data plan, per-model write-ups
Presentation/                           Status-update deck and data-foundation page
Strategy-In-Progress/, Obsidian-Notes/  Exploratory strategy notes (multi-armed bandit track)
ASSUMPTIONS_AUDIT.md                    Line-by-line assumptions audit of generator, segmentation, MEIO, twin
README.md                               This guide
SCHEMA.md                               Time split, table conventions, and joins
```

## Prerequisites

Use Python 3.11 or later with the project environment containing:

- `numpy`, `pandas`, `scipy`, `pyarrow` (parquet)
- `polars` (the final MEIO model and `digital_twin_fast.py`)
- `scikit-learn`
- `matplotlib`
- `xgboost` (SHAP surrogate)
- `streamlit`, `altair` (the App)

Install the common packages if needed:

```bash
python -m pip install numpy pandas scipy pyarrow polars scikit-learn matplotlib xgboost
python -m pip install -r App/requirements.txt       # Streamlit app
python -m pip install -r Agents/requirements.txt    # LangGraph agents
```

Run commands from the project root, or use absolute paths when working from another directory. Paths containing spaces must be quoted in PowerShell.

## Data lifecycle

The boundary between the two data folders is time:

- `input datasets/` contains the past and the givens: network masters, product economics, commercial history, published forecasts, production, receipts, and historical inventory.
- `output datasets/` contains generated forward views and analytical deliverables.

The generator derives period labels and the history/forward split from its horizon constants (`START_YEAR = 2023`, 52-week years, a 104-week burn-in plus a 52-week evaluated year, then a 52-week forward horizon). The current dataset contains 156 historical weeks (2023-W01..2025-W52) and 52 forward weeks (2026-W01..2026-W52). Some year-specific values are still literal (for example `YEAR_TREND` is keyed by 2023-2026), so changing `START_YEAR` is not a one-line change.

The generator writes CSV. The repository also ships a `.parquet` copy of every input table, which the final MEIO pipeline and fast twin read. `input datasets/orders.csv` and `input datasets/inventory.csv` are Git LFS pointer files (see `.gitattributes`); without `git lfs pull` only their parquet copies contain data, so scripts that read those two CSVs (the canonical twin in history mode, `segmentation_v5.py`) fail on a plain clone.

## Rebuild the dataset

To regenerate the full synthetic world, historical replay, validation, and documentation:

```bash
python supply_brain_generator_v5.py --stage all --run-name history
```

Useful independent stages (`--stage` accepts `world`, `history` or `all`; the default is `all`):

```bash
python supply_brain_generator_v5.py --stage world --run-name history
python supply_brain_generator_v5.py --stage history --run-name history
python supply_brain_generator_v5.py --stage history --run-name history --skip-docs
```

The history stage creates the historical inventory record in `input datasets/` and forward-start artifacts in `output datasets/history/`. Validation and documentation (`DATASET_DOCUMENTATION.md`, `SCHEMA.md`, `OUTPUT_DOCUMENTATION.md`) run at the end of the `history`/`all` stages unless `--skip-docs` is passed; there is no separate `document` stage. `--seed` (default 2026) seeds only the history-mode twin run; the world-building seeds are fixed in code.

## Run the digital twin

The twin is the shared physics engine. It simulates daily events across plant, plant DC, regional DC, and customer DC echelons, then writes weekly summaries.

There are two implementations:

- `models/twin/digital_twin.py` is the canonical twin. The generator uses it for the history replay, and it reads and writes CSV.
- `models/MEIO/MEIO_Weekly/final_submission_optimized/digital_twin_fast.py` is a polars/parquet port with the same core physics. It adds the override engine (`models/overrides/`) for scenario arms, and it is the twin that `run_meio_then_twin.py` runs (`digital_twin.entrypoint` in `scenario_config.json`).

Run the baseline forward projection with the canonical twin:

```bash
python models/twin/digital_twin.py --mode forward --run-name baseline \
  --policy "output datasets/history/forward_policy.csv" \
  --state "output datasets/history/opening_position.csv"
```

The baseline output is written to:

```text
output datasets/projections/baseline/
```

Score another policy from the identical opening state (replace the placeholder with your policy file):

```bash
python models/twin/digital_twin.py --mode forward --run-name my_policy \
  --policy "<path/to/policy.csv>" \
  --state "output datasets/history/opening_position.csv"
```

Without `--scenario-config`, a run named `baseline` goes to `output datasets/projections/baseline/` and any other run name to `output datasets/<run-name>/`. With `--scenario-config`, the run goes to a standardized `output datasets/SCENARIO_####_Name/` folder. Use `--out-root` to place a run in another output namespace when needed.

The policy contract is one table with these required columns:

```text
dc_id,sku_id,safety_stock,reorder_point,order_up_to_level
```

Extra policy columns are ignored, except an optional `period` column: when it is present, the policy is time-varying (one row per week), which is how the 52-week MEIO rolling policy is scored. The canonical twin reads the policy as CSV; the fast twin accepts CSV or parquet.

## Run MEIO optimization

The final MEIO model is **Clark-Scarf v2** in `models/MEIO/MEIO_Weekly/final_submission_optimized/` (start with that folder's `README.md`). The pipeline runner builds the policy and then scores it in the fast twin, reading every path and parameter from `scenario_config.json`:

```bash
python run_meio_then_twin.py                 # uses ./scenario_config.json
python run_meio_then_twin.py --yes           # skip the prompt before clearing this scenario's outputs
python run_meio_then_twin.py --scenario-config "output datasets/overrides/SCENARIO_0003_<Name>/scenario_config.json"
```

What it does with the default config (scenario 2, `MEIO_CLARK_SCARF_V2`):

1. `meio.runner_entrypoint` (`run_weekly_tools.py`) runs `meio_optimizer_weekly.py` and then `roll_forward_policy.py` as of `meio.as_of_week` (`2025-W52`). They write `output datasets/MEIO/SCENARIO_0002_MEIO_CLARK_SCARF_V2/2026-W01/` and `.../rolling_2026/meio_policy_rolling.parquet`, a 52-week time-varying policy for 2026-W01..2026-W52.
2. `digital_twin.entrypoint` (`digital_twin_fast.py`) scores the newest `meio_policy_rolling.parquet` in forward mode (seed 2026) from `output datasets/history/opening_position.csv`. It writes to `output datasets/SCENARIO_0002_MEIO_CLARK_SCARF_V2/meio_policy/`.

The runner's clear step targets `output datasets/projections/<scenario>/meio_policy`, which the fast twin does not write to. In practice the twin run folder is overwritten in place rather than cleared first.

The MEIO parameters are not set by hand. `meio_parameters.py` resolves them from the tuning evidence listed in `meio.parameters.evidence` (`output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/`, falling back to `final_submission/results/`). It writes a receipt next to each policy, `meio_parameters.json`. The current receipt (`output datasets/MEIO/SCENARIO_0002_MEIO_CLARK_SCARF_V2/rolling_2026/meio_parameters.json`) resolves to:

| Parameter | Live value |
|---|---|
| model | `clark_scarf_v2` |
| `alpha` (RDC pooling factor) | 0.6 portfolio default |
| per-cluster `alpha` | Flagship Core 1.0, Mid-Tier Core 0.6, Long-Tail Erratic 1.0, Long-Tail Erratic 2 1.0, Long-Tail Erratic 3 0.8, Long-Tail Supply-Risk 1.0 (`alpha_by_sku` overrides 367 SKUs) |
| `netting_discount` | 0.0 |
| DDLT method | `quadrature` (`n_sims` 4,000 only matters for Monte Carlo) |
| service levels | per-SKU `safety_factor_z` from the v5 `output datasets/segmentation/sku_clusters.csv` |

The earlier write-ups recommended `alpha = 0.2`, which is also the code's reference value. The receipt above is what the pipeline actually uses. `run_all.py` in the same folder regenerates the tuning evidence; it is a multi-hour research run and is not part of the pipeline.

`models/MEIO/meio_optimizer.py` is the legacy annual prototype. It has no command-line interface, uses paths relative to `models/MEIO/`, reads `inventory.csv`, and writes `models/MEIO/output/meio_recommendations.csv`, which is not in the twin's policy contract. It is kept only for history.

A policy experiment should always use the same `--state` file as the baseline so the comparison measures the policy decision rather than a different starting position.

## Scenarios, overrides and agents

Scenario changes (demand, supply, service level, inventory, policy, economics) are expressed as `changes.overrides` in a scenario config and applied by `models/overrides/` on both the MEIO (plan) side and the fast-twin (actual) side. The override engine supports 19 measures, and `override.schema.json` is the contract. The LangGraph agents in `Agents/` turn a plain-English what-if into such a config under `output datasets/overrides/SCENARIO_####_Name/`. A person must approve the scenario before it runs:

```bash
python -m Agents.tools readiness
python -m Agents.runtime "What if Southeast beverage demand rises 25% in July?"
python -m Agents.tools approve SCENARIO_0003_<Name> --by "Planner Name"
```

The default model profile (`default_profile: nvidia` in `Agents/runtime/profiles.yaml`) needs `NVIDIA_API_KEY`. `--profile desktop` routes model calls through Claude Desktop and needs no key. See `Agents/README.md` for setup and the other profiles.

## Run the app

```bash
python -m streamlit run App/streamlit_app.py
```

The app has pages for the dashboard, SKU segmentation, suggested service levels, weekly MEIO allocation, the periodic alpha check and the simulation centre. Its "Run all" button runs segmentation (in the sandbox), then the alpha check (`App/alpha_check.py`), then `run_meio_then_twin.py --yes`. Segmentation and simulation launches write to the `App/runs/` sandbox unless you tick the box to overwrite canonical output. `App/BUTTON_MAPPING.md` maps every button to the script or file behind it.

## Run segmentation

The retained segmentation implementation is v5. It uses eight surviving features in four balanced business blocks:

- VALUE: annual margin and margin percentage
- VARIABILITY: base-demand CV and promotional volume share
- FORECASTABILITY: baseline and promo-lift WMAPE
- SUPPLY_RISK: mean lead time and lead-time CV

Perishability features are excluded by design for this dataset. `otd_pct` is pruned when it is near-constant, as it is in the current run. k is chosen from 4..8 on subsample stability; the current run selects k = 6 (`report_artifacts/run_metadata.json`).

Run the canonical segmentation:

```bash
python "Clustering & Segmentation/segmentation_v5/segmentation_v5.py"
```

The script reads CSV inputs (`--input`, default `input datasets/`) and writes to `--outdir` (default `output datasets/segmentation/`). On a clone without Git LFS, `orders.csv` and `inventory.csv` are pointer files, so first stage CSV copies from the parquet tables and point the script at them:

```bash
python App/stage_csv.py
python "Clustering & Segmentation/segmentation_v5/segmentation_v5.py" --input App/runs/csv_input
```

The MEIO model reads `safety_factor_z` from `output datasets/segmentation/sku_clusters.csv`, so re-running segmentation in place changes the service levels of the next MEIO build.

All generated machine-readable files and charts go to:

```text
output datasets/segmentation/report_artifacts/
```

The business report remains at:

```text
output datasets/segmentation/segmentation_report.md
```

## Run SHAP explainability

Clustering is unsupervised, so the SHAP analysis fits a multiclass XGBoost surrogate to reproduce the final cluster labels. Native Tree SHAP then explains the surrogate's cluster boundaries. These values are model explanations, not causal effects.

```bash
python "Clustering & Segmentation/segmentation_v5/shap_analysis_v5.py"
```

The SHAP report is:

```text
output datasets/segmentation/segmentation_shap_report.md
```

The SHAP CSVs, metrics JSON, and charts are in `output datasets/segmentation/report_artifacts/`.

## Important outputs

| Output | Purpose |
|---|---|
| `segmentation_report.md` | Business-facing segment interpretation and policy guidance |
| `segmentation_shap_report.md` | Explainability report for cluster assignments |
| `sku_clusters.csv` | Optimization-facing SKU-level features, cluster, archetype, CSL, case-fill, and safety factor |
| `cluster_service_levels.csv` | Optimization-facing cluster-level value and service-policy roll-up |
| `report_artifacts/cluster_k_diagnostics.csv` | Inertia, silhouette, and subsample stability (ARI over 80% subsamples) for k=4..8 |
| `report_artifacts/cluster_anova.csv` | Feature significance across the final clusters |
| `report_artifacts/run_metadata.json` | Machine-readable feature, pruning, and k-selection record |
| `report_artifacts/shap_global_importance.csv` | Portfolio-wide SHAP feature ranking |
| `report_artifacts/shap_cluster_importance.csv` | Cluster-specific SHAP feature importance |
| `projections/baseline/run_kpis.json` | Baseline forward scenario KPIs |
| `history/opening_position.csv` | Common opening state for policy comparisons |
| `MEIO/SCENARIO_0002_MEIO_CLARK_SCARF_V2/rolling_2026/meio_policy_rolling.parquet` | Final MEIO 52-week policy the pipeline scores |
| `MEIO/SCENARIO_0002_MEIO_CLARK_SCARF_V2/rolling_2026/meio_parameters.json` | Receipt of the MEIO parameters actually used and why |
| `SCENARIO_0002_MEIO_CLARK_SCARF_V2/meio_policy/run_kpis.json` | Fast-twin KPIs of the final MEIO policy |

## Validation checklist

After a rebuild or code change:

1. Confirm the generator prints `validation: all checks passed` and review `output datasets/history/acceptance.csv`. Failed checks are printed, not raised, so a run can finish with failures.
2. Run segmentation and confirm `sku_clusters.csv` has 500 unique SKUs.
3. Confirm cluster revenue and margin shares sum to 1.0.
4. Run SHAP and confirm the surrogate metrics are written to `shap_model_metrics.json`.
5. Run the baseline twin and inspect `run_kpis.json`, `service_summary.csv`, and `cost_summary.csv`.
6. After a MEIO build, read the policy folder's `meio_parameters.json` to confirm which parameters were used.
7. Keep baseline and optimized runs on the same opening state.

## Design notes

- `period` is an ISO week label such as `2025-W52`.
- `sku_id` identifies a product; `dc_id` identifies a stocking location.
- Monetary fields use the `_usd` suffix; percentages and multipliers are rates.
- The plant is production-only; the plant DC is the first inventory-holding echelon.
- Customer and internal replenishment demand/shortfall are kept separate because their fulfillment rules differ.
- The synthetic dataset uses multi-year shelf life, so obsolescence is not a meaningful result in the current horizon.

For the full table dictionary and time-boundary rules, see [SCHEMA.md](SCHEMA.md). For twin artifact schemas, see [output datasets/OUTPUT_DOCUMENTATION.md](output%20datasets/OUTPUT_DOCUMENTATION.md).
