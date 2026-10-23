<!--
MASTER REPORT FILE: Report/Supply_Brain_Capstone_Report.md
- This single file is the source of truth. All section-wise edits are made here.
- Per-chapter copies in Report/report_md/chapters/ are GENERATED from this file by Report/split_master.py
  (re-run after every edit). Never edit those copies directly.
- Figures are embedded with paths relative to Report/ (e.g. ../input datasets/...). Each figure is
  followed by a plain-text "Image file:" line with its repository-relative path, so the reference
  survives if the file is read as plain text.
- Table titles sit above tables; figure captions sit below figures (ISB template convention).

CHANGE LOG (newest first)
- 2026-10-24: Replaced the stale chapter-drafts archive with per-chapter files generated from this master (Report/split_master.py -> Report/report_md/chapters/).
- 2026-10-24: Restructured Ch 6 and Ch 7 by topic. Ch 6 = feature engineering + clustering (segmentation features, transformations, iterations, clustering model choice, segmentation results). Ch 7 = MEIO (demand signals, models, validation, deployed results, scenarios, exchange rate incl. elasticity features). Renumbered tables/figures in both chapters and updated every cross-reference, the TOC and the lists of tables and figures.
- 2026-10-24: Restored the full §1.3 "Constraints" paragraph that had been compressed for word count (word caps are no longer a reason to trim).
- 2026-10-24: §1.3 out-of-scope list now says shelf life is configured in code (SHELF_LIFE_DAYS) but set to 3-4 years by assumption, so perishability and the cold chain never bind.
- 2026-10-24: Compiled from chapter drafts into this single master file.
-->

# CAPSTONE PROJECT REPORT SUBMITTED TO THE INDIAN SCHOOL OF BUSINESS FOR THE ADVANCED MANAGEMENT PROGRAMME IN BUSINESS ANALYTICS

## Supply Brain: A Self-Recalibrating Multi-Echelon Buffer Optimization System with Dynamic Price-Buffer Intelligence and a Natural-Language Agentic Interface

**Submitted by**

| Name | PGID |
|---|---|
| Swasti Baghel | [PGID] |
| Sunnel Nelson | [PGID] |
| Vishwajeet Undhal | [PGID] |
| Shivangini Aggarwal | [PGID] |
| Varshil Rambhia | [PGID] |

**Under the guidance of**

- [Faculty Mentor Name], Institute of Data Science, Indian School of Business
- Sayanjit Dey Sarkar (Senior Manager) and Aman Garg (Manager), Accenture Solutions Pvt. Ltd. (Supply Chain & Engineering, AI and Data practice)

AMPBA 2026S | October 2026

---

# Certificate

| Item | Details |
|---|---|
| Title of the Capstone Project | Supply Brain: A Self-Recalibrating Multi-Echelon Buffer Optimization System with Dynamic Price-Buffer Intelligence and a Natural-Language Agentic Interface |
| Name(s) of the Candidate(s) | Swasti Baghel, Sunnel Nelson, Vishwajeet Undhal, Shivangini Aggarwal, Varshil Rambhia |
| Name & Affiliation of the Faculty Mentor | [Faculty Mentor Name], Institute of Data Science, ISB |
| Name & Affiliation of the Industry Mentor | Sayanjit Dey Sarkar and Aman Garg, Accenture Solutions Pvt. Ltd. |
| Batch | AMPBA 2026S |
| Date / Month of Submission | October 2026 |

## Declaration of Originality

We declare that this report is our own original work, prepared for the Capstone Project of the Advanced Management Programme in Business Analytics at the Indian School of Business. All sources of information used have been duly acknowledged and cited. Our use of generative-AI tools is disclosed in Appendix A. We understand that any violation of the honor code will attract the penalties specified therein.

| Name | PGID | Signature | Date |
|---|---|---|---|
| Swasti Baghel | [PGID] | | [DD/MM/YYYY] |
| Sunnel Nelson | [PGID] | | [DD/MM/YYYY] |
| Vishwajeet Undhal | [PGID] | | [DD/MM/YYYY] |
| Shivangini Aggarwal | [PGID] | | [DD/MM/YYYY] |
| Varshil Rambhia | [PGID] | | [DD/MM/YYYY] |

## Sponsor Confidentiality Note

This report contains no sponsor or client data. Every table, figure and number comes from a synthetic consumer packaged goods (CPG) food and beverage dataset that the team built with its own generator (`supply_brain_generator_v5.py`). The demand side is calibrated to the shape of a public food and beverage sales dataset and to published industry benchmarks. No personally identifiable information was created or used. Network names, banners and SKU identifiers are fictitious. The framework, code and findings are shared with Accenture Solutions Pvt. Ltd. as the project sponsor. Circulation beyond ISB's LMS is subject to the sponsor's written approval.

---

# Acknowledgements

We thank the Indian School of Business and the Institute of Data Science for the opportunity to complete this capstone. We are grateful to our faculty mentor, [Faculty Mentor Name], for guidance on analytical rigour and structure. We thank Sayanjit Dey Sarkar and Aman Garg of Accenture's Supply Chain & Engineering AI and Data practice for framing a problem they meet in client work, and for challenging our assumptions at every checkpoint. Any errors that remain are our own.

Swasti Baghel, Sunnel Nelson, Vishwajeet Undhal, Shivangini Aggarwal, Varshil Rambhia
October 2026

---

# Table of Contents

- [Abstract](#abstract)
- [Executive Summary](#executive-summary)
- [Chapter 1: Introduction](#chapter-1-introduction)
- [Chapter 2: Review of Related Literature](#chapter-2-review-of-related-literature)
- [Chapter 3: Business Context and Data](#chapter-3-business-context-and-data)
- [Chapter 4: Methodology: Assumptions and Approach](#chapter-4-methodology-assumptions-and-approach)
- [Chapter 5: Exploratory Data Analysis](#chapter-5-exploratory-data-analysis)
- [Chapter 6: Feature Engineering and Clustering (SKU Segmentation)](#chapter-6-feature-engineering-and-clustering-sku-segmentation)
- [Chapter 7: Modelling and Results: Multi-Echelon Inventory Optimization](#chapter-7-modelling-and-results-multi-echelon-inventory-optimization)
- [Chapter 8: Deployment, Validation and Responsible AI](#chapter-8-deployment-validation-and-responsible-ai)
- [Chapter 9: Business Impact and Recommendations](#chapter-9-business-impact-and-recommendations)
- [Chapter 10: Limitations and Future Scope](#chapter-10-limitations-and-future-scope)
- [Chapter 11: Conclusion](#chapter-11-conclusion)
- [References](#references)
- [Appendices](#appendices)
- [About the Authors](#about-the-authors)

# List of Tables

| Table | Title |
|---|---|
| 3.1 | The Supply Brain dataset at a glance |
| 3.2 | Table register of the input dataset |
| 3.3 | Time split used throughout the project |
| 4.1 | Material assumptions, justification and effect on results |
| 4.2 | Tools and why they were chosen |
| 5.1 | ABC classification by revenue |
| 5.2 | Promotional lift by category |
| 5.3 | Forecast accuracy and bias by segment |
| 5.4 | Inventory, service and working capital by echelon (history) |
| 5.5 | Price elasticity by category (base and promotional) |
| 6.1 | Candidate segmentation feature register |
| 6.2 | Segmentation iterations, v2 to v5 |
| 6.3 | Clustering models and metrics considered |
| 6.4 | k-selection diagnostics |
| 6.5 | Segment portfolio and recommended service levels |
| 7.1 | Engineered demand and supply signals used by MEIO |
| 7.2 | Inventory models and metrics considered |
| 7.3 | Frozen single-week model comparison (2026 forward year) |
| 7.4 | Validation on replayed 2025 demand |
| 7.5 | Pooling-factor sweep on replayed 2025 demand (netting 0.0) |
| 7.6 | Five-seed robustness of α = 0.4 and α = 0.6 |
| 7.7 | Per-cluster own fill versus pooling factor (five seeds) |
| 7.8 | Deployed configuration versus baseline, 2026 forward year |
| 7.9 | Scenario results: SCENARIO_0004 and SCENARIO_0003 |
| 8.1 | Service equity across echelons, segments and clusters |
| 8.2 | Risk register |
| 8.3 | Monitoring plan |
| 9.1 | Recommendations and quantified impact |
| 9.2 | Implementation roadmap |

# List of Figures

| Figure | Title |
|---|---|
| 3.1 | Supply Brain network and fulfilment channels |
| 4.1 | End-to-end Supply Brain architecture |
| 5.1 | Revenue concentration (Pareto) |
| 5.2 | Revenue by banner |
| 5.3 | Price distribution by category |
| 5.4 | Seasonality by category |
| 5.5 | Distribution of weekly demand CV |
| 5.6 | Promotional discount depth |
| 5.7 | Forecast error distribution |
| 5.8 | Historical demand versus forward forecast |
| 5.9 | Days of supply by echelon |
| 5.10 | DC storage utilisation |
| 6.1 | Segmentation feature pipeline |
| 6.2 | Cluster separation in PCA space |
| 6.3 | Standardised feature centroids per cluster |
| 6.4 | Recommended versus economically implied service level |
| 6.5 | Baseline versus promo-lift forecast error by cluster |
| 7.1 | Frozen single-week model comparison |
| 7.2 | Clark-Scarf v2 versus actual on replayed 2025 demand |
| 7.3 | Pooling-factor sweep on replayed 2025 demand (netting 1.0 vintage) |
| 7.4 | Seasonal segment fill at peak versus trough |
| 7.5 | Price-inventory exchange rate by category |
| 7.6 | Elasticity by price tier |
| 8.1 | Streamlit dashboard (Supply Brain front end) |
| 8.2 | Simulation centre with the scenario agents |
| 8.3 | Global SHAP importance of cluster-assignment drivers |
| 8.4 | Cluster-by-feature SHAP heatmap |

# List of Abbreviations

| Abbreviation | Full Form |
|---|---|
| α (alpha) | Pooling factor: the share of an RDC's safety stock sized on pooled (echelon) demand |
| ABC | Revenue-based inventory classification (A = top 80%, B = next 15%, C = last 5%) |
| ARI | Adjusted Rand Index |
| CDC | Customer distribution centre |
| CPG | Consumer packaged goods |
| CR | Critical ratio (newsvendor) |
| CSL | Cycle service level |
| CV | Coefficient of variation |
| DC | Distribution centre |
| DDLT | Demand during lead time |
| DSD | Direct store delivery |
| EDA | Exploratory data analysis |
| F&B | Food and beverage |
| GSM | Guaranteed Service Model |
| LLM | Large language model |
| MEIO | Multi-echelon inventory optimization |
| OTD | On-time delivery |
| PCA | Principal component analysis |
| PDC | Plant distribution centre |
| pp | Percentage points |
| RDC | Regional distribution centre |
| (R, s, S) | Periodic-review policy: review period R, reorder point s, order-up-to level S |
| SE | Standard error |
| SHAP | SHapley Additive exPlanations |
| SKU | Stock-keeping unit |
| SS | Safety stock |
| WAPE / WMAPE | Weighted (mean) absolute percentage error |


---

# Abstract

Consumer packaged goods (CPG) manufacturers often hold too much of the wrong inventory and too little of the right inventory, because a single safety-stock rule and a blanket service target are applied across portfolios whose SKUs behave very differently. This project, sponsored by Accenture, builds and evaluates Supply Brain, a decision system that sets differentiated service levels and decides where in a four-echelon network buffer stock should sit. Working on a synthetic but structurally realistic US food and beverage network (500 SKUs, 27 storage locations, 156 weeks of history), we combine block-weighted k-means segmentation with stability-based model selection, a Clark-Scarf-style echelon inventory model with a tunable risk-pooling factor, a discrete-event digital twin that scores every policy from a common opening state, and LLM agents that convert planners' what-if questions into governed scenario runs. On the 2026 forward year, the deployed policy raises customer fill from 91.0% to 95.0% and customer-DC fill from 78.7% to 90.2%. It cuts lost-margin cost by $2.15M at a 0.5% rise in total landed cost, at the price of 17.6% more inventory value and weaker internal replenishment. Where buffers sit matters as much as how large they are.

**Keywords:** multi-echelon inventory optimization; SKU segmentation; differentiated service levels; supply-chain digital twin; consumer packaged goods; agentic AI

---

# Executive Summary

**The problem.** A mid-size US food and beverage manufacturer with about $104.5M of annual revenue fills 93.7% of the demand it ships from regional warehouses, but only 78.7% of the demand that flows through its customer-facing distribution centres, which serve convenience stores, smaller grocers and foodservice. It protects all 500 products with essentially the same safety-stock formula, sizes each warehouse as if no other existed, and never revisits the targets. Accenture's Supply Chain and Engineering practice meets this pattern across CPG clients: capital protects products that do not need it, while the products and customers that matter are no better protected than the rest.

**The objective.** We set out to answer one question: *where should inventory buffers sit, and how much protection should each product receive, when value, demand uncertainty, forecast quality and supply risk differ across the portfolio?* We also wanted a planner to be able to ask "what if?" in plain English and get a tested answer back.

**What we built.** No client data could be shared, so we built a realistic synthetic replica of the business: 500 SKUs, four plants, 23 distribution centres, ten retail customers and three years of weekly history. On top of it sit five connected layers:

1. **Segmentation** into six product families, each with its own service target: 97% for the core products that earn 93% of margin, 90–96% for the long tail.
2. **Multi-echelon optimization,** which decides how much stock each warehouse holds and how much a regional warehouse holds on behalf of the customer warehouses it feeds.
3. **A digital twin:** a day-by-day simulator of the network that tests every policy from the same starting position before anyone trusts it.
4. **Scenario agents** that turn a request such as "dairy demand falls 25% and shipping slows for six weeks" into a scenario, get a planner's approval, run it and explain the result.
5. **A planner front end** that brings these layers together in one application.

**What we found.**

- **Service rose most where it was weakest.** On the 2026 forward year, the optimized policy raises customer fill from 91.0% to 95.0%. Fill at the customer-facing DCs jumps from 78.7% to 90.2%. The share of weeks in which a location runs out of stock falls from 9.7% to 5.4%.
- **Lost sales fell by $2.15M with total cost almost flat.** Total landed cost rises only 0.5% ($0.19M). Almost all of that rise is the freight for roughly 568,000 extra units actually delivered to customers.
- **The win comes from timing and placement, not from more stock everywhere.** The old policy sizes stock on a flat annual average and collapses to 78% fill in the seasonal peak week. Weekly re-estimation lifts the peak to 89%, and pooling at regional warehouses closes most of the customer-DC gap.
- **There are real trade-offs.** Average inventory value rises 17.6% ($1.10M of working capital). Holding that stock costs only about $0.13M more a year, roughly 6% of the $2.15M lost margin recovered. Internal replenishment between warehouses becomes less reliable (78% versus 89%), and we have not fully fixed this.
- **The result holds up on past demand.** Re-planning 2025 with the same method beats the policy that actually ran that year in every one of five independent simulation runs, by +1.5 to +2.7 points of customer fill depending on the pooling setting.

**Recommendations.**

1. **Replace the blanket service target with the segment ladder:** 97% for the core families and 90–96% for the long tail.
2. **Adopt echelon-aware stocking with weekly re-estimation.** Pilot it first on the core products at two or three customer DCs, where the service gap is largest.
3. **Fix the forecast before buying stock for the forecast-challenged tail.** The 98 SKUs in the two forecast-challenged families have an average forecast error larger than their demand. For them, better forecasting is cheaper than more inventory.
4. **Add an upstream-health guard before scaling.** No parameter change should be adopted if internal replenishment falls below an agreed floor.
5. **Use price as the buffer only where it is cheaper than stock:** on our exploratory analysis, Frozen Foods.
6. **Route every disruption what-if through the agent and twin sandbox,** with a named planner approving each run.

**Expected impact and caveats.** The evidence supports about four points more customer fill and roughly $2M less lost margin a year, at broadly unchanged landed cost and about $1.1M more working capital. These figures come from a synthetic business and one validation year, and the exact deployed configuration has not yet been back-tested as a unit. The method and tooling transfer to a client; the figures must be re-derived on client data during the pilot.


---

# Chapter 1: Introduction

A planner at a customer distribution centre serving convenience stores and foodservice kitchens watches roughly one order in five go unfilled. Two hundred miles upstream, the regional warehouse that restocks the site carries safety stock sized for its own bulk customers and nobody else. Neither warehouse is managed badly by its own rules. The failure lies in the rules themselves, and it is the problem this project addresses.

## 1.1 Background and Context

Consumer packaged goods (CPG) food and beverage companies run demanding supply chains: margins are thin, assortments wide, promotions can double weekly volume, and retailers penalise missed deliveries. Yet most still apply one safety-stock formula across thousands of SKUs, set service targets once (often near 99%) and rarely revisit them, average lead-time variability away, and have no feedback loop from stockouts, write-offs and working capital back to the parameters that caused them.

Accenture's Supply Chain and Engineering AI and Data practice sees this pattern across client engagements and sponsored this capstone to test a different approach. It asked for *Supply Brain*, a closed-loop system that segments SKUs, sets differentiated policies per segment, decides where in a multi-echelon network to hold buffer stock, and lets planners interact with it in natural language rather than through static reports.

## 1.2 Problem Statement, Objectives and Significance

**Problem statement.** *Where should inventory buffers sit, and how much protection should each SKU receive, when value, demand uncertainty, forecast quality and replenishment risk differ across the portfolio?*

**Objectives.** We pursued five objectives, each answered in a later chapter:

1. **Segment** the portfolio into behaviourally distinct families using value, variability, forecastability and supply-risk evidence (Chapter 6).
2. **Differentiate** service-level targets by segment instead of applying one blanket target (Chapter 6).
3. **Position** buffers across the network, deciding both how much stock each echelon holds and how much protection a regional DC pools for the customer DCs it feeds (Chapter 7).
4. **Validate** every policy in a digital twin that shares its physics with the data-generating process, including disruption scenarios (Chapters 7 and 8).
5. **Expose** the system through a governed natural-language agent layer, so a planner can run what-if analyses with a human approval gate (Chapter 8).

**Significance.** For the sponsor, the value is threefold: service is protected on the SKUs and customers that drive revenue and margin; capital stops being tied up in buffers that protect nothing; and the sponsor gains a reusable framework, including the price-versus-inventory "exchange rate", to take to CPG clients.

## 1.3 Scope and Limitations

**In scope.** The analysis covers:

- A synthetic US CPG food and beverage manufacturer with 500 SKUs in five categories.
- A four-echelon network: 4 plants, 4 plant DCs, 8 regional DCs and 15 customer DCs.
- Ten B2B retail banners.
- 156 weeks of weekly history (2023–2025) and a 52-week forward year (2026).

The deliverables are a segmentation with service ladder, a multi-echelon inventory model, a digital twin, scenario agents and a planner front end.

**Out of scope.** The following are not covered:

- **Real client data.** The sponsor's data could not be shared, so the project is synthetic-first by design.
- **Perishability and the cold chain as binding constraints.** Shelf life is configured in the generator (`SHELF_LIFE_DAYS`), but by assumption every SKU is given three to four years. That is longer than the 156-week history, so stock never expires, write-offs are zero, and every facility is treated as ambient.
- **Lateral transshipment between regional DCs.**
- **Postponement strategies.**
- **Live integration with a client ERP or planning system.**

**Constraints.** Two further constraints shape the evidence:

- **One realisation of history.** Every real-demand result rests on one replayed validation year.
- **A twelve-week build window.** Compute and calendar time limited how many configurations could be tested over multiple seeds. We state these limits wherever they affect a number, and we gather them in Chapter 10.

## 1.4 Key Terminology

### 1.4.1 Multi-echelon inventory optimization (MEIO)

MEIO sets inventory policies for all stocking locations of a supply network jointly rather than one location at a time. It recognises that stock held upstream protects downstream locations too (Clark & Scarf, 1960; de Kok et al., 2018).

### 1.4.2 Echelon stock versus installation stock

Installation stock is what one location physically holds. Echelon stock is that location's stock plus all stock at or in transit to every location downstream of it. Echelon thinking is the basis for decomposing a multi-stage problem into tractable single-stage pieces (Clark & Scarf, 1960; Axsäter, 2015).

### 1.4.3 Cycle service level and fill rate

The cycle service level (CSL) is the probability of not running out during a replenishment cycle; the fill rate is the fraction of demand served immediately from stock. The two are related but not interchangeable (Silver et al., 2017). We set policy on CSL and judge outcomes on fill rate.

### 1.4.4 Safety stock and demand during lead time (DDLT)

Safety stock is inventory held above expected demand to absorb uncertainty in demand and lead time. It is sized from the distribution of demand during the replenishment lead time (Silver et al., 2017).

### 1.4.5 Risk pooling

Risk pooling is the reduction in total safety stock that comes from aggregating uncorrelated demand streams at a shared location: variability grows more slowly than the mean (Eppen, 1979). In this project a pooling factor, α, controls how far a regional DC relies on this effect.

### 1.4.6 Digital twin

A digital twin is a virtual replica of a physical system on which experiments can inform real decisions (Kritzinger et al., 2018; Ivanov & Dolgui, 2021). Ours is a day-by-day discrete-event simulation of the network.

### 1.4.7 (R, s, S) policy

Under an (R, s, S) policy, inventory is reviewed every R periods. If the inventory position is at or below the reorder point s, an order is placed to raise it to the order-up-to level S (Silver et al., 2017).

> **Key Takeaways**
> - The failure is structural: uniform buffer rules and single-location sizing leave the most exposed customers, those served from customer DCs, the least protected.
> - Success means higher service on the SKUs and customers that matter, without a proportional rise in capital or cost, delivered through tools a planner can question in plain English.


---

# Chapter 2: Review of Related Literature

Sixty years of inventory research have answered most questions about how much stock a single location should hold. The harder questions are *where* in a network stock should sit, how protection should differ across thousands of unlike products, and how to test a policy before trusting it. Theory and practice still diverge on these. This chapter synthesises the work that shaped Supply Brain, organised by theme. Each theme ends with what it means for this project.

## 2.1 Multi-echelon inventory theory: two schools

Modern multi-echelon theory begins with Clark and Scarf (1960). They showed that, for a serial system, an echelon base-stock policy is optimal, and that the multi-stage problem decomposes into single-stage problems once each stage is managed on its *echelon* inventory: its own stock plus everything downstream. Two years earlier, Simpson (1958) had taken a different route. Each stage quotes a guaranteed service time to its successor, and the optimal placement of safety stock is then "all or nothing" at each stage. These two papers founded what de Kok et al. (2018) call the stochastic-service and guaranteed-service schools. Their typology of several hundred multi-echelon papers shows the schools still running largely in parallel.

The guaranteed-service model (GSM) became the more tractable of the two. Graves and Willems (2000) extended it to spanning-tree networks solvable by dynamic programming, and Eruguz et al. (2016) survey its many extensions and industrial uses. GSM's tractability rests on an assumption: demand is bounded, and every stage always meets its quoted service time, using "extraordinary measures" if needed. The stochastic-service school (Axsäter, 2015; Zipkin, 2000) instead lets delays propagate when an upstream stage stocks out. It is harder to optimise, but closer to how a real network with weekly reviews and variable lead times behaves.

*For Supply Brain:* our network has stochastic lead times, weekly review and no guaranteed service times. We therefore expected the stochastic-service school to fit better and tested both. The result (§7.4) confirms the theory: a GSM policy sized on a single frozen week achieved only 48% customer fill once simulated, because its stock-or-no-stock placement left whole echelons unprotected.

## 2.2 Risk pooling, and MEIO in practice

Why hold stock upstream at all? Eppen (1979) answered this with the multi-location newsvendor. When demands are independent, centralising stock lets safety stock grow roughly with the square root of the number of locations rather than linearly. Pooling is valuable, but the full benefit assumes stock can reach wherever it is needed when it is needed.

Industrial deployments show both the value and the friction. Procter & Gamble rolled out single- and multi-echelon inventory tools across business units representing over 90% of revenue. It reported inventory reductions of roughly 3–7% at or above target service, and stressed that the gains depended on planners actually adopting the tools (Farasyn et al., 2011). Hewlett-Packard's guaranteed-service deployment reported more than $130M in savings (Billington et al., 2004), and IBM deployed a stochastic-service base-stock model with service constraints (Ettl et al., 2000). Cattani et al. (2011) offer a caution, however. On real data, common textbook simplifications such as arborescent networks, Poisson demand and single-echelon approximations can mislead.

*For Supply Brain:* pooling is the right instinct, but the degree of pooling should be a tuned parameter, not an assumption. That is the origin of our continuous pooling factor α and of our "netting discount". The netting discount removes the original model's assumption that a regional DC can count its customer DCs' stock as instantly available (§7.5). P&G's experience also justifies the effort we put into a planner-facing interface (Chapter 8).

## 2.3 SKU segmentation and differentiated service

Fisher (1997) argued that functional and innovative products need different supply chains: efficient for the former, responsive for the latter. Mismatch, he argued, is a leading cause of poor performance. Within inventory management, the classical tool for differentiation is ABC analysis on annual dollar usage. Flores and Whybark (1986) extended it to two criteria, such as value and criticality. Ramanathan (2006) generalised it with a weighted linear optimisation in which each SKU chooses the criterion weights most favourable to it. Teunter et al. (2010) showed that the conventional dollar-usage criterion is not cost-optimal. A criterion built from shortage cost, holding cost, demand and order quantity achieves the same service at lower inventory cost. Syntetos et al. (2005) added a demand-pattern lens, classifying series as smooth, erratic, intermittent or lumpy using inter-demand interval and CV² thresholds.

van Kampen et al. (2012) organise this literature into three linked choices: which characteristics to classify on, which technique to use, and how to turn classes into policy. They observe that many classifications stop at the first two and never connect to an operational decision.

*For Supply Brain:* we followed van Kampen et al.'s three steps explicitly. The characteristics are four balanced blocks: value, variability, forecastability and supply risk. The technique is clustering with stability-based model selection. The policy link is a service-level ladder that feeds the optimiser directly. Teunter et al. support our choice to let economics, not volume alone, drive the classes. The gap we address is that few published segmentations connect a segment to *network position* (how much pooling it gets) rather than to a single-location service level.

## 2.4 Clustering, validation and explainability

k-means (Lloyd, 1982), seeded with k-means++ (Arthur & Vassilvitskii, 2007), remains the workhorse for partitioning numeric feature spaces. Choosing the number of clusters is the persistent difficulty. The silhouette width (Rousseeuw, 1987) rewards compact, well-separated clusters, but operational data rarely produces them. Hennig (2007) argued instead for judging clusters by whether they reappear under resampling. The Adjusted Rand Index (Hubert & Arabie, 1985) provides a chance-corrected measure of agreement between two partitions with which to measure that.

Unsupervised segments must also be explained to the people who act on them. SHAP (Lundberg & Lee, 2017) attributes a model's output to its inputs using Shapley values. TreeExplainer computes those attributions exactly for tree ensembles such as XGBoost (Chen & Guestrin, 2016; Lundberg et al., 2020). Because k-means has no supervised output, a common practical route is to fit a surrogate classifier to the cluster labels and explain the surrogate. That is useful, but it is one step removed from the clustering itself.

*For Supply Brain:* we chose k by bootstrap ARI stability, with silhouette as a tie-breaker. Our silhouettes were low (about 0.17) at every k, and stability is the property a planner needs: a SKU should not change segment because of sampling noise. We explain segments with an XGBoost surrogate and Tree SHAP, and we report the surrogate's fidelity so readers can judge how far to trust the explanation (§8.2).

## 2.5 Service levels and the distribution of lead-time demand

The economic service level traces back to Arrow et al. (1951). Their single-period model yields the critical ratio of underage cost to underage-plus-overage cost that underlies the newsvendor literature reviewed by Khouja (1999). Silver et al. (2017) distinguish this cycle service level from the fill rate actually experienced by customers. The distinction matters: the two can diverge sharply, especially upstream.

Sizing safety stock to a target service level requires the distribution of demand during lead time. Burgin (1975) argued for the Gamma distribution, which is non-negative and skewed, as a better fit than the Normal for inventory demand. Tyworth and O'Neill (1997) provide the counterpoint. For fast-moving finished goods across seven industries, the Normal approximation was robust in cost and service terms. The Gamma's advantage concentrates in slow-moving, high-variability items.

*For Supply Brain:* our service levels sit at 90–97%, in the tail where distributional shape matters most, and our long tail is volatile (CV up to 0.8). We therefore size demand during lead time as a Gamma mixture over Gamma-distributed lead times. In line with Tyworth and O'Neill, we expect the gain over a Normal approximation to be concentrated in the long-tail clusters.

## 2.6 Forecast error, promotions and the bullwhip

Inventory policy is only as good as its demand signal. Hyndman and Koehler (2006) catalogue the weaknesses of percentage errors such as MAPE, which explode near zero demand. Kolassa and Schütz (2007) show that the MAD/Mean ratio, equivalent to WMAPE, is a volume-weighted average of absolute percentage errors that remains well behaved for intermittent series. In CPG, promotions are the dominant source of forecast difficulty. Trapero et al. (2015) find that SKU-level models including promotional variables can outperform both statistical baselines and judgmentally adjusted forecasts during promotions. Petropoulos et al. (2022) provide a comprehensive review of these methods and their evaluation. Upstream, Lee et al. (1997) explain why variability grows as orders pass through echelons. The causes are demand-signal processing, rationing, batching and price variation, which includes the very promotions that drive CPG volume.

*For Supply Brain:* we use WMAPE as our forecastability feature and separate baseline-week error from promotion-week error. One cluster turns out to be defined almost entirely by promotional forecast failure (§6.6). The bullwhip literature explains why our regional DCs need a variability uplift and warns that price-based buffering can itself amplify upstream variance.

## 2.7 Simulation, digital twins and learning policies

Kritzinger et al. (2018) distinguish three levels of integration: a *digital model* (no automatic data flow), a *digital shadow* (one-way flow from the physical system) and a true *digital twin* (two-way flow). Ivanov and Dolgui (2021) describe the digital supply-chain twin as a combination of data, simulation and optimisation used to manage disruption risk and resilience. Evaluating policies by simulation brings its own methodology: output analysis, replication and variance reduction (Law, 2015). Common random numbers, using the same random draws for each policy compared, sharpen paired comparisons, and Glasserman and Yao (1992) give conditions under which they are guaranteed to help. Where no closed form exists, simulation can itself be the optimisation oracle (Fu, 2002). For sequential decisions under uncertainty, multi-armed bandits offer a principled exploration–exploitation trade-off, from Thompson's (1933) posterior-sampling rule to modern treatments (Russo et al., 2018; Lattimore & Szepesvári, 2020).

*For Supply Brain:* in Kritzinger et al.'s terms our engine is a *digital model*. It is not yet coupled to live operations, and we use the word "twin" in the looser industry sense. We score every policy from a common opening state with common random numbers. We tested random-search simulation optimisation and Thompson-style bandits as alternatives (§7.4), and we use multi-seed paired tests for parameter adoption (§4.2).

## 2.8 Price as a buffer

Inventory is not the only buffer against uncertainty. Federgruen and Heching (1999) show that when price and replenishment are set jointly under price-dependent stochastic demand, a base-stock list-price policy is optimal. Chen and Simchi-Levi (2004) extend this to fixed ordering costs. Gallego and van Ryzin (1994) characterise dynamic pricing of a fixed stock. Bijmolt et al. (2005), in a meta-analysis of price elasticities, report an average around −2.6, varying systematically by category and context. Postponement offers a third buffer: delaying product differentiation lets common stock serve several end products (Lee & Tang, 1997).

*For Supply Brain:* this literature underpins the charter's "exchange rate", the cost of buffering a segment with price relative to buffering it with stock. Our estimated category elasticities (−1.1 to −3.8) sit within Bijmolt et al.'s range. We present the exchange rate as exploratory (§7.8) and did not build postponement (Chapter 10).

## 2.9 Language models as an interface to optimisation

Large language models can reason step by step when prompted to (Wei et al., 2022) and interleave reasoning with tool calls (Yao et al., 2023). In supply chains, OptiGuide uses an LLM to translate a planner's what-if question into a change to an optimisation model. A trusted solver computes the answer, and proprietary data never passes through the model (Li et al., 2023). Because such systems influence consequential decisions, they fall within risk-management frameworks such as NIST's AI RMF, which calls for organisations to govern, map, measure and manage AI risk, with meaningful human oversight (National Institute of Standards and Technology, 2023). Finally, synthetic data, which we rely on throughout, is increasingly accepted as a way to develop analytics when real data is confidential, provided its generating assumptions are explicit and its limits respected (Jordon et al., 2022).

*For Supply Brain:* our agents follow the OptiGuide pattern. The LLM writes scenario *overrides*; the verified optimiser and twin compute the outcome; and a named human approves each run. This maps directly onto the NIST functions (§8.2).

## 2.10 The gap this project addresses

The literature is rich in each component but thin on their combination. Segmentation studies rarely carry their classes into network positioning. MEIO studies rarely tune the degree of pooling by segment, or validate it in a simulator that shares physics with the data. Digital-twin studies rarely expose their what-if capability through a governed natural-language interface. Supply Brain's contribution is to join these four strands in one loop and to measure, honestly, where the joined system helps and where it does not:

1. Multi-criteria, stability-selected segmentation.
2. Echelon positioning with a tuned, segment-specific pooling factor.
3. Twin-validated policy and scenario evaluation.
4. A human-gated agent layer.

> **Key Takeaways**
> - The literature agrees that differentiated service and echelon-aware positioning beat uniform, single-location rules, and that pooling's value depends on whether stock can actually move where it is needed.
> - The gap is integration: connecting segments to network position, tuning pooling by segment, validating in a shared-physics simulator, and governing what-if analysis through a human-in-the-loop language interface.


---

# Chapter 3: Business Context and Data

A buffer policy is only as good as the picture of the business it is built on. This chapter states the problem in the sponsor's operational terms, describes the dataset we built to stand in for client data, and is candid about the biases that dataset carries, because those biases favour our own results.

## 3.1 Business / Research Problem

**Who is affected.** Four groups live with the consequences of the current buffer policy:

- **Demand and supply planners** set and override stocking parameters every week.
- **The inventory or S&OP lead** answers for service and working capital together.
- **Finance** sees inventory as trapped capital.
- **Account teams** for direct-store-delivery (DSD) customers (convenience chains, smaller grocers and foodservice operators) absorb the complaints when shelves go empty.

**What decision this informs.** The operational decision is the stocking policy for every SKU at every stocking location: a safety stock, a reorder point *s* and an order-up-to level *S*, reviewed weekly. Everything else in Supply Brain, from segmentation to scenario agents, exists to set those three numbers better. The policy file is the only thing an optimizer changes. That makes it a natural integration point with a client's existing planning system.

**What success looks like.** In the sponsor's terms, success has four parts:

1. Service on the SKUs and customers that drive revenue and margin rises, or at least holds.
2. The service gap between bulk customers (served from regional DCs) and DSD customers (served from customer DCs) narrows.
3. Total landed cost and working capital do not rise out of proportion to the service gained.
4. A planner can understand, question and override any recommendation.

The charter also named write-off reduction and a price-versus-inventory "exchange rate". We address both, with the limits the data imposes, in Chapters 7 and 10.

## 3.2 Data Available

**Why synthetic.** The sponsor could not share client data within the project's timeline and confidentiality constraints. The charter therefore committed to a *synthetic-first* strategy: build a realistic replica of a CPG food and beverage business so that segmentation, optimization, simulation and agent work could proceed in parallel, with client validation deferred to a later phase. Synthetic data is a recognised approach when real operational data is confidential or scarce, provided its generating assumptions are documented and its limits respected (Jordon et al., 2022).

**Grounding.** The demand side (per-unit prices, per-group price elasticity, seasonality shape and roughly 80/20 revenue concentration) is calibrated to an external real food and beverage sales dataset, with the 2020 COVID spike excluded. Supply-side parameters are modelled assumptions, calibrated to published industry ranges. These include costs, holding rates, lead times and capacities. The generator (`supply_brain_generator_v5.py`) writes every table, validates referential integrity and money balances, and documents itself in `input datasets/DATASET_DOCUMENTATION.md` in the same run.

**Table 3.1: The Supply Brain dataset at a glance**

| Dimension | Value |
|---|---|
| Portfolio | 500 SKUs; 5 categories (Beverages, Snacks & Confectionery, Dairy & Bakery, Frozen Foods, Grocery); 25 subcategories; 15 brands in three price tiers |
| Network | 4 plants → 4 plant DCs → 8 regional DCs → 15 customer DCs; 76 lanes (truck and rail) across 20 US metros |
| Customers | 10 B2B retail banners in three channel types (retail, brick-and-mortar, foodservice) |
| History | 156 weeks of actual demand, 2023-W01 to 2025-W52 |
| Forward horizon | 52 weeks, 2026-W01 to 2026-W52 |
| Order lines | 738,833 day-level lines in 9,454 baskets |
| Demand | 34.1M units over the history; 12.1M units in 2025 |
| Revenue and margin | $291.6M over the history ($104.5M in 2025); gross margin 42.0% |
| Working capital | Average inventory value about $5.96M; about 8.8 annual turns |

Source: `input datasets/DATASET_DOCUMENTATION.md`, Part 7.0 and 7.14; synthetic Supply Brain dataset (generator v5); team analysis.

**Figure 3.1: Supply Brain network and fulfilment channels**

```text
  PLANT (x4) ──0.5 d push──► PLANT DC (x4) ──truck / rail──► REGIONAL DC (x8) ──truck──► CUSTOMER DC (x15)
  production only           first stocking echelon        weekly (R, s, S) review         weekly (R, s, S) review
                                                                  │                                 │
                                                                  ▼                                 ▼
                                                   BULK channel: mass, club,          DSD channel: convenience,
                                                   large grocery (warehouse-          smaller grocery, foodservice
                                                   delivered)                         (direct store delivery)
```

*Figure 3.1: Supply Brain network and fulfilment channels.* Source: `input datasets/DATASET_DOCUMENTATION.md`, §2.3 and Part 7.12; team analysis.

**How the network works.** Plants produce only and hold no stock. Each plant pushes finished goods into its own on-site plant DC (PDC). Regional DCs (RDCs) buy each SKU from the nearest PDC whose plant is qualified to make it, so a single RDC can draw on lanes ranging from half a day to nearly thirteen days. Each customer DC (CDC) is replenished from one RDC. About a third of SKUs, the high-volume ones, are dual-sourced across two plants. Plants run at 52–56% of capacity on average, with line-level capacity clipping about 3% of SKU-weeks, and schedule attainment averages about 95%.

**Commercial structure.** List price is national. Customer differentiation happens below list through trade spend (off-invoice allowances, trade funds, volume rebates and logistics allowances), so the price actually realised (the *pocket price*) ranges from about 80% to 95% of list. That is consistent with US pricing practice under the Robinson-Patman Act. Three commercial postures are modelled: everyday-low-cost (mass and club), high-low (conventional grocery) and contract (foodservice). Promotions run on 18.5% of order lines at an average depth of 17.4%.

**Table 3.2: Table register of the input dataset**

| Group | Tables (rows) | Grain |
|---|---|---|
| Network masters | `plant_master` (4), `plant_dc_master` (4), `regional_dc_master` (8), `customer_dc_master` (15), `network_lanes` (76), `plant_capability`, `sku_plant_sourcing`, `sku_sourcing_map` | Node, lane, SKU × plant |
| Product and economics | `sku_master` (500), `cost_parameters` (675), `price_plan`, `seasonality_master` | SKU; DC × subcategory |
| Customers | `b2b_customer_master` (10), `b2b_customer_terms`, `customer_serving_dc` (24), `sku_customer_service_targets` (3) | Banner; banner × DC |
| Commercial history | `orders` (738,833), `promotions` (19,036), `regional_demand_index` | Day-level order line; promotion event |
| Forecast | `demand_forecast` (777,088) | Week × SKU × DC × customer; 2025 in-sample + 2026 forward |
| Operations | `production` (102,648), `inventory` (1,211,808), `replenishment_receipts`, `write_offs` (empty by design) | Week × plant × SKU; week × DC × SKU |
| Policy and scenarios | `baseline_policy` (7,768), `scenarios` (30) | DC × SKU; disruption event |

Source: `input datasets/DATASET_DOCUMENTATION.md`, Part 7.1; `SCHEMA.md`; team analysis.

**Table 3.3: Time split used throughout the project**

| Window | Weeks | Role |
|---|---|---|
| 2023-W01 to 2024-W52 | 104 | Burn-in: lets the simulated network warm up from a cold start (week-1 customer fill is 22%) |
| 2025-W01 to 2025-W52 | 52 | Evaluated history: the year used for forecast accuracy, segmentation forecastability features and real-demand validation |
| 2026-W01 to 2026-W52 | 52 | Forward horizon: forecast only; every forward policy comparison starts from the same opening position at the end of 2025 |

Source: `SCHEMA.md`; `README.md` ("Data lifecycle"); team analysis.

### Notable features in the data

Five features of the data shape every later choice:

1. **Steep concentration.** The top 20% of SKUs earn 84.2% of revenue, and Beverages alone earn 51%.
2. **Two forecasts, both biased low.** The published forecast under-calls demand by 7.8% over the evaluated year, and the bias is negative in every segment and category.
3. **A large echelon service gap.** Historical customer fill is 92.7% overall, but that blends 95.7% at RDCs with 79.7% at CDCs.
4. **Nearly constant on-time delivery** across SKUs, because every node's promised lead time is set at its own 90th percentile.
5. **A 30-event disruption library.** It covers demand surges, forecast misses, geographic disruptions, lane outages, lead-time spikes and plant shortfalls, each with time-to-recover and time-to-survive estimates. These become the raw material for scenario testing.

**What was excluded, and why.** Four exclusions matter:

- **Perishability.** Shelf life runs from 1,095 to 1,459 days against a 1,092-day history, so it can never bind, and `write_offs` is empty by design.
- **Lateral and failover lanes.** The 25 lateral RDC-to-RDC and failover RDC-to-CDC lanes are declared but carry no simulated flow.
- **Customer segmentation.** Ten banners are too few for it.
- **The baseline policy's own segment labels.** We treat the dataset's three behavioural segments (Fast-Moving, Seasonal, Intermediate) as descriptive only; our own segmentation replaces them as the driver of service levels.

## 3.3 Data Governance and Ethics

**PII and approvals.** The dataset contains no personal data, no client data and no real company figures. Banner names, metros and SKU identifiers are fictitious. The code and data sit in a private Git repository accessible only to the team, the faculty mentor and sponsor stakeholders, as agreed in the charter. Large tables are stored through Git LFS.

**Known biases.** Because the data is synthetic, the biases that matter are biases of *design*, not of collection, and they lean in our favour:

- **Shared structure.** The generator, the published forecast, our segmentation features and the digital twin all share one structural view of the world. "Real replayed 2025 demand" means the generator's own history, and "actual" outcomes mean the twin replaying the dataset's static baseline policy, not observed results.
- **A deliberately mediocre baseline.** The baseline policy was tuned to customer fill of 90–96%. Its regional-DC rows are sized on direct bulk demand only, so 358 RDC–SKU pairs with downstream demand have no policy row at all. That leaves room for any optimizer to improve.
- **Forecast look-ahead.** The forecast's promotional lift and customer split are fitted on all 156 weeks, so its 2025 accuracy is slightly optimistic.

We document these in the project's assumptions audit (`ASSUMPTIONS_AUDIT.md`) and carry them into our interpretation in Chapters 7 and 10. A reader should discount the size of our improvements accordingly. The *direction* of the findings is more robust than their magnitude.

> **Key Takeaways**
> - Every table in this project ultimately informs one decision: how much stock to hold of each SKU at each location, and therefore where in the network buffers sit.
> - The dataset is synthetic and structurally realistic, but its design biases (a weak baseline and shared structure between generator and twin) flatter any optimizer, so results are best read for direction rather than exact size.


---

# Chapter 4: Methodology: Assumptions and Approach

Every inventory model is a bundle of assumptions about how demand, lead time and cost behave. The value of the model depends on whether those assumptions are stated, justified and tested. This chapter sets out the assumptions that most affect our results, then the analytical approach and tools, and why we chose them over the alternatives.

## 4.1 Assumptions

We audited the generator, segmentation, MEIO model and digital twin line by line (`ASSUMPTIONS_AUDIT.md`). Table 4.1 lists the twelve assumptions with the largest bearing on our conclusions. For each, it gives the scientific justification, the effect on results, and where we tested or disclosed it.

**Table 4.1: Material assumptions, justification and effect on results**

| # | Assumption | Justification | Effect on results | Where tested or disclosed |
|---|---|---|---|---|
| A1 | Weekly demand at each (DC, SKU) is independent across weeks and locations. Its uncertainty is the coefficient of variation (CV) of actuals: the mean of a 13-week and a 52-week trailing CV, capped at 3.0. | Standard in periodic-review safety-stock theory (Silver et al., 2017). Blending two windows avoids understating annual volatility with a short window alone. | Seasonality, promotions and trend are partly counted as noise, which inflates σ. Uncertainty does not grow with forecast horizon. | §7.1; Chapter 10 |
| A2 | Demand during lead time follows a Gamma mixture: Gamma demand per week, Gamma lead time. | Gamma is non-negative and right-skewed, so it prices the 95–99% percentiles our service levels sit at better than a Normal (Burgin, 1975; Tyworth & O'Neill, 1997). | Reorder points at high CSL are less biased than a Normal approximation would give. | §7.1, §7.3 |
| A3 | CDC and RDC lead time is the cumulative plant-to-node lead time along the fastest lane, with variances summed. | It makes each node's protection interval explicit and conservative. | The twin replenishes each hop on its own lane, so model and twin disagree slightly, and CDC buffers may be inflated. | Chapter 10 |
| A4 | Service targets come from a newsvendor critical ratio, overridden by revenue-share floors and caps: ≥30% of revenue → 97%, ≤3% → 90%, otherwise the cluster median ratio clipped to [0.85, 0.995]. | The newsvendor ratio is the classical economic service level (Arrow et al., 1951; Khouja, 1999). Floors and caps encode strategic priority. | Synthetic costs make the economic ratios nearly flat (0.950–0.957), so the ladder is mostly strategic. One cluster lands at 95.7% because its revenue share (3.004%) sits just above the 3% cap. | §6.6 |
| A5 | Carrying rate is 10.2% a year (9% WACC + 1.2%) for every SKU. Unit cost is the same at every echelon. | Conservative capital-cost view used by the generator. | Industry ranges are 20–30% a year. Cheap inventory favours buffering and blunts any holding-cost advantage of holding stock upstream. | Chapter 10 |
| A6 | Transport cost is linear per unit by lane, and plant-to-PDC freight is excluded. | Simple and auditable. | Transport is 85–91% of total landed cost, so cost comparisons are mostly a transport-volume signal: serving more demand looks "more expensive". | §7.6, Chapter 9 |
| A7 | Lead-time features in segmentation come from static network parameters. | These were the only lead-time fields available at SKU × DC grain. | The SUPPLY_RISK block partly encodes a SKU's DC mix (geography) rather than realised supply variability. | §6.1, §8.2 |
| A8 | The same year (2025) is used to tune parameters and to score them. The seasonal index used for 2025 is built from all 156 weeks. | Only 2025 has both actuals and a published forecast. | There is look-ahead leakage and overfitting risk, so the real-demand gains are optimistic. | §7.5, Chapter 10 |
| A9 | Simulation seeds vary lead-time, production and Monte Carlo noise, but not demand. A parameter is adopted only if its fill gain exceeds two standard errors over at least five paired seeds and its cost rise does not. | Common random numbers sharpen paired comparisons (Law, 2015). | The standard errors measure simulator noise only, not sampling uncertainty in demand, so "validated" cannot detect overfitting to 2025. | §7.5 |
| A10 | Per-cluster pooling factors were each tuned with the other clusters held at α = 0.6, then adopted together. | Sweeping every combination jointly was too costly within the project window. | Interactions through shared RDC and PDC stock are ignored. Four of six winners sit on the grid edge (α = 1.0). | §7.5, Chapter 10 |
| A11 | The twin releases parent stock only above the parent's own safety stock. Plant allocation shares are applied to an already-split demand signal. | This mirrors common "protect own demand first" practice. | It penalises upstream buffers and probably contributes to the drop in internal replenishment fill. | §7.6, §8.2 |
| A12 | Shelf life never binds and every facility is ambient. | It keeps the first build tractable. | Write-offs cannot be measured, and the cold chain, the defining F&B constraint, is absent. | Chapter 10 |

Source: `ASSUMPTIONS_AUDIT.md`, §0 and §§1–5; team analysis.

**The assumption most likely to affect the results** is the combination of A8 and the baseline design described in §3.3. The baseline is weak by construction, and parameters were tuned and scored on the same year. Together these mean our headline improvements are an *upper* estimate. We therefore report the deployed configuration's forward-year result alongside multi-seed real-demand validation (Chapter 7), and we treat the direction of the findings as more reliable than their magnitude.

## 4.2 Approach and Tools

### 4.2.1 End-to-end design

Supply Brain is built as a pipeline of separable layers that share one dataset and one physics engine (Figure 4.1).

**Figure 4.1: End-to-end Supply Brain architecture**

```text
 ┌──────────────────────────┐   ┌───────────────────────────┐   ┌──────────────────────────────┐
 │ 1. Synthetic world       │──►│ 2. Segmentation (v5)      │──►│ 3. Service ladder            │
 │ supply_brain_generator_v5│   │ 8 features, 4 blocks,     │   │ newsvendor CR + revenue      │
 │ 26 tables, 156 + 52 wks  │   │ k-means, k by stability   │   │ floors/caps → z per SKU      │
 └────────────┬─────────────┘   │ + XGBoost/SHAP surrogate  │   └──────────────┬───────────────┘
              │                 └───────────────────────────┘                  │
              ▼                                                                ▼
 ┌──────────────────────────┐   ┌───────────────────────────┐   ┌──────────────────────────────┐
 │ 6. Digital twin          │◄──│ 5. MEIO: Clark-Scarf v2   │◄──│ 4. Parameter resolver        │
 │ daily discrete-event sim,│   │ weekly rolling (R, s, S), │   │ meio_parameters.py: evidence │
 │ same opening state,      │   │ pooling α, netting, Gamma │   │ → α, netting, per-cluster α; │
 │ common random numbers    │   │ DDLT                      │   │ SHA-256 receipt              │
 └────────────┬─────────────┘   └─────────────▲─────────────┘   └──────────────────────────────┘
              │                               │
              ▼                               │ overrides (19 measures) applied on plan and actual sides
 ┌──────────────────────────┐   ┌─────────────┴─────────────┐
 │ 8. Streamlit front end   │◄─►│ 7. LangGraph agents       │
 │ dashboard, segments,     │   │ orchestrator + specialists│
 │ service, MEIO, sims      │   │ + validator + analyst;    │
 └──────────────────────────┘   │ human approval gate       │
                                └───────────────────────────┘
```

*Figure 4.1: End-to-end Supply Brain architecture.* Source: `README.md`; `Agents/README.md`; team analysis.

**Why a shared physics engine.** The same discrete-event twin generated the historical inventory record and scores every candidate policy. If the history and the scoring came from different simulators, a measured improvement could reflect the mismatch rather than the policy. Using one engine is standard practice in simulation-based policy evaluation (Fu, 2002; Law, 2015). It has a cost, though: it also lets structural assumptions flow straight from generator to evaluation (§3.3).

### 4.2.2 Evaluation protocol

Every policy, whether the baseline, an alternative model or the final model, is judged the same way:

1. **Same opening state.** Each run starts from `output datasets/history/opening_position.csv`, the network position at the end of 2025, so differences measure the policy decision alone.
2. **Same demand.** Demand, lead-time and production draws use independent random streams. Changing the policy therefore never changes the demand draw (common random numbers; Law, 2015).
3. **Rolling, walk-forward sizing.** The final model re-estimates demand level, bias and CV every week using only information available before that week. It outputs a 52-week time-varying policy.
4. **Two evidence bases.** (a) *Real-demand validation:* the policy is re-built week by week through 2025 and compared with the twin replaying 2025 under the dataset's own baseline policy. (b) *Forward evaluation:* the deployed configuration is compared with the baseline over 2026.
5. **Multi-seed paired testing.** Candidate parameters are compared across five paired seeds. A parameter moves off its reference value only if the fill gain exceeds two standard errors and the cost increase does not.
6. **Receipts.** Every policy build writes `meio_parameters.json`, which records each parameter decision and the SHA-256 hash of every evidence file used.

**Metrics.** We judge policies on:

- Unit customer fill rate, overall and split by CDC and RDC.
- Internal replenishment fill, as a guard on upstream health.
- The share of demand-weeks filled in full.
- The share of location-weeks at zero stock.
- Holding, stockout (lost margin) and transport cost, and their sum, total landed cost.
- Average inventory value and safety-stock value (working capital).

We deliberately do *not* use forecast-accuracy or classification metrics to judge the final policy. This is a decision problem, so the right yardstick is the outcome of the decision.

### 4.2.3 Tools and technologies

**Table 4.2: Tools and why they were chosen**

| Need | Tool | Why this rather than the alternative |
|---|---|---|
| Data generation and analysis | Python 3.11, NumPy, pandas, SciPy | Transparent, reproducible and widely known. The whole world is regenerated from one script with fixed seeds. |
| Large panels (1.2M inventory rows; weekly policies for about 7,500 DC–SKU pairs × 52 weeks) | Polars, Parquet | Columnar and multi-threaded. The optimized pipeline ran about ten times faster than the pandas original, which made five-seed sweeps feasible. |
| Clustering and validation | scikit-learn (k-means, StandardScaler, silhouette, ARI) | Mature, well-documented implementations of k-means++ (Arthur & Vassilvitskii, 2007) and of the validation indices we needed. |
| Explainability | XGBoost with native Tree SHAP | Tree SHAP gives exact, fast Shapley attributions for tree ensembles (Chen & Guestrin, 2016; Lundberg et al., 2020). |
| Inventory optimization | Custom Clark-Scarf v2 implementation | No open-source package combined continuous pooling, Gamma DDLT, weekly rolling and per-SKU overrides against our data contracts. |
| Simulation | Custom discrete-event digital twin (pandas original; Polars port) | Commercial supply-chain simulators could not share an engine with the data generator, which our evaluation design requires. |
| Agents | LangGraph, with Anthropic and NVIDIA model profiles | LangGraph supports explicit, inspectable agent graphs and a first-class human-approval *interrupt*. That is essential for a system that changes stocking policy. |
| Front end | Streamlit, Altair | Fast to build, Python-native, and good enough for a planner-facing prototype. |
| Version control | Git, Git LFS | Reproducibility, and LFS storage for the two largest tables. |

Source: `README.md`; `models/MEIO/MEIO_Weekly/final_submission_optimized/OPTIMIZATION.md`; `Agents/README.md`; team analysis.

**Alternatives we considered and set aside.**

- **A pure analytical MEIO solution with no simulation.** This would have been faster, but it could not reveal the interaction effects (hold-back rules, weekly review, rounding of lead times) that turned out to dominate results. §7.4 shows that every model sized analytically on a single frozen week underperformed the baseline once simulated.
- **Reinforcement learning over the full policy space.** It was rejected for this phase. The action space (about 7,500 DC–SKU pairs × three parameters) is combinatorially large, and feedback arrives in weekly batches. We explored a bounded version, a multi-armed bandit over service-level tiers, and report it as an alternative model.
- **An LLM that sets stocking parameters directly.** It was rejected on governance grounds. The agents author *scenario overrides*, and a verified optimizer and twin compute the policy. This follows the "LLM as translator and orchestrator over trusted solvers" pattern (Li et al., 2023).

> **Key Takeaways**
> - The assumption most likely to affect results is that the baseline is weak by construction and that parameters were tuned and scored on the same year. Our improvements are therefore an upper estimate, and their direction is more reliable than their size.
> - Every policy is judged in one shared twin, from one opening state, with common random numbers and multi-seed paired tests. Comparisons isolate the decision, not noise or starting position.


---

# Chapter 5: Exploratory Data Analysis

Before building any model, we asked a simple question of the data: where, and when, does this network fail its customers? The answer shapes everything that follows. Failure is not spread evenly. It concentrates in particular products, at particular echelons and in particular weeks. This chapter documents those patterns, one observation per section, and links each to the modelling choice it motivated. All figures are from the synthetic dataset's own data profile, regenerated in the same run as the data.

## 5.1 Revenue is steeply concentrated

The portfolio follows a classic Pareto pattern. The top 20% of SKUs earn 84.2% of revenue. A strict ABC split puts 77 SKUs (15.4%) in class A with 79.9% of revenue, and 283 SKUs (56.6%) in class C with just 5.0% (Table 5.1, Figure 5.1).

**Table 5.1: ABC classification by revenue**

| Class | Definition | SKUs | % of SKUs | % of revenue |
|---|---|---|---|---|
| A | Top 80% of revenue | 77 | 15.4% | 79.9% |
| B | Next 15% | 140 | 28.0% | 15.1% |
| C | Last 5% | 283 | 56.6% | 5.0% |

Source: `input datasets/DATASET_DOCUMENTATION.md`, §7.4; synthetic Supply Brain dataset (generator v5); team analysis.

![Figure 5.1: Revenue concentration (Pareto)](<../input datasets/report_assets/abc_pareto.png>)

*Figure 5.1: Revenue concentration (Pareto).*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/abc_pareto.png`

**So what.** A uniform service target spends the same protection on a C item earning a few hundred dollars a year as on an A item earning millions. Concentration is the single strongest argument for differentiated service, and the reason the VALUE block carries a quarter of the segmentation distance (Chapter 6).

## 5.2 Category and customer mix

Beverages account for 51.1% of revenue on only 15% of units. Snacks & Confectionery is the reverse: the most units (8.4M) but 7.9% of revenue. List prices span more than two orders of magnitude, from $0.40 to $252 per unit, with the widest spread in Beverages (Figure 5.3). Four banners, MegaMart Stores, ValueClub Warehouse, GreenGrocer National and BullsEye Retail, generate 81% of revenue (Figure 5.2). The bulk (warehouse-delivered) channel carries 81% of units and revenue, but DSD accounts for 47% of order lines, because DSD orders are small and frequent.

![Figure 5.2: Revenue by banner](<../input datasets/report_assets/revenue_by_banner.png>)

*Figure 5.2: Revenue by banner.*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/revenue_by_banner.png`

![Figure 5.3: Price distribution by category](<../input datasets/report_assets/price_by_category.png>)

*Figure 5.3: Price distribution by category.*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/price_by_category.png`

**So what.** Unit volume and economic value point in different directions. A volume-based classification would over-protect Snacks and under-protect Beverages. The DSD channel's many small orders make it operationally exposed out of proportion to its revenue, a theme that returns in §5.8.

## 5.3 Seasonality is strong and category-specific

Weekly demand shows a pronounced fourth-quarter peak every year in every category, sharpest in Snacks & Confectionery (above 100,000 units in late 2024) and Dairy & Bakery (Figure 5.4). At network level, weekly demand in 2025 rises from about 200,000 units mid-year to about 340,000 units at the year-end peak, and the 2026 forecast repeats the shape at a higher level (Figure 5.8). The dataset labels 150 SKUs (30%) as Seasonal.

![Figure 5.4: Seasonality by category](<../input datasets/report_assets/seasonality_by_category.png>)

*Figure 5.4: Weekly demand by category, 2023–2025.*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/seasonality_by_category.png`

**So what.** The baseline policy sizes every SKU on one flat average over all 156 weeks. Before any modelling, that predicts systematic under-protection at the peak and over-protection in the trough. The final model therefore re-estimates demand for each target week using a de-seasonalised level and a subcategory seasonal index (§7.1). The effect is measured at the peak week in §7.5.

## 5.4 Demand variability and intermittency

Weekly demand CV averages 0.60 for Fast-Moving SKUs, 0.67 for Seasonal and 0.68 for Intermediate. Intermediate SKUs have zero-demand weeks 10.3% of the time, against 2.6% for Fast-Moving (Figure 5.5). Variability measured at customer grain is inflated by order batching. Variability measured on aggregated DC-week or network-week series is lower.

![Figure 5.5: Distribution of weekly demand CV](<../input datasets/report_assets/demand_cv_hist.png>)

*Figure 5.5: Distribution of weekly demand coefficient of variation.*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/demand_cv_hist.png`

**So what.** Variability is high enough everywhere that safety stock is material, and different enough across SKUs to justify a VARIABILITY block in segmentation. Because batching inflates customer-grain CV, our segmentation measures base-demand CV on network-week totals (§6.1), and the MEIO model measures it on DC-week totals.

## 5.5 Promotions move volume, and forecast error with it

There are 19,036 promotion events, split evenly between banner-specific and all-banner events. They touch 18.5% of order lines at an average depth of 17.4% (Figure 5.6). Lift varies widely by category (Table 5.2).

**Table 5.2: Promotional lift by category (average line quantity on promotion ÷ off promotion)**

| Category | Base quantity | On-promo quantity | Lift |
|---|---|---|---|
| Beverages | 34.3 | 46.1 | 1.35× |
| Grocery | 37.9 | 66.8 | 1.76× |
| Dairy & Bakery | 35.6 | 71.9 | 2.02× |
| Frozen Foods | 44.3 | 93.9 | 2.12× |
| Snacks & Confectionery | 45.2 | 100.2 | 2.22× |

Source: `input datasets/DATASET_DOCUMENTATION.md`, §7.7; team analysis.

![Figure 5.6: Promotional discount depth](<../input datasets/report_assets/discount_depth.png>)

*Figure 5.6: Distribution of promotional discount depth.*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/discount_depth.png`

**So what.** A SKU that sells a third of its volume on promotion faces a different planning problem from one that never promotes, even at the same average volume. We include promotional volume share as a VARIABILITY feature, and measure forecast error separately for promotion weeks (§6.1).

## 5.6 The forecast is noisy and biased low

The published forecast's weighted absolute percentage error (WAPE) is 34.5% for Fast-Moving SKUs and around 40% for Seasonal and Intermediate SKUs (Table 5.3). Error at this grain is inflated by sparse customer-level series, a known weakness of percentage-based error measures near zero demand (Hyndman & Koehler, 2006). More important for inventory, the bias is negative in every segment and every category: the forecast under-calls demand by 7.8% over the evaluated year. The error distribution has large spikes at −100% (forecast zero, demand positive) and at +100% (Figure 5.7).

**Table 5.3: Forecast accuracy and bias by segment (2025, in-sample)**

| Segment | MAPE | WAPE | Bias |
|---|---|---|---|
| Fast-Moving | 42.1% | 34.5% | −7.7% |
| Intermediate | 54.0% | 38.6% | −16.3% |
| Seasonal | 51.9% | 40.8% | −15.0% |

Source: `input datasets/DATASET_DOCUMENTATION.md`, §7.9; team analysis.

![Figure 5.7: Forecast error distribution](<../input datasets/report_assets/forecast_error_hist.png>)

*Figure 5.7: In-sample forecast error, (forecast − actual) ÷ actual, clipped to ±100%.*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/forecast_error_hist.png`

![Figure 5.8: Historical demand versus forward forecast](<../input datasets/report_assets/hist_vs_forward.png>)

*Figure 5.8: Weekly network demand, 2023–2025 actual versus 2026 forecast.*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/hist_vs_forward.png`

**So what.** Safety stock buffers *random* error, not *systematic* bias. A forecast that is consistently 8–16% low will produce stockouts no matter how the buffer is sized. The final model multiplies each week's forecast by a trailing 13-week actual-to-forecast ratio before sizing anything (§7.1). Forecastability also enters segmentation as its own block, because some SKUs need better forecasts more than they need more stock.

## 5.7 Lead times and on-time delivery are nearly uniform across SKUs

Lane lead times vary a great deal by mode and distance. Plant-DC-to-regional-DC rail lanes average 8.9 days against 3.6 days by truck. But because every node's promised lead time is set at its own 90th percentile, on-time delivery is almost the same for every SKU: its CV across SKUs is just 0.016. The lead-time features available at SKU grain come from static network parameters that never change week to week.

**So what.** On-time delivery cannot discriminate between SKUs, so we drop it from segmentation as near-constant (§6.1). The surviving lead-time features describe *which lanes a SKU travels*, not how reliable its supply is. We carry that caveat into the interpretation of the "supply risk" cluster (§6.6).

## 5.8 The service gap sits at the customer DCs

This is the most decision-relevant pattern in the data. Historically, the network fills 92.7% of customer demand, but that average hides a 16-point gap between echelons (Table 5.4). Regional DCs fill 95.7% of the bulk demand they serve directly. Customer DCs fill only 79.7%, and they stock out in 21.7% of SKU-weeks. Days of supply are similar at all three echelons, about 18–22 days (Figure 5.9), so the gap is not a matter of too little stock overall. It is a matter of stock in the wrong place. Regional DCs fill only 75.4% of customer DCs' replenishment orders, because their policies are sized on their own bulk customers' demand and ignore the flow they pass downstream.

**Table 5.4: Inventory, service and working capital by echelon (history)**

| Echelon | Avg closing stock (units per SKU-DC) | Avg safety stock | Days of supply | Stockout weeks | Avg inventory value per SKU-DC |
|---|---|---|---|---|---|
| Customer DC | 26.8 | 17.4 | 21.0 | 21.7% | $124 |
| Regional DC | 185.9 | 117.0 | 22.1 | 3.4% | $851 |
| Plant DC | 841.6 | 99.9 | 18.1 | 0.0% | $3,909 |

Source: `input datasets/DATASET_DOCUMENTATION.md`, §7.11; team analysis.

![Figure 5.9: Days of supply by echelon](<../input datasets/report_assets/days_of_supply.png>)

*Figure 5.9: Average days of supply by echelon.*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/days_of_supply.png`

**So what.** The problem is positioning, not quantity. The stakeholders who suffer are the DSD customers (convenience, smaller grocery and foodservice) served from customer DCs. This finding motivates the choice of an *echelon* model, in which a regional DC's buffer is sized with its downstream customer DCs in view, over a better single-location formula.

## 5.9 Capacity is not the binding constraint

Plants run at 52–56% of weekly capacity on average. DC storage utilisation averages 9.1% and never exceeds 32.3% (Figure 5.10). Lane utilisation is below 2%. Line-level capacity clips about 3% of SKU-weeks.

![Figure 5.10: DC storage utilisation](<../input datasets/report_assets/dc_utilization.png>)

*Figure 5.10: DC storage utilisation by site.*
Source: synthetic Supply Brain dataset (generator v5); team analysis. Image file: `input datasets/report_assets/dc_utilization.png`

**So what.** There is physical room to reposition stock without new warehouses or production lines. The question is purely where to hold it. Storage and lane limits never bind, so a network plan that looks feasible here may not be feasible on a real, tighter network (Chapter 10).

## 5.10 Price sensitivity differs by category

We recovered two price elasticities per category from the order history. We regressed log quantity on log base price and on log(1 − discount), with SKU and week fixed effects (Table 5.5). Frozen Foods (−3.77) and Snacks (−3.56) are highly elastic. Beverages (−1.12) is the least elastic. Promotional elasticity exceeds base elasticity in three of five categories.

**Table 5.5: Price elasticity by category (base and promotional)**

| Category | Base elasticity | Promo elasticity | Promo ÷ base | Observations |
|---|---|---|---|---|
| Beverages | −1.12 | −1.35 | 1.20 | 141,530 |
| Dairy & Bakery | −1.99 | −2.45 | 1.23 | 146,778 |
| Frozen Foods | −3.77 | −3.18 | 0.85 | 136,778 |
| Grocery | −1.99 | −2.56 | 1.28 | 161,666 |
| Snacks & Confectionery | −3.56 | −3.32 | 0.93 | 152,081 |

Source: `input datasets/DATASET_DOCUMENTATION.md`, §7.8; team analysis.

**So what.** Elastic categories can absorb demand uncertainty through price at lower cost than inelastic ones. These estimates sit within published meta-analytic ranges (Bijmolt et al., 2005) and are the input to the price-inventory exchange rate in §7.8.

> **Key Takeaways**
> - Service failure is concentrated: at customer DCs (79.7% fill versus 95.7% at regional DCs), at seasonal peaks, and in products with biased or promotion-driven forecasts. That makes it a positioning and timing problem, not a "more stock" problem.
> - Each pattern maps to a design choice: concentration → value-weighted service levels; seasonality and bias → weekly re-estimation with bias correction; the echelon gap → an echelon (pooling) model; near-constant OTD → dropping it as a feature.


---

# Chapter 6: Feature Engineering and Clustering (SKU Segmentation)

A segmentation is only as meaningful as the features it is built on. This chapter covers the full segmentation workstream, end to end. It describes how raw order, forecast and inventory history become eight SKU-level features: what we engineered, what we dropped and why. It then explains how those features are transformed and clustered, how the number of clusters was chosen, and what the resulting six segments and their service ladder look like. The inventory model that consumes the service ladder is the subject of Chapter 7.

## 6.1 Segmentation features: what a service level needs to know

We built features at SKU grain, one row per SKU in `sku_master` (500 rows), and organised them into four business blocks. Each block answers one question a service-level decision must answer. This follows van Kampen et al.'s (2012) advice to choose characteristics that map to the operational decision the classes will drive:

- **VALUE: what does a stockout cost?** Annual net margin, on a log scale, and net margin percentage.
- **VARIABILITY: how much buffer does the demand itself need?** Base-demand coefficient of variation, and the share of volume sold on promotion.
- **FORECASTABILITY: how much of that variability is avoidable with a better forecast?** Weighted absolute percentage error (WMAPE) in baseline weeks, and separately in promotion weeks.
- **SUPPLY_RISK: how much protection does the supply side need?** Mean cumulative lead time, lead-time CV and on-time delivery.

The script engineers eleven features: nine candidates in these four blocks, plus two perishability features that are computed for reference but never enter clustering (Table 6.1).

**Table 6.1: Candidate segmentation feature register**

| Feature | Block | Source and window | SKU-level construction | CV across SKUs | Status |
|---|---|---|---|---|---|
| `log_annual_net_margin` | VALUE | `orders.net_margin_usd`, 156 weeks, annualised × 52/156 | ln(max(annual net margin, floor)); floor = max(1st percentile, $1) = $221.02, affecting 5 SKUs | 0.186 | Kept |
| `net_margin_pct` | VALUE | `orders.net_margin_usd`, `net_revenue_usd`, 156 weeks | Σ net margin ÷ Σ net revenue | 0.283 | Kept |
| `demand_cv_base` | VARIABILITY | `orders` non-promotional lines, 156 weeks | Weekly SKU quantity summed across DCs and customers; σ ÷ μ over weeks with demand | 0.478 | Kept |
| `promo_depth_share` | VARIABILITY | `orders.on_promo`, 156 weeks | Σ promotional quantity ÷ Σ quantity (a volume share, not discount depth) | 0.321 | Kept |
| `forecast_baseline_wmape` | FORECASTABILITY | `demand_forecast`, 2025 in-sample | Σ\|actual − forecast\| ÷ Σ actual over non-promotion weeks | 0.840 | Kept |
| `forecast_lift_wmape` | FORECASTABILITY | as above | Same, over promotion weeks; falls back to baseline WMAPE for the 80 SKUs with no promotion weeks | 4.479 | Kept |
| `lead_time_mean_days` | SUPPLY_RISK | `inventory.cum_lead_time_mean_days`; weights from 156 weeks of orders | Order-quantity-weighted mean across the SKU's DCs | 0.032 | Kept |
| `lead_time_cv` | SUPPLY_RISK | `inventory.cum_lead_time_*` | Per-DC std ÷ mean, order-quantity-weighted across DCs | 0.037 | Kept |
| `otd_pct` | SUPPLY_RISK | `replenishment_receipts.on_time_flag` | Quantity-weighted on-time share | 0.016 | **Dropped:** near-constant |
| `shelf_life_days` | PERISHABILITY | `sku_master` | As recorded | 0.084 | **Excluded by design** |
| `write_off_rate` | PERISHABILITY | `write_offs` (empty) | Σ written off ÷ Σ shipments | 0 | **Excluded by design** |

Source: `output datasets/segmentation/segmentation_report.md` ("Candidate features"); `Clustering & Segmentation/segmentation_v5/segmentation_v5.py`; team analysis.

### Why each choice was made

**Log margin, not margin.** Annual margin spans four orders of magnitude across SKUs. On a raw scale, a handful of flagship SKUs would dominate the Euclidean distance and every other SKU would collapse into one cluster. The log compresses the scale while preserving rank. The floor prevents a few near-zero-margin SKUs from becoming extreme negative outliers.

**Base CV from non-promotional lines, on network-week totals.** Measuring CV on customer-level orders would mostly measure order batching (§5.4). Excluding promotional lines separates *structural* volatility from *planned* promotional spikes, which are captured separately by promotional volume share. Weeks with no non-promotional orders are left out rather than counted as zero, so intermittency is not double-counted as variability. The cost is that intermittent SKUs look somewhat less volatile than they are.

**Two WMAPEs, not one.** Promotion-week error behaves differently from baseline error. Pooling them would let a few catastrophic promotion forecasts drown the baseline signal. WMAPE rather than MAPE is used because it remains defined and stable when individual weeks have zero demand (Kolassa & Schütz, 2007; Hyndman & Koehler, 2006). The forecastability features use only 2025, the only year with a published forecast, while value and variability use all 156 weeks. We accept this window mismatch and disclose it.

**Perishability excluded by design.** Shelf life speaks to obsolescence and overstock cost, not to the fill-rate decision a service level encodes. In this dataset it also cannot bind: shelf life is 1,095–1,459 days against a 1,092-day history, and no write-offs exist. An earlier version (v4) kept shelf life and found it became the third-strongest clustering feature (F = 250) despite being economically meaningless here. Removing it was a deliberate correction.

**On-time delivery dropped statistically.** Its CV across SKUs is 0.016, below our 0.02 near-constant cut-off, because promised lead times are set at each node's own 90th percentile (§5.7). A feature that does not vary cannot separate SKUs.

**An honest flag on SUPPLY_RISK.** The two surviving lead-time features clear the cut-off only narrowly (CV 0.032 and 0.037), yet together they carry 25% of the clustering distance. Both come from cumulative lead-time fields that are constant across all 156 weeks for each SKU × DC pair. They encode *which DCs and lanes a SKU flows through*, a static network fact, rather than realised supply variability. We kept them because a supply block is conceptually necessary and they are the best available evidence. But we read any "supply-risk" result with that caveat (§6.6).

## 6.2 Transformations

After pruning, the eight surviving features pass through four transformations (Figure 6.1).

**Figure 6.1: Segmentation feature pipeline**

```text
 11 engineered ──► prune ──────────► median ──────► winsorise ──────► standardise ──────► block-weight ─────► k-means
 features          design exclusions  impute         p1 / p99          StandardScaler      z · sqrt(1/(B·n_b))  k = 4..8
                   (2 perishability)  (no blanks     (5 SKUs clipped   (mean 0, SD 1)      = sqrt(1/8) ≈ 0.354
                   + CV < 0.02 rule   this run)      at each tail)                         each block = 25.0%
                   (otd_pct)          → 8 features
```

*Figure 6.1: Segmentation feature pipeline.* Source: `Clustering & Segmentation/segmentation_v5/segmentation_v5.py`; team analysis.

1. **Median imputation.** Blank values in surviving features are filled with the feature's median. No surviving feature has blanks in this run, so the step is a safeguard for future data rather than an active correction.
2. **Winsorisation at the 1st and 99th percentiles.** This clips five SKUs at each tail of every feature, for example capping promotion-week WMAPE at 47.0 and baseline WMAPE at 2.18. Without it, a single SKU with a promotion-week WMAPE of 169 would stretch that axis enough to make it the only thing k-means sees.
3. **Standardisation.** `StandardScaler` puts every feature on mean 0 and unit variance, so features measured in dollars, days and ratios become comparable.
4. **Block weighting.** Each standardised value is multiplied by √(1/(B · n_b)), where B is the number of blocks and n_b the number of features in that block. This gives every *block*, not every feature, an equal 25% share of total variance (`cluster_feature_block_balance.csv`). Its purpose is to stop a business dimension from dominating simply because it happens to have more surviving features. In this run every block has two features, so the weighting is a uniform rescale that leaves cluster geometry unchanged. It would matter if pruning left blocks of unequal size: had `otd_pct` survived, each supply-risk feature would have received √(1/12) instead of √(1/8).

We also considered principal component analysis before clustering, and rejected it as an input step. PCA would mix blocks into components that planners cannot interpret, and the block structure *is* the interpretation. We use PCA only to visualise the final clusters (§6.6), where the first two components explain 44.5% of variance.

## 6.3 How the feature set evolved, v2 to v5

The current feature set is the fourth iteration. Each revision fixed a specific flaw found in the previous one, as Table 6.2 records. The process mattered as much as the end state, because several early "findings" turned out to be artefacts of feature construction.

**Table 6.2: Segmentation iterations, v2 to v5**

| Version | Features and method | What we learned and changed |
|---|---|---|
| v2 | 10 features, including demand mean, std and CV, MAPE, stockout rate, lead-time mean and std, revenue, list price, margin %, shelf life; k chosen by silhouette (k = 3) | Lead-time standard deviation dominated (F = 1,540), and a "low-margin" tag proved to be an artefact. We moved to block-balanced features. |
| v3 | Five blocks including perishability; "lead time" measured as customer delivery time; k by stability (k = 4) | Net revenue silently ignored trade spend. Fill rate was 100% for every SKU by construction. "Lead time" was customer delivery, not replenishment. We rebuilt the features from the correct sources. |
| v4 | Net margin from order lines; base CV excluding promotional lines; promotion-week WMAPE; cumulative replenishment lead time; winsorisation; k = 4 | Shelf life (3–4 years, meaningless here) became the third-strongest feature and gave perishability 20% of the distance. We excluded perishability by design. |
| **v5 (canonical)** | 8 features in 4 balanced blocks; k = 4–8 by subsample ARI stability; newsvendor and revenue-share service ladder | k = 6 selected (ARI 0.848). Archetype labels are rule-generated and partly misleading, so we interpret clusters by their measured signatures (§6.6). |

Source: `ASSUMPTIONS_AUDIT.md`, §2; `Clustering & Segmentation/` (v2–v5); team analysis.

## 6.4 Clustering models and metrics considered

**Table 6.3: Clustering models and metrics considered**

| Model | Brief approach | Metric(s) and why | Challenges |
|---|---|---|---|
| k-means, k chosen by silhouette (v2) | Euclidean k-means over 2–8 clusters on unbalanced features | Silhouette width (Rousseeuw, 1987) | Picked k = 3, dominated by one lead-time feature; silhouette 0.198 rewarded a coarse split |
| **k-means, k chosen by subsample stability (v3–v5; adopted)** | Block-weighted Euclidean k-means with k-means++ seeding (Lloyd, 1982; Arthur & Vassilvitskii, 2007) over k = 4–8 | Subsample stability (mean ARI over 25 × 80% subsamples), because a SKU's segment must not flip with sampling noise; silhouette as tie-breaker; ANOVA F per feature, descriptive only | Low silhouettes (about 0.17) at every k: the data is a continuum, so boundary SKUs exist |

Source: `ASSUMPTIONS_AUDIT.md`, §2; `Clustering & Segmentation/segmentation_v5/segmentation_v5.py`; team analysis.

**Why these metrics.** Silhouette rewards compact, well-separated clusters, but operational SKU data rarely forms them, and all our silhouettes sit between 0.16 and 0.18. A planner acts on a segment only if the same SKU lands in the same segment when the data is resampled next quarter. We therefore select k on subsample stability, measured as the Adjusted Rand Index between the full-sample clustering and clusterings of 80% subsamples (Hubert & Arabie, 1985; Hennig, 2007). Silhouette is used only to break ties.

## 6.5 Clustering model chosen

**Segmentation: k-means with k = 6.** k = 6 had the highest subsample stability of the range tested (ARI 0.848). It was markedly more stable than k = 5 (0.718) or k = 7 (0.685), even though k = 5 had a fractionally higher silhouette (0.176 versus 0.171). Following Hennig (2007), we treat reproducibility under resampling as the property that matters: a planner can act on a segment only if it will still be there next quarter. All eight features differ significantly across the six clusters (one-way ANOVA, p < 10⁻⁷ for every feature). Because those are the same features the clustering used, we read this as descriptive, not as validation.

**Table 6.4: k-selection diagnostics**

| k | Stability ARI | Silhouette | Inertia |
|---|---|---|---|
| 4 | 0.766 | 0.174 | 310.9 |
| 5 | 0.718 | 0.176 | 282.3 |
| **6** | **0.848** | **0.171** | **258.6** |
| 7 | 0.685 | 0.158 | 243.6 |
| 8 | 0.593 | 0.160 | 232.6 |

Source: `output datasets/segmentation/report_artifacts/cluster_k_diagnostics.csv`; team analysis.

## 6.6 Segmentation Results

Six clusters emerged. Their economics are stark (Table 6.5). Two core clusters hold 53.4% of SKUs but 92.2% of revenue and 92.7% of net margin. The remaining four long-tail clusters share the last 8%.

**Table 6.5: Segment portfolio and recommended service levels**

| Cluster | Code label | What the data says it is | SKUs | % revenue | % margin | Baseline WMAPE | Promo share | CSL | z |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Flagship Core | Highest value, lowest forecast error | 134 | 53.6% | 62.7% | 0.34 | 22% | 97.0% | 1.881 |
| 0 | Mid-Tier Core | High value, promotion-driven (highest promo share) | 133 | 38.6% | 30.0% | 0.45 | 36% | 97.0% | 1.881 |
| 2 | Long-Tail Erratic 2 | Stable supply (lowest lead-time CV), moderate demand variability | 47 | 3.0% | 2.5% | 0.56 | 29% | 95.7% | 1.717 |
| 4 | Long-Tail Erratic | Genuinely volatile (highest base-demand CV, 0.80) | 88 | 2.4% | 2.8% | 0.59 | 28% | 90.0% | 1.282 |
| 5 | Long-Tail Supply-Risk | Forecast-challenged (highest baseline WMAPE, 1.85) | 81 | 2.0% | 1.7% | 1.85 | 24% | 90.0% | 1.282 |
| 3 | Long-Tail Erratic 3 | Promotion forecasts fail (promo-week WMAPE about 51) | 17 | 0.4% | 0.3% | 1.73 | 26% | 90.0% | 1.282 |

Source: `output datasets/segmentation/cluster_service_levels.csv`; `output datasets/segmentation/segmentation_report.md`; team analysis.

**Read the labels with care.** Archetype names are generated by rule in the code, and three of them do not match the measured signature:

- **Cluster 5 ("Supply-Risk") is really the forecast-challenged cluster.** Its lead-time CV (0.4805) differs from cluster 4's (0.4804) by less than 0.0001, but its baseline forecast error is the worst in the portfolio.
- **Cluster 2 ("Erratic 2") has the *lowest* lead-time CV** and a lower demand CV than every other tail cluster.
- **Cluster 3 ("Erratic 3") is defined by promotion-week forecast failure,** typical of sparse promotion-week actuals.

We keep the code labels in tables and figures for traceability to the application and outputs. The middle column gives the interpretation the business should act on. The SHAP analysis in §8.2 confirms each reinterpretation independently.

![Figure 6.2: Cluster separation in PCA space](<../output datasets/segmentation/report_artifacts/chart_pca.png>)

*Figure 6.2: Cluster separation in the first two principal components (44.5% of variance).*
Source: synthetic Supply Brain dataset; team analysis. Image file: `output datasets/segmentation/report_artifacts/chart_pca.png`

Figure 6.2 shows that the two core clusters (0 and 1) occupy distinct regions of the space. The forecast-challenged clusters (3 and 5) spread to the right along the first component, which loads on forecast error. The clusters touch at their edges, consistent with the low silhouette, so boundary SKUs exist and deserve planner review.

![Figure 6.3: Standardised feature centroids per cluster](<../output datasets/segmentation/report_artifacts/chart_fingerprints.png>)

*Figure 6.3: Standardised feature centroids ("fingerprints") per cluster.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `output datasets/segmentation/report_artifacts/chart_fingerprints.png`

The fingerprints in Figure 6.3 make each cluster's identity legible at a glance:

- Cluster 3 is dark red on promotion-week WMAPE.
- Cluster 2 is dark blue on lead-time CV.
- Cluster 5 is elevated on baseline WMAPE.
- Clusters 3 and 5 have the lowest log margin.

![Figure 6.4: Recommended versus economically implied service level](<../output datasets/segmentation/report_artifacts/chart_service_ladder.png>)

*Figure 6.4: Recommended CSL versus the economic (newsvendor) critical ratio, by cluster.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `output datasets/segmentation/report_artifacts/chart_service_ladder.png`

**The service ladder is strategic, not purely economic.** The newsvendor critical ratio uses unit margin times a subcategory stockout multiplier as the understock cost, and half of annual holding cost as the overstock cost (Arrow et al., 1951; Khouja, 1999). It is almost flat across clusters (medians 0.950–0.957), because the synthetic cost inputs are similar and the carrying rate is low. The recommended CSL therefore comes mainly from the revenue-share rule: a 97% floor for clusters with at least 30% of revenue, a 90% cap for clusters with at most 3%, and the cluster's median ratio otherwise (Figure 6.4).

One consequence is a knife edge. Cluster 2's revenue share is 3.004%, just above the 3% cap, so it keeps its 95.7% median ratio, while cluster 4, with 2.4% of revenue, gets 90%. A second consequence is that Flagship Core and Mid-Tier Core both receive 97%, so the tier label carries no policy difference. We recommend recalibrating the ladder once real penalty, substitution and expiry costs are available (Chapter 9).

![Figure 6.5: Baseline versus promo-lift forecast error by cluster](<../output datasets/segmentation/report_artifacts/chart_failure_modes.png>)

*Figure 6.5: Baseline-week versus promotion-week forecast error, by cluster.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `output datasets/segmentation/report_artifacts/chart_failure_modes.png`

Figure 6.5 shows why cluster 3 exists. Its SKUs have ordinary baseline error but promotion-week WMAPE of 20 to 169. Their promotions are essentially unforecastable at the customer grain at which the forecast is published. The business action for these 17 SKUs is a better promotion forecast, or a decision to stop promoting them. It is not more safety stock.

**Does the ladder deliver?** We tested each cluster's own fill rate at candidate service levels of 90%, 95%, 95.7%, 97% and 99% against replayed 2025 demand over five seeds (at α = 0.2), using the inventory model and validation design described in Chapter 7. Five of six clusters land within about three points of their target or above it:

- Flagship Core reaches 94.3% at its 97% target.
- Mid-Tier Core reaches 95.7%.
- The three 90% clusters reach 90.9–93.6%.

Long-Tail Erratic 2 is the exception. It reaches only 88.1% at its 95.7% target, and only 89.9% even at a 99% target. Later pooling experiments show that most of this shortfall is a positioning effect rather than a service-level effect: with full pooling (α = 1.0) its own fill rises to 95.6% (§7.5). That is the clearest single illustration in the project that *where* stock sits can matter more than *how much* is targeted.

> **Key Takeaways**
> - Forecast error and promotional share drove segment boundaries most (SHAP ranks 1 and 2, §8.2). Perishability was excluded by design, and on-time delivery was dropped as near-constant.
> - k = 6 was chosen on subsample stability (ARI 0.848), not silhouette. Two core clusters hold 53.4% of SKUs but 92.2% of revenue and 92.7% of margin, and receive a 97% service level. The four long-tail clusters receive 90–95.7%.
> - Three rule-generated cluster names misdescribe their clusters. The "supply-risk" cluster is really the forecast-challenged one, so the right action for it is a better forecast, not more stock.

---

# Chapter 7: Modelling and Results: Multi-Echelon Inventory Optimization

The question this chapter answers is the one a sponsor asks first: does it work, and how do we know? Chapter 6 set *how much* protection each SKU receives. This chapter covers the inventory optimiser that decides *where* that protection sits, from the demand signals it sizes against to its results. We tested it against a deliberately demanding standard: one shared simulator, one opening position, multiple random seeds and replayed demand. The headline is positive, but the path to it ran through several honest failures, which we report because they explain why the final design looks the way it does.

## 7.1 MEIO demand and supply signals

The segmentation decides *how much protection* each SKU deserves. The MEIO model must then estimate, every week and for every active DC–SKU pair, *how much demand and uncertainty* that protection has to cover. The single most consequential engineering choice in the whole project was to move from one flat annual demand estimate to a weekly re-estimated, seasonally aware one. Table 7.1 lists the engineered signals.

**Table 7.1: Engineered demand and supply signals used by MEIO**

| Signal | Construction | Why |
|---|---|---|
| Forecast bias factor | Trailing 13-week Σ actual ÷ Σ forecast (weeks with forecast > 0), clipped to [0.5, 2.0]; 1.0 with no history | The published forecast is biased low by about 8% (§5.6). Safety stock buffers random error, not bias, so bias must be removed first. |
| De-seasonalised level | Trailing 52-week mean of actual ÷ seasonal index | A level that does not jump with the season it was measured in |
| Seasonal index | Per (subcategory, week of year), pooled over every SKU and DC | Pooling gives a stable index from sparse SKU-level history. Built from all 156 weeks, which introduces some look-ahead for 2025 (disclosed in §4.1). |
| Target-week mean | 0.6 × (forecast × bias factor) + 0.4 × (de-seasonalised level × seasonal index for the target week) | Blends the forward-looking forecast with history-based seasonality. This replaced a fallback that zeroed the policy for about 38% of active pairs in weeks with a literal zero forecast. |
| Demand uncertainty | σ = target mean × CV, where CV is the mean of the 13-week and 52-week trailing CVs of actuals, each capped at 3.0 | A 13-week window alone understated full-year volatility |
| Echelon uplift | σ × 1.00 at customer DCs, × 1.25 at regional DCs | Allows for the order lumpiness a parent absorbs from its children (Lee et al., 1997) |
| Cumulative lead time | Plant → PDC → RDC → CDC along the fastest inbound lane, means added and variances summed | Makes each node's full protection interval explicit |
| Demand during lead time | Gamma demand per week, mixed over Gamma lead time; percentile by Monte Carlo (research) or 64-node quadrature (production) | Non-negative and right-skewed, so high-percentile reorder points are priced better than under a Normal (Burgin, 1975) |
| Safety factor | z = Φ⁻¹(CSL) from the segment ladder: 1.881 (97%), 1.717 (95.7%), 1.282 (90%), clipped to [0.84, 3.10] | Connects segmentation to the optimiser |

Source: `Model Documentation/MEIO Weekly Baseline.md`; `Model Documentation/Clark Scarf v2.md`; `models/MEIO/MEIO_Weekly/final_submission_optimized/meio_weekly_lib.py`; team analysis.

**Quadrature versus Monte Carlo.** The research evidence sized demand during lead time by Monte Carlo sampling. In production we switched to a deterministic quadrature: bisection on the Gamma-mixture distribution over 64 equiprobable lead-time nodes. The reason is practical. Monte Carlo draws come from one shared random stream, so in a development check, changing 20 rows at one customer DC moved safety stock on about 5,566 of 7,468 rows across all 23 DCs by roughly 3% each. That would make every scenario's "prepared versus unprepared" comparison mostly noise. With quadrature, a row changes only when its own inputs change. Our tests require quadrature to stay within 0.5% median relative error of a 400,000-draw Monte Carlo for safety stock, and within 0.3% for reorder point.

## 7.2 Models and Metrics Considered

**Table 7.2: Inventory models and metrics considered**

| Model | Brief approach | Metric(s) and why | Challenges |
|---|---|---|---|
| Baseline (s, S) | The dataset's static policy: one safety stock, reorder point and order-up-to level per DC–SKU, fitted on all 156 weeks; segment z of 2.05 / 1.64 / 1.28 | The reference arm every model must beat | Regional DCs sized on direct bulk demand only; 358 RDC–SKU pairs with downstream demand have no row |
| MEIO Weekly (v1–v10) | Weekly closed-form (R, s, S) at each node independently; bias-corrected, de-seasonalised target-week demand; Normal safety stock | Customer fill (overall, CDC, RDC), internal replenishment fill, holding, stockout, transport and total landed cost | Hand-tuned over ten rounds; no pooling between echelons |
| Clark-Scarf v1 | Echelon (pooled) sizing at regional DCs, netted against downstream stock (Clark & Scarf, 1960) | As above | All-or-nothing pooling |
| Guaranteed Service Model | Graves–Willems service-time placement over the supply tree (Graves & Willems, 2000) | As above | Assumes bounded demand and guaranteed service times that the twin does not honour |
| Simulation-optimisation | Random search (15 trials) over MEIO Weekly's tuning constants, scored by the twin (Fu, 2002) | As above | Expensive per trial; noisy |
| Thompson-sampling bandit (service tiers) | One bandit per demand segment choosing a CSL tier from {90, 95, 97, 99%}; reward = twin-scored cost saving (Thompson, 1933; Russo et al., 2018) | As above, plus belief convergence | Few rounds (8 per segment); batched weekly feedback |
| Cluster bandit | The same bandit over the segmentation's clusters | As above | Same |
| **Clark-Scarf v2** | Continuous pooling factor α between independent and fully pooled regional-DC safety stock; netting discount; Gamma demand during lead time; weekly rolling | All of the above, plus five-seed paired tests and the share of demand-weeks filled in full | Internal replenishment fill degrades |

Source: `Model Documentation/*.md`; `models/MEIO/MEIO_Weekly/alt_models/`; team analysis.

**Why these metrics.** We judge inventory policies on outcomes, not on forecast accuracy, because this is a decision problem:

- **Unit fill rate** is the customer's experience of the promise. We split it by channel because the CDC/DSD channel is where service fails.
- **Total landed cost** combines holding cost, stockout cost (lost margin on unmet units) and transport, so a policy cannot "win" by shifting cost from one bucket to another.
- **Internal replenishment fill** guards against a policy that serves customers by starving the network upstream.
- **Average inventory value** measures working capital.

We do not use classification accuracy or RMSE anywhere in policy selection.

## 7.3 Model Chosen

**Inventory optimiser: Clark-Scarf v2.** We chose Clark-Scarf v2 for three reasons:

1. **It is the only model that beat the baseline on replayed real demand across multiple seeds** (§7.5).
2. **Its central parameter answers the charter's "where" question directly.** The pooling factor α decides how much protection a regional DC holds on behalf of the customer DCs it feeds.
3. **It is auditable.** Every parameter it uses is chosen by an explicit rule from saved evidence and recorded in a receipt.

The model sizes customer-DC and regional-DC stock from the Gamma demand-during-lead-time percentile at each SKU's target service level. At a regional DC it blends two answers:

> RDC safety stock(α) = α × max(0, pooled echelon SS − netting discount × Σ downstream CDC SS) + (1 − α) × independent SS

Here the pooled echelon demand is the RDC's own demand plus that of its customer DCs, with variances added. At α = 0 the regional DC is sized as if it stood alone; at α = 1 it carries the fully pooled echelon buffer. The netting discount controls how much credit the regional DC takes for stock already held downstream. Plant DCs are push nodes and are sized on throughput. Order-up-to levels follow the dataset's convention: reorder point plus 1.5 weeks of demand, with a floor of 1.2 × the reorder point.

## 7.4 The first lesson: single-week sizing fails

Our first comparison sized every candidate model on a single frozen week (as of 2025-W52, targeting 2026-W01) and scored the resulting static policy through the twin over the full 2026 year. Every model underperformed the baseline (Table 7.3, Figure 7.1).

**Table 7.3: Frozen single-week model comparison (2026 forward year, seed 2026)**

| Model | Customer fill | CDC fill | RDC fill | Internal fill | Stockout cost | Total landed cost | Fill vs baseline | Cost vs baseline |
|---|---|---|---|---|---|---|---|---|
| Baseline | 90.97% | 78.63% | 93.64% | 89.05% | $4.69M | $36.22M | – | – |
| Sim-optimise | 85.78% | 72.79% | 88.59% | 85.99% | $7.12M | $38.19M | −5.19 pp | +$1.98M |
| Bandit (service tiers) | 84.86% | 73.34% | 87.35% | 85.42% | $7.38M | $38.48M | −6.11 pp | +$2.26M |
| MEIO Weekly v10 | 83.43% | 73.01% | 85.68% | 86.26% | $8.05M | $38.89M | −7.54 pp | +$2.67M |
| Clark-Scarf v1 | 80.54% | 71.12% | 82.57% | 78.85% | $9.52M | $40.07M | −10.43 pp | +$3.86M |
| Guaranteed Service Model | 48.43% | 69.29% | 42.70% | 80.84% | $35.17M | $56.03M | −42.54 pp | +$19.82M |

Source: `models/MEIO/MEIO_Weekly/alt_models/alt_output/model_comparison_report.md`; team analysis.

![Figure 7.1: Frozen single-week model comparison](<../models/MEIO/MEIO_Weekly/alt_models/alt_output/chart_model_comparison.png>)

*Figure 7.1: Customer fill rate and total landed cost by model, frozen single-week basis.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `models/MEIO/MEIO_Weekly/alt_models/alt_output/chart_model_comparison.png`

**Why.** A policy sized for one week in early January is wrong for a year that peaks in Q4 (§5.3). The baseline, sized on a 156-week average, is at least sized on a full-year average. The GSM result is a vivid illustration of the theory in §2.1. Its stock-or-no-stock placement left regional DCs, which serve 81% of demand directly, with almost no buffer (42.7% RDC fill). That placement is optimal only if upstream service times are guaranteed, and in this network they are not.

**What we changed.** The failure was not in the optimisers but in the demand signal they were given. Every model after this point sizes a *different* policy for each of the 52 target weeks, re-estimating demand level, bias and uncertainty as of the week before (§7.1). This change, more than any choice of optimiser, is what turned below-baseline models into above-baseline ones. It is the most transferable lesson of the project.

## 7.5 Validation on replayed 2025 demand

**Design.** To test whether the weekly rolling approach works on demand it was not tuned to, we built a walk-forward validation. For each week of 2025, Clark-Scarf v2 sizes that week's policy using only information available before it. The resulting 52-week policy is then scored through the twin against 2025's actual order history. The comparison arm, labelled "actual", is the twin replaying the same 2025 orders under the dataset's own baseline policy. Both arms start from the same position and share demand draws. We repeated the key comparisons over five paired seeds (7, 42, 123, 2026, 2027), which vary lead-time, production and Monte Carlo noise.

**Headline.** At the originally validated setting (α = 0.2, netting discount 0.0), Clark-Scarf v2 raises customer fill from 93.08% to 94.51% in the reference seed, and by +1.52 points on average over five seeds (Table 7.4, Figure 7.2). The gain is concentrated at customer DCs, which rise from 80.21% to 86.93%. MEIO Weekly, which sizes each node independently, does not beat the baseline (−0.08 points). The difference between the two models is pooling.

**Table 7.4: Validation on replayed 2025 demand (2025-W02 to W52)**

| Arm | Customer fill | CDC fill | RDC fill | Internal replenishment fill | Weeks at zero stock | Total landed cost vs actual | Seeds |
|---|---|---|---|---|---|---|---|
| Actual (baseline policy) | 93.08% | 80.21% | 95.99% | 93.21% | 11.08% | – | – |
| MEIO Weekly | 93.00% (−0.08 pp) | – | – | 88.86% | – | – | 1 |
| Clark-Scarf v2, α 0.2, netting 1.0 | +1.05 pp (5-seed mean) | – | – | 82.24% | – | +$1.79M | 5 |
| **Clark-Scarf v2, α 0.2, netting 0.0** | **94.51% (+1.43 pp); +1.52 pp 5-seed mean** | **86.93%** | **96.23%** | **82.38%; 82.32% mean** | **10.62%** | **+$1.67M** | **5** |
| Clark-Scarf v2, α 0.6, netting 1.0 | +1.54 pp (5-seed mean) | – | – | – | – | +$1.85M | 5 |
| **Clark-Scarf v2, α 0.6, netting 0.0** | **+2.69 pp (5-seed mean)** | – | – | **84.64%** | – | **+$1.43M** | **5** |

Source: `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/` (`headline_clark_scarf_v2_vs_actual.csv`, `netting_discount_multiseed.csv`, `netting_discount_multiseed_alpha0_6.csv`, `meio_weekly_vs_actual.csv`); team analysis. "–" = not reported for that arm.

![Figure 7.2: Clark-Scarf v2 versus actual on replayed 2025 demand](<../models/MEIO/MEIO_Weekly/final_submission/results/chart_headline.png>)

*Figure 7.2: Fill rate by echelon (left) and cost (right), Clark-Scarf v2 at α = 0.2 versus the baseline policy, replayed 2025 demand.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `models/MEIO/MEIO_Weekly/final_submission/results/chart_headline.png`. The committed chart is from the original results folder; Table 7.4 gives the current figures.

**Where the cost goes.** In the reference seed, the +$1.67M rise in landed cost breaks down as follows (`headline_cost_summary.csv`):

- holding cost: +$0.24M
- stockout cost (lost margin): −$0.57M
- transport: +$2.01M

The transport rise reflects more units actually moved to customers rather than lost. Transport is linear per unit and makes up 85–91% of landed cost (assumption A6), so in this cost model any policy that serves more demand looks more expensive. Chapter 9 returns to what that means for the business case.

**The netting fix.** The original pooled formula credited a regional DC with 100% of the safety stock its customer DCs already held. That assumes stock can move wherever it is needed instantly, which weekly review and real transit times do not allow. We introduced a *netting discount* to dial that credit down. Setting it to zero beats full netting on every axis in five of five seeds:

- at α = 0.2: +1.52 versus +1.05 points of fill, at $128K lower cost
- at α = 0.6: +2.69 versus +1.54 points, at $420K lower cost

There was no trade-off, so netting 0.0 is part of the deployed configuration. In the course of this test we also found and fixed a methodological trap. Sharing one random stream across sequentially built variants created a four-point swing in internal fill from stream position alone, larger than the effect being measured. Giving every variant the same fresh seed isolated the parameter as the only variable.

**How much to pool.** Sweeping α on replayed 2025 demand at netting 0.0 shows a monotonic pattern (Table 7.5). Customer fill rises from 93.68% at α = 0 to 96.04% at α = 1. Internal replenishment fill rises from 81.43% to 86.36%. Stockout cost falls from $8.72M to $7.52M. Total landed cost falls slightly as α rises, because lower stockout cost more than offsets slightly higher transport.

**Table 7.5: Pooling-factor sweep on replayed 2025 demand (netting 0.0, single seed)**

| α | Customer fill | Internal fill | Holding cost | Stockout cost | Transport cost | Total landed cost | Cost Δ vs actual |
|---|---|---|---|---|---|---|---|
| 0.0 | 93.68% | 81.43% | $2.30M | $8.72M | $78.58M | $89.61M | +$1.84M |
| 0.2 | 94.56% | 82.34% | $2.30M | $8.29M | $78.80M | $89.39M | +$1.63M |
| 0.4 | 95.23% | 83.47% | $2.30M | $8.00M | $79.04M | $89.35M | +$1.58M |
| 0.6 | 95.70% | 84.74% | $2.30M | $7.72M | $79.22M | $89.23M | +$1.47M |
| 0.8 | 96.02% | 85.78% | $2.30M | $7.62M | $79.28M | $89.21M | +$1.44M |
| 1.0 | 96.04% | 86.36% | $2.31M | $7.52M | $79.29M | $89.12M | +$1.35M |

Source: `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/alpha_cost_breakdown.csv` and `alpha_sweep_real_data.csv`; team analysis. Costs are full 156-week run totals; the arms differ only in the 2025 test window.

![Figure 7.3: Pooling-factor sweep on replayed 2025 demand](<../Model Documentation/images/clark_scarf_v2_alpha_sweep_real_data.png>)

*Figure 7.3: Fill rate (left) and cost delta (right) versus α on replayed 2025 demand, from the earlier netting 1.0 configuration.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `Model Documentation/images/clark_scarf_v2_alpha_sweep_real_data.png`. Under netting 1.0 the fill curve peaks near α = 0.6 (94.87%). Under the adopted netting 0.0 (Table 7.5) it rises monotonically.

The single-seed sweep runs one random stream through all six α values, so we do not select α from it. Instead, the parameter-selection rule (§4.2.2) adopts a new α only on five-seed paired evidence. Only α = 0.4 and α = 0.6 were run over five seeds (Table 7.6), and both beat the baseline in every seed. α = 0.6 beats α = 0.2 by +1.17 points of fill at $236K lower cost, well beyond two standard errors. The rule therefore selects α = 0.6. α = 0.8 and 1.0 look better still in the single-seed sweep, but they lack multi-seed evidence and cannot be adopted under the rule.

**Table 7.6: Five-seed robustness of α = 0.4 and α = 0.6 (netting 0.0, replayed 2025 demand)**

| α | Seed 7 | Seed 42 | Seed 123 | Seed 2026 | Seed 2027 | Mean fill Δ | Mean cost Δ |
|---|---|---|---|---|---|---|---|
| 0.4 | +2.39 pp | +2.22 pp | +2.30 pp | +2.26 pp | +2.25 pp | **+2.28 pp** | +$1.54M |
| 0.6 | +2.70 pp | +2.67 pp | +2.81 pp | +2.61 pp | +2.66 pp | **+2.69 pp** | +$1.43M |

Source: `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/multiseed_alpha_04_06.csv`; team analysis.

**Pooling by segment.** Should every segment pool to the same degree? An early single-seed experiment on synthetic forward demand suggested that core clusters wanted more pooling and long-tail clusters less. The real-demand, five-seed sweep on the six v5 clusters did not reproduce the long-tail half (Table 7.7). Every cluster's own fill rises with α, and the largest response is Long-Tail Erratic 2's, from 83.9% at α = 0 to 95.6% at α = 1. We report this as a case of the method correcting itself. A plausible, mechanistically explainable finding from one seed on synthetic data did not survive replayed demand and replication.

**Table 7.7: Per-cluster own fill versus pooling factor (five seeds; other clusters held at α = 0.6)**

| Cluster | α = 0.0 | α = 0.6 | α = 1.0 | Adopted α | Own fill at adopted α |
|---|---|---|---|---|---|
| Flagship Core | 93.14% | 95.81% | 96.37% | 1.0 | 96.37% |
| Mid-Tier Core | 95.33% | 96.15% | 96.22% | 0.6 | 96.15% |
| Long-Tail Erratic | 93.13% | 94.34% | 94.69% | 1.0 | 94.69% |
| Long-Tail Erratic 2 | 83.94% | 93.96% | 95.55% | 1.0 | 95.55% |
| Long-Tail Erratic 3 | 90.57% | 91.84% | 91.83% | 0.8 | 92.07% |
| Long-Tail Supply-Risk | 91.40% | 93.26% | 93.81% | 1.0 | 93.81% |

Source: `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/segment_alpha_v5_real_data_summary.csv`; `output datasets/MEIO/SCENARIO_0002_MEIO_CLARK_SCARF_V2/rolling_2026/meio_parameters.json`; team analysis.

Two cautions apply to Table 7.7:

- **Each cluster was swept with the others held fixed,** and the six winners were adopted together without a joint run. Interactions through shared regional and plant stock are therefore untested.
- **Four of the six winners sit on the grid boundary (α = 1.0),** so the true optimum may lie at full pooling or beyond what this formulation allows.

**Where the win comes from: the seasonal peak.** On the 2026 forward basis, we compared fill for the 150 Seasonal-segment SKUs at their trough week (2026-W03) and their peak week (2026-W48) (Figure 7.4). The baseline, sized on a flat annual mean, fills 92.7% at the trough but only 78.4% at the peak, worse at the moment that matters most. Weekly re-estimation lifts the peak to 85.6% (MEIO Weekly) and 88.6% (Clark-Scarf v2). On replayed 2025 demand, the improvement is largest for the Intermediate segment (89.6% → 93.2%) and the Seasonal segment (92.8% → 94.6%), against 93.6% → 94.7% for Fast-Moving SKUs (`segment_breakdown_real_data.csv`). The portfolio-average gain is therefore diluted: the real edge is concentrated in hard weeks and hard SKUs, and even there the gap is not fully closed.

![Figure 7.4: Seasonal segment fill at peak versus trough](<../models/MEIO/MEIO_Weekly/final_submission/results/chart_seasonal_peak_trough.png>)

*Figure 7.4: Seasonal-segment customer fill at the trough week, full-year average and peak week, by model (2026 forward basis, α = 0.2).*
Source: synthetic Supply Brain dataset; team analysis. Image file: `models/MEIO/MEIO_Weekly/final_submission/results/chart_seasonal_peak_trough.png`

## 7.6 Deployed configuration: the 2026 forward year

The pipeline resolves its parameters from the evidence above. It adopts α = 0.6 as the portfolio default, netting 0.0, the per-cluster α values in Table 7.7 (overriding 367 SKUs outside Mid-Tier Core), quadrature sizing of demand during lead time, and the segmentation's service ladder. It then builds a 52-week rolling policy from 2025-W52 and scores it in the twin against the baseline policy from the same opening position (Table 7.8).

**Table 7.8: Deployed configuration versus baseline, 2026 forward year (seed 2026)**

| KPI | Baseline (s, S) | Clark-Scarf v2 (deployed) | Change |
|---|---|---|---|
| Customer fill rate | 91.01% | 95.02% | **+4.01 pp** |
| Customer fill, customer DCs (DSD channel) | 78.67% | 90.19% | **+11.52 pp** |
| Customer fill, regional DCs (bulk channel) | 93.68% | 96.06% | +2.38 pp |
| Demand-weeks filled in full | 86.29% | 91.99% | +5.70 pp |
| Location-weeks at zero stock | 9.71% | 5.44% | −4.27 pp |
| Internal replenishment fill | 89.07% | 78.43% | **−10.64 pp** |
| Stockout cost (lost margin) | $4.67M | $2.52M | **−$2.15M** |
| Holding cost | $0.74M | $0.87M | +$0.13M |
| Transport cost | $30.79M | $33.00M | +$2.21M |
| **Total landed cost** | **$36.20M** | **$36.39M** | **+$0.19M (+0.5%)** |
| Average inventory value | $6.23M | $7.33M | +$1.10M (+17.6%) |
| Safety-stock value | $2.57M | $2.73M | +$0.16M (+6.1%) |

Source: `output datasets/projections/baseline/run_kpis.json`; `output datasets/SCENARIO_0002_MEIO_CLARK_SCARF_V2/meio_policy/run_kpis.json`; team analysis.

**Reading the result.** Five points stand out:

1. **Service improves most where it was worst.** Customer-DC fill rises by 11.5 points, closing most of the gap with the bulk channel (90.2% versus 96.1%, down from 78.7% versus 93.7%).
2. **Lost margin almost halves.** It falls by $2.15M.
3. **Total landed cost is nearly flat.** It rises $0.19M (0.5%). The $2.21M rise in transport is the freight for about 568,000 more units delivered, 4.0% of 14.16M units of demand.
4. **The price is working capital.** Average inventory value rises $1.10M. Most of this is cycle stock, because safety-stock value rises only 6.1%.
5. **The unresolved weakness is internal replenishment.** It falls 10.6 points, which means regional DCs are filling fewer of their customer DCs' orders on time. Customer service still rises because buffers now sit closer to demand. But a network leaning on downstream stock is less resilient to upstream shocks.

The internal-fill drop appears under every model and setting we tested, and the netting fix only partly closed it. The assumptions audit identifies two twin mechanics as probable contributors (assumption A11): the parent hold-back rule and the double-applied plant allocation share. We flag this as the most important open issue before scale-up (§8.2, Chapter 10).

**What has not been tested.** This exact combination (portfolio α 0.6, per-cluster overrides, quadrature sizing and the 52-week roll-forward) has been run once, on the forward year, at one seed. It has not been back-tested on replayed 2025 demand as a unit, or over multiple seeds. The closest validated evidence is the portfolio-only α = 0.6 five-seed result (+2.69 points; Table 7.6).

## 7.7 Scenario testing and the closed loop

The charter asked for a system that "re-learns from outcomes". We implemented this as three arms for any disruption scenario, each simulated from the same opening state and seed:

- **Unprepared:** today's policy meets the disruption.
- **Prepared:** MEIO re-plans for all 52 weeks knowing the scenario in advance.
- **Reactive:** MEIO is not told about the scenario. Before every week it re-sizes the policy from what the twin has simulated so far. It updates the demand history (bias, CV, seasonal level) and scales lane lead times once the observed slowdown passes a significance test (beyond 5% and three standard errors).

A *reactive control*, the same weekly re-planning with no scenario, separates the value of re-planning in a normal year from the reaction to the disruption itself.

**Table 7.9: Scenario results (single seed, 2026 forward year)**

| Scenario and comparison | Customer fill Δ | Total landed cost Δ | Notes |
|---|---|---|---|
| **SCENARIO_0004:** Fresh Dairy and Powdered Milk demand −25% all year; all lane lead times +10% in W01–W06 | | | Authored by the agents from a planner's plain-English request; approved before running |
| Prepared vs unprepared | −0.07 pp | −$77K | Planning ahead trims cost slightly; no fill benefit |
| Reactive vs unprepared | +0.97 pp | +$208K | Holds about $0.77M more inventory |
| …of which normal-year re-planning effect (control vs no scenario) | +1.15 pp | +$298K | |
| …of which reaction to the scenario | −0.18 pp | −$90K | |
| **SCENARIO_0003:** dairy −25%; lanes +10% in W43–W48 | | | |
| Reactive vs unprepared | +1.12 pp | +$225K | |
| …of which reaction to the scenario | −0.03 pp (full year); **+0.40 pp in W43–W48; +0.94 pp at CDCs in W43–W48** | **−$74K** | The lane slowdown was detected 1–2 weeks after onset (estimated ratio 1.08–1.10 vs a true 1.10) and released 2–3 weeks after it ended; the dairy bias factor fell from about 0.99 to 0.79 by week 12 |

Source: `output datasets/SCENARIO_0004_Veganism_Dairy_Decline_Protest_Shipping_Delays/{prepared,unprepared,reactive}/run_kpis.json`; `output datasets/SCENARIO_0002_MEIO_CLARK_SCARF_V2/{meio_policy,counterfactual_reactive_no_scenario}/run_kpis.json`; `models/MEIO/MEIO_Weekly/final_submission_optimized/README.md` ("Reactive arm"); team analysis.

**What the scenarios show.** Three findings follow:

1. **The loop works mechanically.** The reactive arm detected a lead-time disruption within one to two weeks, at close to its true magnitude, released it after it ended, and tracked a structural demand decline through its bias factor.
2. **During a disruption window, reacting helps.** In SCENARIO_0003 it added 0.4 points of fill (0.9 at customer DCs) at lower cost.
3. **Over a full year, most of the reactive arm's gain is simply re-planning weekly, which helps even with no disruption.** That gain costs about 11% more inventory and a further fall in internal fill.

The value of *preparing* in advance is small and changes sign across runs, so we do not claim a measured benefit for it. These are single-seed working examples that demonstrate the mechanism, not validated estimates of value.

## 7.8 The price-inventory exchange rate (exploratory)

The charter's distinctive idea was to ask, for each segment, whether demand uncertainty is cheaper to absorb with inventory or with price. The answer can be expressed as an "exchange rate": the carrying cost of the safety stock needed to cover a given demand shock, divided by the expected discount cost of shifting the same demand through price. A ratio above 1 means price is the cheaper buffer.

**Elasticity features.** For the price-inventory analysis, we estimated two elasticities per category from the order history. We regressed ln(order quantity) on ln(base price) and on ln(1 − discount), with SKU and week fixed effects. The base elasticity captures shelf-price response. The promotional elasticity captures response to temporary discounts. The generator widened the shelf-price band (0.82–1.60 × list, with 3–7 revisions per year) specifically so that base elasticity is identifiable. An earlier version's narrow band left base price with too little variation (log-SD 0.027) to estimate reliably. The results appear in Table 5.5.

Using the category elasticities in Table 5.5, Frozen Foods is the only category where price is the cheaper buffer (exchange rate 1.23×; Figure 7.5). Its base elasticity is −3.77, so a small price move shifts a lot of demand. Beverages sits at the other extreme (0.21×): demand barely responds to price (−1.12), so a price buffer is expensive, and inventory is about five times cheaper. Snacks (0.65×), Dairy & Bakery (0.44×) and Grocery (0.40×) fall between. Elasticity also differs within categories by price tier (Figure 7.6). In Dairy & Bakery, for example, premium SKUs are far more elastic (|−1.69|) than value SKUs (|−0.29|). That points to tier-level rather than category-level routing.

![Figure 7.5: Price-inventory exchange rate by category](<../Presentation/assets_final/fig_exchange_rate.png>)

*Figure 7.5: Price-inventory exchange rate by category: inventory carrying cost ÷ price-buffer cost (log scale). Blue = inventory cheaper; red = price cheaper.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `Presentation/assets_final/fig_exchange_rate.png`

![Figure 7.6: Elasticity by price tier](<../Presentation/assets_final/fig_tier_elasticity.png>)

*Figure 7.6: Absolute price elasticity by category and price tier (solid = premium, faded = value).*
Source: synthetic Supply Brain dataset; team analysis. Image file: `Presentation/assets_final/fig_tier_elasticity.png`

**Status.** We label this analysis exploratory for three reasons:

1. **It is a static, category-level comparison,** not a policy tested in the twin. The twin does not yet simulate price-driven demand response.
2. **It uses the dataset's low 10.2% carrying rate,** which makes inventory look cheap. At an industry-typical 20–30%, more categories would move towards price.
3. **Price buffers carry their own risk.** Discount-driven demand can amplify upstream variability (Lee et al., 1997).

The direction of the finding is consistent with the joint pricing–inventory literature (Federgruen & Heching, 1999; Chen & Simchi-Levi, 2004): elastic, low-margin-sensitivity categories favour price buffers. The magnitudes need twin validation before they inform policy.

> **Key Takeaways**
> - The result that should most change what the sponsor does next: the deployed policy lifts customer-DC fill by 11.5 points and cuts lost margin by $2.15M at a 0.5% rise in landed cost. *Where* stock sits mattered more than *how much* stock there was.
> - The engineered signal that mattered most was weekly re-estimated, bias-corrected, seasonally indexed target-week demand. It, more than any optimiser choice, separated models that beat the baseline from models that did not. Pooling at regional DCs (α ≈ 0.6–1.0, with the netting credit removed) then delivered the customer-DC improvement. The open weakness is a 10.6-point fall in internal replenishment fill.
> - The evidence is multi-seed for the pooling and netting choices but single-seed for the deployed combination and the scenarios. The exchange rate is exploratory.


---

# Chapter 8: Deployment, Validation and Responsible AI

A stocking policy that nobody trusts will be overridden, and one that cannot be explained should be. This chapter describes how Supply Brain is deployed as a working prototype, how a planner interacts with it, and how we address fairness, explainability and risk. We set out the guardrails that keep a language model from changing inventory policy without a human's informed approval, and the monitoring that would tell a client when to intervene.

## 8.1 Deployment and Evaluation

### 8.1.1 Runtime pipeline

The production path is a single command, `run_meio_then_twin.py`. Every path and parameter comes from one shared configuration file, `scenario_config.json`, which the pipeline, the application and the agents all read. The pipeline runs four steps:

1. **Parameter resolution.** `meio_parameters.resolve()` reads the saved tuning evidence and applies the adoption rule from §4.2.2: a fill gain beyond two standard errors over at least five paired seeds, with no cost rise beyond two standard errors. It writes a receipt, `meio_parameters.json`, recording every decision, the settings each piece of evidence was run at, and the SHA-256 hash of every evidence file. A reviewer can therefore trace any number in the policy back to the experiment that justified it.
2. **Policy build.** Clark-Scarf v2 builds the as-of-2025-W52 policy and rolls it forward into a 52-week time-varying policy (`meio_policy_rolling.parquet`).
3. **Twin scoring.** The fast digital twin scores the policy from the common opening position and writes KPIs, service and cost summaries.
4. **Scenario arms (on request).** For an approved scenario, the same pipeline runs the prepared, unprepared and reactive arms (§7.7).

**Integration contract.** The policy file has one row per DC and SKU (and per week): `dc_id, sku_id, safety_stock, reorder_point, order_up_to_level, period`. This is the natural integration point with a client's ERP or advanced planning system. Supply Brain recommends the parameters, and the client's execution system places orders. No other coupling is needed for a pilot.

**Engineering for evaluation at scale.** The research pipeline took about nine to ten hours for a full tuning run. We rebuilt it on Polars, with a cached dense demand panel, one Monte Carlo pass shared across variants, an in-process vectorised twin and a prioritised process pool. Its documentation estimates 30–50 minutes for the same run. That made the five-seed sweeps in Chapter 7 feasible. Equivalence tests compare the optimized code against the original on covered slices. Two caveats are disclosed in Appendix C: the twin equivalence test currently fails before comparing anything, and three parameter tests were written for an earlier segmentation version.

### 8.1.2 The planner front end

A Streamlit application (`App/streamlit_app.py`) gives planners one place to work. It has six pages:

- **Dashboard:** network KPIs and module status.
- **SKU segmentation:** clusters, fingerprints and SHAP drivers.
- **Suggested service levels:** the service ladder.
- **Weekly MEIO allocation:** stocking parameters by DC, SKU and week.
- **Periodic alpha check:** re-validation of the pooling factor.
- **Simulation centre:** scenario agents, approval and results.

Its "Run all" button runs the alpha check and then the MEIO-then-twin pipeline. Segmentation and simulation launches write to a sandbox unless the user explicitly chooses to overwrite canonical outputs.

![Figure 8.1: Streamlit dashboard](<../Presentation/assets_final/screens/01_dashboard_top.png>)

*Figure 8.1: The Supply Brain dashboard: network KPIs and module status.*
Source: Supply Brain application, synthetic data. Image file: `Presentation/assets_final/screens/01_dashboard_top.png`

### 8.1.3 The agentic interface

Planners rarely think in override schemas; they think in sentences such as *"I foresee a decline in dairy demand by 25 percent due to the rise in the veganism movement, and shipping delays from protests for about six weeks."* A team of LangGraph agents turns such a sentence into a precise, auditable scenario. Its design follows the pattern of using LLMs as translators over trusted solvers (Li et al., 2023), with reasoning interleaved with tool calls (Yao et al., 2023). The workflow has five steps:

1. **Orchestration.** An orchestrator agent decomposes the request and delegates to specialists: demand, supply, service policy, inventory and economics.
2. **Authoring through tools only.** Specialists never edit data or code. They call 28 registered tools, which resolve ISO-week windows, check scope against master data, preview the size of each change and draft *overrides*. The override engine supports 19 measures:
   - demand volume and volatility
   - lead-time mean and spread, lane outages, plant capacity and schedule adherence
   - on-hand and in-transit stock
   - service targets and the pooling factor
   - five economic parameters
3. **Validation.** A validator agent checks overlaps, double counting and units. The tools generate a *scenario card*: the request, the hypothesis, every override with scope, window and rationale, and the size of each change as inputs (not outcomes).
4. **Human approval.** Nothing runs until a named person approves the card. Approval records the approver and the SHA-256 hash of the exact configuration approved, so a changed configuration cannot ride on an old approval.
5. **Execution and explanation.** MEIO re-plans and the twin simulates the arms. An analyst agent explains the results against the baseline. Receipts prove each override was actually applied, on the plan side, the actual side or both.

SCENARIO_0004 in §7.7 was authored exactly this way from the sentence above. The agents mapped it to two overrides: dairy demand × 0.75 for the full year, and lead-time mean × 1.10 on all 76 lanes for weeks 1–6. They sized it at −82,179 forecast units (−0.62% of network demand) before anything ran.

**Agents never choose the model.** Scenarios always run the MEIO configuration defined in the shared configuration. An agent can request a pooling-factor override as part of a scenario, but it cannot swap the optimiser or its tuning. The runtime supports three model providers: NVIDIA's API (the default profile), Anthropic's API, and a Claude Desktop bridge that needs no API key. It was live-tested on 9 October 2026 with a larger model as orchestrator and smaller models as workers. Thirty offline tests cover the tools and runtime.

![Figure 8.2: Simulation centre with the scenario agents](<../Presentation/assets_final/screens/11_sim_scenario_agents.png>)

*Figure 8.2: The simulation centre: in-app agent chat, approval and scenario results.*
Source: Supply Brain application, synthetic data. Image file: `Presentation/assets_final/screens/11_sim_scenario_agents.png`

### 8.1.4 Evaluation in production

In a client deployment we would evaluate the system in three stages:

1. **Shadow mode.** Run for one or two quarters, computing recommendations alongside the client's live policy and comparing both against realised demand, with no execution.
2. **Controlled pilot.** Apply the policy to the core clusters at two or three customer DCs, with matched control sites, and measure fill, lost sales, inventory and internal fill.
3. **Progressive rollout,** gated on the pilot. The rolling 2026 actuals would also provide the fresh holdout year our validation lacks.

## 8.2 Fairness, Explainability and Risk

### 8.2.1 Fairness: who gets served

Inventory policy has a fairness dimension that is easy to miss. It decides *which customers* see empty shelves. In this network, the customers served from customer DCs are convenience chains, smaller grocers and foodservice operators. They are typically the smaller accounts, and historically they received far worse service than the large bulk accounts served from regional DCs. We therefore assessed service equity across echelons and channels, behavioural segments and clusters (Table 8.1).

**Table 8.1: Service equity across echelons, segments and clusters**

| Subgroup | Baseline | Supply Brain | Gap change |
|---|---|---|---|
| **By channel, 2026 forward year (deployed configuration)** | | | |
| Bulk accounts (regional DCs) | 93.68% | 96.06% | |
| DSD accounts (customer DCs) | 78.67% | 90.19% | Bulk–DSD gap narrows from 15.0 to 5.9 pp |
| **By dataset segment, replayed 2025 (α = 0.2)** | | | |
| Fast-Moving | 93.57% | 94.66% | |
| Seasonal | 92.84% | 94.62% | |
| Intermediate (sporadic, hard to forecast) | 89.62% | 93.15% | Worst-to-best gap narrows from 3.9 to 1.5 pp |
| **By cluster, own fill at adopted α (replayed 2025, five seeds)** | | | |
| Core clusters (97% target) | – | 96.15–96.37% | |
| Long-tail clusters (90–95.7% targets) | – | 92.07–95.55% | All long-tail clusters at or above 90% |

Source: `output datasets/projections/baseline/run_kpis.json`; `output datasets/SCENARIO_0002_MEIO_CLARK_SCARF_V2/meio_policy/run_kpis.json`; `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/segment_breakdown_real_data.csv` and `segment_alpha_v5_real_data_summary.csv`; team analysis.

**Interpretation.**

- **The channel gap narrows sharply.** The bulk–DSD service gap falls from 15 points to 6 because pooling at regional DCs protects the customer DCs they feed. This is the most material equity improvement in the project.
- **The segment gaps narrow too.** The hardest-to-serve behavioural segment gains most.
- **The lower targets for long-tail clusters are deliberate.** They are a differentiated policy with an economic rationale, and they are transparent to planners. In outcome, every long-tail cluster still reaches at least 92% own fill at its adopted pooling level.
- **Two subgroup views are missing.** Fill by region and fill by individual banner were not computed separately. Both belong in the pilot's monitoring (Table 8.3).

### 8.2.2 Explainability: why is this SKU in this segment?

The segmentation drives every service target, so a planner must be able to ask why a SKU sits where it does. Because k-means has no supervised output, we fitted an XGBoost surrogate classifier (180 trees, depth 3) to reproduce the six cluster labels from the eight raw features. We then explained the surrogate with Tree SHAP (Lundberg & Lee, 2017; Lundberg et al., 2020). On a stratified 75/25 holdout the surrogate reproduces the clusters with 92.0% accuracy and 0.927 macro-F1, high enough for its explanations to be a faithful approximation of the cluster boundaries.

![Figure 8.3: Global SHAP importance of cluster-assignment drivers](<../output datasets/segmentation/report_artifacts/chart_shap_global.png>)

*Figure 8.3: Mean absolute SHAP contribution of each feature to cluster assignment.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `output datasets/segmentation/report_artifacts/chart_shap_global.png`

Globally, baseline forecast error is the strongest driver of assignment (mean |SHAP| 0.66), followed by promotional share (0.55) and base-demand CV (0.53) (Figure 8.3). By block, VARIABILITY (30.9%) and FORECASTABILITY (30.1%) dominate VALUE (19.7%) and SUPPLY_RISK (19.3%). The cluster-level view (Figure 8.4) independently confirms the reinterpretations in §6.6:

- "Long-Tail Supply-Risk" is driven by baseline forecast error (mean SHAP +3.01).
- "Long-Tail Erratic 2" is driven by lead-time CV (+3.37), where it is the *lowest* of all clusters.
- "Long-Tail Erratic 3" is driven by promotion-week forecast error (+3.62).
- Mid-Tier Core is driven by promotional share (+1.02).

![Figure 8.4: Cluster-by-feature SHAP heatmap](<../output datasets/segmentation/report_artifacts/chart_shap_cluster_heatmap.png>)

*Figure 8.4: SHAP importance by cluster and feature.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `output datasets/segmentation/report_artifacts/chart_shap_cluster_heatmap.png`

**How a planner would read one SKU.** Suppose a planner asks why a low-margin dairy SKU is in "Long-Tail Supply-Risk" with a 90% target. The per-SKU SHAP file (`shap_sku_values.csv`) would show that its high baseline WMAPE pushes it strongly into that cluster, while its margin features push only weakly. In plain words: *"This product is in the forecast-challenged tail. Its forecast misses by more than its own volume, and it earns little margin, so we protect it at 90% and recommend fixing the forecast before adding stock."*

**Limits we state plainly.** SHAP explains the surrogate, not the k-means distance itself. The surrogate is trained on raw features, while k-means used winsorised, standardised, block-weighted ones. Fidelity rests on one 75/25 split, with as few as four test SKUs in the smallest cluster. Most explanations are in-sample. And SHAP values describe association within the model, not causal effects (Lundberg et al., 2020): they do not show that improving a forecast would move a SKU to another cluster in the real business.

**Explaining the inventory policy itself.** For stocking parameters, explainability comes from transparency rather than post-hoc attribution. Every number traces to a formula (§7.1), a service level traceable to a cluster, a pooling factor traceable to a receipt and its evidence, and a scenario card that states the inputs in planner language.

### 8.2.3 Risks the sponsor should know

**Table 8.2: Risk register**

| Risk | Evidence | Likelihood / impact | Mitigation |
|---|---|---|---|
| Upstream internal replenishment degrades | −10.6 pp on the forward year; under every model tested | High / high | Add an internal-fill floor to the parameter-adoption rule. Fix the twin's parent hold-back and allocation-share logic before relying on the result. Pilot with upstream monitoring. |
| Overfitting to one validation year | Parameters tuned and scored on 2025; seasonal index includes 2025 | High / medium | Shadow-mode holdout on 2026 actuals before rollout. Re-run the adoption rule quarterly. |
| Deployed configuration never back-tested as a unit | α 0.6 + per-cluster α + quadrature run once (forward, one seed) | Certain / medium | Multi-seed backtest of the exact configuration as the first pilot task |
| Synthetic-to-real transfer | Shared structure between generator, forecast and twin; weak baseline | Certain / medium–high | Treat figures as directional. Re-derive on client data. |
| LLM misinterprets a request (wrong scope, unit or window) | Free-text input | Medium / medium | Tool-only authoring, validator agent, size-of-change preview, scenario card, named human approval with config hash, application receipts |
| Model-provider dependence or outage | Three profiles (NVIDIA, Anthropic, Desktop) | Low / low | Provider-agnostic runtime; the CLI works without agents |
| Segment knife edges | Long-Tail Erratic 2 at 3.004% revenue vs a 3% cap | Medium / low | Hysteresis band on ladder thresholds; planner review of boundary SKUs |
| Working-capital increase | +$1.10M average inventory | Certain / medium | Finance sign-off on the service-for-capital trade. Track cycle versus safety stock separately. |

Source: Chapters 4 and 7; `ASSUMPTIONS_AUDIT.md`; team analysis.

**Governance mapping.** Against the NIST AI Risk Management Framework (National Institute of Standards and Technology, 2023), the controls map as follows:

- **Govern:** a named approver for every scenario, and agents unable to change the model.
- **Map:** assumptions audit, scenario cards stating scope and size, and limitations stated in every report.
- **Measure:** multi-seed paired tests, receipts with evidence hashes, and SHAP fidelity metrics.
- **Manage:** the monitoring triggers in §8.3, and sandboxed runs by default.

## 8.3 Monitoring Plan

Once deployed, the system must notice when the world it was tuned on has changed. Table 8.3 lists the signals we would monitor, the threshold that triggers action, and the action.

**Table 8.3: Monitoring plan**

| Signal | Metric | Threshold | Action |
|---|---|---|---|
| Customer service at DSD sites | Weekly CDC fill rate | < 88% for two consecutive weeks | Planner review of affected SKUs; check regional pooling |
| Upstream health | Internal replenishment fill | < 85% for four weeks | Upstream audit of RDC order-up-to levels and plant allocation; re-test pooling (higher α raised internal fill in validation, Table 7.5) |
| Forecast bias | 13-week bias factor per SKU group | Outside 0.90–1.10 | Escalate to demand planning; review forecast process |
| Demand drift | Trailing CV versus calibration CV | > 25% relative change | Re-estimate uncertainty; re-run the service-level check |
| Lead-time drift | Observed ÷ planned lead time on settled shipments | > 1.05 and > 3 SE (the reactive arm's own rule) | Re-plan lanes; open a supplier or carrier review |
| Segment migration | Share of SKUs changing cluster at quarterly re-cluster | > 10% | Re-validate the service ladder and per-cluster α |
| Pooling validity | Quarterly α check (paired five-seed rule) | Current α fails the rule | Revert to the reference α; investigate |
| Cost drift | Total landed cost versus plan | > 3% over a quarter | Finance and planning review |
| Agent quality | Share of scenario cards rejected or edited at approval | > 20% | Review prompts, tools and validator rules |
| Subgroup equity | Fill by region and by banner | Any subgroup > 5 pp below network average | Targeted review |

Source: team analysis.

> **Key Takeaways**
> - The biggest deployment risk is the fall in upstream internal replenishment fill. Mitigate it with an internal-fill floor in the adoption rule, a fix to the twin's hold-back and allocation logic, and a monitored pilot before scaling.
> - Responsible AI is built into the workflow, not added afterwards. Agents author only validated overrides, never the model; a named human approves a hashed configuration; receipts prove what ran; SHAP explains segment assignments within stated limits; and the policy narrows the service gap between large and small customers.


---

# Chapter 9: Business Impact and Recommendations

The analysis is only worth what the sponsor and its clients can do with it. This chapter translates the results into actions, each with a quantified impact where the evidence allows, an owner and a timeline. The numbers come from a synthetic business, so they show the scale and direction a client should expect, not commitments. Each one must be re-derived on client data during a pilot.

## 9.1 Recommendations and Business Impact

**Table 9.1: Recommendations and quantified impact**

| # | Recommendation | Evidence | Expected impact (synthetic basis) |
|---|---|---|---|
| R1 | Replace the blanket service target with the segment ladder | §6.6; Table 6.5 | 267 core SKUs (92% of revenue) at 97%; 47 at 95.7%; 186 long-tail SKUs at 90%. Protection is concentrated where margin is. |
| R2 | Adopt echelon-aware stocking (Clark-Scarf v2, netting off, α ≈ 0.6–1.0) with weekly re-estimation | §7.5–7.6; Table 7.8 | +4.0 pp customer fill; +11.5 pp customer-DC fill; −$2.15M lost margin; +$0.19M (+0.5%) landed cost; +$1.10M working capital |
| R3 | Size every week on bias-corrected, seasonally indexed demand, not an annual average | §7.4–7.5; Figure 7.4 | Seasonal peak-week fill 78.4% → 88.6%; precondition for every other gain |
| R4 | Fix the forecast before buying stock for the forecast-challenged tail | §6.6; Figure 6.5 | 98 SKUs (clusters 3 and 5) with average forecast error above their own volume; better forecasts protect them without new capital |
| R5 | Add an upstream-health guard before scaling | §7.6; Table 8.2 | Prevents a policy that serves customers by eroding internal replenishment (−10.6 pp today) |
| R6 | Pilot price as the buffer only where it is cheaper than stock | §7.8; Figure 7.5 | Frozen Foods (18% of revenue) is the only category where price is cheaper (1.23×); elsewhere keep inventory buffers |
| R7 | Route every disruption what-if through the agent and twin sandbox, with a named approver | §7.7, §8.1.3 | A planner's sentence becomes a reviewed scenario card and minutes of compute, with an audit trail |

Source: Chapters 6, 7 and 8; team analysis.

### R1. Differentiate service by segment

**Action.** Retire the blanket target (the charter describes a typical figure near 99%) and the dataset's legacy three-tier ladder (98% / 95% / 90%). Set targets from the six-cluster ladder: 97% for Flagship Core and Mid-Tier Core, 95.7% for Long-Tail Erratic 2, and 90% for the three remaining long-tail clusters.

**Impact.** Protection is concentrated on the 267 SKUs that earn 92.2% of revenue and 92.7% of margin. The 186 SKUs that earn under 5% of revenue get a deliberately leaner target. On replayed 2025 demand, five of six clusters met or came within about three points of their targets. The one that did not, Long-Tail Erratic 2, was fixed by positioning, not by a higher target (§6.6, §7.5).

**Caveat.** Because the synthetic cost inputs make the economic critical ratio almost flat, the ladder is mainly strategic. It should be recalibrated with the client's real stockout penalties, substitution rates and, for perishable lines, expiry costs. Add a hysteresis band around the revenue-share thresholds so that a SKU does not swing between 90% and 95.7% because of a 0.004-point change in revenue share.

### R2. Position buffers with echelon-aware MEIO

**Action.** Size regional-DC buffers with their downstream customer DCs in view, using the pooling factor (α = 0.6 portfolio default, with per-cluster values up to 1.0). Remove the assumption that a regional DC can count its customer DCs' safety stock as its own (netting discount 0).

**Impact.** On the 2026 forward year this is the largest single lever:

- **Customer fill** rises by 4.0 points, from 91.0% to 95.0%.
- **Fill at customer DCs** rises by 11.5 points, from 78.7% to 90.2%. These are the convenience, smaller-grocery and foodservice accounts most exposed today.
- **Lost margin** falls by $2.15M.
- **Total landed cost** rises only $0.19M. The extra transport is the freight on about 568,000 more units delivered. At the 2025 average realised price of about $8.60 a unit, those units represent on the order of $4.9M of revenue that is now captured rather than lost. The margin on that revenue is the $2.15M above and is already netted into landed cost, so the two figures should not be added together.

**The trade.** Average inventory value rises by $1.10M, mostly cycle stock. At the dataset's 10.2% carrying rate that costs about $0.11M a year, already inside the reported holding cost. At an industry-typical 25% it would cost about $0.28M, still well below the $2.15M margin recovered. Finance should sign off this service-for-capital trade explicitly.

### R3. Re-estimate demand every week

**Action.** Replace the annual average with a weekly target-week estimate. It should blend the bias-corrected forecast (60%) with a de-seasonalised trailing level times a seasonal index (40%), and size uncertainty from blended 13- and 52-week CVs (§7.1).

**Impact.** This is the change that turned every below-baseline model in §7.4 into an above-baseline one. It lifts Seasonal-segment fill in the peak week from 78.4% to 88.6%. It is also the cheapest change to adopt, because it needs no new optimiser, only a planning cadence and a calculation.

### R4. Fix the forecast before buying stock for the tail

**Action.** For the 81 SKUs in "Long-Tail Supply-Risk" (average baseline WMAPE 1.85) and the 17 in "Long-Tail Erratic 3" (average promotion-week WMAPE about 51), start with a forecast review, not a sourcing review or a safety-stock increase. The review should cover sparse customer-level series, the promotion-lift method, and whether promotions on these SKUs earn their keep.

**Impact.** These 98 SKUs carry 2.4% of revenue. Safety stock sized on an error larger than the item's own volume is mostly wasted capital. The evidence also shows that their label is misleading: their lead-time variability is no different from the rest of the portfolio (§6.6, §8.2). Use the 90%-target clusters as candidates for assortment, order-frequency or make-to-order decisions, not automatic delisting.

### R5. Guard upstream health before scaling

**Action.** Add an internal replenishment fill floor (for example 85%) to the parameter-adoption rule, so that no pooling or service change is adopted if it pushes internal fill below the floor. In parallel, correct the two twin mechanics the audit implicates: parent safety-stock hold-back and the double-applied plant allocation share. Then re-measure.

**Impact.** This protects the network's resilience to upstream shocks. Today's 10.6-point fall in internal fill is tolerable in a stable year but risky in a disruption. Until it is resolved, the policy should be piloted with upstream monitoring (Table 8.3).

### R6. Use price as a buffer only where it is cheaper

**Action.** Run a controlled price-buffer pilot in Frozen Foods, the only category where the exploratory exchange rate favours price (1.23×). Use targeted discounts to absorb demand shortfalls instead of extra safety stock. Keep inventory buffers in Beverages (0.21×), Grocery, Dairy & Bakery and Snacks.

**Impact.** Not yet quantified in the twin. The direction is supported by joint pricing–inventory theory, but price-based buffering can amplify upstream variability, so a pilot should measure order variance as well as cost. Within categories, elasticity differs sharply by price tier, so routing should eventually be by tier, not category.

### R7. Make scenario testing a governed routine

**Action.** Route every material what-if, such as a demand shift, a lane disruption, a capacity loss or a service-level change, through the agent and twin sandbox. Require a named approver for every scenario card, and keep receipts.

**Impact.**

- **Efficiency:** a planner's sentence becomes a validated, sized scenario and a three-arm comparison in one sitting.
- **Quality:** every override is scoped, sized and justified before it runs.
- **Governance:** approvals are tied to a hashed configuration, which is the control a client's risk function will ask for.

## 9.2 Implementation Roadmap

**Table 9.2: Implementation roadmap**

| Phase | Recommendation(s) | Owner | Timeline | Expected impact / exit criterion |
|---|---|---|---|---|
| 0. Mobilise | All | Accenture engagement lead with client S&OP head | Weeks 0–4 | Client data mapped to the 26-table contract; policy file interface agreed with the ERP or planning team |
| 1. Validate | R2, R5 | Accenture data science team | Months 1–3 | Multi-seed backtest of the exact deployed configuration on client history; twin hold-back and allocation fixes; internal-fill floor added; numbers re-derived |
| 2. Quick wins | R3, R4 | Client demand-planning lead | Months 1–3 | Weekly re-estimation live in shadow mode; forecast review of the forecast-challenged tail |
| 3. Pilot | R1, R2 | Client S&OP head; site planners | Months 3–6 | Core clusters at 2–3 customer DCs with matched controls; target: customer-DC fill up ≥ 5 pp at ≤ 2% landed-cost change and internal fill ≥ 85% |
| 4. Scenario routine | R7 | Planning centre of excellence | Months 3–6 | Agent sandbox in use for monthly S&OP what-ifs; approval and receipt audit in place |
| 5. Price pilot | R6 | Revenue-growth-management and supply-chain leads | Months 6–9 | Frozen Foods price-buffer trial; cost, service and order-variance measured against control |
| 6. Scale and monitor | All | Client supply-chain leadership; finance | Months 6–12 | Network rollout gated on pilot results; monitoring plan (Table 8.3) live; quarterly re-segmentation and α check |

Source: team analysis.

> **Key Takeaways**
> - Act first on echelon-aware stocking with weekly re-estimation, piloted on the core clusters at the customer DCs where the service gap is widest. It recovers about $2M of margin a year at roughly flat landed cost.
> - Pair it with the upstream-health guard and a forecast review of the tail. Treat the price-buffer idea as a targeted pilot, not a portfolio policy.


---

# Chapter 10: Limitations and Future Scope

A project that overstates its certainty deserves less trust than one that is clear about its edges. Supply Brain has real edges. Some are inherent to a synthetic-first build, some to the time available, and some are weaknesses in the model we did not resolve. We group them below, then describe the work that would most increase the value of what exists.

## 10.1 Data limitations

- **Synthetic data with one realisation of history.** No real company's figures are in the dataset. The world's random seeds are fixed, so there is one path of demand history. Our seeds vary simulation noise, not demand. The structure and the methods transfer; the specific numbers do not.
- **One validation year.** The published forecast exists only for 2025 and 2026, so every real-demand result rests on 2025 alone. Parameters were tuned and scored on that same year, and the seasonal index used to size 2025 includes 2025 itself. The real-demand gains are therefore optimistic, and none has faced a fresh holdout.
- **A weak baseline by construction.** The dataset's baseline policy targets mediocre service (customer fill 90–96%). It sizes regional DCs on bulk demand only and leaves 358 RDC–SKU pairs without a policy. Any optimiser has room to beat it. A client's real policy may be stronger.
- **Shared structure.** The generator, the forecast, the segmentation inputs and the twin share one view of how the network works. That flatters any model built inside it.
- **Missing F&B realities.** Shelf life never binds, write-offs are empty, every facility is ambient, warehouse and lane capacity never bind, and lateral and failover lanes carry no flow. The defining constraints of fresh and frozen food logistics are absent.
- **Charter KPI not measurable.** Write-off minimisation, one of the charter's three named KPIs, cannot be evaluated in this dataset.

## 10.2 Method and model limitations

- **The deployed configuration is untested as a unit.** Portfolio α 0.6 with per-cluster overrides, quadrature sizing and the 52-week roll-forward has been run once, on the forward year, at one seed.
- **Per-cluster pooling was tuned one cluster at a time,** each with the others held at α = 0.6, then adopted jointly. Four of six winners sit on the α = 1.0 boundary, so the optimum may lie outside the tested formulation.
- **Statistical evidence measures simulator noise only.** The five-seed standard errors (about 0.01–0.03 points) capture lead-time and Monte Carlo noise but not demand uncertainty. The adoption rule uses no small-sample t-correction and no correction for multiple comparisons, and it picks the best point on a grid.
- **Different sizing in evidence and production.** The tuning evidence used Monte Carlo demand-during-lead-time; production uses quadrature. The two agree to within 0.5% in tests, but they are not identical.
- **Internal replenishment fill falls** by 9–11 points under every configuration tested, and the cause is not fully identified.
- **Simplified upstream sizing.** Plant-DC safety stock uses throughput as a stand-in for variability rather than a measured σ.
- **Demand assumptions.** Weekly demand is treated as independent, uncertainty as constant over the horizon, and the CV of actuals as forecast error. Seasonality and promotions are partly counted as noise.
- **Economics.** The 10.2% carrying rate is below industry norms, unit cost is identical at every echelon, and transport is linear per unit. Cost comparisons are therefore mostly transport-volume signals, and holding-cost differences between echelons are muted.
- **Segmentation.** Silhouettes are low (the data is a continuum). The supply-risk block encodes network geography rather than realised variability. The service ladder relies on revenue-share thresholds with a knife edge. Archetype names are rule-generated and partly misleading.
- **Explainability.** The SHAP surrogate is validated on one split, explains raw rather than transformed features, and is mostly in-sample.

## 10.3 Simulation and engineering limitations

- **Twin mechanics that may penalise upstream buffers.** The parent hold-back rule never releases plant-DC safety stock, and withholds regional pooled stock from the customer DCs it is meant to protect. Plant allocation shares are applied twice for dual-sourced SKUs, which lowers plant-DC internal fill for those SKUs.
- **Model–twin mismatch.** The optimiser sizes on cumulative lead time along the fastest lane, while the twin replenishes each hop on its own lane and rounds transit up to whole days.
- **The twin is a digital model, not a live twin** in the strict sense (Kritzinger et al., 2018). It has no data feed from operations.
- **Test coverage.** The twin equivalence test currently fails before comparing anything, three parameter tests still assume an earlier segmentation version, and the override engine's own tests do not run in the current tree. The override, outage and capacity paths exist only in the fast twin.
- **Scenarios are single-seed working examples.** The value of preparing in advance changed sign between runs.
- **Charter scope not built.** Postponement was not modelled, and the price-inventory exchange rate is static and outside the twin.

## 10.4 Future scope

In order of value to a client engagement:

1. **Client-data validation and a fresh holdout.** Run the pipeline on anonymised client history, and use 2026 actuals (synthetic or real) as a true out-of-sample year.
2. **Back-test the deployed configuration as a unit,** over multiple seeds and, ideally, multiple regenerated demand histories so that the statistics capture demand uncertainty.
3. **Resolve the internal-fill gap.** Fix the twin's hold-back and allocation logic, replace the plant-DC throughput proxy with a measured σ, and add internal fill as a constraint in parameter adoption.
4. **Joint optimisation of per-cluster pooling and service levels,** for example by Bayesian optimisation over the twin, with the grid extended beyond its current boundary.
5. **Perishability and the cold chain.** Introduce realistic shelf lives, expiry costs and refrigerated capacity, so that write-offs become measurable and the newsvendor ladder becomes genuinely economic.
6. **Price in the loop.** Simulate price-driven demand in the twin, so the exchange rate becomes a tested policy and potentially a bandit-learned buffer mix per segment (Russo et al., 2018).
7. **Richer network options.** Lateral transshipment, failover lanes and postponement at plant DCs (Lee & Tang, 1997).
8. **Operational coupling.** Connect the twin to live inventory and order feeds, turning a digital model into a digital shadow and eventually a twin.

> **Key Takeaways**
> - The limitation most likely to matter in an extension is that the deployed configuration has no out-of-sample, multi-seed test, and that all real-demand evidence comes from one year that was also used for tuning.
> - The highest-value next steps are client-data validation with a fresh holdout, a fix for the internal-fill gap, and joint tuning of pooling and service levels. Each turns a promising prototype into a defensible client offering.


---

# Chapter 11: Conclusion

We set out to answer a question that CPG planners face every week: *where should inventory buffers sit, and how much protection should each SKU receive, when value, demand uncertainty, forecast quality and replenishment risk differ across the portfolio?*

On **how much**, the answer is that protection should follow economics and behaviour, not a blanket rule. In our network, two core segments hold half the SKUs but over 92% of revenue and margin, and they warrant a 97% cycle service level. Four long-tail segments warrant 90–96%. Some of them need a better forecast far more than they need more stock. A stability-selected segmentation on value, variability, forecastability and supply-risk evidence, explained through SHAP, gives planners classes they can trust and question. The resulting service ladder is mainly strategic in this dataset, because its cost inputs are flat. It should become genuinely economic once a client's real penalty and expiry costs are available.

On **where**, the answer is that a regional DC should hold protection for the customer DCs it feeds, rather than being sized as if they did not exist. It should also stop assuming that its children's stock is instantly its own. Pooling at regional DCs, combined with weekly re-estimation of seasonal, bias-corrected demand, is what moved the result. On the 2026 forward year, customer fill rises from 91.0% to 95.0%. Fill at customer DCs, which serve the smaller DSD accounts, rises from 78.7% to 90.2%. Lost margin falls by $2.15M at a 0.5% rise in landed cost. On replayed 2025 demand, the same approach beat the incumbent policy in every one of five paired simulation runs. The most transferable lesson is negative as much as positive: no optimiser beat the baseline until demand was re-estimated for each target week.

The answer comes with trade-offs and limits that a sponsor should weigh. Working capital rises by about $1.1M. Internal replenishment between warehouses weakens by about ten points, a problem we traced in part to simulator mechanics but did not resolve. All of the evidence comes from a synthetic business and a single validation year, and the exact configuration now deployed has been run once rather than back-tested as a unit. The direction of the findings is well supported. Their magnitude must be re-established on client data.

Finally, the project shows that this kind of analysis can be made usable. A planner can describe a disruption in a sentence. Governed agents translate it into a precise, sized and approved scenario. The same optimiser and twin that produced the headline results then test it. In Supply Brain, the language model translates and the verified models decide, and a named person stays accountable for every run. Together, differentiated service, echelon-aware positioning, shared-physics testing and a human-gated interface form a framework that Accenture can take to CPG clients, beginning with a pilot at the customer DCs where today's service gap is widest.


---

# References

Arrow, K. J., Harris, T., & Marschak, J. (1951). Optimal inventory policy. *Econometrica, 19*(3), 250–272. https://doi.org/10.2307/1906813

Arthur, D., & Vassilvitskii, S. (2007). k-means++: The advantages of careful seeding. In *Proceedings of the Eighteenth Annual ACM-SIAM Symposium on Discrete Algorithms* (pp. 1027–1035). Society for Industrial and Applied Mathematics. https://dl.acm.org/doi/10.5555/1283383.1283494

Axsäter, S. (2015). *Inventory control* (3rd ed.). Springer. https://doi.org/10.1007/978-3-319-15729-0

Bijmolt, T. H. A., van Heerde, H. J., & Pieters, R. G. M. (2005). New empirical generalizations on the determinants of price elasticity. *Journal of Marketing Research, 42*(2), 141–156. https://doi.org/10.1509/jmkr.42.2.141.62296

Billington, C., Callioni, G., Crane, B., Ruark, J. D., Rapp, J. U., White, T., & Willems, S. P. (2004). Accelerating the profitability of Hewlett-Packard's supply chains. *Interfaces, 34*(1), 59–72. https://doi.org/10.1287/inte.0103.0054

Burgin, T. A. (1975). The gamma distribution and inventory control. *Operational Research Quarterly, 26*(3), 507–525. https://doi.org/10.1057/jors.1975.110

Cattani, K. D., Jacobs, F. R., & Schoenfelder, J. (2011). Common inventory modeling assumptions that fall short: Arborescent networks, Poisson demand, and single-echelon approximations. *Journal of Operations Management, 29*(5), 488–499. https://doi.org/10.1016/j.jom.2010.11.008

Chen, T., & Guestrin, C. (2016). XGBoost: A scalable tree boosting system. In *Proceedings of the 22nd ACM SIGKDD International Conference on Knowledge Discovery and Data Mining* (pp. 785–794). Association for Computing Machinery. https://doi.org/10.1145/2939672.2939785

Chen, X., & Simchi-Levi, D. (2004). Coordinating inventory control and pricing strategies with random demand and fixed ordering cost: The finite horizon case. *Operations Research, 52*(6), 887–896. https://doi.org/10.1287/opre.1040.0127

Clark, A. J., & Scarf, H. (1960). Optimal policies for a multi-echelon inventory problem. *Management Science, 6*(4), 475–490. https://doi.org/10.1287/mnsc.6.4.475

de Kok, T., Grob, C., Laumanns, M., Minner, S., Rambau, J., & Schade, K. (2018). A typology and literature review on stochastic multi-echelon inventory models. *European Journal of Operational Research, 269*(3), 955–983. https://doi.org/10.1016/j.ejor.2018.02.047

Eppen, G. D. (1979). Note—Effects of centralization on expected costs in a multi-location newsboy problem. *Management Science, 25*(5), 498–501. https://doi.org/10.1287/mnsc.25.5.498

Eruguz, A. S., Sahin, E., Jemai, Z., & Dallery, Y. (2016). A comprehensive survey of guaranteed-service models for multi-echelon inventory optimization. *International Journal of Production Economics, 172*, 110–125. https://doi.org/10.1016/j.ijpe.2015.11.017

Ettl, M., Feigin, G. E., Lin, G. Y., & Yao, D. D. (2000). A supply network model with base-stock control and service requirements. *Operations Research, 48*(2), 216–232. https://doi.org/10.1287/opre.48.2.216.12376

Farasyn, I., Humair, S., Kahn, J. I., Neale, J. J., Rosen, O., Ruark, J., Tarlton, W., Van de Velde, W., Wegryn, G., & Willems, S. P. (2011). Inventory optimization at Procter & Gamble: Achieving real benefits through user adoption of inventory tools. *Interfaces, 41*(1), 66–78. https://doi.org/10.1287/inte.1100.0546

Federgruen, A., & Heching, A. (1999). Combined pricing and inventory control under uncertainty. *Operations Research, 47*(3), 454–475. https://doi.org/10.1287/opre.47.3.454

Fisher, M. L. (1997). What is the right supply chain for your product? *Harvard Business Review, 75*(2), 105–116. https://hbr.org/1997/03/what-is-the-right-supply-chain-for-your-product

Flores, B. E., & Whybark, D. C. (1986). Multiple criteria ABC analysis. *International Journal of Operations & Production Management, 6*(3), 38–46. https://doi.org/10.1108/eb054765

Fu, M. C. (2002). Optimization for simulation: Theory vs. practice. *INFORMS Journal on Computing, 14*(3), 192–215. https://doi.org/10.1287/ijoc.14.3.192.113

Gallego, G., & van Ryzin, G. (1994). Optimal dynamic pricing of inventories with stochastic demand over finite horizons. *Management Science, 40*(8), 999–1020. https://doi.org/10.1287/mnsc.40.8.999

Glasserman, P., & Yao, D. D. (1992). Some guidelines and guarantees for common random numbers. *Management Science, 38*(6), 884–908. https://doi.org/10.1287/mnsc.38.6.884

Graves, S. C., & Willems, S. P. (2000). Optimizing strategic safety stock placement in supply chains. *Manufacturing & Service Operations Management, 2*(1), 68–83. https://doi.org/10.1287/msom.2.1.68.23267

Hennig, C. (2007). Cluster-wise assessment of cluster stability. *Computational Statistics & Data Analysis, 52*(1), 258–271. https://doi.org/10.1016/j.csda.2006.11.025

Hubert, L., & Arabie, P. (1985). Comparing partitions. *Journal of Classification, 2*(1), 193–218. https://doi.org/10.1007/BF01908075

Hyndman, R. J., & Koehler, A. B. (2006). Another look at measures of forecast accuracy. *International Journal of Forecasting, 22*(4), 679–688. https://doi.org/10.1016/j.ijforecast.2006.03.001

Ivanov, D., & Dolgui, A. (2021). A digital supply chain twin for managing the disruption risks and resilience in the era of Industry 4.0. *Production Planning & Control, 32*(9), 775–788. https://doi.org/10.1080/09537287.2020.1768450

Jordon, J., Szpruch, L., Houssiau, F., Bottarelli, M., Cherubin, G., Maple, C., Cohen, S. N., & Weller, A. (2022). *Synthetic data—What, why and how?* (arXiv:2205.03257). arXiv. https://doi.org/10.48550/arXiv.2205.03257

Khouja, M. (1999). The single-period (news-vendor) problem: Literature review and suggestions for future research. *Omega, 27*(5), 537–553. https://doi.org/10.1016/S0305-0483(99)00017-1

Kolassa, S., & Schütz, W. (2007). Advantages of the MAD/Mean ratio over the MAPE. *Foresight: The International Journal of Applied Forecasting*, (6), 40–43. https://ideas.repec.org/a/for/ijafaa/y2007i6p40-43.html

Kritzinger, W., Karner, M., Traar, G., Henjes, J., & Sihn, W. (2018). Digital twin in manufacturing: A categorical literature review and classification. *IFAC-PapersOnLine, 51*(11), 1016–1022. https://doi.org/10.1016/j.ifacol.2018.08.474

Lattimore, T., & Szepesvári, C. (2020). *Bandit algorithms*. Cambridge University Press. https://doi.org/10.1017/9781108571401

Law, A. M. (2015). *Simulation modeling and analysis* (5th ed.). McGraw-Hill Education.

Lee, H. L., Padmanabhan, V., & Whang, S. (1997). Information distortion in a supply chain: The bullwhip effect. *Management Science, 43*(4), 546–558. https://doi.org/10.1287/mnsc.43.4.546

Lee, H. L., & Tang, C. S. (1997). Modelling the costs and benefits of delayed product differentiation. *Management Science, 43*(1), 40–53. https://doi.org/10.1287/mnsc.43.1.40

Li, B., Mellou, K., Zhang, B., Pathuri, J., & Menache, I. (2023). *Large language models for supply chain optimization* (arXiv:2307.03875). arXiv. https://doi.org/10.48550/arXiv.2307.03875

Lloyd, S. (1982). Least squares quantization in PCM. *IEEE Transactions on Information Theory, 28*(2), 129–137. https://doi.org/10.1109/TIT.1982.1056489

Lundberg, S. M., Erion, G., Chen, H., DeGrave, A., Prutkin, J. M., Nair, B., Katz, R., Himmelfarb, J., Bansal, N., & Lee, S.-I. (2020). From local explanations to global understanding with explainable AI for trees. *Nature Machine Intelligence, 2*(1), 56–67. https://doi.org/10.1038/s42256-019-0138-9

Lundberg, S. M., & Lee, S.-I. (2017). A unified approach to interpreting model predictions. In I. Guyon, U. von Luxburg, S. Bengio, H. Wallach, R. Fergus, S. Vishwanathan, & R. Garnett (Eds.), *Advances in neural information processing systems 30* (pp. 4765–4774). Curran Associates. https://proceedings.neurips.cc/paper_files/paper/2017/file/8a20a8621978632d76c43dfd28b67767-Paper.pdf

National Institute of Standards and Technology. (2023). *Artificial intelligence risk management framework (AI RMF 1.0)* (NIST AI 100-1). https://doi.org/10.6028/NIST.AI.100-1

Petropoulos, F., Apiletti, D., Assimakopoulos, V., Babai, M. Z., Barrow, D. K., Ben Taieb, S., Bergmeir, C., Bessa, R. J., Bijak, J., Boylan, J. E., Browell, J., Carnevale, C., Castle, J. L., Cirillo, P., Clements, M. P., Cordeiro, C., Cyrino Oliveira, F. L., De Baets, S., Dokumentov, A., . . . Ziel, F. (2022). Forecasting: Theory and practice. *International Journal of Forecasting, 38*(3), 705–871. https://doi.org/10.1016/j.ijforecast.2021.11.001

Ramanathan, R. (2006). ABC inventory classification with multiple-criteria using weighted linear optimization. *Computers & Operations Research, 33*(3), 695–700. https://doi.org/10.1016/j.cor.2004.07.014

Rousseeuw, P. J. (1987). Silhouettes: A graphical aid to the interpretation and validation of cluster analysis. *Journal of Computational and Applied Mathematics, 20*, 53–65. https://doi.org/10.1016/0377-0427(87)90125-7

Russo, D. J., Van Roy, B., Kazerouni, A., Osband, I., & Wen, Z. (2018). A tutorial on Thompson sampling. *Foundations and Trends in Machine Learning, 11*(1), 1–96. https://doi.org/10.1561/2200000070

Silver, E. A., Pyke, D. F., & Thomas, D. J. (2017). *Inventory and production management in supply chains* (4th ed.). CRC Press. https://doi.org/10.1201/9781315374406

Simpson, K. F., Jr. (1958). In-process inventories. *Operations Research, 6*(6), 863–873. https://doi.org/10.1287/opre.6.6.863

Syntetos, A. A., Boylan, J. E., & Croston, J. D. (2005). On the categorization of demand patterns. *Journal of the Operational Research Society, 56*(5), 495–503. https://doi.org/10.1057/palgrave.jors.2601841

Teunter, R. H., Babai, M. Z., & Syntetos, A. A. (2010). ABC classification: Service levels and inventory costs. *Production and Operations Management, 19*(3), 343–352. https://doi.org/10.1111/j.1937-5956.2009.01098.x

Thompson, W. R. (1933). On the likelihood that one unknown probability exceeds another in view of the evidence of two samples. *Biometrika, 25*(3–4), 285–294. https://doi.org/10.1093/biomet/25.3-4.285

Trapero, J. R., Kourentzes, N., & Fildes, R. (2015). On the identification of sales forecasting models in the presence of promotions. *Journal of the Operational Research Society, 66*(2), 299–307. https://doi.org/10.1057/jors.2013.174

Tyworth, J. E., & O'Neill, L. (1997). Robustness of the normal approximation of lead-time demand in a distribution setting. *Naval Research Logistics, 44*(2), 165–186. https://doi.org/10.1002/(SICI)1520-6750(199703)44:2<165::AID-NAV2>3.0.CO;2-7

van Kampen, T. J., Akkerman, R., & van Donk, D. P. (2012). SKU classification: A literature review and conceptual framework. *International Journal of Operations & Production Management, 32*(7), 850–876. https://doi.org/10.1108/01443571211250112

Wei, J., Wang, X., Schuurmans, D., Bosma, M., Ichter, B., Xia, F., Chi, E., Le, Q. V., & Zhou, D. (2022). Chain-of-thought prompting elicits reasoning in large language models. In *Advances in Neural Information Processing Systems 35* (pp. 24824–24837). Curran Associates. https://proceedings.neurips.cc/paper/2022/hash/9d5609613524ecf4f15af0f7b31abca4-Abstract-Conference.html

Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., & Cao, Y. (2023). ReAct: Synergizing reasoning and acting in language models. In *The Eleventh International Conference on Learning Representations (ICLR 2023)*. https://openreview.net/forum?id=WE_vluYUL-X

Zipkin, P. H. (2000). *Foundations of inventory management*. McGraw-Hill.

---

# Appendices

## Appendix A: Use of AI Tools Statement

Generative-AI tools were used in this project in two distinct roles. First, **as part of the product**: the Supply Brain scenario agents call large language models (through NVIDIA, Anthropic or Claude Desktop profiles) to translate planners' requests into scenario overrides. That use is described in Chapter 8 and governed by a human approval gate. Second, **as an aid to the team's own work**, as listed below. Every number in this report was taken from the project's own code outputs, and every reference was checked against its publisher or indexing record (Crossref, publisher pages or arXiv) before inclusion.

| Tool used | Purpose | Verified by team? |
|---|---|---|
| Claude (Anthropic), via Claude Code | Code scaffolding, debugging and refactoring (including the Polars port of the MEIO pipeline and digital twin); writing project documentation and the line-by-line assumptions audit | Yes |
| Claude (Anthropic), via Claude Code | Searching for and verifying literature: checking authors, years, volumes, pages and DOIs of candidate references against Crossref and publisher records | Yes |
| Claude (Anthropic), via Claude Code | Structuring and drafting report text from the team's analyses, outputs and documentation; grammar and clarity editing | Yes |
| LLMs in the Supply Brain agent layer (NVIDIA Build API, Anthropic API, Claude Desktop) | Product feature: translating planner requests into scenario overrides and explaining results | Yes; every scenario requires named human approval |

## Appendix B: Additional Tables and Figures

### B.1 Per-cluster own fill versus target service level (replayed 2025, five seeds, α = 0.2)

**Table B.1: Own fill rate by cluster at candidate CSLs**

| Cluster | 90% | 95% | 95.7% | 97% | 99% | Recommended CSL |
|---|---|---|---|---|---|---|
| Flagship Core | 92.19% | 93.52% | 93.73% | 94.32% | 95.19% | 97% |
| Mid-Tier Core | 93.41% | 94.96% | 95.23% | 95.68% | 96.74% | 97% |
| Long-Tail Erratic | 93.59% | 95.66% | 95.92% | 96.48% | 97.51% | 90% |
| Long-Tail Erratic 2 | 86.36% | 87.83% | 88.12% | 88.54% | 89.85% | 95.7% |
| Long-Tail Erratic 3 | 90.88% | 95.52% | 95.99% | 96.81% | 97.94% | 90% |
| Long-Tail Supply-Risk | 91.94% | 95.26% | 95.77% | 96.44% | 97.47% | 90% |

Source: `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/service_level_v5_real_data_summary.csv`; team analysis.

### B.2 Netting-discount comparison (replayed 2025, five seeds)

**Table B.2: Netting discount 0.0 versus 1.0**

| α | Netting | Customer fill Δ vs actual | Internal fill | Cost Δ vs actual |
|---|---|---|---|---|
| 0.2 | 1.0 | +1.05 pp | 82.24% | +$1.79M |
| 0.2 | **0.0** | **+1.52 pp** | **82.32%** | **+$1.67M** |
| 0.6 | 1.0 | +1.54 pp | – | +$1.85M |
| 0.6 | **0.0** | **+2.69 pp** | **84.64%** | **+$1.43M** |

Source: `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/netting_discount_multiseed.csv` and `netting_discount_multiseed_alpha0_6.csv`; team analysis.

### B.3 SHAP: top three drivers per cluster

**Table B.3: Cluster-specific SHAP drivers (XGBoost surrogate, Tree SHAP)**

| Cluster | Code label | Feature | Mean \|SHAP\| | Mean signed SHAP |
|---|---|---|---|---|
| 0 | Mid-Tier Core | `promo_depth_share` | 1.149 | +1.021 |
| 0 | Mid-Tier Core | `net_margin_pct` | 0.712 | +0.448 |
| 0 | Mid-Tier Core | `log_annual_net_margin` | 0.352 | +0.185 |
| 1 | Flagship Core | `promo_depth_share` | 0.875 | +0.659 |
| 1 | Flagship Core | `demand_cv_base` | 0.808 | +0.607 |
| 1 | Flagship Core | `lead_time_mean_days` | 0.502 | +0.208 |
| 2 | Long-Tail Erratic 2 | `lead_time_cv` | 3.494 | +3.369 |
| 2 | Long-Tail Erratic 2 | `forecast_lift_wmape` | 0.137 | +0.070 |
| 2 | Long-Tail Erratic 2 | `forecast_baseline_wmape` | 0.124 | +0.025 |
| 3 | Long-Tail Erratic 3 | `forecast_lift_wmape` | 3.621 | +3.621 |
| 3 | Long-Tail Erratic 3 | `forecast_baseline_wmape` | 0.274 | +0.249 |
| 3 | Long-Tail Erratic 3 | `log_annual_net_margin` | 0.011 | +0.005 |
| 4 | Long-Tail Erratic | `demand_cv_base` | 1.215 | +1.130 |
| 4 | Long-Tail Erratic | `net_margin_pct` | 0.558 | +0.299 |
| 4 | Long-Tail Erratic | `log_annual_net_margin` | 0.432 | +0.282 |
| 5 | Long-Tail Supply-Risk | `forecast_baseline_wmape` | 3.006 | +3.006 |
| 5 | Long-Tail Supply-Risk | `forecast_lift_wmape` | 0.578 | +0.484 |
| 5 | Long-Tail Supply-Risk | `lead_time_cv` | 0.147 | +0.040 |

Source: `output datasets/segmentation/segmentation_shap_report.md`; `output datasets/segmentation/report_artifacts/shap_cluster_importance.csv`; team analysis.

### B.4 Cluster ANOVA (descriptive)

**Table B.4: One-way ANOVA of each feature across the six clusters**

| Feature | F | p-value |
|---|---|---|
| `forecast_baseline_wmape` | 461.6 | 1.5 × 10⁻¹⁸³ |
| `forecast_lift_wmape` | 190.2 | 1.2 × 10⁻¹¹² |
| `lead_time_cv` | 103.0 | 2.8 × 10⁻⁷⁴ |
| `demand_cv_base` | 97.4 | 2.5 × 10⁻⁷¹ |
| `promo_depth_share` | 65.8 | 1.3 × 10⁻⁵² |
| `log_annual_net_margin` | 57.6 | 3.4 × 10⁻⁴⁷ |
| `net_margin_pct` | 25.2 | 1.2 × 10⁻²² |
| `lead_time_mean_days` | 9.5 | 1.2 × 10⁻⁸ |

Source: `output datasets/segmentation/report_artifacts/cluster_anova.csv`; team analysis. The test uses the same features the clustering used, so it is descriptive rather than a validation.

### B.5 SCENARIO_0004 scenario card (excerpt)

> **Request:** "I foresee a decline in dairy demand by 25 percent due to the rise in the Veganism movement. I also expect there to be shipping delays due to rise in protests across the country. Dairy + Powdered Milk; assume delay to be for mean lead time to widen by 10 percent, and the protests to last for about 6 weeks."
>
> | ID | Measure | Change | Scope | Weeks | MEIO plans for it | Twin simulates it |
> |---|---|---|---|---|---|---|
> | OVR-0001 | `demand.volume` | × 0.75 | Dairy & Bakery: Fresh Dairy, Powdered Milk | 2026-W01..W52 | yes | yes |
> | OVR-0002 | `supply.lead_time_mean` | × 1.1 | all lanes | 2026-W01..W06 | yes | yes |
>
> **Size of change (inputs):** OVR-0001 −82,179 forecast units (−25.0% of scope; −0.62% of network demand in window; −$350,325 at list). OVR-0002: 76 lanes, 100% of network demand.
> **Approval:** configuration SHA-256 `3c9bc255…5ef6c`.

Source: `output datasets/overrides/SCENARIO_0004_Veganism_Dairy_Decline_Protest_Shipping_Delays/scenario_card.md`.

### B.6 Additional figures

![Figure B.1: Multi-seed robustness of the real-data headline](<../models/MEIO/MEIO_Weekly/final_submission/results/chart_multiseed.png>)

*Figure B.1: Fill and cost delta by seed at α = 0.4 and α = 0.6 (original netting 1.0 vintage; the current netting 0.0 figures are in Table 7.6).*
Source: synthetic Supply Brain dataset; team analysis. Image file: `models/MEIO/MEIO_Weekly/final_submission/results/chart_multiseed.png`

![Figure B.2: Per-segment α, own fill (early synthetic single-seed diagnostic)](<../Model Documentation/images/clark_scarf_v2_cluster_alpha_own_fill.png>)

*Figure B.2: Own-cluster fill versus α in the early single-seed, synthetic-basis diagnostic (v4 clusters). Its "long-tail wants less pooling" pattern was not reproduced on replayed demand (Table 7.7).*
Source: synthetic Supply Brain dataset; team analysis. Image file: `Model Documentation/images/clark_scarf_v2_cluster_alpha_own_fill.png`

![Figure B.3: Category elasticity](<../Presentation/assets_final/fig_category_elasticity.png>)

*Figure B.3: Base and promotional price elasticity by category.*
Source: synthetic Supply Brain dataset; team analysis. Image file: `Presentation/assets_final/fig_category_elasticity.png`

![Figure B.4: Suggested service levels page](<../Presentation/assets_final/screens/05_service_levels.png>)

*Figure B.4: Application page, suggested service levels.* Image file: `Presentation/assets_final/screens/05_service_levels.png`

![Figure B.5: Weekly MEIO allocation page](<../Presentation/assets_final/screens/06_weekly_meio.png>)

*Figure B.5: Application page, weekly MEIO allocation.* Image file: `Presentation/assets_final/screens/06_weekly_meio.png`

![Figure B.6: Periodic alpha check page](<../Presentation/assets_final/screens/07_alpha_top.png>)

*Figure B.6: Application page, periodic pooling-factor check.* Image file: `Presentation/assets_final/screens/07_alpha_top.png`

![Figure B.7: Simulation results page](<../Presentation/assets_final/screens/10_sim_results.png>)

*Figure B.7: Application page, simulation results.* Image file: `Presentation/assets_final/screens/10_sim_results.png`

## Appendix C: Code and Reproducibility

**Repository.** [Repository URL]. Branch `Optimization-Algorithm_Final`. Start with `README.md`, then `SCHEMA.md`, `input datasets/DATASET_DOCUMENTATION.md` and `output datasets/OUTPUT_DOCUMENTATION.md`.

**Environment.** Python 3.11 or later; `numpy`, `pandas`, `scipy`, `pyarrow`, `polars`, `scikit-learn`, `matplotlib`, `xgboost`. Front end: `App/requirements.txt` (Streamlit, Altair). Agents: `Agents/requirements.txt` (LangGraph, langchain-anthropic, langchain-openai, jsonschema, pyyaml, pytest).

**Data note.** `input datasets/orders.csv` and `input datasets/inventory.csv` are Git LFS pointers. Run `git lfs pull`, or use the parquet copies. `App/stage_csv.py` stages CSVs from the parquet tables for scripts that need them.

**Key commands (run from the repository root).**

```bash
python supply_brain_generator_v5.py --stage all --run-name history
```

```bash
python "Clustering & Segmentation/segmentation_v5/segmentation_v5.py" --input App/runs/csv_input
```

```bash
python "Clustering & Segmentation/segmentation_v5/shap_analysis_v5.py"
```

```bash
python run_meio_then_twin.py --yes
```

```bash
python -m Agents.runtime "What if Southeast beverage demand rises 25% in July?"
```

```bash
python -m streamlit run App/streamlit_app.py
```

**Where the report's numbers come from.**

| Report item | File |
|---|---|
| Segment portfolio, service ladder | `output datasets/segmentation/sku_clusters.csv`, `cluster_service_levels.csv` |
| k diagnostics, ANOVA, SHAP | `output datasets/segmentation/report_artifacts/` |
| Single-week model comparison | `models/MEIO/MEIO_Weekly/alt_models/alt_output/model_comparison_summary.csv` |
| Real-demand validation, sweeps, multi-seed | `output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results/` |
| Deployed parameters (receipt) | `output datasets/MEIO/SCENARIO_0002_MEIO_CLARK_SCARF_V2/rolling_2026/meio_parameters.json` |
| Forward-year KPIs | `output datasets/projections/baseline/run_kpis.json`; `output datasets/SCENARIO_0002_MEIO_CLARK_SCARF_V2/meio_policy/run_kpis.json` |
| Scenario KPIs | `output datasets/SCENARIO_0004_*/{prepared,unprepared,reactive}/run_kpis.json` |
| Assumptions | `ASSUMPTIONS_AUDIT.md` |

**Seeds.** Digital twin default seed 2026. Multi-seed checks use seeds 7, 42, 123, 2026 and 2027. Segmentation uses `random_state` 42. The SHAP surrogate uses seed 42.

**Known reproducibility caveats.**

- The full tuning run (`run_all.py`) does not regenerate the two v5 sweep files that set per-cluster α and test cluster service levels. `segment_alpha_v5_real_data.py` must be run separately, and `service_level_v5_real_data.py` exists only in `final_submission/`.
- `tests/test_twin_equivalence.py` fails before comparing (a parquet/CSV input mismatch and LFS pointers). Three tests in `tests/test_meio_parameters.py` assume v4 cluster names. The override-engine tests in `models/overrides/tests/` fail at collection because of an import path.
- Some committed charts in `models/MEIO/MEIO_Weekly/final_submission/results/` predate the netting-discount fix. Where a figure is from an earlier vintage, its caption says so, and the current figures are given in the accompanying table.

---

# About the Authors

| Name | Short Bio |
|---|---|
| Swasti Baghel | [2–3 lines: background, role on the project] |
| Sunnel Nelson | [2–3 lines: background, role on the project] |
| Vishwajeet Undhal | [2–3 lines: background, role on the project] |
| Shivangini Aggarwal | [2–3 lines: background, role on the project] |
| Varshil Rambhia | [2–3 lines: background, role on the project] |

