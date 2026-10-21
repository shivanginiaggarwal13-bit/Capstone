# Sim-Optimize (Simulation-Optimization over MEIO Weekly's Own Formula)

**Status: phased out** (not advanced past the single-week bake-off), but notable as the
best-scoring alternative model tried, and as the direct conceptual ancestor of the
project's twin-as-reward-oracle search methodology that Clark-Scarf v2's own alpha
search later reused.

Source: `models/MEIO/MEIO_Weekly/alt_models/sim_optimize_model.py`.

## Definition

Not a new inventory formula — the *same* (R, s, S) newsvendor logic already in MEIO
Weekly's `solve_policy`/`solve_pdc_policy`, but with its hand-derived constants
(`SIGMA_UPLIFT` multipliers, `PDC_COVER_WEEKS`, `MEAN_BLEND_ALPHA`) treated as a search
space instead of fixed values, optimized directly against the digital twin's simulated
outcome. The source docstring is explicit about its intellectual lineage: this is
literally what happened by hand across the project's v1–v10 MEIO Weekly tuning rounds
(try a value, measure via the twin, keep or discard) — systematized here as random
search instead of manual reasoning, in the same spirit as the bandit-engine strategy
documented in `Obsidian-Notes/Multi-Armed Bandits — Supply Brain Strategy.md`.

## Method

**Search space** (4 dimensions, sampled around the hand-derived defaults):

| Parameter | Range | Default |
|---|---|---|
| `rdc_uplift_mult` | [0.7, 1.5] × `SIGMA_UPLIFT["RDC"]` | 1.25 |
| `pdc_uplift_mult` | [0.7, 1.5] × `SIGMA_UPLIFT["PDC"]` | 1.45 |
| `pdc_cover_weeks` | [2.0, 7.0] | 4.5 |
| `mean_blend_alpha` | [0.3, 0.9] | 0.6 |

**Objective**: minimize `total_landed_cost_usd` from a full-horizon twin forward run
scoring the resulting single-week (2026-W01) frozen policy — the same basis MEIO
Weekly's own `simulate_policy.py` uses, so results are directly comparable to every
prior hand-tuned round.

**Method, stated plainly**: random search, not Bayesian optimization or a real bandit —
no new dependencies, fully transparent, and the search space is small enough (4
dimensions) that a modest budget of random draws covers it reasonably. Each trial
monkey-patches `meio_weekly_lib`'s module constants for the duration of one solve (never
touches the digital twin itself, which scores each candidate in a fresh subprocess),
then restores them — trials are independent and order doesn't matter.

## Features

- Searches the *exact same* formula and code path as the project's hand-tuned MEIO
  Weekly tool — not a competing model architecture, a systematic search over one.
- The digital twin itself is the reward oracle: every trial is scored by actually
  running the real physics simulation, not a proxy metric.
- Trial-independent by construction (parameter patch/restore around each solve), so
  trials can run in any order or in parallel.
- Outputs every trial's parameters and result (`sim_optimize_trials.csv`), not just the
  winner — the full search is auditable, not a black box.

## Advantages

- **Best-performing alternative model in the single-week bake-off** — see table below.
  Scored closer to baseline than any other candidate (including Clark-Scarf v1,
  cost-tuned MEIO Weekly itself, GSM, and Bandit).
- Directly validates the years of hand-tuning that produced MEIO Weekly's v1–v10 rounds:
  if random search over the same 4 parameters can't beat the hand-tuned defaults by
  much, that's evidence the hand-tuning had already found a reasonable region of the
  search space, not that it was arbitrary.
- No methodological novelty or new dependency risk — a transparent, inspectable
  improvement over manual trial-and-error, not a new black box.

## Disadvantages

- **Still underperformed the dataset's own baseline** — 85.78% fill vs. baseline's
  90.97% (−5.19pp), and +$1.98M cost, even as the best-scoring alternative.
- A 4-dimensional random search over a small, hand-picked range around already-tuned
  defaults is a narrow search — it cannot discover a fundamentally different policy
  shape, only a better-tuned version of the same formula shape.
- Computationally expensive relative to the closed-form models: each trial requires a
  full twin run, so `n_trials` scales directly with wall-clock time ("expect this to
  take several minutes total," per `compare_models.py`'s own docstring, for 15 trials).
- Random search provides no guarantee of finding even a local optimum within its own
  search space, let alone a global one — no evidence was presented that the search had
  converged, only that the best trial found was close to baseline.

## Real comparison snapshot: single-week bake-off

| model | customer fill rate | internal replen fill | holding cost | stockout cost | total landed cost | fill vs. baseline | cost vs. baseline |
|---|---|---|---|---|---|---|---|
| baseline | 90.97% | 89.05% | $740,518 | $4,685,043 | $36,215,502 | — | — |
| **sim_optimize** | **85.78%** | **85.99%** | **$796,688** | **$7,118,558** | **$38,190,521** | **−5.19pp** | **+$1.98M** |
| bandit | 84.86% | 85.42% | $798,797 | $7,376,527 | $38,479,425 | −6.11pp | +$2.26M |
| meio_weekly_v10 | 83.43% | 86.26% | $746,280 | $8,045,544 | $38,889,776 | −7.54pp | +$2.67M |
| clark_scarf (v1) | 80.54% | 78.85% | $717,325 | $9,519,535 | $40,072,092 | −10.43pp | +$3.86M |
| gsm | 48.43% | 80.84% | $367,941 | $35,170,432 | $56,033,564 | −42.54pp | +$19.82M |

Sim-Optimize placed **1st of 5 alternative models** (2nd overall after baseline),
narrowly ahead of Bandit.

## Why it was phased out

Even as the best-performing alternative, it still underperformed the dataset's baseline
by a meaningful margin, and — unlike Clark-Scarf v2 — it never advanced to real-2025-demand
validation. Its search methodology (twin-as-oracle, parameter search) was not abandoned
as an idea, though: Clark-Scarf v2's own alpha search reuses exactly this
pattern — "grid search, twin as reward oracle" — applied to the pooling-factor
parameter instead of MEIO Weekly's formula constants.

## Comparison snapshot

![Sim-Optimize vs. all other models, single-week bake-off](images/all_models_comparison_single_week.png)
*Sim-Optimize is the closest alternative model to baseline on both fill rate and cost.*

## Files referenced

- `models/MEIO/MEIO_Weekly/alt_models/sim_optimize_model.py`,
  `alt_models/sim_optimize_rolling.py`
- `models/MEIO/MEIO_Weekly/alt_models/compare_models.py`,
  `alt_output/model_comparison_summary.csv`, `alt_output/model_comparison_report.md`
- `Obsidian-Notes/Multi-Armed Bandits — Supply Brain Strategy.md` (cited lineage)
