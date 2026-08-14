# Multi-Armed Bandits — Definition

*Status: exploratory / not yet approved by sponsor. This track runs in parallel to the core MEIO build and is a candidate mechanism for the closed-loop, self-recalibrating layer described in the project charter.*

## What it is

A multi-armed bandit (MAB) is a class of sequential decision-making algorithms for choosing repeatedly among a fixed set of options ("arms") whose payoffs are unknown and must be learned from experience, while balancing:

- **Exploitation** — favoring the option that has performed best so far.
- **Exploration** — continuing to test other options, because the current best guess may be wrong or may change over time.

The name comes from a row of slot machines ("one-armed bandits") with different unknown payout rates; the player must decide which machine to pull each round to maximize cumulative winnings.

## The three required ingredients

1. **Arms** — the discrete set of choices being selected between.
2. **Reward** — a scalar signal observed after playing an arm, indicating how good that choice was.
3. **Rounds** — repeated opportunities to choose, with the ability to update beliefs between rounds.

## Core algorithms (candidates for this project)

| Algorithm | Mechanism | Fit for this project |
|---|---|---|
| Epsilon-greedy | Mostly plays the current best arm; plays a random arm with small probability ε | Simple baseline, but explores blindly (no sense of uncertainty) |
| UCB (Upper Confidence Bound) | Plays the arm with the highest "mean + confidence bonus"; bonus shrinks as an arm is tried more | Deterministic, easy to reason about theoretically |
| Thompson Sampling | Maintains a belief *distribution* per arm; draws a random sample from each belief each round and plays the highest sample | Naturally expresses uncertainty as a distribution — easiest to explain to non-technical stakeholders as "confidence"; recommended default for this project |
| Contextual bandit (e.g., LinUCB, contextual Thompson Sampling) | Arms' expected reward is modeled as a function of side information ("context") — e.g., SKU features | Needed here because 500 SKUs are too sparse for independent per-SKU bandits; context lets the model generalize across similar SKUs |

## Why a bandit, not a one-shot optimization

A standard optimization (e.g., a single regression or LP solve) produces one fixed answer computed once from historical data. A bandit produces an answer that keeps updating itself as new outcomes are observed — it is inherently a *learning loop*, not a static calculation. This is the structural reason it is being evaluated for this project: the charter's stated goal is a system that "continuously re-learns from outcomes" and "automatically re-tunes targets ... rather than remaining static after initial setup" — that is a description of bandit behavior, independent of whether the team ultimately implements it as one.

## Key caveats going in

- **Non-stationarity**: demand, elasticity, and cost structures drift with seasonality and market conditions, which violates the "fixed unknown payout" assumption of a vanilla bandit. Requires sliding-window / discounted-reward variants, or context that encodes seasonal phase directly (see `03-usage-in-project.md`).
- **Delayed / batched feedback**: reward is only observable after a simulated replenishment cycle (roughly weekly), not per micro-decision — requires batch-update bandit variants, not the naive per-pull-update formulation.
- **Combinatorial action space**: true MEIO positioning is a multi-node, continuous allocation problem. To fit the bandit framework, decisions must be discretized into a small number of predefined strategy templates (arms) rather than solved as a continuous bandit.
- **This is an enhancement layer, not a replacement** for the underlying MEIO/safety-stock math. The bandit selects *which policy* to use; the policy's actual numbers (safety stock levels, reorder points) still come from the standard inventory optimization models.
