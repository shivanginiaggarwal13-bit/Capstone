**Thompson Sampling** is a [[Multi-Armed Bandit]] algorithm that maintains a full *belief distribution* per arm — not just a single running average — and each round draws one random sample from every arm's current belief, then plays whichever arm drew the highest sample. It is the **recommended default algorithm** for the [[Multi-Armed Bandits — Supply Brain Strategy|Supply Brain bandit track]].

**Why a distribution instead of a point estimate.** A plain average treats "tried once, got a great result" the same as "tried 50 times, consistently good" — both look like a high mean. A belief distribution keeps that uncertainty visible: the first case is wide (still very uncertain), the second is narrow (confident). Sampling from the distribution, rather than just picking the highest mean, means an arm with a promising-but-uncertain result still gets a fair chance to be tried again, while a well-established loser gets tried less — automatically, without a hand-tuned exploration rate like [[Epsilon-Greedy]] needs.

**Worked mechanics, per round:**
1. Draw one random guess from each arm's current belief range.
2. Play whichever arm's guess came out highest.
3. Observe the actual result (from the [[Digital Twin]] in this project).
4. Narrow that arm's belief toward the real result — the mean shifts, the uncertainty shrinks.

See **[[Multi-Armed Bandits — Supply Brain Strategy]] §4** for the full five-round numeric walkthrough (choosing between 100% inventory buffer, a 50/50 hybrid, and 100% price buffer for a "Snacks — Value tier, elastic" segment).

**Why it was chosen over UCB or epsilon-greedy for this specific project.** Two reasons beyond the algorithmic properties: it naturally expresses uncertainty as something describable in plain language ("79% confidence based on 14 simulated weeks"), which matters directly for the **[[Agentic Interface]]**'s `explain_recommendation` tool — a planner asking "why?" gets a confidence number, not just a black-box arm choice. And its belief-per-arm structure extends cleanly to **[[Contextual Bandit|contextual]]** beliefs keyed on (segment × seasonal phase), which is exactly the fix needed for **[[Non-Stationarity (Bandits)|seasonal non-stationarity]]**.

Related: [[Multi-Armed Bandit]], [[Multi-Armed Bandits — Supply Brain Strategy]]
