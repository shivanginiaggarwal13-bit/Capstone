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

**Clark-Scarf v2 at alpha=0.2, netting_discount=0.0, cluster-based service
levels**, a continuous-pooling-factor, Monte Carlo-DDLT echelon model
(`clark_scarf_v2.py`), chosen over the hand-tuned MEIO Weekly tool because it
holds up better against **real replayed 2025 demand**, not just synthetic
noise — and because its core mechanism (deciding how much safety stock an
RDC pools with the CDCs it feeds) is a direct answer to the charter's own
ask: decide not just how much buffer to hold, but **where**.

Two fixes are now both live defaults, not opt-in flags — see "Netting-discount
fix" and "Cluster-based service level" below for each one's own validation:
- `netting_discount=0.0` removes the original model's assumption that an
  RDC's pooled safety stock can be credited against what its CDCs already
  hold. Won on every axis tested, 5/5 seeds, no tradeoff found.
- **Service level (z) now comes from the canonical, LIVE Clustering &
  Segmentation output** (`output datasets/segmentation/sku_clusters.csv` —
  currently segmentation_v5, 6 archetypes), not `sku_master.csv`'s baked-in
  3-way segment label (which the dataset generator supplies directly — not
  something this project's analysis produced). Project decision
  (2026-10-18): adopted as the default regardless of whether it
  statistically "beats" the old segment ladder, because it's the
  segmentation workstream's actual deliverable. Updated again the same day
  to read the *canonical* output instead of a frozen `segmentation_v4`
  snapshot — found that the front-end's "Run segmentation" button
  overwrites the canonical file, which the frozen path never saw, so a
  planner re-running segmentation had no effect on what MEIO sized
  against. See "Cluster-based service level" below for the validation
  caveat this introduces.

| | Actual (as happened) | MEIO Weekly | **Clark-Scarf v2 (chosen)** |
|---|---|---|---|
| Customer fill rate, real 2025 demand | 93.08% | 93.23% (+0.15pp) | **94.51% (+1.43pp)** |
| Internal replenishment fill | 93.21% | −4.60pp | **82.38% (−10.83pp, still degraded, but the best achieved so far)** |
| Total landed cost delta | — | — | **+$1.67M** |

These are a fresh, single-seed real-data check with the netting fix and the
canonical (v5) segmentation link both applied together
(`results/headline_clark_scarf_v2_vs_actual.csv`,
`results/headline_cost_summary.csv` — regenerated 2026-10-18; not yet
multi-seed confirmed as a *combined* configuration — see "Next steps").
MEIO Weekly's own real-data result (context for why it wasn't chosen) is in
`results/meio_weekly_vs_actual_report.md` and `results/meio_weekly_vs_actual.csv`
(its own cost breakdown, computed separately, is in
`results/meio_weekly_vs_actual_cost.csv`) — not regenerated under the new
cluster-z default, since MEIO Weekly wasn't the chosen model either way.

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

## Cluster-based service level adopted as default (2026-10-18)

`meio_weekly_lib.py::assign_z()` previously defaulted to `sku_master.csv`'s
baked-in 3-way segment (`Fast-Moving`/`Seasonal`/`Intermediate` →
z=2.05/1.64/1.28) — a label the dataset generator supplies directly, not
something this project's own analysis produced. The Clustering &
Segmentation deliverable has always computed a per-cluster `safety_factor_z`,
but it sat behind an opt-in override seam (`Z_OVERRIDE_BY_SKU`), used only
by research scripts testing it, never by the live pipeline.

**Project decision: adopt it as the default regardless of whether it
statistically beats the old segment ladder on a validated-rule test.** It's
the segmentation workstream's actual deliverable; sizing stock off a label
the dataset happened to ship with, instead of off the team's own
segmentation analysis, was the bigger problem to fix. (A parallel branch's
`meio_parameters.py` implements a formal "only adopt if it wins" gate for
this same decision — this project explicitly chose not to wait on that
gate here.)

**Second fix, same day: the link was frozen to one segmentation version,
not the live canonical output.** The first version of this change pointed
`assign_z()` at `Clustering & Segmentation/segmentation_v4/sku_clusters.csv`
— a specific, static file. That meant a planner clicking "Run segmentation"
in the front-end app (which overwrites `output datasets/segmentation/`, the
canonical location) had **zero effect** on what MEIO actually sized
against — the frozen v4 path never changed. Fixed by re-pointing at
`output datasets/segmentation/sku_clusters.csv` instead: whatever a
planner's most recently approved segmentation run produced is now what the
next MEIO build genuinely uses.

**This closes the wiring gap but opens a real validation gap, stated
directly.** Canonical segmentation is currently **v5**, with **6**
archetypes, not v4's 4:

| v4 (4 clusters, what all multi-seed evidence was built on) | v5 (6 clusters, now live) |
|---|---|
| Flagship Promo-Driven, Mid-Tier Core → z=1.881 | Flagship Core, Mid-Tier Core → z=1.881 |
| Long-Tail Erratic, Long-Tail Supply-Risk → z=1.282 | Long-Tail Erratic, Long-Tail Erratic 3, Long-Tail Supply-Risk → z=1.282 |
| — | **Long-Tail Erratic 2 → z=1.717 (NEW — never tested against real demand)** |

Every multi-seed validation this project has — the netting-discount
decision, the per-cluster alpha sweep, the per-cluster CSL sweep — was run
against v4's 4-cluster, 2-distinct-z scheme specifically. v5's extra
granularity, and especially the new z=1.717 value, have not been separately
validated. The headline table above is real and was generated against the
live v5 link, but "is 1.717 the right target for Long-Tail Erratic 2" is an
open question, not a validated answer.

`assign_z()` now resolves, in order: `Z_OVERRIDE_BY_SKU` (explicit, for
research scripts only) → the canonical segmentation's cluster ladder (the
live default) → `Z_BY_SEGMENT` (fallback for any SKU the cluster file
doesn't cover — none observed so far, defensive only) → `Z_DEFAULT`.

**Also found and fixed while making the first version of this change**:
`netting_discount`'s adopted value (0.0) had never actually been wired into
the pipeline's *defaults* — `build_walkforward_policy_csv2()`,
`clark_scarf_v2.solve()`, and `build_rolling_policy()` all still defaulted
to `1.0` (the original, pre-fix value), so `stage_headline()`,
`stage_alpha_sweep()`, `stage_multiseed()`, and `segment_alpha_explore.py`
had all been silently running at the stale default since the fix was
"adopted" — only scripts that passed it explicitly
(`netting_discount_sweep.py`, `service_level_real_data.py`,
`scenario_test.py`) were actually using it. Fixed by changing the defaults
themselves, not just documenting the recommendation in prose.

**Validated**: a fresh real-data check with the netting fix and the live
v5 link together reproduces the headline table above — still beats the
original (pre-fix, segment-z) configuration on every axis.
**Not yet validated**: multi-seed confirmation of this exact combined
configuration, or any dedicated test of v5's specific clusters/z-values the
way v4's were tested (segment_alpha_real_data.py / service_level_real_data.py
both still hardcode the v4 4-cluster list).

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

1. **Decide whether to validate v5 directly, or fall back to v4 until it
   is.** `segment_alpha_real_data.py` and `service_level_real_data.py` both
   still hardcode v4's 4-cluster list — none of this project's multi-seed
   evidence says anything about v5's 6 clusters, and the new z=1.717 value
   (Long-Tail Erratic 2) has no real-data evidence behind it at all. Either
   re-run the sweeps against v5's archetypes (more work, but validates what's
   actually live now), or treat v4 as the validated reference and keep
   canonical segmentation's output reconciled to those 4 groupings until v5
   earns its own evidence.
2. **Multi-seed confirmation of the combined configuration** (live
   segmentation z + netting_discount=0.0 together). The single-seed check
   above is promising but this exact combination has never been run 5
   seeds deep — every prior multi-seed confirmation (netting-discount at
   α=0.2 and α=0.6, the per-segment alpha sweep, the service-level sweep)
   was run under the *old* segment-based z default. The current headline
   table rests on one seed.
3. **Re-point or re-run the evidence that was computed elsewhere under the
   old z default.** Checking GitHub (2026-10-18) found that a teammate's
   branch (`multi_agent_orchastration`) already completed both sweeps this
   README previously listed as "partially run" — `segment_alpha_real_data_
   summary.csv` (full v4 4-cluster x 6-alpha x 5-seed grid) and
   `service_level_real_data_summary.csv` (full v4 4-cluster x 4-CSL x
   5-seed grid) both exist there, plus a 5-seed netting_discount
   confirmation at alpha=0.6. None of that work has been merged into this
   branch, and all of it predates both the cluster-z default and the v5
   canonical link — so even once merged, it describes an older
   configuration, not today's.
4. **Find the rest of the internal-replenishment-fill gap.** The
   netting-discount fix closed some of it (82.11% → 82.38% with the live
   segmentation z added) but 93.21% is still ~11pp away. Likely candidates:
   the PDC real-variance formula fix (below, unresolved), or the RDC's
   `order_up_to_level` sizing more generally.
5. **Fresh-holdout confirmation** once more real weeks (2026 actuals) are
   available — everything here is still one real year.
6. **PDC real-variance formula fix.** `meio_weekly_lib.py::solve_pdc_policy`
   sizes PDC safety stock as `z * sqrt(lt_mean_wk) * mu * SIGMA_UPLIFT["PDC"]
   * 0.5` — using throughput mean (`mu`) as a stand-in for variability
   instead of a real measured σ from RDC pull-order history. Not started.
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
