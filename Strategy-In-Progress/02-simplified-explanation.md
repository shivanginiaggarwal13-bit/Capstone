# Multi-Armed Bandits — Simplified Explanation

*Written for someone with no ML background. Aim: understand the mechanics well enough to review the design, not to derive the math.*

## The slot machine analogy

You walk into a casino with 5 slot machines. Each pays out at a different average rate, but you don't know which is best. You have 100 coins. Your goal: win as much as possible.

The tension: do you keep playing the machine that's paid out best *so far* (exploit), or do you try the others in case your best guess is wrong (explore)? A bandit algorithm is just a rule for making that trade-off automatically, coin by coin.

That's the whole idea — a decision-making strategy, not a prediction model. No training data, no neural network required.

## Worked example: choosing between price buffer and inventory buffer

**Setup.** One SKU segment (e.g., "Snacks — Value tier, elastic"). Three arms:

- **Arm A** — 100% inventory buffer (hold extra stock, no discounting)
- **Arm B** — 50/50 hybrid (some stock, some price flexibility)
- **Arm C** — 100% price buffer (no extra stock, rely on discounting)

**Reward** = $ saved that simulated week vs. a "hold lots of stock always" baseline, as reported by the digital twin. Higher is better.

**Belief, not just an average.** Thompson Sampling stores a *range of plausible values* per arm, not one number. At the start, all three arms are identical: "probably around $5,000/week, could be $2,000–$8,000." That width is the uncertainty.

**Each round:**
1. Roll a random guess from each arm's current belief range.
2. Play whichever arm rolled the highest guess.
3. Observe the twin's actual result.
4. Narrow that arm's belief toward the real result — mean shifts, uncertainty shrinks.

| Round | Guess A | Guess B | Guess C | Arm played | Actual result | Updated belief |
|---|---|---|---|---|---|---|
| 1 | $6,200 | $4,100 | **$7,800** | C | $3,000 | C: mean drops to ~$3,800, narrows |
| 2 | $5,900 | **$7,100** | $2,600 | B | $6,500 | B: mean rises to ~$5,800, narrows |
| 3 | $4,700 | **$8,000** | $2,100 | B | $7,000 | B: mean rises to ~$6,300, tighter |
| 4 | $6,600 | **$6,900** | $1,900 | B | $6,800 | B: mean ~$6,450, tighter |
| 5 | $3,000 | **$7,300** | $3,400 | B | $6,600 | B: mean ~$6,500, tight |

What happened:
- **Arm C** was tried once, underperformed, and its belief narrowed downward — it's not banned, just increasingly unlikely to be picked.
- **Arm B** performed well when tested and kept sharpening around a strong number, so it wins more rounds over time.
- **Arm A** was never picked in this run, but its belief stays wide, so it can still occasionally get tested — nothing is permanently ruled out.

Over more rounds the system settles on Arm B as the default, while still spending occasional weeks re-checking A and C — in case conditions change. That "still occasionally checking" behavior is what makes the system *self-recalibrating* rather than a one-time decision.

## Handling seasonality (the part that breaks a naive bandit)

A vanilla bandit assumes the best arm never changes. Seasonality violates that: e.g., Beverages peak in summer (stockouts are expensive → inventory buffer wins) but the same SKU in winter has low, price-sensitive demand (price buffer wins instead). If the bandit's belief has already narrowed tight from a summer run, it will keep recommending the summer answer into the fall, because it's too confident to bother re-checking.

**Fix used in this project:** the seasonal curve per category is already known and embedded in the synthetic data generator (see `../input datasets/EDA_REPORT.md` §1.4). Rather than making the bandit rediscover seasonality by drift detection, feed it directly as context: keep a **separate belief per (segment × seasonal phase)** — e.g., peak / shoulder / trough — instead of one belief per segment. A secondary safety net (a floor on how tight uncertainty can get, or a sliding window over recent weeks) covers unknown shocks not on the seasonal calendar (e.g., a surprise competitor promo).

This matters most for the **Seasonal segment (30% of SKUs, largest demand amplitude** per the EDA report); for Fast-Moving/Intermediate segments a single belief per segment is likely sufficient, since seasonality barely moves the needle there.

## What it is *not*

- Not a demand forecasting model — that's a separate, existing model.
- Not the safety-stock calculation itself — that's still a formula/optimization step.
- It's the layer *above* those calculations that decides **which strategy or setting to use**, and keeps re-checking whether that decision is still right as conditions change.
