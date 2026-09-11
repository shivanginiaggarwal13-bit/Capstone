# Supply Brain — SKU Segmentation v5: Service-Level-Focused Feature Set

**Same core methodology as v4, with one deliberate scope change: perishability is out.**

| | |
|---|---|
| Dataset | `input datasets/` — 500 SKUs, 156 historical weeks (2023–2025) + 52 forward weeks (2026) |
| Method | **9** candidate features in **4** blocks (VALUE, VARIABILITY, FORECASTABILITY, SUPPLY_RISK) → **all 9 survive pruning** · k-means, **k=6 auto-selected** · newsvendor service-level ladder (holding cost only) |
| Script | `segmentation_v5.py` |
| Outputs | all in this same folder (see Appendix) |

---

## 1. What changed from v4, and why it matters

**`shelf_life_days` and `write_off_rate` are no longer candidate features at all** — not pruned by statistics, excluded by design before clustering even runs. The reasoning, recorded verbatim in `dropped_features.csv` under a new `EXCLUDED_BY_DESIGN` reason type: perishability speaks to *obsolescence/overstock* risk — a different question than *"what fill rate should this SKU carry,"* which is what this segmentation exists to answer. The candidate set is now the 9 features that actually feed a service-level decision:

- **VALUE** (what a stockout costs): `log_annual_net_margin`, `net_margin_pct`
- **VARIABILITY** (how uncertain demand is): `demand_cv_base`, `promo_depth_share`
- **FORECASTABILITY** (how much of that uncertainty is a forecasting gap vs. irreducible): `forecast_baseline_wmape`, `forecast_lift_wmape`
- **SUPPLY_RISK** (how uncertain and slow replenishment is): `lead_time_mean_days`, `lead_time_cv`, `otd_pct`

The newsvendor cost model was changed to match: the obsolescence term (keyed off `shelf_life_days`) is gone, so `overstock_cost` is holding cost only. This keeps the segmentation and the service-ladder economics internally consistent — shelf life doesn't get to influence the answer through the back door if it's not allowed to influence the clustering.

**This narrower feature set produced a genuinely different cluster structure, not just a smaller one.** With shelf life included (the previous v4 run), k=4 was the clear stability winner (0.816 vs. next-best 0.625). Remove it, and k=4's stability **collapses to 0.535** while k=5/6/7 all cluster tightly in the 0.77–0.82 range, with **k=6 winning outright (0.824)**. Shelf life had been acting as a structural anchor separating "core" from "tail" cleanly at 4 groups; without it, the remaining 9 features resolve into a finer 6-way split. PCA also improved to **49.2%** of variance explained (vs. 45.9% in the last v4 run) — the best-separated run so far.

---

## 2. Executive summary

| Cluster | Archetype | SKUs | % catalog | % revenue | % margin | Concentration | Baseline WMAPE | Promo-lift WMAPE | Recommended CSL |
|---|---|---|---|---|---|---|---|---|---|
| 5 | Flagship Core | 80 | 16.0% | **52.1%** | **60.5%** | 3.26× | 0.30 | 0.32 | **97.0%** |
| 0 | Mid-Tier Core | 100 | 20.0% | 28.3% | 20.1% | 1.42× | 0.39 | 0.58 | **95.4%** |
| 1 | Long-Tail Erratic | 123 | 24.6% | 14.8% | 14.8% | 0.60× | 0.44 | 0.40 | 95.5% |
| 2 | Long-Tail Erratic 2 | 96 | 19.2% | 2.4% | 2.5% | 0.13× | 0.63 | 0.50 | 90.0% |
| 3 | Long-Tail Forecast-Challenged | 85 | 17.0% | 2.0% | 1.7% | 0.12× | 1.85 | 2.69 | 90.0% |
| 4 | Long-Tail Supply-Risk | 16 | 3.2% | 0.4% | 0.3% | 0.11× | 1.74 | **52.75** | 90.0% |

Reading this top to bottom is now a much cleaner value ladder than the last v4 run produced: **concentration falls almost monotonically** from 3.26× (flagship) down to 0.11× (the two smallest tail clusters), with only clusters 2 and 3 essentially tied. That's a direct consequence of removing shelf life — the previous run had two large, nearly-tied "core" clusters (Flagship 1.58×, Mid-Tier 0.99×) sitting well above three long-tail clusters; this run spreads that same population across six tiers with a smoother gradient.

**The single most important finding carries over unchanged from every prior run of this dataset: cluster 4's promo-forecast collapse.** All 16 SKUs individually show `forecast_lift_wmape` in the 22×–169× range (same SKUs as the last two reports — SKU0071, SKU0062, SKU0007, etc.) That this finding is stable across three different feature-set configurations (v4 first drop, v4 second drop, v5) is itself worth noting: it isn't an artifact of any one clustering setup, it's a real property of those 16 SKUs' promo-week demand data.

---

## 3. Statistical validation & k selection

**8 of 9 features are significant at p < 0.05** (`cluster_anova.csv`):

| Feature | Block | F | p-value |
|---|---|---|---|
| forecast_baseline_wmape | FORECASTABILITY | **619.9** | 3.2e-210 |
| forecast_lift_wmape | FORECASTABILITY | 198.9 | 8.0e-116 |
| demand_cv_base | VARIABILITY | 110.9 | 2.1e-78 |
| lead_time_mean_days | SUPPLY_RISK | 97.3 | 3.1e-71 |
| log_annual_net_margin | VALUE | 86.2 | 4.7e-65 |
| lead_time_cv | SUPPLY_RISK | 81.8 | 1.8e-62 |
| promo_depth_share | VARIABILITY | 60.0 | 8.7e-49 |
| net_margin_pct | VALUE | 29.7 | 2.1e-26 |
| **otd_pct** | SUPPLY_RISK | 2.2 | **0.051 — not significant** (right at the threshold) |

`otd_pct` remains the weakest feature in the set, same as every prior run — its cross-SKU range (80–95%, mean 90.0%, std 2.2%) is consistent with the "pinned near 90% by construction" pattern the original v3 report flagged as a data-generator artifact. It clears the pre-clustering variance test by a comfortable margin here but sits right at the ANOVA significance boundary (p=0.051) — treat it as present but not a real driver of the final clusters.

Block-balancing confirms all 4 surviving blocks carry exactly 25% of variance each (`cluster_feature_block_balance.csv`), regardless of SUPPLY_RISK contributing 3 raw features against 2 for the others.

| k | Inertia | Silhouette | Stability (bootstrap ARI) |
|---|---|---|---|
| 4 | 299.8 | 0.172 | 0.535 |
| 5 | 268.0 | 0.169 | 0.811 |
| **6** | **245.7** | 0.169 | **0.824 ← selected** |
| 7 | 232.7 | 0.159 | 0.773 |
| 8 | 220.2 | 0.167 | 0.715 |

Silhouette is flat and low (0.16–0.17) across every k, same finding as every prior run — this remains a continuum of archetypes rather than well-separated islands. Stability is the deciding signal, and k=6 wins clearly.

---

## 4. The six clusters

### 4.1 Cluster 5 — Flagship Core
**80 SKUs (16.0%) · 52.1% of revenue · 60.5% of net margin · concentration 3.26×**

The most concentrated flagship cluster of any run so far — 16% of the catalogue carrying 60% of margin. Best-in-portfolio forecast accuracy (baseline 0.30, lift 0.32), shortest lead time (5.2 days), lowest demand variability (CV 0.36). Fast-Moving-heavy (55/80) with a meaningfully higher Beverages share (37.5%) than the portfolio average. The highest `net_margin_pct` of any cluster (39.7%) despite not being the single highest-margin-dollar group per SKU — this is quality, not just size.

### 4.2 Cluster 0 — Mid-Tier Core
**100 SKUs (20.0%) · 28.3% of revenue · 20.1% of net margin · concentration 1.42×**

Still meaningfully above-proportional value, with good (if not flagship-tier) forecast accuracy (baseline 0.39) and the **highest promo depth share of any cluster** (37.1% — notably more promo-driven than the flagship cluster, an interesting split from the old v4 4-cluster run where promo share barely varied at all). Lowest `net_margin_pct` (25.4%) of the two core clusters — consistent with being the more promo-dependent one.

### 4.3 Cluster 1 — Long-Tail Erratic
**123 SKUs (24.6%) · 14.8% of revenue · 14.8% of net margin · concentration 0.60×**

The largest single cluster and a genuine — if moderate — long tail: below-proportional value but not dramatically so, decent forecast accuracy (0.44, close to the core clusters), lowest promo share (23.8%) and lowest demand variability among the tail clusters (CV 0.38). The least "erratic" of the three clusters carrying that dominant weakness label — closer to "smaller core" than "problem segment."

### 4.4 Cluster 2 — Long-Tail Erratic 2
**96 SKUs (19.2%) · 2.4% of revenue · 2.5% of net margin · concentration 0.13×**

A sharp step down from cluster 1 — genuine long tail territory. Distinguishing trait: by far the **highest demand variability in the portfolio** (CV 0.82, roughly double the core clusters), with 32.3% Snacks & Confectionery (vs. ~20% baseline). Forecast accuracy (0.63) is worse than the core clusters but nowhere near cluster 3 or 4's territory — the problem here is genuinely erratic demand, not a forecasting failure.

### 4.5 Cluster 3 — Long-Tail Forecast-Challenged
**85 SKUs (17.0%) · 2.0% of revenue · 1.7% of net margin · concentration 0.12×**

Both demand variability (CV 0.74) and forecast error are elevated here, but the gap between baseline (1.85) and promo-lift (2.69) error is what stands out — a genuine, moderate promo-forecasting penalty, well short of cluster 4's extremes. Segment mix skews Intermediate (37/85, vs. ~15–20% elsewhere) — this looks like the "in-between" SKUs that don't have enough volume history for the forecast model to learn a stable promo response, but aren't as sparse as cluster 4's population.

### 4.6 Cluster 4 — Long-Tail Supply-Risk
**16 SKUs (3.2%) · 0.4% of revenue · 0.3% of net margin · concentration 0.11× · highest lead time & lead-time CV of any cluster**

The same 16 SKUs identified in the last two reports, now isolated as their own cluster at k=6 rather than absorbed into a larger tail group. Two things are true simultaneously and both are real:

- **Supply risk is the more defensible part of the label:** median lead time (9.0 days) and lead-time CV (0.39) are both the highest of any cluster (vs. ~6–7.6 days / 0.28–0.36 CV elsewhere) — genuine, moderate signal, and both features are live clustering inputs this run.
- **Promo-forecast collapse remains the dominant, order-of-magnitude signal:** average `forecast_lift_wmape` is 52.75 (individual SKUs 22×–169×), roughly 20–100x every other cluster's promo-forecast error. `chart_fingerprints.png` shows this as the single deep-red cell in the whole heatmap (§1).

Composition: 43.8% Beverages (more than double the portfolio baseline), and almost entirely Intermediate/Seasonal segment (14/16 — only 1 Fast-Mover). *Reading, unchanged from prior reports:* treat this as a "do not trust the promo forecast, and expect slower/less reliable replenishment" list for these 16 specific SKUs, reviewed individually — not a segment to set one blanket policy for.

---

## 5. Service level ladder

```
understock_cost = margin_usd × category_stockout_cost_multiplier
overstock_cost  = 0.5 × holding_cost_per_unit_year_usd        (no obsolescence term — see §1)
CR = understock_cost / (understock_cost + overstock_cost)
```

| Cluster | Median economic CR | Recommended CSL | Target case fill | Safety factor z |
|---|---|---|---|---|
| Flagship Core | 0.958 | **97.0%** | 95.0% | 1.881 |
| Mid-Tier Core | 0.954 | 95.4% | 93.4% | 1.685 |
| Long-Tail Erratic | 0.955 | 95.5% | 93.5% | 1.695 |
| Long-Tail Erratic 2 | 0.953 | 90.0% | 88.0% | 1.282 |
| Long-Tail Forecast-Challenged | 0.956 | 90.0% | 88.0% | 1.282 |
| Long-Tail Supply-Risk | 0.953 | 90.0% | 88.0% | 1.282 |

Same finding as the last two reports, and if anything clearer now that the obsolescence term is fully gone: **the economic CR is flat to two significant figures (0.953–0.958) across every cluster.** With overstock cost now purely holding cost, and holding cost/margin/stockout-multiplier ratios fairly stable across this portfolio, the newsvendor model alone still can't meaningfully separate these six clusters. The recommended CSL differences you see (97.0% down to 90.0%) come entirely from the value floor/cap business rule (≥30% margin share → floor at 97%; ≤3% → cap at 90%; net_margin_share for clusters 0 and 1 falls in between, so they land close to their own raw economic CR rather than at either clamp). This is a structural property of this dataset — the same newsvendor formula would differentiate more if any SKU's overstock cost genuinely varied (e.g. real perishability inside the horizon, which this dataset's 3–4-year shelf life doesn't provide).

---

## 6. Honest caveats

- **`chart_failure_modes.png` is still not usable as delivered**, for the same reason as the last report: cluster 4's extreme values compress every other cluster to the origin. Use `sku_clusters.csv` directly for the promo-forecast story.
- **`otd_pct` is marginal** — right at the ANOVA significance boundary (p=0.051) with a narrow range consistent with a data-generator artifact rather than real operational signal. Don't read a supply-reliability story into it beyond what `lead_time_mean_days`/`lead_time_cv` already show.
- **The service ladder is still economically flat** (§5) for the same structural reason as before — this dataset simply doesn't have a cost input that varies enough to differentiate a newsvendor ratio meaningfully. If shelf life re-enters as a candidate feature in a future version, revisit whether the obsolescence term should return too, or whether the two decisions (segmentation vs. cost model) should be governed independently going forward.
- **Cluster 4 is small (16 SKUs) and dominated by one axis** — same caveat as before, unchanged because it's the same 16 SKUs.
- **Silhouette remains low at every k** (0.16–0.17) — bootstrap stability, not silhouette, is doing the real work of picking k=6 here; boundary SKUs between clusters 1/2/3 in particular will need judgement.
- **The data is synthetic.** The pipeline and its diagnostics are the transferable asset — re-derive feature values on real sell-through before committing targets.

---

## Appendix — outputs in this folder

| File | Contents |
|---|---|
| `sku_clusters.csv` | Per-SKU features (9, no shelf life/write-off), cluster, archetype, `recommended_csl`, `target_case_fill_rate`, `safety_factor_z` |
| `cluster_service_levels.csv` | Cluster-level roll-up → revenue/margin share, archetype, CSL/case-fill/z |
| `cluster_anova.csv` | F-test significance of every surviving feature across the 6 final clusters |
| `dropped_features.csv` | `shelf_life_days` and `write_off_rate`, both `EXCLUDED_BY_DESIGN` (not statistically pruned) |
| `cluster_feature_block_balance.csv` | Confirms all 4 surviving blocks carry an equal 25% variance share |
| `cluster_k_diagnostics.csv` | Inertia, silhouette, bootstrap stability for k = 4..8 |
| `run_metadata.json` | Machine-readable record of kept/dropped features, blocks, and selected k |
| `chart_pca.png` | Cluster separation in PCA space (49.2% of variance) — best-separated run so far |
| `chart_fingerprints.png` | Standardised feature centroids per cluster — cluster 4's `forecast_lift_wmape` cell is the single dominant signal |
| `chart_service_ladder.png` | Economic (newsvendor) CR vs. recommended CSL per cluster — visual proof the economics are flat (§5) |
| `chart_failure_modes.png` | Baseline vs. promo-lift forecast error — **not readable as delivered**, see caveat in §6 |
| `segmentation_v5.py` | The script that produced everything above |

Data provenance: `input datasets/` per `DATASET_DOCUMENTATION.md` (500 SKUs, 2023–2025 actuals + 2026 forward).
