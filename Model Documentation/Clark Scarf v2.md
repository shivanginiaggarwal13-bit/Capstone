# Clark-Scarf v2 (Continuous Pooling + Monte Carlo DDLT)

**Status: ADOPTED — the project's current production MEIO model**, chosen over the
hand-tuned MEIO Weekly tool and every other alternative tried. Still under active
tuning as of the end of this project (the per-cluster alpha and v5-segmentation
validation work was ongoing, not closed out).

Source: `models/MEIO/MEIO_Weekly/final_submission/clark_scarf_v2.py` (research version)
and `models/MEIO/MEIO_Weekly/final_submission_optimized/clark_scarf_v2.py` (a bit-identical,
~40x faster polars/vectorized port used for production — see `Digital Twin.md` for the
engineering story). README: `models/MEIO/MEIO_Weekly/final_submission/README.md`.

## Definition

Clark-Scarf v1 generalized into a continuous model: instead of choosing all-or-nothing
pooling (v1) or no pooling at all (MEIO Weekly), an RDC's local safety stock is a
**continuous blend**, controlled by a pooling factor **alpha**, between those two
previously-validated endpoints:

```
RDC_local_SS(alpha) = alpha * pooled_netted_SS + (1 - alpha) * independent_SS
  alpha = 0  ->  no pooling at all (MEIO Weekly's answer: RDC sized independently)
  alpha = 1  ->  full Clark-Scarf pooling (v1's answer: RDC's local SS = echelon-pooled
                 SS netted against what CDCs already hold)
```

Two upgrades over v1 motivated this version, both found by comparing the project's work
against an external reference slide ("Accenture FY27 Brainstorm"):

1. **Continuous alpha**, not v1's hard either/or choice (and not GSM's binary
   stock/no-stock heuristic either). Because the safety-stock blend is linear in alpha by
   construction, a closed-form cost-formula optimization would degenerate to a corner (0
   or 1) — not useful. Alpha is instead found the same way the Sim-Optimize alt model
   finds its own parameters: grid search, with the digital twin as the reward oracle. The
   twin's own stockout-cost/fill-rate response to alpha is genuinely nonlinear even
   though the safety-stock formula itself blends linearly — searched honestly, not
   shortcut.
2. **Monte Carlo demand-during-lead-time (DDLT)**, replacing every closed-form `z·sigma`
   calculation. Demand is drawn from a **Gamma** distribution fit to (target_mean,
   target_std) by moment matching — Gamma is defined only for x ≥ 0 and right-skewed,
   which fits actual demand shape far better than a Normal (whose symmetric tails
   mis-price the high percentiles, 95–99% CSL, this project's service levels live at; a
   Normal also technically allows negative demand, silently clipped to 0 elsewhere).
   Lead time is *also* drawn from a Gamma fit to (lt_mean_wk, lt_std_wk), so each trial
   samples a genuinely different protection interval, not a fixed mean with a bolted-on
   variance correction. Reorder point = the target-CSL percentile of the simulated DDLT
   distribution; safety stock = that percentile minus the simulated mean.

A second parameter, **netting_discount**, was added later (see "Netting-discount fix"
below) to control how much credit an RDC gets for stock its downstream CDCs already
hold: `ss_p = max(0, pooled_SS − netting_discount * downstream_CDC_SS)`.
`netting_discount=1.0` reproduces the original full-credit assumption exactly;
`netting_discount=0.0` removes it entirely.

## Features

- Continuous pooling factor alpha ∈ [0, 1], twin-validated by grid search rather than
  closed-form optimization.
- Monte Carlo (Gamma-distributed) demand-during-lead-time, later supplemented with a
  faster deterministic **quadrature** DDLT mode in the optimized port, verified
  bit-identical to the Monte Carlo version for the production tools.
- Netting-discount parameter controlling how much of a downstream CDC's own stock
  offsets an RDC's pooled credit.
- Per-SKU override seams (`alpha_by_sku`, `z_by_sku`) wired directly into the live
  weekly optimizer — the mechanism that lets per-cluster alpha and per-cluster service
  level actually change production policy, not just research sweeps.
- Service level (z) sourced from the **live, canonical** SKU segmentation output
  (`output datasets/segmentation/sku_clusters.csv`, currently v5's 6 archetypes), not the
  dataset's baked-in 3-way segment label.
- Scenario-aware: `target_week_demand()`'s output can be scaled for an affected
  region/window (demand-surge scenarios), so a scenario-aware policy build genuinely
  recommends different stocking, not just a differently-scored fixed one.

## Advantages

- **The only model that beat real 2025 demand by a wide margin, not just synthetic
  noise.** See "Headline result" below.
- Directly answers the capstone charter's own framing: not just *how much* buffer to
  hold, but *where* in the network it should sit.
- Every adopted parameter value is backed by a named, dated validation pass with real
  numbers attached (not "we picked something reasonable") — alpha, netting_discount, and
  service level each have their own documented evidence trail below.
- Recovers most of the dataset's seasonal-peak blind spot (see "Where the win comes
  from"), the single biggest driver of the project's headline advantage over the static
  baseline.
- Per-SKU override seams mean the tuning this project validated in research scripts
  (per-cluster alpha, per-cluster service level) can actually reach the live weekly
  policy, not just a one-off analysis notebook.

## Disadvantages / open gaps — stated directly, not glossed over

- **Internal replenishment fill is still badly degraded relative to actual**, even after
  the netting-discount fix: 82.11% vs. actual's 93.21% (a −11.10pp gap). The fix is a
  real improvement over the original 81.99%, not a resolution.
- **Alpha=0.2 (the originally chosen operating point) is not the best setting on real
  data — it's second-worst** of the six values tested. Fill rate peaks near alpha=0.6
  on real 2025 demand; cost is lowest near alpha=0.4. The project explicitly chose *not*
  to adopt 0.6 over 0.2 on this basis alone, since both were chosen and tested on the
  same single real year (2025) — a fresh-holdout confirmation was never completed. (A
  later, separate agent-tooling branch did move the *live* portfolio default to 0.6 —
  see the per-cluster alpha evidence below — but that adoption decision sits outside
  this README's own validated/not-yet-validated boundary.)
- **Per-segment alpha is directionally real but not yet production-grade**: core
  clusters want *more* pooling (α≈0.4–0.6), long-tail clusters want *less* (α≈0.0–0.2),
  mechanistically explainable — but one seed, one synthetic year, clusters swept one at
  a time holding the others fixed (not guaranteed to find the joint optimum, since
  clusters share RDC/PDC capacity). The specific decimal alpha values are explicitly
  **not** a recommendation — only the direction is.
- **Lower holding cost from higher alpha is not a free efficiency gain** — it's the
  direct accounting mirror of the same netting mechanism that degrades internal
  replenishment fill. An RDC holding less of its own stock shows up simultaneously as
  lower holding cost *and* as internal-fill risk.
- **Long-Tail Erratic 2 (one of v5's 6 clusters) does not hit its own service-level
  target at any tested CSL**, even the most aggressive candidate (99%) — own-fill tops
  out at 89.9%, a 7.6pp shortfall at its recommended 95.7% CSL that is not a seed
  artifact (std ≈0.0019 across 5 seeds). This points at the newsvendor z-formula being a
  poor fit for this archetype's demand profile, not just a mis-picked CSL — flagged, not
  yet acted on.
- **Validation currency gap, stated directly in the README itself**: canonical
  segmentation moved from v4 (4 clusters) to v5 (6 clusters) partway through the
  project. Every multi-seed validation of alpha and netting_discount was run against
  v4's scheme specifically; only service level/CSL has been separately re-validated
  against v5. The combined (alpha + netting_discount + v5-z) configuration has not been
  multi-seed confirmed as a single unit.
- Only demand-surge scenarios are wired end to end; lead-time and capacity disruption
  types are scoped but not built. Only CDC/RDC policy is scenario-aware — PDC sizing is
  confirmed, in a real test run, to show exactly 0.00% stocking change under a scenario.

## Headline result: real 2025 demand vs. the dataset's own baseline tool

At the adopted configuration (alpha=0.2, netting_discount=0.0, cluster-based service
level):

| | Actual (as happened) | MEIO Weekly | **Clark-Scarf v2 (chosen)** |
|---|---|---|---|
| Customer fill rate, real 2025 demand | 93.08% | 93.23% (+0.15pp) | **94.51–94.83%** (+1.43 to +1.75pp, depending on segmentation snapshot — see "Validation currency gap") |
| Internal replenishment fill | 93.21% | −4.60pp | **82.11–82.38%** (−10.83 to −11.10pp — still degraded, the best achieved so far) |
| Total landed cost delta (5-seed mean) | — | — | **+$1.67M to +$1.83M** (vs. +$1.95M pre-netting-discount-fix — cheaper, not just better) |

## Netting-discount fix (validated)

The original pooled RDC formula credited the RDC with 100% of what its downstream CDCs
already hold — assuming stock can move to wherever it's needed on no notice, which the
twin's real weekly-review-plus-lead-time physics don't support. `netting_discount`
dials that credit down. Tested at alpha=0.2 against real 2025 demand, two methodology
passes:

- **First pass** found a genuine RNG bug: internal replenishment fill turned out far
  more sensitive to RNG-stream position (a 4pp swing from stream position alone) than to
  netting_discount itself — fixed by giving every tested value its own fresh seed
  instead of one shared sequential stream.
- **Multi-seed confirmation** (5 seeds): `netting_discount=0.0` beat `netting_discount=1.0`
  on every axis, 5/5 seeds, tight standard deviations — customer-fill delta vs. actual
  +1.75pp (σ=0.07) vs. +1.22pp (σ=0.05); internal replen fill 82.11% (σ=0.0007) vs.
  81.94% (σ=0.001); cost delta +$1.83M (σ=$51K) vs. +$1.95M (σ=$42K). **No tradeoff
  found at this alpha — adopted as part of the live configuration.**
- Separately found and fixed: the adopted value (0.0) had never actually been wired into
  the pipeline's own *defaults* — several build functions still defaulted to the
  original 1.0, so multiple evidence-generating stages had silently been running at the
  stale default since the "fix" was first adopted in prose. Fixed by changing the
  defaults themselves.

## Real-data alpha sensitivity (walk-forward, 2025-W02..W52, no lookahead)

| alpha | customer fill rate | CDC fill | RDC fill | internal replen fill | % weeks at zero stock |
|---|---|---|---|---|---|
| actual | 93.08% | 80.21% | 95.99% | 93.21% | 11.08% |
| 0.0 | 93.95% | 83.23% | 96.38% | 81.04% | 15.79% |
| 0.2 (synthetic-basis choice) | 94.26% | 86.49% | 96.02% | 81.89% | 13.28% |
| 0.4 | 94.67% | 89.49% | 95.85% | 82.97% | 12.45% |
| **0.6 (best real-data fill)** | **94.87%** | 92.12% | 95.49% | 84.15% | 11.71% |
| 0.8 | 94.73% | 93.72% | 94.95% | 85.54% | 11.18% |
| 1.0 (full v1 pooling) | 94.06% | 94.33% | 94.00% | 86.35% | 11.17% |

Lowest real-data cost delta: **alpha=0.4** at +$1,880,573; alpha=0.2 (the original
choice) scored +$2,060,252. This is a sensitivity check against a single held-out year,
explicitly **not** treated as a re-optimization — picking a new alpha off the back of
this same window would just relocate the overfitting risk it was designed to test for.

## Cluster-based service level (adopted as default, v5 segmentation)

Service level previously defaulted to the dataset's baked-in 3-way segment label
(Fast-Moving/Seasonal/Intermediate), not the project's own segmentation deliverable.
Adopted the live canonical segmentation output as the default regardless of whether it
statistically beat the old ladder, on the reasoning that sizing off the team's own
segmentation analysis — not a label the dataset happened to ship with — was the bigger
problem to fix. v5's 6-cluster CSL, validated against real demand, 5 seeds:

| Cluster | Recommended CSL | Own-fill at recommended CSL | Gap vs. target |
|---|---|---|---|
| Flagship Core | 97.0% | 94.3% | 2.7pp short |
| Mid-Tier Core | 97.0% | 95.2% | 1.8pp short |
| Long-Tail Erratic | 90.0% | 93.6% | beats target |
| **Long-Tail Erratic 2** | **95.7%** | **88.1%** | **7.6pp short** |
| Long-Tail Erratic 3 | 90.0% | 90.9% | beats target |
| Long-Tail Supply-Risk | 90.0% | 91.9% | beats target |

Five of six clusters hold up within a few points of target; Long-Tail Erratic 2 does
not, and the project found its shortfall doesn't close even at the most aggressive CSL
tested (99% → 89.9% own-fill) — see "Disadvantages" above.

## Why every alternative model was passed over in its favor

Clark-Scarf v2 is the only model in the project's bake-off validated on **real, replayed
2025 demand**, not just the synthetic 2026 basis every other model (including MEIO
Weekly and the four alt models) was scored on. See `Clark Scarf v1.md`,
`GSM (Guaranteed Service Model).md`, `Sim-Optimize.md`, `Bandit (Service-Level Tiers).md`
and `Cluster Bandit.md` for each alternative's own single-week synthetic score, all of
which underperformed the dataset's own baseline on that basis — a comparison this model
was never subjected to in isolation, since its real-data validation came from a
dedicated, later-built pipeline (`final_submission/validate_real_data.py`) rather than
`compare_models.py`'s shared bake-off harness.

## Comparison snapshots

![Clark-Scarf v2 headline vs. actual demand](images/clark_scarf_v2_headline_vs_actual.png)
*Customer fill rate and cost, Clark-Scarf v2 vs. the dataset's own baseline, synthetic
2026 basis.*

![Where the seasonal-peak win comes from](images/seasonal_peak_trough_meio_vs_clarkscarf.png)
*Baseline hits only 78.4% fill at the Seasonal segment's real peak week — worse than its
own trough week (92.7%). Both MEIO Weekly and Clark-Scarf v2 recover most, not all, of
this gap.*

![Clark-Scarf v2 real-data alpha sweep](images/clark_scarf_v2_alpha_sweep_real_data.png)
*Fill rate and cost vs. alpha, real 2025-W02..W52 walk-forward — the table above,
visualized.*

![Clark-Scarf v2 synthetic-basis alpha sweep](images/clark_scarf_v2_alpha_sweep_synthetic.png)
*The original grid search that picked alpha=0.2, on the synthetic 2026 basis.*

![Clark-Scarf v2 multi-seed robustness](images/clark_scarf_v2_multiseed_headline.png)
*5-seed confirmation of the headline result.*

![Clark-Scarf v2 multi-seed real-data robustness](images/clark_scarf_v2_multiseed_robustness.png)
*5-seed real-demand walk-forward robustness.*

![Per-segment alpha, own-fill rate](images/clark_scarf_v2_segment_alpha_own_fill.png)
*Core clusters want more pooling, long-tail clusters want less — the reversal described
above, per v4's 4 clusters.*

![Per-segment alpha, portfolio fill](images/clark_scarf_v2_segment_alpha_portfolio.png)

![Per-segment alpha, cost by cluster](images/clark_scarf_v2_segment_alpha_cost.png)
*The netting-discount accounting mirror: lower holding cost at higher alpha is not a
free gain.*

![Per-cluster alpha, own-fill (6-cluster v5 exploration)](images/clark_scarf_v2_cluster_alpha_own_fill.png)

![Per-cluster alpha response curve](images/clark_scarf_v2_cluster_alpha_response_curve.png)

## Files referenced

- `models/MEIO/MEIO_Weekly/final_submission/clark_scarf_v2.py`,
  `final_submission_optimized/clark_scarf_v2.py`
- `models/MEIO/MEIO_Weekly/final_submission/README.md` (primary source for this document)
- `models/MEIO/MEIO_Weekly/final_submission/results/headline_clark_scarf_v2_vs_actual.csv`,
  `headline_cost_summary.csv`, `alpha_sweep_report.md`, `multiseed_report.md`,
  `netting_discount_sweep_alpha0_2.csv`, `netting_discount_multiseed.csv`
- `models/MEIO/MEIO_Weekly/alt_models/backtest_rolling_real_clark_scarf_alpha_sweep.py`,
  `backtest_rolling_real_clark_scarf_alpha_sweep_report.md`
- `output datasets/agent output/service_level_v5_real_data_multiseed.csv`
