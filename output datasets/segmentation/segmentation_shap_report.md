# Supply Brain — Segmentation v5 SHAP Analysis

## Executive summary

v5 is an unsupervised k-means segmentation, so there is no native supervised target for SHAP to explain. This analysis fits a multiclass XGBoost surrogate to reproduce the final v5 cluster labels, then applies native Tree SHAP contributions to that surrogate. The results explain the model's cluster assignment boundaries; they are not causal effects and should not be read as proof that changing a feature would change a SKU's business behavior.

The surrogate reproduces the v5 labels with **92.0% test accuracy** and **0.927 macro F1** on a single stratified 75/25 holdout (125 test SKUs, seed 42; the smallest cluster has only 4 test SKUs). There is no cross-validation and no repeat across seeds. That is the fidelity check for whether the SHAP explanations are a useful approximation of the clustering boundary.

The surrogate is trained on the **raw** feature values from `sku_clusters.csv`, with only median-filling. k-means clustered on a winsorised (p1/p99), standardised, block-weighted matrix, so the surrogate sees a different feature space from the one k-means used. SHAP values are then computed for **all 500 SKUs** with the model trained on 375 of them, so most explanations are in-sample.

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

These shares are sums of the per-feature mean absolute SHAP values above, each divided by their total. They describe the surrogate fitted on raw features, so they do not correspond to the equal 25% per block enforced in the k-means distance.

Forecastability and supply risk should be read together: a high contribution from forecast error indicates difficulty predicting demand, while a high contribution from lead-time variability indicates difficulty protecting supply. Caveat: `lead_time_mean_days` and `lead_time_cv` come from `inventory.cum_lead_time_*`, which is constant across weeks. They describe a SKU's DC/network mix rather than realised supply variability. VALUE features explain economic priority; VARIABILITY features explain why a stable value tier may still need a different policy.

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

The archetype names come from the v5 rule-based labeller. The SHAP drivers above are consistent with the cluster feature means in `sku_clusters.csv` (summarised in `cluster_service_levels.csv`) but not with some of the names:
- **Cluster 5 ("Supply-Risk")** is driven by `forecast_baseline_wmape`. It has the highest baseline WMAPE of all clusters, and its lead-time CV is essentially tied with cluster 4's.
- **Cluster 2 ("Erratic 2")** is driven by `lead_time_cv`. It has the lowest lead-time CV of all clusters.
- **Cluster 3 ("Erratic 3")** is driven by `forecast_lift_wmape`. It has the highest promo-week WMAPE of all clusters, reflecting promo-week forecast failure.

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
- Fidelity rests on one 75/25 split. Hyperparameters were chosen by hand without tuning: 180 trees, depth 3, learning rate 0.05, subsample and colsample 0.9, λ 2, min_child_weight 3.
- SHAP values are computed on all 500 SKUs, including the 375 used for training, so they are mostly in-sample. Only the contribution to each SKU's assigned class (log-odds scale) is kept.
- `num_class=6` and the 8-feature list are hard-coded in `shap_analysis_v5.py`. If a v5 re-run selects a different k or a different set of surviving features, the script must be edited before it will run correctly.

## Appendix — artifacts

| File | Contents |
|---|---|
| `shap_sku_values.csv` | Per-SKU signed SHAP values for the assigned cluster, plus `mean_abs_shap`: the **mean** (not the total) of the 8 absolute contributions. |
| `shap_global_importance.csv` | Portfolio-wide feature ranking. |
| `shap_cluster_importance.csv` | Top-3 features by cluster and archetype (the table above). |
| `shap_cluster_importance_all.csv` | All 8 features by cluster and archetype. |
| `shap_model_metrics.json` | Surrogate fidelity and model settings. |
| `chart_shap_global.png` | Global feature importance chart. |
| `chart_shap_cluster_heatmap.png` | Cluster-by-feature SHAP importance heatmap. |

The analysis is reproducible with `shap_analysis_v5.py` and the parent-level `sku_clusters.csv` output.
