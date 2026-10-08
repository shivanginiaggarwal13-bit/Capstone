# Supply Brain — SKU Segmentation

## Executive summary

This is the canonical segmentation output for the current regenerated dataset. The v5 service-level-focused pipeline evaluates SKU value, demand variability, forecastability, and supply risk, then assigns each SKU to a business archetype and recommended service-level policy.

The run covers **500 SKUs** and selects **six clusters** using bootstrap stability. The two core groups contain 267 SKUs (53.4% of the catalogue) but carry 92.2% of revenue and 92.7% of net margin. This concentration supports differentiated inventory protection: core items deserve the strongest availability focus, while low-value tail items should receive leaner defaults and targeted exceptions.

## Current segment portfolio

| Cluster | Archetype | SKUs | % catalogue | % revenue | % margin | Baseline WMAPE | Recommended CSL | Case fill |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | **Flagship Core** | 134 | 26.8% | 53.6% | 62.7% | 0.34 | 97.0% | 95.0% |
| 0 | **Mid-Tier Core** | 133 | 26.6% | 38.6% | 30.0% | 0.45 | 97.0% | 95.0% |
| 2 | Long-Tail Erratic 2 | 47 | 9.4% | 3.0% | 2.5% | 0.56 | 95.7% | 93.7% |
| 4 | Long-Tail Erratic | 88 | 17.6% | 2.4% | 2.8% | 0.59 | 90.0% | 88.0% |
| 5 | Long-Tail Supply-Risk | 81 | 16.2% | 2.0% | 1.7% | 1.85 | 90.0% | 88.0% |
| 3 | Long-Tail Erratic 3 | 17 | 3.4% | 0.4% | 0.3% | 1.73 | 90.0% | 88.0% |

## Business use

- Protect Flagship Core and Mid-Tier Core first; they carry almost all portfolio economics.
- Treat Mid-Tier Core promotions separately because promotional volume share is high.
- Investigate forecast process, sparse demand, and promo response for the forecast-challenged tail groups before adding blanket inventory.
- Review lead-time variability and sourcing options for Long-Tail Supply-Risk.
- Use the 90% CSL groups as candidates for assortment, order-frequency, or make-to-order decisions, not automatic deletion.

## Method and controls

The model uses eight active features in four equally weighted blocks: VALUE, VARIABILITY, FORECASTABILITY, and SUPPLY_RISK. Perishability features are excluded by design because the simulated shelf life does not create a live expiry trade-off. `otd_pct` is pruned because it is near-constant in the current data. The selected k=6 solution has bootstrap stability ARI 0.848; silhouette is low, so boundary SKUs should receive business review.

The newsvendor critical ratio is combined with value-based CSL floors and caps. Because the synthetic cost inputs produce economically similar critical ratios, the practical service ladder is primarily strategic. Recalibrate it when real expiry, penalty, substitution, or customer-priority costs are available.

## SHAP explainability

The companion [segmentation_shap_report.md](segmentation_shap_report.md) explains cluster assignments with a supervised XGBoost surrogate and native Tree SHAP contributions. It is an approximation of the k-means boundary, not a causal model.

## Artifacts

The optimization-facing [sku_clusters.csv](sku_clusters.csv) and [cluster_service_levels.csv](cluster_service_levels.csv) stay at this parent level. Diagnostic CSVs, metadata, charts, and SHAP outputs are in [report_artifacts](report_artifacts). The reproducible runners are [segmentation_v5.py](../../Clustering%20%26%20Segmentation/segmentation_v5/segmentation_v5.py) and [shap_analysis_v5.py](../../Clustering%20%26%20Segmentation/segmentation_v5/shap_analysis_v5.py).
