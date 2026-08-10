"""
Supply Brain -- Synthetic Data Generator v3 (single-file, standalone, no LLM, all costs in USD)
================================================================================================
Generates a fully-synthetic, referentially-consistent CPG supply-chain dataset that behaves like
a real client extract, PLUS a forward planning-forecast horizon used to execute MEIO and the other
Supply Brain features. Running this one file produces every CSV table AND the documentation
(DATASET_DOCUMENTATION.md, which includes the price-elasticity calculation methodology).

WHAT'S NEW IN v3 (vs v2)
------------------------
  * All monetary values are in USD, recalibrated to realistic CPG case-level prices. Money columns
    are suffixed `_usd`. (This supersedes v2's hybrid seed magnitudes, which were not USD.)
  * demand_forecast spans TWO years = 104 weeks:
        - 2025 (in-sample):  forecast_qty + actual_qty + forecast_error_pct   (horizon='in_sample')
        - 2026 (forward):    forecast_qty only, NO actuals                    (horizon='forward')
    The 2026 forward year is the execution horizon for MEIO / safety-stock / buffer optimization.
  * NO elasticity "answer key": the hidden true_elasticity column and the measured-elasticity output
    from v2 are removed, so the dataset looks like real client data. Orders still carry genuine
    weekly promo price variation, so elasticity IS computable from history -- see the
    "PRICE ELASTICITY" methodology written into DATASET_DOCUMENTATION.md (documented, not built).
  * Referential integrity is validated across every table (all FKs resolve; forward forecast reuses
    the exact (SKU, DC, customer) keys seen in history).

SETUP / RUN
-----------
    pip install pandas numpy
    python supply_brain_generator_v3.py        # writes ./output/*.csv + DATASET_DOCUMENTATION.md, ~15s
Override output location with SUPPLY_BRAIN_OUTPUT. Fixed RNG seeds => identical output every run.
Fully self-contained: needs no external input files.

TABLES (all in OUTPUT_DIR)
--------------------------
  Masters : sku_master, plant_master, plant_dc_master, regional_dc_master, customer_dc_master,
            network_lanes, b2b_customer_master, customer_serving_dc, cost_parameters
  Facts   : orders                (52wk 2025 actuals; SKU x DC x Customer x Week)
            demand_forecast        (104wk; 2025 in-sample w/ actuals + 2026 forward w/o actuals)
            production            (52wk 2025; SKU x Plant x Week; planned+actual+adherence)
            inventory             (52wk 2025; SKU x DC x Week; (s,S) flow sim)
  Docs    : DATASET_DOCUMENTATION.md  (data dictionary, FK map, price-elasticity methodology)
"""

import os
import math
import time
import numpy as np
import pandas as pd

# ============================================================================
# CONFIG
# ============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE_DIR, "dataset")
OUTPUT_DIR = os.environ.get("SUPPLY_BRAIN_OUTPUT", DATASET_DIR)

REFERENCE_SOURCE_DIR = os.environ.get("SUPPLY_BRAIN_REF_SRC", os.path.dirname(BASE_DIR))
REFERENCE_FILES = [
    "Supply_Brain_Data_Schema.xlsx",
    "Supply_Brain_Data_Schema_and_Assumptions.md",
    "Accenture AMPBA Capstone Project Description Form 1.docx",
    "Capstone Project Charter AMPBA 2026S.docx",
]

HIST_WEEKS = 52
FWD_WEEKS = 52
TOTAL_WEEKS = HIST_WEEKS + FWD_WEEKS
WEEK1_MONDAY = pd.Timestamp("2025-01-06")
YOY_GROWTH = 0.05

CATEGORIES = ["Beverages", "Snacks & Confectionery", "Dairy & Bakery", "Frozen Foods", "Grocery"]
SKUS_PER_CATEGORY = 100

CATEGORY_USD = {
    "Beverages": (14.0, 6.0, 32.0, 0.68),
    "Snacks & Confectionery": (18.0, 8.0, 42.0, 0.66),
    "Dairy & Bakery": (22.0, 9.0, 48.0, 0.70),
    "Frozen Foods": (28.0, 12.0, 60.0, 0.69),
    "Grocery": (16.0, 6.0, 38.0, 0.67),
}
CATEGORY_ELASTICITY = {
    "Beverages": -2.00,
    "Snacks & Confectionery": -2.10,
    "Dairy & Bakery": -0.55,
    "Frozen Foods": -1.30,
    "Grocery": -0.60,
}
CATEGORY_SHELF_LIFE = {
    "Beverages": (120, 365),
    "Snacks & Confectionery": (90, 300),
    "Dairy & Bakery": (5, 21),
    "Frozen Foods": (180, 540),
    "Grocery": (180, 540),
}
CATEGORY_HOLDING_PCT = {
    "Beverages": 0.22,
    "Snacks & Confectionery": 0.21,
    "Dairy & Bakery": 0.34,
    "Frozen Foods": 0.33,
    "Grocery": 0.20,
}
SEGMENT_MIX = [("Fast-Moving", 0.50), ("Seasonal", 0.30), ("Intermediate", 0.20)]
SEGMENT_SEASON_AMP = {"Fast-Moving": 0.35, "Seasonal": 0.90, "Intermediate": 0.20}
SEGMENT_BASE_DEMAND = {"Fast-Moving": 900, "Seasonal": 500, "Intermediate": 220}
SEGMENT_FC_NOISE = {"Fast-Moving": 0.10, "Seasonal": 0.28, "Intermediate": 0.17}

os.makedirs(OUTPUT_DIR, exist_ok=True)


def woy(idx):
    return ((idx - 1) % HIST_WEEKS) + 1


def year_of(idx):
    return 2025 if idx <= HIST_WEEKS else 2026


def period_label(idx):
    return f"{year_of(idx)}-W{woy(idx):02d}"


def week_start(idx):
    return (WEEK1_MONDAY + pd.Timedelta(weeks=idx - 1)).strftime("%Y-%m-%d")


# ============================================================================
# STEP 1: masters (USD economics, Coke-scaled network, customers, lanes)
# ============================================================================
def step1_masters():
    RNG = np.random.default_rng(2025)
    OUT = OUTPUT_DIR

    w = np.arange(1, HIST_WEEKS + 1)

    def wave(peak, width=14):
        s = np.exp(-0.5 * ((w - peak) / width) ** 2)
        return s - s.mean()

    holiday = np.exp(-0.5 * ((w - 50) / 4.0) ** 2)
    holiday -= holiday.mean()
    SHAPE = {
        "Beverages": wave(28, 12) * 1.4 + holiday * 0.3,
        "Frozen Foods": wave(30, 13) * 1.0 + holiday * 0.4,
        "Snacks & Confectionery": wave(27, 15) * 0.6 + holiday * 1.0,
        "Dairy & Bakery": holiday * 1.2 + wave(14, 10) * 0.2,
        "Grocery": holiday * 0.5 + wave(26, 20) * 0.2,
    }
    SHAPE = {c: s / np.max(np.abs(s)) for c, s in SHAPE.items()}

    seg_pool = []
    for lab, frac in SEGMENT_MIX:
        seg_pool += [lab] * round(SKUS_PER_CATEGORY * frac)
    seg_pool = (seg_pool + ["Fast-Moving"] * SKUS_PER_CATEGORY)[:SKUS_PER_CATEGORY]

    sku_rows = []
    seasonal_index = {}
    sku_meta = {}
    n = 1
    for cat in CATEGORIES:
        med, pmin, pmax, cost_ratio = CATEGORY_USD[cat]
        segs = seg_pool.copy()
        RNG.shuffle(segs)
        for i in range(SKUS_PER_CATEGORY):
            sid = f"SKU{n:04d}"
            n += 1
            seg = segs[i]
            list_price = round(float(np.clip(med * RNG.lognormal(0, 0.40), pmin, pmax)), 2)
            unit_cost = round(list_price * cost_ratio * float(RNG.uniform(0.97, 1.03)), 2)
            margin = round(list_price - unit_cost, 2)
            tier = "Premium" if list_price >= med else "Value"
            elast = float(
                np.clip(
                    CATEGORY_ELASTICITY[cat] * (0.65 if tier == "Premium" else 1.30)
                    + RNG.normal(0, 0.08),
                    -4.0,
                    -0.1,
                )
            )
            slo, shi = CATEGORY_SHELF_LIFE[cat]
            shelf = int(RNG.integers(slo, shi + 1))
            base = SEGMENT_BASE_DEMAND[seg] * float(RNG.uniform(0.6, 1.6))
            idx = np.clip(1.0 + SEGMENT_SEASON_AMP[seg] * SHAPE[cat] + RNG.normal(0, 0.03, HIST_WEEKS), 0.15, None)
            seasonal_index[sid] = idx
            sku_meta[sid] = dict(base=base, list_price=list_price, elasticity=elast, category=cat, segment=seg)
            hold_year_usd = round(unit_cost * CATEGORY_HOLDING_PCT[cat], 2)
            sku_rows.append(
                [
                    sid,
                    f"{cat[:3].upper()}-{n-1:04d}",
                    cat,
                    f"{cat} Sub {chr(65 + i % 6)}",
                    f"Brand{1 + i % 8}",
                    list_price,
                    unit_cost,
                    margin,
                    round(margin / list_price, 4),
                    hold_year_usd,
                    shelf,
                    seg,
                    tier,
                    (seg == "Seasonal"),
                ]
            )

    pd.DataFrame(
        sku_rows,
        columns=[
            "sku_id",
            "sku_code",
            "category",
            "subcategory",
            "brand",
            "list_price_usd",
            "unit_cost_usd",
            "margin_usd",
            "margin_pct",
            "holding_cost_per_unit_year_usd",
            "shelf_life_days",
            "segment",
            "price_tier",
            "seasonal_flag",
        ],
    ).to_csv(f"{OUT}/sku_master.csv", index=False)

    pd.DataFrame(
        [[c, CATEGORY_HOLDING_PCT[c], round(RNG.uniform(0.08, 0.10), 3), round(RNG.uniform(1.5, 2.5), 2)] for c in CATEGORIES],
        columns=["category", "holding_cost_pct_annual", "cost_of_capital_pct", "stockout_cost_multiplier"],
    ).to_csv(f"{OUT}/cost_parameters.csv", index=False)

    CITY = {
        "Atlanta, GA": (33.75, -84.39, "Southeast"),
        "Dallas, TX": (32.78, -96.80, "Southwest"),
        "Chicago, IL": (41.88, -87.63, "Midwest"),
        "Los Angeles, CA": (34.05, -118.24, "West"),
        "Charlotte, NC": (35.23, -80.84, "Southeast"),
        "Houston, TX": (29.76, -95.37, "Southwest"),
        "Denver, CO": (39.74, -104.99, "West"),
        "Newark, NJ": (40.74, -74.17, "Northeast"),
        "Tampa, FL": (27.95, -82.46, "Southeast"),
        "Nashville, TN": (36.16, -86.78, "Southeast"),
        "Kansas City, MO": (39.10, -94.58, "Midwest"),
        "Phoenix, AZ": (33.45, -112.07, "West"),
        "Boston, MA": (42.36, -71.06, "Northeast"),
        "Minneapolis, MN": (44.98, -93.27, "Midwest"),
        "Seattle, WA": (47.61, -122.33, "West"),
        "Miami, FL": (25.76, -80.19, "Southeast"),
        "Philadelphia, PA": (39.95, -75.17, "Northeast"),
        "St. Louis, MO": (38.63, -90.20, "Midwest"),
    }

    def sp(c):
        return c.split(",")[0], c.split(",")[1]

    def hav(a, b):
        (la1, lo1, _), (la2, lo2, _) = CITY[a], CITY[b]
        R = 3958.8
        p1, p2 = math.radians(la1), math.radians(la2)
        dphi = math.radians(la2 - la1)
        dl = math.radians(lo2 - lo1)
        h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))

    plants = [
        ("PLANT01", "Atlanta Production Center", "Atlanta, GA"),
        ("PLANT02", "Dallas Production Center", "Dallas, TX"),
        ("PLANT03", "Chicago Production Center", "Chicago, IL"),
        ("PLANT04", "Los Angeles Production Center", "Los Angeles, CA"),
    ]
    pd.DataFrame(
        [[p, nm, *sp(c), int(RNG.integers(2_000_000, 4_000_000)), "TRUE"] for p, nm, c in plants],
        columns=["plant_id", "plant_name", "city", "state", "production_capacity_units_per_week", "no_inventory_flag"],
    ).to_csv(f"{OUT}/plant_master.csv", index=False)

    plant_dcs = [
        ("PDC01", "PLANT01", "Atlanta, GA"),
        ("PDC02", "PLANT02", "Dallas, TX"),
        ("PDC03", "PLANT03", "Chicago, IL"),
        ("PDC04", "PLANT04", "Los Angeles, CA"),
    ]
    pd.DataFrame(
        [[d, p, *sp(c), int(RNG.integers(400_000, 900_000))] for d, p, c in plant_dcs],
        columns=["plant_dc_id", "plant_id", "city", "state", "storage_capacity_units"],
    ).to_csv(f"{OUT}/plant_dc_master.csv", index=False)

    regional_dcs = [
        ("RDC01", "Southeast RDC", "Charlotte, NC", "Southeast"),
        ("RDC02", "Southwest RDC", "Houston, TX", "Southwest"),
        ("RDC03", "Midwest RDC", "Kansas City, MO", "Midwest"),
        ("RDC04", "West RDC", "Denver, CO", "West"),
        ("RDC05", "Northeast RDC", "Newark, NJ", "Northeast"),
        ("RDC06", "Florida RDC", "Tampa, FL", "Southeast"),
    ]
    pd.DataFrame(
        [[d, nm, *sp(c), reg, int(RNG.integers(250_000, 600_000))] for d, nm, c, reg in regional_dcs],
        columns=["regional_dc_id", "dc_name", "city", "state", "region", "storage_capacity_units"],
    ).to_csv(f"{OUT}/regional_dc_master.csv", index=False)

    customer_dcs = [
        ("CDC01", "Nashville Sales DC", "Nashville, TN", "Southeast"),
        ("CDC02", "Miami Sales DC", "Miami, FL", "Southeast"),
        ("CDC03", "Phoenix Sales DC", "Phoenix, AZ", "West"),
        ("CDC04", "Seattle Sales DC", "Seattle, WA", "West"),
        ("CDC05", "Boston Sales DC", "Boston, MA", "Northeast"),
        ("CDC06", "Philadelphia Sales DC", "Philadelphia, PA", "Northeast"),
        ("CDC07", "Minneapolis Sales DC", "Minneapolis, MN", "Midwest"),
        ("CDC08", "St. Louis Sales DC", "St. Louis, MO", "Midwest"),
        ("CDC09", "Houston Sales DC", "Houston, TX", "Southwest"),
        ("CDC10", "Dallas Sales DC", "Dallas, TX", "Southwest"),
        ("CDC11", "Denver Sales DC", "Denver, CO", "West"),
        ("CDC12", "Atlanta Sales DC", "Atlanta, GA", "Southeast"),
    ]
    pd.DataFrame(
        [[d, nm, *sp(c), reg, int(RNG.integers(60_000, 160_000))] for d, nm, c, reg in customer_dcs],
        columns=["customer_dc_id", "dc_name", "city", "state", "region", "storage_capacity_units"],
    ).to_csv(f"{OUT}/customer_dc_master.csv", index=False)

    def lane_params(dist, rng):
        if dist < 25:
            mode, mean, cv = "Truck", 0.5, 0.15
        elif dist < 600:
            mode, mean, cv = "Truck", rng.uniform(2, 4), rng.uniform(0.2, 0.35)
        elif dist < 1200:
            mode, mean, cv = ("Train" if rng.random() < 0.5 else "Truck"), rng.uniform(4, 7), rng.uniform(0.3, 0.45)
        else:
            mode, mean, cv = "Train", rng.uniform(7, 13), rng.uniform(0.4, 0.6)
        logi = round(0.0018 * dist + rng.uniform(0.4, 1.2), 2)
        return mode, round(mean, 2), round(mean * cv, 2), logi, int(rng.integers(150_000, 500_000))

    lanes = []
    ln = 1

    def add(o, ot, oc, d, dt, dc):
        nonlocal ln
        dist = round(hav(oc, dc), 1)
        mode, mean, std, logi, cap = lane_params(dist, RNG)
        lanes.append([f"LANE{ln:03d}", o, ot, d, dt, mode, mean, std, dist, logi, cap])
        ln += 1

    for pid, _, c in plants:
        pdc = next(x for x in plant_dcs if x[1] == pid)
        add(pid, "PLANT", c, pdc[0], "PLANT_DC", pdc[2])
    for rid, _, rc, _ in regional_dcs:
        for pdc in sorted(plant_dcs, key=lambda x: hav(rc, x[2]))[:2]:
            add(pdc[0], "PLANT_DC", pdc[2], rid, "REGIONAL_DC", rc)
    for cid, _, cc, creg in customer_dcs:
        same = [r for r in regional_dcs if r[3] == creg] or regional_dcs
        rdc = min(same, key=lambda r: hav(cc, r[2]))
        add(rdc[0], "REGIONAL_DC", rdc[2], cid, "CUSTOMER_DC", cc)

    pd.DataFrame(
        lanes,
        columns=[
            "lane_id",
            "origin_node_id",
            "origin_node_type",
            "destination_node_id",
            "destination_node_type",
            "transport_mode",
            "lead_time_mean_days",
            "lead_time_std_days",
            "distance_miles",
            "logistics_cost_per_unit_usd",
            "capacity_units_per_week",
        ],
    ).to_csv(f"{OUT}/network_lanes.csv", index=False)

    banners = [
        ("BNR01", "MegaMart Stores", "RT", "BULK", 6.0),
        ("BNR02", "ValueClub Warehouse", "RT", "BULK", 4.5),
        ("BNR03", "GreenGrocer National", "RT", "BULK", 4.0),
        ("BNR04", "BullsEye Retail", "RT", "BULK", 3.2),
        ("BNR05", "QuickStop Convenience", "Brick-and-Mortar", "DSD", 1.3),
        ("BNR06", "SunBelt Grocers", "RT", "DSD", 1.6),
        ("BNR07", "Coastal Marts", "Brick-and-Mortar", "DSD", 1.1),
        ("BNR08", "MetroPetro C-Stores", "Brick-and-Mortar", "DSD", 0.9),
        ("BNR09", "CampusDining Foodservice", "Foodservice", "DSD", 1.2),
        ("BNR10", "FreshFoods Regional", "RT", "DSD", 1.5),
    ]
    pd.DataFrame(
        [[b, nm, ch, dm] for b, nm, ch, dm, _ in banners],
        columns=["b2b_customer_id", "customer_name", "channel_type", "delivery_mode"],
    ).to_csv(f"{OUT}/b2b_customer_master.csv", index=False)

    rdc_ids = [r[0] for r in regional_dcs]
    cdc_ids = [c[0] for c in customer_dcs]
    serve_rows = []
    cust_dcs = {}
    for b, nm, ch, dm, scale in banners:
        pool = rdc_ids if dm == "BULK" else cdc_ids
        k = int(RNG.integers(2, 4))
        chosen = list(RNG.choice(pool, size=k, replace=False))
        cust_dcs[b] = chosen
        for dcid, sh in zip(chosen, RNG.dirichlet(np.ones(k))):
            serve_rows.append([b, dcid, "REGIONAL_DC" if dm == "BULK" else "CUSTOMER_DC", round(float(sh), 4)])
    pd.DataFrame(serve_rows, columns=["b2b_customer_id", "dc_id", "dc_type", "demand_share"]).to_csv(f"{OUT}/customer_serving_dc.csv", index=False)

    print(f"  SKUs 500 (5x100) | Plants 4 PlantDCs 4 RDCs 6 CDCs 12 | Lanes {len(lanes)} | Banners 10 | USD")
    return dict(sku_meta=sku_meta, seasonal_index=seasonal_index, cust_dcs=cust_dcs, cust_scale={b: s for b, _, _, _, s in banners}, cust_deliv={b: dm for b, _, _, dm, _ in banners})


# ============================================================================
# STEP 2: 52wk historical orders (demand model w/ internal elasticity + seasonality + promos)
# ============================================================================
def step2_orders(ctx):
    RNG = np.random.default_rng(4242)
    OUT = OUTPUT_DIR
    sku_meta, seasonal_index = ctx["sku_meta"], ctx["seasonal_index"]
    cust_dcs, cust_scale, cust_deliv = ctx["cust_dcs"], ctx["cust_scale"], ctx["cust_deliv"]
    serve = pd.read_csv(f"{OUT}/customer_serving_dc.csv")
    share_lookup = {(r.b2b_customer_id, r.dc_id): r.demand_share for r in serve.itertuples()}
    sku_ids = list(sku_meta.keys())

    promo_price, promo_disc = {}, {}
    for sid in sku_ids:
        lp = sku_meta[sid]["list_price"]
        disc = np.zeros(HIST_WEEKS)
        on = RNG.random(HIST_WEEKS) < 0.30
        disc[on] = RNG.uniform(0.05, 0.35, on.sum())
        promo_disc[sid] = disc
        promo_price[sid] = lp * (1 - disc)

    carries = {}
    for b in cust_dcs:
        frac = 0.85 if cust_deliv[b] == "BULK" else 0.55
        carries[b] = set(RNG.choice(sku_ids, size=int(len(sku_ids) * frac), replace=False).tolist())

    labels = [period_label(x) for x in range(1, HIST_WEEKS + 1)]
    dates = [week_start(x) for x in range(1, HIST_WEEKS + 1)]
    rows = []
    for b, dcs in cust_dcs.items():
        cscale = cust_scale[b]
        for sid in carries[b]:
            m = sku_meta[sid]
            lp = m["list_price"]
            elast = m["elasticity"]
            si = seasonal_index[sid]
            price = promo_price[sid]
            disc = promo_disc[sid]
            ratio = price / lp
            for dcid in dcs:
                base = m["base"] * cscale * share_lookup[(b, dcid)]
                mu = base * si * (ratio ** elast)
                qty = np.maximum(0, np.round(mu * RNG.lognormal(0, 0.15, HIST_WEEKS))).astype(int)
                for wi in np.nonzero(qty > 0)[0]:
                    q = int(qty[wi])
                    pr = round(float(price[wi]), 2)
                    rows.append((labels[wi], dates[wi], sid, dcid, b, q, pr, round(lp, 2), round(float(disc[wi]), 3), round(q * pr, 2)))

    orders = pd.DataFrame(
        rows,
        columns=[
            "period",
            "week_start_date",
            "sku_id",
            "dc_id",
            "b2b_customer_id",
            "order_qty",
            "unit_price_usd",
            "list_price_usd",
            "discount_pct",
            "revenue_usd",
        ],
    )
    orders.to_csv(f"{OUT}/orders.csv", index=False)
    print(f"  orders rows: {len(orders):,} | promo share {(orders.discount_pct > 0).mean():.0%} | 52 wks 2025")


# ============================================================================
# STEP 3: demand_forecast (104 wks: 2025 in-sample w/ actuals + 2026 forward w/o actuals),
#         production (52wk), inventory (52wk)
# ============================================================================
def step3_forecast_production_inventory(ctx):
    RNG = np.random.default_rng(909)
    OUT = OUTPUT_DIR
    sku_meta, seasonal_index = ctx["sku_meta"], ctx["seasonal_index"]
    orders = pd.read_csv(f"{OUT}/orders.csv")
    seg_of = {s: m["segment"] for s, m in sku_meta.items()}

    idx_of = {period_label(i): i for i in range(1, HIST_WEEKS + 1)}
    fc = orders[["period", "week_start_date", "sku_id", "dc_id", "b2b_customer_id", "order_qty"]].copy()
    fc.rename(columns={"order_qty": "actual_qty"}, inplace=True)
    seg_arr = fc.sku_id.map(seg_of).values
    wk_arr = fc.period.map(idx_of).values
    sd = np.array([SEGMENT_FC_NOISE[s] for s in seg_arr])
    bias = np.where(np.isin(wk_arr, [48, 49, 50, 51, 52]) & (seg_arr == "Seasonal"), -0.15, 0.0)
    fc["forecast_qty"] = np.maximum(0, np.round(fc.actual_qty.values * (1 + RNG.normal(bias, sd)))).astype(int)
    fc["forecast_error_pct"] = ((fc.forecast_qty - fc.actual_qty) / fc.actual_qty.replace(0, np.nan)).round(4)
    fc["horizon"] = "in_sample"

    combos = orders.groupby(["sku_id", "dc_id", "b2b_customer_id"], as_index=False).order_qty.sum()
    combos["wk_level"] = combos.order_qty / HIST_WEEKS
    fwd_rows = []
    seas_mean = {s: seasonal_index[s].mean() for s in seasonal_index}
    for r in combos.itertuples():
        si = seasonal_index[r.sku_id]
        sm = seas_mean[r.sku_id]
        lvl = r.wk_level * (1 + YOY_GROWTH)
        noise = RNG.normal(0, SEGMENT_FC_NOISE[seg_of[r.sku_id]], HIST_WEEKS)
        qf = np.maximum(0, np.round(lvl * (si / sm) * (1 + noise))).astype(int)
        for j in range(HIST_WEEKS):
            gi = HIST_WEEKS + j + 1
            fwd_rows.append((period_label(gi), week_start(gi), r.sku_id, r.dc_id, r.b2b_customer_id, int(qf[j])))

    fwd = pd.DataFrame(fwd_rows, columns=["period", "week_start_date", "sku_id", "dc_id", "b2b_customer_id", "forecast_qty"])
    fwd["actual_qty"] = np.nan
    fwd["forecast_error_pct"] = np.nan
    fwd["horizon"] = "forward"

    cols = ["period", "week_start_date", "horizon", "sku_id", "dc_id", "b2b_customer_id", "forecast_qty", "actual_qty", "forecast_error_pct"]
    fc_out = fc[cols].copy()
    fc_out["actual_qty"] = fc_out["actual_qty"].astype(float)
    fc_out["forecast_error_pct"] = fc_out["forecast_error_pct"].astype(float)
    pd.concat([fc_out, fwd[cols]], ignore_index=True).to_csv(f"{OUT}/demand_forecast.csv", index=False)

    mape = fc.assign(ape=(fc.forecast_qty - fc.actual_qty).abs() / fc.actual_qty.replace(0, np.nan), seg=fc.sku_id.map(seg_of)).groupby("seg").ape.mean()
    print(f"  demand_forecast: in_sample {len(fc):,} + forward {len(fwd):,} = {len(fc)+len(fwd):,} rows (104 wks)")
    print("  in-sample MAPE by segment:", {k: round(v, 3) for k, v in mape.items()})

    plants = pd.read_csv(f"{OUT}/plant_master.csv").plant_id.tolist()
    sku_ids = list(sku_meta.keys())
    sku_plant = {s: plants[i % len(plants)] for i, s in enumerate(sku_ids)}
    nat = orders.groupby(["sku_id", "period"], as_index=False).order_qty.sum()
    nat["wk"] = nat.period.map(idx_of)
    nat["pw"] = (nat.wk - 1).clip(lower=1)
    planned = nat.groupby(["sku_id", "pw"], as_index=False).order_qty.sum().rename(columns={"pw": "wk", "order_qty": "planned_qty"})
    planned["plant_id"] = planned.sku_id.map(sku_plant)
    adh = np.where(
        RNG.random(len(planned)) < 0.90,
        np.clip(RNG.normal(0.945, 0.02, len(planned)), 0.85, 1.02),
        RNG.uniform(0.75, 0.90, len(planned)),
    )
    planned["adherence_pct"] = adh.round(3)
    planned["actual_qty"] = (planned.planned_qty * planned.adherence_pct).round().astype(int)
    planned["period"] = planned.wk.map(period_label)
    planned["week_start_date"] = planned.wk.map(week_start)
    planned[["period", "week_start_date", "plant_id", "sku_id", "planned_qty", "actual_qty", "adherence_pct"]].to_csv(f"{OUT}/production.csv", index=False)
    print(f"  production rows: {len(planned):,} | mean adherence {planned.adherence_pct.mean():.3f}")

    dcd = orders.groupby(["dc_id", "sku_id", "period"], as_index=False).order_qty.sum()
    dcd["wk"] = dcd.period.map(idx_of)
    inv_rows = []
    for (dcid, sid), g in dcd.groupby(["dc_id", "sku_id"]):
        wk_qty = np.zeros(HIST_WEEKS + 1)
        for r in g.itertuples():
            wk_qty[int(r.wk)] = r.order_qty
        avg = wk_qty[1:].mean()
        std = wk_qty[1:].std()
        lt = 1
        safety = int(max(avg * 0.5, 1.65 * std * math.sqrt(lt)))
        reorder = int(avg * lt + safety)
        S = int(reorder + 2 * avg)
        on_hand = S
        pipe = {}
        for wk in range(1, HIST_WEEKS + 1):
            rec = pipe.pop(wk, 0)
            opening = on_hand + rec
            shipped = min(opening, wk_qty[wk])
            closing = opening - shipped
            pos = closing + sum(pipe.values())
            if pos <= reorder:
                pipe[wk + lt] = pipe.get(wk + lt, 0) + (S - pos)
            inv_rows.append((period_label(wk), week_start(wk), sid, dcid, int(opening - rec), int(rec), int(shipped), int(closing), int(safety), int(reorder)))
            on_hand = closing

    inv = pd.DataFrame(
        inv_rows,
        columns=["period", "week_start_date", "sku_id", "dc_id", "opening_stock", "receipts", "shipments", "closing_stock", "safety_stock", "reorder_point"],
    )
    inv.to_csv(f"{OUT}/inventory.csv", index=False)
    print(f"  inventory rows: {len(inv):,} | zero-closing weeks {(inv.closing_stock == 0).mean():.1%}")


# ============================================================================
# STEP 4: referential-integrity + sanity validation (behaves like real client data)
# ============================================================================
def validate():
    OUT = OUTPUT_DIR
    errs = []

    def chk(name, cond):
        print(f"[{'OK' if cond else 'FAIL'}] {name}")
        errs.append(name) if not cond else None

    sku = pd.read_csv(f"{OUT}/sku_master.csv")
    orders = pd.read_csv(f"{OUT}/orders.csv")
    fc = pd.read_csv(f"{OUT}/demand_forecast.csv")
    prod = pd.read_csv(f"{OUT}/production.csv")
    inv = pd.read_csv(f"{OUT}/inventory.csv")
    lanes = pd.read_csv(f"{OUT}/network_lanes.csv")
    serve = pd.read_csv(f"{OUT}/customer_serving_dc.csv")
    banners = pd.read_csv(f"{OUT}/b2b_customer_master.csv")
    plants = pd.read_csv(f"{OUT}/plant_master.csv")
    pdc = pd.read_csv(f"{OUT}/plant_dc_master.csv")
    rdc = pd.read_csv(f"{OUT}/regional_dc_master.csv")
    cdc = pd.read_csv(f"{OUT}/customer_dc_master.csv")

    sku_ids = set(sku.sku_id)
    cust_ids = set(banners.b2b_customer_id)
    stock_dcs = set(rdc.regional_dc_id) | set(cdc.customer_dc_id)
    all_nodes = set(plants.plant_id) | set(pdc.plant_dc_id) | stock_dcs

    chk("500 SKUs, 5 x 100", len(sku) == 500 and (sku.category.value_counts() == 100).all())
    chk("no elasticity answer-key column in sku_master", "true_elasticity" not in sku.columns)
    chk("no measured-elasticity file emitted", not os.path.exists(f"{OUT}/price_elasticity_measured.csv"))
    chk(
        "all money columns present in USD",
        {"list_price_usd", "unit_cost_usd", "margin_usd"}.issubset(sku.columns)
        and "logistics_cost_per_unit_usd" in lanes.columns
        and "revenue_usd" in orders.columns,
    )
    chk("all SKU margins positive (USD)", (sku.margin_usd > 0).all())
    chk("shelf life present", (sku.shelf_life_days > 0).all())
    chk("orders.sku_id -> sku_master", set(orders.sku_id) <= sku_ids)
    chk("orders.b2b_customer_id -> banners", set(orders.b2b_customer_id) <= cust_ids)
    chk("orders.dc_id -> stocking DCs", set(orders.dc_id) <= stock_dcs)
    chk("forecast keys -> sku/customer/dc", set(fc.sku_id) <= sku_ids and set(fc.b2b_customer_id) <= cust_ids and set(fc.dc_id) <= stock_dcs)
    chk("production.plant_id -> plants", set(prod.plant_id) <= set(plants.plant_id))
    chk("production.sku_id -> sku_master", set(prod.sku_id) <= sku_ids)
    chk("inventory.dc_id -> stocking DCs", set(inv.dc_id) <= stock_dcs)
    chk("lane endpoints -> known nodes", set(lanes.origin_node_id) <= all_nodes and set(lanes.destination_node_id) <= all_nodes)
    chk("serving map dc -> stocking DCs", set(serve.dc_id) <= stock_dcs)
    chk("forecast spans 104 weeks", fc.period.nunique() == 104)
    chk("in_sample horizon has actuals", fc[fc.horizon == "in_sample"].actual_qty.notna().all())
    chk("forward horizon has NO actuals", fc[fc.horizon == "forward"].actual_qty.isna().all())
    fwd_keys = set(map(tuple, fc[fc.horizon == "forward"][['sku_id', 'dc_id', 'b2b_customer_id']].drop_duplicates().values))
    ord_keys = set(map(tuple, orders[['sku_id', 'dc_id', 'b2b_customer_id']].drop_duplicates().values))
    chk("forward forecast reuses historical (sku,dc,customer) keys", fwd_keys <= ord_keys and len(fwd_keys) > 0)
    chk("transport modes Truck/Train only", set(lanes.transport_mode) <= {"Truck", "Train"})
    chk("inventory closing stock non-negative", (inv.closing_stock >= 0).all())

    print(f"\n{len(errs)} check(s) failed" if errs else "\nAll checks passed.")
    return len(errs) == 0


# ============================================================================
# STEP 5: write DATASET_DOCUMENTATION.md (data dictionary + FK map + elasticity METHOD)
# ============================================================================
def write_documentation():
    OUT = OUTPUT_DIR
    doc = r"""# Supply Brain Dataset -- Documentation (v3, all costs USD)

Fully-synthetic CPG supply-chain dataset generated by `supply_brain_generator_v3.py`. Designed to
behave like a real client extract: consistent keys across every table, realistic USD economics, a
52-week historical actuals period (2025) and a 52-week forward planning horizon (2026) with no
actuals -- the horizon used to execute MEIO and the other Supply Brain features.

## Entity / key map (referential integrity)

```
plant_master(plant_id) ─< plant_dc_master(plant_dc_id, plant_id)
                          network_lanes(origin_node_id, destination_node_id)  ── every endpoint is a
                          plant_id / plant_dc_id / regional_dc_id / customer_dc_id
regional_dc_master(regional_dc_id) ─═
customer_dc_master(customer_dc_id) ═╩═> "stocking DCs"
b2b_customer_master(b2b_customer_id) ─< customer_serving_dc(b2b_customer_id, dc_id)   dc_id ∈ stocking DCs
sku_master(sku_id) ──< orders / demand_forecast / production / inventory
orders(sku_id, dc_id, b2b_customer_id, period)               dc_id ∈ stocking DCs
demand_forecast(sku_id, dc_id, b2b_customer_id, period, horizon)   forward keys ⊇ historical order keys
production(sku_id, plant_id, period)
inventory(sku_id, dc_id, period)
```
All fact-table foreign keys are validated to resolve against their masters at generation time.

## Time & horizons

`period` is `YYYY-Www` (e.g. `2025-W01` .. `2025-W52`, then `2026-W01` .. `2026-W52`); `week_start_date`
is the Monday of that week. `orders`, `production`, and `inventory` cover the 52 historical weeks
(2025). `demand_forecast` covers 104 weeks with a `horizon` column:
  * `in_sample`  (2025): `forecast_qty`, `actual_qty`, `forecast_error_pct` all populated.
  * `forward`    (2026): `forecast_qty` only; `actual_qty` / `forecast_error_pct` are blank.
The `forward` rows are the execution horizon for MEIO / service-level / buffer optimization.

## Currency

All monetary fields are USD and suffixed `_usd`: `list_price_usd`, `unit_cost_usd`, `margin_usd`,
`holding_cost_per_unit_year_usd` (sku_master); `logistics_cost_per_unit_usd` (network_lanes);
`unit_price_usd`, `list_price_usd`, `revenue_usd` (orders). `holding_cost_pct_annual` and
`cost_of_capital_pct` in `cost_parameters` are rates (fractions), not currency.

## Tables (grain)

| Table | Grain | Notes |
|---|---|---|
| sku_master | SKU | 500 SKUs (100 x 5 categories); USD economics, shelf life, segment, seasonal flag |
| plant_master / plant_dc_master / regional_dc_master / customer_dc_master | node | Coca-Cola-modelled US network |
| network_lanes | lane | Truck/Train only; lead-time mean+std (days); USD logistics cost/unit |
| b2b_customer_master | banner | BULK (warehouse, served from RDCs) vs DSD (served from CDCs) |
| customer_serving_dc | banner x DC | which stocking DC serves each banner + demand share |
| cost_parameters | category | holding %, cost of capital %, stockout multiplier |
| orders | SKU x DC x Customer x Week | 52 wks 2025 actual sales, with weekly promo price variation |
| demand_forecast | SKU x DC x Customer x Week | 104 wks; horizon in_sample (w/ actuals) or forward (no actuals) |
| production | SKU x Plant x Week | 52 wks; planned + actual + adherence % |
| inventory | SKU x DC x Week | 52 wks; opening/receipts/shipments/closing/safety/reorder |

## PRICE ELASTICITY -- how to calculate it from the historical orders (METHOD, not shipped)

This dataset deliberately does NOT ship an elasticity column or a precomputed elasticity table --
just like a real client extract, you estimate it yourself from `orders`. It is estimable because
`orders` contains genuine within-SKU price variation: ~30% of SKU-weeks are promoted (5-35% off),
so each SKU is observed at several different prices.

Recommended method -- log-log regression with SKU (and ideally week) fixed effects:

    ln(order_qty)_{i,t} = alpha_i + gamma_t + beta * ln(unit_price_usd)_{i,t} + e

  * `alpha_i` (SKU fixed effect) absorbs each SKU's baseline volume and list price, so `beta` is
    identified purely from within-SKU price changes (i.e. promotions).
  * `gamma_t` (week fixed effect) absorbs seasonality/holidays common across SKUs, removing the main
    confounder (don't skip this: promo timing can correlate with seasonal demand).
  * `beta` is the own-price elasticity. Estimate per category (or per segment) by running the
    regression on that subset; pool SKUs to get a stable category elasticity.

Minimal reproducible snippet (statsmodels; pip install statsmodels):

    import pandas as pd, numpy as np, statsmodels.formula.api as smf
    o = pd.read_csv("orders.csv")
    s = pd.read_csv("sku_master.csv")[["sku_id","category"]]
    df = o.merge(s, on="sku_id")
    df = df[(df.order_qty>0) & (df.unit_price_usd>0)].copy()
    df["ln_q"] = np.log(df.order_qty)
    df["ln_p"] = np.log(df.unit_price_usd)
    for cat, g in df.groupby("category"):
        m = smf.ols("ln_q ~ ln_p + C(sku_id) + C(period)", data=g).fit()
        print(cat, "elasticity (beta on ln_p) =", round(m.params["ln_p"], 3))

Pure-numpy alternative (no statsmodels): within-SKU demean ln_q and ln_p, then
`beta = sum(dp*dq)/sum(dp*dp)` per category (add week demeaning too for the seasonality control).

Caveats: (1) you need within-SKU price variation -- SKUs never promoted can't be identified alone,
lean on the category pool; (2) always include the week control; (3) elasticity is negative and, for
these categories, roughly: Beverages/Snacks strongly elastic, Frozen mid, Dairy & Bakery/Grocery
inelastic -- a sanity band, not a target.
"""
    with open(f"{OUT}/DATASET_DOCUMENTATION.md", "w", encoding="utf-8") as f:
        f.write(doc)
    print("  wrote DATASET_DOCUMENTATION.md")


def bundle_reference_docs():
    """Copy project reference/governance docs into OUTPUT_DIR/reference/ so the folder is a single
    complete bundle. Guarded: skips (does not error) any file not found -> safe on a fresh machine."""
    import shutil

    ref_dir = os.path.join(OUTPUT_DIR, "reference")
    os.makedirs(ref_dir, exist_ok=True)
    copied, missing = [], []
    for fn in REFERENCE_FILES:
        src = os.path.join(REFERENCE_SOURCE_DIR, fn)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(ref_dir, fn))
            copied.append(fn)
        else:
            missing.append(fn)
    print(f"  bundled {len(copied)} reference doc(s) into ./reference/ (source: {REFERENCE_SOURCE_DIR})")
    for fn in copied:
        print(f"    + {fn}")
    if missing:
        print(f"  skipped {len(missing)} not found (fine if running standalone): {missing}")


# ============================================================================
# ORCHESTRATOR
# ============================================================================
def main():
    t0 = time.time()
    print("\n=== STEP 1: masters (USD economics, Coke-scaled network) ===")
    ctx = step1_masters()
    print("\n=== STEP 2: historical orders (2025) ===")
    step2_orders(ctx)
    print("\n=== STEP 3: forecast (104wk) + production + inventory ===")
    step3_forecast_production_inventory(ctx)
    print("\n=== STEP 4: referential-integrity validation ===")
    ok = validate()
    print("\n=== STEP 5: documentation ===")
    write_documentation()
    print("\n=== STEP 6: bundle reference docs (optional, guarded) ===")
    bundle_reference_docs()
    print(f"\nAll tables + docs written to: {OUTPUT_DIR}\nTotal runtime: {time.time() - t0:.1f}s")
    return ok


if __name__ == "__main__":
    main()
