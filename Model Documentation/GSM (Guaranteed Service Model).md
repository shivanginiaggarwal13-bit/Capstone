# GSM — Guaranteed Service Model (Graves-Willems, simplified)

**Status: phased out.** Built as "Alt MEIO Model 2" in the project's model bake-off —
the other classical academic MEIO paradigm, structurally different from Clark-Scarf.
Scored worst of all six models tested, by a wide margin.

Source: `models/MEIO/MEIO_Weekly/alt_models/gsm_model.py`.

## Definition

Where Clark-Scarf decides **how much** safety stock each node holds against its own
demand, GSM decides **where in the network stock sits at all**. Each node guarantees a
service time *S* to its downstream customer ("you'll always have stock within *S* time
of an order"). A node that guarantees *S=0* must hold safety stock to cover its own full
replenishment lead time; a node that guarantees *S>0* can hold less (or zero) stock
because it relies on its upstream node's own guarantee to cover part of the wait. The
real **Graves-Willems** algorithm solves this placement decision — which is
combinatorial across the whole network — with dynamic programming over the supply tree.

## Method

**Honest scope note, stated directly in the source**: exact Graves-Willems dynamic
programming (continuous *S* with piecewise-linear cost functions per node) is a
substantial undertaking on its own. This implements the **simplified, all-or-nothing**
variant used in introductory GSM treatments: each node either fully stocks (*S=0*) or
fully passes the wait through (no local safety stock at all), decided by a single
top-down greedy pass comparing the local holding cost of stocking against the cost of
pushing that same protection downstream. A real, if heuristic, GSM — not the exact
optimal DP.

Walked per SKU, in network order Plant → PDC → RDC → CDC (a tree, single path per node,
verified against `sku_sourcing_map.csv`):
- **PDC always stocks** (*S=0*) — it's the network's only source of supply, with no
  upstream guarantee to lean on.
- **RDC**: incoming service time from PDC is 0. If the RDC stocks, its protection
  interval is the PDC→RDC lead time and it guarantees *S=0* downstream. If it passes
  through, its local protection interval is 0 (no stock) but it pushes *S = lead time*
  onto its CDCs. Chosen by whichever option has the lower total holding-cost-weighted
  safety stock.

## Features

- A structurally different question from every other model in the bake-off: "where does
  stock sit," not "how much stock at each point."
- Binary per-node stock/no-stock decision, made by a single greedy top-down pass —
  computationally trivial compared to Monte Carlo or search-based alternatives.
- Explicit network-tree verification against real sourcing data before the walk.

## Advantages

- Conceptually distinct enough from Clark-Scarf and MEIO Weekly to genuinely test a
  different hypothesis about where the project's safety stock should live, not just a
  different parameterization of the same idea.
- Cheapest model to compute in the whole bake-off — no search, no simulation, one pass
  per SKU.
- The simplification (binary stock/no-stock) is explicitly disclosed as a real but
  heuristic GSM, not silently presented as the exact algorithm.

## Disadvantages

- **By far the worst-performing model tested.** See the comparison table below —
  customer fill rate collapsed to 48.43% (a 42.5pp drop from baseline), and total landed
  cost came in at $56.0M against baseline's $36.2M — more than $19.8M worse, roughly 10x
  every other model's cost gap.
- The all-or-nothing simplification is the likely direct cause: a node that "passes
  through" holds **zero** local safety stock, with no partial middle ground — unlike
  Clark-Scarf v2's continuous alpha, there's no way for this implementation to express
  "hold some, but not all, of the buffer here."
- The greedy top-down pass optimizes each node's local decision given its parent's
  choice, with no backtracking — a genuinely suboptimal network-wide placement is
  possible, and likely, given the result.
- No validated use of this model beyond the single synthetic-basis bake-off; it was not
  carried forward into real-data testing the way Clark-Scarf was.

## Real comparison snapshot: single-week bake-off

| model | customer fill rate | internal replen fill | holding cost | stockout cost | total landed cost | fill vs. baseline | cost vs. baseline |
|---|---|---|---|---|---|---|---|
| baseline | 90.97% | 89.05% | $740,518 | $4,685,043 | $36,215,502 | — | — |
| sim_optimize | 85.78% | 85.99% | $796,688 | $7,118,558 | $38,190,521 | −5.19pp | +$1.98M |
| bandit | 84.86% | 85.42% | $798,797 | $7,376,527 | $38,479,425 | −6.11pp | +$2.26M |
| meio_weekly_v10 | 83.43% | 86.26% | $746,280 | $8,045,544 | $38,889,776 | −7.54pp | +$2.67M |
| clark_scarf (v1) | 80.54% | 78.85% | $717,325 | $9,519,535 | $40,072,092 | −10.43pp | +$3.86M |
| **gsm** | **48.43%** | **80.84%** | **$367,941** | **$35,170,432** | **$56,033,564** | **−42.54pp** | **+$19.82M** |

The stockout-cost column tells the real story: GSM's holding cost is the *lowest* of any
model (it correctly minimizes stock held), but its stockout cost is **more than 4x**
every other model's — the direct, measurable cost of the all-or-nothing placement
decision leaving large parts of the network with zero local protection.

## Why it was phased out

The single-week bake-off result was decisive enough that this model was not carried
into any further real-data or multi-seed validation — the simplification's cost was too
large to be a plausible candidate regardless of further tuning, and the project's
remaining effort concentrated on Clark-Scarf v2 and the other models that at least
stayed within a few points of baseline.

## Comparison snapshot

![GSM vs. all other models, single-week bake-off](images/all_models_comparison_single_week.png)
*GSM is the clear outlier on both axes — the bar furthest from every other model on
both fill rate and cost.*

## Files referenced

- `models/MEIO/MEIO_Weekly/alt_models/gsm_model.py`
- `models/MEIO/MEIO_Weekly/alt_models/compare_models.py`,
  `alt_output/model_comparison_summary.csv`, `alt_output/model_comparison_report.md`
