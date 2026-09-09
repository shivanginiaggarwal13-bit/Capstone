#!/usr/bin/env python3
"""
segmentation_v4.py
==================
SKU segmentation on the shared "input datasets" directory, following the same
methodology documented in segmentation_v3_report.md:

  * A candidate set of 11 features in 5 business blocks (VALUE, VARIABILITY,
    FORECASTABILITY, SUPPLY_RISK, PERISHABILITY).
  * Each candidate feature is tested and pruned for one of two reasons before
    clustering:
      - STATISTICAL: zero (or near-zero) variance across SKUs.
      - SEMANTIC / UNAVAILABLE: the field doesn't carry real signal for this
        dataset, or the source table needed to compute it faithfully isn't
        part of this shared input dataset.
  * Surviving features are scaled to zero mean / unit variance, then
    block-balanced so every surviving *block* (not feature) contributes an
    equal share of total variance to the distance metric.
  * k-means for k in [4..8]; k is selected on bootstrap stability (Adjusted
    Rand Index across resamples), tie-broken on silhouette.
  * One-way ANOVA F-test per feature across the final clusters.
  * A newsvendor-style economic service level (critical ratio) per SKU,
    combined with cluster value share into a recommended CSL / case-fill /
    safety-factor ladder.

Self-contained: reads the shared "input datasets" directory, writes every
artifact under Output/segmentation_v4/.

Run:
    python segmentation_v4.py --input "/path/to/input datasets" --outdir Output/segmentation_v4
"""
import argparse
import json
import os
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import f_oneway, norm
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score, adjusted_rand_score
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA

warnings.filterwarnings("ignore")

RNG_SEED = 42
np.random.seed(RNG_SEED)

BLOCKS_CANDIDATE = {
    "VALUE": ["log_annual_net_margin", "net_margin_pct"],
    "VARIABILITY": ["demand_cv_base", "promo_depth_share"],
    "FORECASTABILITY": ["forecast_baseline_wmape", "forecast_lift_wmape"],
    "SUPPLY_RISK": ["lead_time_mean_days", "lead_time_cv", "otd_pct"],
    "PERISHABILITY": ["write_off_rate", "shelf_life_days"],
}


# --------------------------------------------------------------------------- #
# 1. LOAD
# --------------------------------------------------------------------------- #
def load_tables(input_dir):
    def rd(name, **kw):
        return pd.read_csv(os.path.join(input_dir, name), **kw)

    tables = {
        "sku_master": rd("sku_master.csv"),
        "cost_parameters": rd("cost_parameters.csv"),
        "network_lanes": rd("network_lanes.csv"),
        "orders": rd("orders.csv", parse_dates=["order_date", "delivery_date"]),
        "demand_forecast": rd("demand_forecast.csv"),
        "sku_customer_service_targets": rd("sku_customer_service_targets.csv"),
    }
    return tables


# --------------------------------------------------------------------------- #
# 2. FEATURE ENGINEERING (candidate set of 11)
# --------------------------------------------------------------------------- #
def feature_value(orders, sku_master):
    """log_annual_net_margin, net_margin_pct — realised, post-discount economics."""
    g = orders.groupby("sku_id").agg(revenue_usd=("revenue_usd", "sum"),
                                      qty=("order_qty", "sum")).reset_index()
    g = g.merge(sku_master[["sku_id", "unit_cost_usd"]], on="sku_id", how="left")
    g["cogs_usd"] = g["qty"] * g["unit_cost_usd"]
    g["annual_net_margin_usd"] = g["revenue_usd"] - g["cogs_usd"]
    g["net_margin_pct"] = g["annual_net_margin_usd"] / g["revenue_usd"].replace(0, np.nan)
    # floor before log: a small number of low-volume SKUs can show near-zero
    # or (rarely) slightly negative realised margin once heavy promo discount
    # is netted against cost; clip at a small positive floor rather than drop.
    floor = max(g["annual_net_margin_usd"].quantile(0.01), 1.0)
    g["log_annual_net_margin"] = np.log(g["annual_net_margin_usd"].clip(lower=floor))
    return g[["sku_id", "annual_net_margin_usd", "net_margin_pct", "log_annual_net_margin"]]


def feature_variability(orders):
    """demand_cv_base (non-promo weekly demand CV), promo_depth_share (promo volume share)."""
    total = orders.groupby("sku_id")["order_qty"].sum().rename("total_qty")
    promo_qty = orders[orders["on_promo"]].groupby("sku_id")["order_qty"].sum().rename("promo_qty")

    base = orders[~orders["on_promo"]]
    weekly = base.groupby(["sku_id", "period"])["order_qty"].sum().reset_index()
    # a SKU with fewer than 2 non-promo weeks (extreme edge case) gets CV=0 (no variability signal)
    def cv(s):
        m = s.mean()
        return (s.std(ddof=0) / m) if m > 0 else 0.0
    demand_cv_base = weekly.groupby("sku_id")["order_qty"].apply(cv).rename("demand_cv_base")

    out = pd.concat([total, promo_qty], axis=1).fillna(0.0)
    out["promo_depth_share"] = out["promo_qty"] / out["total_qty"].replace(0, np.nan)
    out = out.join(demand_cv_base)
    out = out.reset_index().rename(columns={"index": "sku_id"})
    return out[["sku_id", "demand_cv_base", "promo_depth_share"]]


def feature_forecastability(orders, demand_forecast):
    """forecast_baseline_wmape / forecast_lift_wmape, split by whether the
    sku/dc/customer/week had any promo activity in orders that week."""
    promo_flag = (orders.groupby(["sku_id", "dc_id", "b2b_customer_id", "period"])["on_promo"]
                  .any().reset_index().rename(columns={"on_promo": "promo_week"}))

    ins = demand_forecast[demand_forecast["horizon"] == "in_sample"].copy()
    ins = ins.merge(promo_flag, on=["sku_id", "dc_id", "b2b_customer_id", "period"], how="left")
    ins["promo_week"] = ins["promo_week"].fillna(False)
    ins["abs_err"] = (ins["actual_qty"] - ins["forecast_qty"]).abs()

    def wmape(g):
        denom = g["actual_qty"].sum()
        return g["abs_err"].sum() / denom if denom > 0 else np.nan

    base_w = (ins[~ins["promo_week"]].groupby("sku_id").apply(wmape)
              .rename("forecast_baseline_wmape"))
    lift_w = (ins[ins["promo_week"]].groupby("sku_id").apply(wmape)
              .rename("forecast_lift_wmape"))
    out = pd.concat([base_w, lift_w], axis=1).reset_index()
    # a handful of SKUs may have zero promo weeks in-sample -> lift wmape
    # undefined; fall back to the baseline value for those (no evidence of a
    # distinct promo-forecast regime, not evidence of a good one).
    out["forecast_lift_wmape"] = out["forecast_lift_wmape"].fillna(out["forecast_baseline_wmape"])
    return out


def feature_supply_risk(orders, network_lanes):
    """lead_time_mean_days, lead_time_cv — SKU-weighted (by order volume at
    each stocking DC) average of that DC's inbound-lane lead time / CV.
    otd_pct is engineered as NaN and dropped downstream: this shared dataset
    carries no promised-vs-actual receipt table (no replenishment_receipts.csv),
    so a genuine on-time-delivery rate cannot be computed here."""
    dc_lanes = network_lanes[network_lanes["destination_node_type"].isin(
        ["REGIONAL_DC", "CUSTOMER_DC"])].copy()

    def agg(g):
        w = g["capacity_units_per_week"]
        mean_lt = (g["lead_time_mean_days"] * w).sum() / w.sum()
        cv_lane = g["lead_time_std_days"] / g["lead_time_mean_days"]
        mean_cv = (cv_lane * w).sum() / w.sum()
        return pd.Series({"dc_lead_time_mean_days": mean_lt, "dc_lead_time_cv": mean_cv})

    dc_profile = dc_lanes.groupby("destination_node_id").apply(agg).reset_index().rename(
        columns={"destination_node_id": "dc_id"})

    vol = orders.groupby(["sku_id", "dc_id"])["order_qty"].sum().reset_index()
    vol = vol.merge(dc_profile, on="dc_id", how="left")

    def wavg(g, col):
        w = g["order_qty"]
        return (g[col] * w).sum() / w.sum()

    lt_mean = vol.groupby("sku_id").apply(lambda g: wavg(g, "dc_lead_time_mean_days")).rename(
        "lead_time_mean_days")
    lt_cv = vol.groupby("sku_id").apply(lambda g: wavg(g, "dc_lead_time_cv")).rename("lead_time_cv")
    out = pd.concat([lt_mean, lt_cv], axis=1).reset_index()
    out["otd_pct"] = np.nan
    return out


def feature_perishability(sku_master):
    """shelf_life_days is read straight from sku_master. write_off_rate is
    engineered as NaN and dropped downstream: inventory.csv has no lot-level
    aging/expiry field, so realised write-offs cannot be derived from this
    shared dataset (unlike lead time, which the lane table does support)."""
    out = sku_master[["sku_id", "shelf_life_days"]].copy()
    out["write_off_rate"] = np.nan
    return out


def build_feature_table(tables):
    orders = tables["orders"]
    fv = feature_value(orders, tables["sku_master"])
    fva = feature_variability(orders)
    ffc = feature_forecastability(orders, tables["demand_forecast"])
    fsr = feature_supply_risk(orders, tables["network_lanes"])
    fpe = feature_perishability(tables["sku_master"])

    df = tables["sku_master"][["sku_id", "sku_code", "category", "subcategory", "segment"]].copy()
    for part in (fv, fva, ffc, fsr, fpe):
        df = df.merge(part, on="sku_id", how="left")
    return df


# --------------------------------------------------------------------------- #
# 3. PRUNING (statistical + semantic/unavailable)
# --------------------------------------------------------------------------- #
def prune_features(df, cv_zero_tol=1e-6, near_constant_cv_tol=0.02):
    unavailable_reasons = {
        "otd_pct": "No promised-vs-actual receipt table (e.g. replenishment_receipts.csv) "
                   "is part of this shared input dataset, so a genuine on-time-delivery "
                   "rate cannot be computed here (lead_time_cv carries the supply-risk "
                   "signal instead).",
        "write_off_rate": "inventory.csv carries no lot-level aging/expiry field, so "
                           "realised write-offs cannot be derived from this shared dataset "
                           "(shelf_life_days is retained as the perishability signal).",
    }
    drops = []
    keep = {}
    for block, feats in BLOCKS_CANDIDATE.items():
        kept_feats = []
        for feat in feats:
            col = df[feat]
            if col.isna().all():
                drops.append((feat, block, "UNAVAILABLE",
                               unavailable_reasons.get(feat, "Not computable from this shared input dataset.")))
                continue
            var = col.var(ddof=0)
            mean = col.mean()
            cv = (col.std(ddof=0) / mean) if mean not in (0, np.nan) else np.nan
            if var <= cv_zero_tol:
                drops.append((feat, block, "STATISTICAL",
                               f"Zero variance across SKUs (var={var:.2e})."))
                continue
            if pd.notna(cv) and abs(cv) < near_constant_cv_tol:
                drops.append((feat, block, "STATISTICAL",
                               f"Near-constant by construction (CV={cv:.4f})."))
                continue
            kept_feats.append(feat)
        if kept_feats:
            keep[block] = kept_feats
    dropped_df = pd.DataFrame(drops, columns=["feature", "block", "reason_type", "reason"])
    return keep, dropped_df


# --------------------------------------------------------------------------- #
# 4. BLOCK-BALANCED SCALING
# --------------------------------------------------------------------------- #
def block_balanced_matrix(df, kept_blocks):
    all_feats = [f for feats in kept_blocks.values() for f in feats]
    X_raw = df[all_feats].copy()
    X_raw = X_raw.fillna(X_raw.median())

    scaler = StandardScaler()
    X_std = pd.DataFrame(scaler.fit_transform(X_raw), columns=all_feats, index=df.index)

    n_blocks = len(kept_blocks)
    target_share = 1.0 / n_blocks
    balance_rows = []
    X_bal = pd.DataFrame(index=df.index)
    for block, feats in kept_blocks.items():
        block_std = X_std[feats]
        # each block's total variance (sum of per-feature unit variances = len(feats))
        # rescaled so the block contributes exactly target_share of total variance
        n_f = len(feats)
        weight = np.sqrt(target_share / n_f)
        for f in feats:
            X_bal[f] = block_std[f] * weight
        balance_rows.append({"block": block, "n_features": n_f,
                               "raw_variance_share": n_f / len(all_feats),
                               "balanced_variance_share": (X_bal[feats].var(ddof=0).sum())})
    total_var = sum(r["balanced_variance_share"] for r in balance_rows)
    for r in balance_rows:
        r["balanced_variance_share_pct"] = 100 * r["balanced_variance_share"] / total_var
    balance_df = pd.DataFrame(balance_rows)
    return X_bal, X_std, balance_df


# --------------------------------------------------------------------------- #
# 5. K SELECTION (bootstrap stability) + FIT
# --------------------------------------------------------------------------- #
def select_k(X, k_range=range(4, 9), n_boot=25, sample_frac=0.8):
    Xv = X.values
    full_labels = {}
    diag_rows = []
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=RNG_SEED, n_init=10)
        labels = km.fit_predict(Xv)
        full_labels[k] = labels
        inertia = km.inertia_
        sil = silhouette_score(Xv, labels) if k > 1 else np.nan

        aris = []
        n = len(Xv)
        rng = np.random.RandomState(RNG_SEED)
        for b in range(n_boot):
            idx = rng.choice(n, size=int(n * sample_frac), replace=False)
            km_b = KMeans(n_clusters=k, random_state=RNG_SEED + b, n_init=5)
            labels_b = km_b.fit_predict(Xv[idx])
            aris.append(adjusted_rand_score(labels[idx], labels_b))
        stability = float(np.mean(aris))
        diag_rows.append({"k": k, "inertia": inertia, "silhouette": sil, "stability_ari": stability})

    diag_df = pd.DataFrame(diag_rows)
    best = diag_df.sort_values(["stability_ari", "silhouette"], ascending=False).iloc[0]
    best_k = int(best["k"])
    return best_k, diag_df, full_labels[best_k]


# --------------------------------------------------------------------------- #
# 6. ANOVA
# --------------------------------------------------------------------------- #
def anova_table(df, feats, labels):
    rows = []
    for f in feats:
        groups = [df.loc[labels == c, f].dropna().values for c in np.unique(labels)]
        groups = [g for g in groups if len(g) > 1]
        if len(groups) < 2:
            continue
        F, p = f_oneway(*groups)
        rows.append({"feature": f, "F": F, "p_value": p})
    return pd.DataFrame(rows).sort_values("F", ascending=False)


# --------------------------------------------------------------------------- #
# 7. ARCHETYPE LABELLING (rank-based, generalisable across datasets)
# --------------------------------------------------------------------------- #
def label_archetypes(cluster_stats, feat_df=None):
    """Name clusters from their actual behaviour, not rank position alone:
      1. Highest net-margin-share cluster -> 'Flagship Promo-Driven' (or
         'Flagship Core' if its promo share isn't above the cross-cluster
         median -- a real distinction, since a flagship needn't be
         promo-dependent).
      2. Among the remaining clusters, the one that is genuinely best-behaved
         (lowest combined forecast error + demand variability) AND still
         carries a meaningful value share (>=10% of net margin) is named
         'Mid-Tier Core'. If no remaining cluster clears that value bar, the
         'Mid-Tier Core' label is skipped rather than misapplied.
      3. Every other cluster is a long-tail cluster, named after its single
         most extreme weakness relative to the full cluster set (forecast
         error, demand variability, or lead-time risk if that feature
         survived pruning), so the name always matches the data behind it.
    """
    cs = cluster_stats.copy().reset_index(drop=True)
    cs["_var_forecast_rank"] = cs["forecast_baseline_wmape"].rank() + cs["promo_depth_share"].rank() * 0
    top_idx = cs["net_margin_share"].idxmax()
    promo_median = cs["promo_depth_share"].median()

    names = {}
    names[cs.loc[top_idx, "cluster"]] = (
        "Flagship Promo-Driven" if cs.loc[top_idx, "promo_depth_share"] >= promo_median else "Flagship Core")

    remaining = cs.drop(index=top_idx).copy()
    has_lead_time = "lead_time_cv" in remaining.columns and remaining["lead_time_cv"].notna().any()

    # behaviour score: lower is better-behaved (rank-normalised so scales don't matter)
    behaviour_cols = ["forecast_baseline_wmape"]
    if "demand_cv_base" in remaining.columns:
        pass  # demand_cv_base not aggregated in cluster_stats by default; forecast error alone is the primary signal
    remaining["_behaviour_score"] = remaining["forecast_baseline_wmape"].rank()
    mid_candidate = remaining.sort_values("_behaviour_score").iloc[0]
    if mid_candidate["net_margin_share"] >= 0.10:
        names[mid_candidate["cluster"]] = "Mid-Tier Core"

    tail = remaining[~remaining["cluster"].isin(names.keys())].copy()
    if len(tail) > 0:
        fc_max = tail["forecast_baseline_wmape"].max()
        lt_max = tail["lead_time_cv"].max() if has_lead_time else None
        erratic_count = 0
        for _, row in tail.sort_values("net_margin_share", ascending=False).iterrows():
            if has_lead_time and row["lead_time_cv"] == lt_max:
                names[row["cluster"]] = "Long-Tail Supply-Risk"
            elif row["forecast_baseline_wmape"] == fc_max:
                names[row["cluster"]] = "Long-Tail Forecast-Challenged"
            else:
                erratic_count += 1
                names[row["cluster"]] = "Long-Tail Erratic" + ("" if erratic_count == 1 else f" {erratic_count}")

    out = cs[["cluster"]].copy()
    out["archetype"] = out["cluster"].map(names)
    return out


# --------------------------------------------------------------------------- #
# 8. SERVICE LEVEL LADDER (newsvendor + value floor/cap)
# --------------------------------------------------------------------------- #
def service_ladder(df, sku_master, cost_parameters):
    m = df.merge(sku_master[["sku_id", "category", "margin_usd", "list_price_usd",
                              "holding_cost_per_unit_year_usd"]], on="sku_id", how="left",
                 suffixes=("", "_sm"))
    m = m.merge(cost_parameters, on="category", how="left")

    understock_cost = m["margin_usd"] * m["stockout_cost_multiplier"]
    holding = m["holding_cost_per_unit_year_usd"]
    # obsolescence proxy: expected annual write-off cost per unit implied by
    # shelf life relative to the ~2-year (728-day) simulated span — this is
    # the term that was exactly zero in segmentation_v3's relaxed-shelf-life
    # dataset (report §5) and is what lets the economics differentiate here.
    span_days = 728.0
    obsolescence_risk = (span_days / m["shelf_life_days"]).clip(upper=1.0)
    overstock_cost = holding * 0.5 + m["list_price_usd"] * 0.15 * obsolescence_risk
    cr = understock_cost / (understock_cost + overstock_cost)
    m["newsvendor_cr"] = cr
    return m[["sku_id", "newsvendor_cr"]]


def assign_csl(cluster_stats):
    """Map each cluster's median economic CR + value share into a discrete
    CSL ladder, with a value floor/cap exactly as in segmentation_v3 §5."""
    rows = []
    for _, r in cluster_stats.iterrows():
        cr = r["median_newsvendor_cr"]
        rev_share = r["revenue_share"]
        base = np.clip(cr, 0.85, 0.995)
        if rev_share >= 0.30:
            csl = max(base, 0.97)
        elif rev_share <= 0.03:
            csl = min(base, 0.90)
        else:
            csl = base
        csl = round(float(csl), 3)
        z = float(norm.ppf(csl))
        rows.append({"cluster": r["cluster"], "recommended_csl": csl,
                      "target_case_fill_rate": round(csl - 0.02, 3),
                      "safety_factor_z": round(z, 3)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #
def main(input_dir, outdir):
    os.makedirs(outdir, exist_ok=True)
    print("[1/8] Loading tables ...")
    tables = load_tables(input_dir)

    print("[2/8] Engineering candidate features (11 in 5 blocks) ...")
    feat_df = build_feature_table(tables)

    print("[3/8] Pruning (statistical + unavailable) ...")
    kept_blocks, dropped_df = prune_features(feat_df)
    dropped_df.to_csv(os.path.join(outdir, "dropped_features.csv"), index=False)
    print(dropped_df.to_string(index=False))

    all_feats = [f for feats in kept_blocks.values() for f in feats]
    print(f"    kept {len(all_feats)} features in {len(kept_blocks)} blocks: {kept_blocks}")

    print("[4/8] Block-balanced scaling ...")
    X_bal, X_std, balance_df = block_balanced_matrix(feat_df, kept_blocks)
    balance_df.to_csv(os.path.join(outdir, "cluster_feature_block_balance.csv"), index=False)

    print("[5/8] Selecting k (bootstrap stability, k in 4..8) ...")
    best_k, diag_df, labels = select_k(X_bal)
    diag_df.to_csv(os.path.join(outdir, "cluster_k_diagnostics.csv"), index=False)
    print(diag_df.to_string(index=False))
    print(f"    selected k = {best_k}")

    feat_df["cluster"] = labels

    print("[6/8] ANOVA per feature ...")
    anova_df = anova_table(feat_df, all_feats, labels)
    anova_df.to_csv(os.path.join(outdir, "cluster_anova.csv"), index=False)
    print(anova_df.to_string(index=False))

    print("[7/8] Service ladder ...")
    sl = service_ladder(feat_df, tables["sku_master"], tables["cost_parameters"])
    feat_df = feat_df.merge(sl, on="sku_id", how="left")

    orders = tables["orders"]
    rev = orders.groupby("sku_id")["revenue_usd"].sum().rename("revenue_usd_total")
    feat_df = feat_df.merge(rev, on="sku_id", how="left")

    total_rev = feat_df["revenue_usd_total"].sum()
    total_margin = feat_df["annual_net_margin_usd"].sum()

    agg_spec = {"revenue_usd_total": "sum", "annual_net_margin_usd": "sum",
                "newsvendor_cr": "median", "promo_depth_share": "mean",
                "forecast_baseline_wmape": "mean", "sku_id": "count"}
    if "lead_time_cv" in feat_df.columns:
        agg_spec["lead_time_cv"] = "mean"
    cluster_stats = feat_df.groupby("cluster").agg(agg_spec).rename(
        columns={"sku_id": "n_skus", "newsvendor_cr": "median_newsvendor_cr"}).reset_index()
    cluster_stats["revenue_share"] = cluster_stats["revenue_usd_total"] / total_rev
    cluster_stats["net_margin_share"] = cluster_stats["annual_net_margin_usd"] / total_margin

    label_df = label_archetypes(cluster_stats, feat_df)
    csl_df = assign_csl(cluster_stats)
    cluster_service = cluster_stats.merge(label_df, on="cluster").merge(csl_df, on="cluster")
    cluster_service.to_csv(os.path.join(outdir, "cluster_service_levels.csv"), index=False)
    print(cluster_service.to_string(index=False))

    print("[8/8] Writing sku_clusters.csv + charts ...")
    sku_out = feat_df.merge(label_df, on="cluster").merge(
        csl_df[["cluster", "recommended_csl", "target_case_fill_rate", "safety_factor_z"]],
        on="cluster")
    sku_out.to_csv(os.path.join(outdir, "sku_clusters.csv"), index=False)

    make_charts(feat_df, X_bal, labels, cluster_service, outdir)

    meta = {"n_skus": int(len(feat_df)), "kept_features": all_feats,
            "kept_blocks": kept_blocks, "selected_k": best_k,
            "dropped_features": dropped_df.to_dict(orient="records")}
    with open(os.path.join(outdir, "run_metadata.json"), "w") as f:
        json.dump(meta, f, indent=2, default=str)

    print(f"\nDone. Outputs in {outdir}")


def make_charts(feat_df, X_bal, labels, cluster_service, outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    uniq = sorted(pd.unique(labels))
    cmap = plt.get_cmap("tab10")
    color_map = {c: cmap(i) for i, c in enumerate(uniq)}
    point_colors = [color_map[c] for c in labels]

    # PCA chart
    pca = PCA(n_components=2, random_state=RNG_SEED)
    coords = pca.fit_transform(X_bal.values)
    var_explained = pca.explained_variance_ratio_.sum()
    fig, ax = plt.subplots(figsize=(7, 6))
    for c in uniq:
        mask = labels == c
        ax.scatter(coords[mask, 0], coords[mask, 1], color=color_map[c], alpha=0.7, s=25,
                   label=f"cluster {c}")
    ax.set_title(f"Cluster separation in PCA space ({var_explained*100:.1f}% of variance)")
    ax.set_xlabel("PC1"); ax.set_ylabel("PC2")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "chart_pca.png"), dpi=150)
    plt.close(fig)

    # Fingerprints (standardized centroid heatmap)
    feats = [c for c in X_bal.columns]
    centroids = X_bal.groupby(labels).mean()
    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(centroids.values, cmap="RdBu_r", aspect="auto")
    ax.set_xticks(range(len(feats))); ax.set_xticklabels(feats, rotation=45, ha="right")
    ax.set_yticks(range(len(centroids))); ax.set_yticklabels([f"cluster {i}" for i in centroids.index])
    ax.set_title("Standardised feature centroids per cluster")
    plt.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "chart_fingerprints.png"), dpi=150)
    plt.close(fig)

    # Service ladder
    cs = cluster_service.sort_values("recommended_csl", ascending=False)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(cs["archetype"], cs["median_newsvendor_cr"], alpha=0.5, label="Economic (newsvendor) CR")
    ax.bar(cs["archetype"], cs["recommended_csl"], alpha=0.5, label="Recommended CSL", width=0.4)
    ax.set_ylabel("Service level"); ax.set_xticklabels(cs["archetype"], rotation=30, ha="right")
    ax.legend(); ax.set_title("Recommended vs economically-implied service level")
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "chart_service_ladder.png"), dpi=150)
    plt.close(fig)

    # Failure modes: baseline vs lift forecast error
    fig, ax = plt.subplots(figsize=(7, 6))
    for c in uniq:
        mask = labels == c
        ax.scatter(feat_df.loc[mask, "forecast_baseline_wmape"], feat_df.loc[mask, "forecast_lift_wmape"],
                   color=color_map[c], alpha=0.6, s=20, label=f"cluster {c}")
    lims = [0, max(feat_df["forecast_baseline_wmape"].max(), feat_df["forecast_lift_wmape"].max()) * 1.05]
    ax.plot(lims, lims, "k--", alpha=0.4)
    ax.set_xlabel("Baseline WMAPE"); ax.set_ylabel("Promo-lift WMAPE")
    ax.set_title("Baseline vs promo-lift forecast error")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(outdir, "chart_failure_modes.png"), dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=os.path.join("..", "..", "input datasets"),
                         help="Path to the shared input datasets folder "
                              "(default assumes this script sits in "
                              "'Clustering & Segmentation/segmentation_v4/').")
    parser.add_argument("--outdir", default=".",
                         help="Where outputs are written (default: this same folder).")
    args = parser.parse_args()
    main(args.input, args.outdir)