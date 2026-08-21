*Exploratory strategy track for [[Supply Brain]] — not yet approved by the sponsor. Runs in parallel to the core MEIO build; this is a candidate mechanism for the closed-loop, self-recalibrating layer described in the Capstone Project Charter, written up in full because it is the mechanism most likely to make that language literally true rather than aspirational. If adopted, the Capstone Project Charter and `Docs/MEIO in FnB.md` should be updated to reference it explicitly.*

> [!important] Why this note exists
> The charter's desired-state language says the system should *"evolve over time, automatically re-tuning targets and buffer positions as demand, supply, and market conditions shift — rather than remaining static after initial setup."* A one-shot regression or LP solve, run once on historical data, cannot do that — it produces a fixed answer that goes stale the moment conditions change. A **[[Multi-Armed Bandit]]** produces an answer that keeps updating itself as new outcomes are observed. That structural fit — independent of whether the team ultimately ships it as the final mechanism — is the entire reason this track exists and has been researched this thoroughly.

---

## 1. What a Multi-Armed Bandit Is

A **[[Multi-Armed Bandit]]** (MAB) is a class of sequential decision-making algorithms for choosing repeatedly among a fixed set of options ("arms") whose payoffs are unknown and must be learned from experience, while balancing two competing pressures:

- **Exploitation** — favor the option that has performed best so far.
- **Exploration** — keep testing other options, because the current best guess may be wrong, or may stop being right as conditions change.

The name comes from a row of slot machines ("one-armed bandits") with different, unknown payout rates — the player must decide which machine to pull each round to maximize cumulative winnings. See **[[Exploration-Exploitation Trade-off]]** for why this tension can't simply be solved once and forgotten.

**The three required ingredients**, in any bandit formulation:
1. **Arms** — the discrete set of choices being selected between.
2. **Reward** — see **[[Reward Function (Bandit)]]** — a scalar signal observed after playing an arm, indicating how good that choice was.
3. **Rounds** — repeated opportunities to choose, with beliefs updated between rounds.

Critically: this is **not** a forecasting model and **not** the safety-stock calculation itself. It is the layer *above* those calculations that decides *which strategy or setting to use*, and keeps re-checking whether that decision is still right as conditions change. It does not replace **[[SKU Segmentation]]**, **[[Price Elasticity]]** estimation, or **[[Multi-Echelon Inventory Optimization (MEIO)|MEIO]]** — those still produce the actual numbers a chosen policy runs on.

---

## 2. Why a Bandit, Not a One-Shot Optimization

| | One-shot optimization (regression / LP) | Multi-armed bandit |
|---|---|---|
| Computed | Once, from historical data | Continuously, from live outcomes |
| Behavior under drift | Stale until manually re-run | Re-converges automatically |
| Matches charter's "continuously re-learns from outcomes"? | No | Yes, by construction |
| Matches charter's "human confirmation before anything committed"? | N/A (no ongoing loop to gate) | Yes — see the [[Agentic Interface]] toolbox below |

This is the structural argument, not a preference: the Project Description Form's desired-state language *is a description of bandit behavior*, whether or not "bandit" is the word the sponsor would have used. That is what justifies the depth of this research track relative to its current unapproved status — the fit to the stated requirement is unusually direct.

---

## 3. Algorithm Options Considered

| Algorithm | Mechanism | Fit for Supply Brain |
|---|---|---|
| **[[Epsilon-Greedy]]** | Mostly plays the current best arm; plays a random arm with small probability ε | Simple baseline, but explores blindly — no sense of *how* uncertain the model is about each arm |
| **[[Upper Confidence Bound (UCB)]]** | Plays the arm with the highest "mean + confidence bonus"; the bonus shrinks as an arm is tried more | Deterministic, easy to reason about theoretically |
| **[[Thompson Sampling]]** | Maintains a full belief *distribution* per arm; draws a random sample from each belief every round and plays the highest sample | **Recommended default.** Naturally expresses uncertainty as a distribution — the easiest of the four to explain to a non-technical planner as "confidence," which matters given the whole point of Layer 6–7 is planner-facing explainability |
| **[[Contextual Bandit]]** (e.g. LinUCB, contextual Thompson Sampling) | Expected reward is modeled as a function of side information ("context") — e.g. SKU features, seasonal phase | **Required, not optional**, for this project — 5,000 SKUs are far too sparse for independent per-SKU bandits; context lets the model generalize across similar SKUs instead of learning each one from a cold start |

**Working recommendation: Thompson Sampling, with context.** The context variable that matters most here is *(segment × seasonal phase)* — see §5.

---

## 4. Worked Example — Price Buffer vs. Inventory Buffer

*Reproduced and annotated from the team's exploratory documentation (`Strategy-In-Progress/02-simplified-explanation.md`).*

**Setup.** One SKU segment: "Snacks — Value tier, elastic." Three arms, each a different point on the buffer-mix spectrum:

- **Arm A** — 100% inventory buffer (hold extra stock, no discounting)
- **Arm B** — 50/50 hybrid (some stock, some price flexibility)
- **Arm C** — 100% price buffer (no extra stock, rely on discounting)

**Reward** = $ saved that simulated week vs. a "hold lots of stock always" baseline, as reported by the **[[Digital Twin]]**. Higher is better.

Thompson Sampling stores a *range of plausible values* per arm, not a single point estimate. At the start, all three arms look identical: "probably around $5,000/week, could be $2,000–$8,000." That width is the model's honest uncertainty.

**Each round:**
1. Draw one random guess from each arm's current belief range.
2. Play whichever arm drew the highest guess.
3. Observe the twin's actual simulated result.
4. Narrow that arm's belief toward the real result — mean shifts, uncertainty shrinks.

| Round | Guess A | Guess B | Guess C | Arm played | Actual result | Updated belief |
|---|---|---|---|---|---|---|
| 1 | $6,200 | $4,100 | **$7,800** | C | $3,000 | C: mean drops to ~$3,800, narrows |
| 2 | $5,900 | **$7,100** | $2,600 | B | $6,500 | B: mean rises to ~$5,800, narrows |
| 3 | $4,700 | **$8,000** | $2,100 | B | $7,000 | B: mean rises to ~$6,300, tighter |
| 4 | $6,600 | **$6,900** | $1,900 | B | $6,800 | B: mean ~$6,450, tighter |
| 5 | $3,000 | **$7,300** | $3,400 | B | $6,600 | B: mean ~$6,500, tight |

**What happened, and why it matters for this project specifically:**
- **Arm C** was tried once, underperformed, and its belief narrowed *downward* — it isn't banned, just increasingly unlikely to be picked again. This is the mechanism that lets a bad price-buffer call get corrected automatically instead of persisting until someone notices in a quarterly review.
- **Arm B** performed well when tested and kept sharpening around a strong number, so it wins more rounds over time — the system is converging on the hybrid strategy for this segment.
- **Arm A** was never picked in this run, but its belief *stays wide*, so it can still occasionally get tested. Nothing is permanently ruled out — which is exactly the property a static, one-shot policy doesn't have.

Over more rounds the system settles on Arm B as the default while still spending occasional weeks re-checking A and C, in case conditions change. That "still occasionally checking" behavior is the literal mechanism behind the word **self-recalibrating** in the project's own title.

---

## 5. The Part That Breaks a Naive Bandit: Seasonality

A vanilla bandit assumes the best arm never changes. **[[Non-Stationarity (Bandits)|Seasonality violates that assumption directly]]**: Beverages peak in summer, where stockouts are expensive and the inventory-buffer arm wins — but the same SKU in winter has low, price-sensitive demand, where the price-buffer arm wins instead. If the bandit's belief has already narrowed tight from a summer run, it will keep confidently recommending the summer answer into the fall, precisely because it's too confident to bother re-checking.

**Fix used in this project:** the seasonal curve per category is already known and embedded in the synthetic data generator. Rather than making the bandit *rediscover* seasonality through drift detection, feed it directly as **context**: keep a separate belief per **(segment × seasonal phase)** — peak / shoulder / trough — instead of one belief per segment. A secondary safety net (a floor on how tight uncertainty is allowed to get, or a sliding window over recent weeks) covers unknown shocks that aren't on the seasonal calendar, like a surprise competitor promotion.

This matters most for the **Seasonal segment** (~30% of SKUs per the SKU Segmentation split, but the largest demand amplitude in the portfolio). For Fast-Moving and Intermediate segments a single belief per segment is likely sufficient — seasonality barely moves the needle there, so the added context dimension isn't worth the extra sparsity it introduces.

---

## 6. The Three Decision Points — Mapping Onto the Charter's Own Layers

> [!tip] One engine, reused three times
> Bandits don't replace the underlying MEIO / safety-stock / elasticity math — that math still produces the actual numbers. Bandits sit **above** it, choosing *which policy or strategy template* to apply, and re-checking that choice as outcomes come in. The design goal is **one generic bandit engine, reused three times with three different arm sets** — not three separate builds.

### 6.1 Price buffer vs. inventory buffer routing → [[Price-Inventory Exchange Rate|Layer 4]] of Supply Brain
- **Arms**: discrete buffer-mix templates per SKU segment — `100% inventory / 75-25 / 50-50 / 25-75 / 100% price`.
- **Reward**: twin-simulated total cost for that mix (holding cost + stockout cost + capital cost − price-buffer discount cost).
- **Deliverable link**: this *is* the mechanism behind the charter's headline **[[Price-Inventory Exchange Rate]]** analysis. Instead of a one-shot regression estimate, the exchange rate becomes something the system converges to and continuously re-tunes — a stronger, differentiated version of a deliverable already committed in the charter, not an addition to it.

### 6.2 MEIO echelon positioning → [[Multi-Echelon Inventory Optimization (MEIO)|Layer 3]] of Supply Brain
- **Arms**: predefined positioning templates — `hold at Plant DC / pool at Regional DC / push to Customer DC / split Regional+Customer`.
- **Reward**: twin-simulated total inventory cost net of the service-level outcome achieved.
- **Deliverable link**: directly supports the charter's committed "MEIO output — network-level buffer positioning recommendations showing working capital reduction vs. a single-node baseline."

### 6.3 Service-level target selection → [[Service-Level Target|Layer 2]] of Supply Brain
- **Arms**: discrete service-level tiers per segment — `90% / 95% / 97% / 99%`.
- **Reward**: twin-simulated stockout cost vs. holding cost trade-off at that target.
- **Deliverable link**: supports "differentiated service-level targets per segment" — makes the target itself something the system optimizes and re-checks, rather than a fixed policy assumption set once at kickoff.

---

## 7. Deployment Pipeline — Mapped to the Charter's Actual Phase Dates

> [!important] No new calendar time requested
> Every bandit-track activity below is scoped to run **inside** an existing charter phase window, not alongside it. This is deliberate: the track has to remain visibly additive to committed deliverables, and asking for separate calendar time would undercut that positioning.

| Charter Phase | Window | Charter's own committed activity | Bandit-track activity riding inside it |
|---|---|---|---|
| **Phase 1 — Data Preparation & Sampling** | 8–21 Aug 2026 | Synthetic dataset generation, cleaning, sampling, security setup | *(none — bandit track has no dependency this early)* |
| **Phase 2 — Exploration & Segmentation** | 22 Aug–4 Sep 2026 | SKU segmentation validated with mentor feedback | *(none yet — waits on validated segments as its context variable)* |
| **Phase 3 — Model Development & Optimization** | 5–25 Sep 2026 | Service-level optimization, MEIO, price-elasticity modeling | **Build the generic bandit engine**: arm configuration, reward functions, Thompson Sampling sampler/updater — tested standalone against *mocked* twin output, since the real twin doesn't exist yet in this phase |
| **Phase 4 — Simulation & Digital Twin Testing** | 26 Sep–9 Oct 2026 | Digital twin sandbox built and tested; failure signals captured | **Wire the bandit engine to the real digital twin**; run the full 52-week 2026 forward horizon per segment — this is what produces the belief-evolution data needed for the exchange-rate (§6.1) and MEIO (§6.2) deliverables |
| **Phase 5 — Agentic Interface & LLM Integration** | 10–16 Oct 2026 | Natural-language interface, LLM translation layer, usability testing | **Expose current bandit recommendations + confidence through the agentic layer** — see §8 for the exact toolbox this plugs into |
| **Phase 6 — Assessment & Refinement** | 17–30 Oct 2026 | KPI evaluation, refinement, final report and documentation | **Compare the bandit-converged policy against the static baseline** on the charter's own KPIs (working capital reduction, service-level improvement) — this produces the before/after story for the final report |

**Why this sequencing is feasible given what's already committed elsewhere in the charter:**
- The **digital twin already planned for Phase 4** is exactly the safe, cheap reward-signal generator a bandit needs. Exploration happens entirely in simulation, never against a live customer — which removes the usual real-world objection to letting an algorithm "experiment" with inventory policy.
- The dataset's **synthetic-first, 52-week forward horizon** (see [[Synthetic-First Data Strategy]]) provides enough repeated rounds per segment for beliefs to actually converge within the project timeline, especially when pooled by segment rather than run per-SKU.
- The dataset already encodes **known seasonal curves per category**, which lets the bandit use seasonal phase directly as context (§5) instead of having to rediscover seasonality via drift detection from scratch — a meaningfully cheaper build than the general non-stationary-bandit literature usually assumes.

**Fixed external milestones this pipeline has to land inside:** weekly progress reports every Sunday to sponsor/faculty mentor/program office · final presentation to sponsor by **19 Oct 2026** · final report submission **25 Oct 2026** · project handover by **30 Oct 2026**.

---

## 8. The Agentic Layer Built on Top

*Scope note: charter Phase 5 (10–16 Oct 2026) is a one-week window — this is intentionally a small, fixed toolset wired to an LLM with function-calling, not an open-ended conversational system.*

**What "agentic" means here, concretely.** Not a new algorithm — an LLM given a fixed toolbox of functions drawn from the bandit engine and the digital twin, which it can call. The LLM's job: map the planner's plain-English question to the right tool, call it, turn the structured result back into plain English, and ask for explicit confirmation before anything gets written or committed. This matches the charter's two-directional **[[LLM-Powered Translation]]** layer (English → structured parameters, structured output → English) plus the **[[Agentic Interface]]**'s own requirement of human confirmation before commitment.

| Tool | Underlying call | Purpose | Can it write anything real? |
|---|---|---|---|
| `get_recommendation` | `bandit/recommend.py` | "What's the current best strategy for this segment?" | No — read-only |
| `explain_recommendation` | `recommend.py` + belief history | "Why is that the recommendation?" — turns numbers (mean reward, confidence, arms tried) into a reasoned explanation | No — read-only |
| `run_whatif` | `bandit/twin_interface.py`, sandboxed | "What if lead time went up 2 weeks for this cluster?" — runs a fresh twin simulation with a changed assumption | No — sandboxed, doesn't touch live belief state |
| `trigger_recalculation` | `bandit/loop.py` | Re-runs the bandit loop for a segment given new data (e.g. an updated promo calendar) | Updates belief state only, not downstream policy systems |
| `flag_anomaly` | compares actual vs. forecast, or watches reward volatility | Runs proactively — e.g. "this segment's chosen arm has underperformed 3 weeks running, possible regime shift" | No — alert only |
| `apply_recommendation` | writes to the actual policy table used downstream | The only tool that changes anything real | **Yes — gated behind explicit planner confirmation** |

> [!important] The load-bearing design decision
> The gating on `apply_recommendation` is not a nice-to-have UX detail — it is the literal implementation of the charter's "human confirmation before anything is committed" requirement. Everything upstream of it (`get_recommendation`, `explain_recommendation`, `run_whatif`) is free exploration the agent can run without limit, because it only ever touches the twin in sandbox mode.

**Example interaction, reproduced from the design doc:**

> **Planner:** "Why is the system holding so much inventory for the summer beverage SKUs?"
> **Agent** (`explain_recommendation`): "For Seasonal-Beverages in peak phase, the system favors 100% inventory buffer over price flexibility, 79% confidence based on 14 simulated weeks. In peak season, stockouts cost roughly 3x more than holding the extra stock, so the model leans toward stock here — it favored a 50/50 hybrid in the off-season instead."
>
> **Planner:** "What if our lead time from the Atlanta plant goes up by 5 days next month?"
> **Agent** (`run_whatif`): "Simulating... stockout risk for the Southeast region rises from 3% to 11% weeks-at-zero-stock unless safety stock increases ~18%. Want me to test how the buffer-mix recommendation changes under this assumption?"
>
> **Planner:** "Yes, and if it looks better, apply it to that segment."
> **Agent** (`trigger_recalculation`, then confirms before `apply_recommendation`): "New recommendation: shift to 70% inventory / 30% price buffer, projected to save $4,200/week vs. current. Confirm to apply?"

**Relationship to the bandit engine.** The agent does not decide strategy — it is a conversational front-end over decisions the bandit engine has already made or can simulate. Every tool above maps directly to a function already scoped in §6–7 (`recommend.py`, `twin_interface.py`, `loop.py`). No new decision logic is invented at this layer; the "intelligence" a planner perceives is a well-chosen function call into already-scoped, auditable work from Phases 3–4.

**Build sequencing within Phase 5:**
1. Requires the bandit engine's `recommend.py` and `twin_interface.py` to already exist (Phases 3–4) — this layer has no independent data or model logic of its own.
2. Stack: LangChain/LangGraph for the tool-calling orchestration, per the charter's own data plan.
3. Given the one-week window, prioritize `get_recommendation`, `explain_recommendation`, `run_whatif` first — these three alone deliver a working conversational demo. `trigger_recalculation`, `flag_anomaly`, `apply_recommendation` are stretch goals if time allows.

---

## 9. Open Risks and Caveats

- **Non-stationarity** — demand, elasticity, and cost structures drift with seasonality and market conditions, violating the "fixed unknown payout" assumption of a vanilla bandit. Addressed via sliding-window/discounted-reward variants and seasonal-phase context (§5) — this needs to stay explicit in the implementation, not be assumed away.
- **Delayed / batched feedback** — reward is only observable after a simulated replenishment cycle (roughly weekly), not per micro-decision. Requires batch-update bandit variants, not the naive per-pull-update formulation typically taught in the vanilla-bandit literature. See **[[Batched Feedback]]**.
- **Combinatorial action space** — true MEIO positioning is a multi-node, continuous allocation problem. To fit the bandit framework at all, decisions have to be discretized into a small number of predefined strategy templates (the arms), rather than solved as a continuous bandit — a simplification worth flagging explicitly in the final technical report, not glossing over. See **[[Combinatorial Action Space]]**.
- **This is an enhancement layer, not a replacement.** The bandit selects *which policy* to use; the policy's actual numbers still come from the standard **[[Safety Stock]]** / MEIO optimization models underneath it.
- **Feedback-loop data gap** — `Docs/MEIO in FnB.md` flags actual-vs-forecast drift tracking as an unresolved gap in the current data schema. The bandit track depends on that exact class of data (observed outcomes over time), so closing this gap is a shared dependency with the core MEIO build, not bandit-specific scope creep.
- **Team scope guardrail** — this track must remain *visibly additive* to committed deliverables. If time runs short, the generic engine (Phase 3) plus decision point 6.1 (price/inventory routing, given its direct deliverable link to the exchange rate) should be prioritized over attempting all three decision points.

---

## 10. Why This Matters — Tying Back to the Charter's Own Words

Three phrases from the charter's Project Description, and how this track answers each one directly:

- *"continuously re-learns from outcomes"* → this is Thompson Sampling's belief-update loop (§4), running against real digital-twin output (§7, Phase 4).
- *"automatically re-tunes targets and buffer positions... rather than remaining static after initial setup"* → this is exactly what happens across all three decision points in §6, none of which require a human to manually re-run anything once deployed.
- *"human confirmation before anything is committed"* → this is the `apply_recommendation` gate in §8, the only tool in the entire agentic toolbox capable of writing anything real.

And one strategic argument the sponsor cares about beyond the mechanics: the price-inventory **[[Price-Inventory Exchange Rate|"exchange rate"]]** is explicitly called out in the charter as strategic/IP impact for Accenture — "a differentiated, deployable framework Accenture can take to CPG clients as a new offering." A bandit-converged exchange rate that keeps re-tuning itself per segment is a materially stronger version of that IP claim than a static regression coefficient computed once and left alone — which is the argument for why this track is worth the research investment reflected in this note, even before sponsor sign-off.
