# Supply Brain

Supply Brain is a synthetic consumer packaged goods supply-chain decision system. It combines a multi-echelon digital twin, SKU segmentation, and multi-echelon inventory optimization (MEIO) so inventory policies can be tested against the same network physics that generated the historical data.

The project is designed around one practical question:

> Where should inventory buffers sit, and how much protection should each SKU receive, when value, demand uncertainty, forecast quality, and replenishment risk differ across the portfolio?

The dataset is synthetic. Its structure and operating tensions are intended to be realistic; its exact numbers are not claims about a real company.

## Project map

```text
input datasets/                         Past and givens
  masters, orders, forecasts, inventory, receipts, production

models/twin/digital_twin.py             Shared discrete-event simulation
models/MEIO/meio_optimizer.py           MEIO policy optimization

Clustering & Segmentation/segmentation_v5/
  segmentation_v5.py                    Canonical segmentation runner
  shap_analysis_v5.py                   Cluster-assignment explainability

output datasets/
  history/                               Historical replay outputs and opening state
  projections/baseline/                  Forward baseline projection
  projections/SCENARIO_####_Name/        Digital Twin scenario runs
  MEIO/Scenario/SCENARIO_####_Name/      MEIO policies and validation outputs
  segmentation/                          Canonical segmentation reports and artifacts
  OUTPUT_DOCUMENTATION.md               Forward-output schema

README.md                               This guide
SCHEMA.md                               Time split, table conventions, and joins
```

## Prerequisites

Use Python 3.11 or later with the project environment containing:

- `numpy`, `pandas`, `scipy`
- `scikit-learn`
- `matplotlib`
- `xgboost`

Install the common packages if needed:

```bash
python -m pip install numpy pandas scipy scikit-learn matplotlib xgboost
```

Run commands from the project root, or use absolute paths when working from another directory. Paths containing spaces must be quoted in PowerShell.

## Shared scenario runs

Create or edit the single root-level `scenario_config.json` before rerunning a
scenario. The same file drives MEIO Weekly and the Digital Twin; both use its
`scenario_number` and `scenario_name` to derive the identical folder name.

```bash
python models/MEIO/MEIO_Weekly/final_submission/run_weekly_tools.py
python models/twin/digital_twin.py
```

To build the MEIO policy across the entire forward horizon and immediately score that policy in the
Digital Twin, use the config-driven runner:

```powershell
python run_meio_then_twin.py
```

The runner invokes the configured `meio.runner_entrypoint`, which runs the
MEIO production tools, then finds the generated
`meio_policy_rolling.parquet` under the configured MEIO policy scenario folder, and
passes that time-varying policy to the configured Digital Twin entrypoint. It
does not run the research artefact sweeps. Those are separate and configured
under `artifacts.entrypoint`, with their exact scenario destination in
`artifacts.scenario`:

```powershell
python models/MEIO/MEIO_Weekly/final_submission/run_all.py --steps 5,6,7
```

If the scenario already
has outputs, it asks for approval before clearing only that scenario; use
`--yes` for an explicit non-interactive approval.

MEIO policy outputs are written below
`output datasets/MEIO/SCENARIO_####_Name/`; research and validation artefacts
are written below
`output datasets/MEIO artifacts/SCENARIO_####_Name/`. Twin artefacts are
written below `output datasets/projections/SCENARIO_####_Name/`. Each scenario
folder receives a copy of `scenario_config.json` for auditability. The config
also names the model entrypoints, data directory, policy, state, and output
roots, so reruns do not require path arguments.

### Regenerate the baseline

Run these commands from the project root in PowerShell. The first command is
only needed when the input/history dataset itself must be rebuilt. The second
clears generated MEIO and Twin projections without touching inputs or history.
The final command reads the policy, opening state, seed, and output locations
from `scenario_config.json`.

```powershell
python supply_brain_generator_v5.py --stage all --run-name history
Remove-Item -LiteralPath 'output datasets/MEIO','output datasets/projections' -Recurse -Force -ErrorAction SilentlyContinue
python models/twin/digital_twin.py
```

With the checked-in config, the regenerated baseline is written to:

```text
output datasets/projections/SCENARIO_0002_MEIO_CLARK_SCARF_V2/baseline/
```

The MEIO weekly tools use the same scenario config and write to:

```powershell
python models/MEIO/MEIO_Weekly/Archive/run_weekly_tools.py
```

Their policy output is under
`output datasets/MEIO/SCENARIO_0002_MEIO_CLARK_SCARF_V2/`; alpha sweeps,
validation reports, and supporting artefacts are under
`output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/`.

## Data lifecycle

The boundary between the two data folders is time:

- `input datasets/` contains the past and the givens: network masters, product economics, commercial history, published forecasts, production, receipts, and historical inventory.
- `output datasets/` contains generated forward views and analytical deliverables.

The generator derives the history/forward boundary from the order history rather than hardcoding a calendar year. The current dataset contains 156 historical weeks and 52 forward weeks.

## Rebuild the dataset

To regenerate the full synthetic world, historical replay, validation, and documentation:

```bash
python supply_brain_generator_v5.py --stage all --run-name history
```

Useful independent stages:

```bash
python supply_brain_generator_v5.py --stage world --run-name history
python supply_brain_generator_v5.py --stage history --run-name history
python supply_brain_generator_v5.py --stage document --run-name history
```

The history stage creates the historical inventory record in `input datasets/` and forward-start artifacts in `output datasets/history/`.

## Run the digital twin

The twin is the shared physics engine. It simulates daily events across plant, plant DC, regional DC, and customer DC echelons, then writes weekly summaries.

Run the baseline forward projection using the scenario config:

```bash
python models/twin/digital_twin.py
```

The baseline output is written to:

```text
output datasets/projections/SCENARIO_0002_MEIO_CLARK_SCARF_V2/baseline/
```

Score another policy from the identical opening state:

```bash
python models/twin/digital_twin.py --mode forward --run-name meio_v2 \
  --policy "output datasets/meio_v2/policy.parquet" \
  --state "output datasets/history/opening_position.parquet"
```

The policy contract is one table (Parquet; a CSV is accepted too) with these required columns:

```text
dc_id,sku_id,safety_stock,reorder_point,order_up_to_level
```

Extra policy columns are ignored. Use `--out-root` to place a run in another output namespace when needed.

## Run MEIO optimization

The optimizer lives at `models/MEIO/meio_optimizer.py`. Its output is a policy table that can be passed directly to the digital twin through `--policy`.

Before running an optimization experiment, confirm the optimizer's command-line help and output location:

```bash
python models/MEIO/meio_optimizer.py --help
```

A policy experiment should always use the same `--state` file as the baseline so the comparison measures the policy decision rather than a different starting position.

## Run segmentation

The retained segmentation implementation is v5. It uses eight surviving features in four balanced business blocks:

- VALUE: annual margin and margin percentage
- VARIABILITY: base-demand CV and promotional volume share
- FORECASTABILITY: baseline and promo-lift WMAPE
- SUPPLY_RISK: mean lead time and lead-time CV

Perishability features are excluded by design for this dataset. `otd_pct` is pruned when it is near-constant.

Run the canonical segmentation:

```bash
python "Clustering & Segmentation/segmentation_v5/segmentation_v5.py"
```

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
| `report_artifacts/cluster_k_diagnostics.csv` | Inertia, silhouette, and bootstrap stability for k=4..8 |
| `report_artifacts/cluster_anova.csv` | Feature significance across the final clusters |
| `report_artifacts/run_metadata.json` | Machine-readable feature, pruning, and k-selection record |
| `report_artifacts/shap_global_importance.csv` | Portfolio-wide SHAP feature ranking |
| `report_artifacts/shap_cluster_importance.csv` | Cluster-specific SHAP feature importance |
| `projections/baseline/run_kpis.json` | Baseline forward scenario KPIs |
| `history/opening_position.parquet` | Common opening state for policy comparisons |

## Validation checklist

After a rebuild or code change:

1. Confirm the generator completes its validation checks.
2. Run segmentation and confirm `sku_clusters.csv` has 500 unique SKUs.
3. Confirm cluster revenue and margin shares sum to 1.0.
4. Run SHAP and confirm the surrogate metrics are written to `shap_model_metrics.json`.
5. Run the baseline twin and inspect `run_kpis.json`, `service_summary.csv`, and `cost_summary.csv`.
6. Keep baseline and optimized runs on the same opening state.

## Design notes

- `period` is an ISO week label such as `2025-W52`.
- `sku_id` identifies a product; `dc_id` identifies a stocking location.
- Monetary fields use the `_usd` suffix; percentages and multipliers are rates.
- The plant is production-only; the plant DC is the first inventory-holding echelon.
- Customer and internal replenishment demand/shortfall are kept separate because their fulfillment rules differ.
- The synthetic dataset uses multi-year shelf life, so obsolescence is not a meaningful result in the current horizon.

For the full table dictionary and time-boundary rules, see [SCHEMA.md](SCHEMA.md). For twin artifact schemas, see [output datasets/OUTPUT_DOCUMENTATION.md](output%20datasets/OUTPUT_DOCUMENTATION.md).
