"""
Supply Brain - Phase 2 SKU Segmentation (FINAL approach)

Produces TWO outputs, as agreed:
  OUTPUT 1: sku_clusters_annual.csv     - single global clustering, whole-history features
  OUTPUT 2: sku_clusters_quarterly.csv  - same approach, re-run fresh for each of 2025's
                                           4 quarters (no category split in either case)

Both outputs include: financial exposure ($ at risk from stockouts), business-friendly
cluster names (rule-based, not manual), ANOVA significance test, PCA visualization,
and a revenue/margin concentration profile.

Requirements: pip install pandas numpy scikit-learn scipy matplotlib
Usage: edit DATA_DIR / OUTPUT_DIR below, then: python build_sku_clusters_final.py
"""
import os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from scipy import stats
import warnings
warnings.filterwarnings("ignore")

# ---- EDIT THESE TWO PATHS FOR YOUR MACHINE ----
# DATA_DIR points to the "input datasets" folder at the Capstone workspace root.
# From this script's location (Capstone/Clustering & Segmentation/Segmentation_v1/),
# that means going up two levels, then into "input datasets".
# OUTPUT_DIR creates an "Output" folder right inside this same Segmentation_v1 folder.
DATA_DIR = "../../input datasets"
OUTPUT_DIR = "./Output"
# ------------------------------------------------
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(f"{OUTPUT_DIR}/annual", exist_ok=True)
os.makedirs(f"{OUTPUT_DIR}/quarterly", exist_ok=True)
FE_DIR_ANNUAL = f"{OUTPUT_DIR}/annual/feature_engineering"
FE_DIR_QUARTERLY = f"{OUTPUT_DIR}/quarterly/feature_engineering"
os.makedirs(FE_DIR_ANNUAL, exist_ok=True)
os.makedirs(FE_DIR_QUARTERLY, exist_ok=True)

CLUSTER_COLS = [
    "margin_pct", "list_price_usd", "shelf_life_days",
    "demand_cv", "forecast_mape", "revenue_total",
    "supply_adherence_std", "lead_time_mean_days", "lead_time_std_days",
    "stockout_rate",
]

# =========================================================================
# 1. LOAD RAW FILES
# =========================================================================
sku = pd.read_csv(f"{DATA_DIR}/sku_master.csv")
demand = pd.read_csv(f"{DATA_DIR}/demand_forecast.csv")
orders = pd.read_csv(f"{DATA_DIR}/orders.csv")
prod = pd.read_csv(f"{DATA_DIR}/production.csv")
inv = pd.read_csv(f"{DATA_DIR}/inventory.csv")
lanes = pd.read_csv(f"{DATA_DIR}/network_lanes.csv")
plant_dc = pd.read_csv(f"{DATA_DIR}/plant_dc_master.csv")
cost_params = pd.read_csv(f"{DATA_DIR}/cost_parameters.csv")

if "margin_usd" not in sku.columns:
    sku["margin_usd"] = sku["list_price_usd"] - sku["unit_cost_usd"]
if "margin_pct" not in sku.columns:
    sku["margin_pct"] = sku["margin_usd"] / sku["list_price_usd"]

orders["order_date"] = pd.to_datetime(orders["order_date"])
orders_full = orders[orders["order_date"] < "2026-01-01"].copy()  # keep to the one real actuals year
orders_full = orders_full.merge(sku[["sku_id", "unit_cost_usd", "category"]], on="sku_id", how="left")
orders_full["margin_usd_txn"] = (orders_full["unit_price_usd"] - orders_full["unit_cost_usd"]) * orders_full["order_qty"]

demand_actual = demand[demand.horizon == "in_sample"].copy()
demand_actual["week_start_date"] = pd.to_datetime(demand_actual["week_start_date"])

prod_actual = prod.copy()
prod_actual["week_start_date"] = pd.to_datetime(prod_actual["week_start_date"])
prod_actual = prod_actual[prod_actual["week_start_date"] < "2026-01-01"]

inv_actual = inv.copy()
inv_actual["week_start_date"] = pd.to_datetime(inv_actual["week_start_date"])
inv_actual = inv_actual[inv_actual["week_start_date"] < "2026-01-01"]

# =========================================================================
# 2. CATEGORY-LEVEL POOLED ELASTICITY (used for profiling only, never clustering math)
# =========================================================================
def fixed_effects_demean(df, value_cols, group_cols):
    out = df.copy()
    for col in value_cols:
        for g in group_cols:
            out[col] = out[col] - out.groupby(g)[col].transform("mean") + out[col].mean()
    return out

category_elasticity = {}
for cat, g in orders_full.groupby("category"):
    g = g[(g.base_price_usd > 0) & (g.order_qty > 0) & (g.discount_pct < 1)].copy()
    g["ln_qty"] = np.log(g.order_qty)
    g["ln_base_price"] = np.log(g.base_price_usd)
    g["ln_one_minus_disc"] = np.log(1 - g.discount_pct + 1e-6)
    demeaned = fixed_effects_demean(g, ["ln_qty", "ln_base_price", "ln_one_minus_disc"], ["sku_id", "period"])
    y = demeaned["ln_qty"].values
    X = np.column_stack([np.ones(len(demeaned)), demeaned["ln_base_price"].values, demeaned["ln_one_minus_disc"].values])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    category_elasticity[cat] = {"elasticity_base": coef[1], "elasticity_promo": coef[2]}

print("Category-level elasticity (profiling only, not used in clustering):")
print(pd.DataFrame(category_elasticity).T.round(3))


# =========================================================================
# 3. RULE-BASED CLUSTER NAMING
# =========================================================================
def name_clusters(profile_df):
    """Assigns a business-friendly name to each cluster. Revenue tag is RATIO-based
    (revenue share vs. catalog share) rather than a simple z-score, because with only
    3-5 clusters, z-score against a tiny sample can mislabel a "middle" cluster as
    "Long-Tail" just for not being the single top performer. Other tags use z-score
    across cluster means, which is fine for metrics without this catalog-share issue."""
    metrics = ["margin_pct", "stockout_rate", "demand_cv", "lead_time_mean_days"]
    z = profile_df[metrics].apply(lambda col: (col - col.mean()) / (col.std() if col.std() > 0 else 1))
    revenue_ratio = profile_df["pct_of_total_revenue"] / profile_df["pct_of_catalog"].replace(0, np.nan)

    names = []
    for idx in profile_df.index:
        tags = []
        if revenue_ratio.loc[idx] >= 2.0:
            tags.append("High-Revenue")
        elif revenue_ratio.loc[idx] <= 0.3:
            tags.append("Long-Tail")
        if z.loc[idx, "margin_pct"] > 0.5:
            tags.append("High-Margin")
        elif z.loc[idx, "margin_pct"] < -0.5:
            tags.append("Low-Margin")
        if z.loc[idx, "stockout_rate"] > 0.5:
            tags.append("Stockout-Risk")
        if z.loc[idx, "demand_cv"] > 0.5:
            tags.append("Volatile")
        elif z.loc[idx, "demand_cv"] < -0.5:
            tags.append("Stable-Demand")
        if z.loc[idx, "lead_time_mean_days"] > 0.5:
            tags.append("Slow-Replenish")
        if not tags:
            tags = ["Core / Standard"]
        names.append(" / ".join(tags[:3]))
    return names


# =========================================================================
# 4. VALIDATION HELPERS (ANOVA, PCA, business profile, financial exposure)
# =========================================================================
def run_anova(df, cluster_col, feature_cols):
    rows = []
    for col in feature_cols:
        groups = [df.loc[df[cluster_col] == c, col].dropna() for c in sorted(df[cluster_col].unique())]
        groups = [g for g in groups if len(g) > 1]
        if len(groups) < 2:
            continue
        f_stat, p_val = stats.f_oneway(*groups)
        rows.append({"feature": col, "f_statistic": round(f_stat, 2), "p_value": p_val,
                      "significant (p<0.05)": p_val < 0.05})
    return pd.DataFrame(rows).sort_values("f_statistic", ascending=False)


def save_pca_plot(X_scaled, cluster_labels, path, title):
    pca = PCA(n_components=2)
    coords = pca.fit_transform(X_scaled)
    fig, ax = plt.subplots(figsize=(8, 6))
    scatter = ax.scatter(coords[:, 0], coords[:, 1], c=cluster_labels, cmap="tab10", alpha=0.7, s=30)
    ax.set_xlabel(f"PC1 ({pca.explained_variance_ratio_[0]*100:.1f}% of variance)")
    ax.set_ylabel(f"PC2 ({pca.explained_variance_ratio_[1]*100:.1f}% of variance)")
    ax.set_title(title)
    legend1 = ax.legend(*scatter.legend_elements(), title="Cluster")
    ax.add_artist(legend1)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return pca.explained_variance_ratio_.sum()


def business_and_financial_profile(df, cluster_col):
    df = df.merge(cost_params[["category", "stockout_cost_multiplier"]], on="category", how="left")
    df["stockout_exposure_usd"] = df["revenue_total"] * df["stockout_rate"] * df["stockout_cost_multiplier"]
    profile = df.groupby(cluster_col).agg(
        n_skus=("sku_id", "count"),
        total_revenue=("revenue_total", "sum"),
        total_margin_usd=("total_margin_usd_realized", "sum"),
        avg_margin_pct=("margin_pct", "mean"),
        avg_stockout_rate=("stockout_rate", "mean"),
        total_stockout_exposure_usd=("stockout_exposure_usd", "sum"),
        avg_lead_time_days=("lead_time_mean_days", "mean"),
        avg_demand_cv=("demand_cv", "mean"),
    ).reset_index()
    profile["pct_of_catalog"] = (profile["n_skus"] / profile["n_skus"].sum() * 100).round(1)
    profile["pct_of_total_revenue"] = (profile["total_revenue"] / profile["total_revenue"].sum() * 100).round(1)
    profile["pct_of_total_margin"] = (profile["total_margin_usd"] / profile["total_margin_usd"].sum() * 100).round(1)
    profile["pct_of_total_stockout_exposure"] = (
        profile["total_stockout_exposure_usd"] / profile["total_stockout_exposure_usd"].sum() * 100
    ).round(1)
    profile["cluster_name"] = name_clusters(profile.rename(columns={
        "avg_lead_time_days": "lead_time_mean_days",
        "avg_demand_cv": "demand_cv",
        "avg_stockout_rate": "stockout_rate",
        "avg_margin_pct": "margin_pct",
    }))
    return profile, df


def pick_k(X_scaled, k_min=2, k_max=8, min_k_floor=3):
    """Auto-picks k by silhouette score, but won't select k=2 unless it wins by a wide
    margin (business floor: 2 segments is too coarse to build differentiated policy on).
    Returns (best_k, results_dataframe) so the full table can be saved, not just printed."""
    results = []
    for k in range(k_min, min(k_max, len(X_scaled) - 1) + 1):
        km = KMeans(n_clusters=k, random_state=42, n_init=10).fit(X_scaled)
        sil = silhouette_score(X_scaled, km.labels_)
        results.append((k, km.inertia_, sil))
    print("k | inertia | silhouette")
    for k, inertia, sil in results:
        print(f"{k} | {inertia:.1f} | {sil:.3f}")
    results_df = pd.DataFrame(results, columns=["k", "inertia", "silhouette_score"])
    best = max(results, key=lambda r: r[2])
    if best[0] == 2:
        candidates = [r for r in results if r[0] >= min_k_floor]
        if candidates:
            runner_up = max(candidates, key=lambda r: r[2])
            if best[2] - runner_up[2] < 0.03:  # not a wide margin -> respect the business floor
                best = runner_up
    results_df["selected"] = results_df["k"] == best[0]
    return best[0], results_df


def cluster_centroid_distances(X_scaled, cluster_labels):
    """Computes the Euclidean distance between every pair of cluster centers, in the
    SAME standardized feature space used for clustering. Answers, as a single number:
    'exactly how different is Cluster A from Cluster B?' Larger = more different."""
    clusters = sorted(pd.Series(cluster_labels).unique())
    centroids = {c: X_scaled[cluster_labels == c].mean(axis=0) for c in clusters}
    rows = []
    for i, c1 in enumerate(clusters):
        for c2 in clusters[i + 1:]:
            dist = np.linalg.norm(centroids[c1] - centroids[c2])
            rows.append({"cluster_a": c1, "cluster_b": c2, "centroid_distance": round(dist, 3)})
    return pd.DataFrame(rows).sort_values("centroid_distance", ascending=False)


# =========================================================================
# OUTPUT 1: ANNUAL (whole-history) global clustering
# =========================================================================
print("\n" + "=" * 70)
print("OUTPUT 1: ANNUAL CLUSTERING (whole-history features, no category split)")
print("=" * 70)

demand_sku = demand_actual.groupby("sku_id").agg(
    demand_mean=("actual_qty", "mean"), demand_std=("actual_qty", "std"),
    forecast_mape=("forecast_error_pct", lambda x: x.abs().mean()),
).reset_index()
demand_sku["demand_cv"] = demand_sku["demand_std"] / demand_sku["demand_mean"]

revenue_margin_sku = orders_full.groupby("sku_id").agg(
    revenue_total=("revenue_usd", "sum"), total_margin_usd_realized=("margin_usd_txn", "sum"),
).reset_index()

supply_sku = prod_actual.groupby("sku_id")["adherence_pct"].std().reset_index(name="supply_adherence_std")

# lead time: trace full plant -> PDC -> RDC -> CDC path, demand-weighted across each SKU's DCs
plant_to_pdc = lanes[lanes.origin_node_type == "PLANT"].set_index("origin_node_id")[
    ["destination_node_id", "lead_time_mean_days", "lead_time_std_days"]
].rename(columns={"destination_node_id": "pdc_id", "lead_time_mean_days": "leg1_mean", "lead_time_std_days": "leg1_std"})
pdc_to_rdc = lanes[lanes.origin_node_type == "PLANT_DC"].rename(
    columns={"origin_node_id": "pdc_id", "destination_node_id": "rdc_id",
             "lead_time_mean_days": "leg2_mean", "lead_time_std_days": "leg2_std"})
rdc_to_cdc = lanes[lanes.origin_node_type == "REGIONAL_DC"].rename(
    columns={"origin_node_id": "rdc_id", "destination_node_id": "cdc_id",
             "lead_time_mean_days": "leg3_mean", "lead_time_std_days": "leg3_std"})

def path_leadtime(plant_id, target_dc_id):
    if plant_id not in plant_to_pdc.index:
        return np.nan, np.nan
    leg1 = plant_to_pdc.loc[plant_id]
    pdc_id, leg1_mean, leg1_std = leg1["pdc_id"], leg1["leg1_mean"], leg1["leg1_std"]
    if target_dc_id.startswith("RDC"):
        leg2 = pdc_to_rdc[(pdc_to_rdc.pdc_id == pdc_id) & (pdc_to_rdc.rdc_id == target_dc_id)]
        if leg2.empty:
            return np.nan, np.nan
        return leg1_mean + leg2.leg2_mean.iloc[0], np.sqrt(leg1_std**2 + leg2.leg2_std.iloc[0]**2)
    if target_dc_id.startswith("CDC"):
        leg3 = rdc_to_cdc[rdc_to_cdc.cdc_id == target_dc_id]
        if leg3.empty:
            return np.nan, np.nan
        rdc_id = leg3.rdc_id.iloc[0]
        leg2 = pdc_to_rdc[(pdc_to_rdc.pdc_id == pdc_id) & (pdc_to_rdc.rdc_id == rdc_id)]
        if leg2.empty:
            return np.nan, np.nan
        mean = leg1_mean + leg2.leg2_mean.iloc[0] + leg3.leg3_mean.iloc[0]
        std = np.sqrt(leg1_std**2 + leg2.leg2_std.iloc[0]**2 + leg3.leg3_std.iloc[0]**2)
        return mean, std
    return np.nan, np.nan

sku_plant = prod[["sku_id", "plant_id"]].drop_duplicates().set_index("sku_id")["plant_id"].to_dict()
dc_weights = orders_full.groupby(["sku_id", "dc_id"])["order_qty"].sum().reset_index()
dc_weights["weight"] = dc_weights.groupby("sku_id")["order_qty"].transform(lambda x: x / x.sum())

lt_rows = []
for sid, grp in dc_weights.groupby("sku_id"):
    plant_id = sku_plant.get(sid)
    means, stds, weights = [], [], []
    for _, r in grp.iterrows():
        m, s = path_leadtime(plant_id, r.dc_id)
        if not np.isnan(m):
            means.append(m); stds.append(s); weights.append(r.weight)
    if means:
        w = np.array(weights) / np.sum(weights)
        lt_rows.append({"sku_id": sid, "lead_time_mean_days": np.sum(np.array(means) * w),
                         "lead_time_std_days": np.sqrt(np.sum((np.array(stds) ** 2) * w))})
    else:
        lt_rows.append({"sku_id": sid, "lead_time_mean_days": np.nan, "lead_time_std_days": np.nan})
leadtime_sku = pd.DataFrame(lt_rows)

stockout_sku = inv_actual.groupby("sku_id").agg(
    stockout_weeks=("closing_stock", lambda x: (x == 0).sum()), total_weeks=("closing_stock", "count"),
).reset_index()
stockout_sku["stockout_rate"] = stockout_sku["stockout_weeks"] / stockout_sku["total_weeks"]

# Save each intermediate building block separately, BEFORE the merge -- lets you audit
# any one feature in isolation (e.g. check a SKU's lead time or elasticity alone)
# without scrolling through the full combined matrix.
demand_sku.to_csv(f"{FE_DIR_ANNUAL}/features_demand_variability.csv", index=False)
revenue_margin_sku.to_csv(f"{FE_DIR_ANNUAL}/features_revenue_margin.csv", index=False)
supply_sku.to_csv(f"{FE_DIR_ANNUAL}/features_supply_variability.csv", index=False)
leadtime_sku.to_csv(f"{FE_DIR_ANNUAL}/features_lead_time.csv", index=False)
stockout_sku.to_csv(f"{FE_DIR_ANNUAL}/features_inventory_health.csv", index=False)
pd.DataFrame(category_elasticity).T.reset_index().rename(columns={"index": "category"}).to_csv(
    f"{FE_DIR_ANNUAL}/features_category_elasticity_pooled.csv", index=False
)
print(f"Saved intermediate feature-engineering tables to: {FE_DIR_ANNUAL}/")

feat_annual = sku.merge(demand_sku, on="sku_id", how="left") \
                  .merge(revenue_margin_sku, on="sku_id", how="left") \
                  .merge(supply_sku, on="sku_id", how="left") \
                  .merge(leadtime_sku, on="sku_id", how="left") \
                  .merge(stockout_sku, on="sku_id", how="left")
feat_annual["elasticity_base"] = feat_annual["category"].map(lambda c: category_elasticity[c]["elasticity_base"])
feat_annual["elasticity_promo"] = feat_annual["category"].map(lambda c: category_elasticity[c]["elasticity_promo"])

# Checkpoint: the fully combined feature matrix, BEFORE clustering runs -- every SKU
# attribute and engineered feature in one table, with no cluster assignment yet.
feat_annual.to_csv(f"{OUTPUT_DIR}/annual/sku_feature_matrix_annual.csv", index=False)
print(f"Saved: {OUTPUT_DIR}/annual/sku_feature_matrix_annual.csv (pre-clustering feature matrix)")

X_annual = feat_annual[CLUSTER_COLS].fillna(feat_annual[CLUSTER_COLS].median())
X_annual_scaled = StandardScaler().fit_transform(X_annual)

print("\nChoosing k:")
K_annual, silhouette_table_annual = pick_k(X_annual_scaled)
print(f"Selected k = {K_annual}")
silhouette_table_annual.to_csv(f"{OUTPUT_DIR}/annual/cluster_silhouette_scores_annual.csv", index=False)

km_annual = KMeans(n_clusters=K_annual, random_state=42, n_init=10)
feat_annual["cluster"] = km_annual.fit_predict(X_annual_scaled)

centroid_dist_annual = cluster_centroid_distances(X_annual_scaled, feat_annual["cluster"].values)
print("\nCentroid distances (how different each pair of clusters is, in standardized feature space):")
print(centroid_dist_annual.to_string(index=False))
centroid_dist_annual.to_csv(f"{OUTPUT_DIR}/annual/cluster_centroid_distances_annual.csv", index=False)

anova_annual = run_anova(feat_annual, "cluster", CLUSTER_COLS)
print("\nANOVA significance:")
print(anova_annual.to_string(index=False))

pca_var_annual = save_pca_plot(X_annual_scaled, feat_annual["cluster"],
                                f"{OUTPUT_DIR}/annual/cluster_pca_annual.png",
                                "Annual SKU clusters (2D via PCA)")
print(f"\nPCA variance explained: {pca_var_annual*100:.1f}%")

profile_annual, feat_annual = business_and_financial_profile(feat_annual, "cluster")
name_map_annual = dict(zip(profile_annual["cluster"], profile_annual["cluster_name"]))
feat_annual["cluster_name"] = feat_annual["cluster"].map(name_map_annual)

print("\nCluster business & financial profile:")
print(profile_annual[["cluster", "cluster_name", "n_skus", "pct_of_catalog", "pct_of_total_revenue",
                       "pct_of_total_margin", "avg_stockout_rate", "total_stockout_exposure_usd",
                       "pct_of_total_stockout_exposure"]].to_string(index=False))

feat_annual.to_csv(f"{OUTPUT_DIR}/annual/sku_clusters_annual.csv", index=False)
profile_annual.to_csv(f"{OUTPUT_DIR}/annual/cluster_business_profile_annual.csv", index=False)
anova_annual.to_csv(f"{OUTPUT_DIR}/annual/cluster_anova_annual.csv", index=False)
print(f"\nSaved Output 1 files to: {OUTPUT_DIR}/annual/")


# =========================================================================
# OUTPUT 2: QUARTERLY (fresh per quarter, no category split) global clustering
# =========================================================================
print("\n" + "=" * 70)
print("OUTPUT 2: QUARTERLY CLUSTERING (fresh features per quarter, no category split)")
print("=" * 70)

orders_full["quarter"] = orders_full["order_date"].dt.quarter
demand_actual["quarter"] = demand_actual["week_start_date"].dt.quarter
prod_actual["quarter"] = prod_actual["week_start_date"].dt.quarter
inv_actual["quarter"] = inv_actual["week_start_date"].dt.quarter

quarterly_results = []
quarterly_profiles = []
quarterly_anovas = []
quarterly_silhouettes = []
quarterly_centroid_dists = []

for q in [1, 2, 3, 4]:
    print(f"\n--- Quarter {q} ---")
    demand_q = demand_actual[demand_actual.quarter == q].groupby("sku_id").agg(
        demand_mean=("actual_qty", "mean"), demand_std=("actual_qty", "std"),
        forecast_mape=("forecast_error_pct", lambda x: x.abs().mean()),
    ).reset_index()
    demand_q["demand_cv"] = demand_q["demand_std"] / demand_q["demand_mean"]

    rev_margin_q = orders_full[orders_full.quarter == q].groupby("sku_id").agg(
        revenue_total=("revenue_usd", "sum"), total_margin_usd_realized=("margin_usd_txn", "sum"),
    ).reset_index()

    supply_q = prod_actual[prod_actual.quarter == q].groupby("sku_id")["adherence_pct"].std().reset_index(
        name="supply_adherence_std")

    stockout_q = inv_actual[inv_actual.quarter == q].groupby("sku_id").agg(
        stockout_weeks=("closing_stock", lambda x: (x == 0).sum()), total_weeks=("closing_stock", "count"),
    ).reset_index()
    stockout_q["stockout_rate"] = stockout_q["stockout_weeks"] / stockout_q["total_weeks"]

    demand_q.to_csv(f"{FE_DIR_QUARTERLY}/features_demand_variability_Q{q}.csv", index=False)
    rev_margin_q.to_csv(f"{FE_DIR_QUARTERLY}/features_revenue_margin_Q{q}.csv", index=False)
    supply_q.to_csv(f"{FE_DIR_QUARTERLY}/features_supply_variability_Q{q}.csv", index=False)
    stockout_q.to_csv(f"{FE_DIR_QUARTERLY}/features_inventory_health_Q{q}.csv", index=False)

    # structural features stay static (reuse the annual values -- price, shelf life, lead time)
    static_cols = ["sku_id", "category", "list_price_usd", "shelf_life_days", "margin_pct",
                   "lead_time_mean_days", "lead_time_std_days", "elasticity_base", "elasticity_promo"]
    feat_q = feat_annual[static_cols].merge(demand_q, on="sku_id", how="left") \
                                      .merge(rev_margin_q, on="sku_id", how="left") \
                                      .merge(supply_q, on="sku_id", how="left") \
                                      .merge(stockout_q, on="sku_id", how="left")
    feat_q["quarter"] = q
    feat_q.to_csv(f"{FE_DIR_QUARTERLY}/sku_feature_matrix_Q{q}.csv", index=False)

    Xq = feat_q[CLUSTER_COLS].fillna(feat_q[CLUSTER_COLS].median())
    Xq_scaled = StandardScaler().fit_transform(Xq)

    K_q, silhouette_table_q = pick_k(Xq_scaled)
    print(f"Selected k = {K_q}")
    silhouette_table_q["quarter"] = q
    quarterly_silhouettes.append(silhouette_table_q)

    km_q = KMeans(n_clusters=K_q, random_state=42, n_init=10)
    feat_q["cluster"] = km_q.fit_predict(Xq_scaled)

    centroid_dist_q = cluster_centroid_distances(Xq_scaled, feat_q["cluster"].values)
    centroid_dist_q["quarter"] = q
    quarterly_centroid_dists.append(centroid_dist_q)

    anova_q = run_anova(feat_q, "cluster", CLUSTER_COLS)
    anova_q["quarter"] = q
    quarterly_anovas.append(anova_q)

    save_pca_plot(Xq_scaled, feat_q["cluster"], f"{OUTPUT_DIR}/quarterly/cluster_pca_Q{q}.png",
                  f"Q{q} 2025 SKU clusters (2D via PCA)")

    profile_q, feat_q = business_and_financial_profile(feat_q, "cluster")
    profile_q["quarter"] = q
    quarterly_profiles.append(profile_q)
    name_map_q = dict(zip(profile_q["cluster"], profile_q["cluster_name"]))
    feat_q["cluster_name"] = feat_q["cluster"].map(name_map_q)

    print(profile_q[["cluster", "cluster_name", "n_skus", "pct_of_total_revenue",
                      "pct_of_total_stockout_exposure"]].to_string(index=False))

    quarterly_results.append(feat_q)

feat_quarterly = pd.concat(quarterly_results, ignore_index=True)
profile_quarterly = pd.concat(quarterly_profiles, ignore_index=True)
anova_quarterly = pd.concat(quarterly_anovas, ignore_index=True)
silhouette_quarterly = pd.concat(quarterly_silhouettes, ignore_index=True)
centroid_dist_quarterly = pd.concat(quarterly_centroid_dists, ignore_index=True)

feat_quarterly.to_csv(f"{OUTPUT_DIR}/quarterly/sku_clusters_quarterly.csv", index=False)
profile_quarterly.to_csv(f"{OUTPUT_DIR}/quarterly/cluster_business_profile_quarterly.csv", index=False)
anova_quarterly.to_csv(f"{OUTPUT_DIR}/quarterly/cluster_anova_quarterly.csv", index=False)
silhouette_quarterly.to_csv(f"{OUTPUT_DIR}/quarterly/cluster_silhouette_scores_quarterly.csv", index=False)
centroid_dist_quarterly.to_csv(f"{OUTPUT_DIR}/quarterly/cluster_centroid_distances_quarterly.csv", index=False)
print(f"\nSaved Output 2 files to: {OUTPUT_DIR}/quarterly/")

# Cross-quarter movement: does a SKU's business-friendly NAME change quarter to quarter?
# (comparing NAMES, not raw cluster ids, since each quarter is a separate k-means run
# and ids 0/1/2 aren't inherently the same group across runs -- names are.)
pivot = feat_quarterly.pivot_table(index="sku_id", columns="quarter", values="cluster_name", aggfunc="first")
pivot["n_distinct_names_across_year"] = pivot.apply(lambda row: len(set(row.dropna())), axis=1)
stability = pivot["n_distinct_names_across_year"].value_counts().sort_index()
print("\nHow many SKUs kept the same business-friendly segment name all year vs. moved:")
print(stability.to_string())
pivot.to_csv(f"{OUTPUT_DIR}/quarterly/sku_segment_name_stability.csv")
print(f"Saved: {OUTPUT_DIR}/quarterly/sku_segment_name_stability.csv")

# =========================================================================
# SUMMARY: annual vs quarterly, side by side
# =========================================================================
summary = pd.DataFrame({
    "approach": ["Annual (whole-year)", "Quarterly (avg across 4 quarters)"],
    "n_clusters": [K_annual, round(profile_quarterly.groupby("quarter")["cluster"].nunique().mean(), 1)],
    "top_cluster_pct_of_revenue": [
        profile_annual["pct_of_total_revenue"].max(),
        round(profile_quarterly.groupby("quarter")["pct_of_total_revenue"].max().mean(), 1),
    ],
})
summary.to_csv(f"{OUTPUT_DIR}/annual_vs_quarterly_summary.csv", index=False)
print("\n=== Annual vs Quarterly summary ===")
print(summary.to_string(index=False))
print(f"\nSaved: {OUTPUT_DIR}/annual_vs_quarterly_summary.csv")
print("\nDone.")
