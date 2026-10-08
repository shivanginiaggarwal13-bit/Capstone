The **Reward** is the scalar signal a [[Multi-Armed Bandit]] observes after playing an arm — the number the whole algorithm is trying to maximize the cumulative sum of. Everything about a bandit's behavior traces back to how this number is defined, so choosing it correctly is a design decision, not an implementation detail.

**How reward is defined at each of the three [[Supply Brain]] decision points** (see **[[Multi-Armed Bandits — Supply Brain Strategy]] §6** for full context on each):
| Decision point | Reward definition |
|---|---|
| Price vs. inventory buffer routing | $ saved that simulated week vs. a "hold lots of stock always" baseline |
| MEIO echelon positioning | Twin-simulated total inventory cost, net of the service-level outcome achieved |
| Service-level target selection | Twin-simulated stockout cost vs. holding cost trade-off at that target |

In every case, the reward is a **[[Digital Twin]]** output, not a live business outcome — the twin exists specifically to generate this signal safely and cheaply, repeatedly, without any real customer ever being affected by an arm that turns out to be a bad choice.

**Why reward is harder here than in the textbook slot-machine case.** Three complications, all documented as open risks in the strategy track:
- **Delayed / batched**, not instantaneous — reward only resolves after a simulated replenishment cycle (~weekly), not per micro-decision. See **[[Batched Feedback]]**.
- **Non-stationary** — the "true" reward for a given arm drifts with season and market conditions, not fixed. See **[[Non-Stationarity (Bandits)]]**.
- **Multi-component** — the price/inventory reward specifically nets *four* cost components against each other (holding cost + stockout cost + capital cost − price-buffer discount cost), any one of which could be mis-specified and quietly bias which arm looks best.

**Why getting this right matters more than the choice of algorithm.** A bandit with a flawless algorithm but a badly-specified reward will still converge — confidently — on the wrong answer. This is why the reward definitions above are pinned down explicitly per decision point rather than left as "whatever the twin happens to output."

Related: [[Multi-Armed Bandit]], [[Digital Twin]], [[Multi-Armed Bandits — Supply Brain Strategy]]
