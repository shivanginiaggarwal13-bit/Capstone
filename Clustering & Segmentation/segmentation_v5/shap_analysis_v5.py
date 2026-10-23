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


# --------------------------------------------------------------------------- #
# Report helpers (used only by write_report; no effect on the model/artefacts)
# --------------------------------------------------------------------------- #
# Plain-English names used in report prose.
FEATURE_LABELS = {
    "log_annual_net_margin": "log annual net margin",
    "net_margin_pct": "net margin %",
    "demand_cv_base": "base-demand CV",
    "promo_depth_share": "promo volume share",
    "forecast_baseline_wmape": "baseline WMAPE",
    "forecast_lift_wmape": "promo-week WMAPE",
    "lead_time_mean_days": "mean lead time",
    "lead_time_cv": "lead-time CV",
}
# Extra interpretation appended when a cluster has the highest mean of a feature.
FEATURE_HIGH_NOTE = {
    "forecast_lift_wmape": "reflecting promo-week forecast failure",
}
# What each long-tail archetype name claims, mirroring label_archetypes() in
# segmentation_v5.py: "Supply-Risk" = highest lead_time_cv among long-tail
# clusters, "Forecast-Challenged" = highest forecast_baseline_wmape, and
# "Erratic" is the residual name, which reads as a demand-variability story.
# (name fragment, block the name implies, feature the labeller keyed it on)
ARCHETYPE_CLAIMS = [
    ("Supply-Risk", "SUPPLY_RISK", "lead_time_cv"),
    ("Forecast-Challenged", "FORECASTABILITY", "forecast_baseline_wmape"),
    ("Erratic", "VARIABILITY", None),
]
# Two cluster means count as "essentially tied" when they differ by less than
# this fraction of the feature's SKU-level standard deviation.
TIE_TOL_SD = 0.01
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}


def _num(value):
    """Format a hyperparameter compactly (2.0 -> '2', 0.05 -> '0.05')."""
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _rank_fact(profile, cluster, feature):
    """'It has the highest/lowest <feature> of all clusters' or None."""
    col = profile[feature]
    label = FEATURE_LABELS.get(feature, f"`{feature}`")
    if col.idxmax() == cluster:
        note = FEATURE_HIGH_NOTE.get(feature)
        return f"It has the highest {label} of all clusters" + (f", {note}" if note else "")
    if col.idxmin() == cluster:
        return f"It has the lowest {label} of all clusters"
    return None


def _claim_clause(profile, feature_sd, cluster, feature):
    """Where a cluster stands on the feature its archetype name was keyed on."""
    col = profile[feature]
    label = FEATURE_LABELS.get(feature, f"`{feature}`")
    others = col.drop(index=cluster)
    nearest = (others - col[cluster]).abs().idxmin()
    if abs(others[nearest] - col[cluster]) < TIE_TOL_SD * feature_sd[feature]:
        return f"its {label} is essentially tied with cluster {nearest}'s"
    rank = int(col.rank(ascending=False, method="min")[cluster])
    if rank == 1:
        return f"it does have the highest {label}"
    return f"its {label} ranks {rank} of {len(col)} clusters"


def label_mismatch_lines(cluster_df, profile, feature_sd, feature_block):
    """Compare each long-tail archetype name with the cluster's top SHAP
    driver and its feature means; return markdown lines. Everything here is
    computed from the data, so the paragraph stays true if clusters change."""
    top = cluster_df.sort_values(["cluster", "mean_abs_shap"], ascending=[True, False]).groupby("cluster").head(1)
    bullets = []
    for fragment, claimed_block, claimed_feature in ARCHETYPE_CLAIMS:
        for row in top.itertuples(index=False):
            if fragment not in str(row.archetype) or feature_block.get(row.feature) == claimed_block:
                continue
            short = str(row.archetype).replace("Long-Tail ", "")
            text = f'- **Cluster {row.cluster} ("{short}")** is driven by `{row.feature}`'
            fact = _rank_fact(profile, row.cluster, row.feature)
            clause = (_claim_clause(profile, feature_sd, row.cluster, claimed_feature)
                      if claimed_feature and claimed_feature in profile.columns
                      and claimed_feature != row.feature else None)
            if fact and clause:
                text += f". {fact}, and {clause}."
            elif fact:
                text += f". {fact}."
            elif clause:
                text += f". {clause[0].upper()}{clause[1:]}."
            else:
                text += "."
            bullets.append(text)
    intro = "The archetype names come from the v5 rule-based labeller."
    if not bullets:
        return [intro + " Each long-tail cluster's strongest SHAP driver falls in the block its name refers to."]
    return [intro + " The SHAP drivers above are consistent with the cluster feature means in "
            "`sku_clusters.csv` (summarised in `cluster_service_levels.csv`) but not with some of the names:"] + bullets


def write_report(outdir, metrics, global_df, cluster_df, direction_df, *, profile, feature_sd,
                 n_train, n_test, min_test_per_cluster, model_params):
    """Render segmentation_shap_report.md.

    profile: per-cluster means of the raw FEATURES (index = cluster).
    feature_sd: SKU-level standard deviation of each raw feature.
    n_train / n_test / min_test_per_cluster: holdout sizes from main().
    model_params: surrogate.get_params(), so the hyperparameters quoted in the
    report are the ones actually used.
    """
    top = global_df.head(8)
    n_skus = n_train + n_test
    n_features = len(global_df)
    feature_block = dict(zip(global_df["feature"], global_df["block"]))
    n_blocks = global_df["block"].nunique()
    train_pct = round(100 * n_train / n_skus)
    test_pct = 100 - train_pct
    top_n = int(cluster_df.groupby("cluster").size().max())
    top_n_word = NUMBER_WORDS.get(top_n, str(top_n))
    smallest = (f"the smallest cluster has only {min_test_per_cluster} test SKUs"
                if min_test_per_cluster < 10 else
                f"the smallest cluster has {min_test_per_cluster} test SKUs")
    mostly = "most" if n_train > n_skus / 2 else "some"
    has_lead_time = any(f in feature_block for f in ("lead_time_mean_days", "lead_time_cv"))
    p = model_params
    if p.get("subsample") == p.get("colsample_bytree"):
        sampling = f"subsample and colsample {_num(p.get('subsample'))}"
    else:
        sampling = f"subsample {_num(p.get('subsample'))}, colsample {_num(p.get('colsample_bytree'))}"
    hyper = (f"{_num(p.get('n_estimators'))} trees, depth {_num(p.get('max_depth'))}, "
             f"learning rate {_num(p.get('learning_rate'))}, {sampling}, "
             f"λ {_num(p.get('reg_lambda'))}, min_child_weight {_num(p.get('min_child_weight'))}")
    num_class = p.get("num_class")
    lines = [
        "# Supply Brain — Segmentation v5 SHAP Analysis",
        "",
        "## Executive summary",
        "",
        "v5 is an unsupervised k-means segmentation, so there is no native supervised target for SHAP to explain. This analysis fits a multiclass XGBoost surrogate to reproduce the final v5 cluster labels, then applies native Tree SHAP contributions to that surrogate. The results explain the model's cluster assignment boundaries; they are not causal effects and should not be read as proof that changing a feature would change a SKU's business behavior.",
        "",
        f"The surrogate reproduces the v5 labels with **{pct(metrics['test_accuracy'])} test accuracy** and **{metrics['test_macro_f1']:.3f} macro F1** on a single stratified {train_pct}/{test_pct} holdout ({n_test} test SKUs, seed {RANDOM_STATE}; {smallest}). There is no cross-validation and no repeat across seeds. That is the fidelity check for whether the SHAP explanations are a useful approximation of the clustering boundary.",
        "",
        f"The surrogate is trained on the **raw** feature values from `sku_clusters.csv`, with only median-filling. k-means clustered on a winsorised (p1/p99), standardised, block-weighted matrix, so the surrogate sees a different feature space from the one k-means used. SHAP values are then computed for **all {n_skus} SKUs** with the model trained on {n_train} of them, so {mostly} explanations are in-sample.",
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
    lead_time_caveat = (
        " Caveat: `lead_time_mean_days` and `lead_time_cv` come from `inventory.cum_lead_time_*`, which is constant across weeks. They describe a SKU's DC/network mix rather than realised supply variability."
        if has_lead_time else "")
    lines += [
        "",
        f"These shares are sums of the per-feature mean absolute SHAP values above, each divided by their total. They describe the surrogate fitted on raw features, so they do not correspond to the equal {100 / n_blocks:g}% per block enforced in the k-means distance.",
        "",
        "Forecastability and supply risk should be read together: a high contribution from forecast error indicates difficulty predicting demand, while a high contribution from lead-time variability indicates difficulty protecting supply." + lead_time_caveat + " VALUE features explain economic priority; VARIABILITY features explain why a stable value tier may still need a different policy.",
        "",
        "## Cluster-specific explanations",
        "",
        f"The table shows the {top_n_word} strongest features for each cluster. The direction is the mean signed SHAP value for members of that cluster's assigned class: positive means the feature pushes the surrogate toward that cluster; negative means it tends to push away from it.",
        "",
        "| Cluster | Archetype | Feature | Mean absolute SHAP | Mean signed SHAP |",
        "|---:|---|---|---:|---:|",
    ]
    for row in cluster_df.itertuples(index=False):
        lines.append(f"| {row.cluster} | {row.archetype} | `{row.feature}` | {row.mean_abs_shap:.4f} | {row.mean_signed_shap:+.4f} |")

    lines += [""] + label_mismatch_lines(cluster_df, profile, feature_sd, feature_block)

    limitations_extra = [
        f"- Fidelity rests on one {train_pct}/{test_pct} split. Hyperparameters were chosen by hand without tuning: {hyper}.",
        f"- SHAP values are computed on all {n_skus} SKUs, including the {n_train} used for training, so they are {'mostly' if mostly == 'most' else 'partly'} in-sample. Only the contribution to each SKU's assigned class (log-odds scale) is kept.",
        f"- `num_class={num_class}` and the {n_features}-feature list are hard-coded in `shap_analysis_v5.py`. If a v5 re-run selects a different k or a different set of surviving features, the script must be edited before it will run correctly.",
    ]
    if num_class is not None and int(num_class) != int(metrics["n_clusters"]):
        limitations_extra.append(
            f"- **Warning:** `num_class={num_class}` does not match the {metrics['n_clusters']} clusters in the input, so these results are not valid until the script is updated.")

    artefact_rows = [
        f"| `shap_sku_values.csv` | Per-SKU signed SHAP values for the assigned cluster, plus `mean_abs_shap`: the **mean** (not the total) of the {n_features} absolute contributions. |",
        "| `shap_global_importance.csv` | Portfolio-wide feature ranking. |",
        f"| `shap_cluster_importance.csv` | Top-{top_n} features by cluster and archetype (the table above). |",
    ]
    if (outdir / "shap_cluster_importance_all.csv").exists():
        artefact_rows.append(f"| `shap_cluster_importance_all.csv` | All {n_features} features by cluster and archetype. |")

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
    ] + limitations_extra + [
        "",
        "## Appendix — artifacts",
        "",
        "| File | Contents |",
        "|---|---|",
    ] + artefact_rows + [
        "| `shap_model_metrics.json` | Surrogate fidelity and model settings. |",
        "| `chart_shap_global.png` | Global feature importance chart. |",
        "| `chart_shap_cluster_heatmap.png` | Cluster-by-feature SHAP importance heatmap. |",
        "",
        "The analysis is reproducible with `shap_analysis_v5.py` and the parent-level `sku_clusters.csv` output.",
    ]
    # newline="\n": keep LF endings on every OS so regeneration matches the repo copy.
    (outdir.parent / "segmentation_shap_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(input_csv, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    data = pd.read_csv(input_csv)
    missing = [feature for feature in FEATURES if feature not in data.columns]
    if missing:
        raise ValueError(f"Missing v5 features: {missing}")
    data[FEATURES] = data[FEATURES].replace([np.inf, -np.inf], np.nan)
    # Report-only: cluster means / SKU-level SD of the raw features (before
    # median-filling), used for the archetype-name check in write_report().
    profile = data.groupby("cluster")[FEATURES].mean()
    feature_sd = data[FEATURES].std()
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

    write_report(
        outdir, metrics, global_df, cluster_df, cluster_df,
        profile=profile, feature_sd=feature_sd,
        n_train=len(train_idx), n_test=len(test_idx),
        min_test_per_cluster=int(pd.Series(y[test_idx]).value_counts().min()),
        model_params=model.get_params(),
    )
    print(json.dumps(metrics, indent=2))
    print(global_df.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    parser.add_argument("--input", default=os.path.join(project_root, "output datasets", "segmentation", "sku_clusters.csv"))
    parser.add_argument("--outdir", default=os.path.join(project_root, "output datasets", "segmentation", "report_artifacts"))
    args = parser.parse_args()
    main(Path(args.input), Path(args.outdir))
