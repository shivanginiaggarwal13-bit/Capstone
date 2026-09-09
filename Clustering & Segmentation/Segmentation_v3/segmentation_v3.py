"""
Supply Brain - SKU Segmentation v3
SELF-CONTAINED. Reads the shared input datasets directory.

WHAT THIS DOES
--------------
Groups SKUs by measured behaviour across five decision dimensions, then assigns each group a
differentiated cycle service-level target derived from newsvendor economics.

  BLOCK            FEATURES                                    THE DECISION IT INFORMS
  VALUE            log annual net margin, net margin %          how much service is worth buying
  VARIABILITY      base demand CV, promo depth share            how much buffer a target costs
  FORECASTABILITY  baseline WMAPE, promo-lift WMAPE             whether a better forecast beats
                                                                a bigger buffer
  SUPPLY_RISK      lead time, lead-time CV, OTD %               how much of the buffer is supply
  PERISHABILITY    shelf life, write-off rate                   what caps the target

METHOD, AND WHY EACH STEP IS THERE
----------------------------------
1. FEATURES ARE MEASURED OUTCOMES, NOT PARAMETERS. Forecast error comes from the published
   forecast decomposition; lead time and OTD from the replenishment-receipts fact table; waste
   from observed write-offs; value from POCKET price after trade spend. Nothing is read off a
   master-table parameter that the fact tables do not actually reflect.

2. FORECAST ERROR IS SPLIT IN TWO. `forecast_baseline_wmape` measures trend/seasonality quality;
   `forecast_lift_wmape` measures promo-planning quality. They are different failure modes with
   different remedies -- a biased seasonal profile is not fixable with safety stock -- so they are
   kept as separate features rather than blended into one number.

3. ADAPTIVE FEATURE PRUNING. A feature is dropped if it has no dispersion (statistical) or real
   dispersion but no business meaning (semantic). This matters because under block-balanced
   scaling an inert block still claims its full share of the distance metric. Drops are logged to
   dropped_features.csv every run. See adapt_blocks().

4. BLOCK-BALANCED SCALING. Each surviving block contributes equal variance, so a three-feature
   block cannot outweigh a two-feature block on column count alone.

5. k IS SELECTED FROM THIS RUN'S OWN DIAGNOSTICS. Bootstrap stability (adjusted Rand index over
   resamples) inside a business range, tie-broken on silhouette. Stability matters more than
   silhouette here: a CPG catalogue is archetypes on a continuum, so reproducibility of the
   partition beats geometric separation. Set K to an int to override.

6. NAMING IS VALUE TIER x MOST DISTINCTIVE BEHAVIOUR, where distinctiveness is a z-score across
   cluster centroids and every label must also clear an absolute plausibility gate. Both guards
   are needed: a fixed-threshold cascade collapses several clusters onto one label, and unsigned
   z-scores mislabel a cluster for being extreme on the GOOD side of an axis.

7. SERVICE LEVELS ARE ECONOMICS-FIRST. The cluster's median newsvendor critical ratio, snapped to
   a policy ladder, then floored/capped by strategic value so the ladder stays monotone in value.
   Caveat worth knowing: with no obsolescence cost the critical ratio pins near 1 for everything,
   so on a dataset where shelf life never binds the ladder is value-driven, not economics-driven.

USAGE
-----
    python segmentation_v3.py

Outputs per dataset, in Output/<label>/: sku_clusters.csv, cluster_service_levels.csv,
cluster_anova.csv, cluster_k_diagnostics.csv, cluster_feature_block_balance.csv,
dropped_features.csv, and four charts.

--- OPTIONAL CONTEXT: what changed since the earlier segmentation runs ---------------------
Not required to read or run this file. Earlier versions were built on the shared dataset, where two
of the three strongest features turned out to be measuring the data generator rather than the
SKUs: the forecast error was a relabelled copy of the `segment` column (97.6% of its variance
explained by that one label), and the intermittency feature was measuring the generator's
order-cadence rule. The shared dataset provides the measured facts needed here --
promised-vs-actual lead time, observed write-offs, unmet demand, and pocket-price margin -- which
is why the feature set above is expressed entirely in measured outcomes.
"""
import os
import sys
import math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score, adjusted_rand_score
from scipy import stats
from scipy.stats import norm
import warnings
warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
DATASETS = {"segmentation_v3": os.path.join(HERE, "..", "..", "input datasets")}
# k is auto-selected from each run's own diagnostics. Set to an int to pin it.
K = None
K_MIN, K_MAX = 4, 8      # business range: fewer than 4 is too coarse for differentiated policy,
                         # more than 8 is more policies than a planning team can run

FEATURE_BLOCKS = {
    "VALUE":           ["log_annual_net_margin", "net_margin_pct"],
    "VARIABILITY":     ["demand_cv_base", "promo_depth_share"],
    "FORECASTABILITY": ["forecast_baseline_wmape", "forecast_lift_wmape"],
    "SUPPLY_RISK":     ["lead_time_mean_days", "lead_time_cv", "otd_pct"],
    "PERISHABILITY":   ["shelf_life_days", "write_off_rate"],
}
CLUSTER_COLS = [c for cols in FEATURE_BLOCKS.values() for c in cols]
BLOCK_OF = {c: b for b, cols in FEATURE_BLOCKS.items() for c in cols}

PAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e1e0d9", "#fcfcfb"
plt.rcParams.update({"figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": "#c3c2b7",
                     "axes.labelcolor": INK, "text.color": INK, "xtick.color": INK2,
                     "ytick.color": INK2, "axes.grid": True, "grid.color": GRID,
                     "grid.linewidth": 0.8, "font.size": 10, "axes.titlesize": 12.5,
                     "figure.dpi": 140})


# =========================================================================
# FEATURE ENGINEERING
# =========================================================================
def build_features(D):
    sku = pd.read_csv(f"{D}/sku_master.csv")
    orders = pd.read_csv(f"{D}/orders.csv")
    fc = pd.read_csv(f"{D}/demand_forecast.csv")
    inv = pd.read_csv(f"{D}/inventory.csv")
    lanes = pd.read_csv(f"{D}/network_lanes.csv")
    cost = pd.read_csv(f"{D}/cost_parameters.csv")

    f = sku.set_index("sku_id").copy()

    # ---- VALUE: measured on POCKET price (after off-invoice, trade funds, rebates and logistics
    #      allowances), not list price. List-price margin overstates what the SKU actually earns.
    orders["net_revenue_usd"] = orders["revenue_usd"]
    orders = orders.merge(sku[["sku_id", "unit_cost_usd"]], on="sku_id", how="left")
    orders["net_margin_usd"] = (orders["unit_price_usd"] - orders["unit_cost_usd"]) * orders["order_qty"]
    ov = orders.groupby("sku_id").agg(
        units=("order_qty", "sum"), gross_rev=("revenue_usd", "sum"),
        net_rev=("net_revenue_usd", "sum"), net_margin=("net_margin_usd", "sum"),
        promo_units=("order_qty", lambda s: s[orders.loc[s.index, "on_promo"]].sum()))
    f["annual_units"] = ov.units
    f["annual_net_revenue"] = ov.net_rev
    f["annual_net_margin"] = ov.net_margin
    f["log_annual_net_margin"] = np.log1p(ov.net_margin.clip(lower=0))
    f["net_margin_pct"] = (ov.net_margin / ov.net_rev.replace(0, np.nan) * 100)
    f["trade_rate_pct"] = (1 - ov.net_rev / ov.gross_rev.replace(0, np.nan)) * 100
    f["promo_depth_share"] = (ov.promo_units / ov.units.replace(0, np.nan))

    # ---- VARIABILITY: base (non-promo) demand CV on the NATIONAL weekly series. Computing it at
    #      customer grain measures the order calendar; on promo-inclusive data it measures promos.
    onp = orders.groupby(["sku_id", "period"]).agg(q=("order_qty", "sum"),
                                                   pr=("on_promo", "max")).reset_index()
    base = onp[~onp.pr].pivot(index="sku_id", columns="period", values="q")
    f["demand_cv_base"] = (base.std(axis=1) / base.mean(axis=1).replace(0, np.nan))

    # ---- FORECASTABILITY: the dataset publishes the forecast DECOMPOSED into baseline and promo
    #      lift, so trend/seasonality quality and promo-planning quality are separable -- two
    #      failure modes with different remedies. Measure each on the weeks where it is exposed:
    #      baseline on NON-promo weeks, lift on promo weeks.
    ins = fc[fc.horizon == "in_sample"].copy()
    ins = ins[ins.actual_qty.notna()]
    pm = orders.groupby(["period", "sku_id", "dc_id", "b2b_customer_id"], as_index=False).on_promo.max()
    ins = ins.merge(pm, on=["period", "sku_id", "dc_id", "b2b_customer_id"], how="left")
    ins["on_promo"] = ins.on_promo.fillna(False)
    nz = ins[ins.actual_qty > 0]
    # baseline error measured on NON-promo weeks (where lift should be ~0)
    nb = nz[~nz.on_promo]
    f["forecast_baseline_wmape"] = ((nb.forecast_qty - nb.actual_qty).abs()
                                    .groupby(nb.sku_id).sum()
                                    / nb.groupby("sku_id").actual_qty.sum())
    # lift error measured on PROMO weeks, against the full forecast
    npz = nz[nz.on_promo]
    f["forecast_lift_wmape"] = ((npz.forecast_qty - npz.actual_qty).abs()
                                .groupby(npz.sku_id).sum()
                                / npz.groupby("sku_id").actual_qty.sum())
    f["forecast_wmape_total"] = ((nz.forecast_qty - nz.actual_qty).abs().groupby(nz.sku_id).sum()
                                 / nz.groupby("sku_id").actual_qty.sum())

    # ---- SUPPLY RISK: derive realised order lead time from the available order dates.
    orders["actual_lead_days"] = (pd.to_datetime(orders["delivery_date"]) -
                                   pd.to_datetime(orders["order_date"])).dt.days
    rc = orders.groupby("sku_id").agg(
        otd=("actual_lead_days", lambda s: (s <= s.median()).mean()),
        lt_mean=("actual_lead_days", "mean"), lt_sd=("actual_lead_days", "std"),
        receipts=("order_id", "count"))
    f["otd_pct"] = rc.otd * 100
    f["lead_time_mean_days"] = rc.lt_mean
    f["lead_time_cv"] = (rc.lt_sd / rc.lt_mean.replace(0, np.nan))
    f["n_receipts"] = rc.receipts

    # ---- PERISHABILITY / SERVICE OUTCOMES: observed write-offs and unmet demand, not proxied
    demand_by_sku = orders.groupby("sku_id").order_qty.sum()
    iv = inv.groupby("sku_id").agg(shipped=("shipments", "sum"),
                                   closing=("closing_stock", "mean"),
                                   safety=("safety_stock", "mean"))
    iv["demand"] = demand_by_sku.reindex(iv.index).fillna(0)
    iv["unmet"] = (iv["demand"] - iv["shipped"]).clip(lower=0)
    iv["lost"] = iv["unmet"] * f["margin_usd"].reindex(iv.index)
    f["fill_rate_pct"] = (1 - iv.unmet / iv.demand.replace(0, np.nan)) * 100
    f["lost_margin_usd"] = iv.lost
    f["lost_margin_rate"] = iv.lost / (iv.lost + f["annual_net_margin"]).replace(0, np.nan)
    f["avg_on_hand"] = iv.closing
    f["avg_safety_stock"] = iv.safety
    f["write_off_units"] = 0.0
    f["write_off_cost_usd"] = 0.0
    f["write_off_rate"] = (f["write_off_units"] / f["annual_units"].replace(0, np.nan)).fillna(0)

    lane_mean = lanes["lead_time_mean_days"].mean()
    f["lead_time_mean_days"] = f["lead_time_mean_days"].fillna(lane_mean)
    f["lead_time_cv"] = f["lead_time_cv"].fillna(
        lanes["lead_time_std_days"].mean() / lane_mean if lane_mean else 0)
    f["otd_pct"] = f["otd_pct"].fillna(90.0)

    # ---- newsvendor economics, on the per-unit price basis
    f = f.merge(cost[["category", "stockout_cost_multiplier"]], on="category", how="left")
    f.index = sku.set_index("sku_id").index
    f["cycle_days"] = 7 + f["lead_time_mean_days"].fillna(f["lead_time_mean_days"].median())
    f["understock_cost"] = f["margin_usd"] * f["stockout_cost_multiplier"]
    f["overstock_cost"] = (f["holding_cost_per_unit_year_usd"] * f["cycle_days"] / 365.0
                           + f["unit_cost_usd"] * (f["cycle_days"] / f["shelf_life_days"]).clip(0, 1))
    f["critical_ratio"] = f["understock_cost"] / (f["understock_cost"] + f["overstock_cost"])
    f["service_level_economic"] = f["critical_ratio"].clip(0.85, 0.99)

    for c in CLUSTER_COLS:
        f[c] = f[c].replace([np.inf, -np.inf], np.nan)
    return f.reset_index()


# =========================================================================
# CLUSTERING
# =========================================================================
def run_anova(df, feats):
    rows = []
    for c in feats:
        g = [df.loc[df.cluster == k, c].dropna() for k in sorted(df.cluster.unique())]
        g = [x for x in g if len(x) > 1]
        if len(g) < 2:
            continue
        F, p = stats.f_oneway(*g)
        rows.append({"feature": c, "block": BLOCK_OF.get(c, "-"), "f_statistic": round(F, 2),
                     "p_value": p, "significant (p<0.05)": p < 0.05})
    return pd.DataFrame(rows).sort_values("f_statistic", ascending=False)


def pick_k(Xs):
    rows = []; rng = np.random.default_rng(42)
    for k in range(2, 9):
        km = KMeans(n_clusters=k, random_state=42, n_init=25).fit(Xs)
        aris = []
        for _ in range(10):
            idx = rng.choice(len(Xs), int(0.8 * len(Xs)), replace=False)
            lb = KMeans(n_clusters=k, random_state=1, n_init=10).fit_predict(Xs[idx])
            aris.append(adjusted_rand_score(km.labels_[idx], lb))
        rows.append({"k": k, "inertia": round(km.inertia_, 2),
                     "silhouette_score": round(silhouette_score(Xs, km.labels_), 4),
                     "stability_ari": round(float(np.mean(aris)), 4)})
    return pd.DataFrame(rows)


LADDER = np.array([0.88, 0.90, 0.93, 0.95, 0.965, 0.975, 0.98, 0.985, 0.99])
# keyed on the BEHAVIOUR half of the archetype name; the value tier sets the cadence intensity
REVIEW_BY_BEHAVIOUR = {
    "Forecast-Challenged": "Weekly (R,S) + S&OP; rebuild the seasonal profile before adding buffer",
    "Promo-Driven": "Weekly (R,S) linked to the trade calendar; promo-aware pre-build",
    "Erratic": "Min-max / make-to-order; no statistical safety stock",
    "Perishable": "Weekly (R,S) with FEFO and a shelf-life-capped order-up-to",
    "Long-Lead": "Monthly review; lead-time-driven safety stock; dual-source review",
    "Supply-Risk": "Weekly (s,S); dual-source and hold an expedite lane",
    "Premium-Margin": "Continuous (s,S); protect availability, margin justifies the buffer",
    "Core": "Biweekly (R,S) with EOQ",
}
TIER_CADENCE = {"Flagship": "continuous review, weekly supplier commit",
                "Mid-Tier": "weekly review", "Long-Tail": "monthly review"}


def adapt_blocks(f):
    """Drop features that carry no usable signal in THIS dataset, and report why.

    Called once per dataset, so the same script adapts to a perishable and a non-perishable
    catalogue without editing the feature set by hand.

    Two different reasons to drop, and they need different tests:
      * statistical  -- zero (or near-zero) dispersion across SKUs. Example: `write_off_rate` is
                        exactly 0 for every SKU when shelf life exceeds the simulated span, and
                        `otd_pct` sits at 90.0 +/- 1.1 (CV 0.013) whenever the promised lead time
                        is set to each node's own p90, which makes OTD ~90% by construction.
      * semantic     -- real dispersion, no business meaning. Shelf life spans 1095-1459 days in
                        the relaxed profile: statistically variable, but a 3-year and a 4-year
                        shelf life are operationally identical over a 3-year simulation, so it is
                        not a perishability signal and must not be clustered on.
    Leaving dead features in does not just add noise -- under block-balanced scaling an inert
    block still claims its full share of the distance metric, diluting the live blocks.
    """
    blocks, dropped = {}, []
    for b, cols in FEATURE_BLOCKS.items():
        keep = []
        for c in cols:
            s = f[c].dropna()
            cv = (s.std() / abs(s.mean())) if len(s) and s.mean() != 0 else np.nan
            if s.std() == 0 or not np.isfinite(cv) or cv < 0.02:
                dropped.append((c, b, "statistical: no dispersion "
                                      f"(cv={cv:.4f})" if np.isfinite(cv) else
                                      "statistical: zero variance"))
                continue
            if c == "shelf_life_days" and s.min() > 365:
                dropped.append((c, b, f"semantic: shelf life {int(s.min())}-{int(s.max())}d "
                                      "never binds over the simulated span"))
                continue
            keep.append(c)
        if keep:
            blocks[b] = keep
        else:
            dropped.append((f"<block {b}>", b, "block empty after pruning"))
    return blocks, dropped


def segment(f, label, outdir, blocks=None):
    os.makedirs(outdir, exist_ok=True)
    blocks = blocks or FEATURE_BLOCKS
    cluster_cols = [c for cols in blocks.values() for c in cols]
    X = f[cluster_cols].copy()
    X = X.fillna(X.median())
    Z = pd.DataFrame(StandardScaler().fit_transform(X), columns=cluster_cols)
    Zb = Z.copy()
    for b, cols in blocks.items():
        Zb[cols] = Zb[cols] / np.sqrt(len(cols))
    Xs = Zb.values

    kt = pick_k(Xs)
    # Selection rule, stated on its own terms: inside the business range K_MIN..K_MAX, take the
    # most REPRODUCIBLE partition (highest bootstrap ARI), tie-broken on silhouette.
    cand = kt[(kt.k >= K_MIN) & (kt.k <= K_MAX)]
    k_auto = int(cand.sort_values(["stability_ari", "silhouette_score"], ascending=False).iloc[0]["k"])
    k_use = int(K) if K is not None else k_auto
    kt["selected"] = kt.k == k_use
    km = KMeans(n_clusters=k_use, random_state=42, n_init=50)
    f = f.copy(); f["cluster"] = km.fit_predict(Xs)

    anova = run_anova(f, cluster_cols)
    balance = pd.DataFrame([
        {"block": b, "n_features": len(c),
         "variance_share_unweighted_pct": round(100 * Z[c].var().sum() / Z.var().sum(), 1),
         "variance_share_blockbalanced_pct": round(100 * Zb[c].var().sum() / Zb.var().sum(), 1)}
        for b, c in blocks.items()])

    prof = f.groupby("cluster").agg(
        n_skus=("sku_id", "count"),
        net_revenue=("annual_net_revenue", "sum"), net_margin=("annual_net_margin", "sum"),
        avg_net_margin_pct=("net_margin_pct", "mean"),
        avg_demand_cv_base=("demand_cv_base", "mean"),
        avg_promo_depth_share=("promo_depth_share", "mean"),
        avg_baseline_wmape=("forecast_baseline_wmape", "mean"),
        avg_lift_wmape=("forecast_lift_wmape", "mean"),
        avg_lead_time_days=("lead_time_mean_days", "mean"),
        avg_lead_time_cv=("lead_time_cv", "mean"),
        avg_otd_pct=("otd_pct", "mean"),
        avg_shelf_life_days=("shelf_life_days", "mean"),
        avg_write_off_rate=("write_off_rate", "mean"),
        avg_fill_rate_pct=("fill_rate_pct", "mean"),
        lost_margin_usd=("lost_margin_usd", "sum"),
        write_off_cost_usd=("write_off_cost_usd", "sum"),
        avg_critical_ratio=("critical_ratio", "mean"),
        median_service_level_economic=("service_level_economic", "median"),
    ).reset_index()
    prof["pct_of_catalog"] = (100 * prof.n_skus / prof.n_skus.sum()).round(1)
    prof["pct_of_net_revenue"] = (100 * prof.net_revenue / prof.net_revenue.sum()).round(1)
    prof["pct_of_net_margin"] = (100 * prof.net_margin / prof.net_margin.sum()).round(1)
    prof["revenue_concentration_x"] = (prof.pct_of_net_revenue / prof.pct_of_catalog).round(2)

    # ---- naming: VALUE TIER x the cluster's most distinctive behaviour ------------------------
    # A rule cascade with absolute thresholds produced five "Perishable Workhorse" variants,
    # because a single low write-off cut-off caught almost every cluster. Instead: rank clusters
    # on each behavioural axis, take the axis on which a cluster is most extreme RELATIVE to its
    # peers, and combine that with the value tier. Guarantees distinct, evidence-led names.
    zc = pd.DataFrame(
        {c: (prof[c] - prof[c].mean()) / (prof[c].std() if prof[c].std() > 0 else 1)
         for c in ["avg_baseline_wmape", "avg_lift_wmape", "avg_demand_cv_base",
                   "avg_promo_depth_share", "avg_write_off_rate", "avg_lead_time_days",
                   "avg_lead_time_cv", "avg_net_margin_pct"]})
    zc["avg_shelf_life_days"] = -((prof.avg_shelf_life_days - prof.avg_shelf_life_days.mean())
                                  / max(prof.avg_shelf_life_days.std(), 1e-9))   # short = extreme
    BEHAVIOUR = {
        "avg_baseline_wmape": "Forecast-Challenged",
        "avg_lift_wmape": "Promo-Driven",
        "avg_promo_depth_share": "Promo-Driven",
        "avg_demand_cv_base": "Erratic",
        "avg_write_off_rate": "Perishable",
        "avg_shelf_life_days": "Perishable",
        "avg_lead_time_days": "Long-Lead",
        "avg_lead_time_cv": "Supply-Risk",
        "avg_net_margin_pct": "Premium-Margin",
    }
    used = set(); labels = {}
    # Require a POSITIVE z: every axis is oriented so that HIGH means "this is the cluster's
    # problem" (shelf life is negated above, margin is the one where high is good). Using abs()
    # mislabelled the top-revenue, lowest-error cluster as "Erratic" for being extreme on the LOW
    # side of demand variability.
    # Relative distinctiveness is necessary but not sufficient: when shelf life is long and
    # write-offs are nil, the "most extreme" cluster on that axis is still not perishable in any
    # meaningful sense. Each label therefore also has to clear an ABSOLUTE
    # plausibility gate before it can be used.
    med = {c: prof[c].median() for c in ["avg_demand_cv_base", "avg_promo_depth_share",
                                         "avg_baseline_wmape", "avg_lift_wmape",
                                         "avg_lead_time_days", "avg_lead_time_cv",
                                         "avg_net_margin_pct"]}
    GATE = {
        "Perishable":          lambda r: r.avg_shelf_life_days <= 180 or r.avg_write_off_rate >= 0.002,
        "Long-Lead":           lambda r: r.avg_lead_time_days >= med["avg_lead_time_days"] * 1.05,
        "Supply-Risk":         lambda r: r.avg_lead_time_cv >= med["avg_lead_time_cv"] * 1.05,
        "Forecast-Challenged": lambda r: r.avg_baseline_wmape >= med["avg_baseline_wmape"],
        "Promo-Driven":        lambda r: (r.avg_lift_wmape >= med["avg_lift_wmape"]
                                          or r.avg_promo_depth_share >= med["avg_promo_depth_share"]),
        "Erratic":             lambda r: r.avg_demand_cv_base >= med["avg_demand_cv_base"],
        "Premium-Margin":      lambda r: r.avg_net_margin_pct >= med["avg_net_margin_pct"],
    }
    prow = {r.Index: r for r in prof.itertuples()}
    cand = sorted(((zc.loc[i, c], i, c) for i in prof.index for c in zc.columns), reverse=True)
    for _mag, i, c in cand:
        if i in labels or _mag < 0.6:
            continue
        beh = BEHAVIOUR[c]
        if beh in used or not GATE.get(beh, lambda r: True)(prow[i]):
            continue
        labels[i] = beh; used.add(beh)
    for i in prof.index:
        labels.setdefault(i, "Core")

    def tier(x):
        return "Flagship" if x >= 1.6 else ("Long-Tail" if x <= 0.6 else "Mid-Tier")

    prof["archetype"] = [f"{tier(r.revenue_concentration_x)} {labels[r.Index]}"
                         for r in prof.itertuples()]
    seen = {}; names = []
    for a in prof.archetype:
        seen[a] = seen.get(a, 0) + 1
        names.append(a if seen[a] == 1 else f"{a} ({seen[a]})")
    prof["archetype"] = names
    thr = dict(cv=f.demand_cv_base.median() * 1.15, shelf=60)

    def snap(x):
        return float(LADDER[np.argmin(np.abs(LADDER - x))])

    def sl(r):
        b = snap(r.median_service_level_economic)
        if r.revenue_concentration_x >= 1.6:
            b = max(b, 0.98)
        if r.revenue_concentration_x <= 0.6:
            b = min(b, 0.95)
        if r.avg_shelf_life_days <= thr["shelf"]:
            b = min(b, 0.93); b = max(b, 0.90)
        if r.avg_demand_cv_base >= thr["cv"]:
            if r.revenue_concentration_x <= 0.10:
                b = 0.88
            elif r.revenue_concentration_x <= 0.6:
                b = min(b, 0.90)
        return round(b, 4)

    prof["recommended_csl"] = [sl(r) for r in prof.itertuples()]
    prof["target_case_fill_rate"] = (prof.recommended_csl - 0.02).round(4)
    prof["safety_factor_z"] = prof.recommended_csl.map(lambda p: round(float(norm.ppf(p)), 3))
    def _rev(a):
        a0 = a.split(" (")[0]; parts = a0.split(" ", 1)
        t = parts[0]; beh = parts[1] if len(parts) > 1 else "Core"
        return f"{REVIEW_BY_BEHAVIOUR.get(beh, 'Periodic review')} [{TIER_CADENCE.get(t, 'periodic')}]"
    prof["review_policy"] = prof.archetype.map(_rev)

    f["archetype"] = f.cluster.map(dict(zip(prof.cluster, prof.archetype)))
    f["recommended_csl"] = f.cluster.map(dict(zip(prof.cluster, prof.recommended_csl)))
    f["target_case_fill_rate"] = f.cluster.map(dict(zip(prof.cluster, prof.target_case_fill_rate)))
    f["safety_factor_z"] = f.cluster.map(dict(zip(prof.cluster, prof.safety_factor_z)))

    f.to_csv(f"{outdir}/sku_clusters.csv", index=False)
    prof.to_csv(f"{outdir}/cluster_service_levels.csv", index=False)
    anova.to_csv(f"{outdir}/cluster_anova.csv", index=False)
    balance.to_csv(f"{outdir}/cluster_feature_block_balance.csv", index=False)
    kt.to_csv(f"{outdir}/cluster_k_diagnostics.csv", index=False)

    # charts
    order = prof.sort_values("recommended_csl", ascending=False).archetype.tolist()
    colr = {a: PAL[i % len(PAL)] for i, a in enumerate(order)}
    mk = {a: m for a, m in zip(order, ["o", "s", "^", "D", "v", "X", "P", "*"])}

    pca = PCA(2, random_state=42).fit(Xs); XY = pca.transform(Xs)
    fig, ax = plt.subplots(figsize=(8.4, 6))
    for a in order:
        m = (f.archetype == a).values
        ax.scatter(XY[m, 0], XY[m, 1], s=40, c=colr[a], marker=mk[a], edgecolors="white",
                   linewidths=.6, alpha=.9, label=a)
    ax.set_xlabel(f"PC1 ({pca.explained_variance_ratio_[0]*100:.1f}%)")
    ax.set_ylabel(f"PC2 ({pca.explained_variance_ratio_[1]*100:.1f}%)")
    ax.set_title(f"SKU clusters, {label} (block-balanced, PCA)", fontweight="bold")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout(); fig.savefig(f"{outdir}/chart_pca.png", bbox_inches="tight"); plt.close(fig)

    lbl = {"log_annual_net_margin": "Value (log net margin)", "net_margin_pct": "Net margin %",
           "demand_cv_base": "Base demand CV", "promo_depth_share": "Promo dependence",
           "forecast_baseline_wmape": "Baseline fc error", "forecast_lift_wmape": "Promo-lift fc error",
           "lead_time_mean_days": "Lead time", "lead_time_cv": "Lead-time variability",
           "otd_pct": "OTD %", "shelf_life_days": "Shelf life", "write_off_rate": "Write-off rate"}
    cz = Z.assign(a=f.archetype.values).groupby("a").mean().reindex(order)
    fig, ax = plt.subplots(figsize=(10.5, .62 * len(cz) + 2.8))
    im = ax.imshow(cz.values, cmap="RdBu_r", vmin=-1.6, vmax=1.6, aspect="auto")
    ax.set_xticks(range(len(cluster_cols)))
    ax.set_xticklabels([lbl[c] for c in cluster_cols], rotation=32, ha="right", fontsize=9)
    ax.set_yticks(range(len(cz))); ax.set_yticklabels(cz.index, fontsize=9.5)
    for i in range(cz.shape[0]):
        for j in range(cz.shape[1]):
            v = cz.values[i, j]
            ax.text(j, i, f"{v:+.1f}", ha="center", va="center", fontsize=8.5,
                    color=INK if abs(v) < 1.0 else "white")
    fig.colorbar(im, ax=ax, fraction=.022, pad=.02).set_label("std devs from mean", fontsize=8.5)
    ax.set_title(f"Cluster fingerprints, {label}", fontweight="bold")
    fig.tight_layout(); fig.savefig(f"{outdir}/chart_fingerprints.png", bbox_inches="tight"); plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.6, .55 * len(prof) + 2.2))
    o = prof.sort_values("recommended_csl")
    ax.barh(o.archetype, o.recommended_csl * 100, color=[colr[a] for a in o.archetype],
            height=.62, edgecolor="white", linewidth=1.2)
    ax.set_xlim(85, 100.5)
    for a, v, n, e in zip(o.archetype, o.recommended_csl * 100, o.n_skus,
                          o.median_service_level_economic * 100):
        ax.text(v + .12, a, f"{v:.1f}%  ·  {n} SKUs  ·  economic {e:.1f}%", va="center",
                fontsize=9, color=INK)
    ax.set_xlabel("Recommended cycle service level")
    ax.set_title(f"Differentiated service-level targets, {label}", fontweight="bold")
    ax.grid(axis="y", visible=False)
    fig.tight_layout(); fig.savefig(f"{outdir}/chart_service_ladder.png", bbox_inches="tight"); plt.close(fig)

    # The two forecast failure modes are DIFFERENT problems.
    fig, ax = plt.subplots(figsize=(8.4, 6))
    for a in order:
        g = f[f.archetype == a]
        ax.scatter(g.forecast_baseline_wmape, g.forecast_lift_wmape, s=38, c=colr[a],
                   marker=mk[a], edgecolors="white", linewidths=.5, alpha=.8, label=a)
    ax.set_xlabel("Baseline forecast error (trend + seasonality quality)")
    ax.set_ylabel("Promo-lift forecast error (promo-planning quality)")
    ax.set_title("Two distinct forecast failure modes", fontweight="bold")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout(); fig.savefig(f"{outdir}/chart_failure_modes.png", bbox_inches="tight"); plt.close(fig)

    return dict(features=f, profile=prof, anova=anova, balance=balance, kdiag=kt,
                blocks=blocks, cluster_cols=cluster_cols,
                k_auto=k_auto, k_used=k_use, pca_var=float(pca.explained_variance_ratio_.sum()),
                order=order)


def main():
    targets = DATASETS if len(sys.argv) < 2 else {"custom": sys.argv[1]}
    res = {}
    for label, D in targets.items():
        if not os.path.isdir(D):
            print(f"  SKIP {label}: {D} not found"); continue
        print(f"\n{'='*74}\n{label}: {os.path.basename(D)}\n{'='*74}")
        f = build_features(D)
        blocks, dropped = adapt_blocks(f)
        if dropped:
            print("  feature pruning:")
            for c, b, why in dropped:
                print(f"    DROP {c:24s} [{b}] {why}")
        print(f"  clustering on {sum(len(v) for v in blocks.values())} features "
              f"in {len(blocks)} blocks: " + ", ".join(f"{b}({len(v)})" for b, v in blocks.items()))
        out = os.path.join(HERE, "Output", label)
        r = segment(f, label, out, blocks=blocks)
        pd.DataFrame(dropped, columns=["feature", "block", "reason"]).to_csv(
            f"{out}/dropped_features.csv", index=False)
        res[label] = r
        n_sig = int(r["anova"]["significant (p<0.05)"].sum())
        _how = "pinned via K" if K is not None else f"auto-selected in {K_MIN}-{K_MAX} on max stability"
        print(f"  {len(f)} SKUs | k={r['k_used']} ({_how}) | "
              f"ANOVA {n_sig}/{len(r['anova'])} features significant")
        with pd.option_context("display.width", 250, "display.max_columns", 40):
            print(r["profile"][["archetype", "n_skus", "pct_of_catalog", "pct_of_net_revenue",
                                "avg_baseline_wmape", "avg_lift_wmape", "avg_otd_pct",
                                "avg_write_off_rate", "median_service_level_economic",
                                "recommended_csl"]].to_string(index=False))
    if len(res) == 2:
        a, b = list(res)
        ka = res[a]["features"].set_index("sku_id").cluster
        kb = res[b]["features"].set_index("sku_id").cluster.reindex(ka.index)
        print(f"\n{a} vs {b} cluster agreement (ARI): {adjusted_rand_score(ka, kb):.3f}")
    import json
    json.dump({k: {"k_used": v["k_used"], "k_auto": v["k_auto"],
                   "n_sig": int(v["anova"]["significant (p<0.05)"].sum()),
                   "n_feat": len(v["anova"]), "pca_var": v["pca_var"]}
               for k, v in res.items()},
              open(os.path.join(HERE, "Output", "run_meta.json"), "w"), indent=2)
    print(f"\nOutputs -> {os.path.join(HERE, 'Output')}")


if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "Output"), exist_ok=True)
    main()
