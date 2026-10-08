#!/usr/bin/env python3
"""SHAP explainability for the v5 clustering assignment.

The clusters themselves come from k-means, which has no supervised target to
explain directly. This script fits a multiclass XGBoost surrogate to reproduce
the v5 labels, then uses XGBoost's native Tree SHAP contributions to explain
which v5 features push each SKU toward its assigned cluster.
"""
import argparse
import json
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier, DMatrix

FEATURES = [
    "log_annual_net_margin", "net_margin_pct", "demand_cv_base",
    "promo_depth_share", "forecast_baseline_wmape", "forecast_lift_wmape",
    "lead_time_mean_days", "lead_time_cv",
]
BLOCKS = {
    "VALUE": ["log_annual_net_margin", "net_margin_pct"],
    "VARIABILITY": ["demand_cv_base", "promo_depth_share"],
    "FORECASTABILITY": ["forecast_baseline_wmape", "forecast_lift_wmape"],
    "SUPPLY_RISK": ["lead_time_mean_days", "lead_time_cv"],
}
RANDOM_STATE = 42


def make_model():
    return XGBClassifier(
        objective="multi:softprob",
        num_class=6,
        n_estimators=180,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=2.0,
        min_child_weight=3,
        random_state=RANDOM_STATE,
        n_jobs=1,
        eval_metric="mlogloss",
    )


def contribution_matrix(model, X, labels):
    matrix = model.get_booster().predict(
        DMatrix(X, feature_names=FEATURES), pred_contribs=True
    )
    if matrix.ndim != 3:
        raise ValueError(f"Expected multiclass SHAP output, received shape {matrix.shape}")
    row_ids = np.arange(len(labels))
    return matrix[row_ids, labels, :-1]


def pct(value):
    return f"{100 * value:.1f}%"


def write_report(outdir, metrics, global_df, cluster_df, direction_df):
    top = global_df.head(8)
    lines = [
        "# Supply Brain — Segmentation v5 SHAP Analysis",
        "",
        "## Executive summary",
        "",
        "v5 is an unsupervised k-means segmentation, so there is no native supervised target for SHAP to explain. This analysis fits a multiclass XGBoost surrogate to reproduce the final v5 cluster labels, then applies native Tree SHAP contributions to that surrogate. The results explain the model's cluster assignment boundaries; they are not causal effects and should not be read as proof that changing a feature would change a SKU's business behavior.",
        "",
        f"The surrogate reproduces the v5 labels with **{pct(metrics['test_accuracy'])} test accuracy** and **{metrics['test_macro_f1']:.3f} macro F1** on a stratified holdout. That is the fidelity check for whether the SHAP explanations are a useful approximation of the clustering boundary.",
        "",
        "## What drives assignment",
        "",
        "The global ranking below averages the absolute SHAP contribution for each SKU's assigned cluster. Larger values mean the feature was more influential in separating that SKU's assigned cluster from the other cluster logits.",
        "",
        "| Rank | Feature | Block | Mean absolute SHAP |",
        "|---:|---|---|---:|",
    ]
    for i, row in enumerate(top.itertuples(index=False), 1):
        lines.append(f"| {i} | `{row.feature}` | {row.block} | {row.mean_abs_shap:.4f} |")

    block_df = global_df.groupby("block", as_index=False)["mean_abs_shap"].sum().sort_values("mean_abs_shap", ascending=False)
    lines += ["", "### Business interpretation", ""]
    for row in block_df.itertuples(index=False):
        lines.append(f"- **{row.block}** contributes {pct(row.mean_abs_shap / global_df.mean_abs_shap.sum())} of total absolute assignment importance.")
    lines += [
        "",
        "Forecastability and supply risk should be read together: a high contribution from forecast error indicates difficulty predicting demand, while a high contribution from lead-time variability indicates difficulty protecting supply. VALUE features explain economic priority; VARIABILITY features explain why a stable value tier may still need a different policy.",
        "",
        "## Cluster-specific explanations",
        "",
        "The table shows the three strongest features for each cluster. The direction is the mean signed SHAP value for members of that cluster's assigned class: positive means the feature pushes the surrogate toward that cluster; negative means it tends to push away from it.",
        "",
        "| Cluster | Archetype | Feature | Mean absolute SHAP | Mean signed SHAP |",
        "|---:|---|---|---:|---:|",
    ]
    for row in cluster_df.itertuples(index=False):
        lines.append(f"| {row.cluster} | {row.archetype} | `{row.feature}` | {row.mean_abs_shap:.4f} | {row.mean_signed_shap:+.4f} |")

    lines += [
        "",
        "## How to use this operationally",
        "",
        "- Use `shap_global_importance.csv` to decide which business data improvements are most likely to change segment boundaries.",
        "- Use `shap_cluster_importance.csv` to explain why a SKU belongs to a particular archetype during a planner review.",
        "- Use `shap_sku_values.csv` to investigate individual boundary SKUs; large absolute values identify assignments that are feature-sensitive.",
        "- Do not use SHAP values as service-level targets. The recommended CSL remains the policy output in the parent-level `cluster_service_levels.csv`.",
        "",
        "## Limitations",
        "",
        "- SHAP explains the supervised surrogate, not k-means distance directly. The fidelity metrics above should be checked before relying on the ranking.",
        "- Cluster labels are nominal. Cluster 1 is not inherently more important than cluster 0; business importance comes from revenue, margin, and the archetype roll-up.",
        "- SHAP is an association-based model explanation. It does not establish that changing forecast error, lead time, or margin would cause a new cluster assignment in the real business.",
        "- The data is synthetic, so the rankings are useful for pipeline and decision design, not for claiming real-world causal drivers.",
        "",
        "## Appendix — artifacts",
        "",
        "| File | Contents |",
        "|---|---|",
        "| `shap_sku_values.csv` | Per-SKU signed SHAP values for the assigned cluster, plus absolute contribution total. |",
        "| `shap_global_importance.csv` | Portfolio-wide feature ranking. |",
        "| `shap_cluster_importance.csv` | Feature importance by cluster and archetype. |",
        "| `shap_model_metrics.json` | Surrogate fidelity and model settings. |",
        "| `chart_shap_global.png` | Global feature importance chart. |",
        "| `chart_shap_cluster_heatmap.png` | Cluster-by-feature SHAP importance heatmap. |",
        "",
        "The analysis is reproducible with `shap_analysis_v5.py` and the parent-level `sku_clusters.csv` output.",
    ]
    (outdir.parent / "segmentation_shap_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(input_csv, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    data = pd.read_csv(input_csv)
    missing = [feature for feature in FEATURES if feature not in data.columns]
    if missing:
        raise ValueError(f"Missing v5 features: {missing}")
    data[FEATURES] = data[FEATURES].replace([np.inf, -np.inf], np.nan)
    data[FEATURES] = data[FEATURES].fillna(data[FEATURES].median())
    X = data[FEATURES].astype(float)
    y = data["cluster"].astype(int).to_numpy()

    train_idx, test_idx = train_test_split(
        np.arange(len(data)), test_size=0.25, random_state=RANDOM_STATE, stratify=y
    )
    model = make_model()
    model.fit(X.iloc[train_idx], y[train_idx])
    test_pred = model.predict(X.iloc[test_idx]).astype(int)
    metrics = {
        "test_accuracy": float(accuracy_score(y[test_idx], test_pred)),
        "test_macro_f1": float(f1_score(y[test_idx], test_pred, average="macro")),
        "n_skus": int(len(data)),
        "n_clusters": int(data["cluster"].nunique()),
        "features": FEATURES,
        "method": "XGBoost multiclass surrogate with native Tree SHAP contributions",
        "random_state": RANDOM_STATE,
    }
    (outdir / "shap_model_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    shap_assigned = contribution_matrix(model, X, y)
    shap_df = pd.DataFrame(shap_assigned, columns=FEATURES)
    shap_df.insert(0, "sku_id", data["sku_id"])
    shap_df.insert(1, "cluster", y)
    if "archetype" in data.columns:
        shap_df.insert(2, "archetype", data["archetype"])
    shap_df["mean_abs_shap"] = shap_df[FEATURES].abs().mean(axis=1)
    shap_df.to_csv(outdir / "shap_sku_values.csv", index=False)

    global_df = pd.DataFrame({
        "feature": FEATURES,
        "mean_abs_shap": np.abs(shap_assigned).mean(axis=0),
        "mean_signed_shap": shap_assigned.mean(axis=0),
    })
    global_df["block"] = global_df["feature"].map({feature: block for block, values in BLOCKS.items() for feature in values})
    global_df = global_df.sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)
    global_df.to_csv(outdir / "shap_global_importance.csv", index=False)

    rows = []
    for cluster in sorted(np.unique(y)):
        mask = y == cluster
        class_values = shap_assigned[mask]
        for feature_idx, feature in enumerate(FEATURES):
            rows.append({
                "cluster": int(cluster),
                "archetype": str(data.loc[mask, "archetype"].iloc[0]) if "archetype" in data.columns else f"cluster {cluster}",
                "feature": feature,
                "block": global_df.set_index("feature").loc[feature, "block"],
                "mean_abs_shap": float(np.abs(class_values[:, feature_idx]).mean()),
                "mean_signed_shap": float(class_values[:, feature_idx].mean()),
            })
    cluster_all = pd.DataFrame(rows)
    cluster_all.to_csv(outdir / "shap_cluster_importance_all.csv", index=False)
    cluster_df = cluster_all.sort_values(["cluster", "mean_abs_shap"], ascending=[True, False]).groupby("cluster", as_index=False).head(3)
    cluster_df.to_csv(outdir / "shap_cluster_importance.csv", index=False)

    fig, ax = plt.subplots(figsize=(8, 5))
    plot_df = global_df.sort_values("mean_abs_shap")
    ax.barh(plot_df["feature"], plot_df["mean_abs_shap"], color="#1f6f8b")
    ax.set_xlabel("Mean absolute SHAP contribution")
    ax.set_title("v5 cluster-assignment drivers")
    fig.tight_layout()
    fig.savefig(outdir / "chart_shap_global.png", dpi=160)
    plt.close(fig)

    heat = cluster_all.pivot(index="cluster", columns="feature", values="mean_abs_shap").loc[:, FEATURES]
    fig, ax = plt.subplots(figsize=(10, 4.5))
    image = ax.imshow(heat.values, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(FEATURES)), FEATURES, rotation=45, ha="right")
    ax.set_yticks(range(len(heat)), [f"cluster {cluster}" for cluster in heat.index])
    ax.set_title("SHAP importance by v5 cluster")
    fig.colorbar(image, ax=ax, label="Mean absolute SHAP")
    fig.tight_layout()
    fig.savefig(outdir / "chart_shap_cluster_heatmap.png", dpi=160)
    plt.close(fig)

    write_report(outdir, metrics, global_df, cluster_df, cluster_df)
    print(json.dumps(metrics, indent=2))
    print(global_df.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    parser.add_argument("--input", default=os.path.join(project_root, "output datasets", "segmentation", "sku_clusters.csv"))
    parser.add_argument("--outdir", default=os.path.join(project_root, "output datasets", "segmentation", "report_artifacts"))
    args = parser.parse_args()
    main(Path(args.input), Path(args.outdir))
