# Strategy In Progress — Multi-Armed Bandit Track

Exploratory documentation for a candidate mechanism (multi-armed bandits) to implement the closed-loop, self-recalibrating layer described in the Project Description Form and the Capstone Project Charter. This is a parallel investigation track, not yet approved scope — treat as a proposal under review, not a committed deliverable.

## Contents

1. [`01-definition.md`](01-definition.md) — formal definition, terminology, algorithm options, and known caveats.
2. [`02-simplified-explanation.md`](02-simplified-explanation.md) — beginner-friendly explanation with a fully worked numeric example, including how seasonality is handled.
3. [`03-usage-in-project.md`](03-usage-in-project.md) — how the mechanism maps onto the project's three candidate decision points (price/inventory routing, MEIO positioning, service-level targets) and the existing charter phase timeline.
4. [`04-agentic-and-llm.md`](04-agentic-and-llm.md) — how the agentic/LLM layer (charter Phase 5) would sit on top of the bandit engine as a conversational front-end, including the tool list and human-confirmation gating.

## Status

Under discussion. Not yet reflected in `Docs/Capstone Project Charter AMPBA 2026S.docx` or `Docs/MEIO in FnB.md` — if adopted, both should be updated to reference this track.
