# Digital Twin — Simulation / Scoring Engine (Original vs. Optimized Port)

**Status: both versions in active use.** Not an inventory-optimization model itself —
the physics engine every MEIO model above is built, validated and scored against. Listed
here because understanding how a model's number was produced requires understanding what
scored it. Two versions exist: the original (`models/twin/digital_twin.py`) and a
rebuilt, ~10x-faster port (`final_submission_optimized/digital_twin_fast.py`) used for
production because of its speed. `OPTIMIZATION.md` reports the port as bit-identical to the
original on the shared core physics (see the caveat under "Verification"). The port also
carries the scenario override engine, which the original does not have, so with overrides
in play the two are not equivalent. A third entry point,
`final_submission_optimized/reactive_twin.py`, wraps the port so that MEIO re-plans every
week (see "Reactive arm" below).

Source: `models/twin/digital_twin.py` (original), `models/MEIO/MEIO_Weekly/final_submission_optimized/digital_twin_fast.py`,
`reactive_twin.py`, `models/twin/scenario_engine.py`, `models/overrides/twin.py`, and
`OPTIMIZATION.md` (optimized port and verification story).

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

**Network**: Plant → Plant DC (0.5-day push lane) → Regional DC → Customer DC. Plants are
production-only and hold no stock; Plant DCs are *pushed* stock by production, not
pulled by a reorder point. RDCs and CDCs pull on a weekly (R, s, S) review. RDCs carry
both direct bulk-customer demand and downstream CDC demand. Because arrival day =
ship day + max(1, ceil(drawn lead time)), the 0.5-day plant→PDC lane always lands the
next day.

**The optimization seam**: the only thing any optimizer changes is one standalone
policy table: `dc_id, sku_id, safety_stock, reorder_point, order_up_to_level`, passed via
`--policy`. The original reads it as CSV; the port reads CSV or Parquet by file suffix.
Extra columns are ignored, so any model's native output can be passed verbatim. Opening
state comes from a separate `--state` file, never touched by a policy swap, so two runs
start from an identical physical position and the difference between them measures the
decision alone. Demand, lead-time and production RNG are independent streams
(`SeedSequence(seed).spawn(3)`), so changing the policy never moves the demand draw. The
demand tensor is drawn in full before the simulation starts. The common-random-numbers
property holds fully **only for demand**, though. The lead-time stream is consumed once
per shipped SKU line, so it falls out of step as soon as two policies ship differently.
The production stream takes one adherence draw per producing plant per week, so it stays
aligned.

## Mechanics (both versions, verified against the code)

- **Daily event order** (`step()`): (1) receive today's arrivals; (2) clear backlog from
  *all* on-hand stock, not only today's arrivals; cleared units are not counted in
  `shipments`; (3) serve external demand from on-hand stock: a CDC shortfall is a lost
  sale, an RDC/PDC shortfall is backlogged; (4) on the first day of each week
  (`REVIEW_DAY = 0`), review, then plan production; (5) fulfil the open order book, PDC
  origins first, then RDC origins; (6) add end-of-day on-hand to the holding snapshot.
- **Review**: inventory position = on_hand − backlog + on_order. Only active RDC/CDC
  pairs order. If IP ≤ s, the pair orders S − IP. PDC `reorder_point` and
  `order_up_to_level` are never read.
- **Parent hold-back**: a parent releases only stock above its own policy
  `safety_stock` for that week (`reserve_own_demand=True`). A shortfall is split pro rata
  to the quantities requested, and grants are floored to whole units. An internal order
  still open at week close is cancelled, booked as `unmet_replen_qty`, and re-raised at
  the next review.
- **Production** (`_produce`, on review day): each PDC's demand signal is an EMA
  (`DEMAND_EMA_ALPHA = 0.25`, starting at 0 in every run) of the internal orders just
  placed on it. For each plant, sig = EMA[pdc] × allocation_share and
  pos = (on_hand + on_order)[pdc] × allocation_share. The plan is
  sig + max(0, `FG_COVER_WEEKS` × sig − pos), with `FG_COVER_WEEKS = 2.5`. It is clipped
  pro rata by family-line capacity, then by plant-wide capacity, then scaled by one
  U(0.90, 1.00) adherence draw per plant-week and floored. Routing has already
  restricted the PDC's order signal to its own RDCs, so each plant plans
  `allocation_share` × what its own PDC's signal implies. For SKUs made at more than one
  plant (`allocation_share` < 1), this is likely a double count of the split (open audit
  item).
- **Lead times**: each shipped SKU line draws max(0.5, Normal(mean, max(std, 0.05))) from
  its lane in `network_lanes`. Missing lanes default to (3.0, 1.0). This is a parametric
  draw; the `_ship` docstring still says "resampled from that lane's observed receipts",
  which is stale. Promise = mean + 1.2816·std (p90). `on_time_flag` compares the
  *continuous* draw with the promise, not the rounded arrival day.
- **Costs**: holding = avg end-of-day on-hand × (storage + carrying% × unit_cost) / 52,
  keyed on (dc_id, subcategory). Pipeline (in-transit) stock is not charged holding.
  Stockout = `margin_usd` × units short at **every** node, including RDC backorders that
  are filled later. Transport = qty × lane cost, charged to the destination, and
  plant→PDC freight is excluded (only `origin ≥ 0` is costed). Total landed cost =
  holding + stockout + transport.
- **Active pairs**: a (node, SKU) with no row in the policy is inactive. It never
  orders and has no row in `inventory`, so fill KPIs and the service tables cover active
  pairs only. It still faces customer demand and accrues holding and stockout cost, and
  cost totals include it.

## Features

- Every MEIO model documented in this folder was scored by this engine, from the same
  opening state, with the same independent RNG streams — the one constant across every
  comparison table in this folder.
- Supports both a single frozen-week policy (used by the project's single-week bake-off,
  `compare_models.py`) and a genuinely time-varying rolling weekly policy (an optional
  `period` column on the policy CSV, falling back to the original static behavior when
  absent). A pair with no row in a given week carries its last seen values forward;
  weeks before a pair's first row are 0.
- Scenario-aware, though the two versions support different amounts:
  - **Original**: applies only the legacy `changes.demand_surge_pct` (via
    `scenario_engine.py`). This scales daily external demand by (1 + pct/100) at
    `affected_nodes` or the CDCs and RDCs in `affected_region`, over
    `duration_weeks` from `start_week_offset`. Every other `changes` key is saved to the
    scenario folder and silently ignored.
  - **Optimized port**: also applies the full override engine (`changes.overrides`,
    the *actual* side only, forward mode only). Supported overrides are economics (unit
    cost, margin, holding rate, storage cost, logistics cost), demand volume (on
    forecast lines before the error draw), demand volatility (r → max(0, 1 + k(r − 1))),
    opening on-hand and in-transit stock, lane lead-time mean and std by dispatch week,
    lane outages (dispatches wait until the lane reopens), plant/family capacity by
    week, and schedule adherence. Unknown `changes` keys raise an error rather than
    being ignored. Legacy surge keys still take the `scenario_engine.py` path, and any
    `changes` in history mode stop the run. See `Clark Scarf v2.md`'s "Scenario
    testing" section.
- Self-describing run manifests — every run records its own knobs, not just its output.

## The two versions

### Original (`models/twin/digital_twin.py`)

The reference implementation. Single-threaded, pandas-based, one fresh Python
interpreter per run: inputs re-read from disk each time, the policy (often well over a
million rows for a full rolling-horizon run) loaded with `itertuples`, Python loops per
shipped SKU and per active pair each simulated week, CSV output written by pandas and
read back by the caller. **This is the authoritative version** — every "bit-identical"
claim about the optimized port is a claim about matching *this* version's output exactly.

It reads its inputs as CSV from `input datasets/`. `orders.csv` there is currently a
Git LFS pointer, so the original's history mode cannot run on this checkout; forward
mode reads `demand_forecast.csv` and works. CLI: `--policy` is required. The defaults
are `--mode forward`, `--seed 2026`, `--run-name baseline_asis` and no `--state` (a cold
start). It writes CSV (`inventory.csv`, `replenishment_receipts.csv`, `production.csv`,
`policy.csv`, `service_summary.csv`, `service_by_node.csv`, `cost_summary.csv`) plus
`run_kpis.json` and `run_manifest.json`. Without `--scenario-config` these go to
`<out-root>/<run-name>/` (`<out-root>/projections/baseline/` when the run name is
`baseline`). With it, they go to `<out-root>/SCENARIO_####_Name/`, which also gets
`scenario_config.json`. Every run rewrites `output datasets/OUTPUT_DOCUMENTATION.md`.
The dataset generator (`supply_brain_generator_v5.py`) runs this version in history
mode. It writes `forward_policy.csv`, `opening_position.csv` and `acceptance.csv` to
`output datasets/history/` itself, and moves the historical detail tables into
`input datasets/`.

### Optimized port (`final_submission_optimized/digital_twin_fast.py`)

A from-scratch re-implementation of the same engine, rebuilt for speed. It uses the same
method and the same constants (imported from the original module), and
`OPTIMIZATION.md` reports its core physics as **bit-identical** to the original. It is
not the same program at its edges, though:

- **Inputs**: reads the `.parquet` copies of the input tables; policy and state may be
  CSV or Parquet.
- **CLI**: every argument is optional. `--run-name`, `--mode`, `--seed`, `--policy`,
  `--state`, `--data-dir` and `--out-root` default to the `digital_twin` block of
  `scenario_config.json` (or of `--scenario-config`). `--state` falls back to the config
  only in forward mode, so history mode cold-starts. It adds
  `--skip-output-documentation` (do not rewrite the shared OUTPUT_DOCUMENTATION.md) and
  `--lean-outputs` (skip the three large detail tables; the manifest records
  `lean_outputs.not_written`).
- **Outputs**: `inventory.parquet`, `replenishment_receipts.parquet`,
  `production.parquet` and `policy.parquet`; `cost_summary.csv`, `service_summary.csv`
  and `service_by_node.csv` stay CSV, plus `run_kpis.json`, `run_manifest.json` and
  `scenario_config.json`. There is always a scenario config, so the run folder is always
  `<output_root>/SCENARIO_####_Name/<run_name>/`, and a raw copy of the config is also
  written to the `SCENARIO_####_Name/` folder. With overrides, the override receipt goes
  to `<overrides.output_root>/SCENARIO_####_Name/receipts/twin_<run_name>.json`.
- **Scenarios**: the override engine described above exists only here.
- **In-process API**: `simulate()` and `simulate_task()` run the twin inside a worker
  and return KPIs plus window/SKU-group summaries.

On the shared core, the speed changes are:

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

**Caveat: these are the results `OPTIMIZATION.md` reports, not a re-run.** As
`tests/test_twin_equivalence.py` is written today, it passes
`output datasets/history/*.parquet` policy and state files to the original, whose
`_load_policy` reads them with `pd.read_csv`. Its build-notes comparison would also fail,
because the original records `.csv` source names where the port records `.parquet`
(e.g. `sku_sourcing_map.csv` vs `sku_sourcing_map.parquet`). The original's history mode
also needs `orders.csv`, which is an LFS pointer on this checkout. So the twin
equivalence claim cannot be re-confirmed as shipped. None of the tests above exercises
the override engine (economics, demand, opening-state, lead-time, outage, capacity or
adherence overrides).

## Advantages (optimized port)

- Reported bit-identical to the original across every test above (subject to the caveat
  under "Verification"). If that holds, the speed is "free": no result in this project's
  final-submission evidence base is at risk of being an artifact of the rewrite. Results
  that depend on overrides have no original to be checked against.
- Makes the project's heaviest validation passes (multi-seed robustness, the full
  per-cluster alpha and CSL sweeps, `run_all.py`'s complete evidence rebuild) tractable
  within a session — a ~9–10 hour overnight run on the original becomes something that
  can be iterated on same-day.
- Disk-conscious by default (Parquet instead of CSV cuts the full `run_all` output from
  ~11GB to ~8GB, with `run_all.py --lean-twin-outputs` taking it under 1GB; a single
  twin run takes the equivalent `--lean-outputs`).

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
- The port imports `ASSUMPTIONS` (written into every `run_manifest.json`) and
  `write_output_documentation` from the original, so both carry the original's text. In
  places that text no longer matches either engine: it says "~1.5 weeks" of FG cover
  (code: `FG_COVER_WEEKS = 2.5`) and says a parent "holds back its own expected demand"
  (code: it holds back policy `safety_stock`). Its note that only a demand surge is
  applied is true only for the original.
- `run_meio_then_twin.py` clears `<output_root>/projections/SCENARIO_####_Name/<run_name>`
  before a run, but the port writes to `<output_root>/SCENARIO_####_Name/<run_name>`. The
  folder it clears is not the one the twin overwrites.

## Why both versions still exist (neither fully phased out)

The original remains the authoritative reference implementation — every equivalence
claim about the optimized port is checked against it, and it is what a from-scratch
auditor should read first to understand the method itself, unobscured by the
performance engineering. The optimized port is what production and research tooling
actually execute, because the original's ~9–10 hour full-evidence-rebuild time is not
practical to re-run routinely. Documented as a pair deliberately, not as "old" vs. "new."

## Reactive arm (`final_submission_optimized/reactive_twin.py`)

A forward run of the optimized port in which MEIO **re-plans every week** from what the
twin has simulated so far. The twin's physics do not change: there is no expediting,
emergency ordering or reaction within a week. Only the policy table changes, week by
week. It is the third scenario arm, beside `prepared` (sized up front knowing the
scenario) and `unprepared` (today's policy).

- **Schedule**: week 1 runs the given policy (`--policy`, default the config's
  `digital_twin.policy`). Before each later week's review (w ≥ 2), MEIO re-sizes that
  week as of week w − 1, with no look-ahead. It uses the same sizing code
  `roll_forward_policy.py` uses (`meio_optimizer_weekly.size_rdc_cdc` plus
  `meio_weekly_lib.solve_pdc_policy`, with parameters from `meio_parameters.resolve`).
  That is 51 re-plans on a 52-week horizon. A new plan applies only to pairs active in
  week 1; other rows are ignored.
- **Surprise design**: the twin applies the scenario's *actual*-side overrides exactly
  as the other arms do, at the same seed. MEIO gets none of the plan-side overrides and
  learns only from what it observes.
- **Demand learning**: at each week close, the week's simulated customer demand
  (`wk_dem_cust`) is written into MEIO's demand panel as in-sample actuals. This is
  true, uncensored demand, CDC lost sales included. MEIO's trailing bias, CV and level
  then update through its normal estimators.
- **Lead-time learner** (`LeadTimeLearner`): it judges each lane only on shipments
  dispatched between `today − settle − 14` and `today − settle` days that arrived
  before today. Here settle = ceil(mean + 3·max(std, 0.05)) + 1 days, and the 14 days
  come from `RECENT_WEEKS = 2`. A lane needs at least `MIN_OBS = 30` draws. It is
  adjusted only if the observed mean differs from the expected truncated-normal draw by
  more than `DEADBAND = 5%` **and** by more than `Z_SIGNIFICANT = 3` standard errors. The
  ratio is clipped to [0.5, 3.0] and applied to the published mean. The std is rescaled
  only on lanes with a mean of at least `STD_MIN_MEAN_DAYS = 1.5` days. Adjustments are
  recomputed from the published table each week, not accumulated. The learner reads the
  continuous lead-time draws, so it does not see the twin's rounding up to whole days.
- **Not applied**: a legacy `changes.demand_surge_pct` config is silently not applied
  to the reactive twin (`build_inputs` is called without `scenario_changes`).
- **Outputs**: the usual twin files in `<output_root>/SCENARIO_####_Name/<run_name>/`
  (default run name `reactive`), plus `reactive_policy.parquet` (every week's plan) and
  `reactive_log.csv` (per week: lanes adjusted, mean lane ratio, the dairy bias factor,
  planned safety stock). The override receipt is `twin_<run_name>.json`. The manifest's
  `policy_source` names only the week-1 policy, and the reactive run does not rewrite
  OUTPUT_DOCUMENTATION.md.

## Files referenced

- `models/twin/digital_twin.py` (original, ~docstring quoted above)
- `models/MEIO/MEIO_Weekly/final_submission_optimized/digital_twin_fast.py`,
  `OPTIMIZATION.md` (primary source for the optimized-port section of this document)
- `models/MEIO/MEIO_Weekly/final_submission_optimized/reactive_twin.py` (reactive arm)
- `models/twin/scenario_engine.py` (legacy demand surge), `models/overrides/twin.py` and
  `models/overrides/compile.py` (override engine, port only)
- `scenario_config.json` (`digital_twin.entrypoint` points at the port), `run_meio_then_twin.py`
- `models/MEIO/MEIO_Weekly/final_submission_optimized/tests/test_equivalence.py`,
  `test_twin_equivalence.py`, `test_meio_parameters.py`, `verify_headline_full.py`
