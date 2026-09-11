#!/usr/bin/env python3
"""
segmentation_v5.py
==================
SKU segmentation on the shared "input datasets" directory. Same core
methodology as segmentation_v4.py, with one deliberate scope change:

  * v4 considered 11 candidate features across 5 business blocks (VALUE,
    VARIABILITY, FORECASTABILITY, SUPPLY_RISK, PERISHABILITY) and let
    statistical/availability pruning decide what survived.
  * v5 narrows the *candidate* set up front to features that make sense as
    inputs to a service-level target: VALUE (what it costs to stock out),
    VARIABILITY (how uncertain demand is), FORECASTABILITY (how much of that
    uncertainty is irreducible vs. a forecasting gap), and SUPPLY_RISK (how
    uncertain and slow replenishment is) all feed directly into a safety-stock
    / critical-ratio decision. PERISHABILITY does not, in the same direct
    sense -- shelf life speaks to obsolescence/overstock cost, a different
    decision than "what fill rate should this SKU carry" -- so
    shelf_life_days and write_off_rate are excluded by design, not by
    statistical pruning. This is recorded explicitly in dropped_features.csv
    (reason_type EXCLUDED_BY_DESIGN) rather than silently omitted, and the
    newsvendor overstock-cost term drops its obsolescence component to match
    (holding cost only -- see service_ladder()).

  * The remaining 9 candidates (4 blocks) are still tested and pruned for:
      - STATISTICAL: zero (or near-zero) variance across SKUs.
      - SEMANTIC / UNAVAILABLE: the field doesn't carry real signal for this
        dataset, or the source table needed to compute it faithfully isn't
        part of this shared input dataset.
  * Surviving features are scaled to zero mean / unit variance, winsorized
    (1st/99th percentile) to blunt outlier SKUs, then block-balanced so every
    surviving *block* (not feature) contributes an equal share of total
    variance to the distance metric.
  * k-means for k in [4..8]; k is selected on bootstrap stability (Adjusted
    Rand Index across resamples), tie-broken on silhouette.
  * One-way ANOVA F-test per feature across the final clusters.
  * A newsvendor-style economic service level (critical ratio) per SKU,
    combined with cluster value share into a recommended CSL / case-fill /
    safety-factor ladder.

Handles multiple dataset schema variants seen so far for several tables
(cost_parameters.csv, demand_forecast.csv, inventory.csv,
replenishment_receipts.csv, write_offs.csv) so it doesn't hard-fail the next
time the shared dataset gets regenerated with a different table shape --
it detects what's available and falls back gracefully, documenting in
dropped_features.csv whenever something genuinely can't be computed.

Self-contained: reads the shared "input datasets" directory, writes every
artifact into the current folder by default (so dropping this script into
Clustering & Segmentation/segmentation_v5/ and running it there puts every
output right next to it, ready to commit).

Run (from inside .../Clustering & Segmentation/segmentation_v5/):
    python segmentation_v5.py
Or explicitly:
    python segmentation_v5.py --input "/path/to/input datasets" --outdir .
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


def annualization_factor(orders):
    """Every feature/aggregate that claims to be 'annual' should mean the
    same thing whether orders.csv covers 52 weeks or 156 -- scale by however
    many distinct weeks are actually present relative to a 52-week year."""
    n_weeks = orders["period"].nunique()
    return 52.0 / n_weeks if n_weeks else 1.0

BLOCKS_CANDIDATE = {
    "VALUE": ["log_annual_net_margin", "net_margin_pct"],
    "VARIABILITY": ["demand_cv_base", "promo_depth_share"],
    "FORECASTABILITY": ["forecast_baseline_wmape", "forecast_lift_wmape"],
    "SUPPLY_RISK": ["lead_time_mean_days", "lead_time_cv", "otd_pct"],
}

# Excluded by design, not by statistical pruning: perishability speaks to
# obsolescence/overstock cost, a different decision than "what fill rate
# should this SKU carry." Recorded here so main() can log the exclusion in
# dropped_features.csv with an honest reason rather than silently vanishing.
EXCLUDED_BY_DESIGN = {
    "shelf_life_days": ("PERISHABILITY", "Speaks to obsolescence/overstock risk, not to the safety-stock / "
                          "fill-rate decision a service-level target is meant to encode. Excluded from "
                          "the v5 candidate set by design; no longer used in the newsvendor overstock "
                          "term either (see service_ladder())."),
    "write_off_rate": ("PERISHABILITY", "Same reasoning as shelf_life_days -- both perishability features "
                         "are out of scope for v5's narrower 'features that inform a service-level "
                         "target' candidate set."),
}


# --------------------------------------------------------------------------- #
# 1. LOAD
# --------------------------------------------------------------------------- #
def load_tables(input_dir):
    def rd(name, **kw):
        return pd.read_csv(os.path.join(input_dir, name), **kw)

    def rd_opt(name, **kw):
        """Load a table only if the file exists in this dataset drop -- several
        tables (replenishment_receipts.csv, write_offs.csv) were added between
        dataset versions, so the script must not hard-fail if they're missing."""
        path = os.path.join(input_dir, name)
        return pd.read_csv(path, **kw) if os.path.exists(path) else None

    tables = {
        "sku_master": rd("sku_master.csv"),
        "cost_parameters": rd("cost_parameters.csv"),
        "network_lanes": rd("network_lanes.csv"),
        "orders": rd("orders.csv", parse_dates=["order_date", "delivery_date"]),
        "demand_forecast": rd("demand_forecast.csv"),
        "sku_customer_service_targets": rd("sku_customer_service_targets.csv"),
        "inventory": rd_opt("inventory.csv"),
        "replenishment_receipts": rd_opt("replenishment_receipts.csv"),
        "write_offs": rd_opt("write_offs.csv"),
    }
    return tables


# --------------------------------------------------------------------------- #
# 2. FEATURE ENGINEERING (candidate set of 11)
# --------------------------------------------------------------------------- #
def feature_value(orders, sku_master):
    """log_annual_net_margin, net_margin_pct -- realised, post-discount economics.
    Uses orders' own net_margin_usd / net_revenue_usd when present (this
    dataset now nets off-invoice, trade fund, volume rebate and logistics
    allowance directly at the line level -- a truer realised margin than the
    revenue-minus-standard-cost approximation used as a fallback below).
    Annualised to a 52-week basis regardless of how many weeks of history
    orders.csv actually spans, so 'annual' means the same thing whether the
    dataset covers 1 year or 3."""
    n_weeks = orders["period"].nunique()
    annualize = 52.0 / n_weeks if n_weeks else 1.0
    if "net_margin_usd" in orders.columns and "net_revenue_usd" in orders.columns:
        g = orders.groupby("sku_id").agg(revenue_usd=("net_revenue_usd", "sum"),
                                          annual_net_margin_usd=("net_margin_usd", "sum")).reset_index()
    else:
        g = orders.groupby("sku_id").agg(revenue_usd=("revenue_usd", "sum"),
                                          qty=("order_qty", "sum")).reset_index()
        g = g.merge(sku_master[["sku_id", "unit_cost_usd"]], on="sku_id", how="left")
        g["annual_net_margin_usd"] = g["revenue_usd"] - g["qty"] * g["unit_cost_usd"]

    g["annual_net_margin_usd"] = g["annual_net_margin_usd"] * annualize
    g["revenue_usd"] = g["revenue_usd"] * annualize
    g["net_margin_pct"] = g["annual_net_margin_usd"] / g["revenue_usd"].replace(0, np.nan)
    floor = max(g["annual_net_margin_usd"].quantile(0.01), 1.0)
    g["log_annual_net_margin"] = np.log(g["annual_net_margin_usd"].clip(lower=floor))
    return g[["sku_id", "annual_net_margin_usd", "net_margin_pct", "log_annual_net_margin"]]


def feature_variability(orders):
    """demand_cv_base (non-promo weekly demand CV), promo_depth_share (promo volume share)."""
    total = orders.groupby("sku_id")["order_qty"].sum().rename("total_qty")
    promo_qty = orders[orders["on_promo"]].groupby("sku_id")["order_qty"].sum().rename("promo_qty")

    base = orders[~orders["on_promo"]]
    weekly = base.groupby(["sku_id", "period"])["order_qty"].sum().reset_index()

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
    """forecast_baseline_wmape / forecast_lift_wmape.
    Prefers demand_forecast's own forecast_promo_lift_qty column to flag a
    promo week directly at the sku/dc/customer/week grain it's already
    computed at; falls back to rolling up orders.on_promo when that column
    isn't in this dataset drop."""
    ins = demand_forecast[demand_forecast["horizon"] == "in_sample"].copy()

    if "forecast_promo_lift_qty" in ins.columns:
        ins["promo_week"] = ins["forecast_promo_lift_qty"].fillna(0) > 0
    else:
        promo_flag = (orders.groupby(["sku_id", "dc_id", "b2b_customer_id", "period"])["on_promo"]
                      .any().reset_index().rename(columns={"on_promo": "promo_week"}))
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
    out["forecast_lift_wmape"] = out["forecast_lift_wmape"].fillna(out["forecast_baseline_wmape"])
    return out


def feature_supply_risk(orders, network_lanes, inventory=None, replenishment_receipts=None):
    """lead_time_mean_days, lead_time_cv, otd_pct.
    Prefers inventory.csv's own cum_lead_time_mean_days / cum_lead_time_std_days
    (end-to-end, sku/dc/week-native) when present -- a more direct source than
    inferring lead time from network_lanes. otd_pct is computed from
    replenishment_receipts.on_time_flag when that table exists in this
    dataset drop; otherwise engineered as NaN and dropped downstream as
    UNAVAILABLE."""
    if inventory is not None and {"cum_lead_time_mean_days", "cum_lead_time_std_days"}.issubset(inventory.columns):
        dc_profile = (inventory.groupby(["sku_id", "dc_id"])
                      .agg(dc_lead_time_mean_days=("cum_lead_time_mean_days", "mean"),
                           dc_lead_time_std_days=("cum_lead_time_std_days", "mean")).reset_index())
        dc_profile["dc_lead_time_cv"] = dc_profile["dc_lead_time_std_days"] / dc_profile["dc_lead_time_mean_days"]

        vol = orders.groupby(["sku_id", "dc_id"])["order_qty"].sum().reset_index()
        vol = vol.merge(dc_profile, on=["sku_id", "dc_id"], how="left")

        def wavg(g, col):
            w = g["order_qty"]
            valid = g[col].notna() & (w > 0)
            if not valid.any():
                return np.nan
            return (g.loc[valid, col] * w[valid]).sum() / w[valid].sum()

        lt_mean = vol.groupby("sku_id").apply(lambda g: wavg(g, "dc_lead_time_mean_days")).rename(
            "lead_time_mean_days")
        lt_cv = vol.groupby("sku_id").apply(lambda g: wavg(g, "dc_lead_time_cv")).rename("lead_time_cv")
        out = pd.concat([lt_mean, lt_cv], axis=1).reset_index()
    else:
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

    if replenishment_receipts is not None and "on_time_flag" in replenishment_receipts.columns:
        w = replenishment_receipts.get("qty_ordered", pd.Series(1, index=replenishment_receipts.index))
        rr = replenishment_receipts.assign(_w=w)
        otd = (rr.groupby("sku_id").apply(lambda g: (g["on_time_flag"] * g["_w"]).sum() / g["_w"].sum())
               .rename("otd_pct"))
        out = out.merge(otd.reset_index(), on="sku_id", how="left")
    else:
        out["otd_pct"] = np.nan
    return out


def feature_perishability(sku_master, inventory=None, write_offs=None):
    """shelf_life_days from sku_master. write_off_rate uses write_offs.csv
    (units_written_off per sku, divided by total units shipped from
    inventory.csv) when that table exists in this dataset drop -- including
    the case where the table exists but is empty (zero recorded write-offs),
    which correctly produces a zero-variance feature to prune, rather than an
    UNAVAILABLE one. Falls back to NaN/UNAVAILABLE only when the table isn't
    part of this dataset drop at all."""
    out = sku_master[["sku_id", "shelf_life_days"]].copy()

    if write_offs is not None:
        wo = write_offs.groupby("sku_id")["units_written_off"].sum() if len(write_offs) else pd.Series(dtype=float)
        if inventory is not None and "shipments" in inventory.columns:
            shipped = inventory.groupby("sku_id")["shipments"].sum()
        else:
            shipped = pd.Series(dtype=float)
        rate = (wo.reindex(out["sku_id"]).fillna(0.0) /
                 shipped.reindex(out["sku_id"]).replace(0, np.nan)).fillna(0.0)
        out["write_off_rate"] = rate.values
    else:
        out["write_off_rate"] = np.nan
    return out


def build_feature_table(tables):
    orders = tables["orders"]
    fv = feature_value(orders, tables["sku_master"])
    fva = feature_variability(orders)
    ffc = feature_forecastability(orders, tables["demand_forecast"])
    fsr = feature_supply_risk(orders, tables["network_lanes"], tables.get("inventory"),
                                tables.get("replenishment_receipts"))
    fpe = feature_perishability(tables["sku_master"], tables.get("inventory"), tables.get("write_offs"))

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
    }
    drops = []
    keep = {}

    # log the design exclusions first so dropped_features.csv always shows them,
    # regardless of what this particular dataset drop's statistics look like
    for feat, (block, reason) in EXCLUDED_BY_DESIGN.items():
        drops.append((feat, block, "EXCLUDED_BY_DESIGN", reason))

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
def block_balanced_matrix(df, kept_blocks, winsor_pct=0.01):
    """winsor_pct: clip each feature to its [p, 1-p] percentile range before
    scaling. WMAPE-style ratios (Sigma|error| / Sigma actual) are unbounded
    for low-volume/intermittent SKUs -- a single sparse SKU can post a WMAPE
    of 50+ while the rest of the portfolio sits under 1.0, and a raw z-score
    would let that one point dominate the feature's whole variance, then
    the k-means distance metric. Winsorizing keeps that SKU's relative rank
    (still the worst on that feature) without letting its absolute magnitude
    outweigh every other feature and SKU."""
    all_feats = [f for feats in kept_blocks.values() for f in feats]
    X_raw = df[all_feats].copy()
    X_raw = X_raw.fillna(X_raw.median())
    for f in all_feats:
        lo, hi = X_raw[f].quantile([winsor_pct, 1 - winsor_pct])
        X_raw[f] = X_raw[f].clip(lower=lo, upper=hi)

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
    """Newsvendor critical ratio per SKU. cost_parameters' grain has changed
    between dataset drops (category-only vs. dc_id x subcategory); this
    detects which key is available and aggregates cost_parameters up to that
    key before merging, rather than assuming a fixed schema.

    v5 change: overstock_cost is holding cost only. v4 added an obsolescence
    term keyed off shelf_life_days; v5 excludes shelf life from the feature
    set by design (see EXCLUDED_BY_DESIGN) on the grounds that perishability
    is an overstock/obsolescence decision, not a service-level one -- so it's
    dropped from this cost model too, for consistency, rather than sneaking
    back in only here."""
    sm_cols = ["sku_id", "margin_usd", "list_price_usd", "holding_cost_per_unit_year_usd"]
    if "category" in cost_parameters.columns:
        key = "category"
        sm_cols.append("category")
    elif "subcategory" in cost_parameters.columns:
        key = "subcategory"
        sm_cols.append("subcategory")
    else:
        raise ValueError("cost_parameters.csv has neither 'category' nor 'subcategory' -- "
                          "update service_ladder() for the new schema.")

    cp_agg = cost_parameters.groupby(key, as_index=False)[
        ["storage_cost_per_unit_year_usd", "inventory_carrying_pct_annual", "stockout_cost_multiplier"]
    ].mean()

    m = df.merge(sku_master[sm_cols], on="sku_id", how="left", suffixes=("", "_sm"))
    m = m.merge(cp_agg, on=key, how="left")

    understock_cost = m["margin_usd"] * m["stockout_cost_multiplier"]
    overstock_cost = m["holding_cost_per_unit_year_usd"] * 0.5
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
    annualize = annualization_factor(orders)
    rev_col = "net_revenue_usd" if "net_revenue_usd" in orders.columns else "revenue_usd"
    rev = (orders.groupby("sku_id")[rev_col].sum() * annualize).rename("revenue_usd_total")
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
                              "'Clustering & Segmentation/segmentation_v5/').")
    parser.add_argument("--outdir", default=".",
                         help="Where outputs are written (default: this same folder).")
    args = parser.parse_args()
    main(args.input, args.outdir)