Capstone project, ISB AMPBA 2026S, sponsored by **Accenture** (Solutions Pvt. Ltd. — Supply Chain & Engineering, AI and Data practice). Client mentors: Sayanjit Dey Sarkar (Senior Manager), Aman Garg (Manager). Team of five — Swasti Baghel, N Sunnel, Vishwajeet Undhal, Shivangini Aggarwal, Varshil Rambhia. This note is written to be **navigated from**: every layer of the system links out to its own concept note, and the mechanism the team is currently betting on to make the system's "self-recalibrating" claim real — **[[Multi-Armed Bandits — Supply Brain Strategy|multi-armed bandits]]** — has its own full deep-dive note, because it is the least conventional and most heavily researched piece of the build.

> [!tip] The one-sentence problem the whole project exists to solve
> *"Every CPG supply chain faces the same paradox — warehouses full of the wrong inventory while stockouts occur on the SKUs that matter."* The root cause isn't bad forecasting. It's structural: one safety-stock formula applied uniformly across thousands of SKUs, service-level targets set once and never revisited, lead-time variability averaged away instead of modeled, and no feedback loop connecting outcomes back to the parameters that caused them. **Supply Brain** is a closed-loop, self-recalibrating buffer optimization system built to close exactly that gap.

**Project map — the eight integrated layers:**
1. **[[SKU Segmentation]]** — cluster SKUs by demand variability, lead-time variability, elasticity, shelf life, margin, and criticality, so every downstream decision is segment-driven rather than one-size-fits-all.
2. **[[Service-Level Target|Service-Level Optimization]]** — differentiated service-level targets per segment instead of a blanket ~99% ceiling.
3. **[[Safety Stock]] & Cycle Stock via [[Multi-Echelon Inventory Optimization (MEIO)|MEIO]]** — decide both *how much* buffer to hold and *where* to position it across the network (factory → regional DC → local DC).
4. **[[Price Elasticity]] & [[Price-Inventory Exchange Rate|Buffer Strategy Routing]]** — for each segment, compare the cost of buffering with inventory vs. buffering with price, and quantify the exchange rate: *"$1 of price flexibility = $Y of safety stock."*
5. **[[Digital Twin]] Simulation** — a safe sandbox where every strategy is tested head-to-head under stochastic demand and lead time before being trusted with anything real.
6. **[[LLM-Powered Translation]]** — an LLM that translates plain-English planner input into structured model parameters, and structured model output back into plain English.
7. **[[Agentic Interface]]** — the natural-language front door to the whole system: triggers recalculations, runs what-ifs, flags anomalies, recommends actions — with human confirmation gating anything that actually gets committed.
8. **[[Knowledge Graph (Neo4j)|Knowledge Graph]]** — the Neo4j graph of the physical and commercial network (plants, DCs, lanes, customers, SKUs) that layers 1–7 all read from and write to.

Underneath all eight layers sits one design decision that shapes the entire build: **[[Synthetic-First Data Strategy|the system is built synthetic-first]]** — realistic Packaged Foods CPG data generated internally so every workstream proceeds in parallel without waiting on client data access, with anonymized client-data validation deferred to the final phase.

---

# The Business Case

## Why this exists — the current state
Across the CPG Food & Beverage portfolio, a single blanket service-level target (~99%) gets applied uniformly to every SKU, ignoring SKU-specific realities like forecast accuracy, perishability, seasonality, and channel-driven demand swings. Safety-stock formulas stay static and are rarely revisited even as promotions, trends, or supply conditions shift. The result: capital gets locked up protecting SKUs that don't need it, while genuinely critical SKUs aren't safeguarded any better than the rest. This is the pattern Accenture's Supply Chain & Engineering practice sees repeatedly across CPG, retail, and manufacturing engagements — which is why the project is sponsored.

## The desired state
A dynamic, self-recalibrating buffer policy where service levels are set by SKU economics, not a uniform default — continuously adjusted through closed-loop monitoring of stockouts, write-offs, and working-capital impact, with the system automatically re-tuning targets and buffer positions as conditions shift, **rather than remaining static after initial setup.** That last clause — "rather than remaining static" — is the specific sentence in the charter that motivates evaluating a learning mechanism (the bandit track) instead of a one-shot optimization.

## Four measurable areas of impact
| Area | What "success" looks like |
|---|---|
| **Working capital** | Reduce redundant safety stock across echelons by routing price-elastic SKUs to price-buffer strategies instead of inventory buffer — freeing trapped capital without hurting service. |
| **Service level** | Protect service on SKUs that matter most (high-margin, critical, low-substitutability) through segment-differentiated targets and multi-echelon positioning. |
| **Write-offs / obsolescence** | Reduce risk on high-variety, demand-uncertain SKUs via elasticity-aware forecasting, promotions, and the agentic interface. |
| **Strategic / IP impact for Accenture** | The price-inventory "exchange rate" concept and the closed-loop agentic architecture are positioned as a differentiated, deployable framework Accenture can take to CPG clients — with this prototype as the concrete next-step engagement. |

---

# Layer 1 — SKU Segmentation

> [!tip] Every downstream decision inherits its shape from here
> Nothing later in the system — not the service-level target, not the MEIO positioning, not which bandit arm gets tried — makes sense without first answering "which kind of SKU is this?" Get segmentation wrong and every layer built on top of it is optimizing the wrong thing for the wrong SKU.

**[[SKU Segmentation]]** clusters the portfolio by demand variability, lead-time variability, price elasticity, shelf life, margin, and criticality — with a manual-override path for edge cases (e.g. new launches) so the tiering isn't 100% mechanical. In the project's synthetic dataset, this settles into roughly a **50% Fast-Moving / 30% Seasonal / 20% Intermediate** mix, derived from real observed volume and seasonality patterns in the 4-year, ~10M-row POS history rather than assigned at random. The Seasonal segment — smaller in count but the largest in demand amplitude — turns out to matter disproportionately later, in the bandit track's handling of [[Non-Stationarity (Bandits)|non-stationarity]].

---

# Layer 2 — Service-Level Optimization

**[[Service-Level Target]]** design replaces the uniform ~99% policy with differentiated targets per segment — roughly 97%+ for the top tier, tapering to ~90% for the tail. This is deliberately set against real benchmark evidence rather than an aspirational ceiling: a GMA/BCG study of 30+ US CPG manufacturers found a median *achieved* case fill rate of only **89.7%** — closer to what "current state" should be assumed to look like than the 99% often quoted as a target. See **[[Case Fill Rate]]** for how this is actually measured and why it diverges from the raw service-level percentage.

---

# Layer 3 — Safety Stock, Cycle Stock, and MEIO

**[[Safety Stock]]** and cycle stock are calculated dynamically, with demand-side and supply-side variability modeled explicitly rather than averaged away — lead-time variability, in particular, is assigned per lane and category (domestic/regional lanes ~3–7 day mean, CV 0.2–0.4; longer or imported lanes ~10–20 day mean, CV 0.4–0.6) instead of one global standard deviation.

**[[Multi-Echelon Inventory Optimization (MEIO)]]** extends this from *how much* to *where*: across a network structured as **Plant → Plant DC → Regional DC → Customer DC**, with the plant itself holding zero inventory (production-only) and the Plant DC as the first stock-holding echelon. MEIO decides whether to hold buffer close to the customer (fast response, higher cost) or pool it further upstream (cheaper, slower) — the deliverable is explicitly "network-level buffer positioning recommendations showing working capital reduction vs. a single-node baseline."

---

# Layer 4 — Price Elasticity & Buffer Strategy Routing

> [!tip] The idea no existing supply-chain system makes systematically
> Two SKUs can have identical stockout risk and still deserve completely different treatment — one should get more safety stock, the other should get a discount lever instead. The reason: elasticity. A price-elastic SKU can absorb demand uncertainty with a small, cheap price nudge; an inelastic SKU can't be discounted its way out of a stockout and needs the stock. This is the system's core differentiator, and it's the one that gets an "exchange rate" attached to it.

**[[Price Elasticity]]** is estimated per SKU/category via log-log regression on real promotional discount data already present in the transaction history (~21% of transactions carry a promo, 5–50% discount depth) — deliberately **not** fabricated, since real price variation already exists to estimate from. Benchmark ranges: staples/private-label toward −2.5 to −3.5 (most elastic), premium/differentiated toward −0.8 to −1.5 (least elastic), mainstream FMCG in between.

**[[Price-Inventory Exchange Rate|Buffer Strategy Routing]]** turns that elasticity estimate into an action: for each segment, compare the cost of buffering with inventory (holding cost + capital cost) against buffering with price (discount cost), and quantify *"$1 of price flexibility = $Y of safety stock for this segment."* This is exactly the deliverable the **[[Multi-Armed Bandits — Supply Brain Strategy|bandit track]]** is designed to strengthen — turning a one-shot regression estimate into a number the system continuously re-converges to as conditions change. See that note for the full mechanism.

---

# Layer 5 — Digital Twin Simulation

**[[Digital Twin]]** is the safe sandbox where every strategy gets tested before being trusted with anything real: inventory buffering vs. price buffering, single-node vs. multi-echelon positioning, head-to-head, under stochastic demand and lead time. Its job is to generate the **failure signals** — stockouts, write-offs, margin impact, working capital — that any learning mechanism sitting on top of it needs as a reward signal. This makes the twin the load-bearing piece for the bandit track specifically: exploration happens in simulation, never against a live customer, which is what removes the usual objection to letting an algorithm "experiment" with real inventory policy.

---

# Layer 6 & 7 — LLM Translation and the Agentic Interface

**[[LLM-Powered Translation]]** handles both directions of the language problem: plain-English planner input → structured model parameters, and structured model output → plain-English explanation a planner can act on immediately.

**[[Agentic Interface]]** is the front door built on top of that translation layer — it autonomously triggers recalculations, runs what-if scenarios, flags anomalies, and recommends actions, with **human confirmation required before anything is committed.** That confirmation gate is not a UX nicety; it is the literal mechanism that makes the system safe to expose to a planner who isn't a data scientist. The full toolbox design — six tools, only one of which can write anything real — is detailed inside the **[[Multi-Armed Bandits — Supply Brain Strategy|bandit deep-dive note]]**, since in the current design the agentic layer's entire job is to be a conversational front-end over decisions the bandit engine has already made or can simulate.

---

# Layer 8 — Knowledge Graph

**[[Knowledge Graph (Neo4j)]]** models the physical and commercial network as nodes (Plant, PlantDC, RegionalDC, CustomerDC, Customer, Store, SKU) and edges (`PRODUCES`, `SHIPS_TO`, `SERVES`, `BELONGS_TO`, `STOCKS`, `FLAGGED_AS`) built directly off the project's table register. Every lane in the network — with its lead-time mean/std, transport mode, and cost — lives in a single unified edge table by design, so it doubles cleanly as the Neo4j edge list rather than needing a separate graph-specific schema.

---

# The Data Foundation

The system runs on a hybrid of **real, derived, and synthetic** data, tagged explicitly rather than blended silently — a discipline worth internalizing since it recurs everywhere in the schema:

| Status | Meaning | Example in this project |
|---|---|---|
| 🟢 **Real** | Exists today in the retail dataset, used as-is | `sales_transactions` (~10M rows, 4 yrs, 5,000 SKUs, 30 stores), `sku_master`, `inventory_snapshot` |
| 🔵 **Derived** | Computed/estimated *from* real data (regression, aggregation) | `price_elasticity` — log-log regression on real discount/quantity variation |
| 🔴 **Synthetic** | Doesn't exist in any source; generated from scratch, calibrated to researched CPG benchmarks | The entire upstream US network (plants, DCs, lanes) — the real dataset's 30 stores sit in South Asian cities and are geographically decoupled from the synthetic upstream network by design, connected only through the SKU/customer key structure |

Every synthetic parameter (lead-time CV, holding cost %, elasticity range, forecast MAPE) is anchored to a researched CPG/F&B industry benchmark rather than an arbitrary number — see **[[Synthetic-First Data Strategy]]** for the full source list and the calibration table.

---

# ⭐ The Multi-Armed Bandit Track — Why It's the Focus

> [!important] Read the full deep-dive
> This is deliberately the most heavily researched piece of the project outside the committed scope, because it's the mechanism being evaluated to make the charter's *"continuously re-learns from outcomes"* and *"automatically re-tunes targets... rather than remaining static"* language literally true, instead of just aspirational. The full argument — definition, algorithm comparison, the three decision points it maps onto, the worked numeric example, the seasonal-non-stationarity fix, the agentic toolbox built on top of it, and the phase-by-phase deployment plan mapped against the charter's actual dates — lives in **[[Multi-Armed Bandits — Supply Brain Strategy]]**.

In one paragraph: a standard optimization — a regression, an LP solve — produces one fixed answer computed once from historical data. The charter's own desired-state language describes something that keeps re-learning as outcomes come in. That is, structurally, a description of bandit behavior, independent of whether the team ultimately implements it as one. **[[Multi-Armed Bandit|Multi-armed bandits]]** are being evaluated as the mechanism that makes layers 3, 4, and 5 self-recalibrating rather than one-shot — not as a replacement for the underlying MEIO/safety-stock/elasticity math, but as a layer sitting *above* it that chooses which policy template to apply and keeps re-checking that choice as conditions change.

**Status: exploratory, not yet approved by sponsor.** It runs as a parallel investigation track alongside the core MEIO build and is not yet reflected in the Capstone Project Charter or the data-schema document — if adopted, both should be updated to reference it.

---

# Phase Timeline (from the Capstone Project Charter)

| Phase | Window | Key deliverable |
|---|---|---|
| **1 — Data Preparation & Sampling** | 8–21 Aug 2026 | Clean, representative dataset ready for modeling; secure hosting environment |
| **2 — Exploration & Segmentation** | 22 Aug–4 Sep 2026 | SKU segments validated with mentor feedback; clear segmentation logic |
| **3 — Model Development & Optimization** | 5–25 Sep 2026 | Working service-level, MEIO, and price-elasticity models |
| **4 — Simulation & Digital Twin Testing** | 26 Sep–9 Oct 2026 | Digital twin sandbox tested; failure signals captured for iterative learning |
| **5 — Agentic Interface & LLM Integration** | 10–16 Oct 2026 | Prototype agentic interface functional; planners can interact conversationally |
| **6 — Assessment & Refinement** | 17–30 Oct 2026 | Final prototype refined; KPI improvements documented; final report ready |

**Fixed milestones:** Charter finalized 7 Aug 2026 · weekly progress reports every Sunday · final presentation to sponsor by 19 Oct 2026 · final report submission 25 Oct 2026 · project handover by 30 Oct 2026.

The bandit track is scoped to run **inside** this timeline with no new calendar time requested — see **[[Multi-Armed Bandits — Supply Brain Strategy]]** for exactly how its activities map onto Phases 3–6 above.

---

# Status Checklist
- [x] Charter finalized and signed off (7 Aug 2026)
- [x] Data schema / table register drafted — real vs. derived vs. synthetic tagged (`Docs/MEIO in FnB.md`)
- [x] CPG/F&B benchmark research completed and cited (lead time, holding cost, service level, elasticity, forecast MAPE)
- [x] Multi-armed bandit strategy track fully documented (definition, worked example, project mapping, agentic design) — **exploratory, pending sponsor approval**
- [ ] Feedback-loop / actual-vs-forecast drift-tracking data — flagged gap, not yet resolved (shared dependency between the core MEIO build and the bandit track)
- [ ] SKU segmentation validated with client mentor (Phase 2, due 4 Sep 2026)
- [ ] Sponsor decision on whether the bandit track becomes committed scope or stays an enhancement layer
- [ ] Bandit engine (`recommend.py`, `twin_interface.py`) built and tested standalone (Phase 3, due 25 Sep 2026)
- [ ] Digital twin wired to real bandit engine; 52-week 2026 forward horizon run (Phase 4, due 9 Oct 2026)
- [ ] Agentic layer exposes bandit recommendations + confidence conversationally (Phase 5, due 16 Oct 2026)
- [ ] Bandit-converged policy compared against static baseline on charter KPIs (Phase 6, due 30 Oct 2026)
