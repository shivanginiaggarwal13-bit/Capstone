# Supply Brain — SKU Segmentation on the Shared Input Dataset (v4)

**Same methodology as v3, re-run on this project's actual "input datasets" folder**

| | |
|---|---|
| Dataset | `input datasets/` — 500 SKUs, 52 historical weeks (2025) + 52 forward weeks (2026) |
| Method | 11 candidate features in 5 blocks → **8 survive pruning, still 5 blocks** · k-means, **k=4 auto-selected** · newsvendor service-level ladder |
| Script | `segmentation_v4.py` — self-contained, reads `../../input datasets` by default |
| Reproduce | `python segmentation_v4.py` from inside `Clustering & Segmentation/segmentation_v4/` |
| Outputs | all in this same folder (see Appendix) |

---

## 1. Executive summary

The portfolio resolves into **4 clusters** (not v3's 5 — this dataset's own diagnostics picked a different k; see §3). Two things stand out immediately, and both are genuine, data-driven findings rather than assumptions carried over from v3:

1. **One feature block effectively disappeared as a *statistical* matter, not a business one.** `lead_time_cv` — the SKU-level lead-time variability computed from this dataset's actual lanes — has almost no spread at all (std **0.006**, full range **0.29–0.34** across all 500 SKUs). It was correctly dropped before clustering. Two other candidates (`otd_pct`, `write_off_rate`) couldn't be computed at all because the source tables they need aren't part of this shared dataset. Net effect: **8 of 11 candidate features survive**, still spread across all **5** blocks because `shelf_life_days` (dropped in v3 as semantically inert) is a **live, discriminating feature here** — shelf life in this dataset ranges from ~12 days (Dairy & Bakery) to 370+ days (Frozen/Grocery), squarely inside the simulated span. §2 has the full pruning table.

2. **The strongest single cluster split in this data is seasonality, and it does not line up with revenue tail.** Cluster 1 (128 SKUs, **26.7% of revenue**) is **122 of the dataset's 150 Seasonal-segment SKUs** — it is a seasonality cluster that happens to also have the worst forecast error, not a small or unprofitable one. The automatic labeller in `segmentation_v4.py` names it *"Long-Tail Forecast-Challenged"* purely because it ranks by margin share and dominant weakness; that name **undersells its size** (its revenue concentration is 1.04×, i.e. almost exactly average value-per-SKU, the opposite of "long tail"). Read it as **"Seasonal Core — Forecast-Challenged"** instead. See §4.2 and the caveat in §6.

| Cluster (script label) | Better read | SKUs | % catalog | % revenue | % net margin | Margin rate | Concentration | Baseline WMAPE | Promo-lift WMAPE | Promo share | Recommended CSL |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 — Flagship Promo-Driven | Flagship / Fast-Moving core | 179 | 35.8% | **60.3%** | **60.7%** | 31.7% | 1.68× | 0.089 | 0.081 | 9.9% | **97.0%** |
| 1 — "Long-Tail Forecast-Challenged" | **Seasonal Core** (near-average value/SKU, worst forecast) | 128 | 25.6% | 26.7% | 27.6% | 32.6% | 1.04× | **0.219** | **0.224** | 9.4% | 85.0% |
| 2 — Long-Tail Erratic | True long tail, forecast is fine | 82 | 16.4% | 6.9% | 6.4% | 29.2% | 0.42× | 0.099 | 0.092 | 9.6% | 85.0% |
| 3 — "Long-Tail Supply-Risk" | **Long-Tail, Short-Shelf-Life & Erratic** (see caveat) | 111 | 22.2% | 6.1% | 5.3% | 27.2% | 0.28× | 0.110 | 0.104 | 11.6% | 85.0% |

**Concentration.** The top cluster alone (35.8% of the catalogue) carries 60.3% of revenue and 60.7% of net margin. The two smallest clusters (193 SKUs, 38.6% of the catalogue) return only **13.0%** of revenue between them — that part of the picture does look like a classic long tail.

**Promo depth is not a real discriminator here.** Unlike v3 (where promo share ranged 21%–36% across clusters and drove the "Flagship" narrative), promo_depth_share sits in a tight 9.4%–11.6% band across all four clusters in this dataset and is **not statistically significant** (ANOVA p = 0.227, §3). Cluster 0 is the flagship segment on margin and demand-variability grounds, not because it is more promo-dependent.

---

## 2. Feature engineering: what was computed, and what survived pruning

The script builds the same 11 candidates in the same 5 blocks as v3, then applies the same two-part pruning test (statistical zero/near-zero variance, or semantic/unavailable) — but the test is re-run against *this* dataset's own numbers, so the outcome differs from v3's.

| Feature | Block | Status | Why |
|---|---|---|---|
| `log_annual_net_margin` | VALUE | **kept** | Realised, post-discount margin (`revenue_usd − qty×unit_cost_usd`, summed per SKU over 2025), log-transformed. |
| `net_margin_pct` | VALUE | **kept** | Realised margin ÷ realised revenue (not the static `sku_master.margin_pct` — this one reflects actual promo discounting). |
| `demand_cv_base` | VARIABILITY | **kept** | Coefficient of variation of weekly *non-promo* order quantity. |
| `promo_depth_share` | VARIABILITY | **kept** (but not significant, see §3) | Share of a SKU's annual units sold on promotion. |
| `forecast_baseline_wmape` | FORECASTABILITY | **kept** | WMAPE (`Σ|actual−forecast| / Σactual`) on in-sample weeks with no promo activity for that sku/dc/customer/week. |
| `forecast_lift_wmape` | FORECASTABILITY | **kept** | Same WMAPE, restricted to weeks flagged as promo weeks (via `orders.on_promo`, rolled up to the forecast grain). |
| `lead_time_mean_days` | SUPPLY_RISK | **kept** | Per-SKU order-volume-weighted average of the inbound-lane lead time at each stocking DC that sells it. |
| `lead_time_cv` | SUPPLY_RISK | **dropped — STATISTICAL** | CV = 0.0191 across SKUs (std 0.006 on the raw feature, range 0.29–0.34). This dataset's lane network doesn't vary lead-time *variability* enough by SKU mix to carry clustering signal — every SKU sees roughly the same blend of dual-sourced lanes. |
| `otd_pct` | SUPPLY_RISK | **dropped — UNAVAILABLE** | No promised-vs-actual receipt table (e.g. a `replenishment_receipts.csv`) exists in this shared dataset, so a genuine on-time-delivery rate can't be derived — there's nothing to compute it *from*, unlike v3 where it existed but was constant by construction. |
| `shelf_life_days` | PERISHABILITY | **kept** | Read from `sku_master`. Genuinely live here: median shelf life is ~12 days for Dairy & Bakery vs. 370+ days for Frozen Foods/Grocery — a real, wide, operationally meaningful spread, the opposite of v3's relaxed-shelf-life dataset. |
| `write_off_rate` | PERISHABILITY | **dropped — UNAVAILABLE** | `inventory.csv` has opening/receipts/shipments/closing stock but no lot-level aging or expiry field, so realised write-offs cannot be derived from what's shipped in this dataset. |

**Net result: 8 features survive, spanning all 5 blocks** (SUPPLY_RISK and PERISHABILITY each keep exactly one feature). Block-balanced scaling still gives every *surviving block* an equal 20% share of total variance regardless of how many features it contributes — confirmed numerically in `cluster_feature_block_balance.csv` (each block: 20.0% ± 0.0%).

This is the main structural difference from v3: v3 dropped an entire block (PERISHABILITY) and kept 4 blocks; this dataset drops individual *features* from two different blocks but keeps all 5 blocks alive, because shelf life is a real signal here and lead-time variability isn't.

---

## 3. Statistical validation

**7 of 8 features are significant at p < 0.05** (`cluster_anova.csv`):

| Feature | Block | F | p-value |
|---|---|---|---|
| forecast_baseline_wmape | FORECASTABILITY | **355.0** | 4.9e-123 |
| forecast_lift_wmape | FORECASTABILITY | 322.5 | 4.2e-116 |
| lead_time_mean_days | SUPPLY_RISK | 133.5 | 2.1e-63 |
| shelf_life_days | PERISHABILITY | 104.4 | 2.0e-52 |
| demand_cv_base | VARIABILITY | 24.3 | 1.1e-14 |
| log_annual_net_margin | VALUE | 19.4 | 6.4e-12 |
| net_margin_pct | VALUE | 11.2 | 3.9e-07 |
| **promo_depth_share** | VARIABILITY | **1.5** | **0.227 — not significant** |

`promo_depth_share` survives the *pre-clustering* pruning test (it has real variance across SKUs, ~5%–35% between individuals) but doesn't turn out to separate the final 4 clusters — the clusters differ on margin, seasonality/forecastability, lead time and shelf life, not on how promo-heavy they are. That's a legitimate, disclosed result, not a defect in the pipeline: pruning removes features with *no* variance; ANOVA afterward is a diagnostic on whether a *surviving* feature happened to matter for *this particular* k and cluster split. PCA (2 components) captures **41.6%** of total variance — lower than v3's 57.5%, consistent with a less separable / more continuum-like structure this time (see `chart_pca.png`).

### Choosing k

| k | Inertia | Silhouette | Stability (bootstrap ARI) |
|---|---|---|---|
| **4** | **334.2** | 0.161 | **0.847 ← selected** |
| 5 | 310.6 | 0.144 | 0.636 |
| 6 | 285.2 | 0.153 | 0.680 |
| 7 | 267.7 | 0.156 | 0.694 |
| 8 | 254.8 | 0.150 | 0.694 |

Same selection rule as v3 (most reproducible partition inside a 4–8 business range, silhouette as tie-break) — but on this dataset **k=4 wins outright** on stability (0.847 vs. the next-best 0.694), rather than needing a tie-break. Silhouette is uniformly low (0.14–0.16) across every k, same as v3's finding: this is a continuum of archetypes, not well-separated islands.

---

## 4. The four clusters

### 4.1 Cluster 0 — "Flagship Promo-Driven" → read as **Flagship / Fast-Moving core**
**179 SKUs (35.8%) · 60.3% of revenue · 60.7% of net margin · concentration 1.68×**

The commercial engine of the catalogue, and — unlike v3's flagship — **not particularly promo-driven** (9.9% promo share, right in the middle of the four clusters' narrow 9.4–11.6% band). What actually sets it apart: the **best forecast accuracy in the portfolio** (baseline WMAPE 0.089, lift 0.081), the **lowest demand variability** (CV 0.348 vs. 0.46–0.55 elsewhere), and it's overwhelmingly Fast-Moving segment (134/179 SKUs). It also skews toward longer shelf life (avg 338 days) and carries the healthiest margin rate isn't actually the highest (31.7%, second to cluster 1's 32.6%) — its size, not its rate, is what makes it the flagship.

*Reading:* this looks like the "well-behaved, high-volume, easy-to-forecast" segment — protect it with a straightforward continuous-review policy; there's little forecast risk to manage.

### 4.2 Cluster 1 — "Long-Tail Forecast-Challenged" → read as **Seasonal Core**
**128 SKUs (25.6%) · 26.7% of revenue · 27.6% of net margin · concentration 1.04×**

**This is the most important relabeling in this report.** The script's automated namer calls this cluster "Long-Tail Forecast-Challenged" because among the three non-flagship clusters it has the worst forecast error — true — but "long-tail" is the wrong frame: at 1.04× concentration it earns almost exactly its proportional share of revenue, and its actual defining trait is composition: **122 of the dataset's 150 Seasonal-segment SKUs (81%) sit in this one cluster**, plus 16 more SKUs (44% of Dairy & Bakery, the shortest-shelf-life category). Forecast error here is roughly **2.5× worse** than the flagship cluster (baseline WMAPE 0.219 vs. 0.089) — seasonality is genuinely hard to forecast, and the clustering has correctly isolated that population.

*Reading:* this is a real, average-value segment whose core problem is seasonal demand shaping, not neglect or low value. Investment in seasonal-profile forecasting (not safety stock) is the highest-leverage fix here, same diagnosis v3 reached for its (differently-composed) forecast-challenged tail.

### 4.3 Cluster 2 — "Long-Tail Erratic"
**82 SKUs (16.4%) · 6.9% of revenue · 6.4% of net margin · concentration 0.42×**

A genuine long tail: below-average value per SKU, moderate-to-high demand variability (CV 0.463, tied with cluster 1) but **good forecast accuracy** (baseline WMAPE 0.099 — nearly as good as the flagship cluster). A mixed segment composition (57 Fast-Moving, 15 Intermediate, 10 Seasonal) and a longer average shelf life (180 days) than cluster 3.

*Reading:* the forecast isn't the problem here — the demand itself is erratic and the value doesn't justify heavy buffering. A min-max / lighter-review policy fits better than statistical safety stock.

### 4.4 Cluster 3 — "Long-Tail Supply-Risk" → read with caution, see caveat below
**111 SKUs (22.2%) · 6.1% of revenue · 5.3% of net margin · concentration 0.28×**

The smallest-value cluster on every axis: lowest margin rate (27.2%), lowest economic critical ratio (0.658, see §5), **highest promo share of the four** (11.6%), **highest demand variability** (CV 0.551), and by far the **shortest average shelf life** (90.5 days vs. 180–338 elsewhere) — 62 of its 111 SKUs are Dairy & Bakery, the category with a median 12-day shelf life. That combination — short shelf life, high demand variability, weak margin — is the genuinely distinctive story for this cluster.

**Caveat on the "Supply-Risk" name:** the automated labeller assigned this name because this cluster has the *highest average* `lead_time_cv` among the tail clusters — but `lead_time_cv` was **dropped before clustering** for having almost no variance across the whole 500-SKU population (§2: std 0.006, full range 0.29–0.34). The gap between this cluster's average (0.325) and the others (0.312–0.322) is real but tiny relative to that near-zero spread, and it played **no role** in forming the clusters. Read this cluster as **"Long-Tail, Short-Shelf-Life & Erratic,"** not as a supply-risk story — there isn't one in this dataset the way there was in v3 (whose Long-Tail Supply-Risk cluster was built on genuine lead-time CV up to 0.99).

---

## 5. Service level ladder

For each SKU, `segmentation_v4.py` computes a newsvendor-style critical ratio (`newsvendor_cr` in `sku_clusters.csv`):

```
understock_cost = margin_usd × category_stockout_cost_multiplier      (from cost_parameters.csv)
overstock_cost   = 0.5 × holding_cost_per_unit_year_usd
                   + 0.15 × list_price_usd × min(1, 728 / shelf_life_days)
CR = understock_cost / (understock_cost + overstock_cost)
```

Unlike v3's relaxed-shelf-life dataset — where the obsolescence term was exactly zero and every SKU's economics pinned at ~99% — this dataset's `overstock_cost` carries a real obsolescence term driven by `shelf_life_days` relative to the ~728-day (2-year) simulated span, so the economics **do** differentiate by cluster:

| Cluster | Median economic CR | Recommended CSL | Target case fill | Safety factor z |
|---|---|---|---|---|
| 0 — Flagship | 0.716 | **97.0%** | 95.0% | 1.881 |
| 1 — Seasonal Core | 0.714 | 85.0% | 83.0% | 1.036 |
| 2 — Long-Tail Erratic | 0.709 | 85.0% | 83.0% | 1.036 |
| 3 — Long-Tail Short-Shelf-Life | **0.658** (lowest) | 85.0% | 83.0% | 1.036 |

The recommended CSL applies a value floor (≥97% for any cluster carrying ≥30% of net margin — only cluster 0 qualifies) and a value cap (≤90% for any cluster carrying ≤3% of net margin — none fall that low here) on top of the raw economic ratio, clipped to an 85%–99.5% business range. Because none of the three tail clusters individually earns more than 3% of net margin as a *whole cluster* but none is starved to a floor either, all three land at the 85% clip floor. This is a coarser ladder than v3's five-tier 88–99% spread; a useful next iteration would tighten the floor/cap thresholds or work at the SKU level rather than the cluster level for the final CSL assignment.

---

## 6. Honest caveats

- **Two of the four archetype names generated by the script are misleading if taken at face value** (§4.2, §4.4). This report gives the corrected reading; if you re-use `sku_clusters.csv` / `cluster_service_levels.csv` downstream, prefer the cluster **number** and the characteristics table in §1 over the `archetype` string, or relabel the column before presenting it further.
- **Silhouette is low (0.14–0.16) at every k**, same finding as v3 — these are archetypes on a continuum, not well-separated groups. Bootstrap stability (0.847 at k=4) and the ANOVA table are the stronger evidence; boundary SKUs will need judgement.
- **`otd_pct` and `write_off_rate` are not just weak here — they're not computable from this shared dataset at all** (no promised/actual receipt log, no lot-level expiry tracking). If those tables get added to the shared dataset later, re-running this script will very likely surface them as live features.
- **`lead_time_cv` has almost no cross-SKU signal in this dataset's network** (§2) — worth checking whether that's a realistic feature of this network design (heavily dual-sourced, homogeneous lane mix) or an artifact of how SKUs are assigned to plants/lanes in the generator.
- **The service-level ladder is coarse** (§5) — three of four clusters land at the same 85% floor. Treat it as a starting point, not a final policy.
- **The data is synthetic** (per `DATASET_DOCUMENTATION.md` / `EDA_REPORT.md`). The pipeline and its diagnostics are the transferable asset; re-derive feature values on real sell-through before committing targets.

---

## Appendix — outputs in this folder

| File | Contents |
|---|---|
| `sku_clusters.csv` | Per-SKU features, cluster, archetype label, `recommended_csl`, `target_case_fill_rate`, `safety_factor_z` |
| `cluster_service_levels.csv` | Cluster-level roll-up → revenue/margin share, archetype, CSL/case-fill/z |
| `cluster_anova.csv` | F-test significance of every surviving feature across the final 4 clusters |
| `dropped_features.csv` | The 3 pruned candidates and why (1 statistical, 2 unavailable) |
| `cluster_feature_block_balance.csv` | Confirms all 5 surviving blocks carry an equal 20% variance share |
| `cluster_k_diagnostics.csv` | Inertia, silhouette, bootstrap stability for k = 4..8 |
| `run_metadata.json` | Machine-readable record of kept/dropped features, blocks, and selected k |
| `chart_pca.png` | Cluster separation in PCA space (41.6% of variance) |
| `chart_fingerprints.png` | Standardised feature centroids per cluster |
| `chart_service_ladder.png` | Economic (newsvendor) CR vs. recommended CSL per cluster |
| `chart_failure_modes.png` | Baseline vs. promo-lift forecast error, colored by cluster — this is the clearest visual evidence for the seasonality finding in §4.2 |
| `segmentation_v4.py` | The script that produced everything above |

Data provenance: `input datasets/` per `DATASET_DOCUMENTATION.md` and `EDA_REPORT.md` (auto-generated 2026-09-08, 500 SKUs, 2025 actuals + 2026 forward).
