# Bandit — Thompson Sampling over Service-Level Tiers

**Status: phased out** (not advanced past the single-week bake-off as a MEIO policy
model), but its underlying strategy — multi-armed bandits for service-level decisions —
was a project-level exploration documented separately
(`Obsidian-Notes/Multi-Armed Bandits — Supply Brain Strategy.md`), and its twin-validated
Thompson Sampling mechanism was reused for `Cluster Bandit.md`.

Source: `models/MEIO/MEIO_Weekly/alt_models/bandit_model.py` (ships as "v2" — v1's issues
are documented and fixed in the same file, there is no separate `bandit_v1.py`),
`bandit_rolling.py` (52-week rolling variant).

## Definition

A working implementation of the "6.3 Service-level target selection" decision point
specified in the project's own bandit strategy note. Each of the dataset's 3 demand
segments (Fast-Moving, Seasonal, Intermediate) is treated as an independent **Thompson
Sampling bandit** choosing among service-level (CSL) tiers — pull an "arm" (a target
CSL), observe a reward (the digital twin's simulated cost/fill outcome), update a belief
distribution over that arm's value, repeat.

## Method

**Arms per segment**: the 4 fixed CSL tiers {90%, 95%, 97%, 99%} *plus* the segment's own
current hand-tuned default as a named 5th arm (e.g. Fast-Moving's z=2.05 ≈ 98% — not
exactly any of the 4 fixed tiers).

**Belief model**: Normal-Normal conjugate update (`PRIOR_MEAN=0`, `PRIOR_VAR=2e12`,
`OBS_VAR=5e11`) — standard Thompson Sampling machinery.

**Tier-order penalty**: an explicit `PENALTY_PER_Z_VIOLATION_USD` ($300,000, disclosed
as a modeling choice, not a calibrated business input — no real $-per-tier-break number
exists to calibrate against) discourages the bandit from converging to an order that
contradicts the dataset's own baseline convention (Fast-Moving > Seasonal >
Intermediate protection).

## Four fixes made from v1 to v2, each tied to a specific found problem

1. **Arms now include the segment's own current default**, not just the 4 fixed tiers.
   v1's fixed-tier-only arms meant the bandit could never reproduce the "confirms the
   hand-tuned default was already good" result `sim_optimize_model.py` got by always
   including the current defaults as trial 0 — now it can.
2. **Forced initial exploration**: the first *n* rounds (one per tier) of each segment's
   bandit play every arm once, in order, before genuine Thompson Sampling begins. v1's
   rolling run (3 rounds, 4 arms) converged the Fast-Moving segment to a tier that was
   **never actually played** — its belief was still the untouched prior mean (0), which
   happened to beat every arm that *was* played (all scored negative that round). Not
   evidence the unplayed arm was best — an artifact of too few rounds. Now structurally
   impossible.
3. **Tier-order penalty added to the reward.** The reference strategy slide's own
   "Target Service Level Optimization" objective explicitly includes a linear penalty
   for breaking tier order; v1 had no such term, so nothing stopped the Intermediate
   segment from converging to *more* protection than Fast-Moving, contradicting the
   dataset's own baseline convention.
4. **Seasonal-phase awareness, scoped but not fully built.** The strategy note calls
   segment × seasonal-phase context "required, not optional," warning a vanilla bandit
   "will keep confidently recommending the summer answer into the fall." Properly wiring
   phase context into the 52-week rolling loop would mean each round's reward is a
   mixture of three phases' choices — a genuinely larger bandit-attribution problem, not
   attempted here. What *is* tested on the frozen single-week basis: does the Seasonal
   segment's converged tier actually differ between a peak week and a trough week? If it
   does, that's direct evidence the fuller contextual rebuild is worth the investment.

## Advantages

- Second-best alternative model in the single-week bake-off, close behind Sim-Optimize.
- A genuinely self-critical build process: v2's four fixes each cite a specific,
  diagnosed failure mode in v1, not a generic "improved version."
- The forced-exploration fix in particular catches a real, subtle bandit failure mode
  (a converged "best" arm that was never actually played) that's easy to miss by just
  looking at a final result.
- Reusable mechanism: the same v2 fixes (forced exploration, named "current" arm) carry
  directly into `Cluster Bandit.md`'s segmentation-aware variant.

## Disadvantages

- **Still underperformed the dataset's own baseline** by a wide margin — 84.86% fill vs.
  90.97% baseline (−6.11pp), +$2.26M cost.
- The $300,000 tier-order-violation penalty is an explicitly disclosed, uncalibrated
  modeling choice — no real business number backs it, so the bandit's convergence
  behavior is partly shaped by an arbitrary constant.
- Seasonal-phase context (fix #4) was deliberately scoped down to a diagnostic check,
  not a real fix — the bandit still cannot adapt its recommendation within a year as
  phases change, the exact failure mode the strategy note warned about.
- A bandit converges over many rounds; on the single frozen-week bake-off basis it only
  gets as many rounds as the comparison script's `rounds_per_segment` budget allows
  (several twin evaluations per segment), which bounds how much genuine exploration vs.
  exploitation tradeoff it can demonstrate.

## Real comparison snapshot: single-week bake-off

| model | customer fill rate | internal replen fill | holding cost | stockout cost | total landed cost | fill vs. baseline | cost vs. baseline |
|---|---|---|---|---|---|---|---|
| baseline | 90.97% | 89.05% | $740,518 | $4,685,043 | $36,215,502 | — | — |
| sim_optimize | 85.78% | 85.99% | $796,688 | $7,118,558 | $38,190,521 | −5.19pp | +$1.98M |
| **bandit** | **84.86%** | **85.42%** | **$798,797** | **$7,376,527** | **$38,479,425** | **−6.11pp** | **+$2.26M** |
| meio_weekly_v10 | 83.43% | 86.26% | $746,280 | $8,045,544 | $38,889,776 | −7.54pp | +$2.67M |
| clark_scarf (v1) | 80.54% | 78.85% | $717,325 | $9,519,535 | $40,072,092 | −10.43pp | +$3.86M |
| gsm | 48.43% | 80.84% | $367,941 | $35,170,432 | $56,033,564 | −42.54pp | +$19.82M |

Placed 2nd of 5 alternative models (3rd overall), just behind Sim-Optimize.

## Why it was phased out

Like Sim-Optimize, it never closed the gap to baseline on the single-week synthetic
basis and was not carried into real-2025-demand validation as a MEIO policy model. Its
mechanism was not discarded outright, though — `Cluster Bandit.md` reuses the same
Thompson Sampling machinery and v2 fixes, applied to the segmentation deliverable's
cluster archetypes instead of the dataset's original 3-way segment, to test a genuinely
different and still-open question (whether the segmentation's prescribed CSL ladder for
the long-tail clusters holds up under a twin-validated search).

## Comparison snapshot

![Bandit vs. all other models, single-week bake-off](images/all_models_comparison_single_week.png)
*Bandit placed 3rd overall, ahead of MEIO Weekly, Clark-Scarf v1 and GSM.*

## Files referenced

- `models/MEIO/MEIO_Weekly/alt_models/bandit_model.py`, `bandit_rolling.py`
- `models/MEIO/MEIO_Weekly/alt_models/compare_models.py`,
  `alt_output/model_comparison_summary.csv`, `alt_output/model_comparison_report.md`
- `Obsidian-Notes/Multi-Armed Bandits — Supply Brain Strategy.md` (the strategy
  specification this implements)
