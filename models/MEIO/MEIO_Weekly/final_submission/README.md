# MEIO — Final Submission: Clark-Scarf v2

Start here. This folder has everything behind the model-building recommendation;
the broader exploration history (5 models, 10 tuning rounds, bugs found and
fixed) lives in `..` and `../alt_models/` for anyone who wants the full process,
but isn't required reading to evaluate the recommendation itself.

## The recommendation

**Clark-Scarf v2 at alpha=0.2**, a continuous-pooling-factor, Monte Carlo-DDLT
echelon model (`clark_scarf_v2.py`), chosen over the hand-tuned MEIO Weekly
tool because it holds up better against **real replayed 2025 demand**, not
just synthetic noise — and because its core mechanism (deciding how much
safety stock an RDC pools with the CDCs it feeds) is a direct answer to the
charter's own ask: decide not just how much buffer to hold, but **where**.

| | Actual (as happened) | MEIO Weekly | **Clark-Scarf v2 (chosen)** |
|---|---|---|---|
| Customer fill rate, real 2025 demand | 93.08% | 93.23% (+0.15pp) | **94.27% (+1.19pp)** |
| Total landed cost | $87.77M | +1.9% | +2.3% |
| Internal replenishment fill | 93.21% | −4.60pp | **−11.22pp** |

Full detail: `results/headline_report.md`,
`results/headline_clark_scarf_v2_vs_actual.csv`, `results/headline_cost_summary.csv`,
`results/chart_headline.png`. MEIO Weekly's own real-data result (context for
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

## Known limitations — stated up front, not left for a reviewer to find

- **Write-off minimization** (one of the charter's three named KPIs) is not
  measurable in this dataset at all: no shelf-life or obsolescence modeling,
  `write_offs.csv` is empty by design.
- **Working capital** is a mixed result, not a clean win: safety stock value
  fell 9.1%, but total inventory value rose 9.1% (cycle stock grew faster
  than safety stock shrank).
- **Internal replenishment fill degrades under every model tested**, worse
  for Clark-Scarf v2 (−11.22pp) than MEIO Weekly (−4.60pp) — the leading
  hypothesis is the pooled endpoint's netting step
  (`ss_p = pooled_SS - downstream_CDC_SS` in `clark_scarf_v2.py::solve`)
  assumes stock can move to wherever it's needed, which the twin's real
  weekly-review-plus-lead-time physics don't support instantly. Not yet
  fixed; a netting-discount parameter is the proposed next step (see Next
  steps).
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

## Next steps, in priority order

1. **Netting-discount fix for internal replenishment fill** (see "Known
   limitations"). Cheap to prototype on the infrastructure already here —
   a second parameter in `clark_scarf_v2.py::solve`, tested the same way
   alpha was.
2. **Fresh-holdout confirmation of alpha 0.4/0.6** once more real weeks
   (2026 actuals) are available.
3. **Multi-seed validation of the per-segment alpha direction** before it's
   anything more than a diagnostic — likely a 2-way (core vs. long-tail)
   split rather than the full 4-way, to limit overfitting risk.
