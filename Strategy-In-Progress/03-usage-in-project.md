# Multi-Armed Bandits — Usage Areas in This Project

*How the bandit mechanism maps onto the project's charter layers and phases. This is additive scope layered on committed deliverables, not a replacement for them or a separate deliverable requiring its own sponsor sign-off.*

## Design principle

Bandits don't replace the underlying MEIO / safety-stock / elasticity math — that math still produces the actual numbers. Bandits sit **above** it, choosing *which policy or strategy template* to apply, and re-checking that choice as outcomes come in. One generic bandit engine, reused three times with different arm sets, rather than three separate builds.

## The three decision points

### 1. Price buffer vs. inventory buffer routing (charter layer 4)

- **Arms**: discrete buffer-mix templates per SKU segment, e.g. `100% inventory / 75-25 / 50-50 / 25-75 / 100% price`.
- **Reward**: twin-simulated total cost for that mix (holding cost + stockout cost + capital cost − price-buffer discount cost).
- **Deliverable link**: this is the mechanism behind the charter's headline "$1 of price flexibility = $Y of safety stock" exchange-rate analysis. Instead of a one-shot regression estimate, the exchange rate becomes something the system converges to and continuously re-tunes — a stronger, differentiated version of the same deliverable already committed.

### 2. MEIO echelon positioning (charter layer 3)

- **Arms**: predefined positioning templates, e.g. `hold at Plant DC / pool at Regional DC / push to Customer DC / split Regional+Customer`.
- **Reward**: twin-simulated total inventory cost net of service-level outcome.
- **Deliverable link**: directly supports the "MEIO output — network-level buffer positioning recommendations showing working capital reduction vs. a single-node baseline" deliverable already in the Project Description Form.

### 3. Service-level target selection (charter layer 2)

- **Arms**: discrete service-level tiers per segment, e.g. `90% / 95% / 97% / 99%`.
- **Reward**: twin-simulated stockout cost vs. holding cost trade-off at that target.
- **Deliverable link**: supports "differentiated service-level targets per segment" — makes the target itself something the system optimizes and re-checks, rather than a fixed policy assumption.

## Mapping to charter phases (no new calendar time required)

| Phase | Dates | Bandit-track activity |
|---|---|---|
| Phase 3 — Model Dev & Optimization | 5–25 Sep 2026 | Build the generic bandit engine (arm config, reward functions, Thompson Sampling sampler/updater), tested standalone against mocked twin output |
| Phase 4 — Simulation & Digital Twin Testing | 26 Sep–9 Oct 2026 | Wire the engine to the real digital twin; run the full 52-week 2026 forward horizon per segment; this produces the belief-evolution data needed for the exchange-rate and MEIO deliverables |
| Phase 5 — Agentic Interface & LLM Integration | 10–16 Oct 2026 | Expose current bandit recommendations + confidence through the agentic layer (see `04-agentic-and-llm.md`) |
| Phase 6 — Assessment & Refinement | 17–30 Oct 2026 | Compare bandit-converged policy against the static baseline on the charter's own KPIs (working capital reduction, service-level improvement) — the before/after story for the final report |

## Why this is feasible given what's already built

- The **digital twin already planned in Phase 4** is exactly the safe, cheap reward-signal generator a bandit needs — exploration happens in simulation, never against a live customer, which removes the usual objection to bandit exploration risk.
- The dataset's **synthetic-first, 52-week forward horizon** (see `../input datasets/DATASET_DOCUMENTATION.md`) provides enough repeated rounds per segment for beliefs to converge, especially when pooled by segment rather than run per-SKU.
- The dataset already encodes **known seasonal curves per category** (see `EDA_REPORT.md` §1.4), which lets the bandit use seasonal phase as context instead of having to rediscover seasonality via drift detection — see `02-simplified-explanation.md` for the mechanics.

## Known gaps / open risks for this track

- **Non-stationarity**: requires sliding-window or discounted-reward updates, plus seasonal-phase context (addressed above) — needs to be explicit in the implementation, not assumed away.
- **Delayed/batched feedback**: reward only resolves after a simulated replenishment cycle, so belief updates happen weekly against twin output, not per micro-transaction.
- **Combinatorial action space**: true MEIO positioning is a continuous, multi-node allocation problem; this track discretizes it into a small set of templates to fit the bandit framework, which is a simplification worth flagging explicitly in the technical report.
- **Feedback-loop data gap**: `../Docs/MEIO in FnB.md` flags actual-vs-forecast drift tracking as an unresolved gap in the current schema. This bandit track depends on that same class of data (observed outcomes over time) — building it out is a shared dependency, not bandit-specific scope.
- **Team scope guardrail**: this must remain visibly additive to committed deliverables. If time runs short, the generic engine (Phase 3) and one decision point (most likely #1, price/inventory routing, given its direct deliverable link) should be prioritized over building all three.
