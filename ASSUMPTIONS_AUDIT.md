# Assumptions Audit: Generator, Segmentation, MEIO Weekly (final) and Digital Twin

*Audit date: 2026-10-23. The code was read line by line, and the key claims were checked against the shipped data in `input datasets/*.parquet` and `output datasets/`. No code was modified.*

**Scope**

| Component | Files audited |
|---|---|
| Data generator | `supply_brain_generator_v5.py` (canonical); `input datasets/DATASET_DOCUMENTATION.md`; `SCHEMA.md`; `scenarios.csv` |
| Segmentation | `Clustering & Segmentation/` v2 (the .py is empty, so the .docx was used), v3, v4, v5 (canonical), and `shap_analysis_v5.py` |
| MEIO final weekly | `models/MEIO/MEIO_Weekly/final_submission_optimized/`: build code (`clark_scarf_v2.py`, `meio_weekly_lib.py`, `meio_parameters.py`, `policy_builds.py`, `roll_forward_policy.py`, `meio_optimizer_weekly.py`, `runtime.py`) and evaluation code (`run_all.py`, `validate_real_data.py`, the alpha, CSL and netting sweeps, `scenario_test.py`, `tests/`) |
| Digital twin | `models/twin/digital_twin.py` (canonical), `scenario_engine.py`; `final_submission_optimized/digital_twin_fast.py` (the version the pipeline actually runs) and `reactive_twin.py` |

**Legend:** **[E]** = explicit (stated in a comment, docstring, constant or doc). **[I]** = implicit (built into the logic but never stated). ⚠ = materially affects results or credibility.

---

## 0. The 20 assumptions that matter most

| # | Assumption | Where | Why it matters |
|---|---|---|---|
| 1 | ⚠ The baseline RDC policy is sized only on the RDC's *direct* bulk demand. Its child CDCs' demand is left out, and PDC throughput leaves out CDC flow as well. | gen v5 `derive_baseline_policy` L3715–3808 | 358 RDC-SKU pairs that have downstream demand get **no policy row** (12.5% of CDC-routed volume) and never reorder. This weakens the baseline that MEIO is compared against. |
| 2 | ⚠ The baseline is tuned to a deliberately mediocre target: customer fill 90–96%, ≥10% of receipts short. It is also sized on the same history it is scored on. | gen v5 L3634–3636, L3883–3890 | This builds in room for MEIO to "win". |
| 3 | ⚠ **The live MEIO parameters are not the documented ones.** The receipt resolves to **alpha = 0.6**, netting 0.0, quadrature DDLT, and **per-cluster alpha 0.6–1.0 on 367 SKUs**. The README and model docs say alpha = 0.2. | `output datasets/MEIO/.../meio_parameters.json` (verified) | No real-data backtest exists for the configuration that is actually deployed. The headline numbers describe a different configuration. |
| 4 | ⚠ The same year (2025) is used both to tune and to score parameters. The seasonal index used to size 2025 test weeks is built from all 156 weeks, 2025 included. | `meio_weekly_lib.py` L579–594; `meio_parameters.py` | Look-ahead leakage, which contradicts the "no lookahead" claims. |
| 5 | ⚠ "Real replayed 2025 demand" is the generator's own synthetic history. "Actual (as happened)" is the twin running the static `baseline_policy.csv`, not observed outcomes. | `validate_real_data.py` L5, L143–146 | Generator, forecast, clusters and twin share structure. That favours the model. |
| 6 | ⚠ Seeds vary only simulation and Monte Carlo noise; demand is the same in every seed. The pass rule is "gain > 2 SE over ≥ 5 seeds", with no t-correction and no multiple-comparison correction. The winner is the best-of-grid. | `meio_parameters.py` L207–251 | The SEs (≈0.01–0.03 pp) measure simulator noise only, so "validated" cannot detect overfitting to 2025. |
| 7 | ⚠ The per-cluster alphas were each tested with the other clusters held at 0.6, then adopted *jointly*. The combination was never simulated. Four of six winners sit on the grid edge (1.0). | `meio_parameters.py` L378 | Ignores interactions through shared RDC/PDC stock. The optimum may lie outside the grid. |
| 8 | ⚠ Tuning evidence uses Monte Carlo DDLT (n = 1,200); production uses quadrature (n_sims = 4,000 is inert). | `policy_builds.py` L223, L259; `scenario_config.json` | The evidence was gathered on a different sizing method from the one deployed. |
| 9 | ⚠ The twin double-applies plant `allocation_share`: each PDC's signal already covers only its own RDCs, then it is scaled by share again. | `digital_twin.py` L944–948; `digital_twin_fast.py` L734–738 | Steady state is impossible when share < 0.286 (50 rows). PDC internal fill is 82.0% for multi-sourced SKUs vs 97.1% for single-sourced. This is a likely contributor to the persistent −11 pp internal-fill gap. |
| 10 | ⚠ The twin holds back a parent's `safety_stock` from its children. PDCs have no customers, so PDC safety stock can never be released. RDC pooled safety stock, which is meant to protect CDCs, is withheld from them. | `digital_twin.py` L856–878 | Penalises any upstream buffer, which works against the core claim of the Clark-Scarf pooling model. |
| 11 | ⚠ MEIO sizes CDC/RDC stock on the **cumulative plant→node lead time** via the fastest lane. The twin replenishes each hop over its own per-SKU sourcing lane, and lead times are rounded up to whole days (≈ +0.5 d per hop). | `meio_weekly_lib.py` L618–667 vs twin L889 | The model and the "ground truth" physics disagree. CDC buffers are inflated as if upstream stock did not exist. |
| 12 | ⚠ The RDC's pooled s and S include downstream echelon demand, but the twin compares them with the RDC's **installation** inventory position. | `clark_scarf_v2.py` L324–381; twin L790 | This is not Clark-Scarf in the textbook sense, since there is no echelon inventory position. |
| 13 | ⚠ Sigma is treated as demand variability (CV of actuals), not forecast error. CV is held constant over a 52-week horizon, and weeks are assumed i.i.d. and independent across DCs/SKUs. | `meio_weekly_lib.py` L462–546 | Seasonality, promotions and trend are counted as noise. Uncertainty does not grow with forecast horizon. |
| 14 | ⚠ Shelf life is 1,095–1,459 days for **every** SKU, Fresh Dairy, Frozen and Bakery included. Write-offs are hard-coded empty. Every facility is ambient. | gen v5 L104–112, L449–452, L4033 | Perishability and the cold chain, the defining F&B constraints, are switched off. Newsvendor critical ratios pin near 1. |
| 15 | ⚠ The carrying rate is 10.2%/yr (9% WACC + 1.2%) for every SKU, and unit cost is the same at every echelon. | gen v5 L558–583; twin L359–375 | Industry norm is 20–30%. Cheap inventory favours buffering, and there is almost no holding-cost benefit from holding stock upstream. |
| 16 | ⚠ Transport is ≈ 88–91% of total landed cost, yet it is linear per unit regardless of cube or weight. Plant→PDC freight is excluded. | twin L898–900; gen v5 L780 | Cost rankings are mainly a transport-volume signal. |
| 17 | ⚠ The promo-lift estimate and the customer-split shares are fitted on all 156 weeks, despite "strictly ex-ante" comments. | gen v5 L1239–1310 | Forecast look-ahead leakage in the in-sample panel. |
| 18 | ⚠ In v5 segmentation, the SUPPLY_RISK block (25% of distance) uses two lead-time features that are **static network parameters**: constant across weeks, CV ≈ 0.03 across SKUs. The cluster labelled "Supply-Risk" is really the worst-forecast cluster. | `segmentation_v5.py`; v4 L197–216 | Clusters partly encode geography. The labels and recommended actions are misdirected. |
| 19 | ⚠ The CSL is set by revenue-share thresholds (≥ 30% → 0.97, ≤ 3% → 0.90), not economics. "Long-Tail Erratic 2" misses the 3% cap by 0.004 pp and gets 0.957. | `segmentation_v5.py` L556–575 | A knife-edge rule. The critical-ratio "economics" are flat (≈ 0.955) for every SKU. |
| 20 | ⚠ The equivalence and parameter tests do not pass as shipped. `test_twin_equivalence` fails 3/3 (parquet passed to the CSV-reading original; LFS pointers). `test_meio_parameters` fails 3 tests (written for v4 clusters). OPTIMIZATION.md claims all pass. | `tests/` | The "bit-identical" claim is unverified, and the override, outage and capacity paths exist only in the fast twin with no test coverage. |

---

## 1. Data generator: `supply_brain_generator_v5.py`

### 1.1 Time and calendar
- **[E]** Start year 2023. Every year is exactly 52 weeks. Burn-in is 104 weeks (2023–24), in-sample 52 (2025), forward 52 (2026), for 156 history weeks and 208 in total (L124–131).
- **[E]** Week 1 starts on Monday 2023‑01‑02, and every week has 7 days. Labels are "YYYY‑Www" but are **not ISO weeks**: the calendar drifts about one day per year, so 2026‑W01 starts on 2025‑12‑29. ISO week 53 is ignored, although 2026 has one (L132, L425–434).
- **[E]** The docstring says the horizon boundary is "never hardcoded to a calendar year" (L35–38). **This is false.** `YEAR_TREND` is keyed {2023..2026} and literal `52`s appear at L700, 703, 1003, 1019. The EDA filters on the string "2025" (L3294).
- **[I]** A week's calendar month is the month of its Monday, so seasonality steps at month boundaries (L653).
- **[E]** B2B orders are placed only on weekdays: BULK on Tue/Thu, DSD on Mon/Wed/Fri. There are no weekend orders (L1099).
- **[I]** Each (banner, SKU) orders on the same fixed weekday for all four years (L1155).
- **[E]** Customer delivery lead time is uniform 2–6 days (BULK) or 1–3 days (DSD), drawn i.i.d. It does not depend on distance (L1179).
- **[E]** The 104-week burn-in is assumed to absorb the cold-start artefact of week 1 starting at S (DOC Part 8).

### 1.2 Network and facilities
- **[E]** The network is 4 plants (holding no stock) → 4 PDCs (co-located, push) → 8 RDCs → 15 CDCs (pull, weekly review): 27 storage sites in 20 metros and 5 regions (L9–14, L752, L783, L793).
- **[E]** Metro coordinates are hard-coded, and so is the region map (Tampa counts as Southeast) (L758–768).
- **[I]** Distance is great-circle (haversine, R = 3,958.8 mi), not road miles, so it understates road distance by about 15–25% (L770–774).
- **[E]** Every facility is a plain ambient warehouse: no freezer, chiller or bonded store. Every DC can hold every subcategory (L449–452). ⚠ This is unrealistic for Frozen and Chilled.
- **[I]** DC storage capacity is uniform random: PDC 0.8–1.2M, RDC 400–700k, CDC 90–190k. It is unrelated to the demand each DC serves (L794–799). It never binds (average utilisation 9.1%).
- **[E]** Every PDC has a lane to every RDC (32 lanes) (L813).
- **[E]** Each CDC gets lanes from its 2 nearest RDCs (primary + failover). Lateral RDC↔RDC lanes connect each RDC to its 2 nearest (L815–819). ⚠ **Neither failover nor lateral lanes are ever used.** 25 of 76 lanes carry no flow; the documentation wrongly says 16.
- **[E]** Every RDC is a full-range buffer that can stock any SKU (L1386).
- **[E]** Lane capacity is U(200k, 550k) units/week, random and unrelated to demand (L780). It is effectively infinite (0.6% utilised).
- **[I]** Transport modes are Truck and Train only (L1589). Train is no cheaper than Truck.

### 1.3 Products and SKU master
- **[E]** 500 SKUs = 5 categories × 100. Each SKU's product group is sampled in proportion to real product counts (L143–167, L675).
- **[E]** Within every category the segment mix is exactly 50/30/20: Fast-Moving / Seasonal / Intermediate (250/150/100 SKUs). Segment is independent of subcategory and price, and is then *imposed* on volume, seasonal amplitude and z (L389, L655–658). ⚠ This makes downstream segmentation partly circular.
- **[E/I]** Brand tier is assigned round-robin (`i % 3`), giving exactly ⅓ Premium (×1.60), ⅓ Mainstream (×1.00) and ⅓ Value (×0.62), independent of subcategory (L367–373, L681–682).
- **[E]** Shelf life is uniform 1,095–1,459 days, longer than the history, so perishability is inert by design (L104–112). The `CATEGORY_SHELF_LIFE` table (Dairy 5–21 days etc.) is defined but never used (L383–386). The comment at L160–163 says Fresh Dairy exists "so perishability can bind", which contradicts this.
- **[E]** Fresh Dairy and Bakery parameters are invented "US retail norms" (μ 1.20/1.05, σ 0.35/0.40, elasticity −1.85/−2.10). Frozen Dessert reuses the Canned Sweet parameters (L196–200).
- **[E]** There are six production families defined by process: LIQUID_FILL, DRY_BLEND, CONFECTIONERY, BEVERAGE_DRY, FROZEN, CHILLED. Oil and tomato sauce share the bottling line with wine and liquor (L316–325).
- **[I]** There are no BOMs, no pack changes, no discontinuations, and no lifecycle beyond launch events.
- **[I]** `units_per_case` ranges from 1 to 24 (median 1), but **it is never used** for orders, rounding or MOQ.

### 1.4 Pricing, costs and economics
- **[E]** Case price ~ lognormal(μ, σ) per group, clipped to [lo, hi]. Unit price = case price ÷ units per case × brand multiplier, floored at $0.05 (L171–223, L678–683).
- **[E]** List price is national, justified by the Robinson-Patman Act. All customer differentiation happens below list (L335–338).
- **[E]** Unit cost = list × category cost ratio (0.53–0.58) × U(0.97, 1.03), capped at 0.95 × list (L381–382, L684). ⚠ Cost scales with the brand premium, so Premium and Value earn the same margin %, which is unrealistic.
- **[I]** Unit cost is constant over 4 years while price inflates about 4%/yr, so margins widen over time (L684, L1064, L1173).
- **[E]** A secondary plant costs 2–5% more. Cost is later re-assigned cheapest-first to whichever plant ended up with the most volume (L332, L943, L1447–1454). That is circular: cost follows the sourcing outcome.
- **[E]** Holding cost per unit-year = storage (facility $/sqft ÷ units/sqft) + unit cost × carrying % (L446–467). Storage is treated as variable cost although facility cost is fixed.
- **[E]** Facility benchmarks are "assumptions, not fetched": capex $110/sqft, energy 6 kWh/sqft, opex $4.50/sqft, shrink and insurance 1.2%, depreciation 2.5%/yr, cap rates 5.0–6.9%, metro indices 0.88–1.28, units/sqft 120–420, size factors PDC 0.88 / RDC 0.94 / CDC 1.15 (L484–559).
- **[E]** ⚠ WACC is 9%, so the carrying rate is **10.2%/yr for every SKU** (L558–583). That is low against the 20–30% norm.
- **[I]** Storage cost works out to about $0.03–0.16 per unit-year, negligible next to carrying cost, so location barely affects holding cost.
- **[E]** Refrigerated space costing 2–3× ambient is explicitly ignored (DOC Part 8).
- **[E]** The stockout multiplier is U(1.5, 2.5) per *category*, not per SKU or customer (L749).
- **[E/I]** Logistics cost per unit = 0.0018 × miles + U(0.4, 1.2). It is the same for a sachet of yeast and a bottle of liquor, with no cube or weight basis (L780). Plant→PDC lanes are 0 miles but still cost about $0.71/unit.
- **[E]** Price waterfall: invoice = base × (1 − off-invoice) × (1 − discount); net = invoice × (1 − trade fund − rebate − logistics allowance). `revenue_usd` ignores off-invoice (L1158–1174).
- **[I]** Net margin excludes logistics and holding (L1174).
- **[I]** Validation tolerates up to 10% of SKUs with negative net margin and up to 10% of volume priced below cost (L1707–1712).

### 1.5 Customers and channels
- **[E]** There are 10 banners: 4 BULK customers served from RDCs (scale 3.2–6.0) and 6 DSD customers served from CDCs (scale 0.9–1.6) (L825–831).
- **[E]** Trade terms and strategy (EDLC / HIGH_LOW / CONTRACT) are set per banner (L345–360). The "negative logistics allowance = DSD premium" comment (L342) is not implemented.
- **[E]** Banner-to-DC coverage is round-robin plus a top-up to 2–3 DCs. **It ignores geography** (L842–843).
- **[I]** A banner's demand split across its DCs ~ Dirichlet(1, …, 1). It is random and unrelated to population (L851).
- **[E/I]** Assortment: BULK banners carry a random 85% of SKUs, DSD banners 55%. It is fixed for four years and independent of channel fit (L1086–1091).
- **[E]** CONTRACT (foodservice) accounts get no promotions (L1029). ⚠ **This is broken** for promotions aimed at "ALL", which do discount the foodservice account (L1025, L1043).
- **[I]** No returns, cancellations, credit holds or customer substitution.
- **[E]** Part 1 says "3 channel types" while §2.1 lists 5 channels (documentation inconsistency).

### 1.6 Demand generation
- **[E]** Demand is multiplicative and separable: SKU base × banner scale × DC share × regional index × seasonality × year trend × price response × promotion × structural events × noise (DOC §2.5, L1142).
- **[E]** SKU base = 6.0 × lognormal(0, 1.15) × segment factor (Fast 1.6, Seasonal 1.0, Intermediate 0.5). ⚠ Popularity is independent of price, so a $60 liquor bottle sells like a $1 yeast sachet (L393–395, L689–690).
- **[E]** Monthly seasonality = one curve per subcategory × a macro calendar (0.96–1.05). All SKUs in a subcategory share that curve, so they are strongly correlated (L240–272, L693).
- **[E]** Each SKU gets per-year monthly jitter N(0, 0.08), renormalised to mean 1 without weighting by weeks per month (L698–699). Weekly noise is N(0, 0.03), floored at 0.10 (L702–703).
- **[E]** Seasonal amplitude is scaled by segment label: Fast 1.0, Seasonal 1.35, Intermediate 0.6 (L390, L702). ⚠ Circular with segmentation.
- **[E]** Year trend steps: 1.00 / 1.04 / 1.092 / 1.147. The step happens at week 1, with no ramp, and is the same for every SKU, so growth is perfectly correlated. The 2026 value is never realised (L134, L704). `YOY_GROWTH = 0.05` is dead code (L136).
- **[I]** ⚠ Noise is i.i.d. lognormal(0, 0.15) per (banner, DC, SKU, week): no autocorrelation, no common or macro shocks, no cross-SKU correlation. That is low variability, plus about 1.1% upward bias (L1144).
- **[I]** Weekly quantity = max(0, round(·)). Intermittency comes only from rounding small values (L1143).
- **[E]** Regional index per (region, subcategory) ranges 0.55–1.55, renormalised so national volume is unchanged (L282–309). Validation requires at least one subcategory above 1.4× in some region, and maximum regional correlation below 0.75.
- **[E]** 5% of SKUs get a permanent ×0.55 or ×1.45 distribution change in weeks 30–146, applied to all banners at once although the comment says "gains/loses a banner" (L139, L707–709). There is an off-by-one between 1-based and 0-based weeks (L708).
- **[E/I]** 2% of SKUs launch late (weeks 20–124), with no ramp-up curve (L141, L713–714).
- **[I]** ⚠ Orders equal **true unconstrained demand**: they are never censored by stockouts (L1146–1151). Real order history would be censored.
- **[I]** No competitor, weather or macro effects, and no discontinuations.
- **[E]** The EDA blurb says demand is "grounded in an external real F&B sales dataset (2020-shape seasonality, COVID spike excluded)". That source is unnamed and appears nowhere else (L3315).
- **[I]** Realised volume growth is +3.8% (2024), +8.6% (2025) and +9.0% (2026 forecast). None of this is stated in the assumptions section.

### 1.7 Promotions and price elasticity
- **[E]** Base elasticity = raw group value + N(0, 0.10). Positive and extreme values are kept "verbatim" (Tea +2.74, Powdered Milk +0.43, Flour −5.57) (L168–170, L686).
- **[E]** Promo elasticity = base × 1.75, clipped to [−4.5, +3.0] (L396–399, L717). ⚠ This is **inverted** for very elastic groups (Flour, Cassava, Candy end up with promo elasticity below base). For Tea, a discount *reduces* demand. The EDA recovers only a 0.85–1.28× ratio, not 1.75×.
- **[I]** Constant-elasticity power response: (everyday net ÷ list)^ε_base × (1 − discount)^ε_promo, capped at 12× (L1129–1130). The reference price is fixed list, so ~4%/yr inflation steadily erodes elastic demand (about −30% by 2025 at ε = −3), partly cancelling the trend.
- **[E]** Demand responds to off-invoice discounts, but not to trade fund, rebates or logistics allowance (L1128).
- **[E]** 8–16 promotion attempts per SKU per year, lasting 1–3 weeks. Start weeks are weighted by a holiday calendar (0.55–2.00). 45% target ALL banners (L411–416, L1003–1025).
- **[I]** Frequency multipliers above 1 (HIGH_LOW 1.3–1.5) have no effect because the code uses `min(1, ·)`. High-low grocers are not actually promoted more often (L1029).
- **[E]** Planned depth = U(0.05, 0.30) × multiplier, clipped to [0.02, 0.32]. Actual = planned × N(1, 0.12), clipped to [0.02, 0.36] (L1031–1032).
- **[E]** Overlapping promotions do not stack: the deepest wins (L1045–1048).
- **[I]** Promo type (TPR / Feature / Display / both) has **no effect on lift** (L1001, L1041).
- **[E]** Post-promo dip = storability × min(depth/0.30, 1) × 20% in week +1 and half that in week +2. Storability: Dairy 0.2, Frozen 1.0, others 0.9 (L417–419, L1120–1124). There is no pre-promo lull, no cannibalisation, no halo and no competitor response.
- **[E]** A promotion that starts after week 156 is "planned" only, with no actual depth (L1036–1042).
- **[I]** Promo share of volume is about 30% (validation band 20–45%). Promo share ranges from about 15% in January to about 45% in Nov–Dec, a figure hard-coded in prose.

### 1.8 Orders and basket generation
- **[E]** Order cadence by national SKU base percentile: top 40% weekly, middle every 2 weeks, bottom 20% every 3 weeks (L1093–1095).
- **[I]** Demand accumulates over the cadence and is placed as one order line in the phase week, so customers order *after* the demand happened. Any remainder is flushed in week 156 (L1138, L1146–1151).
- **[E]** One basket per (banner, DC, order date) (L1175–1177).
- **[I]** No case or pallet rounding, no MOQ, no order caps.
- **[E]** "62% of apparent volatility at customer grain is order batching." This is a hard-coded figure, not computed (DOC §2.5).

### 1.9 Forecast generation and forecast error
- **[E]** The forecast is built at SKU × DC grain (L1234–1237).
- **[E]** De-promotion masks promo weeks and linearly interpolates them. Post-promo dip weeks remain in the baseline (L1264–1265).
- **[E]** Level = trailing 13-week mean (minimum 3 weeks) (L1245, L1266).
- **[E]** In-sample baseline = de-promoted value from 52 weeks earlier × clip(level ratio, 0.5, 2.0), i.e. seasonal-naive. It ignores the known price plan, elasticity and structural events (L1292–1296).
- **[E]** Forward baseline = de-promoted 2025 value × one year-over-year ratio fixed at cut-over, flat for the whole year (L1297–1301).
- **[E]** ⚠ Lift = median over promo weeks of (actual ÷ baseline − 1) ÷ depth, taken over **all 156 weeks**. The "strictly ex-ante / prior weeks only" comments (L1239–1287) are false.
- **[E]** Lift is deliberately mis-estimated by × (1 + N(0, 0.35)), clipped to 0–8. The default when there is no history is 1.8 (L1244, L1283–1288).
- **[I]** The promo forecast is linear in depth, which is mis-specified against the true power response (L1302).
- **[I]** Planned depth at SKU × DC = the maximum over banners, so a single-banner promotion lifts the whole DC baseline (L1269–1278).
- **[I]** ⚠ The forecast is split to customers by full-history shares, which leaks future data. New cells get no forecast (L1306–1310).
- **[I]** Rounding after disaggregation means totals are not preserved (L1329–1330).
- **[I]** Every accuracy metric drops weeks with actual = 0, which is about a third of the panel (L1993, L2002, L3460).
- **[I]** ⚠ Bias is **negative in every group** (−4% to −17%, −7.8% overall). This systematic under-forecast is not stated in the assumptions or limitations.
- **[I]** Validation treats "too good" as unrealistic: correlation(forecast, actual) must be below 0.95. Segment must explain less than 25% of WMAPE (L1991–1998).
- **[E]** `SEGMENT_FC_NOISE` is dead code (L420).

### 1.10 Sourcing, routing and lanes
- **[E]** Plant capability: each production family can be made at 2–3 plants (L326–331).
- **[E]** ABC is ranked by *volume*, not revenue: A = top 20%, B = next 30% (L905–908).
- **[E]** Capacity budget = family demand ÷ 0.88 target utilisation. The comment says "~78%" (L914–929).
- **[E]** A items are dual-sourced whenever possible; B items with probability 0.35. Primary share is clipped to 0.55–0.85 (L939–947).
- **[E]** ⚠ Each CDC is single-sourced from the RDC with the shortest *mean lead time*, which is random within distance bands and so not necessarily the nearest RDC. Each RDC buys each SKU from exactly one PDC. Step-1 budgets are **overridden** by this, and allocation shares are rewritten afterwards from realised volume (L1387–1458).
- **[E]** Routing is static over the whole horizon. There is no re-sourcing or rebalancing (L1495–1515).
- **[E]** The key finding claims "Network is fully dual-sourced (15/15 CDCs with failover)" (L3607). The failover lanes exist on paper only.

### 1.11 Lead times and replenishment
- **[E]** Lead time by distance band (L776–779):

  | Distance | Mode | Mean lead time | CV |
  |---|---|---|---|
  | < 25 mi | Truck | 0.5 d | 0.15 |
  | < 600 mi | Truck | U(2, 4) d | 0.20–0.35 |
  | 600–1,200 mi | Train or Truck, 50/50 | U(4, 7) d | 0.30–0.45 |
  | > 1,200 mi | Train | U(7, 13) d | 0.40–0.60 |

  Lead time is random within a band and does not depend on exact distance. 2–4 days for under 600 miles is slow; a full truckload over that distance typically takes about a day.
- **[E]** Promised lead time = the lane's p90 under a Normal distribution (z = 1.2816), so on-time delivery is about 90% by construction (DOC §2.9).
- **[E]** Lead-time ranges are stated three different ways in the docs: "~8 days", "nearly thirteen", "0.5–7.7". The data says PDC→RDC up to 12.94 days and CDC lanes up to 6.89 days.
- **[E]** Review period R = 1 week at RDCs and CDCs (L3647).
- **[E]** Internal shortfall is cancelled at week close and re-ordered at the next review. Bulk customers backorder; DSD customers lose the sale (DOC §2.9).
- **[E]** "A short receipt makes a stockout 9× more likely" is a hard-coded claim, not computed.

### 1.12 Production and capacity
- **[E]** Capacity = p97 of weekly planned volume × 1.06 (plant) or × 1.10 (family), then × **1.15** at world build. Effective headroom is about 1.22–1.27× (L1468–1491, L3926–3957). ⚠ The 1.15 ramp was justified from **forward-run results**, so the world was tuned on the test horizon.
- **[I]** Capacity is sized on *customer* demand only, so it excludes bullwhip and safety-stock builds.
- **[I]** Capacity is constant over time. There are no changeovers, batch sizes, maintenance, yield loss, materials, or production lead time.
- **[E]** Production plan = last week's demand plus a top-up to about 1.5 weeks of finished-goods cover (DOC §2.4). The twin uses **2.5**.
- **[I]** Schedule adherence ~ U[0.90, 1.00], never below 90%: no breakdowns and no fat lower tail.

### 1.13 Baseline policy (`derive_baseline_policy`)
- **[E]** z by segment: Fast 2.05, Seasonal 1.64, Intermediate 1.28, i.e. 98/95/90% CSL (L3643). The comment calls Intermediate SKUs "intermittent", which they are not.
- **[E]** Sigma uplift: PDC 1.45, RDC 1.25, CDC 1.00. This is an ad hoc stand-in for modelling bullwhip (L3646).
- **[E]** SS = z·√(P·σ² + μ²·σ_LT²) with P = R + L, under a Normal approximation (L3684, L3774).
- **[E]** S = max(s + μ·max(R, 1.5), 1.2·s). The comment says "about three weeks" but the code uses 1.5 (L3779, L3654).
- **[E]** ⚠ All policy knobs were "fitted against the acceptance profile" (L3634).
- **[E/I]** ⚠ History basis: μ = total over 156 weeks ÷ 156, which includes the lower 2023 level and pre-launch zeros and so sits about 6% below the 2025 level. σ is computed over **non-zero weeks only**, a different basis from μ. σ also includes seasonality and trend (L3722–3738). The docstring says "2025 in-sample actuals" (L3670), but the code uses 2023–25.
- **[E]** Forward basis: μ = mean forward forecast, σ = in-sample σ × (μ ratio), i.e. constant CV. Pairs with no history get σ = 0 and so SS = 0 (L3740–3752).
- **[I]** One static policy for the whole year, with no seasonal re-planning.
- **[E]** A missing lane defaults to lead time (3.0, 1.0) days (L3772). Pairs with μ = σ = 0 get no row (L3769).
- **[E/I]** ⚠ RDC and PDC policies leave out child CDC flow (see top-20 #1). PDC SS = z·√(L/7)·μ·1.45·**0.5**: it is proportional to μ, not σ, and the 0.5 is arbitrary (L3791–3808).
- **[I]** Python `round()` rounds half to even.

### 1.14 Opening position and history replay
- **[E]** Opening stock = closing stock of the last history week (L3836–3839).
- **[I]** All in-transit stock for a pair is collapsed into one quantity at the earliest ETA (L3842–3846).
- **[I]** Lead time is assumed static: it is taken from the first inventory row of each pair (L3851).
- **[E/I]** Pairs without a policy get s = S = 0 and never reorder. Pairs with a policy but no terminal row are dropped (L3855–3868).
- **[I]** No backlog, open customer orders or plant WIP crosses the cut-over.
- **[E]** `write_offs.csv` is hard-coded empty (L4033–4036).
- **[E]** History is replayed with a baseline policy sized on that same history (L4016, L4029).

### 1.15 Scenario library (`generate_scenarios`, `scenarios.csv`)
- **[I]** The scenario library has its own seed (77): 30 scenarios in fixed counts of 6/5/6/5/4/4 (L3178).
- **[I]** Start week is uniform over the year, with **no seasonality**: hurricanes, winter rail problems and peak surges are equally likely in any week (L3193).
- **[I]** ⚠ `end_date` = start + duration + 6 days, which is **off by a week**: a 1-week event spans 14 days (L3196–3198).
- **[I]** Classes:

  | Class | Scope | Size and duration |
  |---|---|---|
  | demand_surge | one CDC × one SKU (never bulk RDC customers) | 1–3 weeks, +50–150% |
  | forecast_miss | one SKU, network-wide | fixed 2 weeks, 20% "shortfall" (direction ambiguous) |
  | lane_outage | one CDC's primary lane | 1–2 weeks; recovery 3–7 days, unrelated to duration |
  | leadtime_spike | one RDC inbound lane (which PDC lane is unspecified) | +4–10 days additive, 2–4 weeks |
  | plant_shortfall | one plant × family | 20–40%, labelled "cost" impact |
  | geographic_disruption | one region | 30–70% "shortfall" of an **undefined quantity**, no node list |

  The lane_outage note says it "requires failover or lateral transfer", which history never exercised.
- **[I]** Time-to-survive = DC-level mean stock ÷ daily shipments **under the historical policy**, truncated, one value for all SKUs. TTS = 0 conflates "not applicable" with zero (L3183–3185).
- **[I]** Probabilities are uniform draws within each class band. They have no time basis, are uncalibrated, and sum to about 5 (L3213 etc.).
- **[I]** Every shock is a single, independent event with a flat profile: no compounding, though windows overlap.
- **[I]** Missing classes: supplier or ingredient failure, quality recall, labour, cyber, cost or price shocks (L1603–1616).
- **[E]** Scenario magnitudes are calibrated to history days of supply (L4071–4075).

### 1.16 Randomness and reproducibility
- **[E]** Seeds are fixed in code: masters 2025, shelf life 31337, network 2026, orders 4242, forecast 909, scenarios 77. ⚠ `--seed` controls **only the twin**, so variability in the world itself cannot be tested and there is one realisation of history (L646–1215, L3965).
- **[E]** Separate RNG streams and sorted SKU lists keep draws stable (L755–756, L1087–1091).
- **[E]** "Byte-identical output" is claimed (DOC Part 9), but the docs embed a timestamp.

### 1.17 Validation and acceptance thresholds
- **[E]** Acceptance bands (L3883–3890):
  - customer fill 0.90–0.96
  - internal fill 0.85–0.96
  - on-hand 0.8–1.3M units (a unit count across items priced $0.05–$100)
  - receipts on time 0.85–0.95
  - receipts short 0.10–0.30
  - capacity-limited 0.02–0.15
- **[I]** ⚠ Failing an acceptance check only prints a message. `validate()` returns a boolean and the run continues. The docs say checks "fail loudly" (L3893–3908, L4080–4087).
- **[I]** Bullwhip is *required*: PDC CV must exceed 1.05 × CDC CV (L1923–1925).
- **[I]** Fresh Dairy fill target is 96%, yet realised CDC (DSD) fill is only **79.7%** against RDC 95.7%. The 92.7% headline hides this (DOC §6, not disclosed in Part 2).
- **[I]** Several checks are trivially true, e.g. "SS differs by segment" means std > 0, and "every tier has a target" means at least 3 rows (L1860, L1600).
- **[I]** `validate(opening_path=None)` crashes because of `read_csv(None)` before the null check (L1962 vs L2055).
- **[E]** Docstrings hard-code results (+8.8% forecast, +5.3 pp fill, −$2.5M, 11.3% → 0.6%) that go stale whenever data is regenerated (L3674, L3930).

### 1.18 Documentation vs code (generator)
- EDA year-over-year compares one forward year with **all three** history years and prints "YoY −61.2%". The true figure is +9.0% (L3467–3468, L3606).
- Raw code (`"""` and `doc += _facility_cost_doc_section()`) leaks into DATASET_DOCUMENTATION.md (doc lines 656–657).
- Part numbering in the "How to read" table does not match the actual parts.
- §5.1 says estimate elasticity by subcategory; the snippet and EDA do it per category.
- "Forecast accuracy degrades from fast-movers to seasonal" is hard-coded; the data shows Intermediate is worst on MAPE.
- The scenario EDA labels ("supplier, geographic, systemic, market-driven") do not match the six classes.
- Several prose figures are hard-coded (CV 0.16/0.19, WMAPE 0.57/0.39, "nine times", "62%", "two-thirds") and can drift from the data.
- `inventory.csv` and `orders.csv` in the repo are **Git LFS pointer files**. Scripts that read the CSVs fail on a clone without LFS; only the parquet copies have data.

---

## 2. Segmentation: v2 → v5 and SHAP

### 2.1 v2
- **[E]** `segmentation_v2.py` is **0 bytes**, so v2 is not reproducible. From the .docx: 10 features (demand mean, std and CV, MAPE, stockout rate, adherence std, lead time mean and std, revenue, list price, margin %, shelf life); whole-history, one row per SKU; lead time from lane paths with √Σσ² (independent legs); KMeans with k chosen by silhouette over 2–8 (k = 3, silhouette 0.198). The doc admits the "Low-Margin" tag is an artefact and that lead_time_std dominated (F = 1540).

### 2.2 Common to v3, v4 and v5
- **[I]** One row per SKU. DC behaviour is collapsed, and the cluster CSL is applied to every DC.
- **[I]** Features use history only, and the forward panel is never used, so there is no forward leakage. However, the value and variability features include the 2023 burn-in that the dataset docs say to avoid.
- **[I]** Forecast features use 2025 only (52 weeks) while value and variability use 156 weeks: a window mismatch.
- **[I]** Base CV comes from the national weekly series. Weeks with no non-promo orders are *absent*, not zero, which understates intermittency.
- **[I]** `promo_depth_share` is actually promo **volume** share, not discount depth.
- **[I]** ⚠ WMAPE is scored at SKU × DC × customer × week grain. The docs warn that this grain mostly measures order batching.
- **[E]** Block-balanced scaling: each surviving business block gets equal total variance (weight z × √(1/(n_blocks × n_in_block))). This assumes equal importance across blocks and within blocks.
- **[I]** NaN → column median before scaling.
- **[I]** KMeans with Euclidean distance assumes spherical, equal-variance clusters. Silhouettes of about 0.16–0.18 show the data is a continuum.
- **[E]** k is chosen by subsample ARI stability, tie-broken on silhouette, within a "business range" of 4–8. The resampling is called a bootstrap but is 80% subsampling without replacement.
- **[E]** ⚠ ANOVA on the same features used to cluster is presented as "validation". This is circular, has no multiple-testing correction, and violates normality and equal-variance assumptions.
- **[E/I]** ⚠ Newsvendor CR uses **list-price** `margin_usd` × multiplier for understock cost, while the VALUE block uses net margin: two margin bases in one pipeline. Stockout cost is linear, with no substitution or backorder distinction.
- **[E]** The CSL comes from value rules on top of a flat CR. The reports admit this.
- **[I]** Target case fill = CSL − 0.02 (arbitrary). z = Φ⁻¹(CSL) assumes Normal lead-time demand.
- **[I]** ⚠ The input file `sku_customer_service_targets.csv` (98/96, 95/92, 90/88) is loaded but **ignored**; it is never reconciled with the cluster CSLs.
- **[I]** Cluster names are rule-generated strings. Downstream MEIO keys on them, so a renamed archetype breaks consumers.

### 2.3 v3 specifics
- **[E]** "Features are measured outcomes, not parameters." Largely untrue in the code:
  - **[I]** `net_revenue = revenue_usd` overwrites net revenue, so **trade spend is never deducted** (L126–128).
  - **[I]** "Lead time" = order delivery − order date, i.e. customer delivery time (about 3 days), not replenishment lead time (L173–183).
  - **[I]** OTD = share of orders at or below the SKU's *own median* lead time, about 50–67% by construction (L177).
  - **[I]** Fill rate uses echelon-total shipments, so fill = 100% and lost margin = 0 for every SKU (L186–195).
  - **[E]** `write_off_units` is hard-coded to 0 (L198–200).
- **[I]** Margin feature: log1p(max(margin, 0)), so negative-margin SKUs collapse to 0. It is a 156-week total named "annual" (L138).
- **[I]** Missing OTD is filled with 90.0, and missing lead time with the lane mean (L202–206).
- **[I]** Cost parameters are merged on `category`, which no longer exists, so **v3 cannot run on current inputs** (L209).
- **[I]** Overstock = holding × (7 + LT)/365 + unit cost × min(cycle/shelf, 1). CR is clipped to [0.85, 0.99] (L211–216).
- **[E]** Drop features with CV < 0.02. Drop shelf life if its minimum exceeds 365 days (L294–299).
- **[E]** KMeans settings: random_state 42, n_init 25 (diagnostics) and 50 (final). Stability uses 10 subsamples, each refit with the same seed (L241–330).
- **[E]** Labels require z ≥ 0.6, assigned greedily with each label used at most once. Plausibility gates: shelf life ≤ 180 days for "Perishable", ≥ 1.05× median for lead-time labels. Value tier: Flagship ≥ 1.6× concentration, Long-Tail ≤ 0.6× (L369–423).
- **[E]** CSL ladder [0.88 … 0.99]. Flagship floor 0.98, Long-Tail cap 0.95, short shelf life → [0.90, 0.93], erratic → 0.88/0.90 (L254, L437–450).
- **[E]** Review policies by behaviour (e.g. "no statistical SS" for erratic SKUs). This is rule text only and never validated (L256–267).
- **[E]** The v3 report (k = 5, 8 features, fill 79–93%, OTD 90%, cross-dataset ARI 0.829) **does not match** the shipped output (k = 4, 10 features, fill 100%, OTD 58–67%). The report came from a different dataset.

### 2.4 v4 specifics (changes from v3)
- **[E]** Annualise totals by × 52 / number of periods, assuming stationarity (L119–131).
- **[E]** Value uses order `net_margin_usd` / `net_revenue_usd` (fixes the v3 bug). Log margin is floored at max(p1, 1.0) (L121–134).
- **[I]** Base CV now excludes promo *lines*, not promo weeks, so promo-week effects leak into "base" demand. ddof = 0.
- **[E]** Promo week = *planned* lift > 0, so unplanned promotions count as baseline (L166–167).
- **[I]** The actual > 0 filter is removed, so 35.9% zero-actual rows are scored. Lift WMAPE reaches up to 169 (L174–178).
- **[I]** SKUs with no promotions get lift WMAPE = baseline WMAPE (L185).
- **[E]** ⚠ Lead time and LT CV come from `inventory.cum_lead_time_mean/std_days`, which is **constant across weeks** (verified). It is a static network parameter weighted by order quantity, so SUPPLY_RISK encodes geography (L197–216).
- **[I]** Fallback lead time is weighted by lane *capacity* as a proxy for flow (L217–241).
- **[E/I]** OTD = quantity-weighted receipt `on_time_flag`, which is about 90% by construction (L243–247).
- **[E]** Write-off rate's denominator double-counts shipments across echelons (L262–274).
- **[E]** Near-constant drop rule: |CV| < 0.02 or variance ≤ 1e‑6. ⚠ The v3 shelf-life semantic drop rule was **silently removed**, so shelf life (3–4 years, economically meaningless) became the third-strongest feature (F = 250) and gave PERISHABILITY 20% of the distance metric.
- **[E]** Winsorise at p1/p99 before scaling (L337–351).
- **[E/I]** k range 4–8. KMeans random_state 42, n_init 10. 25 subsamples refit with n_init 5. **No final refit** (L381–406).
- **[E/I]** Naming:
  - The highest *margin-share* cluster is named Flagship (Promo-Driven if promo share ≥ the median).
  - The lowest-WMAPE cluster is Mid-Tier Core if its margin share is ≥ 10%.
  - **Everything else is "Long-Tail"**, whatever its value share.
  - Supply-Risk is always assigned to the cluster with the highest LT CV, with no magnitude test. "Forecast-Challenged" can be skipped because of an `elif`. "Erratic" is a fallback that never checks CV.
  - The docstring says variability is considered; the code has `pass` (L445–476).
- **[E]** Cost parameters are a simple average across DCs (L492–507).
- **[E]** ⚠ `span_days = 728` is hard-coded, but the data spans 1,092 days (L511).
- **[E/I]** Overstock = **0.5 × annual holding** (an excess unit is assumed to sit about 6 months) + 0.15 × list × obsolescence risk. The 15% salvage loss is arbitrary (L517–518).
- **[E]** CSL: base = clip(median CR, 0.85, 0.995). Revenue share ≥ 30% → at least 0.97; ≤ 3% → at most 0.90. No ladder snapping. The docstring says "discrete ladder exactly as v3", which is false (L524–543).
- **[I]** The CSL floor and cap use **revenue** share while naming uses **margin** share.
- **[E]** The v4 report says "≥ 30% net-margin share", but the code uses revenue share. The report also hand-renames a cluster because the automatic "Supply-Risk" label was wrong.

### 2.5 v5 specifics (canonical)
- **[E]** The PERISHABILITY block is removed by design (shelf life and write-off rate are excluded) on the grounds that it is an obsolescence decision, "not fill-rate". The segmentation report gives a *different* rationale ("no live expiry trade-off") (L11–23, L87–106).
- **[E]** Overstock = 0.5 × holding only. CR ≈ 0.95–0.96 for nearly every SKU (median 0.955) (L549–550).
- **[E]** `otd_pct` is dropped as near-constant (CV 0.0159). Whether it survives depends on the data run.
- **[I]** ⚠ Survivors: 8 features in 4 blocks at 25% each. lead_time_mean (CV 0.032) and lead_time_cv (CV 0.037) **barely clear** the 0.02 cut, yet take 25% of the distance.
- **[E]** k = 6 chosen by ARI 0.848, although k = 5 had a higher silhouette.
- **[E]** ⚠ CSL rule is identical to v4. "Long-Tail Erratic 2" has a revenue share of 0.030044, so it misses the 3% cap and gets **0.957**, while Long-Tail Erratic (2.4% share) gets 0.90 (L556–575).
- **[I]** ⚠ Labels contradict the data:
  - "Long-Tail Supply-Risk" differs from the next cluster by 0.0001 in LT CV; its real signature is the **highest baseline WMAPE**.
  - "Long-Tail Erratic 2" has the *lowest* LT CV and is not erratic.
  - "Long-Tail Erratic 3" (17 SKUs) is a promo-WMAPE / sparse-actuals artefact.
  - No cluster is named Forecast-Challenged.
- **[I]** Flagship Core and Mid-Tier Core both get 0.97 (Mid-Tier has 38.6% of revenue), so the tier label carries no policy difference.
- **[I]** No seed sensitivity, no partition-stability check against v4, and no check that names persist across runs.
- **[E]** Outputs go to `output datasets/segmentation/`, which MEIO, the app and the agents read directly. Older MEIO scripts still expect v4 archetype names.
- **[E]** Stale comments: "candidate set of 11" vs 9. "Winsorized after scaling" is wrong; it happens before.

### 2.6 SHAP analysis
- **[E]** An XGBoost surrogate is assumed to explain KMeans. It explains the surrogate, not the distance metric.
- **[I]** `num_class = 6` and the 8-feature list are **hard-coded**, so the script breaks if k or the feature survivors change.
- **[E]** Hyperparameters were hand-picked with no CV: 180 trees, depth 3, learning rate 0.05, subsample 0.9, λ 2, min_child_weight 3, seed 42.
- **[I]** The surrogate is trained on raw features, not the winsorised, block-weighted matrix, so block "importance" shares (e.g. VARIABILITY 30.9%) do not correspond to the enforced 25%.
- **[E]** Fidelity comes from a single 75/25 split: 92% accuracy, macro F1 0.927. The smallest cluster has about 4 test cases.
- **[I]** SHAP values are computed on all 500 SKUs using the 75%-trained model, so the explanations are mostly in-sample.
- **[I]** Only assigned-class contributions are kept (log-odds). The archetype is read from the first row. The same frame is passed twice to `write_report`. "mean_abs_shap" is described as a total but is a mean.

---

## 3. MEIO final weekly model: policy building

### 3.1 Network structure
- **[I]** The echelon is inferred from the first three letters of `dc_id`. An unknown prefix silently gets uplift 1.0 (L723, L878).
- **[I]** Active RDCs and CDCs are exactly the DCs with rows in `demand_forecast` (L957).
- **[E]** Lead times assume a strict tree with one inbound lane per node, chosen by the *lowest mean lead time*. Ties fall to quicksort order (L618–643).
- **[I]** RDC→RDC lanes are ignored (L648). Each CDC pools only at its "primary" RDC; there is no multi-sourcing or transshipment.
- **[I]** ⚠ RDC lead time comes from the fastest PDC→RDC lane regardless of SKU, while the twin routes by the per-SKU `sku_sourcing_map`. The docstring admits there are two different routings (L96–97, L648).
- **[I]** Plant→PDC is 1:1. Every RDC-SKU is assumed to have a single PDC source (unenforced) (L761–888).
- **[E]** PDCs are pushed. Their S is treated as a "holding ceiling" (L86–93).
- **[E]** Plant capacity only caps PDC throughput (family then plant). There is no production lead time, adherence or batching (L809–836). NaN capacity means unlimited (L825, L832).
- **[I]** No warehouse, storage or lane capacity limits.
- **[E]** ⚠ Clark-Scarf v2 adds RDC own demand to CDC demand (`mu_echelon = own + down`). This conflicts with the lib comment (L77–84) that removed pooling because RDC rows might already include downstream pull. In the data, RDC own demand is about 4.4× total CDC demand.

### 3.2 Demand and forecast
- **[E]** Demand is aggregated to (period, sku, dc) across customers. Missing actuals are treated as **0** (L31–35, L382–386).
- **[E]** 52-week years. A W53 would silently get seasonal index 1.0 (L316–318, L433).
- **[E]** Point forecast = forecast_qty × bias, where bias = Σactual/Σforecast over the trailing 13 in-sample weeks with forecast > 0, clipped to [0.5, 2.0], default 1.0 (L451–459, L534, L542).
- **[E]** ⚠ Target mean = **0.6** × (forecast × bias) + 0.4 × (deseasonalised level × seasonal index). The 0.6 is hand-set and never tuned (L236, L543).
- **[E]** ⚠ Seasonal index per (subcategory, week-of-year) from `orders` 2023–2025, pooled over SKUs and DCs, normalised to mean 1, clipped to [0.2, 5.0]. Historical promotions are baked into the "seasonal" shape, and **2025 is included** in the index used for the 2025 test weeks (L579–594).
- **[I]** Deseasonalised level = mean(actual ÷ index) over the trailing 52 weeks, including zeros. It is 0 with no history (L469–485).
- **[I]** ⚠ σ = target_mean × CV of **actuals**, i.e. demand variability, not forecast error. The available `forecast_baseline_wmape` is unused (L546).
- **[I]** Constant CV means σ scales with the week's mean: low-season weeks get less safety stock, promo weeks more (L546).
- **[E]** CV = simple mean of the 13-week and 52-week CVs (not of their variances), clipped at 3.0, ddof = 1 (L462–519).
- **[I]** CV is 0 when n = 1 or the mean is 0. ⚠ The first walk-forward week (2025‑W01) has CV = 0 for every pair, and the "52-week" window holds only 1–52 weeks throughout 2025 because `demand_forecast` history begins in 2025 (L516–517).
- **[I]** A missing CV becomes the cross-pair median, or 0.5 (L537).
- **[E]** The as-of week's actuals are assumed known when sizing the next week (L421–423).
- **[I]** ⚠ Weeks are treated as i.i.d.: variance over P is σ²·P, with no autocorrelation. The target week's μ and σ are used for the whole protection interval, so seasonal ramps inside the lead time are missed (L733; csv2 L120, L178).
- **[I]** ⚠ Demand is assumed independent across CDCs, between RDC own demand and its CDCs, across SKUs, and between demand and lead time: σ_echelon = √(own² + Σcdc²) with no correlation term (csv2 L319–325).
- **[E]** Distribution: Normal (MEIO Weekly); Gamma by method of moments conditional on L (Clark-Scarf v2). A zero mean or std becomes a near-point mass (shape 1e6) (csv2 L74–82).
- **[I]** Promotions get no separate treatment. They may be **double-counted**: in the forecast lift, again in the seasonal index, and again in the inflated CV.
- **[E]** Scenario demand surge scales μ and σ in the RDC/CDC plan only; PDC smoothing ignores it (csv2 L247–267; policy_builds L243).
- **[I]** No cold-start or analogue logic for new SKUs.

### 3.3 Lead time
- **[E]** Lead time comes from published `network_lanes` mean and std (days ÷ 7), not from receipts, and is not SKU-specific (L663–667).
- **[E/I]** ⚠ RDC and CDC lead times are **cumulative from the plant**, with variances summed as if independent. A CDC is sized for about 10.6 days plus review against a real lane of about 3.3 days, as if upstream held no stock (L651–659).
- **[E]** Lead-time distribution: Normal variance term (Weekly) / Gamma (v2), sampled or 64 midpoint quantiles. The twin uses a truncated Normal, rounded up to whole days, per hop (L733; csv2 L115–177).
- **[E]** Missing Plant→PDC lane defaults to (0.5, 0.07) days; the twin's default is (3.0, 1.0) (L765, L904).
- **[I]** PDC lead time is only the 0.5-day plant lane: no manufacturing cycle and no review period (L906–921).
- **[E]** Scenario lead-time overrides keep the base routing choice (L618–643).

### 3.4 Service level and targets
- **[I]** ⚠ The service metric is **cycle service level** (no stockout over R + L). Evaluation and selection use **unit fill rate**, and nothing maps one to the other. `target_case_fill_rate` is unused.
- **[E]** Live z comes from v5 `sku_clusters.csv`: 1.881 (0.97), 1.717 (0.957), 1.282 (0.90) (L250–279).
- **[E]** The 3-segment fallback ladder (2.05 / 1.64 / 1.28) is dead code (L238–285).
- **[E]** z is not cost-derived. No critical ratio is computed in MEIO (L74–76).
- **[E]** z is clipped to [0.84, 3.10]. The comment says "diagnostic only", which is false for Clark-Scarf v2, where it drives the quantile (L240, L304; csv2 L362).
- **[I]** The same CSL applies at every echelon for a SKU. There is no internal service target.
- **[E]** Precedence: z_by_sku → Z_OVERRIDE (empty) → cluster → segment → 1.28 → scenario hook (L271–301).
- **[E]** Sigma uplift PDC 1.45 / RDC 1.25 / CDC 1.00 is copied from the generator. In Clark-Scarf v2 it applies only to the RDC's own σ, not to pooled σ (L72–73, L237; csv2 L323–325).

### 3.5 Safety stock and DDLT math
- **[E]** P = R + L, with R = 1 week deterministic (L230).
- **[E]** MEIO Weekly: SS = z·√(P·σ² + μ²·σ_L²), s = μP + SS (L732–736).
- **[E]** Clark-Scarf v2 Monte Carlo: n = 4,000 (1,200 in evidence runs). L is drawn first, then D given P. The quantile uses linear interpolation; SS = max(0, q − mean) (csv2 L109–158).
- **[E]** Quadrature (the production default): 64 midpoint L-nodes and 40 bisection steps. It drops the outer 1/128 of lead-time tails, which is negligible at CSL ≤ 0.97 (csv2 L172–210).
- **[I]** For CDCs, rop is the raw quantile (it can sit below the mean) while SS is floored at 0, so rop ≠ cycle + SS. This matters because the twin uses SS as a release reserve (csv2 L158).
- **[I]** ⚠ (s, S) undershoot is ignored: s is sized as if orders trigger exactly at s, so achieved service will likely fall below target.
- **[I]** ⚠ The policy is installation-based but sized with echelon quantities (see top-20 #12).
- **[I]** The quantile treats all shortfalls as backorders, while the twin's CDCs lose sales.

### 3.6 Pooling (alpha) and netting
- **[E]** RDC: SS_r = α·SS_pooled + (1−α)·SS_independent, and the same blend applies to cycle stock (csv2 L378–380).
- **[E]** α is a per-SKU scalar in [0, 1], applied only at RDCs. The Variant default is 0.2, but the **live value is 0.6, with per-cluster overrides of 0.6–1.0** (csv2 L64–71, L238). The docs justify grid search because a closed-form optimum would hit a corner.
- **[E]** Netting credit applies to SS only: SS_p = max(0, SS_pooled − nd·ΣSS_CDC). With live nd = 0 and α = 1, the RDC carries the full echelon SS **on top of** every CDC's SS, a deliberate double buffer (csv2 L372).
- **[I]** S-netting always takes the full credit: S_p = max(rop_r + 1.5·μ_echelon − ΣS_CDC, rop_r), regardless of nd. At high α the RDC collapses to base-stock (csv2 L381).
- **[I]** The independent S has a 1.2× floor; the pooled S does not (csv2 L381–382).
- **[I]** RDC rows exist for SKUs with only downstream demand. At α = 0 they hold nothing (csv2 L301–308).
- **[E]** Per-cluster α was measured with the others at 0.6 and adopted jointly, assuming no interaction (params L378–379).

### 3.7 Policy form
- **[E]** (R, s, S) with weekly review. The twin orders S − IP when IP ≤ s.
- **[E]** S = max(s + μ·max(R, 1.5), 1.2·s), with 1.5 and 1.2 copied from the generator. Cover-based: no EOQ, no fixed ordering cost (L231–232, L737).
- **[E]** PDC: SS = z·√L·μ·1.45·0.5 (μ used as a proxy for σ), s = SS + μL, S = 4.5·μ + SS. The README lists a real-variance fix as open (L233, L921–923).
- **[E/I]** ⚠ PDC throughput = 5-week *centred* moving average of RDC target_mean, i.e. it uses future weeks. **It omits CDC demand passing through the RDC** (about 18% of pull) (L234, L849–869).
- **[I]** PDC throughput is scaled down to capacity rather than pre-built ahead of peaks. Rows with throughput ≤ 0 are dropped (L809–901).
- **[I]** No rounding to case, pallet, MOQ or whole units: values are rounded to 0.1. No max caps (L734–738; csv2 L393).
- **[E]** Scenario hooks can override demand, z, α, capacity, lanes and final rows.

### 3.8 Costs and objective
- **[E/I]** ⚠ **Sizing optimises no cost.** Holding and stockout costs are computed only as output columns. Cost enters only through the selection guard (L556–571, L74–76).
- **[I]** The cost join on subcategory gives every SKU a row for every DC, with no SKU-DC filter (L561).
- **[I]** No transport, ordering, budget, working-capital or obsolescence terms.
- **[I]** Each node is sized independently by formula plus a one-dimensional α blend. There is no network optimisation.

### 3.9 Rolling and roll-forward
- **[E]** ⚠ Production sizes **all 52 weeks of 2026 from one frozen as-of week (2025‑W52)**. Bias, CV and level are frozen (`roll_forward_policy` L58–122).
- **[I]** Forecast uncertainty does not grow with horizon: week 52 gets the same CV as week 1 (L546).
- **[E]** Weekly tool: target = as-of + 1. If as-of is the last week, it raises an IndexError (L122–131).
- **[E]** ⚠ Walk-forward evidence differs from deployment. Evidence: 105 warm-up weeks, then weekly refits, one week ahead, Monte Carlo n = 1,200. Deployment: 1–52 weeks ahead, frozen, quadrature (policy_builds L128–224).
- **[E]** Lead-time overrides are rebuilt each week, but CDC→RDC routing is fixed (L74–78).

### 3.10 Parameter-selection rule (`meio_parameters.py`)
- **[E]** References: netting 1.0, α 0.2, **cluster-α 0.6 (hand-maintained)**, cluster CSL = segmentation recommendation (L77–91).
- **[E]** Pass rule: paired by seed, n ≥ 5, mean gain > 2·SE and > 0, Δcost ≤ 2·SE, guards ≥ −2·SE, SE = sd/√n (L207–232). At df = 4, 2·SE is roughly one-sided p ≈ 0.06. "Cost not significantly worse" is not evidence of cost neutrality.
- **[I]** No multiple-comparison correction across 4 + 2 + 30 tests. The largest passing gain wins, which invites winner's-curse bias (L240–251).
- **[I]** ⚠ Seeds vary only simulator noise around one replayed year (see top-20 #6).
- **[E]** Coordinate search, never joint: netting at α = 0.2, then α at the chosen netting, then cluster α at 0.6 (L255–379).
- **[E]** Missing context columns are filled from a hard-coded `EVIDENCE` dict (L103–114).
- **[E/I]** Evidence lookup is first-folder-wins by file name, and the first row wins within a key. α and nd are rounded to 4 decimals (L172–196, L264).
- **[E]** A cluster sweep is used only if complete and run at the selected α and nd (L291–366). Exact float equality gates this (L321, L357).
- **[I]** The cluster-CSL ladder must beat a "segment ladder" arm built from a different file. This assumes `fill_delta_vs_actual_pp` and `fill_delta_pp` share a baseline (L320–328).

### 3.11 Data handling and engineering
- **[I]** Fallbacks: bias → 1.0, CV → median or 0.5, level → 0, seasonal index → 1.0, z → cluster then 1.28, unknown prefix → uplift 1.0, missing capacity → unconstrained, empty PDC frame → no error (L534–540, L685–724, L898).
- **[I]** The `--short-window` / `--long-window` CLI flags are only logged; the computation always uses 13/52 (meio_optimizer_weekly L111–220).
- **[I]** The repo root is located by folder depth (`parents[3]` / `parents[4]`), so the folder cannot move (runtime L42; params L75).
- **[I]** The cluster-z path is hard-coded (lib L257), independent of config `meio.parameters.clusters` (params L408), so the two can diverge.
- **[I]** A process-global `_CLUSTER_Z_CACHE` is never invalidated. `read_table` caches by (path, mtime, columns). Cached frames are assumed immutable (lib L247–260; runtime L88–106).
- **[E]** PCG64, seed 2026, with one generator advancing across weeks and variants. Under quadrature, the seed and n_sims have no effect. The README notes a 4 pp internal-fill swing from RNG stream position alone.
- **[E]** Bit-exactness with pandas (Kahan sums, Welford std, quicksort ties) is required because last-bit differences flip twin decisions (runtime L17–23), i.e. results are fragile at the last bit.
- **[I]** The rolling CLI always passes `--data-dir "input datasets"`, ignoring the config's `data_dir` (roll_forward_policy L123).
- **[E]** A hard-coded "static single-week policy was 195.3" is printed (roll_forward_policy L115).

---

## 4. MEIO final weekly model: evaluation and tuning

### 4.1 Evaluation design
- **[E]** Warm-up 2023‑W01 to 2025‑W01 (105 weeks); test 2025‑W02 to W52 (51 weeks). **One test year, with no second holdout** (validate_real_data L5–8, L64–76).
- **[I]** ⚠ "Real" demand = the synthetic history from `orders.parquet`.
- **[I]** ⚠ Leakage: the seasonal index uses 2025. Cluster z comes from a segmentation built on 2025. It is unverified whether the 2025 in-sample `forecast_qty` is ex-ante (it isn't fully; see §1.9).
- **[E]** ⚠ Committed reports say re-picking α on 2025 "would relocate the overfitting risk". `meio_parameters` now does exactly that.
- **[E/I]** ⚠ The "Actual" arm is the twin running the static `baseline_policy.csv` (with segment z 2.05 / 1.64 / 1.28), not observed outcomes. `inventory.parquet` history is never the comparator. The "model effect" mixes a ladder change with the model change.
- **[E]** History mode cold-starts at zero stock (a bug fix on 2026‑10‑18). Results from before and after the fix are not comparable.
- **[I]** Warm-up uses the baseline policy for both arms (105 identical weeks). Full-horizon cost deltas are read as test-window effects.
- **[I]** ⚠ No terminal inventory valuation. A policy that ends long or short is neither credited nor penalised, and stock in transit at the end is never received.
- **[E]** The per-cluster α diagnostic (`segment_alpha_explore`) runs forward-mode, single seed, frozen at W52, against a **different baseline** (`forward_policy`, 7,476 rows vs 7,768, about 6,900 rows differ).
- **[I]** Forward-mode demand = forecast × resampled error from the same data the policy was fitted on, so the policy is scored against its own noise model.
- **[E]** The MEIO Weekly vs actual comparison is **single seed**, so the choice of Clark-Scarf v2 over MEIO Weekly rests on single-seed evidence for one side.
- **[E]** The planner cost table uses a 4-week forward horizon, which is dominated by the opening state.

### 4.2 Metrics and cost accounting
- **[E]** Customer fill = 1 − Σunmet/Σdemand, unit-weighted and pooled across CDC and RDC-direct demand. It is not margin- or line-weighted.
- **[E]** Fill is measured on the test window. Cost KPIs cover the full 156 weeks, labelled `delta_attributable_to_test_window` (L156, L291).
- **[E]** Total landed cost = holding + stockout + transport. It excludes production, ordering, capacity, obsolescence and write-offs.
- **[I]** Stockout = `margin_usd` × shortfall for all customer shortfalls, including RDC backorders that are later filled. No goodwill cost.
- **[E]** ⚠ Transport is about 88% of total landed cost (76.8M of 87.8M), so total-cost rankings are mostly a transport-volume signal.
- **[I]** ⚠ The internal-fill denominator is generated by the policy under test, so it is not like-for-like. **Internal fill is never a decision guard**, which is how a drop from 93.2% to about 82% gets accepted.
- **[I]** `pct_demand_weeks_in_full` is worse for Clark-Scarf v2 (0.8644 vs 0.8717) and ignored.
- **[I]** Fill is rounded to 4 decimals and deltas to 3, a quantisation step comparable to the paired SE.
- **[I]** Own-cluster fill has no matching own-cluster cost.
- **[I]** `alpha_cost_table` prints a fixed narrative ("holding flat, stockout falls, transport rises") regardless of the numbers.

### 4.3 Statistical and robustness assumptions
- **[E]** 5 seeds: (2026, 7, 42, 123, 2027).
- **[E]** ⚠ Seeds change only Monte Carlo draws and twin lead-time and production noise, never demand. "5/5 seeds" therefore reads as robustness when it is only simulation noise.
- **[E]** ⚠ The alpha sweep chains one RNG through every build, so each α sees a different stream position. This is the same flaw the README says contaminated the first netting sweep. For example, α = 0.2 gives 0.9456/+$1.626M in the sweep vs 0.9451/+$1.673M in the headline.
- **[E]** `segment_alpha_explore` also chains by default; `--fresh-rng` is opt-in.
- **[E]** The headline, the α = 0 sweep and the seed-2026 multiseed share one Monte Carlo pass. Some "independent" evidence rows are literally the same run.
- **[I]** Pairing is by seed across different files and builds, which is valid only if both used the same twin streams.
- **[E]** Warm-up parity is checked on 4 sums (not transport or stockout) and only logged, never enforced.
- **[E]** Quadrature is accepted on median relative error < 0.5% (SS) and < 0.3% (ROP). Tail error is not bounded.
- **[E]** The scenario test is single seed and single scenario.

### 4.4 Parameter search spaces
- **[E]** ⚠ Portfolio α grid {0, 0.2, …, 1.0}, single seed. Only 0.4 and 0.6 are multi-seed. In the single-seed sweep, α = 0.8 and 1.0 are *better* on fill and cost but can never be selected.
- **[E]** Netting grid {0, 0.25, 0.5, 0.75, 1}, multi-seed only for {0, 1}. The winner, 0.0, is on the grid boundary.
- **[E]** CSL grid {0.90, 0.95, 0.97, 0.99} (+0.957 in v5). Own fill rises steadily, so the best value sits on the 0.99 boundary.
- **[E]** ⚠ Per-cluster α grid 0–1 in steps of 0.2. 4 of 6 live winners sit on the 1.0 boundary.
- **[I]** CSL→z uses the Normal quantile while sizing uses a Gamma-mixture quantile.
- **[E]** `alpha_cost_table` ignores per-cluster α and z, so it does not show the production policy.
- **[E]** Context values are hand-assumed for files lacking columns (netting files at α = 0.2, etc.).

### 4.5 Cluster and segment handling
- **[E]** ⚠ Mixed cluster vintages:
  - `validate_real_data`, `segment_alpha_explore` and `service_level_real_data` use **v4** (4 archetypes).
  - `segment_alpha_v5_real_data` and `meio_parameters` use **v5** (6 archetypes).
  - The default z everywhere is read from **live v5**, so the v4 α sweep mixes v4 groups with v5 z.
  - The segment breakdown uses a third grouping, `sku_master.segment`.
- **[I]** Results depend on whichever `sku_clusters.csv` is live, so re-running segmentation silently changes the "same" experiment.
- **[I]** Small clusters (16–17 SKUs) are tested with the same 5 seeds as large ones, so own-fill is fragile.
- **[I]** One α per cluster across all RDCs assumes cluster parameters generalise to every SKU and DC.

### 4.6 Scenario and stress tests
- **[E]** The only scenario is +25% demand in the Southeast for 4 weeks at offset 0 (first weeks of 2026), when opening stock still covers it.
- **[I]** KPIs cover the full 52 weeks, diluting a 4-week shock about 13×.
- **[E]** Hard-coded to α = 0.2, not the live configuration. PDC smoothing ignores the surge.
- **[E]** Result: the surge arm has *higher* fill than baseline, and "prepared" is below "unprepared". The test shows no measurable value from preparing. The README table (93.99/93.93/94.02, +0.09 pp) does not match the current CSV (93.79/93.84/93.82).

### 4.7 Tests and equivalence
- **[E]** "Bit-identical" = `np.array_equal`, with rtol 1e‑12 for service tables. OPTIMIZATION.md admits 1-ULP KPI differences.
- **[I]** Equivalence proves only optimised == original, not correctness. References are cached once (`_reference/DONE`) and never invalidated.
- **[E]** Coverage is slices only: 3 walk-forward weeks, 8–12-week twin horizons, 2 seeds, no overrides.
- **[E]** ⚠ `verify_headline_full.py` checks **netting 1.0 with a `forward_policy` warm-up**. The real headline uses netting 0.0 and `baseline_policy`.
- **[E]** ⚠ `test_meio_parameters` **fails 3 tests** today (v4 cluster names and file names); OPTIMIZATION.md claims 11/11.
- **[I]** ⚠ `smoke_run_all.py` does not sandbox step 10. Cleanup resolves the *real* config paths, writes to the real artifacts folder, and offers to delete real research folders.

### 4.8 Engineering (evaluation)
- **[E]** A baseline cache is accepted if `run_kpis.json` + `inventory.parquet` exist, without checking policy, seed, data or code version. A stale "actual" arm can survive fixes (actual fill 0.9308 is identical across all vintages).
- **[E]** Steps 4 and 9 write the same file names, so the order is forced.
- **[I]** Code and docs disagree on the twin run folder (`projections/` vs not). The `segments` stage check never finds the folder and always re-runs. Cleanup targets a path that never exists (the 8 GB of twin runs are not deleted).
- **[E]** ⚠ `run_all.py` runs the v4 per-cluster sweeps and **never runs `segment_alpha_v5_real_data.py`**, so the evidence driving live per-cluster α cannot be regenerated by `run_all`.
- **[E]** Evidence files missing from the first folder are silently filled from an older code vintage.
- **[E]** A failing step does not stop dependent steps.

---

## 5. Digital twin: canonical (`models/twin/digital_twin.py`), fast (`digital_twin_fast.py`) and reactive

*The fast twin is what `run_meio_then_twin.py` executes. Its core physics were confirmed bit-identical to the canonical twin on 8–10-week forward runs (static and real rolling policy). Its differences are listed in §5.13.*

### 5.1 Time and event sequencing
- **[E]** One step is one day; a week is 7 days. 52-week labels drift from ISO; 2026‑W01 starts on 2025‑12‑29 (L13, L92–98, L134–140).
- **[E]** History is 156 weeks; forward defaults to 52 weeks from week 157 (L99, L235).
- **[E]** Fixed daily order: receive → clear backlog → serve customers → (Monday) review + produce → fulfil internal orders → snapshot. Arrivals are usable the same day; customers are served before internal replenishment (L742–779).
- **[E/I]** Review runs on Monday, *after* Monday's demand. All nodes review at the same instant, so an RDC's order does not see the CDC orders just placed on it: a built-in one-week information lag (L771).
- **[I]** No order-processing or pick delay: orders ship the same day.
- **[E]** Unfilled internal orders are **cancelled** at week close, so an internal order lives at most one week (L989–1008).
- **[I]** Holding is charged on end-of-day on-hand, so same-day pass-through stock is free (L779, L1009).
- **[I]** ⚠ No warm-up exclusion. Cold-start history week-1 fill is **21.7%**, then 59.5%, 74.7%, 82.0%.
- **[I]** ⚠ No terminal-state accounting. In-transit stock at the end is never received; stock run down near the end goes unpenalised. The pipeline extends 90 days, with arrivals clipped (L94, L889).

### 5.2 Demand realisation
- **[E]** History mode replays `orders.order_qty` on the **delivery_date**, aggregated to (day, node, SKU) (L577–598).
- **[E/I]** ⚠ Deliveries outside the window are clamped to the last day, putting **175,024 units on 2025‑12‑28** (about 8× a normal day). This distorts `opening_position.csv`, which every forward run uses (L587–591).
- **[I]** Recorded orders are treated as unconstrained demand: no censoring, lost sales do not reduce future orders, no re-orders.
- **[E]** Forward demand = forward `forecast_qty` × a ratio resampled from the pair's own **2025 in-sample** actual/forecast, weighted by forecast volume (reproducing +8.5% bias), capped at 10, using only weeks with forecast > 0. Zero-actual weeks give ratio 0, which reproduces intermittency (L600–643).
- **[I]** ⚠ Ratios are i.i.d. per (node, SKU, week): no error autocorrelation, no cross-SKU or cross-DC correlation, no seasonal link. This probably overstates pooling benefits.
- **[I]** Pairs without history get ratio 1.0 (deterministic demand). Forecast 0 means demand 0 (no unforecast demand).
- **[I]** ⚠ Weekly demand is spread **uniformly over 7 days, weekends included** (multinomial). History has weekday-specific orders, a Saturday delivery peak and 1–3-week cadences, so forward demand is smoother than the history it was calibrated on (L645–651).
- **[I]** A horizon longer than 52 weeks gets zero demand silently (L608).
- **[I]** External demand exists only at RDCs (bulk) and CDCs (DSD). Customer advance notice (1–6 days) is not used.

### 5.3 Fulfilment and allocation
- **[E]** CDC shortfall = lost sale; RDC/PDC shortfall = backorder, decided purely by node prefix (L258).
- **[I]** Partial fills are allowed; there is no allocation among individual customers.
- **[E/I]** Backlog is cleared from *all* on-hand (OD wrongly says "from arrivals"). It never expires, takes priority over new demand, and the cleared units are **not counted** as shipments (L750–754).
- **[E]** ⚠ A parent releases only stock **above its own safety_stock** (`reserve_own_demand=True`). PDC safety stock is therefore permanently frozen (77k units in the forward policy) (L856–878). In the pipeline run, RDC→CDC replenishment fill is only 47–57%.
- **[E/I]** Shortfalls are rationed **pro rata to requested quantity**, recomputed daily, with no priority. Grants are floored, which can strand stock (3 children asking for 1 unit each with 2 units available → 0 shipped) (L843–854).
- **[E]** Single sourcing, with no transshipment, failover or RDC→RDC flow. A missing map row falls back to the fastest lane (0 pairs use the fallback today) (L270–291).

### 5.4 Replenishment policy execution
- **[E]** Periodic (R, s, S) with R = 7 days at RDCs and CDCs; order S − IP if IP ≤ s. PDCs never order (L783–796).
- **[E/I]** IP = on_hand − backlog + in-transit. The docs define IP without backlog (L790).
- **[I]** No case, pallet, MOQ or cap. Orders can be fractional, but shipments are floored, so fractional residuals show up as "internal unmet" (L794).
- **[I]** Only `reorder_point` and `order_up_to_level` drive orders. `safety_stock` is used **only** as the parent hold-back.
- **[I]** ⚠ PDC `reorder_point` and `order_up_to_level` are **never used**, so MEIO's PDC sizing affects only the hold-back.
- **[E]** A policy file is mandatory, and extra columns are ignored. Pairs not in the policy are inactive: they never order and are excluded from fill KPIs, but they still receive demand and accrue cost (292 opening pairs fall outside the forward policy) (L478–534).
- **[E]** A time-varying policy forward-fills. Weeks before a pair's first row are 0; weeks after the last row are clamped (L496–566).
- **[I]** Policy values are not validated: no check of SS ≤ s ≤ S, non-negativity or NaN.

### 5.5 Upstream production
- **[E]** Plants hold no stock; production is pushed to the single PDC (L918–980).
- **[E/I]** Demand signal = EMA (α = 0.25) of the **gross internal orders** placed on the PDC. The EMA **starts at 0 in every run**, including forward runs, so week-1 production is about 25% of need (L933–935).
- **[E]** Plan = sig + max(0, 2.5·sig − position). `FG_COVER_WEEKS = 2.5`, but the manifest's ASSUMPTIONS text says "~1.5". There is no upper ceiling, and the plan never drops below sig even when the PDC is overstocked (L944–948).
- **[I]** ⚠ **The allocation share is applied twice** (see top-20 #9).
- **[E]** Capacity is applied family-first then plant-wide, pro rata, with no priority and no carry-over of shortfall (L951–965). A NaN cap never binds.
- **[E]** Adherence is one U(0.90, 1.00) draw per plant per week, shared by all SKUs (L967).
- **[I]** Zero production lead time. No changeovers, batches, materials or yield. Plant→PDC always takes exactly 1 day, so the whole week lands on Tuesday.
- **[I]** `is_primary` ties produce several primaries.

### 5.6 Lead times and transport
- **[E/I]** Each SKU line on each ship day draws its own lead time from Normal(mean, max(sd, 0.05)), truncated at 0.5 days. The missing-lane default is (3.0, 1.0) (L317–335, L731–735).
- **[I]** ⚠ Arrival = ship day + max(1, ceil(lt)). Rounding up adds about **+0.5 day per lane** against the published means MEIO plans with (L889).
- **[I]** Lines from one dispatch arrive on different days (no truck consolidation), so orders cross. Lead times are stationary, with no congestion.
- **[I]** A Normal distribution with sd up to 7 days on a 12.9-day lane has a symmetric tail, not a right-skewed one.
- **[E]** Promise = mean + 1.2816·sd (p90). When the promise is missing it becomes mean × 1.4 (L111–113, L891).
- **[I]** ⚠ `on_time_flag` compares the *continuous* draw against the promise, so roughly 90% on-time holds by construction. The KPI tells you nothing about the policy (L913).
- **[E]** No lane capacity, no mode choice and no expediting.

### 5.7 Inventory physics
- **[E]** No shelf life, expiry, write-offs or FEFO. Shelf life ≥ 1,095 days against a 1,092-day history, which is only barely true, and false over history plus forward.
- **[E]** No warehouse capacity.
- **[I]** On-hand never goes negative. No damage, shrink, returns or quality holds.

### 5.8 Costs
- **[E/I]** Holding per unit-week = (storage + 10.2% × unit_cost) / 52 per (DC, subcategory). **Unit cost is the same at every echelon.** Storage differs only $0.076–0.094/yr, so upstream holding is only about 3–8% cheaper; the comment calls it "genuinely cheaper" (L359–375).
- **[I]** ⚠ In-transit (pipeline) stock is **free**, which favours long lanes and keeping stock moving.
- **[E/I]** Stockout = gross `margin_usd` × units short at **every** node, including RDC backorders that are later filled. `stockout_cost_multiplier` (1.69–2.45) is ignored. `lost_margin_usd` (CDC only) does not reconcile with `stockout_cost_usd` (L131, L765–768).
- **[E]** The margin basis is one switch, `MARGIN_COLUMN = "margin_usd"` (gross). The comment says net would be about 11–15% lower.
- **[I]** ⚠ Transport = quantity × lane $/unit, charged to the destination, linear, with no fixed shipment cost. **Plant→PDC freight is excluded** ($0.58–0.82/unit) (L898–900).
- **[I]** Lost sales slightly *lower* transport cost.
- **[E]** Total landed cost = holding + stockout + transport. It excludes production, ordering, handling, pipeline carrying, write-offs and expediting.
- **[I]** A missing (DC, subcategory) cost silently turns the KPIs into NaN.

### 5.9 KPIs
- **[E/I]** Customer fill is unit-based and immediate (Type 2): backorders count as unmet even if filled the next day.
- **[E]** "In full" = share of node-SKU-weeks with zero unmet. It is not order-line OTIF.
- **[E]** Internal fill = 1 − cancelled/requested, measured at the parent.
- **[I]** No cycle service level, ready rate, backorder duration or OTIF. Yet the MEIO model targets CSL.
- **[I]** `pct_weeks_at_zero_stock` includes pairs with no demand. Safety-stock value includes unusable PDC SS (planned, not realised).
- **[I]** `shipments` = demand − unmet. It is an identity, not measured, so the inventory identity fails wherever backlog was cleared. There is no `backlog` column, though the docs list one.
- **[I]** In receipts, `qty_ordered` and `short_qty` repeat the remaining balance on every partial-ship day, so summing them overstates. `period` means the ship week.
- **[I]** Fill KPIs cover active pairs only; costs cover all cells.

### 5.10 Opening state and modes
- **[E]** Policy and state come from separate files, so swapping the policy never changes the starting position (L35–39).
- **[E]** Without `--state`, the run cold-starts at zero, which every history backtest does (L538–554).
- **[I]** One in-transit quantity and ETA per pair. ETAs in the past arrive on day 0. A duplicate row overwrites the earlier one.
- **[I]** NaN `opening_stock` propagates as NaN (`getattr(...) or 0.0` does not catch NaN), a latent bug that the fast twin copies faithfully.
- **[I]** Opening backlog, open orders and EMA are always 0, so RDC backlog and plant EMA at the end of history are **dropped** at cut-over.
- **[I]** 358 active RDC pairs start forward runs with zero stock.
- **[I]** Scenario configs point `digital_twin.policy` at `baseline_policy.csv` (sized on history). Without `--policy` the twin scores that file.

### 5.11 Scenarios
- **[E]** Canonical twin: **only `demand_surge_pct`** is applied, as (1 + pct/100) on daily external demand at the affected nodes over [offset, offset + duration). A region resolves to CDCs plus RDCs; an unmatched region silently does nothing (SE L43–86).
- **[I]** The surge is rounded half-to-even per daily cell, so for slow movers the realised surge is **below nominal, often zero** (1 × 1.25 → 1).
- **[I]** The surge is deterministic: no added variance.
- **[I]** The MEIO-side surge window aligns with the twin's only if as_of = 156.
- **[E/I]** Any other `changes` key is **silently ignored** by the canonical twin. The `Digital Twin.md` claim of "lead-time and capacity overrides" holds only for the fast twin.

### 5.12 Randomness
- **[E]** `SeedSequence(2026)` spawns three streams: demand, lead time, production (L213–218).
- **[E/I]** ⚠ The "two runs differ only by the policy" claim holds **only for demand**. The lead-time stream is consumed per shipped line, so it de-syncs as soon as policies differ. Production stays aligned (4 draws per week). Paired comparisons therefore have more variance than claimed.

### 5.13 Fast twin: divergences from the canonical twin
| # | Divergence | Tested? |
|---|---|---|
| D1 | ⚠ **Full override engine** (economics, demand volume and volatility, opening state, lead-time changes, lane outages, plant capacity, adherence) exists **only** in the fast twin. Every scenario arm (prepared, unprepared, reactive) depends on it. | **No** |
| D2 | Reads parquet; the canonical twin reads CSV, and two of those CSVs are LFS pointers, so canonical history mode cannot run. | The equivalence test fails 3/3 |
| D3 | Notes text differs (.parquet vs .csv), so the equivalence test would fail even with CSV inputs. | Fails |
| D4–D6 | Output format (parquet), lean mode, run-folder layout, and CLI defaults (policy/state/seed pulled from config). | No |
| D7 | Legacy `changes` raise in history mode and are ignored when overrides are present. | No |
| D8–D9 | KPIs from arrays (1 ULP); service tables via polars (rtol 1e‑12). | Partly |
| D10–D11 | Caching (no mtime in some keys); in-process `simulate()`; history-state fix. Cache-hit path never exercised. | No |
| D12 | Core physics. | Matches on short forward runs. History, 52/156 weeks, other seeds and overrides untested. |
| D13 | ⚠ The manifest ASSUMPTIONS are **imported from the canonical twin and wrong for the fast twin**: "1.5-week cover" (it is 2.5), "holds back expected demand" (it holds back SS), "only demand surge applied" (the override engine exists). | n/a |

Fast-twin-only override assumptions:
- **[E]** Economics overrides are clipped at ≥ 0. A unit-cost increase cuts margin one-for-one (list price held).
- **[E]** `demand.volume` scales forecast lines before the error draw.
- **[E]** `demand.volatility` maps r → max(0, 1 + k(r − 1)).
- **[E]** Lead-time overrides are keyed by the **request** week, not the departure week.
- **[E/I]** ⚠ Lane outages delay departures to the closed window's end + 1 day, but stock is deducted immediately and sits uncosted in transit. Parents keep shipping into the closed lane. **Back-to-back windows are mishandled**, because the check uses the request day rather than the shifted day. The outage wait is excluded from `actual_lead_days` and `on_time_flag`.
- **[E]** Plant capacity overrides at plant scope also scale every family line. The adherence multiplier is capped at 100%.
- **[E]** Overrides apply to forward mode only.
- **[E]** Run folder `<output_root>/SCENARIO_…/<run>`, but `run_meio_then_twin.py` clears `output datasets/projections/SCENARIO_…/meio_policy`, **a path that doesn't match**.

### 5.14 Reactive twin (`reactive_twin.py`)
- **[E]** "Reactive" means MEIO **re-plans each week** from simulated observations. The twin's physics are unchanged: no expediting, emergency orders, allocation changes or intra-week reaction (RT L5–16).
- **[E]** Week 1 uses the given policy; re-plans happen 51 times, as of w − 1 for week w, with no look-ahead (RT L196–210).
- **[E]** "Surprise" design: the twin receives the actual overrides, MEIO receives none (RT L18–21).
- **[E]** ⚠ The planner observes **true, uncensored demand**, lost CDC sales included. A real planner sees only sales (RT L239–246).
- **[E]** Learning comes through the 13-week bias (clipped 0.5–2.0) and 52-week CV and level, so it lags up to 13 weeks.
- **[E]** A new plan applies only to pairs active in week 1; new pairs are ignored (RT L129–141).
- **[E]** Lead-time learner (RT L51–126):
  - Evidence window = dispatches in [today − settle − 14, today − settle], where settle = ceil(mean + 3σ) + 1 days (up to 36 days).
  - Needs ≥ 30 observations per lane; adjusts only if the change is > 5% **and** > 3 SE.
  - Ratio clipped to [0.5, 3.0]; std re-estimated only on lanes with mean ≥ 1.5 days; non-cumulative.
  - It ignores the twin's rounding-up of lead times.
  - ⚠ It is censored low under severe slowdowns, lags about 5 weeks on long lanes, and makes false-positive adjustments after recovery (seen in the SCENARIO_0004 log).
- **[I]** Learned corrections on lanes that are not the fastest do not affect sizing, because MEIO uses only the fastest inbound lane.
- **[I]** MEIO's Monte Carlo uses one advancing generator across the 51 re-plans, so the reactive arm differs from the prepared arm by Monte Carlo noise as well as by information.
- **[I]** ⚠ Under a legacy `demand_surge_pct` config, the reactive twin **silently does not apply the surge** (RT L163–165).
- **[I]** The manifest misstates the policy actually run (it is only in `reactive_policy.parquet`). The script writes to private `DemandPanel` fields, and the "dairy" metric is hard-coded.

### 5.15 Not modelled (twin)
Substitution, lateral transshipment, failover or dual sourcing in flow, expediting, returns, labour/dock/warehouse/lane capacity, shelf life/FEFO/markdowns, case/pallet/truck rounding, MOQ, fixed order or shipment costs, consolidation, advance demand information, priority allocation, demand censoring, customer cancellation, production setups/materials/WIP/production lead time, forecast-error autocorrelation, price- or service-dependent demand.

---

## 6. Where the components disagree with each other

| # | Generator | MEIO model | Twin | Consequence |
|---|---|---|---|---|
| 1 | Daily demand: weekday-specific, cadence-batched, lognormal noise | Weekly i.i.d. Normal/Gamma, CV of actuals | Forward: uniform 7-day split, i.i.d. resampled ratios | The model is calibrated on one demand process and scored on another |
| 2 | Lead time per lane, p90 promise | **Cumulative** plant→node, via the fastest lane | Per hop, per-SKU sourcing lane, rounded up to whole days (+0.5 d) | CDC buffers inflated; RDC sized on the wrong lane |
| 3 | RDC policy ignores child CDC demand | RDC pools own + CDC demand (α), echelon-sized | RDC compares s against **installation** IP; holds SS back from CDCs | The pooled buffer can't reach the CDCs it is sized for |
| 4 | PDC S = "holding ceiling", PDC SS ∝ μ | PDC SS/s/S formula on RDC-only throughput | PDC s and S ignored; PDC SS frozen; production = EMA × share² | MEIO's PDC decisions barely reach the physics; multi-plant SKUs are starved |
| 5 | FG cover ~1.5 weeks (doc) | — | 2.5 weeks (code) | Documentation drift |
| 6 | Service targets as CSL and case fill by segment | CSL quantile (cluster z) | Unit fill rate (Type 2), with lost sales at CDCs | Objective and metric are not mapped |
| 7 | Holding 10.2%, same unit cost everywhere; stockout multiplier 1.5–2.5 | Costs computed but unused in sizing | Gross margin per unit short, multiplier ignored; pipeline free; plant freight free | "Cost" means different things in each layer |
| 8 | Orders = uncensored demand | Treats actuals as demand | Replays orders as demand; reactive planner sees uncensored demand | Every layer assumes perfect demand observability |
| 9 | Shelf life inert, write-offs empty | No obsolescence term | No expiry | No layer can test perishability trade-offs |
| 10 | Segments are labels imposed on volume, seasonality and z | z from v5 clusters (value-threshold CSL) | — | Circular: clusters partly rediscover generator labels; CSLs are not economic |

---

## 7. Documentation vs code: stale or false claims (consolidated)

1. README and model docs say **α = 0.2**; live is **α = 0.6 + per-cluster α**. The README says long-tail wants *less* pooling; live gives long-tail **1.0 / 0.8**.
2. README headline (94.83%, +1.75 pp, +$1.83M) comes from the committed vintage; the current SCENARIO_0002 evidence shows about +1.52 pp and +$1.67M. The netting-1.0 headline appears with three different values (0.9427 / 0.9431 / 0.9414).
3. MEIO Weekly "+0.15 pp vs actual" now reads **−0.08 pp** (0.9300 vs 0.9308).
4. README says the cluster sweeps are "partially run"; they are complete. Its evidence table cites v4 files and netting 1.0, while the resolver reads v5 at netting 0.0.
5. "Quadrature verified bit-identical to Monte Carlo" (Clark Scarf v2.md) is false; it is a different estimate.
6. "Monte Carlo replaces every closed-form z·σ" is false for PDCs.
7. "No lookahead" is false (seasonal index, lift, customer shares, cluster z).
8. OPTIMIZATION.md "11/11 pass" and "bit-identical, verified" are false: 3 parameter tests fail and the twin equivalence test fails 3/3.
9. Twin manifest ASSUMPTIONS: wrong FG cover, wrong hold-back rule, wrong scenario support (fast twin).
10. OUTPUT_DOCUMENTATION lists a `backlog` column that is never written, defines IP without backlog, and says backlog is cleared "from arrivals".
11. `Digital Twin.md` claims lead-time and capacity scenarios for the canonical twin; only the demand surge is implemented there.
12. The generator claims "never hardcoded year", "strictly ex-ante forecast", "2025 in-sample policy basis", "16 of 76 lanes unused", "fully dual-sourced", "fail loudly", "YoY −61%". All are false or wrong.
13. Segmentation: v3/v4 reports don't match their outputs; v4/v5 docstrings claim a "discrete ladder as v3" and "variability considered", both false; the two perishability-exclusion rationales disagree.
14. `receipt service_level_by_cluster` says "segment ladder kept", but the base z is the v5 cluster ladder for all 500 SKUs.
15. `run_meio_then_twin.py` clears a `projections/` path the fast twin doesn't write to; `cleanup_research_artifacts` targets a path that never exists.

---

*Produced by seven parallel line-by-line reviews (generator ×2, segmentation, MEIO build, MEIO evaluation, canonical twin, fast/reactive twin), merged and de-duplicated. Line numbers are approximate (±a few lines). Data-verified claims: missing RDC rows (358), live α = 0.6 with 367 per-cluster SKUs, `units_per_case` 1–24, constant `cum_lead_time`, the 175k-unit clamp spike, multi- vs single-plant PDC fill, test failures, and the clustering knife-edge (0.030044).*
