# Supply Brain — Segmentation v5 SHAP Analysis

## Executive summary

v5 is an unsupervised k-means segmentation, so there is no native supervised target for SHAP to explain. This analysis fits a multiclass XGBoost surrogate to reproduce the final v5 cluster labels, then applies native Tree SHAP contributions to that surrogate. The results explain the model's cluster assignment boundaries; they are not causal effects and should not be read as proof that changing a feature would change a SKU's business behavior.

The surrogate reproduces the v5 labels with **92.0% test accuracy** and **0.927 macro F1** on a stratified holdout. That is the fidelity check for whether the SHAP explanations are a useful approximation of the clustering boundary.

## What drives assignment

The global ranking below averages the absolute SHAP contribution for each SKU's assigned cluster. Larger values mean the feature was more influential in separating that SKU's assigned cluster from the other cluster logits.

| Rank | Feature | Block | Mean absolute SHAP |
|---:|---|---|---:|
| 1 | `forecast_baseline_wmape` | FORECASTABILITY | 0.6618 |
| 2 | `promo_depth_share` | VARIABILITY | 0.5538 |
| 3 | `demand_cv_base` | VARIABILITY | 0.5255 |
| 4 | `lead_time_cv` | SUPPLY_RISK | 0.4712 |
| 5 | `forecast_lift_wmape` | FORECASTABILITY | 0.3902 |
| 6 | `net_margin_pct` | VALUE | 0.3826 |
| 7 | `log_annual_net_margin` | VALUE | 0.3066 |
| 8 | `lead_time_mean_days` | SUPPLY_RISK | 0.2028 |

### Business interpretation

- **VARIABILITY** contributes 30.9% of total absolute assignment importance.
- **FORECASTABILITY** contributes 30.1% of total absolute assignment importance.
- **VALUE** contributes 19.7% of total absolute assignment importance.
- **SUPPLY_RISK** contributes 19.3% of total absolute assignment importance.

Forecastability and supply risk should be read together: a high contribution from forecast error indicates difficulty predicting demand, while a high contribution from lead-time variability indicates difficulty protecting supply. VALUE features explain economic priority; VARIABILITY features explain why a stable value tier may still need a different policy.

## Cluster-specific explanations

The table shows the three strongest features for each cluster. The direction is the mean signed SHAP value for members of that cluster's assigned class: positive means the feature pushes the surrogate toward that cluster; negative means it tends to push away from it.

| Cluster | Archetype | Feature | Mean absolute SHAP | Mean signed SHAP |
|---:|---|---|---:|---:|
| 0 | Mid-Tier Core | `promo_depth_share` | 1.1487 | +1.0210 |
| 0 | Mid-Tier Core | `net_margin_pct` | 0.7123 | +0.4482 |
| 0 | Mid-Tier Core | `log_annual_net_margin` | 0.3522 | +0.1847 |
| 1 | Flagship Core | `promo_depth_share` | 0.8748 | +0.6587 |
| 1 | Flagship Core | `demand_cv_base` | 0.8075 | +0.6073 |
| 1 | Flagship Core | `lead_time_mean_days` | 0.5021 | +0.2081 |
| 2 | Long-Tail Erratic 2 | `lead_time_cv` | 3.4938 | +3.3690 |
| 2 | Long-Tail Erratic 2 | `forecast_lift_wmape` | 0.1369 | +0.0697 |
| 2 | Long-Tail Erratic 2 | `forecast_baseline_wmape` | 0.1240 | +0.0247 |
| 3 | Long-Tail Erratic 3 | `forecast_lift_wmape` | 3.6205 | +3.6205 |
| 3 | Long-Tail Erratic 3 | `forecast_baseline_wmape` | 0.2741 | +0.2488 |
| 3 | Long-Tail Erratic 3 | `log_annual_net_margin` | 0.0114 | +0.0046 |
| 4 | Long-Tail Erratic | `demand_cv_base` | 1.2146 | +1.1303 |
| 4 | Long-Tail Erratic | `net_margin_pct` | 0.5575 | +0.2988 |
| 4 | Long-Tail Erratic | `log_annual_net_margin` | 0.4323 | +0.2819 |
| 5 | Long-Tail Supply-Risk | `forecast_baseline_wmape` | 3.0063 | +3.0063 |
| 5 | Long-Tail Supply-Risk | `forecast_lift_wmape` | 0.5783 | +0.4844 |
| 5 | Long-Tail Supply-Risk | `lead_time_cv` | 0.1470 | +0.0400 |

## How to use this operationally

- Use `shap_global_importance.csv` to decide which business data improvements are most likely to change segment boundaries.
- Use `shap_cluster_importance.csv` to explain why a SKU belongs to a particular archetype during a planner review.
- Use `shap_sku_values.csv` to investigate individual boundary SKUs; large absolute values identify assignments that are feature-sensitive.
- Do not use SHAP values as service-level targets. The recommended CSL remains the policy output in the parent-level `cluster_service_levels.csv`.

## Limitations

- SHAP explains the supervised surrogate, not k-means distance directly. The fidelity metrics above should be checked before relying on the ranking.
- Cluster labels are nominal. Cluster 1 is not inherently more important than cluster 0; business importance comes from revenue, margin, and the archetype roll-up.
- SHAP is an association-based model explanation. It does not establish that changing forecast error, lead time, or margin would cause a new cluster assignment in the real business.
- The data is synthetic, so the rankings are useful for pipeline and decision design, not for claiming real-world causal drivers.

## Appendix — artifacts

| File | Contents |
|---|---|
| `shap_sku_values.csv` | Per-SKU signed SHAP values for the assigned cluster, plus absolute contribution total. |
| `shap_global_importance.csv` | Portfolio-wide feature ranking. |
| `shap_cluster_importance.csv` | Feature importance by cluster and archetype. |
| `shap_model_metrics.json` | Surrogate fidelity and model settings. |
| `chart_shap_global.png` | Global feature importance chart. |
| `chart_shap_cluster_heatmap.png` | Cluster-by-feature SHAP importance heatmap. |

The analysis is reproducible with `shap_analysis_v5.py` and the parent-level `sku_clusters.csv` output.
