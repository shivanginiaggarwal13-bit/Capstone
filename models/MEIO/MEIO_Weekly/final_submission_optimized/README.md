> **Optimized copy.** This folder is a duplicate of `../final_submission/` with the same
> method, parameters, CLIs and output files, rebuilt for speed (polars, a dense cached
> demand panel, one Monte Carlo pass shared by every variant on an RNG stream, an
> in-process vectorised digital twin, and all `run_all.py` steps on one prioritised
> process pool). Its equivalence tests check that results are bit-identical to the
> original on the slices they cover. As of 2026-10-23 the twin equivalence test fails
> before comparing anything (see OPTIMIZATION.md "Exactness"). See **OPTIMIZATION.md**
> for what changed, how it was verified, disk requirements (`--lean-twin-outputs`) and
> the one step (`scenario_test.py`) that needed a fix to run. Timings quoted below
> ("~9-10 hours") describe the original folder.
>
> **Where `results/` points.** This folder has no `results/` directory. Its `run_all.py`
> writes to `<artifacts.scenario>/results/`, i.e. `output datasets/MEIO artifacts/
> SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/` with the root `scenario_config.json`. The
> original's committed copy (CSVs, charts and the narrative `*_report.md` files) is
> `../final_submission/results/`. The two folders hold different vintages of several
> files, so this README says which one each number comes from.
>
> **Note (2026-10-23): the live configuration is not alpha=0.2.** The pipeline's
> `meio_parameters.resolve()` currently gives Clark-Scarf v2 with alpha=0.6,
> netting_discount=0.0, `ddlt="quadrature"` and per-cluster alpha on 367 SKUs (see "What
> it gives today" below). The alpha=0.2 sections that follow describe the configuration
> this project originally validated. That headline has not been re-run at the live
> configuration.

# MEIO — Final Submission: Clark-Scarf v2

Start here. This folder has everything behind the model-building recommendation;
the broader exploration history (5 models, 10 tuning rounds, bugs found and
fixed) lives in `..` and `../alt_models/` for anyone who wants the full process,
but isn't required reading to evaluate the recommendation itself.

**To reproduce everything end to end** (not required — `results/` already
has the computed evidence): `python run_all.py`, ~9-10 hours, meant for an
overnight/unattended run. See "Files" below for what it runs and in what
order.

## The recommendation (as originally validated)

**Clark-Scarf v2 at alpha=0.2, netting_discount=0.0**, a continuous-pooling-factor,
Monte Carlo-DDLT echelon model (`clark_scarf_v2.py`), chosen over the
hand-tuned MEIO Weekly tool because it holds up better against **real
replayed 2025 demand**, not just synthetic noise — and because its core
mechanism (deciding how much safety stock an RDC pools with the CDCs it
feeds) is a direct answer to the charter's own ask: decide not just how much
buffer to hold, but **where**.

`netting_discount=0.0` is a fix validated this session (see "Netting-discount
fix" below) — it removes the original model's assumption that an RDC's
pooled safety stock can be credited against what its CDCs already hold, and
it wins on every axis tested, 5/5 seeds, with no tradeoff found. The table
below reflects it; the original `netting_discount=1.0` figures (what shipped
before this fix) are in that section for comparison.

| | Actual (twin, baseline policy) | MEIO Weekly | **Clark-Scarf v2, alpha=0.2** |
|---|---|---|---|
| Customer fill rate, real 2025 demand | 93.08% | 93.00% (−0.08pp, single seed) | **94.51% (+1.43pp) single seed; +1.52pp 5-seed mean** |
| Internal replenishment fill | 93.21% | 88.86% (−4.35pp) | **82.38% single seed; 82.32% 5-seed mean (still degraded)** |
| Total landed cost delta | — | — | **+$1.67M single seed; +$1.67M 5-seed mean (vs. +$1.79M at nd=1.0)** |

Sources (all in `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/`):
single seed from `headline_clark_scarf_v2_vs_actual.csv` / `headline_cost_summary.csv`
(identical copies are in `../final_submission/results/`); 5-seed means from
`netting_discount_multiseed.csv`; MEIO Weekly from `meio_weekly_vs_actual.csv`.
"Actual" is the twin replaying 2025 orders under the dataset's `baseline_policy`
(`validate_real_data.actual_arm`), not observed outcomes.

An earlier vintage of this table read 94.83% (+1.75pp) / 82.11% / +$1.83M for
Clark-Scarf v2 and 93.23% (+0.15pp) / −4.60pp for MEIO Weekly. Those figures come from
`../final_submission/results/netting_discount_multiseed.csv` and
`../final_submission/results/meio_weekly_vs_actual.csv`, which predate the
cluster-based service-level default. `../final_submission/results/headline_report.md`
shows the netting 1.0 run (94.27% / 81.99% / +$2.01M).
MEIO Weekly's own cost breakdown, computed separately, is in
`../final_submission/results/meio_weekly_vs_actual_cost.csv` (old vintage; the current
`validate_real_data.py` stage writes only the fill table).

## How the tuning reaches the pipeline's policy

`run_all.py`'s steps write their evidence to `<artifacts.scenario>/results/`. The
pipeline (`run_meio_then_twin.py` → `run_weekly_tools.py` → `roll_forward_policy.py`
and `meio_optimizer_weekly.py`) builds the policy the Digital Twin runs. Before
`meio_parameters.py`, nothing connected the two: the pipeline sized stock with MEIO
Weekly, which has no alpha or netting parameter at all.

Now the shared `scenario_config.json` names the model and the rule in
`meio.parameters`, and `meio_parameters.resolve()` turns the evidence into the
parameters the policy is built with.

The config's `meio.parameters.evidence` lists two folders, searched in order, file by
file: `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results` (what
this folder's `run_all.py` writes) first, then `../final_submission/results`. A file
name found in the first folder shadows the same name in the second.

| Evidence file | Columns read | Run at | Feeds |
|---|---|---|---|
| `netting_discount_multiseed*.csv` | netting_discount, seed, fill_delta_pp, cost_delta_usd, alpha | `alpha` column in the current files (0.2 and 0.6); 0.2 assumed where the column is missing | `netting_discount`, then `alpha` |
| `multiseed_alpha_*.csv` | alpha, seed, fill_delta_pp, cost_delta_usd, netting_discount | `netting_discount` column in the current file (0.0); 1.0 assumed where the column is missing | `alpha`, only if it was run at the chosen netting |
| `segment_alpha_v5_real_data_multiseed.csv` | cluster, alpha, seed, own_fill_rate, portfolio_fill_rate, cost_delta_usd | v5's 6 clusters, netting 0.0, every other cluster held at alpha 0.6 (`CLUSTER_ALPHA_REFERENCE`) | per-SKU alpha by cluster |
| `service_level_v5_real_data_multiseed.csv` | cluster, csl, seed, own_fill_rate, portfolio_fill_rate, fill_delta_vs_actual_pp, cost_delta_usd | v5's 6 clusters, alpha 0.2 and netting 0.0 (assumed: the file has no setting columns) | per-SKU z by cluster |
| headline, alpha_sweep (single seed), meio_weekly_vs_actual, seasonal, segment_breakdown, scenario_*, and the v4 `segment_alpha_real_data_*` / `service_level_real_data_*` | — | — | evaluation only (not read by the resolver) |

**The "validated" rule.** A parameter moves off its reference only when the
evidence passes a paired test across at least 5 seeds: fill must improve by more
than two standard errors, and cost must not rise by more than two standard errors.
Evidence gathered at other settings doesn't count. The cluster sweeps are used only
once complete and run at the selected settings. The full rule is in
`meio_parameters.py`'s docstring.

**What it gives today** (`output datasets/MEIO/SCENARIO_0002_MEIO_CLARK_SCARF_V2/
rolling_2026/meio_parameters.json`; re-running `meio_parameters.resolve()` on
2026-10-23 gave the same receipt): Clark-Scarf v2 with **alpha 0.6, netting 0.0,
`ddlt="quadrature"`, per-cluster alpha on 367 SKUs**.
- Netting 0.0 beats 1.0 at alpha 0.2 on 5 paired seeds: +0.47 pp fill (SE 0.05) and
  −$128k cost (SE $19k).
- At netting 0.0, alpha 0.4 (+0.77 pp, −$131k) and alpha 0.6 (+1.17 pp, −$236k) both
  pass against 0.2. The larger gain wins, so alpha = 0.6. These rows come from
  `netting_discount_multiseed_alpha0_6.csv` and `multiseed_alpha_04_06.csv`, now
  re-run at netting 0.0.
- Per-cluster alpha (v5 sweep, complete: 36 of 36 combinations at 5 seeds): Flagship
  Core 1.0, Mid-Tier Core 0.6, Long-Tail Erratic 1.0, Long-Tail Erratic 2 1.0,
  Long-Tail Erratic 3 0.8, Long-Tail Supply-Risk 1.0. The 367 SKUs are those outside
  Mid-Tier Core (133 of 500), whose value equals the portfolio alpha. Each cluster was
  measured with the others held at 0.6. The six values were adopted together and
  never run jointly. Four of the six sit on the grid's 1.0 boundary.
- The cluster CSL ladder is **not** adopted. The v5 CSL sweep was run at alpha 0.2, not
  the selected 0.6. The receipt's wording, "segment ladder kept", names the gate's
  fallback. The z actually used for all 500 SKUs is still the v5 cluster ladder
  (1.881 / 1.717 / 1.282), which `meio_weekly_lib.z_values()` reads from
  `output datasets/segmentation/sku_clusters.csv`.

What has **not** been run is a headline or multi-seed check of this exact combination:
portfolio alpha 0.6 plus the per-cluster overrides, with the pipeline's quadrature DDLT
and its 52-week roll-forward from 2025-W52. The closest evidence is the portfolio-only
alpha 0.6 / netting 0.0 5-seed run. Against the twin's baseline-policy arm it gives
+2.69 pp customer fill, 84.64% internal replenishment fill and +$1.43M cost
(`netting_discount_multiseed_alpha0_6.csv`, means over 5 seeds), using Monte Carlo
walk-forward builds.

Each policy folder gets a `meio_parameters.json` listing every decision and the SHA-256
of every evidence file it used. Each sweep script now writes the settings it ran at as
columns; for files without them (the `../final_submission/results` copies, and
`service_level_v5_real_data_multiseed.csv`), the resolver records which settings it
assumed. `run_all.py` does **not** regenerate the two v5 files the per-cluster
decisions read. Its steps 6-7 run the v4 sweeps, `segment_alpha_v5_real_data.py` has
to be run separately, and `service_level_v5_real_data.py` exists only in
`../final_submission/` (it writes to `output datasets/agent output/`).

**Deterministic DDLT in the pipeline.** The pipeline runs Clark-Scarf v2 with
`ddlt="quadrature"`: the same Gamma-mixture DDLT percentile, computed by bisection over 64
equiprobable lead-time nodes instead of sampled. It is a different estimate from Monte
Carlo, not an identical one. `tests/test_meio_parameters.py::QuadratureTests` checks that
its median relative error against a 400,000-draw Monte Carlo is below 0.5% for safety stock
and 0.3% for reorder point. Its cycle stock is the analytic mean·(R + L̄), not a sample mean.
The comparison against 4,000 draws ("about 7× closer") was measured during development
and is not reproduced by a committed test.
- **Why:** Monte Carlo draws come from one shared stream. In a development check,
  bumping 20 CDC03 rows moved safety stock on 5,566 of 7,468 rows in all 23 DCs, by
  about 3% each (not reproduced by a committed test). That would make every
  scenario's prepared-vs-unprepared delta mostly noise.
- **With quadrature:** a row changes only when its own inputs do.
- **Research scripts:** they keep Monte Carlo, so their published results reproduce.

A config without `meio.parameters` (anything written before this, such as
SCENARIO_0003) still runs MEIO Weekly, and its rolling policy rebuilds byte-identical.

## Where the win actually comes from

On the synthetic 2026 basis, baseline is not weak on average — it's blind at
the seasonal peak. It sizes stock off one flat annual mean per SKU, with no
seasonal awareness at all: at the Seasonal segment's real peak week it hits
only **78.4% fill**, worse than its own trough week (92.7%). Both models
recover most of that gap by re-estimating demand per target week — MEIO
Weekly to 85.6% at the peak, Clark-Scarf v2 to 88.6% — but neither closes it
fully (`results/seasonal_peak_trough.csv`,
`results/chart_seasonal_peak_trough.png`). This means the portfolio-average
advantage reported everywhere else in this project (+1.2 to +2.6pp) is
diluted: the real edge is concentrated at specific hard weeks, not spread
evenly across the year, and is still incomplete even where it shows up.

## What's validated vs. what's exploratory

**Validated** (real replayed demand, multi-seed where it matters). "No lookahead" holds
for the weekly refit of bias, CV and level. It does not hold for the seasonal index,
which `meio_weekly_lib.build_seasonal_index()` builds from all of `orders.parquet`
(2023-W01..2025-W52), the test year included.
- The headline number above.
- Alpha=0.2 is *not* the best setting on real data. In the original folder's sweep
  (`../final_submission/results/alpha_sweep_real_data.csv`, netting 1.0 era) it was
  second-worst: fill peaked near alpha=0.6 (94.87%) and cost was lowest near alpha=0.4.
  The current re-run at netting 0.0 (`alpha_sweep_real_data.csv` in the artifacts
  results) is monotonic instead. Fill rises from 94.56% at 0.2 to 96.04% at 1.0, and
  cost is lowest at 1.0 (+$1.35M vs +$1.63M at 0.2). Both are single-seed sweeps that
  chain one RNG through all six alphas (`validate_real_data._stage_alpha_sweep`), so
  each alpha sees a different stream position.
- Alpha=0.4 and alpha=0.6 both hold up 5/5 seeds, in the original netting 1.0 run
  (`../final_submission/results/multiseed_report.md`) and in the netting 0.0 re-run
  (`multiseed_alpha_04_06.csv` in the artifacts results: +2.28 pp and +2.69 pp mean fill
  delta). **Both were chosen and tested on the same single real year (2025)**, so this is
  not a fresh-holdout confirmation. The project originally declined to adopt 0.6 over
  0.2 on this basis. `meio_parameters.py`'s "validated" rule now does adopt 0.6, on this
  same 2025 evidence (see "What it gives today").

**Exploratory / diagnostic only** — real signal, not yet production-grade validation.
(The single-seed synthetic diagnostic below has since been run on real demand with 5
seeds. `segment_alpha_real_data_summary.csv` holds the v4 clusters at netting 0.0, and
`segment_alpha_v5_real_data_summary.csv` holds the v5 clusters, both in the artifacts
results. Neither reproduces "long-tail wants less pooling". On real demand every
long-tail cluster's own fill rises with alpha: for example v4 Long-Tail Erratic goes
from 91.89% at 0.0 to 94.13% at 1.0, and v5 Long-Tail Erratic 2 from 83.94% to 95.55%.
The live per-cluster alphas are 1.0 / 0.8 for the long-tail clusters.)
- Per-segment alpha, using the Clustering & Segmentation deliverable's own
  4 clusters (Flagship Promo-Driven, Mid-Tier Core, Long-Tail Erratic,
  Long-Tail Supply-Risk — `segment_alpha_explore.py`). Finding: core
  clusters want *more* pooling (α≈0.4–0.6), long-tail clusters want *less*
  (α≈0.0–0.2) — a real, mechanistically-explainable reversal
  (`results/segment_alpha_sweep_own_fill.csv`,
  `results/chart_segment_alpha_own_fill.png`), **not yet multi-seed
  validated**. One seed, one synthetic year, and the four clusters were
  swept one at a time holding the others fixed — not guaranteed to find the
  joint optimum, since clusters share RDC/PDC capacity.
- Do not read specific per-cluster alpha values as a recommendation. The
  defensible takeaway is the *direction* (core vs. long-tail), not the four
  decimals.
- **Important complication, found when the cost side was added**
  (`results/segment_alpha_sweep_cost_by_cluster.csv`,
  `results/chart_segment_alpha_cost.png`): each cluster's *own* holding cost
  falls as its alpha rises, for all four clusters — but this is not a free
  efficiency gain. It's the direct accounting mirror of the netting
  mechanism already flagged above as the cause of internal-replenishment
  degradation (`ss_p = pooled_SS - downstream_CDC_SS`): more netting means
  the RDC holds less of its own stock, which shows up as lower holding cost
  *and* as the internal-fill risk in the same breath. Lost margin (the
  dollar cost of unmet demand) also favors higher alpha for the two core
  clusters and Long-Tail Erratic, which is in tension with Long-Tail
  Erratic's own *unit* fill rate favoring alpha=0.0 — plausibly because
  dollar-weighted and unit-weighted stockouts aren't hitting the same SKUs
  at different alphas, not a contradiction, but not yet explained either.
  For both long-tail clusters the absolute dollars involved are tiny
  (Long-Tail Supply-Risk's "best" alpha by lost margin varies by a few
  dollars total across the whole sweep) — treat the cost side as near-noise
  for the small clusters and read the own-fill-rate result as the more
  meaningful signal for them. **Net: "long-tail wants less pooling" was
  specifically a single-seed, synthetic-basis fill-rate finding, and the real-data
  5-seed sweeps did not reproduce it (see the note at the top of this list). Don't
  conflate it with a cost-saving claim — on cost alone, more pooling looks better for
  every cluster, for the same reason it's risky for internal supply.**

## Netting-discount fix (validated this session)

The pooled RDC endpoint's safety stock was `max(0, pooled_SS -
downstream_CDC_SS)` — crediting the RDC with 100% of what its CDCs already
hold, which assumes stock can move to wherever it's needed on no notice; the
twin's real weekly-review-plus-lead-time physics don't support that
instantly. `clark_scarf_v2.py::solve` now takes a `netting_discount`
parameter: `ss_p = max(0, pooled_SS - netting_discount * downstream_CDC_SS)`.
`netting_discount=1.0` reproduces the original behavior exactly (verified:
byte-identical formula, and a fresh re-run matched the saved headline
numbers to the decimal). `netting_discount=0.0` removes the netting
entirely.

Tested at alpha=0.2 (the chosen operating point) against real 2025 demand,
with two methodology passes:
- **First pass** (`netting_discount_sweep.py --alpha 0.2`) swept
  `netting_discount` in {0.0, 0.25, 0.5, 0.75, 1.0}. First attempt shared one
  RNG sequentially across all 5 builds and produced a misleading result —
  `internal_replen_fill_rate` turned out to be far more sensitive to
  RNG-stream position than customer fill rate (a 4pp swing from stream
  position alone, bigger than the entire netting_discount effect). Fixed by
  giving every value the same fresh seed instead (`netting_discount` only
  changes post-hoc arithmetic on Monte Carlo draws, never the draws
  themselves, so an identical seed isolates it as the only variable). With
  that fix, `../final_submission/results/netting_discount_sweep_alpha0_2.csv` shows a smooth,
  monotonic improvement as netting is reduced — 94.77%/82.14% fill at
  `nd=0.0` vs. 94.27%/81.99% at `nd=1.0` (the original), at *lower* cost.
  The current re-run (artifacts `netting_discount_sweep_alpha0_2.csv`) reads
  94.51%/82.38% at `nd=0.0` vs. 94.14%/82.35% at `nd=1.0`.
- **Multi-seed confirmation** (`netting_discount_sweep.py --multiseed`, 5
  seeds, `../final_submission/results/netting_discount_multiseed.csv`, the vintage before
  the cluster-based service level): `netting_discount=0.0`
  beats `netting_discount=1.0` on every axis, 5/5 seeds, tight std:
  customer-fill delta vs. actual +1.75pp (σ=0.07) vs. +1.22pp (σ=0.05);
  internal replenishment fill 82.11% (σ=0.0007) vs. 81.94% (σ=0.001); cost
  delta +$1.83M (σ=$51k) vs. +$1.95M (σ=$42k). The current re-run
  (artifacts `netting_discount_multiseed.csv`, which `meio_parameters` reads) still wins
  on every axis in 5/5 seeds, by a smaller margin: +1.52pp vs. +1.05pp, 82.32% vs.
  82.24%, +$1.67M vs. +$1.79M. No tradeoff found at this
  alpha — **`netting_discount=0.0` is adopted as part of the recommended
  configuration.**
- **At alpha=0.6** (`run_all.py` step 9, artifacts `netting_discount_multiseed_alpha0_6.csv`):
  `netting_discount=0.0` also beats 1.0 in 5/5 seeds, +2.69pp vs. +1.54pp fill delta and
  +$1.43M vs. +$1.85M cost.

Caveats this does *not* resolve: internal replenishment fill is improved
(82.32% vs. 82.24% at alpha=0.2 in the current 5-seed run) but still badly degraded
relative to actual (93.21%) — removing the netting discount is not a full fix, just a
real improvement. Tested at alpha=0.2 and 0.6 only, one real year, 5 seeds of one RNG
family — see "Next steps."

## Scenario testing (Feature 2, front-end agent — demand surge only so far)

`digital_twin.py --scenario-config` previously only saved a scenario's
`changes` object as metadata — it was never actually applied to anything
(confirmed directly in the code: `_scenario_execution()` only copied it
into the saved manifest). This session wired one scenario type —
**demand surge** — end to end, into both halves a "test this scenario"
front-end feature needs:

- **The simulator** (`models/twin/digital_twin.py` + new
  `scenario_engine.py`): `_build_demand()`'s output tensor is scaled for
  the affected region/nodes over the affected weeks, after the normal
  demand is built, before the twin runs. Region resolution reuses
  `customer_dc_master.parquet`/`regional_dc_master.parquet`'s own `region` column
  — no new data needed.
- **The policy generator** (`clark_scarf_v2.py::solve`): the SAME scaling
  is applied to `target_week_demand()`'s output (`target_mean`/
  `target_std`) for weeks inside the scenario's window, so a
  scenario-aware policy build actually recommends different stocking, not
  just simulates a fixed one differently.

Both are opt-in parameters, `None` by default — **regression safety
verified by direct comparison, not just review**: a baseline run (the
`ms_actual_s2026` arm from earlier in this session) was rerun after these
changes and matched every KPI to the decimal, and a Clark-Scarf v2 build
was independently rerun and matched its own prior run exactly too.

**Tested with the example scenario** (`scenario_config.example.json`: 25%
demand surge, Southeast, 4 weeks) via `scenario_test.py`, which runs three
arms — A) baseline policy/normal demand, B) baseline policy/scenario
demand ("if we don't prepare"), C) scenario-reoptimized policy/scenario
demand ("if we do"). The current run of this folder's `scenario_test.py` (artifacts
`scenario_DemandSurgeEast_comparison.csv`, alpha=0.2, netting 0.0, single seed):

| Arm | Customer fill | Total landed cost |
|---|---|---|
| A: baseline | 93.79% | $36.59M |
| B: unprepared (scenario hits, no prep) | 93.84% | $36.86M |
| C: prepared (scenario-aware policy) | 93.82% | $36.74M |

In this run the surge arm (B) has slightly *higher* fill than baseline. B→C (the value
of preparing) is −0.02pp fill at −$120K cost, and A→B is +0.05pp fill, +$263K cost.
The test therefore shows no measurable fill benefit from preparing. The recommended
stocking change (`scenario_DemandSurgeEast_stocking_by_dc.csv`) added +1.5–1.8% safety
stock at 5 of the 6 Southeast nodes (CDC03/04/05, RDC02/03). CDC06, also in the
Southeast but holding only ~134 units of safety stock, moved −0.15%. Every other node
stayed within ±0.22%, and every PDC at exactly 0.00%.

An earlier run by the original script, which the previous version of this table showed,
read 93.99% / 93.93% / 94.02% and $36.69M / $36.91M / $36.94M. In that run all 6
Southeast nodes rose +1.4–2.0%, B→C was +0.09pp, and A→B was −0.06pp and +$226K
(`output datasets/agent output/scenario_DemandSurgeEast_{comparison,stocking_by_dc}.csv`).
The scenario test is pinned to alpha=0.2 (`scenario_test.ALPHA`), not the live
configuration.

**Known gaps, stated directly:**
- **In `scenario_test.py`'s legacy path, only CDC/RDC policy is scenario-aware — PDC
  sizing is not wired.** Confirmed in the test run: every PDC showed exactly 0.00%
  stocking change. (The pipeline's override engine, `models/overrides`, does reach PDC
  sizing. See "Next steps" #7.)
- **Only demand surge is implemented in this legacy path.** The override engine now
  covers lead-time and capacity measures (see "Next steps" #8).
- **Single seed, synthetic basis, one scenario tested.** This is a
  working-example validation, not a multi-seed/multi-scenario one. The two runs
  above disagree in sign on the value of preparing, so treat it as "the mechanism
  moves stock to the right region," not as a measured benefit.

## Reactive arm: MEIO re-plans every week from the simulation (`reactive_twin.py`)

`prepared` sizes all 52 weeks up front with the scenario known; `unprepared` keeps today's
policy. Neither learns from the simulated year. `reactive` closes the loop inside one twin
run: week 1 uses today's policy, and before every later week's review MEIO re-sizes that
week as of the week before, after (1) writing every simulated week into its demand history
(bias factor, CV and seasonal level update) and (2) scaling the planning lane table by the
observed lead time of shipments that have fully settled (dispatched at least mean + 3 std
days ago, so slow draws are not censored out; a lane moves only beyond 5% and 3 standard
errors). The scenario's plan side is NOT applied -- MEIO only learns what it observes. Same
seed, so demand and lead-time draws match the other arms.

`Agents.tools run-job` runs it as a third arm and, once per baseline and seed, a control
`counterfactual_reactive_no_scenario` (the same weekly re-planning with no scenario).
Reactive minus unprepared mixes two things; the control separates them:

| SCENARIO_0003 (dairy -25%, lanes +10% W43-W48) | Customer fill | Fill W43-W48 | CDC fill W43-W48 | Total landed cost |
|---|---|---|---|---|
| Re-planning weekly (reactive - unprepared) | +1.12pp | +2.05pp | +1.12pp | +$225K |
| ...normal-year effect (control - no scenario) | +1.15pp | +1.65pp | +0.18pp | +$298K |
| ...reaction to this scenario | -0.03pp | **+0.40pp** | **+0.94pp** | **-$74K** |

Detection: the lane slowdown is picked up one to two weeks after it starts (ratio 1.08-1.10
vs the true 1.10) and released two to three weeks after it ends; the dairy bias factor falls
from ~0.99 to ~0.79 by week 12. Weekly re-planning also holds ~11% more inventory and lowers
internal replenishment fill (-3.3pp in the normal year). Single seed.

## Known limitations — stated up front, not left for a reviewer to find

- **Write-off minimization** (one of the charter's three named KPIs) is not
  measurable in this dataset at all: no shelf-life or obsolescence modeling,
  `write_offs.parquet` is empty by design.
- **Working capital** is a mixed result, not a clean win: safety stock value
  fell 9.1%, but total inventory value rose 9.1% (cycle stock grew faster
  than safety stock shrank).
- **Internal replenishment fill still degrades under every model tested**
  even after the netting-discount fix above. The current 5-seed means are 82.32% at
  alpha=0.2 and 84.64% at alpha=0.6, nowhere near actual's 93.21%. Internal fill is not
  a guard in `meio_parameters.py`'s selection rule. The netting step was only one
  contributor; something else is also at play (see "Next steps").
- **One real year of validation data.** `demand_forecast.parquet` only has rows
  from 2025 onward even though real orders go back to 2023, so every
  real-data result here is tested against a single year. As 2026 actuals
  accumulate, that becomes a genuine second holdout — none of today's
  real-data numbers have had that check yet.

## Files

- `clark_scarf_v2.py` — the model (Monte Carlo DDLT sizing, or the deterministic
  `ddlt="quadrature"` the pipeline uses; continuous alpha; scenario-override hooks;
  `ALPHA_OVERRIDE_BY_SKU` seam for the per-segment exploration).
- `meio_parameters.py` — reads `run_all.py`'s evidence and picks the parameters
  the pipeline's policy is built with (see "How the tuning reaches the pipeline's
  policy"). Tests: `tests/test_meio_parameters.py`. As of 2026-10-23, 8 pass and 3 fail
  because they still assume v4 cluster names and files (see OPTIMIZATION.md).
- `validate_real_data.py` — the full real-data validation suite (headline,
  alpha sweep, 5-seed check, segment breakdown). Run with `--stage <name>`
  for one piece, or no flag for all of them in order. Depends on
  `../meio_weekly_lib.py` (shared demand-estimation/sizing library — kept in
  its original location since other work in this project depends on it
  too) and `../../twin/digital_twin.py` (the shared simulator).
- `segment_alpha_explore.py` — the per-segment alpha diagnostic described
  above. `--stage sweep` (portfolio-level, ~90 min) then `--stage own_fill`
  (re-slices the sweep's own output, seconds) are the two real stages.
- `netting_discount_sweep.py` — the netting-discount fix's real-data test
  (see "Netting-discount fix" above). `--alpha 0.2` or `--alpha 0.6` for the
  single-seed sweep over `netting_discount`; `--multiseed` for the 5-seed
  robustness check of `netting_discount` in {0.0, 1.0} at alpha=0.2, or
  `--multiseed --alpha 0.6` (writes `netting_discount_multiseed_alpha0_6.csv`).
- `segment_alpha_real_data.py` — the real-data, multi-seed version of
  `segment_alpha_explore.py`'s diagnostic. Same v4
  4-cluster x 6-alpha sweep (others at 0.2, netting 0.0), but against real replayed
  demand and across 5 seeds instead of 1, on the shared pool, resumable (a combo
  already on disk is skipped on restart).
  `--workers N`, `--cluster "<name>"` for one cluster, `--summarize-only`
  to just re-aggregate. **Complete in the artifacts results** (24 of 24 combinations
  at 5 seeds, `segment_alpha_real_data_summary.csv`). It uses v4 clusters, so
  `meio_parameters.py` no longer reads it.
- `segment_alpha_v5_real_data.py` — the same sweep on v5's 6 clusters, every other
  cluster held at alpha 0.6 (the live portfolio value), netting 0.0. Its
  `segment_alpha_v5_real_data_multiseed.csv` (complete: 36 of 36 combinations at 5
  seeds) is what sets the live per-cluster alpha. **Not run by `run_all.py`**; run it
  directly.
- `service_level_real_data.py` — Feature 1 (front-end service-planning
  agent): a twin-validated, real-data, multi-seed answer to "what service
  level should each cluster target?" A per-cluster recommendation already
  existed (`Clustering & Segmentation/segmentation_v4/sku_clusters.csv`'s
  `recommended_csl`/`safety_factor_z`) and had been tested before
  (`rerun_with_cluster_csl.py`, `alt_models/cluster_bandit.py`), but both
  were synthetic-basis, single-seed, and predate the netting-discount fix.
  This sweeps each cluster's target CSL over {90%, 95%, 97%, 99%} against
  real demand, 5 seeds, holding the other three clusters at the
  segmentation team's own recommended z, at the current config (alpha=0.2,
  netting_discount=0.0). Same parallel/resumable infrastructure as
  `segment_alpha_real_data.py`. `--workers N`, `--cluster "<name>"`,
  `--summarize-only`. **Complete in the artifacts results** (16 of 16 combinations at
  5 seeds, `service_level_real_data_summary.csv`), but on v4 clusters, so
  `meio_parameters.py` no longer reads it. The v5 counterpart the resolver reads,
  `service_level_v5_real_data.py`, exists only in `../final_submission/` and is not run
  by `run_all.py`. Its output (`service_level_v5_real_data_multiseed.csv`, 150 rows,
  complete) is identical in `output datasets/agent output/` and the artifacts results.
- `scenario_test.py` — Feature 2 (front-end scenario-testing agent): given
  a scenario (`models/twin/scenario_config.example.json`'s schema -- so
  far only `demand_surge_pct` + `affected_region`/`affected_nodes` +
  `duration_weeks`/`start_week_offset` actually does anything; see
  `models/twin/scenario_engine.py`), builds a NEW stocking recommendation
  that anticipates it and runs three arms: A) baseline policy, normal
  demand; B) baseline policy, SCENARIO demand ("if we don't prepare");
  C) scenario-reoptimized policy, SCENARIO demand ("if we do"). With the
  example config (25% Southeast demand surge, 4 weeks) the current run added
  safety stock at 5 of the 6 Southeast nodes and showed no fill benefit from
  preparing (see "Scenario testing" above for both runs' numbers).
  `python scenario_test.py --scenario <path-to-config.json>`.
  **Known gap: only CDC/RDC policy is scenario-aware in this legacy path; PDC
  sizing isn't wired** (PDCs showed exactly 0% change).
- `run_all.py` — runs ten steps on one shared process pool: `validate_real_data.py`,
  `netting_discount_sweep.py` (`--alpha 0.2`, `--alpha 0.6`, `--multiseed`,
  `--multiseed --alpha 0.6`), `segment_alpha_explore.py`, the v4
  `segment_alpha_real_data.py` and `service_level_real_data.py`, `scenario_test.py`,
  and step 10 `cleanup_research_artifacts.py`, which writes a digest and then, after
  confirmation, deletes bulk twin outputs (`--skip-cleanup` to keep them). It does
  **not** run `segment_alpha_v5_real_data.py` or `service_level_v5_real_data.py`, the
  two sweeps the live per-cluster decisions read. Logs each step to its own file under
  `run_logs/`, continues past a failed step instead of aborting, and prints a final
  pass/fail summary. `--from N` / `--steps` to select steps. OPTIMIZATION.md estimates
  30-50 minutes end to end (not timed in full).
- `results/` — see "Where `results/` points" at the top. The artifacts results hold
  the current CSVs. `../final_submission/results/` holds the original's committed CSVs,
  charts and narrative `*_report.md` files. Those reports were written by the earlier
  `backtest_rolling_real*.py` scripts named in their headers, and
  `validate_real_data.py` does not regenerate them. Several are stale against the CSVs
  beside them: `headline_report.md` shows the netting 1.0 run, while
  `headline_clark_scarf_v2_vs_actual.csv` now holds the netting 0.0 / v5 cluster-z
  run. Each report carries a dated note saying so.
- `validation_output/` — gitignored. Full twin run detail (inventory.parquet,
  receipts, etc.) per run; regenerate by re-running the scripts above.

## Next steps, in priority order

0. **Run the live configuration end to end.** Alpha 0.6, the per-cluster overrides,
   netting 0.0 and quadrature have never been simulated together, multi-seed or
   otherwise (see "What it gives today").
1. **Find the rest of the internal-replenishment-fill gap.** The
   netting-discount fix closed some of it, but the current 5-seed means (82.32% at
   alpha 0.2, 84.64% at 0.6) are still 9-11pp below 93.21%. The netting step wasn't
   the only cause — likely
   candidates: the PDC real-variance formula fix (see below, also
   unresolved), or the RDC's `order_up_to_level` sizing more generally.
2. ~~Test netting_discount at alpha=0.6.~~ Done (`run_all.py` steps 3 and 9):
   netting 0.0 wins at alpha 0.6 too. Whether netting_discount should vary by alpha or
   segment is still open.
3. **Fresh-holdout confirmation of netting_discount=0.0 and alpha 0.4/0.6**
   once more real weeks (2026 actuals) are available — everything here is
   still one real year, and the live alpha was selected on that same year.
4. ~~Multi-seed validation of the per-segment alpha direction.~~ Done on real demand,
   5 seeds, for v4 (`segment_alpha_real_data_summary.csv`) and v5
   (`segment_alpha_v5_real_data_summary.csv`). The synthetic "long-tail wants less"
   direction did not hold up, and the v5 values now feed the pipeline. Still open: the
   clusters were swept one at a time, four of six winners sit on the 1.0 grid
   boundary, and `run_all.py` cannot regenerate the v5 file.
5. **PDC real-variance formula fix.** `meio_weekly_lib.py::solve_pdc_policy`
   sizes PDC safety stock as `z * sqrt(lt_mean_wk) * mu * SIGMA_UPLIFT["PDC"]
   * 0.5` — using throughput mean (`mu`) as a stand-in for variability
   instead of a real measured σ from RDC pull-order history. Not started.
6. **Re-run the v5 service-level sweep at the live alpha.** The v4 and v5 sweeps are
   complete, but the v5 one was run at alpha 0.2, so the resolver doesn't adopt it
   while alpha is 0.6.
7. ~~Wire PDC policy into the scenario engine.~~ Done by the override engine
   (`models/overrides`): PDC sizing sees a scenario's demand, capacity, lead time
   and service changes, in the pipeline's MEIO Weekly and Clark-Scarf v2 paths.
   `scenario_test.py` still uses the legacy `scenario_engine.py` path.
8. ~~Build the lead-time and capacity disruption scenario types.~~ Done:
   `models/overrides` applies 19 measures, including lead time, lane outage,
   plant capacity and attainment, in both MEIO and the Twin.
9a. ~~Run `run_all.py` step 9.~~ Done: with its output and the netting 0.0 re-run of
   `multiseed_alpha_04_06.csv`, the resolver moved alpha to 0.6, and the v5 per-cluster
   sweep (netting 0.0, reference 0.6) then fed per-cluster alpha.
9. **Multi-seed / multi-scenario validation of the scenario-testing
   feature** before any "value of preparing" number is treated as more than a single
   working example. The two runs so far disagree in sign (+0.09pp vs. −0.02pp).
