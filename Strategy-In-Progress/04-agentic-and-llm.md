# Agentic / LLM Layer — How It Connects to the Bandit Strategy

*Scope note: charter Phase 5 (10–16 Oct 2026) is a one-week window. This is intentionally a small, fixed toolset wired to an LLM with function-calling — not an open-ended conversational system.*

## What "agentic" means here, concretely

Not a new algorithm — an LLM given a fixed toolbox of functions (from the bandit engine and the digital twin) that it can call. The LLM's job:

1. Map the planner's plain-English question or instruction to the right tool.
2. Call it.
3. Turn the structured result back into plain English.
4. Ask for explicit confirmation before anything gets written/committed.

This matches the charter's two-directional "LLM-Powered Translation" layer (English → structured parameters, and structured output → English) plus the "Agentic Interface" layer (autonomously triggers recalculations, runs what-ifs, flags anomalies, recommends actions, **human confirmation before anything is committed**).

## The toolbox

| Tool | Underlying call | Purpose | Can it write anything real? |
|---|---|---|---|
| `get_recommendation` | `bandit/recommend.py` | "What's the current best strategy for this segment?" | No — read-only |
| `explain_recommendation` | `recommend.py` + belief history | "Why is that the recommendation?" — turns numbers (mean reward, confidence, arms tried) into a reasoned explanation | No — read-only |
| `run_whatif` | `bandit/twin_interface.py` in sandboxed mode | "What if lead time went up 2 weeks for this cluster?" — runs a fresh twin simulation with a changed assumption | No — sandboxed, does not touch live belief state |
| `trigger_recalculation` | `bandit/loop.py` | Re-runs the bandit loop for a segment given new data (e.g., updated promo calendar) | Updates belief state, not downstream policy systems |
| `flag_anomaly` | compares actual vs. forecast, or watches bandit reward volatility | Runs proactively — e.g., "this segment's chosen arm has underperformed 3 weeks running, possible regime shift" | No — alert only |
| `apply_recommendation` | writes to the actual policy table used downstream | The only tool that changes anything real | **Yes — must be gated behind explicit planner confirmation** |

The gating on `apply_recommendation` is the load-bearing design decision, not a nice-to-have: it is the literal implementation of the charter's "human confirmation before anything is committed" requirement.

## Example interaction

> **Planner:** "Why is the system holding so much inventory for the summer beverage SKUs?"
> **Agent** (`explain_recommendation`): "For Seasonal-Beverages in peak phase, the system favors 100% inventory buffer over price flexibility, 79% confidence based on 14 simulated weeks. In peak season, stockouts cost roughly 3x more than holding the extra stock, so the model leans toward stock here — it favored a 50/50 hybrid in the off-season instead."

> **Planner:** "What if our lead time from the Atlanta plant goes up by 5 days next month?"
> **Agent** (`run_whatif`): "Simulating... stockout risk for the Southeast region rises from 3% to 11% weeks-at-zero-stock unless safety stock increases ~18%. Want me to test how the buffer-mix recommendation changes under this assumption?"

> **Planner:** "Yes, and if it looks better, apply it to that segment."
> **Agent** (`trigger_recalculation`, then confirms before `apply_recommendation`): "New recommendation: shift to 70% inventory / 30% price buffer, projected to save $4,200/week vs. current. Confirm to apply?"

Everything before the final confirmation is free exploration — the agent can run unlimited what-ifs because it's only calling the twin in sandbox mode. Only the last step touches anything real.

## Relationship to the bandit track

The agent does not decide strategy — it is a **conversational front-end over decisions the bandit engine has already made or can simulate**. Every tool call in the table above maps directly to a function already scoped in `03-usage-in-project.md`'s bandit engine (`recommend.py`, `twin_interface.py`, `loop.py`). No new decision logic is being invented at this layer; the "intelligence" a user perceives is a well-chosen function call into already-scoped, auditable work from Phases 3–4.

## Build sequencing

1. Requires the bandit engine's `recommend.py` and `twin_interface.py` to exist first (Phases 3–4) — this layer has no independent data/model logic of its own.
2. Suggested stack per the charter's own data plan: LangChain/LangGraph for the tool-calling orchestration.
3. Given the one-week window, prioritize: `get_recommendation`, `explain_recommendation`, `run_whatif` first (these alone deliver a working conversational demo); `trigger_recalculation`, `flag_anomaly`, `apply_recommendation` are stretch goals if time allows.

## Open risks

- **Scope creep risk**: resist handling arbitrary open-ended planner requests. The toolset above should stay fixed and small; breadth is not the goal in a one-week build window.
- **Confirmation UX**: needs a concrete, unambiguous mechanism for "planner confirmed" (not just the LLM inferring assent from conversational tone) before `apply_recommendation` fires.
- **Dependency on bandit track**: if the bandit engine (Phase 3–4) slips, this layer has nothing real to call and would need to fall back to a demo against mocked/static data — worth flagging as a schedule risk, not treating as independent.
