**Synthetic-First** is [[Supply Brain]]'s foundational data decision: build the system on realistic, internally-generated Packaged Foods CPG data rather than waiting on client data access, with anonymized client-data validation deferred to the final phase. This lets every workstream — segmentation, MEIO, elasticity, the digital twin — proceed in parallel instead of blocking on data availability.

**The three-tier tagging discipline.** Rather than blending real and generated data silently, every table in the schema is tagged explicitly:
| Status | Meaning |
|---|---|
| 🟢 **Real** | Exists today in the real retail dataset — used as-is |
| 🔵 **Derived** | Computed/estimated *from* real data (regression, aggregation) — not fabricated |
| 🔴 **Synthetic** | Doesn't exist anywhere today; generated from scratch, anchored to real keys and calibrated to researched benchmarks |

The real dataset (~10M POS transaction rows, 4 years, 5,000 SKUs, 30 stores in South Asian cities) provides the demand-side and customer-side ground truth. The synthetic layer builds a benchmark-calibrated US multi-echelon network — plants, DCs, lanes, costs — on top of it. The two layers are **geographically decoupled by design**, connected only through the SKU/customer key structure, not physical geography.

**Why calibration matters — "not arbitrary numbers."** Every synthetic parameter is anchored to researched CPG/F&B industry benchmarks rather than invented: lead-time variability follows APQC cross-industry data (top performers ~4 days vs. bottom performers ~11 days supplier lead time); holding cost follows CPG-average ranges (20–30%/yr, 30–40% for perishables); **[[Case Fill Rate]]** targets follow a GMA/BCG study of 30+ US manufacturers (median achieved 89.7%, not the 99% aspirational ceiling); **[[Price Elasticity]]** ranges follow FMCG meta-analysis; forecast error is calibrated so realized MAPE lands ~8–12% for fast-movers, 15–20% intermediate, 25–40% long tail.

**Why `price_elasticity` is 🔵 Derived, not 🔴 Synthetic.** Real price variation already exists in the data through promotions (~21% of transactions, 5–50% discount range) — so elasticity is estimated via regression on that real variation rather than fabricated from scratch, a distinction the schema documentation is explicit about.

**A known, unresolved gap.** Feedback-loop data — actual-vs-forecast tracking, drift detection over time — is flagged as an open gap not covered by any table in the current schema. This is a shared dependency between the core MEIO build and the **[[Multi-Armed Bandits — Supply Brain Strategy|bandit track]]**, since both need exactly that class of "observed outcome over time" data to function.

Related: [[Supply Brain]], [[Knowledge Graph (Neo4j)]], [[Price Elasticity]]
