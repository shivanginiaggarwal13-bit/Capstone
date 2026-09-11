# Supply Brain — SKU Segmentation on the Shared Input Dataset (v4, 2nd dataset drop)

**Same methodology as before, re-run on this project's newest "input datasets" folder**
(156 weeks of history — 2023–2025 — with `replenishment_receipts.csv`, `write_offs.csv`, and richer order/inventory economics added since the first v4 run.)

| | |
|---|---|
| Dataset | `input datasets/` — 500 SKUs, 156 historical weeks (2023–2025) + 52 forward weeks (2026) |
| Method | 11 candidate features in 5 blocks → **10 survive pruning** · k-means, **k=4 auto-selected** · newsvendor service-level ladder |
| Script | `segmentation_v4.py` (schema-adaptive version) |
| Outputs | all in this same folder (see Appendix) |

---

## 1. Executive summary

This is the cleanest run so far, and it looks structurally close to the original v3 "relaxed shelf-life" scenario — because this dataset drop **is** that scenario: 156 weeks of history and shelf life of 1,095–1,458 days (3–4 years), far beyond anything the simulation window can exhaust. Two things worth knowing up front:

1. **Only 1 of 11 candidate features drops out this time** — `write_off_rate`. The new `write_offs.csv` table exists but is **completely empty** (0 recorded events across 500 SKUs, 3 years), so the rate is exactly 0.0 for everyone: a genuine zero-variance statistical drop, not a missing-table one. Every other candidate — including `otd_pct`, which was uncomputable in the first v4 run — now has real (if narrow) signal and survives. Full table in §2.

2. **One 16-SKU cluster is defined almost entirely by a single extreme signal: catastrophic promo-week forecasting.** Cluster 3's average `forecast_lift_wmape` is **52.75** (i.e. the forecast is off by ~53x the actual demand, on average, during promo weeks) versus 0.40–2.43 everywhere else. This isn't a data error — every one of the 16 SKUs individually shows lift error in the 20–170x range (see §4.4) — but it does mean this cluster's identity rests on one dominant axis rather than a rounded profile, and the standard `chart_failure_modes.png` scatter is visually useless as a result (everything else gets squashed into a corner). Read the numbers in `sku_clusters.csv` directly for this cluster rather than the chart.

| Cluster (script label) | SKUs | % catalog | % revenue | % net margin | Concentration | Baseline WMAPE | Promo-lift WMAPE | Lead time (days) | Recommended CSL |
|---|---|---|---|---|---|---|---|---|---|
| 2 — Flagship Promo-Driven | 189 | 37.8% | **59.9%** | **61.0%** | 1.58× | 0.42 | 0.42 | 6.0 | **97.0%** |
| 1 — Mid-Tier Core | 190 | 38.0% | 37.7% | 36.8% | 0.99× | 0.44 | 0.40 | 6.6 | **97.0%** |
| 0 — Long-Tail Erratic | 105 | 21.0% | 2.0% | 1.9% | 0.10× | 1.63 | 2.43 | 7.6 | 90.0% |
| 3 — Long-Tail Supply-Risk | 16 | 3.2% | 0.4% | 0.3% | 0.11× | 1.74 | **52.75** | **8.5 (highest)** | 90.0% |

**Concentration is stark and clean this time.** Clusters 1 and 2 together are 75.8% of the catalogue and 97.6% of both revenue and margin. Clusters 0 and 3 (126 SKUs, 24.2% of the catalogue) return just **2.4%** of revenue between them — an unambiguous long tail, unlike the first v4 run where one "long-tail" label was actually misapplied to a near-average-value cluster.

**The economics genuinely don't differentiate clusters here** — same finding as the original v3 report on this scenario. Median newsvendor critical ratio is essentially flat: 95.3%–95.5% across all four clusters (chart_service_ladder.png). With shelf life running 3–4x longer than the simulated horizon, the obsolescence term in the cost model is ~0 for every SKU, so the recommended CSL split (97.0% for clusters 1–2 vs. 90.0% for clusters 0/3) is being driven entirely by the **value floor/cap business rule**, not by the underlying economics. That's a legitimate and important thing to say out loud in any write-up that uses this ladder: the "97% vs 90%" story here is "how much revenue this cluster carries," not "how differently its stockout/holding costs behave."

---

## 2. Feature engineering: what survived, and why this run differs from the first v4 run

| Feature | Block | Status | Why (this dataset) |
|---|---|---|---|
| `log_annual_net_margin` | VALUE | **kept** | From orders' own `net_margin_usd` (already nets off-invoice, trade fund, volume rebate, logistics allowance), annualized to a 52-week basis since this dataset spans 156 weeks. |
| `net_margin_pct` | VALUE | **kept** (not significant, F=2.45, p=0.063) | Realized net margin ÷ realized net revenue. |
| `demand_cv_base` | VARIABILITY | **kept** | CV of weekly non-promo order quantity. |
| `promo_depth_share` | VARIABILITY | **kept** (significant here, F=4.99, p=0.002 — flips from *not* significant in the first v4 run) | Share of annual units sold on promotion. |
| `forecast_baseline_wmape` | FORECASTABILITY | **kept** | Non-promo-week WMAPE. |
| `forecast_lift_wmape` | FORECASTABILITY | **kept** | Promo-week WMAPE — the dominant signal in this run (§1, §4.4). |
| `lead_time_mean_days` | SUPPLY_RISK | **kept** | Now read directly from `inventory.csv`'s own `cum_lead_time_mean_days` (end-to-end, sku/dc-native) rather than inferred from lane capacity. |
| `lead_time_cv` | SUPPLY_RISK | **kept this time** (was dropped in the first v4 run for near-zero variance) | The inventory-native cumulative lead-time fields carry real spread here (CV range 0.23–0.48 across SKUs) that the lane-level approximation used in the first run didn't expose. |
| `otd_pct` | SUPPLY_RISK | **kept, barely** (not significant, F=1.59, p=0.191) | `replenishment_receipts.csv` now exists in this dataset drop. Per-SKU on-time rate is pinned tight around 90% (mean 0.900, std 0.022, range 0.80–0.95) — this is very likely the same "OTD fixed near 90% by construction" artifact the original v3 report flagged, just not tight enough to trip the near-constant statistical test this time. Treat it as marginal, not a real driver. |
| `shelf_life_days` | PERISHABILITY | **kept** | Read from `sku_master`; ranges 1,095–1,458 days, uniformly 3–4x the simulated span, so no SKU's obsolescence risk differs meaningfully — it's a valid clustering feature, but see §5 for why it doesn't create real economic separation. |
| `write_off_rate` | PERISHABILITY | **dropped — STATISTICAL** | `write_offs.csv` exists this time but contains **zero rows** — every SKU's rate computes to exactly 0.0. |

**Net result: 10 of 11 features survive**, still across all 5 blocks. Block-balancing confirms every block still gets exactly 20% of total variance (`cluster_feature_block_balance.csv`) even though SUPPLY_RISK now carries 3 features and PERISHABILITY only 1.

**A methodology fix made between the two v4 runs, relevant to this output:** `forecast_lift_wmape` is a ratio (`Σ|error| / Σactual`) that's unbounded for low-volume/intermittent SKUs — this run's own cluster 3 is the proof, with values up to 169x. Before scaling for clustering, the script now winsorizes every feature to its 1st/99th percentile so one or two extreme SKUs can't dominate a whole feature's variance (and therefore the whole distance metric) by themselves. The **raw, un-winsorized** values are still what's reported in `sku_clusters.csv` and `cluster_service_levels.csv` — so the 52.75 average you see for cluster 3 in this report is the true, un-clipped picture; only the clustering math underneath it was protected from being skewed by it.

---

## 3. Statistical validation & k selection

**7 of 10 surviving features are significant at p < 0.05** (`cluster_anova.csv`):

| Feature | F | p-value |
|---|---|---|
| forecast_baseline_wmape | 433.4 | 3.8e-138 |
| forecast_lift_wmape | 332.3 | 3.1e-118 |
| shelf_life_days | 250.4 | 6.9e-99 |
| log_annual_net_margin | 60.1 | 3.9e-33 |
| lead_time_mean_days | 46.0 | 2.9e-26 |
| demand_cv_base | 45.2 | 7.8e-26 |
| lead_time_cv | 45.2 | 8.1e-26 |
| promo_depth_share | 5.0 | 0.0020 |
| net_margin_pct | 2.5 | 0.063 — not significant |
| otd_pct | 1.6 | 0.191 — not significant |

PCA (2 components) captures **45.9%** of total variance (up from 41.6% in the first v4 run) — a cleaner, more separable structure this time, visible in `chart_pca.png` as four fairly distinct clouds (cluster 3 sits off to the upper right, clearly apart from the rest).

| k | Inertia | Silhouette | Stability (bootstrap ARI) |
|---|---|---|---|
| **4** | **315.7** | 0.181 | **0.816 ← selected** |
| 5 | 293.7 | 0.153 | 0.546 |
| 6 | 273.6 | 0.143 | 0.625 |
| 7 | 260.7 | 0.133 | 0.486 |
| 8 | 248.6 | 0.131 | 0.468 |

k=4 wins outright on stability again, by a wide margin (0.816 vs. next-best 0.625).

---

## 4. The four clusters

### 4.1 Cluster 2 — Flagship Promo-Driven
**189 SKUs (37.8%) · 59.9% of revenue · 61.0% of net margin · concentration 1.58×**

The commercial core: best-in-portfolio forecast accuracy (baseline 0.42, lift 0.42 — essentially no promo-forecasting penalty at all), shortest average lead time (6.0 days), and the longest average shelf life of the four (1,368 days). 119 of 189 SKUs are Fast-Moving segment. This is the archetype the "Flagship" label is built for, and it earns it.

### 4.2 Cluster 1 — Mid-Tier Core
**190 SKUs (38.0%) · 37.7% of revenue · 36.8% of net margin · concentration 0.99×**

Near-identical forecast performance to the flagship cluster (baseline 0.44, lift 0.40) and almost exactly proportional value (0.99× concentration — it earns precisely its catalogue share, no more, no less). Its one real distinguishing trait: **shortest average shelf life** of the four clusters (1,178 days) — still nowhere near the 728-day simulated span, so this doesn't translate into different economics (§5), but it is the cluster's most literal fingerprint (see the dark cell in `chart_fingerprints.png`).

### 4.3 Cluster 0 — Long-Tail Erratic
**105 SKUs (21.0%) · 2.0% of revenue · 1.9% of net margin · concentration 0.10×**

A genuine, unambiguous long tail this time (unlike the equivalent cluster in the first v4 run). Forecast error is meaningfully worse than the two core clusters (baseline 1.63, ~4x worse) and demand variability is the highest in the portfolio (CV 0.77 vs. 0.45–0.65 elsewhere), but nothing here is as extreme as cluster 3. Category mix is fairly even; nothing about it screams a single category story.

### 4.4 Cluster 3 — "Long-Tail Supply-Risk" → read as **Long-Tail: Extreme Promo-Forecast Failure**
**16 SKUs (3.2%) · 0.4% of revenue · 0.3% of net margin · concentration 0.11×**

The smallest and most extreme cluster, and worth naming precisely because both parts of the automated label are individually defensible here but tell only part of the story:

- **Supply risk is real, if secondary:** this cluster's median lead time (9.0 days) and lead-time CV (0.39) are both the highest of the four clusters (vs. 6.2 days / 0.32 CV portfolio-wide) — a genuine, if modest, difference, and `lead_time_cv` did survive pruning this time (§2), so it's contributing real signal, unlike the first v4 run's version of this label.
- **But the defining trait, by an order of magnitude, is promo-week forecast collapse.** Every one of the 16 SKUs has `forecast_lift_wmape` between 22x and 169x — led by SKU0071 (169x), SKU0062 (82x), and SKU0007 (81x). These are low-revenue, promo-sensitive SKUs (7 of 16 are Beverages/Seasonal, several under $2,000/year in revenue) where a promo week's actual demand is small enough that even a modest absolute forecast miss produces an enormous relative error. Baseline (non-promo) forecast error for this cluster (1.74) is only modestly worse than cluster 0's, not extreme — **the failure is specific to promo weeks.**

*Reading:* this is a "do not trust the promo forecast for these 16 SKUs" list, with a secondary, smaller supply-risk flag attached. It's too small and too dominated by one axis to generalize a broader policy from — worth reviewing SKU-by-SKU (`sku_clusters.csv`, filter `cluster == 3`) rather than treating it as a segment to set one blanket policy for.

---

## 5. Service level ladder

Same newsvendor construction as the first v4 run:
```
understock_cost = margin_usd × category_stockout_cost_multiplier
overstock_cost   = 0.5 × holding_cost_per_unit_year_usd
                   + 0.15 × list_price_usd × max(0, 1 − shelf_life_days/728)
CR = understock_cost / (understock_cost + overstock_cost)
```

| Cluster | Median economic CR | Recommended CSL | Target case fill | Safety factor z |
|---|---|---|---|---|
| Flagship Promo-Driven | 0.955 | **97.0%** | 95.0% | 1.881 |
| Mid-Tier Core | 0.955 | **97.0%** | 95.0% | 1.881 |
| Long-Tail Erratic | 0.954 | 90.0% | 88.0% | 1.282 |
| Long-Tail Supply-Risk | 0.953 | 90.0% | 88.0% | 1.282 |

The economic CR is flat to three significant figures (0.953–0.955) across every cluster — because with shelf life 3–4x the simulated span, `max(0, 1 - shelf_life/728)` clips to 0 for every single SKU, so the obsolescence term vanishes and the whole ratio collapses to `margin × stockout_multiplier ÷ (that + 0.5×holding_cost)`, which is fairly stable across this portfolio. The 97.0%/90.0% split you actually see is the **value floor/cap rule** (≥30% net-margin-share clusters get floored to 97%; ≤3% clusters get capped to 90%) doing all the work, not the economics underneath it. This matches the original v3 report's own finding on its "relaxed shelf-life" dataset: when perishability can't bite, the newsvendor model alone can't tell your best SKU from your worst, and a value-based override is what actually produces a usable ladder.

---

## 6. Honest caveats

- **`chart_failure_modes.png` is not usable as delivered.** Cluster 3's extreme `forecast_lift_wmape` values (up to 169) compress every other cluster into an unreadable cluster of points near the origin. Read `sku_clusters.csv` directly for the promo-forecast story instead of this chart; a log-scale or capped-axis version would fix it if this chart needs to go in front of an audience.
- **`otd_pct` is marginal, not a real driver.** It technically survived the pre-clustering variance test (CV just above the 2% threshold) but isn't ANOVA-significant on the final clusters (p=0.19) and its cross-SKU range (80–95%) is exactly the "pinned near 90% by construction" pattern the original v3 report flagged as a data-generator artifact rather than real signal. Don't read operational meaning into it.
- **The service-level ladder is economically flat this run** (§5) — treat the 97%/90% split as a value-based business decision, not a data-driven economic one. If perishability risk needs to actually differentiate the ladder, it requires a dataset where shelf life falls inside (not outside) the simulated horizon for at least some SKUs — as the first v4 dataset drop did.
- **Cluster 3 is small (16 SKUs) and built almost entirely on one axis.** Treat any policy recommendation for it as SKU-level, not segment-level, guidance.
- **The data is synthetic** (per `DATASET_DOCUMENTATION.md`). The pipeline and its diagnostics are the transferable asset — re-derive feature values on real sell-through before committing targets.

---

## Appendix — outputs in this folder

| File | Contents |
|---|---|
| `sku_clusters.csv` | Per-SKU features, cluster, archetype label, `recommended_csl`, `target_case_fill_rate`, `safety_factor_z` |
| `cluster_service_levels.csv` | Cluster-level roll-up → revenue/margin share, archetype, CSL/case-fill/z |
| `cluster_anova.csv` | F-test significance of every surviving feature across the final 4 clusters |
| `dropped_features.csv` | The 1 pruned candidate (`write_off_rate`) and why |
| `cluster_feature_block_balance.csv` | Confirms all 5 surviving blocks carry an equal 20% variance share |
| `cluster_k_diagnostics.csv` | Inertia, silhouette, bootstrap stability for k = 4..8 |
| `run_metadata.json` | Machine-readable record of kept/dropped features, blocks, and selected k |
| `chart_pca.png` | Cluster separation in PCA space (45.9% of variance) — four distinct clouds, cluster 3 clearly isolated |
| `chart_fingerprints.png` | Standardised feature centroids per cluster — cluster 3's `forecast_lift_wmape` cell is the single dominant signal |
| `chart_service_ladder.png` | Economic (newsvendor) CR vs. recommended CSL per cluster — visual proof the economics are flat (§5) |
| `chart_failure_modes.png` | Baseline vs. promo-lift forecast error — **not readable as delivered**, see caveat in §6 |
| `segmentation_v4.py` | The script that produced everything above |

Data provenance: `input datasets/` per `DATASET_DOCUMENTATION.md` (500 SKUs, 2023–2025 actuals + 2026 forward, this drop adding `replenishment_receipts.csv` and `write_offs.csv`).