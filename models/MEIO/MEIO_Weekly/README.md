# MEIO Weekly

Weekly-cadence sibling of `models/MEIO/meio_optimizer.py`. The annual script
produces one flat SKU x DC safety-stock number for the whole forward
horizon; this tool answers the question a planner actually asks every week:
**given everything known up to this week, how much stock should each DC
carry to cover next week?**

This is a rebuild — an earlier version of this tool (built during a prior
session) was lost from the repo and from disk before this one existed. This
version targets the `digital-twin` branch's dataset generator (v5) schema,
which differs from what the earlier version targeted.

## Run it weekly

```bash
python meio_optimizer_weekly.py --as-of-week 2025-W52
```

With no `--as-of-week`, it defaults to the latest week that has actual
demand on record. Output goes to the repo-tracked
`../../../output datasets/MEIO/<target_week>/` (alongside the other
deliverables' own `output datasets/<name>/` folders):

- `meio_recommendations_weekly.csv` — SKU x DC lines with target demand,
  lead time, service level, safety stock, reorder point, order-up-to.
- `meio_policy_weekly.csv` — the same policy in the
  `dc_id,sku_id,safety_stock,reorder_point,order_up_to_level` contract
  `models/twin/digital_twin.py --policy` expects.
- `meio_summary_by_dc_weekly.csv` — **the "stock to maintain" rollup by
  DC** that answers the top-level question.

To run this and `roll_forward_policy.py` (below) together, once each:
`python run_weekly_tools.py`.

Every run also appends one row to `meio_weekly_history.csv` (repo-tracked)
and redraws `chart_ss_trend.png` (repo-tracked) so you can see the
recommended safety-stock trend across weeks.

## Method — and the fixes it bakes in

Same newsvendor / RDC-CDC pooling method as the annual model (see its
docstring), with three corrections found necessary during backtesting of
the earlier version of this tool:

1. **Grain fix**: `demand_forecast.csv` is really `(period, sku, dc,
   b2b_customer)` grain — every loader here aggregates to true
   `(period, sku, dc)` totals before anything else touches it.
2. **Forecast-bias correction**: `forecast_qty` was found to persistently
   under-predict `actual_qty`. A z-score safety stock only buffers *random*
   variance — it cannot correct a persistent bias. Next week's forecast is
   multiplied by the trailing (13-week) actual/forecast ratio before
   sizing stock.
3. **Blended trailing CV**: a 13-week trailing window alone was found to
   understate full-year volatility. Demand uncertainty uses the average of
   a 13-week and 52-week trailing CV.
4. **DC-specific cost**: `cost_parameters.csv` is keyed by
   `(dc_id, subcategory)`, not `category` — holding and stockout cost are
   DC-specific, not one number per SKU. (The currently-committed annual
   `meio_optimizer.py` was found broken against this dataset for the same
   reason — `KeyError: 'category'` — and was fixed in the same change that
   added this tool.)

All of this lives in `meio_weekly_lib.py`, shared by all three scripts
below so the backtest and simulation validate the *exact* estimation logic
the live optimizer uses, not a re-implementation of it.

## Validate it: three independent checks

**Statistical CSL backtest** — replays historical weeks with no lookahead
and checks whether realized demand-during-lead-time actually stayed within
the recommended reorder point:

```bash
python backtest_csl.py --start-week 2025-W14 --end-week 2025-W48
```
Writes `calibration_summary.csv`, `chart_csl_calibration.png`,
`backtest_report.md` (tracked); full per-line detail goes to
`backtest_output/` (gitignored).

**Full twin simulation (frozen single-week policy)** — scores one MEIO
recommendation, computed once, against the dataset's own baseline policy
through the *same* shared physics engine (`models/twin/digital_twin.py`)
from the same opening state, across the full 2026 forward horizon:

```bash
python simulate_policy.py
```
Writes `simulation_summary.csv`, `chart_fill_rate_comparison.png`,
`simulation_report.md` (tracked); full twin run detail goes to
`simulation_output/` (gitignored). Useful for isolating demand-estimate
quality, but see the finding below for why it understates the tool.

**Full twin simulation (rolling weekly policy)** — the real test. Builds a
genuinely time-varying policy (`roll_forward_policy.py`, one row set per
(period, dc, sku) for every week of 2026) and scores it the same way:

```bash
python simulate_rolling_policy.py
```
Writes `rolling_simulation_summary.csv`, `chart_rolling_fill_rate.png`,
`rolling_simulation_report.md` (tracked); the 52-week policy itself and
full twin run detail go to `simulation_output/` (gitignored). This required
adding optional time-varying-policy support to `models/twin/digital_twin.py`
itself (an optional `period` column on the policy CSV; falls back to the
original single-static-policy behavior when absent — verified
byte-for-byte identical KPIs on the existing baseline run before trusting
any new result from it).

**Multi-seed robustness check** — a single simulation is one random
demand/lead-time/production draw. This re-runs baseline vs. MEIO rolling at
several `--seed` values (reseeds demand, lead-time AND production RNG
together, so each is a genuinely different world) to check the win isn't a
fluke of one draw:

```bash
python multi_seed_robustness.py --seeds 2026,7,42,123,2027
```
Writes `robustness_summary.csv`, `chart_robustness.png`,
`robustness_report.md` (tracked); full per-seed twin run detail goes to
`simulation_output/twin_runs/robustness/` (gitignored). Requires
`roll_forward_policy.py` to have been run first.

## Current finding — read before trusting the numbers

As of the 2026-09-25 dataset regeneration, **the weekly policy still
underperforms the baseline, but the gap has narrowed a lot** after matching
`solve_policy()`'s formula to `supply_brain_generator_v5.derive_baseline_
policy()` (protection interval = review + lead time, not lead time alone;
`ORDER_COVER_WEEKS=1.5` order-up-to cover with a 1.2x floor; per-echelon
sigma uplift; segment-based z instead of a cost-derived one — see point 5
in `meio_weekly_lib.py`'s module docstring for the full diagnosis):

| | v1 (bias/CV fixes only) | v2 (formula matched to baseline) | v3 (+ PDC coverage) | v4 (+ mean-blend) | v5 (+ de-seasonalization) |
|---|---|---|---|---|---|
| mean order_up_to_level vs baseline (matched SKU-DC lines) | 132 vs 185 (-29%) | 195 vs 185 (+6%) | 195 vs 185 (+6%, unchanged) | 195 vs 185 (+6%, unchanged) | 195 vs 185 (+6%, unchanged) |
| backtest achieved CSL vs target | n/a (99.8% target, cost-derived z) | 88.8% vs 99.8% | 88.8% vs 99.8% (unchanged) | 97.0% vs 95.5% | **97.6% vs 95.5%** |
| full-sim customer fill rate | 73.4% vs baseline 91.0% | 81.3% vs baseline 91.0% | 81.1% vs baseline 91.0% (flat) | 84.3% vs baseline 91.0% | 83.5% vs baseline 91.0% (**-0.8pp**) |
| full-sim internal_replen_fill_rate | 65.8% | 53.2% (**worse** than v1) | 83.4% (+30pp vs v2) | 86.5% (close to baseline's 89.1%) | 86.3% (flat) |
| full-sim pct_weeks_at_zero_stock | — | 32.0% | 32.5% | 16.3% | 17.2% (slightly worse) |
| full-sim total landed cost delta | +$6.3M | +$3.3M | +$3.5M (flat/slightly worse) | +$2.6M | +$2.65M (slightly worse) |

Steady, real progress across all four rounds, but baseline still wins.
v3 (PDC coverage) taught something worth keeping in mind: it fixed the
metric it targeted (`internal_replen_fill_rate` +30pp) but didn't move
customer fill rate at all, because the actual cause turned out to be
upstream of the twin entirely -- comparing `service_by_node.csv` between
runs showed **every** node worse (not concentrated) while `avg_on_hand` was
simultaneously *higher* at 18 of 22 nodes, which ruled out "not enough
stock" and pointed at demand-mix misallocation instead. That led to v4's
fix (point 7 in `meio_weekly_lib.py`'s docstring): 38% of active SKU-DC
pairs had a literal $0 forecast for any single target week despite real
annual demand (worst for Seasonal-segment SKUs), zeroing their entire
policy. Blending the target week's forecast with the trailing long-window
mean fixed it -- the single biggest jump of any round so far.

**v5 (de-seasonalization, replacing v4's flat trailing-mean fallback):**
backtest CSL improved again, 97.0% -> **97.6%** vs 95.5% target. But the
full simulation went slightly the other way: customer fill rate 84.3% ->
**83.5%**, total landed cost delta +$2.6M -> **+$2.65M**. A real, if small,
regression on the metric that matters, despite improving on the metric
de-seasonalization targeted.

**Why, and what it means:** the backtest checks one-week-ahead accuracy
across ~35 *different* as-of weeks -- exactly what de-seasonalization is
built for, and it helped. `simulate_policy.py` does something structurally
different: it builds ONE policy as-of 2025-W52 (target 2026-W01) and holds
it *static* for the entire simulated 2026 year, because
`digital_twin.py --policy` has no time dimension. 2026-W01 happens to be a
seasonally *elevated* week for several subcategories (portfolio-wide
forecast is 9% above the 2026 annual mean that week) -- so de-seasonalizing
specifically *for that week* pulled the one-shot policy slightly further
from the full-year average baseline actually uses (`derive_baseline_
policy()`'s "forward" basis is explicitly the mean across all 52 forward
weeks, never a single week). Three rounds of refining the single-week
demand estimate (raw forecast -> mean-blend -> de-seasonalized
reconstruction) have each helped the backtest but hit diminishing-to-negative
returns on the full simulation, which is evidence the remaining sim gap is
capped by this structural mismatch, not by demand-estimate quality anymore.

**v6 (rolling weekly policy, `roll_forward_policy.py` + `simulate_rolling_
policy.py`) — this closed it.** Built a policy that actually varies week to
week across the full 2026 horizon (52 x ~7,768 SKU-DC-PDC rows) instead of
freezing one week's snapshot, and added optional time-varying-policy
support to `models/twin/digital_twin.py` to score it properly (backward
compatible -- the existing frozen-policy runs reproduce byte-for-byte
identical KPIs).

| | v5 (frozen single week) | **v6 (rolling, 52 weeks)** |
|---|---|---|
| customer fill rate | 83.5% vs baseline 91.0% | **92.6% vs baseline 91.0% — WINS by +1.6pp** |
| customer fill rate, CDC | 73.1% | **81.0%** vs baseline 78.6% |
| customer fill rate, RDC direct | 85.7% | **95.1%** vs baseline 93.6% |
| total landed cost delta | +$2.65M | **+$0.56M** (holding +$71K, transport +$1.18M, stockout **-$689K**) |
| internal_replen_fill_rate | 86.3% | 80.8% (down -8.2pp vs baseline's 89.1% -- a real tradeoff, not free) |

Four rounds of refining the single-week demand estimate under a frozen
policy (bias correction -> formula matching -> PDC coverage ->
de-seasonalization) plateaued at an 8-17pp fill-rate gap and increasingly
diminishing or negative returns. Letting the policy actually vary week to
week closed essentially all of it in one round -- strong evidence the
earlier gap was structural (testing a "recompute weekly" tool by freezing
it for a year), not a demand-estimation problem, exactly as v5's regression
hinted.

**Scope honesty, two things NOT to over-claim from this:**
1. This isn't a full simulation of "real" weekly operation. Every one of
   the 52 weekly policies is anchored to the SAME fixed as-of point (the
   last real week of 2025) for its trailing bias/CV/de-seasonalized-level
   statistics -- it does not close the loop of each week learning from the
   twin's own simulated 2026 outcome so far. What genuinely varies week to
   week is each week's own already-published `forecast_qty` and seasonal
   index. See `roll_forward_policy.py`'s docstring for the full reasoning.
2. `internal_replen_fill_rate` dropped -8.2pp even as customer fill rate
   rose +1.6pp -- the rolling policy is trading upstream replenishment
   smoothness for downstream customer service, not a free lunch. Worth
   understanding before treating this as a finished result.

**v7 (PDC look-ahead) — barely moved it.** `solve_pdc_policy()` was sizing
PDC throughput off only the current week's RDC pull; gave it 3 weeks of
look-ahead (`lookahead_rdc_demand()`, `PDC_LOOKAHEAD_WEEKS=3`) on the theory
that production needs lead time to build ahead of a ramp.
`internal_replen_fill_rate` moved 80.8% -> 81.0% -- +0.2pp against an
8.2pp gap. Wrong diagnosis, or at best a minor contributor.

**v8 (plant capacity, wired in and ruled out) — the real diagnosis.**
Checked `service_by_node.csv` instead of guessing again: BOTH PDC
(fill_rate_replen -6.2pp) and RDC (-10.1pp) internal fill dropped together,
with `replen_demand` itself higher almost everywhere under the rolling
policy -- it demands more total internal replenishment volume
network-wide, not just later than a 3-week look-ahead could catch. That
looked like a production-capacity ceiling, so `clip_to_plant_capacity()`
wires in `plant_capability.csv` (per-plant, per-production-family weekly
capacity) and `plant_master.csv` (plant-wide capacity), mirroring the
twin's own two-stage production clip exactly (family line, then
plant-wide -- see `models/twin/digital_twin.py` ASSUMPTIONS) so a PDC
recommendation is never a target the plant cannot physically build.

**Result: zero capacity-clipped lines across all 52 weeks.** Not just an
absence of log lines -- checked the actual numbers: required PDC
throughput peaks at 40-50% of stated plant capacity, at every plant, every
week of the rolling horizon. Real headroom, not a units bug (cross-checked
that summed family capacities exceed plant-wide capacity at every plant by
~9%, which is the expected shape if plant-wide is meant to be the tighter
constraint). **Capacity is not the bottleneck.** Ruling this out cleanly,
with real data wired in rather than assumed, is the actual value of this
round -- the -8.2pp gap is not costing real throughput headroom.

The internal_replen_fill_rate gap remains only partially explained. Most
likely candidate now: the rolling policy's week-to-week order-up-to swings
are bursty relative to a fairly thin PDC safety-stock buffer
(`SIGMA_UPLIFT["PDC"] * 0.5` in `solve_pdc_policy`) -- a buffer-sizing /
timing problem, not a capacity ceiling. Not yet fixed. Two candidate
levers, not yet tried: strengthen the PDC safety-stock multiplier, or
smooth the week-to-week order-up-to swings directly (the original
suggestion, set aside in favor of investigating capacity first since
smoothing trades away some of the fill-rate gain that made v6 a win).

**v9 (multi-seed robustness check) — the win generalizes, not a fluke of
one draw.** Every result above (v1-v8) used the twin's default seed (2026)
for one specific demand/lead-time/production-adherence realization -- a win
on one draw isn't evidence of anything by itself. `multi_seed_robustness.py`
re-ran baseline vs. MEIO rolling at 5 different seeds (`--seed` reseeds
demand, lead-time AND production RNG together, so each is a genuinely
different world, not just noisier demand):

| seed | fill rate delta | cost delta |
|---|---|---|
| 2026 | +1.8pp | +$518K |
| 7 | +1.3pp | +$816K |
| 42 | +1.4pp | +$774K |
| 123 | +1.4pp | +$558K |
| 2027 | +1.5pp | +$670K |

**MEIO rolling beats baseline on fill rate in 5/5 seeds.** Fill rate delta:
mean +1.48pp, std only 0.18pp (range +1.26 to +1.76pp) -- tight and
consistently positive, not noisy or seed-dependent. Cost delta: mean
+$667K, std $130K -- also consistently positive (MEIO costs more, every
seed). This is real evidence the fill-rate win generalizes across random
demand/lead-time realizations *within this dataset's structure*.

**What this does NOT test** -- worth being precise about, since the whole
point of this check was not overclaiming generalization: different seeds
resample noise around the SAME underlying demand distribution, network
topology, cost structure and capacity. It says nothing about whether the
method would hold on a different business's demand mix, seasonality
pattern, or network shape. Any future smoothing/buffer parameter (see the
`internal_replen_fill_rate` tradeoff below) should be grounded in a
physical quantity (lead time, review cadence) rather than fit to hit a
target number on this one dataset, and re-validated across these same 5
seeds before being trusted.

**v10 (demand smoothing for PDC, bullwhip-effect mitigation) — held the
win, still didn't close the internal-fill gap.** `smoothed_rdc_demand_for_
pdc()` (formerly `lookahead_rdc_demand()`) now averages RDC target_mean
over a `PDC_SMOOTH_WEEKS=5` window CENTERED on the target week instead of
forward-only -- standard bullwhip-effect mitigation (decouple a
production-constrained node from raw demand noise, leave RDC/CDC's own
sizing untouched), with the window grounded in `PDC_COVER_WEEKS`
(ceil(4.5)=5, the horizon PDC is already sized to buffer) rather than fit
to this dataset's KPI, per the v9 scope caveat above.

Re-ran the full 5-seed robustness check: **fill-rate win held exactly**
(mean +1.52pp vs +1.48pp pre-smoothing, still 5/5 seeds, cost tradeoff
unchanged at ~+$675K mean) -- this round cost nothing. But
`internal_replen_fill_rate` barely moved: 81.2% vs 81.0% pre-smoothing at
the default seed, ~80% across all 5 seeds, still well below baseline's
~88-89%. **Three independent fixes now (look-ahead v7, capacity-clip v8,
centered smoothing v10) have each moved this specific metric by ~0.2pp or
less.** Reading that as a real result: PDC's ACTUAL pull volume is
generated by RDC's real, intentionally-unsmoothed ordering behavior (the
same volatility that wins the customer-fill-rate gain) -- smoothing what
PDC is SIZED against doesn't reduce what it's actually asked for.
Meaningfully closing this would require either smoothing RDC/CDC's own
policy (which would give back the fill-rate win) or a substantially larger
PDC safety-stock allocation (a real cost, not a formula tweak) -- left
documented as a structural tradeoff rather than force-fit further.

**Bottom line:** customer fill rate 92.6-92.8% vs baseline's ~91% (robust
across 5 seeds, +$0.5-0.8M/year cost), internal_replen_fill_rate ~80-81%
vs baseline's ~88-89% (a real, understood, apparently structural
tradeoff). That is where this stands.

Reported honestly rather than smoothed over.
