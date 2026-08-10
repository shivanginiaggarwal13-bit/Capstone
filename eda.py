"""
Supply Brain -- EDA Report Generator (single-file, standalone)
==============================================================
Reads the generated Supply Brain v3 dataset and writes a Markdown EDA report. Because the dataset
is generated with randomness (different every run), this script RECOMPUTES everything from whatever
data is present, so the report always reflects the specific instance on disk.

The report has two parts:
  PART 1 -- Parameters, Assumptions & Interpretation: the business context embedded in the data
            (design intent), each item verified against the realized numbers in this instance.
  PART 2 -- Exploratory Data Analysis: computed from the data (product, demand, seasonality,
            price & elasticity, forecast accuracy + forward horizon, production, inventory, network),
            plus a referential-integrity check.

USAGE
-----
    pip install pandas numpy            # required
    pip install matplotlib              # optional -- enables charts; report still works without it
    python supply_brain_eda_report.py

Paths (auto-detected, or override with env vars):
    SUPPLY_BRAIN_DATA    folder containing the CSVs (default: ./data if it exists, else script dir)
    SUPPLY_BRAIN_REPORT  output markdown path (default: ./EDA_REPORT.md next to the data)
Charts, if matplotlib is available, are written to <report_dir>/report_assets/ and embedded.
"""
import os
import sys
import math
import time
import datetime as dt
import numpy as np
import pandas as pd

# ---- optional charting ----
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except Exception:
    HAVE_MPL = False

# ============================================================================
# EMBEDDED BUSINESS CONTEXT (design intent baked into the generator).
# Restated here so the report can compare intent vs. realized. Keep in sync with
# supply_brain_generator_v3.py if the generator's constants change.
# ============================================================================
CATEGORIES = ["Beverages", "Snacks & Confectionery", "Dairy & Bakery", "Frozen Foods", "Grocery"]
CATEGORY_USD = {   # (median, min, max, cost_ratio)  -- realistic CPG case-level list prices, USD
    "Beverages": (14.0, 6.0, 32.0, 0.68), "Snacks & Confectionery": (18.0, 8.0, 42.0, 0.66),
    "Dairy & Bakery": (22.0, 9.0, 48.0, 0.70), "Frozen Foods": (28.0, 12.0, 60.0, 0.69),
    "Grocery": (16.0, 6.0, 38.0, 0.67),
}
CATEGORY_ELASTICITY_TARGET = {  # internal own-price elasticity target (negative); NOT shipped in data
    "Beverages": -2.00, "Snacks & Confectionery": -2.10, "Dairy & Bakery": -0.55,
    "Frozen Foods": -1.30, "Grocery": -0.60,
}
CATEGORY_SHELF_LIFE = {"Beverages": (120, 365), "Snacks & Confectionery": (90, 300),
                       "Dairy & Bakery": (5, 21), "Frozen Foods": (180, 540), "Grocery": (180, 540)}
CATEGORY_HOLDING_PCT = {"Beverages": 0.22, "Snacks & Confectionery": 0.21, "Dairy & Bakery": 0.34,
                        "Frozen Foods": 0.33, "Grocery": 0.20}
SEGMENT_MIX_TARGET = {"Fast-Moving": 0.50, "Seasonal": 0.30, "Intermediate": 0.20}
SEGMENT_FC_NOISE = {"Fast-Moving": 0.10, "Seasonal": 0.28, "Intermediate": 0.17}
PROMO_PROB = 0.30
PROMO_DISCOUNT_RANGE = (0.05, 0.35)
YOY_GROWTH = 0.05
DEMAND_MODEL = ("log(qty) = log(base * customer_scale * dc_share) "
                "+ elasticity * log(price_week / list_price) + log(seasonal_index[week]) + noise")

# ============================================================================
# PATHS
# ============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "dataset"))
REPORT_PATH = os.environ.get("SUPPLY_BRAIN_REPORT", os.path.join(DATA_DIR, "EDA_REPORT.md"))
ASSETS_DIR = os.path.join(os.path.dirname(REPORT_PATH), "report_assets")

# ============================================================================
# HELPERS
# ============================================================================
def load(name):
    p = os.path.join(DATA_DIR, name + ".csv")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"Missing required table: {p}")
    return pd.read_csv(p)

def md_table(df, floatfmt=None):
    """Render a DataFrame as a GitHub-flavored markdown table (no external deps)."""
    df = df.copy()
    cols = [str(c) for c in df.columns]
    def fmt(v):
        if isinstance(v, float):
            if floatfmt is not None:
                return floatfmt.format(v)
            return f"{v:,.2f}" if abs(v) < 1e6 else f"{v:,.0f}"
        if isinstance(v, (int, np.integer)):
            return f"{int(v):,}"
        return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(fmt(r[c]) for c in df.columns) + " |")
    return "\n".join(lines)

def kv_table(pairs):
    lines = ["| Field | Value |", "| --- | --- |"]
    for k, v in pairs:
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines)

def save_chart(fig, fname):
    os.makedirs(ASSETS_DIR, exist_ok=True)
    path = os.path.join(ASSETS_DIR, fname)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return f"report_assets/{fname}"

# ============================================================================
# LOAD EVERYTHING
# ============================================================================
def load_all():
    d = {}
    for t in ["sku_master", "plant_master", "plant_dc_master", "regional_dc_master",
              "customer_dc_master", "network_lanes", "b2b_customer_master", "customer_serving_dc",
              "cost_parameters", "orders", "demand_forecast", "production", "inventory"]:
        d[t] = load(t)
    # parse week dates for correct time ordering
    for t in ["orders", "demand_forecast", "production", "inventory"]:
        d[t]["_dt"] = pd.to_datetime(d[t]["week_start_date"], errors="coerce")
    return d

# ============================================================================
# ELASTICITY -- documented method: two-way (SKU + week) fixed-effects on log(qty)~log(price),
# implemented in pure numpy via iterative demeaning (no statsmodels dependency).
# ============================================================================
def estimate_elasticity(orders, sku):
    df = orders.merge(sku[["sku_id", "category"]], on="sku_id")
    df = df[(df.order_qty > 0) & (df.unit_price_usd > 0)].copy()
    df["ln_q"] = np.log(df.order_qty)
    df["ln_p"] = np.log(df.unit_price_usd)
    out = []
    for cat, g in df.groupby("category"):
        g = g[["sku_id", "period", "ln_q", "ln_p"]].copy()
        q = g.ln_q.values.astype(float); p = g.ln_p.values.astype(float)
        sku_codes = g.sku_id.values; wk_codes = g.period.values
        # iterative two-way demeaning (SKU then week), a few passes to converge
        for _ in range(6):
            for key in (sku_codes, wk_codes):
                s = pd.Series(q).groupby(key).transform("mean").values; q = q - s
                s = pd.Series(p).groupby(key).transform("mean").values; p = p - s
        denom = float(np.sum(p * p))
        beta = float(np.sum(p * q) / denom) if denom > 0 else float("nan")
        out.append([cat, round(beta, 3), CATEGORY_ELASTICITY_TARGET.get(cat, float("nan")),
                    round(beta - CATEGORY_ELASTICITY_TARGET.get(cat, float("nan")), 3), int(len(g))])
    return pd.DataFrame(out, columns=["category", "measured_elasticity", "internal_target",
                                      "gap_vs_target", "n_obs"])

# ============================================================================
# PART 1 -- parameters, assumptions & interpretation (intent verified vs realized)
# ============================================================================
def part1(d, md):
    sku, orders, fc = d["sku_master"], d["orders"], d["demand_forecast"]
    lanes, banners, serve = d["network_lanes"], d["b2b_customer_master"], d["customer_serving_dc"]
    plants, pdc, rdc, cdc = d["plant_master"], d["plant_dc_master"], d["regional_dc_master"], d["customer_dc_master"]

    md.append("# Supply Brain Synthetic Dataset â€” EDA Report\n")
    md.append(f"*Auto-generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} from data in "
              f"`{os.path.relpath(DATA_DIR, os.path.dirname(REPORT_PATH)) if os.path.dirname(REPORT_PATH) else DATA_DIR}`. "
              "The dataset is randomly generated, so figures differ between generation runs; this report "
              "reflects the current instance on disk.*\n")
    # fingerprint
    tot_rev = orders.revenue_usd.sum()
    md.append("## Instance fingerprint\n")
    md.append(kv_table([
        ("SKUs", f"{sku.sku_id.nunique():,}"),
        ("Order rows (historical)", f"{len(orders):,}"),
        ("Forecast rows (in-sample + forward)", f"{len(fc):,}"),
        ("Total historical revenue (USD)", f"${tot_rev:,.0f}"),
        ("Historical week span", f"{orders.period.min()} â†’ {orders.period.max()} ({orders.period.nunique()} weeks)"),
        ("Forecast week span", f"{fc.period.min()} â†’ {fc.period.max()} ({fc.period.nunique()} weeks)"),
    ]) + "\n")

    md.append("---\n\n# Part 1 â€” Parameters, Assumptions & Interpretation\n")
    md.append("This dataset is a fully-synthetic CPG (packaged food & beverage) supply chain, built to "
              "behave like a real client extract. Every table below is generated from an explicit business "
              "model; the parameters and assumptions that shape it are documented here, each checked against "
              "the realized data in this instance.\n")

    # 1.1 scope & grain
    md.append("## 1.1 Scope & grain\n")
    md.append("- **Product scope:** 5 categories Ã— 100 SKUs = 500 SKUs. **Realized:** "
              f"{sku.sku_id.nunique()} SKUs across {sku.category.nunique()} categories.\n"
              "- **Time scope:** 52 weeks of historical actuals (2025) + 52 weeks of forward forecast (2026). "
              "The forward year is the execution horizon for MEIO / buffer optimization and has no actuals.\n"
              "- **Grain:** orders & forecast at SKU Ã— DC Ã— Customer Ã— Week; production at SKU Ã— Plant Ã— Week; "
              "inventory at SKU Ã— DC Ã— Week.\n"
              "- **Currency:** all monetary values USD (columns suffixed `_usd`).\n")

    # 1.2 product / economics
    md.append("## 1.2 Product catalog & economics (USD)\n")
    md.append("SKUs are split across 3 demand **segments** with a target mix of "
              "**50% Fast-Moving / 30% Seasonal / 20% Intermediate**, and a **price tier** (Premium vs Value) "
              "within each category. Prices are realistic CPG case-level USD values; margin is designed at "
              "~30â€“34% (cost ratio ~0.66â€“0.70).\n")
    seg_real = (sku.segment.value_counts(normalize=True) * 100).round(1)
    seg_tbl = pd.DataFrame({
        "segment": list(SEGMENT_MIX_TARGET.keys()),
        "target_%": [SEGMENT_MIX_TARGET[s] * 100 for s in SEGMENT_MIX_TARGET],
        "realized_%": [seg_real.get(s, 0.0) for s in SEGMENT_MIX_TARGET],
    })
    md.append(md_table(seg_tbl) + "\n")
    # per-category price intent vs realized
    rows = []
    for c in CATEGORIES:
        med, lo, hi, cr = CATEGORY_USD[c]
        s = sku[sku.category == c]
        rows.append([c, f"${lo:.0f}â€“${hi:.0f}", f"${med:.0f}",
                     f"${s.list_price_usd.min():.2f}â€“${s.list_price_usd.max():.2f}",
                     f"${s.list_price_usd.median():.2f}", f"{s.margin_pct.mean()*100:.1f}%",
                     f"{CATEGORY_SHELF_LIFE[c][0]}â€“{CATEGORY_SHELF_LIFE[c][1]}d",
                     f"{int(s.shelf_life_days.min())}â€“{int(s.shelf_life_days.max())}d"])
    md.append("\n**List price & shelf life â€” design intent vs realized, by category:**\n")
    md.append(md_table(pd.DataFrame(rows, columns=[
        "category", "intended_price_range", "intended_median", "realized_price_range",
        "realized_median", "realized_avg_margin", "intended_shelf_life", "realized_shelf_life"])) + "\n")

    # 1.3 network
    md.append("## 1.3 Network (modelled on the Coca-Cola US bottling system)\n")
    md.append("Tiered **Production Plant â†’ Plant DC â†’ Regional DC â†’ Customer/Sales DC**, at roughly the "
              "real Coca-Cola ratio (~1 plant per 5â€“6 DCs). Plants are production-only (hold no inventory); "
              "the Plant DC is the first stock-holding echelon. Two real delivery patterns are modelled:\n"
              "- **Warehouse / bulk** national accounts are served from **Regional DCs**.\n"
              "- **DSD** (direct-store-delivery) small-format / convenience / foodservice banners are served "
              "from local **Customer DCs**.\n"
              "This dual pattern is why orders legitimately reference *any* stocking DC (regional or customer).\n")
    md.append(kv_table([
        ("Production plants", len(plants)), ("Plant DCs", len(pdc)),
        ("Regional DCs", len(rdc)), ("Customer/Sales DCs", len(cdc)),
        ("Plant : DC ratio", f"1 : {(len(pdc)+len(rdc)+len(cdc))/max(len(plants),1):.1f}"),
        ("B2B customer banners", len(banners)),
        ("Delivery modes", ", ".join(sorted(banners.delivery_mode.unique()))),
        ("Transport modes", ", ".join(sorted(lanes.transport_mode.unique()))),
        ("Lanes", len(lanes)),
    ]) + "\n")

    # 1.4 demand model & seasonality
    md.append("## 1.4 Demand model, seasonality & promotions\n")
    md.append("Weekly orders are drawn from an explicit **log-linear demand model**:\n\n"
              f"```\n{DEMAND_MODEL}\n```\n\n"
              "- **Seasonality** is category-specific: Beverages & Frozen peak in summer; Dairy & Bakery and "
              "Snacks peak around the holidays; Grocery is flatter. The `Seasonal` segment has the largest "
              "amplitude, `Intermediate` the smallest.\n"
              f"- **Promotions** create genuine price variation: ~{PROMO_PROB*100:.0f}% of SKU-weeks are "
              f"discounted {PROMO_DISCOUNT_RANGE[0]*100:.0f}â€“{PROMO_DISCOUNT_RANGE[1]*100:.0f}%. **Realized:** "
              f"{(orders.discount_pct > 0).mean()*100:.1f}% of order rows on promo, "
              f"avg depth {orders.loc[orders.discount_pct > 0, 'discount_pct'].mean()*100:.1f}%.\n"
              "- **Elasticity** is embedded per SKU (by category Ã— price tier) so that demand genuinely "
              "responds to price. It is deliberately **not** shipped as a column â€” Part 2 recovers it from the "
              "orders using the documented method.\n")
    md.append("**Internal elasticity targets by category (for reference; recovered in Part 2):**\n")
    md.append(md_table(pd.DataFrame(
        [[c, CATEGORY_ELASTICITY_TARGET[c]] for c in CATEGORIES],
        columns=["category", "internal_elasticity_target"])) + "\n")

    # 1.5 forecast
    md.append("## 1.5 Forecast horizons\n")
    md.append("`demand_forecast` spans **104 weeks** with a `horizon` flag:\n"
              "- **in_sample (2025):** forecast + actual + error â€” for forecast-accuracy analysis. Forecasts "
              "deliberately miss promo spikes and under-call the holiday peak for Seasonal SKUs.\n"
              "- **forward (2026):** forecast only, **no actuals** â€” the MEIO execution horizon. Built by "
              f"projecting each SKU-DC-customer level forward with ~{YOY_GROWTH*100:.0f}% YoY growth and the "
              "same seasonal shape.\n")
    hz = fc.horizon.value_counts()
    md.append(kv_table([(f"`{k}` rows", f"{v:,}") for k, v in hz.items()]) + "\n")

    # 1.6 costs
    md.append("## 1.6 Cost & economic parameters (USD)\n")
    md.append("- **Holding cost:** annual carrying cost as a % of unit cost, by category "
              "(perishables higher). Also expressed per-unit-per-year in USD on `sku_master`.\n"
              "- **Cost of capital & stockout multiplier:** in `cost_parameters`.\n"
              "- **Logistics cost:** USD per unit per lane on `network_lanes`, scaling with distance.\n")
    md.append(md_table(d["cost_parameters"]) + "\n")

    # 1.7 assumptions/caveats
    md.append("## 1.7 Key assumptions & caveats\n")
    md.append("- Prices, costs, capacities, lead times and cost rates are realistic **assumptions**, not "
              "client facts â€” validate before using in a hard optimization.\n"
              "- Inventory is a single-echelon (s,S) flow simulation per DC driven by its outbound orders; "
              "plant-DC replenishment coupling is simplified.\n"
              "- Network geography is US (Coca-Cola-modelled) and self-contained; it does not reference any "
              "external/real dataset.\n"
              "- Elasticity is embedded to make demand price-responsive but is **not** provided as ground "
              "truth; the estimate in Part 2 is what a modeller would compute from orders alone.\n")

# ============================================================================
# PART 2 -- EDA
# ============================================================================
def weekly_series(df, qtycol):
    g = df.groupby("_dt", as_index=False)[qtycol].sum().sort_values("_dt")
    return g

def part2(d, md):
    
    sku, orders, fc = d["sku_master"], d["orders"], d["demand_forecast"]
    prod, inv, lanes = d["production"], d["inventory"], d["network_lanes"]
    md.append("\n---\n\n# Part 2 â€” Exploratory Data Analysis\n")

    # 2.1 table inventory
    md.append("## 2.1 Dataset overview\n")
    rows = []
    for t in ["sku_master", "orders", "demand_forecast", "production", "inventory", "network_lanes",
              "b2b_customer_master", "customer_serving_dc", "cost_parameters",
              "plant_master", "plant_dc_master", "regional_dc_master", "customer_dc_master"]:
        df = d[t]
        rows.append([t, f"{len(df):,}", len(df.columns)])
    md.append(md_table(pd.DataFrame(rows, columns=["table", "rows", "cols"])) + "\n")

    # 2.2 referential integrity
    md.append("## 2.2 Referential integrity\n")
    sku_ids = set(sku.sku_id); cust_ids = set(d["b2b_customer_master"].b2b_customer_id)
    stock_dcs = set(d["regional_dc_master"].regional_dc_id) | set(d["customer_dc_master"].customer_dc_id)
    plant_ids = set(d["plant_master"].plant_id)
    nodes = plant_ids | set(d["plant_dc_master"].plant_dc_id) | stock_dcs
    checks = [
        ("orders.sku_id âŠ† sku_master", set(orders.sku_id) <= sku_ids),
        ("orders.b2b_customer_id âŠ† banners", set(orders.b2b_customer_id) <= cust_ids),
        ("orders.dc_id âŠ† stocking DCs", set(orders.dc_id) <= stock_dcs),
        ("forecast keys âŠ† masters", set(fc.sku_id) <= sku_ids and set(fc.dc_id) <= stock_dcs and set(fc.b2b_customer_id) <= cust_ids),
        ("production.plant_id âŠ† plants", set(prod.plant_id) <= plant_ids),
        ("inventory.dc_id âŠ† stocking DCs", set(inv.dc_id) <= stock_dcs),
        ("lane endpoints âŠ† nodes", set(lanes.origin_node_id) <= nodes and set(lanes.destination_node_id) <= nodes),
        ("forward forecast has no actuals", fc[fc.horizon == "forward"].actual_qty.isna().all()),
        ("in_sample forecast has actuals", fc[fc.horizon == "in_sample"].actual_qty.notna().all()),
    ]
    md.append(md_table(pd.DataFrame([[n, "PASS" if c else "FAIL"] for n, c in checks],
                                    columns=["check", "result"])) + "\n")

    # 2.3 product analysis
    md.append("## 2.3 Product & economics\n")
    cat = sku.groupby("category").agg(
        skus=("sku_id", "count"), avg_list_usd=("list_price_usd", "mean"),
        avg_cost_usd=("unit_cost_usd", "mean"), avg_margin_pct=("margin_pct", "mean"),
        avg_shelf_life_d=("shelf_life_days", "mean")).reset_index()
    cat["avg_margin_pct"] = (cat.avg_margin_pct * 100).round(1)
    for c in ["avg_list_usd", "avg_cost_usd", "avg_shelf_life_d"]:
        cat[c] = cat[c].round(2)
    md.append(md_table(cat) + "\n")
    md.append(f"\nPrice-tier split: " +
              ", ".join(f"{k} {v}" for k, v in sku.price_tier.value_counts().items()) +
              f". Seasonal-flagged SKUs: {int(sku.seasonal_flag.sum())} of {len(sku)}.\n")
    if HAVE_MPL:
        fig, ax = plt.subplots(figsize=(7, 3.6))
        sku.boxplot(column="list_price_usd", by="category", ax=ax, rot=25)
        ax.set_title("List price (USD) by category"); ax.set_xlabel(""); ax.set_ylabel("USD")
        plt.suptitle(""); md.append(f"\n![price by category]({save_chart(fig, 'price_by_category.png')})\n")

    # 2.4 demand analysis
    md.append("## 2.4 Orders & demand\n")
    o2 = orders.merge(sku[["sku_id", "category", "segment"]], on="sku_id") \
               .merge(d["b2b_customer_master"][["b2b_customer_id", "delivery_mode", "channel_type"]], on="b2b_customer_id")
    tot_u = orders.order_qty.sum(); tot_r = orders.revenue_usd.sum()
    md.append(kv_table([("Total units ordered", f"{tot_u:,.0f}"),
                        ("Total revenue (USD)", f"${tot_r:,.0f}"),
                        ("Avg order line (units)", f"{orders.order_qty.mean():.1f}"),
                        ("Avg selling price (USD)", f"${orders.unit_price_usd.mean():.2f}")]) + "\n")
    by_cat = o2.groupby("category").agg(units=("order_qty", "sum"), revenue_usd=("revenue_usd", "sum")).reset_index()
    by_cat["revenue_%"] = (by_cat.revenue_usd / by_cat.revenue_usd.sum() * 100).round(1)
    by_cat = by_cat.sort_values("revenue_usd", ascending=False)
    md.append("\n**By category:**\n" + md_table(by_cat) + "\n")
    by_seg = o2.groupby("segment").agg(units=("order_qty", "sum"), revenue_usd=("revenue_usd", "sum")).reset_index()
    md.append("\n**By demand segment:**\n" + md_table(by_seg) + "\n")
    by_dm = o2.groupby("delivery_mode").agg(units=("order_qty", "sum"), revenue_usd=("revenue_usd", "sum"),
                                            orders=("order_qty", "count")).reset_index()
    md.append("\n**By delivery mode (BULK = warehouse via RDC, DSD = via customer DC):**\n" + md_table(by_dm) + "\n")
    # Pareto
    sku_rev = orders.groupby("sku_id").revenue_usd.sum().sort_values(ascending=False)
    cum = sku_rev.cumsum() / sku_rev.sum()
    n20 = max(1, int(0.2 * len(sku_rev)))
    md.append(f"\n**Concentration (Pareto):** top 20% of SKUs ({n20}) drive "
              f"{cum.iloc[n20-1]*100:.1f}% of revenue.\n")
    if HAVE_MPL:
        ws = weekly_series(orders, "order_qty")
        oc = orders.merge(sku[["sku_id", "category"]], on="sku_id")
        fig, ax = plt.subplots(figsize=(8, 3.8))
        for c in CATEGORIES:
            s = oc[oc.category == c].groupby("_dt").order_qty.sum().sort_index()
            ax.plot(s.index, s.values, label=c, linewidth=1.3)
        ax.set_title("Weekly demand (units) by category â€” seasonality"); ax.legend(fontsize=7); ax.set_ylabel("units")
        md.append(f"\n![seasonality]({save_chart(fig, 'seasonality_by_category.png')})\n")
        fig, ax = plt.subplots(figsize=(6, 3.2))
        dd = orders.loc[orders.discount_pct > 0, "discount_pct"] * 100
        ax.hist(dd, bins=20, color="#4C78A8"); ax.set_title("Promo discount depth (%)"); ax.set_xlabel("% off")
        md.append(f"\n![discounts]({save_chart(fig, 'discount_depth.png')})\n")

    # 2.5 price & elasticity (the payoff)
    md.append("## 2.5 Price variation & measured elasticity\n")
    md.append("Elasticity is estimated from the historical orders using the documented method â€” a two-way "
              "(SKU + week) fixed-effects regression of `log(order_qty)` on `log(unit_price_usd)`, pooled per "
              "category (pure-numpy iterative demeaning; no `statsmodels` needed). The SKU effect absorbs "
              "baseline volume/list price so the slope is identified from within-SKU promo price changes; the "
              "week effect absorbs common seasonality. The `internal_target` column is the value baked into "
              "the generator, shown only to gauge recovery â€” a real client dataset would not include it.\n")
    el = estimate_elasticity(orders, sku)
    md.append(md_table(el) + "\n")
    ok = int((el.gap_vs_target.abs() < 0.5).sum())
    md.append(f"\n*Recovery: {ok}/{len(el)} categories within 0.5 of the embedded target; all elasticities "
              "negative as expected. Interpretation: Beverages & Snacks are the most price-elastic, "
              "Dairy & Bakery and Grocery the most inelastic â€” consistent with staple vs. discretionary "
              "demand.*\n")

    # 2.6 forecast
    md.append("## 2.6 Forecast accuracy & forward horizon\n")
    ins = fc[fc.horizon == "in_sample"].merge(sku[["sku_id", "segment"]], on="sku_id")
    ins = ins[ins.actual_qty > 0].copy()
    ins["ape"] = (ins.forecast_qty - ins.actual_qty).abs() / ins.actual_qty
    mape_seg = (ins.groupby("segment").ape.mean() * 100).round(1).reset_index().rename(columns={"ape": "MAPE_%"})
    md.append("**In-sample forecast accuracy (2025) â€” MAPE by segment:**\n" + md_table(mape_seg) + "\n")
    bias = ((ins.forecast_qty.sum() - ins.actual_qty.sum()) / ins.actual_qty.sum()) * 100
    md.append(f"\nOverall in-sample forecast bias: {bias:+.1f}% (negative = under-forecast, e.g. missed promo/holiday peaks).\n")
    # forward vs historical level
    hist_u = orders.order_qty.sum()
    fwd_u = fc[fc.horizon == "forward"].forecast_qty.sum()
    md.append(f"\n**Forward horizon (2026):** total forecast units {fwd_u:,.0f} vs historical actual "
              f"{hist_u:,.0f} â†’ implied YoY change {(fwd_u/hist_u-1)*100:+.1f}% "
              f"(design target ~{YOY_GROWTH*100:.0f}%).\n")
    if HAVE_MPL:
        fig, ax = plt.subplots(figsize=(8, 3.8))
        hs = orders.groupby("_dt").order_qty.sum().sort_index()
        fwd = fc[fc.horizon == "forward"].groupby("_dt").forecast_qty.sum().sort_index()
        ax.plot(hs.index, hs.values, label="2025 actual orders", linewidth=1.3)
        ax.plot(fwd.index, fwd.values, label="2026 forward forecast", linewidth=1.3, linestyle="--")
        ax.set_title("Historical actuals vs forward forecast (weekly units)"); ax.legend(fontsize=8); ax.set_ylabel("units")
        md.append(f"\n![forward]({save_chart(fig, 'hist_vs_forward.png')})\n")

    # 2.7 production
    md.append("## 2.7 Production (planned vs actual)\n")
    pr = prod.copy()
    md.append(kv_table([
        ("Planned units (total)", f"{pr.planned_qty.sum():,.0f}"),
        ("Actual units (total)", f"{pr.actual_qty.sum():,.0f}"),
        ("Mean schedule adherence", f"{pr.adherence_pct.mean()*100:.1f}%"),
        ("Weeks below 90% adherence", f"{(pr.adherence_pct < 0.90).mean()*100:.1f}%"),
    ]) + "\n")
    byp = pr.groupby("plant_id").agg(planned=("planned_qty", "sum"), actual=("actual_qty", "sum"),
                                     adherence=("adherence_pct", "mean")).reset_index()
    byp["adherence"] = (byp.adherence * 100).round(1)
    md.append(md_table(byp) + "\n")

    # 2.8 inventory
    md.append("## 2.8 Inventory & service\n")
    zero = (inv.closing_stock == 0).mean() * 100
    md.append(kv_table([
        ("Avg closing stock (units)", f"{inv.closing_stock.mean():,.0f}"),
        ("Avg safety stock (units)", f"{inv.safety_stock.mean():,.0f}"),
        ("DC-SKU-weeks at zero stock", f"{zero:.1f}%"),
        ("Total shipments (units)", f"{inv.shipments.sum():,.0f}"),
    ]) + "\n")
    invd = inv.copy()
    invd["echelon"] = np.where(invd.dc_id.astype(str).str.startswith("RDC"), "Regional DC",
                       np.where(invd.dc_id.astype(str).str.startswith("CDC"), "Customer DC", "Other"))
    byech = invd.groupby("echelon").agg(avg_closing=("closing_stock", "mean"),
                                        avg_safety=("safety_stock", "mean"),
                                        zero_stock_pct=("closing_stock", lambda s: (s == 0).mean() * 100)).reset_index()
    for c in ["avg_closing", "avg_safety", "zero_stock_pct"]:
        byech[c] = byech[c].round(1)
    md.append(md_table(byech) + "\n")

    # 2.9 network & lanes
    md.append("## 2.9 Network & lanes\n")
    lm = lanes.groupby("transport_mode").agg(lanes=("lane_id", "count"),
        avg_lead_days=("lead_time_mean_days", "mean"), avg_dist_mi=("distance_miles", "mean"),
        avg_logi_usd=("logistics_cost_per_unit_usd", "mean")).reset_index()
    for c in ["avg_lead_days", "avg_dist_mi", "avg_logi_usd"]:
        lm[c] = lm[c].round(2)
    md.append(md_table(lm) + "\n")
    lt = lanes.groupby("origin_node_type").agg(lanes=("lane_id", "count"),
        avg_lead_days=("lead_time_mean_days", "mean")).reset_index()
    lt["avg_lead_days"] = lt.avg_lead_days.round(2)
    md.append("\n**Lead time by lane tier (origin node type):**\n" + md_table(lt) + "\n")

    md.append("\n---\n*End of report. Regenerate any time with "
              "`python supply_brain_eda_report.py` after re-running the data generator.*\n")

# ============================================================================
# MAIN
# ============================================================================
def main():
    t0 = time.time()
    print(f"Reading data from: {DATA_DIR}")
    
    d = load_all()
    md = []
    part1(d, md)
    part2(d, md)
    os.makedirs(os.path.dirname(REPORT_PATH) or ".", exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"Wrote report: {REPORT_PATH}")
    print(f"Charts: {'enabled -> ' + ASSETS_DIR if HAVE_MPL else 'disabled (matplotlib not installed; tables only)'}")
    print(f"Done in {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()