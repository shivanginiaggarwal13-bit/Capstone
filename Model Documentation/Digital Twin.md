# Digital Twin — Simulation / Scoring Engine (Original vs. Optimized Port)

**Status: both versions in active use.** Not an inventory-optimization model itself —
the physics engine every MEIO model above is built, validated and scored against. Listed
here because understanding how a model's number was produced requires understanding what
scored it. Two versions exist: the original (`models/twin/digital_twin.py`) and a
rebuilt, ~10x-faster port (`final_submission_optimized/digital_twin_fast.py`) used for
production because of its speed, verified bit-identical to the original.

Source: `models/twin/digital_twin.py` (original), `models/MEIO/MEIO_Weekly/final_submission_optimized/digital_twin_fast.py`
and `OPTIMIZATION.md` (optimized port + verification story).

## Definition

A single-file, **discrete-event simulation** of the project's multi-echelon network — the
project's *only* physics engine. The same dataset generator
(`supply_brain_generator_v5`) uses it to generate the historical dataset the project
analyzes, and every MEIO model above uses it to score a candidate policy. Those two
things sharing one engine is a deliberate design point stated directly in its own
docstring: if the history and the reward came from different simulators, no measured
improvement could ever be trusted.

**Design** follows the ISOMORPH pattern: one simulated step is one day, fixed event
ordering inside each step, an explicit in-transit shipment pipeline keyed by arrival
day, and a self-describing run directory (a manifest recording every knob and
assumption behind a given run).

**Network**: Plant → Plant DC (0.5-day push) → Regional DC → Customer DC. Plants are
production-only and hold no stock; Plant DCs are *pushed* stock by production, not
pulled by a reorder point. RDCs and CDCs pull on a weekly (R, s, S) review. RDCs carry
both direct bulk-customer demand and downstream CDC demand.

**The optimization seam**: the only thing any optimizer changes is one standalone CSV —
`dc_id, sku_id, safety_stock, reorder_point, order_up_to_level` — passed via `--policy`.
Extra columns are ignored, so any model's native output can be passed verbatim. Opening
state comes from a separate `--state` file, never touched by a policy swap, so two runs
start from an identical physical position and the difference between them measures the
decision alone. Demand, lead-time and production RNG are independent streams, so
changing the policy never moves the demand draw.

## Features

- Every MEIO model documented in this folder was scored by this engine, from the same
  opening state, with the same independent RNG streams — the one constant across every
  comparison table in this folder.
- Supports both a single frozen-week policy (used by the project's single-week bake-off,
  `compare_models.py`) and a genuinely time-varying rolling weekly policy (an optional
  `period` column on the policy CSV, falling back to the original static behavior when
  absent).
- Scenario-aware: can apply demand-surge, lead-time, and capacity scenario overrides to
  its own demand tensor (see `Clark Scarf v2.md`'s "Scenario testing" section).
- Self-describing run manifests — every run records its own knobs, not just its output.

## The two versions

### Original (`models/twin/digital_twin.py`)

The reference implementation. Single-threaded, pandas-based, one fresh Python
interpreter per run: inputs re-read from disk each time, the policy (often well over a
million rows for a full rolling-horizon run) loaded with `itertuples`, Python loops per
shipped SKU and per active pair each simulated week, CSV output written by pandas and
read back by the caller. **This is the authoritative version** — every "bit-identical"
claim about the optimized port is a claim about matching *this* version's output exactly.

### Optimized port (`final_submission_optimized/digital_twin_fast.py`)

A from-scratch re-implementation of the exact same engine — same method, same
parameters, same CLI, same output files — rebuilt purely for speed. Verified
**bit-identical** to the original, not just "close":

| What changed | Original | Optimized |
|---|---|---|
| Demand estimates | `target_week_demand` filtered/grouped 739K rows with pandas ~6 times per target week, ~1s each, across ~230 policy builds | A dense `[pair, week]` cached panel; trailing stats computed once per as-of week and cached per process (~0.06s/week) |
| Monte Carlo draws | Every sweep variant redrew the same Gamma variates from a fresh generator at the same seed | One pass of draws per RNG stream, every variant read off it (~40 stream builds total instead of ~230 redraws) |
| Digital twin itself | ~250 fresh-interpreter runs, policy loaded with `itertuples`, Python loops per SKU/day, pandas CSV I/O | In-process, vectorized hot loops, static inputs cached per worker, polars output — **~5s per 156-week history run** (3.6s simulate + 1.0s write) |
| Scheduling | Nine `run_all.py` steps sequential; only 2 of 9 used a worker pool | One shared, priority-ordered process pool across every step |

**Libraries**: polars for every table (Parquet for data, CSV kept only for the small
`results/` evidence tables planners read); numpy for arithmetic, in the original's exact
operation order (summation order changes the last bit of a demand estimate, which
changes the Gamma parameters, which changes the reorder point, which changes a
replenishment decision — reproduced exactly, not approximately); pandas kept only at the
override-engine boundary. **CUDA was evaluated and explicitly not used**: the Monte
Carlo draws must come from numpy's own sequential stream to reproduce published results
(can't move to GPU without breaking reproducibility); the deterministic quadrature DDLT
mode was actually *slower* on this machine's GPU than on 8 CPU threads (0.42s vs. 0.27s
— a laptop GPU weak at the 64-bit floats the Gamma functions need); and the twin's
per-day arrays (27×500) are too small for a GPU launch to pay off anyway.

## Verification (why the optimized port can be trusted)

| Test | What it compares | Result |
|---|---|---|
| `tests/test_equivalence.py` | Original vs. optimized, small slices: seasonal index, lead-time graph, demand estimation, PDC sizing, Monte Carlo solve (including RNG state after consecutive solves), alpha/z maps, quadrature solve, MEIO Weekly closed form, a 3-week walk-forward (1 and 4 threads) | **Bit-identical** |
| `tests/test_twin_equivalence.py` | Original vs. optimized twin: forward static policy, forward time-varying policy (with gaps), history time-varying policy — inventory, receipts, production, notes, KPIs, cost summary | **Identical**; the two service tables match to 1e-12 |
| `tests/test_meio_parameters.py` | Original's own test suite, ported to the polars API | 11/11 pass |
| `tests/verify_headline_full.py` | The full 156-week headline policy (105-week warm-up + 51-week refit), built and simulated by both versions | **Policy identical, 1,199,384 rows**; KPIs and test-window fill identical. Policy build: 614s (original) vs. 380s (optimized). Twin alone: **70s vs. 14s**. |
| `tests/smoke_run_all.py` | Full `run_all` on a shrunken config | 9/9 steps OK, 18/18 result files |

## Advantages (optimized port)

- Verified bit-identical to the original across every test above — the speed is "free"
  in the sense that no result in this project's final-submission evidence base is at
  risk of being an artifact of the rewrite.
- Makes the project's heaviest validation passes (multi-seed robustness, the full
  per-cluster alpha and CSL sweeps, `run_all.py`'s complete evidence rebuild) tractable
  within a session — a ~9–10 hour overnight run on the original becomes something that
  can be iterated on same-day.
- Disk-conscious by default (Parquet instead of CSV cuts the full `run_all` output from
  ~11GB to ~8GB, with a `--lean-twin-outputs` mode taking it under 1GB).

## Disadvantages / limitations

- **A second codebase to keep in sync.** Every method change to the original engine
  must be mirrored in the optimized port and re-verified, or the two will silently
  drift — the project's own equivalence test suite is the only thing preventing that,
  and it must be re-run after any change to either side, not assumed to still hold.
- One real accuracy bug was found and fixed *during* this reconciliation, not before it:
  an unconditional state-file fallback was injecting a 2026-W01 forward production
  snapshot (1.23M units) as week-1 2023 opening stock in history-mode runs, inflating
  every history-mode fill rate by several points. Found only because the optimized
  port's results were being checked against the original's, line by line — a reminder
  that the reconciliation process itself surfaces real bugs, not just confirms
  equivalence.
- Step 5 of `run_all.py` (`segment_alpha_explore.py`) resists full parallelization: the
  original passes one advancing RNG generator through all 24 of its rolling builds, so
  build *k*'s draws depend on build *k*-1 having run first — a `--fresh-rng` mode can
  break that dependency for speed, but doing so means the result no longer reproduces
  the originally published `segment_alpha_sweep_*.csv` numbers, a real
  reproducibility/speed tradeoff, not a free win.
- The optimized port is the one actually wired into `scenario_config.json`'s
  `digital_twin.entrypoint` for production use — any code path in the broader project
  that still hardcodes the *original* engine's path will silently use stale, unoptimized
  behavior (a real class of bug found and fixed multiple times elsewhere in this
  project's later agent-tooling work).

## Why both versions still exist (neither fully phased out)

The original remains the authoritative reference implementation — every equivalence
claim about the optimized port is checked against it, and it is what a from-scratch
auditor should read first to understand the method itself, unobscured by the
performance engineering. The optimized port is what production and research tooling
actually execute, because the original's ~9–10 hour full-evidence-rebuild time is not
practical to re-run routinely. Documented as a pair deliberately, not as "old" vs. "new."

## Files referenced

- `models/twin/digital_twin.py` (original, ~docstring quoted above)
- `models/MEIO/MEIO_Weekly/final_submission_optimized/digital_twin_fast.py`,
  `OPTIMIZATION.md` (primary source for the optimized-port section of this document)
- `models/MEIO/MEIO_Weekly/final_submission_optimized/tests/test_equivalence.py`,
  `test_twin_equivalence.py`, `test_meio_parameters.py`, `verify_headline_full.py`
