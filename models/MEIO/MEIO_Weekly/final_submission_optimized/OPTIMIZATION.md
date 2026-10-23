# final_submission_optimized — what changed, and how it was verified

A copy of `../final_submission/` that runs `run_all.py` (and the weekly production
tools) in a fraction of the time and produces the same numbers. The research method,
the parameters, the CLI of every script and every results file are unchanged. The
original folder is untouched.

```bash
python run_all.py                     # the nine research steps concurrently, then step 10 (cleanup)
python run_all.py --steps 5,6,7       # same --from / --steps as before
python run_all.py --workers 8         # pool size (default: logical CPUs - 2)
python run_all.py --lean-twin-outputs # skip most runs' detail tables (see Disk)
python run_all.py --skip-cleanup      # omit step 10 (cleanup_research_artifacts.py)
python run_weekly_tools.py            # the two production tools
python -m pytest tests -q             # equivalence + the original's own tests (see Exactness for current status)
```

Step 10 (`cleanup_research_artifacts.py`, not in the original) writes a digest of the
research outputs and then, after confirmation, deletes bulk twin outputs that the weekly
tools don't read. `run_all.py` reproduces the original's nine steps. It does **not** run
`segment_alpha_v5_real_data.py`, or `service_level_v5_real_data.py` (which exists only in
`../final_submission/`). Those two produce the v5 evidence that `meio_parameters.py`
now reads for the per-cluster decisions, so `run_all` alone cannot regenerate the
evidence behind the live per-cluster alpha.

## Disk

Data tables are Parquet across the project (generator, MEIO, twin); summaries stay
CSV. Like the original, a full `run_all` writes every twin run's detail tables. Each
156-week history run writes `inventory.parquet` (~20 MB; it was ~200 MB as CSV),
`replenishment_receipts.parquet` and `production.parquet`, about 30 MB in all. ~260
runs come to roughly **8 GB**, plus ~0.1 GB of walk-forward policy files. The same
policies took ~11 GB as CSV, each repeating the 105-week warm-up baseline.

`--lean-twin-outputs` (on `run_all.py` and on each research script) is the opt-in
alternative, at under 1 GB:
* research twin runs keep `run_kpis.json`, `run_manifest.json`, `policy.parquet` and the
  cost/service summaries, but skip the three detail tables. The manifest records this
  under `lean_outputs`.
* runs whose inventory a later step reads back keep everything: the `actual` /
  `ms_actual_s*` / `synthetic_baseline` caches, `csv2_headline`, and the
  segment-explore and scenario runs.

By default every table the original wrote is written, in Parquet instead of CSV.

## Where the ~10 hours went

Profiling the originals (no full runs) put the cost in four places:

| Cost | Original | Here |
|---|---|---|
| Demand estimates | `target_week_demand` filtered and grouped 739k rows with pandas ~6 times per target week (the week itself plus the 5 weeks PDC smoothing averages), ~1 s each, in every one of ~230 policy builds | A dense `[pair, week]` panel (`DemandPanel`). Trailing statistics depend only on the as-of week, so they're computed once per as-of week and cached per process. ~0.06 s per week. |
| Monte Carlo draws | Every variant of a sweep redrew the same gamma variates. Netting discount, alpha and z only rescale or re-quantile them, and each variant started from a fresh generator at the same seed. About 230 builds × 51 weeks × ~1.3 s | One pass of draws per RNG stream, with every variant read off it (`clark_scarf_v2.WeekPlan` + endpoints). About 40 stream builds. |
| Digital twin | ~250 runs, each a fresh interpreter: inputs re-read, the 1.2M-row policy loaded with `itertuples`, Python loops per shipped SKU and per active pair each week, ~250 MB of CSV written with pandas, then read back by the caller | `digital_twin_fast.py`: the same engine in-process, hot loops vectorised, static inputs and replayed history demand cached per worker, polars output, summaries returned directly. **~5 s per 156-week history run** (3.6 s simulation + 1.0 s writes). |
| Scheduling | Nine steps run one after another; only steps 6 and 7 used a pool (4 workers) | One shared process pool, all steps at once. A priority dispatcher puts the sequential RNG chains first, and each chain runs in its own warm "lane" process. |

The remaining critical path is step 5 (`segment_alpha_explore.py`). The original passes **one advancing
generator** through its 24 rolling builds, so build *k*'s draws start where build *k*-1's
ended, and the chain can't be parallelised without changing its numbers. That's ~1,250
weeks of gamma draws at ~1.2 s each (numpy's gamma sampler, ~30 ns per variate). It runs
first, at top priority, with 4 threads for the arithmetic around the draws.
`--explore-fresh-rng` (or `segment_alpha_explore.py --fresh-rng`) gives each of those builds a
fresh generator instead. That's the same fix `segment_alpha_real_data.py` already uses,
and it cuts the step to a couple of minutes, but step 5 then no longer reproduces the
published `segment_alpha_sweep_*.csv`.

## Libraries

* **polars** for every table read and write and the table work. Data tables are
  Parquet; summaries (the `results/` evidence, `cost_summary`, `service_*`,
  `meio_summary_*`) stay CSV. `runtime.read_table` / `write_table` pick the format from
  the suffix and cache static inputs per process. A CSV is written the way pandas did
  (NaN as empty, booleans as `True`/`False`, `os.linesep` line endings), so it parses to
  the same values.
* **numpy** for the arithmetic, in the original's operation order.
* **Multithreading/multiprocessing:** a process pool across steps and combos. Inside a task,
  thread pools for GIL-free numpy/scipy chunks (Monte Carlo parameter maths by rows,
  quantiles by columns, the quadrature bisection by columns).
* **CUDA was evaluated and not used.** CuPy runs on this machine's RTX A2000 once `CUDA_PATH` points at the
  pip-installed NVRTC. However:
  - The Monte Carlo draws must come from numpy's own sequential stream to
    reproduce published results, so they can't move to the GPU.
  - For the deterministic quadrature DDLT (the production tools; a different estimate
    from Monte Carlo, checked against a 400,000-draw Monte Carlo to a median relative
    error below 0.5% for safety stock in `test_meio_parameters.QuadratureTests`) the GPU bisection took
    0.42 s per call against 0.27 s for 8 CPU threads. This laptop GPU is weak at
    64-bit floating point, which the gamma functions need.
  - The twin's per-day arrays (27 × 500) are too small for GPU launches to pay off.

  So the CPU-thread path is used, and it is bit-identical.
* **pandas** remains only at the boundary with the shared scenario-override engine
  (`models/overrides/`, also used by the Digital Twin and the agents) when a scenario
  carries plan- or actual-side overrides. `run_all` never uses that path.

## Exactness

Published results come out of pandas groupby (Kahan-compensated sums, Welford's
one-pass std), numpy's pairwise `add.reduce`, and pandas' quicksort tie order. A
different summation order changes the last bit of a demand estimate. That changes the
gamma parameters, then the reorder point, then a replenishment decision in the twin.
`runtime.py` reproduces each of those orders exactly. The tests check the following.
The "Result" column records what was observed when each check was written. The status
re-checked on 2026-10-23 is given below the table.

| Test | What is compared | Result |
|---|---|---|
| `tests/test_equivalence.py` | Originals run in a separate process on small slices. Covered: seasonal index, lead-time graph, `target_week_demand` at 6 (as-of, target) pairs, PDC smoothing and sizing, Monte Carlo `solve` (including the generator state after two consecutive solves), alpha/z maps, quadrature `solve`, MEIO Weekly closed form, a 3-week walk-forward built as one of three variants on a shared stream (1 and 4 threads), and `meio_parameters` receipts on the committed evidence and two synthetic complete sweeps | bit-identical (optimized vs original; "quadrature `solve`" compares the two quadrature implementations, not quadrature against Monte Carlo) |
| `tests/test_twin_equivalence.py` | Original and optimized twin side by side: forward with static policy, forward with time-varying policy (with forward-fill gaps), history with time-varying policy. Covers inventory, receipts, production, notes, `run_kpis`, cost summary | identical when written. The two service tables match to 1e-12 (see below). **Fails 3/3 on 2026-10-23** (see below). |
| `tests/test_meio_parameters.py` | The original's own test suite, ported to the polars API | 11/11 passed when written. **8 pass, 3 fail on 2026-10-23** (see below). |
| `tests/verify_headline_full.py` | The full 156-week Clark-Scarf v2 alpha=0.2 policy (105 warm-up + 51 refit weeks), built by original and optimized code, then simulated by both twins. The script uses **netting_discount=1.0 and `output datasets/history/forward_policy.parquet` as the warm-up policy**. The published headline uses netting 0.0 and the config's `baseline_policy`, so this checks the code path, not the published headline run. | **policy identical (1,199,384 rows); KPIs and test-window fill identical.** Original build 614 s vs optimized 380 s for this single variant on a busy machine (the draws dominate a single build; the saving comes from sharing them across variants). Twin: 70 s vs 14 s. |
| `tests/smoke_run_all.py --lean-twin-outputs` | The whole `run_all` on a shrunken config (3 test weeks, 4-week rolling builds) in a temp folder | 9/9 steps OK, 18/18 results files, 4.5 GB, 22.6 min. The 18 expected files do not include the v5 sweeps. `shrink()` does not redirect step 10 (`cleanup_research_artifacts.py`), which resolves the *real* `scenario_config.json` paths, so use `--skip-cleanup` with the smoke run. |
| `roll_forward_policy.run(out_path=tmp)` | The production rolling policy against the original's last run log | 422,552 rows, mean order-up-to 287.0, the same as the log; 96 s. The committed policy built with today's resolved parameters (`output datasets/MEIO/SCENARIO_0002_MEIO_CLARK_SCARF_V2/rolling_2026/meio_policy_rolling.parquet`, alpha 0.6 + per-cluster alpha) also has 422,552 rows, with mean order-up-to 281.6. |

**Status on 2026-10-23** (run read-only from this folder):
- `python -m pytest tests/test_meio_parameters.py -q`: **8 passed, 3 failed**. The
  failures are `test_committed_evidence_gives_the_validated_config`,
  `test_complete_service_level_sweep_builds_the_cluster_ladder` and
  `test_grids_match_the_sweep_scripts`. The tests still assume v4: they use the 4 v4
  cluster names and file names, and compare `meio_parameters.CLUSTERS` (now v5's 6
  archetypes) with `segment_alpha_explore.CLUSTERS` (v4's 4). `QuadratureTests` passes.
- `python -m pytest tests/test_twin_equivalence.py -q`: **3 failed** (all three cases).
  The original `models/twin/digital_twin.py` reads the policy with `pd.read_csv`, but the
  test hands it a `.parquet` policy, so it raises `UnicodeDecodeError` before any
  comparison. The repo's `input datasets/*.csv` files are also Git LFS pointer files,
  not data.
- `tests/test_equivalence.py` was not re-run. On first use it writes a reference cache
  to `tests/_reference/` by running the originals. Until it is re-run, the
  "bit-identical" claims above rest on the earlier runs.

The full-length check also showed something about the original: today's original code
(digital_twin.py as it stands) gives fill 0.9431 / internal 0.8183 for this netting 1.0
/ `forward_policy` warm-up configuration, not the 0.9427 / 0.8199 recorded for the
netting 1.0 headline in `../final_submission/results/headline_report.md` (and the
`nd=1.00` row of `../final_submission/results/netting_discount_sweep_alpha0_2.csv`).
Those committed results predate later code changes. The committed
`headline_clark_scarf_v2_vs_actual.csv` itself has since been regenerated at netting 0.0
(0.9451 / 0.8238). The optimized folder matches today's code.

## Expected run time

The full nine-step run has not been timed end to end (that would rewrite the real
output folders). Measured pieces:
* a 156-week twin run takes ~5 s alone, ~15-20 s with every core busy.
* a walk-forward week takes ~1.5 s of draws plus ~0.06 s per extra variant on the stream.
* the step-5 chain is about 24 × 52 weeks.

That puts `run_all` at roughly **30-50 minutes**, against ~10 hours for the original. The
range is because this laptop throttles under sustained load: the same gamma draws ran
2x slower after 15 minutes at full load. `--explore-fresh-rng` removes most of the
step-5 chain.

Two known non-differences:
* `service_summary.csv` / `service_by_node.csv` (diagnostic tables) are summed with polars, and a value
  there can differ from the original in the last floating-point digit. Nothing reads
  them back.
* The original re-parsed each twin run's `inventory.parquet` text with pandas' parser before
  computing fill-rate KPIs. Here they're computed from the in-memory values. The two can
  differ by 1 ULP on a fractional value, which is far below the 4-decimal rounding of every reported KPI.

## Behaviour fixes (flagged, not silent)

* **`scenario_test.py` (step 8) can't finish in the original against the current twin.**
  - Arm A reads `validation_output/<run>/run_kpis.json`, but `digital_twin.py` now always writes
    `<out-root>/projections/SCENARIO_####_Name/<run>`.
  - Arms B and C pass the example scenario JSON, which has no `digital_twin` section, so the twin raises
    `KeyError: 'digital_twin'`.

  The copy here reads the folder the twin actually wrote, and its temporary scenario config carries the root config's
  `digital_twin` section.
* **Steps 4 and 9 write the same file names** (`policy_netting_ms_nd*_s*` and twin runs
  `netting_ms_nd*_s*` carry no alpha). The original's sequential order meant step 9's
  copies won. `run_all.py` here runs step 9 after step 4 to keep that, instead of racing them.

## Files

| File | Role |
|---|---|
| `runtime.py` | paths, thread caps, polars Parquet/CSV I/O, pandas-exact reductions, the priority process pool (`Context`), per-step logs |
| `meio_weekly_lib.py` | demand panel and estimator (port of the original) |
| `clark_scarf_v2.py` | Clark-Scarf v2 split into WeekPlan / endpoints / per-variant finish |
| `policy_builds.py` | walk-forward and rolling builds, many variants per RNG stream; pool tasks |
| `digital_twin_fast.py` | the optimized twin (CLI-compatible with `models/twin/digital_twin.py`) |
| `validate_real_data.py`, `netting_discount_sweep.py`, `segment_alpha_explore.py`, `segment_alpha_real_data.py`, `service_level_real_data.py`, `scenario_test.py` | the research steps: same stages, flags and output files |
| `segment_alpha_v5_real_data.py` | the v5 (6-cluster) per-cluster alpha sweep `meio_parameters.py` reads; new here, not in the original, and not run by `run_all.py` |
| `cleanup_research_artifacts.py` | `run_all.py` step 10: digest, then (with confirmation) delete bulk research outputs |
| `run_all.py`, `run_weekly_tools.py` | orchestration |
| `meio_parameters.py`, `meio_optimizer_weekly.py`, `roll_forward_policy.py` | production tools on the port |

## Outputs

The same folders, file names and file types as the original's `run_all.py`, all under
the scenario config's `artifacts` section:

| What | Where |
|---|---|
| results (`*.csv`), read by `meio_parameters.py` | `<artifacts.scenario>/results/` |
| walk-forward and rolling policies (`policy_*.parquet`), real-data partials, step 8's twin runs | `<artifacts.scenario>/validation_output/` |
| every other twin run (`actual`, `ms_actual_s*`, `synthetic_baseline`, sweeps…) | `<artifacts.twin_output_root>/projections/SCENARIO_####_Name/<run>/` |
| a copy of the scenario config | `<artifacts.scenario>/` and each `projections/SCENARIO_####_Name/` |
| per-step logs | `run_logs/` in this folder |

With the root `scenario_config.json`, `<artifacts.scenario>` is
`output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2` and the twin runs go
to its `twin_runs/` subfolder. `OUTPUT_DOCUMENTATION.md` is written where the original's
twin calls write it: `twin_runs/` (step 5) and `validation_output/` (step 8). The
real-data steps skip it, as the original passes `--skip-output-documentation`.

Each run folder has the same files as the original's. CSVs have pandas' text, CRLF line
endings included. Parquet files hold the same tables (same columns, dtypes and values
with `pd.read_parquet`), but polars encodes them with ZSTD and without pandas' schema
metadata, so they aren't byte-identical. The service tables can also differ in the last
digit (see Exactness).

So running this folder's `run_all.py` refreshes the portfolio-level evidence
`meio_parameters.py` reads (`netting_discount_multiseed*.csv`, `multiseed_alpha_*.csv`),
exactly as the original would. It does not refresh the two v5 cluster files
(`segment_alpha_v5_real_data_multiseed.csv`, `service_level_v5_real_data_multiseed.csv`),
which have to be produced by their own scripts (see the top of this file). Cached baselines in `twin_runs/` from a previous run of
either version are reused. Runs in `output datasets/projections/SCENARIO_0002_…/`, where
both versions wrote before the switch to `artifacts.twin_output_root`, are not read.
