# MEIO — Final Submission: Clark-Scarf v2

Start here. This folder has everything behind the model-building recommendation;
the broader exploration history (5 models, 10 tuning rounds, bugs found and
fixed) lives in `..` and `../alt_models/` for anyone who wants the full process,
but isn't required reading to evaluate the recommendation itself.

**To reproduce everything end to end** (not required — `results/` already
has the computed evidence): `python run_all.py`, ~9-10 hours, meant for an
overnight/unattended run. See "Files" below for what it runs and in what
order.

## The recommendation

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

| | Actual (as happened) | MEIO Weekly | **Clark-Scarf v2 (chosen)** |
|---|---|---|---|
| Customer fill rate, real 2025 demand | 93.08% | 93.23% (+0.15pp) | **94.83% (+1.75pp)** |
| Internal replenishment fill | 93.21% | −4.60pp | **82.11% (−11.10pp, still degraded, but better than nd=1.0's 81.99%)** |
| Total landed cost delta, 5-seed mean | — | — | **+$1.83M (vs. +$1.95M at nd=1.0 — cheaper, not just better)** |

Full detail: `results/headline_report.md`,
`results/headline_clark_scarf_v2_vs_actual.csv`, `results/headline_cost_summary.csv`,
`results/chart_headline.png` (these still reflect the pre-fix `netting_discount=1.0`
run — see "Netting-discount fix" below for the current recommended
configuration's numbers). MEIO Weekly's own real-data result (context for
why it wasn't chosen) is in `results/meio_weekly_vs_actual_report.md` and
`results/meio_weekly_vs_actual.csv` (its own cost breakdown, computed
separately, is in `results/meio_weekly_vs_actual_cost.csv`).

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

**Validated** (real demand, no lookahead, multi-seed where it matters):
- The headline number above.
- Alpha=0.2 is *not* the best setting on real data — it's second-worst.
  Fill peaks near alpha=0.6, cost is lowest near alpha=0.4
  (`results/alpha_sweep_report.md`, `results/chart_alpha_sweep.png`).
- Alpha=0.4 and alpha=0.6 both hold up 5/5 seeds
  (`results/multiseed_report.md`) — but **both were chosen and tested on the
  same single real year (2025)**, so this is not yet a fresh-holdout
  confirmation. We are explicitly not adopting 0.6 over 0.2 on this basis
  alone — see "Known limitations" below.

**Exploratory / diagnostic only** — real signal, not yet production-grade validation:
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
  meaningful signal for them. **Net: "long-tail wants less pooling" is
  specifically a fill-rate finding. Don't conflate it with a cost-saving
  claim — on cost alone, more pooling looks better for every cluster, for
  the same reason it's risky for internal supply.**

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
  that fix, `results/netting_discount_sweep_alpha0_2.csv` shows a smooth,
  monotonic improvement as netting is reduced — 94.77%/82.14% fill at
  `nd=0.0` vs. 94.27%/81.99% at `nd=1.0` (the original), at *lower* cost.
- **Multi-seed confirmation** (`netting_discount_sweep.py --multiseed`, 5
  seeds, `results/netting_discount_multiseed.csv`): `netting_discount=0.0`
  beats `netting_discount=1.0` on every axis, 5/5 seeds, tight std:
  customer-fill delta vs. actual +1.75pp (σ=0.07) vs. +1.22pp (σ=0.05);
  internal replenishment fill 82.11% (σ=0.0007) vs. 81.94% (σ=0.001); cost
  delta +$1.83M (σ=$51k) vs. +$1.95M (σ=$42k). No tradeoff found at this
  alpha — **`netting_discount=0.0` is adopted as part of the recommended
  configuration.**

Caveats this does *not* resolve: internal replenishment fill is improved
(82.11% vs. 81.99%) but still badly degraded relative to actual (93.21%) —
removing the netting discount is not a full fix, just a real improvement.
Only tested at alpha=0.2, one real year, 5 seeds of one RNG family — see
"Next steps."

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
  `customer_dc_master.csv`/`regional_dc_master.csv`'s own `region` column
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
demand ("if we do"):

| Arm | Customer fill | Total landed cost |
|---|---|---|
| A: baseline | 93.99% | $36.69M |
| B: unprepared (scenario hits, no prep) | 93.93% | $36.91M |
| C: prepared (scenario-aware policy) | **94.02%** | $36.94M |

The recommended stocking change landed in **exactly** the 6 nodes actually
in the Southeast (CDC03/04/05/06, RDC02/03) — no false positives or
negatives among 23 total nodes — each getting +1.4–2.0% more safety stock,
every other node within noise (±0.2%) of unchanged. B→C (the value of
preparing) is +0.09pp fill at roughly flat cost; A→B (the scenario's raw
impact if ignored) is −0.06pp fill, +$226K cost.

**Known gaps, stated directly:**
- **Only CDC/RDC policy is scenario-aware — PDC sizing is not wired.**
  Confirmed in the test run: every PDC showed exactly 0.00% stocking
  change. `meio_weekly_lib.py::solve_pdc_policy` would need the same
  treatment.
- **Only demand surge is implemented.** Lead-time and capacity disruption
  types are scoped (see memory) but not built — `digital_twin.py`'s own
  "Known limitations" section says so explicitly.
- **Single seed, synthetic basis, one scenario tested.** This is a
  working-example validation, not a multi-seed/multi-scenario one — treat
  it as "the mechanism works," not "this exact 0.09pp number is robust."

## Known limitations — stated up front, not left for a reviewer to find

- **Write-off minimization** (one of the charter's three named KPIs) is not
  measurable in this dataset at all: no shelf-life or obsolescence modeling,
  `write_offs.csv` is empty by design.
- **Working capital** is a mixed result, not a clean win: safety stock value
  fell 9.1%, but total inventory value rose 9.1% (cycle stock grew faster
  than safety stock shrank).
- **Internal replenishment fill still degrades under every model tested**
  even after the netting-discount fix above — improved (82.11% vs. the
  original 81.99%) but nowhere near actual's 93.21%. The netting step was
  only one contributor; something else is also at play (see "Next steps").
- **One real year of validation data.** `demand_forecast.csv` only has rows
  from 2025 onward even though real orders go back to 2023, so every
  real-data result here is tested against a single year. As 2026 actuals
  accumulate, that becomes a genuine second holdout — none of today's
  real-data numbers have had that check yet.

## Files

- `clark_scarf_v2.py` — the model (Monte Carlo DDLT sizing, continuous alpha,
  `ALPHA_OVERRIDE_BY_SKU` seam for the per-segment exploration).
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
  robustness check of `netting_discount` in {0.0, 1.0} at alpha=0.2.
- `segment_alpha_real_data.py` — the real-data, multi-seed version of
  `segment_alpha_explore.py`'s diagnostic (see "Next steps" #4). Same
  4-cluster x 6-alpha sweep, but against real replayed demand and across 5
  seeds instead of 1. Parallel (`ProcessPoolExecutor`, default 4 workers)
  and resumable — a combo already on disk is skipped on restart.
  `--workers N`, `--cluster "<name>"` for one cluster, `--summarize-only`
  to just re-aggregate. ~1.5-2 hours for a full run at the default worker
  count; **partially run as of this commit (a handful of combos only) —
  not yet a complete sweep.**
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
  `--summarize-only`. ~1.5-2 hours for a full run. **Just started as of
  this commit (one correctness-check combo only) — not yet a complete
  sweep; see `output datasets/agent output/service_level_real_data_
  summary.csv`.**
- `scenario_test.py` — Feature 2 (front-end scenario-testing agent): given
  a scenario (`models/twin/scenario_config.example.json`'s schema -- so
  far only `demand_surge_pct` + `affected_region`/`affected_nodes` +
  `duration_weeks`/`start_week_offset` actually does anything; see
  `models/twin/scenario_engine.py`), builds a NEW stocking recommendation
  that anticipates it and runs three arms: A) baseline policy, normal
  demand; B) baseline policy, SCENARIO demand ("if we don't prepare");
  C) scenario-reoptimized policy, SCENARIO demand ("if we do"). Verified
  with the example config (25% Southeast demand surge, 4 weeks): the
  policy recommended MORE safety stock in EXACTLY the 6 nodes actually in
  that region (no false positives/negatives among 23 total nodes), and the
  prepared arm beat the unprepared one by +0.09pp fill at roughly flat
  cost. `python scenario_test.py --scenario <path-to-config.json>`.
  **Known gap: only CDC/RDC policy is scenario-aware; PDC sizing isn't
  wired yet** (confirmed in the test run -- PDCs showed exactly 0% change).
- `run_all.py` — runs the scripts above back to back, in an order chosen
  for cache reuse (`validate_real_data.py` first, since its baseline caches
  are reused by the netting-discount, segment-alpha-real-data and
  service-level steps). For an unattended/overnight run: logs each step to
  its own file under `run_logs/`, continues past a failed step instead of
  aborting, prints a final pass/fail summary. `--from N` to skip ahead
  (e.g. `--from 6` to just finish the two real-data sweeps). ~9-10 hours
  end to end.
- `results/` — every CSV, chart, and report cited above. Already computed;
  you don't need to re-run anything to read the evidence. The CSVs and the
  `own_fill`/`sweep` numbers are the direct output of `validate_real_data.py`
  and `segment_alpha_explore.py`'s own default (full-loop, no `--stage` flag)
  runs — both were executed end to end with zero errors, and every number
  reproduced the project's previously-validated results exactly. The
  narrative `*_report.md` files and charts predate the consolidation into
  this folder and are carried over unchanged (the underlying data didn't
  change, so they're still accurate).
- `validation_output/` — gitignored. Full twin run detail (inventory.csv,
  receipts, etc.) per run; regenerate by re-running the scripts above.
- `../../../../output datasets/agent output/` (i.e. the repo's
  `output datasets/agent output/`) — the subset of results that directly
  feed the two front-end agent features (service-level recommendation,
  scenario testing), kept separate from the broader research `results/`
  above: `segment_alpha_real_data_{multiseed,summary}.csv`,
  `service_level_real_data_{multiseed,summary}.csv`, and
  `scenario_<name>_{comparison,stocking_by_dc}.csv` per scenario tested.
  `segment_alpha_real_data.py`, `service_level_real_data.py` and
  `scenario_test.py` all write here directly.

## Next steps, in priority order

1. **Find the rest of the internal-replenishment-fill gap.** The
   netting-discount fix closed some of it (82.11% vs. 81.99%) but 93.21% is
   still 11pp away. The netting step wasn't the only cause — likely
   candidates: the PDC real-variance formula fix (see below, also
   unresolved), or the RDC's `order_up_to_level` sizing more generally.
2. **Test netting_discount at alpha=0.6** (the real-data fill-optimal
   alpha, not yet combined with this fix) and consider whether
   netting_discount should itself vary by alpha or by segment.
3. **Fresh-holdout confirmation of netting_discount=0.0 and alpha 0.4/0.6**
   once more real weeks (2026 actuals) are available — everything here is
   still one real year.
4. **Multi-seed validation of the per-segment alpha direction** before it's
   anything more than a diagnostic. `segment_alpha_real_data.py` now exists
   to do exactly this (real demand, 5 seeds, same 4-way split as the
   synthetic diagnostic) but is only partially run — finish it with
   `python segment_alpha_real_data.py` (resumable, ~1.5-2 hrs for the rest)
   or `run_all.py --from 6`, then re-read `output datasets/agent output/
   segment_alpha_real_data_summary.csv` before trusting any per-cluster
   alpha claim. A 2-way (core vs. long-tail) split rather than the full
   4-way may still be the better final call, to limit overfitting risk —
   the full run will show whether the 4-way direction holds up or not.
5. **PDC real-variance formula fix.** `meio_weekly_lib.py::solve_pdc_policy`
   sizes PDC safety stock as `z * sqrt(lt_mean_wk) * mu * SIGMA_UPLIFT["PDC"]
   * 0.5` — using throughput mean (`mu`) as a stand-in for variability
   instead of a real measured σ from RDC pull-order history. Not started.
6. **Finish Feature 1's service-level sweep** (`service_level_real_data.py`
   — see that section above and its own "not yet run" note).
7. **Wire PDC policy into the scenario engine.** `scenario_test.py`'s test
   run confirmed PDCs don't respond to a scenario at all (`solve_pdc_policy`
   isn't touched) — a demand surge big/long enough to matter at the plant
   level would currently be invisible to PDC sizing.
8. **Build the lead-time and capacity disruption scenario types.** Only
   demand surge exists; `scenario_engine.py`'s docstring and
   `digital_twin.py`'s "Known limitations" section both say so. Same
   pattern as demand surge (scale an already-computed value, don't touch
   RNG draws) should apply to both.
9. **Multi-seed / multi-scenario validation of the scenario-testing
   feature** before the +0.09pp "value of preparing" number (or any future
   scenario's number) is treated as more than a single working example.
