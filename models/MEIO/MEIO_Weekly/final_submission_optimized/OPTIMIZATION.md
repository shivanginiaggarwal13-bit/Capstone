# final_submission_optimized — what changed, and how it was verified

A copy of `../final_submission/` that runs `run_all.py` (and the weekly production
tools) in a fraction of the time and produces the same numbers. The research method,
the parameters, the CLI of every script and every results file are unchanged. The
original folder is untouched.

```bash
python run_all.py                     # all nine steps, concurrently
python run_all.py --steps 5,6,7       # same --from / --steps as before
python run_all.py --workers 8         # pool size (default: logical CPUs - 2)
python run_all.py --lean-twin-outputs # ~6 GB on disk instead of ~70 GB (see Disk)
python run_weekly_tools.py            # the two production tools
python -m pytest tests -q             # equivalence + the original's own tests
```

## Disk

Like the original, a full `run_all` writes every twin run's detail tables. Each
156-week history run writes `inventory.csv` (~200 MB), `replenishment_receipts.csv` and
`production.csv`, so ~260 runs come to about **60 GB**. Add ~11 GB of walk-forward
policy CSVs, each of which repeats the 105-week warm-up baseline. The original needs
the same space. Check free space before running.

`--lean-twin-outputs` (on `run_all.py` and on each research script) is the opt-in
alternative, at about 6 GB:
* research twin runs keep `run_kpis.json`, `run_manifest.json`, `policy.csv` and the
  cost/service summaries, but skip the three detail CSVs. The manifest records this
  under `lean_outputs`.
* runs whose inventory a later step reads back keep everything: the `actual` /
  `ms_actual_s*` / `synthetic_baseline` caches, `csv2_headline`, and the
  segment-explore and scenario runs.
* policy files are written as Parquet next to the CSV name (56x smaller). The twin
  reads either format, with identical results.

The default writes exactly what the original wrote.

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

* **polars** for every CSV read and write and the table work. `runtime.read_csv` caches static inputs per
  process; `runtime.write_csv` writes NaN as empty and booleans as `True`/`False`, the
  way pandas did. CSV text can differ cosmetically (`1e-05` vs `0.00001`), but parsed
  values are identical.
* **numpy** for the arithmetic, in the original's operation order.
* **Multithreading/multiprocessing:** a process pool across steps and combos. Inside a task,
  thread pools for GIL-free numpy/scipy chunks (Monte Carlo parameter maths by rows,
  quantiles by columns, the quadrature bisection by columns).
* **CUDA was evaluated and not used.** CuPy runs on this machine's RTX A2000 once `CUDA_PATH` points at the
  pip-installed NVRTC. However:
  - The Monte Carlo draws must come from numpy's own sequential stream to
    reproduce published results, so they can't move to the GPU.
  - For the deterministic quadrature DDLT (the production tools) the GPU bisection took
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
`runtime.py` reproduces each of those orders exactly. The tests check:

| Test | What is compared | Result |
|---|---|---|
| `tests/test_equivalence.py` | Originals run in a separate process on small slices. Covered: seasonal index, lead-time graph, `target_week_demand` at 6 (as-of, target) pairs, PDC smoothing and sizing, Monte Carlo `solve` (including the generator state after two consecutive solves), alpha/z maps, quadrature `solve`, MEIO Weekly closed form, a 3-week walk-forward built as one of three variants on a shared stream (1 and 4 threads), and `meio_parameters` receipts on the committed evidence and two synthetic complete sweeps | bit-identical |
| `tests/test_twin_equivalence.py` | Original and optimized twin side by side: forward with static policy, forward with time-varying policy (with forward-fill gaps), history with time-varying policy. Covers inventory, receipts, production, notes, `run_kpis`, cost summary | identical. The two service tables match to 1e-12 (see below). |
| `tests/test_meio_parameters.py` | The original's own test suite, ported to the polars API | 11/11 pass |
| `tests/verify_headline_full.py` | The full 156-week headline policy (105 warm-up + 51 refit weeks), built by original and optimized code, then simulated by both twins | **policy identical (1,199,384 rows); KPIs and test-window fill identical.** Original build 614 s vs optimized 380 s for this single variant on a busy machine (the draws dominate a single build; the saving comes from sharing them across variants). Twin: 70 s vs 14 s. |
| `tests/smoke_run_all.py --lean-twin-outputs` | The whole `run_all` on a shrunken config (3 test weeks, 4-week rolling builds) in a temp folder | 9/9 steps OK, 18/18 results files, 4.5 GB, 22.6 min |
| `roll_forward_policy.run(out_path=tmp)` | The production rolling policy against the original's last run log | 422,552 rows, mean order-up-to 287.0, the same as the log; 96 s |

The full-length check also showed something about the original: today's original code
(digital_twin.py as it stands) gives headline fill 0.9431 / internal 0.8183, not the
0.9427 / 0.8199 in the committed `results/headline_clark_scarf_v2_vs_actual.csv`. The
committed results predate later code changes. The optimized folder matches today's code.

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
* The original re-parsed each twin run's `inventory.csv` text with pandas' parser before
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
| `runtime.py` | paths, thread caps, polars CSV I/O, pandas-exact reductions, the priority process pool (`Context`), per-step logs |
| `meio_weekly_lib.py` | demand panel and estimator (port of the original) |
| `clark_scarf_v2.py` | Clark-Scarf v2 split into WeekPlan / endpoints / per-variant finish |
| `policy_builds.py` | walk-forward and rolling builds, many variants per RNG stream; pool tasks |
| `digital_twin_fast.py` | the optimized twin (CLI-compatible with `models/twin/digital_twin.py`) |
| `validate_real_data.py`, `netting_discount_sweep.py`, `segment_alpha_explore.py`, `segment_alpha_real_data.py`, `service_level_real_data.py`, `scenario_test.py` | the research steps: same stages, flags and output files |
| `run_all.py`, `run_weekly_tools.py` | orchestration |
| `meio_parameters.py`, `meio_optimizer_weekly.py`, `roll_forward_policy.py` | production tools on the port |

Outputs go to the same places as the original's (`output datasets/MEIO artifacts/…`,
`output datasets/projections/…`). Running this folder's `run_all.py` therefore refreshes
the evidence `meio_parameters.py` reads, exactly as the original would. Cached baselines
(`actual`, `ms_actual_s*`, `synthetic_baseline`) from a previous run of either version
are reused.
