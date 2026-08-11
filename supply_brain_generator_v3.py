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
OUTPUT_DIR = os.environ.get("SUPPLY_BRAIN_OUTPUT", os.path.join(BASE_DIR, "input datasets"))

# Optional reference/governance docs to co-locate with the data so the output folder is a single
# complete project bundle. Copied by bundle_reference_docs() IF present; silently skipped if not,
# so the script stays fully portable on any machine. Default source = the folder ABOVE this script
# (i.e. the project root when the script lives in synthetic_layer_v3/). Override with SUPPLY_BRAIN_REF_SRC.
REFERENCE_SOURCE_DIR = os.environ.get("SUPPLY_BRAIN_REF_SRC", os.path.dirname(BASE_DIR))
REFERENCE_FILES = [
    "Supply_Brain_Data_Schema.xlsx",                 # data schema (design reference)
    "Supply_Brain_Data_Schema_and_Assumptions.md",   # schema + benchmark assumptions
    "Accenture AMPBA Capstone Project Description Form 1.docx",  # sponsor PD form
    "Capstone Project Charter AMPBA 2026S.docx",      # project charter
]

HIST_WEEKS = 52                          # 2025 historical
FWD_WEEKS = 52                           # 2026 forward planning horizon
TOTAL_WEEKS = HIST_WEEKS + FWD_WEEKS     # 104
WEEK1_MONDAY = pd.Timestamp("2025-01-06")
YOY_GROWTH = 0.05                        # forward-year demand growth vs history

CATEGORIES = ["Beverages", "Snacks & Confectionery", "Dairy & Bakery", "Frozen Foods", "Grocery"]
SKUS_PER_CATEGORY = 100

# ---------------------------------------------------------------------------
# REAL-GROUNDED PARAMETERS -- derived from the external "Food and Beverage Sales" dataset
# (Product.xlsx + SalesData.xlsx), 2020 EXCLUDED (COVID spike stripped). Prices are PER-UNIT USD.
# The 5 synthetic categories are repriced/reseasonalized by mapping each to real product groups;
# each SKU inherits its group's per-unit price distribution and RAW empirical price elasticity.
# Seasonality uses the real calendar SHAPE from mature-year 2020 (Sep-Nov peak). See REALDATA_OPPORTUNITIES.md.
# ---------------------------------------------------------------------------
CATEGORY_GROUP_MAP = {
    "Beverages": ["Liquor", "Dry Wine", "Red Wine", "Imported Wine", "Energy Drink", "Tea"],
    "Snacks & Confectionery": ["Candy", "Popcorn", "Canned Sweet"],
    "Dairy & Bakery": ["Powdered Milk", "Wheat Flour", "Flour", "Yeasts"],
    "Grocery": ["Oil", "Sugar", "Spices", "Manioc Flour", "Cassava starch", "Ground Coffee", "Coffee in Capsules", "Tomato Sauce"],
    "Frozen Foods": ["Vegetable", "Canned Sweet"],   # no real frozen group -> Vegetable + Canned Sweet blend
}
# per real product group: n (assignment weight = real product count), per-unit price lognormal
# (mu, sigma of log USD) clipped to [lo, hi], and RAW empirical elasticity (kept verbatim incl.
# near-zero / positive / extreme values, per instruction -- "noise is good").
GROUP_PARAMS = {
    "Liquor": dict(n=100, mu=3.5086, sigma=0.8647, lo=8.50, hi=157.60, elast=-1.746),
    "Dry Wine": dict(n=50, mu=0.4399, sigma=0.2989, lo=1.33, hi=4.36, elast=-0.957),
    "Red Wine": dict(n=35, mu=1.6999, sigma=0.6526, lo=2.53, hi=20.86, elast=-0.129),
    "Imported Wine": dict(n=10, mu=3.9955, sigma=0.6571, lo=8.44, hi=85.55, elast=-1.630),
    "Energy Drink": dict(n=28, mu=4.5814, sigma=0.4102, lo=49.59, hi=155.34, elast=-0.128),
    "Tea": dict(n=2, mu=2.6004, sigma=0.0097, lo=13.34, hi=13.60, elast=2.742),
    "Candy": dict(n=10, mu=3.2788, sigma=0.4211, lo=13.90, hi=46.78, elast=-4.967),
    "Popcorn": dict(n=9, mu=1.0390, sigma=0.1695, lo=2.31, hi=4.07, elast=-3.605),
    "Canned Sweet": dict(n=7, mu=1.2689, sigma=0.3196, lo=1.65, hi=4.41, elast=-3.412),
    "Powdered Milk": dict(n=11, mu=1.5370, sigma=0.2992, lo=3.34, hi=11.10, elast=0.431),
    "Wheat Flour": dict(n=91, mu=1.4830, sigma=1.0644, lo=0.89, hi=67.90, elast=-1.230),
    "Flour": dict(n=12, mu=1.8092, sigma=0.6486, lo=0.94, hi=10.06, elast=-5.565),
    "Yeasts": dict(n=73, mu=0.5543, sigma=0.6050, lo=0.41, hi=4.81, elast=-3.019),
    "Oil": dict(n=101, mu=0.9643, sigma=1.0120, lo=0.33, hi=18.97, elast=-2.682),
    "Sugar": dict(n=34, mu=1.6391, sigma=0.4612, lo=1.95, hi=14.30, elast=-1.083),
    "Spices": dict(n=20, mu=2.1508, sigma=0.5927, lo=2.84, hi=24.01, elast=-1.722),
    "Manioc Flour": dict(n=26, mu=3.5293, sigma=0.4217, lo=17.11, hi=101.72, elast=-0.279),
    "Cassava starch": dict(n=19, mu=2.8260, sigma=0.1553, lo=11.19, hi=20.22, elast=-5.000),
    "Ground Coffee": dict(n=22, mu=3.4936, sigma=0.6269, lo=7.87, hi=126.42, elast=-1.580),
    "Coffee in Capsules": dict(n=14, mu=1.8247, sigma=0.5587, lo=2.47, hi=14.43, elast=-1.910),
    "Tomato Sauce": dict(n=6, mu=1.6958, sigma=0.4214, lo=3.45, hi=8.81, elast=-0.559),
    "Vegetable": dict(n=40, mu=1.9404, sigma=0.4511, lo=2.80, hi=17.38, elast=-4.488),
}
# per-category monthly seasonality (Jan..Dec, mean=1). Derived from the NORMALIZED SHAPE of 2020 --
# the only complete, mature year (2019 was a business ramp-up whose growth trend would otherwise be
# mis-read as a deep Q1 trough; normalizing removes the COVID volume spike, keeping only the pattern).
# Realistic peak/trough ~1.6-3.8x, gentle Dec->Jan step, peaks Sep-Nov.
CATEGORY_SEASONALITY_MONTHLY = {
    "Beverages": [0.901, 1.021, 0.928, 0.798, 0.995, 0.926, 0.985, 0.826, 1.190, 1.208, 1.284, 0.938],
    "Snacks & Confectionery": [0.710, 0.649, 0.998, 0.787, 0.920, 0.966, 0.960, 1.067, 1.220, 1.274, 1.362, 1.087],
    "Dairy & Bakery": [0.604, 0.514, 0.780, 0.650, 0.699, 0.724, 0.956, 1.250, 1.606, 1.585, 1.437, 1.194],
    "Grocery": [0.721, 0.536, 0.768, 0.789, 0.960, 0.925, 0.991, 1.223, 1.262, 1.383, 1.306, 1.135],
    "Frozen Foods": [0.491, 0.421, 0.844, 0.721, 0.891, 0.864, 0.987, 1.226, 1.392, 1.600, 1.430, 1.133],
}
# margin stays a modelled assumption (real data has no cost): unit_cost = price x cost_ratio
CATEGORY_COST_RATIO = {"Beverages": 0.68, "Snacks & Confectionery": 0.66, "Dairy & Bakery": 0.70,
                       "Frozen Foods": 0.69, "Grocery": 0.67}
CATEGORY_SHELF_LIFE = {
    "Beverages": (120, 365), "Snacks & Confectionery": (90, 300),
    "Dairy & Bakery": (5, 21), "Frozen Foods": (180, 540), "Grocery": (180, 540),
}
CATEGORY_HOLDING_PCT = {
    "Beverages": 0.22, "Snacks & Confectionery": 0.21, "Dairy & Bakery": 0.34,
    "Frozen Foods": 0.33, "Grocery": 0.20,
}
SEGMENT_MIX = [("Fast-Moving", 0.50), ("Seasonal", 0.30), ("Intermediate", 0.20)]
SEGMENT_SEASON_AMP = {"Fast-Moving": 1.0, "Seasonal": 1.35, "Intermediate": 0.6}  # scales the real seasonal deviation
# heavy-tailed SKU popularity -> real ~80/20 revenue concentration; segment factor keeps fast-movers
# larger; BASE_SCALE sets per-unit order-line magnitude. Tuned to real ~median line qty & ~80/20.
POP_SIGMA = 1.15
SEGMENT_VOL_FACTOR = {"Fast-Moving": 1.6, "Seasonal": 1.0, "Intermediate": 0.5}
BASE_SCALE = 6.0
# two-elasticity model: promo (deal) elasticity = base x PROMO_MULT, bounded to the literature promo band.
# Grounded in CPG research (promo elasticity ~1.5-2x regular; Bijmolt/van Heerde meta-analysis) -- the
# multiplier is a labelled assumption since the source data has no promo flag to estimate it directly.
PROMO_MULT = 1.75
# storability drives the post-promo forward-buy dip (pantry-loadable -> deeper dip; perishable -> ~none)
STORABILITY = {"Beverages": 0.9, "Snacks & Confectionery": 0.9, "Dairy & Bakery": 0.2,
               "Frozen Foods": 1.0, "Grocery": 0.9}
SEGMENT_FC_NOISE = {"Fast-Moving": 0.10, "Seasonal": 0.28, "Intermediate": 0.17}

os.makedirs(OUTPUT_DIR, exist_ok=True)


def woy(idx):                       # 1..104 -> week-of-year 1..52
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

    # seasonality shapes: zero-mean weekly wave per category (52 weeks)
    # real-calendar seasonality: calendar-month index (0..11) for each of the 52 historical weeks
    week_month = np.array([(WEEK1_MONDAY + pd.Timedelta(weeks=k)).month - 1 for k in range(HIST_WEEKS)])

    seg_pool = []
    for lab, frac in SEGMENT_MIX:
        seg_pool += [lab] * round(SKUS_PER_CATEGORY * frac)
    seg_pool = (seg_pool + ["Fast-Moving"] * SKUS_PER_CATEGORY)[:SKUS_PER_CATEGORY]

    sku_rows = []
    seasonal_index = {}          # sku_id -> np.array(52), mean ~1
    sku_meta = {}                # sku_id -> internal dict (incl. hidden elasticity)
    n = 1
    for cat in CATEGORIES:
        groups = CATEGORY_GROUP_MAP[cat]
        gweights = np.array([GROUP_PARAMS[g]["n"] for g in groups], dtype=float); gweights /= gweights.sum()
        monthly = np.array(CATEGORY_SEASONALITY_MONTHLY[cat])          # 12 monthly indices, mean ~1
        cost_ratio = CATEGORY_COST_RATIO[cat]
        segs = seg_pool.copy(); RNG.shuffle(segs)
        for i in range(SKUS_PER_CATEGORY):
            sid = f"SKU{n:04d}"; n += 1
            seg = segs[i]
            grp = str(RNG.choice(groups, p=gweights))                 # assign SKU to a real product group
            gp = GROUP_PARAMS[grp]
            list_price = round(float(np.clip(np.exp(RNG.normal(gp["mu"], gp["sigma"])), gp["lo"], gp["hi"])), 2)
            unit_cost = round(min(list_price * cost_ratio * float(RNG.uniform(0.97, 1.03)), list_price * 0.95), 2)
            margin = round(list_price - unit_cost, 2)
            tier = "Premium" if list_price >= math.exp(gp["mu"]) else "Value"
            elast = round(float(gp["elast"] + RNG.normal(0, 0.10)), 3)  # RAW per-group elasticity + noise (kept verbatim)
            slo, shi = CATEGORY_SHELF_LIFE[cat]
            shelf = int(RNG.integers(slo, shi + 1))
            pop = float(RNG.lognormal(0.0, POP_SIGMA)) * SEGMENT_VOL_FACTOR[seg]   # heavy tail -> ~80/20
            base = BASE_SCALE * pop
            dev = monthly[week_month] - 1.0                            # real-calendar seasonal deviation per week
            idx = np.clip(1.0 + SEGMENT_SEASON_AMP[seg] * dev + RNG.normal(0, 0.03, HIST_WEEKS), 0.10, None)
            seasonal_index[sid] = idx
            elast_promo = round(float(np.clip(elast * PROMO_MULT, -4.5, 3.0)), 3)  # deal elasticity (grounded band)
            sku_meta[sid] = dict(base=base, list_price=list_price, elast_base=elast, elast_promo=elast_promo,
                                 storability=STORABILITY[cat], category=cat, segment=seg)
            hold_year_usd = round(unit_cost * CATEGORY_HOLDING_PCT[cat], 2)
            sku_rows.append([sid, f"{cat[:3].upper()}-{n-1:04d}", cat, grp,
                             f"Brand{1+i%8}", list_price, unit_cost, margin,
                             round(margin/list_price, 4), hold_year_usd, shelf, seg, tier,
                             (seg == "Seasonal")])
    pd.DataFrame(sku_rows, columns=[
        "sku_id", "sku_code", "category", "subcategory", "brand",
        "list_price_usd", "unit_cost_usd", "margin_usd", "margin_pct",
        "holding_cost_per_unit_year_usd", "shelf_life_days", "segment", "price_tier",
        "seasonal_flag"]).to_csv(f"{OUT}/sku_master.csv", index=False)

    pd.DataFrame([[c, CATEGORY_HOLDING_PCT[c], round(RNG.uniform(0.08, 0.10), 3),
                   round(RNG.uniform(1.5, 2.5), 2)] for c in CATEGORIES],
                 columns=["category", "holding_cost_pct_annual", "cost_of_capital_pct",
                          "stockout_cost_multiplier"]).to_csv(f"{OUT}/cost_parameters.csv", index=False)

    # ---- Coca-Cola-scaled US network ----
    CITY = {
        "Atlanta, GA": (33.75, -84.39, "Southeast"), "Dallas, TX": (32.78, -96.80, "Southwest"),
        "Chicago, IL": (41.88, -87.63, "Midwest"), "Los Angeles, CA": (34.05, -118.24, "West"),
        "Charlotte, NC": (35.23, -80.84, "Southeast"), "Houston, TX": (29.76, -95.37, "Southwest"),
        "Denver, CO": (39.74, -104.99, "West"), "Newark, NJ": (40.74, -74.17, "Northeast"),
        "Tampa, FL": (27.95, -82.46, "Southeast"), "Nashville, TN": (36.16, -86.78, "Southeast"),
        "Kansas City, MO": (39.10, -94.58, "Midwest"), "Phoenix, AZ": (33.45, -112.07, "West"),
        "Boston, MA": (42.36, -71.06, "Northeast"), "Minneapolis, MN": (44.98, -93.27, "Midwest"),
        "Seattle, WA": (47.61, -122.33, "West"), "Miami, FL": (25.76, -80.19, "Southeast"),
        "Philadelphia, PA": (39.95, -75.17, "Northeast"), "St. Louis, MO": (38.63, -90.20, "Midwest"),
    }
    def sp(c): return c.split(", ")[0], c.split(", ")[1]
    def hav(a, b):
        (la1, lo1, _), (la2, lo2, _) = CITY[a], CITY[b]
        R = 3958.8; p1, p2 = math.radians(la1), math.radians(la2)
        dphi = math.radians(la2-la1); dl = math.radians(lo2-lo1)
        h = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return R*2*math.atan2(math.sqrt(h), math.sqrt(1-h))

    plants = [("PLANT01", "Atlanta Production Center", "Atlanta, GA"),
              ("PLANT02", "Dallas Production Center", "Dallas, TX"),
              ("PLANT03", "Chicago Production Center", "Chicago, IL"),
              ("PLANT04", "Los Angeles Production Center", "Los Angeles, CA")]
    pd.DataFrame([[p, nm, *sp(c), int(RNG.integers(2_000_000, 4_000_000)), "TRUE"] for p, nm, c in plants],
                 columns=["plant_id", "plant_name", "city", "state",
                          "production_capacity_units_per_week", "no_inventory_flag"]).to_csv(f"{OUT}/plant_master.csv", index=False)
    plant_dcs = [("PDC01", "PLANT01", "Atlanta, GA"), ("PDC02", "PLANT02", "Dallas, TX"),
                 ("PDC03", "PLANT03", "Chicago, IL"), ("PDC04", "PLANT04", "Los Angeles, CA")]
    pd.DataFrame([[d, p, *sp(c), int(RNG.integers(400_000, 900_000))] for d, p, c in plant_dcs],
                 columns=["plant_dc_id", "plant_id", "city", "state", "storage_capacity_units"]).to_csv(f"{OUT}/plant_dc_master.csv", index=False)
    regional_dcs = [("RDC01", "Southeast RDC", "Charlotte, NC", "Southeast"),
                    ("RDC02", "Southwest RDC", "Houston, TX", "Southwest"),
                    ("RDC03", "Midwest RDC", "Kansas City, MO", "Midwest"),
                    ("RDC04", "West RDC", "Denver, CO", "West"),
                    ("RDC05", "Northeast RDC", "Newark, NJ", "Northeast"),
                    ("RDC06", "Florida RDC", "Tampa, FL", "Southeast")]
    pd.DataFrame([[d, nm, *sp(c), reg, int(RNG.integers(250_000, 600_000))] for d, nm, c, reg in regional_dcs],
                 columns=["regional_dc_id", "dc_name", "city", "state", "region", "storage_capacity_units"]).to_csv(f"{OUT}/regional_dc_master.csv", index=False)
    customer_dcs = [("CDC01", "Nashville Sales DC", "Nashville, TN", "Southeast"),
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
                    ("CDC12", "Atlanta Sales DC", "Atlanta, GA", "Southeast")]
    pd.DataFrame([[d, nm, *sp(c), reg, int(RNG.integers(60_000, 160_000))] for d, nm, c, reg in customer_dcs],
                 columns=["customer_dc_id", "dc_name", "city", "state", "region", "storage_capacity_units"]).to_csv(f"{OUT}/customer_dc_master.csv", index=False)

    def lane_params(dist, rng):
        if dist < 25:
            mode, mean, cv = "Truck", 0.5, 0.15
        elif dist < 600:
            mode, mean, cv = "Truck", rng.uniform(2, 4), rng.uniform(0.2, 0.35)
        elif dist < 1200:
            mode, mean, cv = ("Train" if rng.random() < 0.5 else "Truck"), rng.uniform(4, 7), rng.uniform(0.3, 0.45)
        else:
            mode, mean, cv = "Train", rng.uniform(7, 13), rng.uniform(0.4, 0.6)
        # logistics cost per unit in USD (case-level): short lanes ~$0.5-2, long rail ~$2-6
        logi = round(0.0018 * dist + rng.uniform(0.4, 1.2), 2)
        return mode, round(mean, 2), round(mean*cv, 2), logi, int(rng.integers(150_000, 500_000))

    lanes = []; ln = 1
    def add(o, ot, oc, d, dt, dc):
        nonlocal ln
        dist = round(hav(oc, dc), 1)
        mode, mean, std, logi, cap = lane_params(dist, RNG)
        lanes.append([f"LANE{ln:03d}", o, ot, d, dt, mode, mean, std, dist, logi, cap]); ln += 1
    for pid, _, c in plants:
        pdc = next(x for x in plant_dcs if x[1] == pid); add(pid, "PLANT", c, pdc[0], "PLANT_DC", pdc[2])
    for rid, _, rc, _ in regional_dcs:
        for pdc in sorted(plant_dcs, key=lambda x: hav(rc, x[2]))[:2]:
            add(pdc[0], "PLANT_DC", pdc[2], rid, "REGIONAL_DC", rc)
    for cid, _, cc, creg in customer_dcs:
        same = [r for r in regional_dcs if r[3] == creg] or regional_dcs
        rdc = min(same, key=lambda r: hav(cc, r[2]))
        add(rdc[0], "REGIONAL_DC", rdc[2], cid, "CUSTOMER_DC", cc)
    pd.DataFrame(lanes, columns=[
        "lane_id", "origin_node_id", "origin_node_type", "destination_node_id", "destination_node_type",
        "transport_mode", "lead_time_mean_days", "lead_time_std_days", "distance_miles",
        "logistics_cost_per_unit_usd", "capacity_units_per_week"]).to_csv(f"{OUT}/network_lanes.csv", index=False)

    banners = [
        ("BNR01", "MegaMart Stores", "RT", "BULK", 6.0), ("BNR02", "ValueClub Warehouse", "RT", "BULK", 4.5),
        ("BNR03", "GreenGrocer National", "RT", "BULK", 4.0), ("BNR04", "BullsEye Retail", "RT", "BULK", 3.2),
        ("BNR05", "QuickStop Convenience", "Brick-and-Mortar", "DSD", 1.3), ("BNR06", "SunBelt Grocers", "RT", "DSD", 1.6),
        ("BNR07", "Coastal Marts", "Brick-and-Mortar", "DSD", 1.1), ("BNR08", "MetroPetro C-Stores", "Brick-and-Mortar", "DSD", 0.9),
        ("BNR09", "CampusDining Foodservice", "Foodservice", "DSD", 1.2), ("BNR10", "FreshFoods Regional", "RT", "DSD", 1.5),
    ]
    pd.DataFrame([[b, nm, ch, dm] for b, nm, ch, dm, _ in banners],
                 columns=["b2b_customer_id", "customer_name", "channel_type", "delivery_mode"]).to_csv(f"{OUT}/b2b_customer_master.csv", index=False)

    rdc_ids = [r[0] for r in regional_dcs]; cdc_ids = [c[0] for c in customer_dcs]
    serve_rows = []; cust_dcs = {}
    for b, nm, ch, dm, scale in banners:
        pool = rdc_ids if dm == "BULK" else cdc_ids
        k = int(RNG.integers(2, 4)); chosen = list(RNG.choice(pool, size=k, replace=False))
        cust_dcs[b] = chosen
        for dcid, sh in zip(chosen, RNG.dirichlet(np.ones(k))):
            serve_rows.append([b, dcid, "REGIONAL_DC" if dm == "BULK" else "CUSTOMER_DC", round(float(sh), 4)])
    pd.DataFrame(serve_rows, columns=["b2b_customer_id", "dc_id", "dc_type", "demand_share"]).to_csv(f"{OUT}/customer_serving_dc.csv", index=False)

    print(f"  SKUs 500 (5x100) | Plants 4 PlantDCs 4 RDCs 6 CDCs 12 | Lanes {len(lanes)} | Banners 10 | USD")
    return dict(sku_meta=sku_meta, seasonal_index=seasonal_index, cust_dcs=cust_dcs,
                cust_scale={b: s for b, _, _, _, s in banners}, cust_deliv={b: dm for b, _, _, dm, _ in banners})


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
    banners = list(cust_dcs.keys())

    # ---------------- PROMOTIONS: customer-specific (or ALL) trade-promo events -----------------
    # Written to promotions.csv (the promo calendar) and used as the single source of the discounts
    # that appear in orders. Each event = a SKU discounted for a customer (or ALL) over a date range.
    PROMO_TYPES = ["TPR", "Feature", "Display", "Feature+Display"]
    promo_rows = []; pid_seq = 1
    disc = {s: {b: np.zeros(HIST_WEEKS) for b in banners} for s in sku_ids}   # discount by (sku,customer,week)
    pidx = {s: {b: np.array([""] * HIST_WEEKS, dtype=object) for b in banners} for s in sku_ids}
    for sid in sku_ids:
        for _ in range(int(RNG.integers(2, 6))):                 # 2-5 promo events per SKU/year
            dur = int(RNG.integers(1, 4))                        # 1-3 weeks
            start = int(RNG.integers(0, HIST_WEEKS - dur + 1))
            depth = round(float(RNG.uniform(0.05, 0.35)), 3)
            target_all = RNG.random() < 0.35
            tgt = "ALL" if target_all else str(RNG.choice(banners))
            pid = f"PR{pid_seq:05d}"; pid_seq += 1
            sd = week_start(start + 1)
            ed = (WEEK1_MONDAY + pd.Timedelta(weeks=start + dur - 1) + pd.Timedelta(days=6)).strftime("%Y-%m-%d")
            promo_rows.append([pid, sid, tgt, sd, ed, depth, str(RNG.choice(PROMO_TYPES))])
            for b in (banners if target_all else [tgt]):
                for w in range(start, start + dur):
                    if depth > disc[sid][b][w]:                  # deepest promo wins that week
                        disc[sid][b][w] = depth; pidx[sid][b][w] = pid
    pd.DataFrame(promo_rows, columns=["promo_id", "sku_id", "b2b_customer_id", "start_date",
                                      "end_date", "discount_pct", "promo_type"]).to_csv(f"{OUT}/promotions.csv", index=False)

    # ---------------- base/shelf price path per SKU (slow drift + 1-3 step revisions) ------------
    weeks = np.arange(HIST_WEEKS)
    base_price_path = {}
    for sid in sku_ids:
        lp = sku_meta[sid]["list_price"]
        drift = (1.02) ** (weeks / float(HIST_WEEKS))
        walk = 1.0 + np.cumsum(RNG.normal(0, 0.004, HIST_WEEKS))
        rev = np.ones(HIST_WEEKS)
        for _ in range(int(RNG.integers(1, 4))):
            wk = int(RNG.integers(4, HIST_WEEKS)); step = float(RNG.uniform(0.02, 0.06)) * RNG.choice([-1, 1])
            rev[wk:] *= (1 + step)
        base_price_path[sid] = np.clip(lp * drift * walk * rev, lp * 0.85, lp * 1.20)

    carries = {}
    for b in banners:
        frac = 0.85 if cust_deliv[b] == "BULK" else 0.55
        carries[b] = set(RNG.choice(sku_ids, size=int(len(sku_ids) * frac), replace=False).tolist())

    # order cadence: faster SKUs (higher base) are ordered every week; slower ones every 2-3 weeks
    bases = np.array([sku_meta[s]["base"] for s in sku_ids]); p60, p20 = np.percentile(bases, [60, 20])
    cadence = {s: (1 if sku_meta[s]["base"] >= p60 else (2 if sku_meta[s]["base"] >= p20 else 3)) for s in sku_ids}

    # ---------------- DAILY orders: each (customer,SKU) has an order weekday + cadence; a week's demand
    # (accumulated over the cadence) is placed on that weekday -> realistic B2B baskets grouped by order_id.
    ORDER_DAYS = {"BULK": [1, 3], "DSD": [0, 2, 4]}     # weekday offsets from Monday
    line_rows = []
    for b in banners:
        mode = cust_deliv[b]; cscale = cust_scale[b]; odays = ORDER_DAYS[mode]
        for sid in carries[b]:
            m = sku_meta[sid]; lp = m["list_price"]; si = seasonal_index[sid]
            eb, ep, stor = m["elast_base"], m["elast_promo"], m["storability"]
            bp = base_price_path[sid]; dsc = disc[sid][b]; pids = pidx[sid][b]
            pant = np.ones(HIST_WEEKS)                 # post-promo forward-buy dip (this customer's promos)
            for w in np.nonzero(dsc > 0)[0]:
                dip = stor * min(dsc[w] / 0.30, 1.0) * 0.20
                if w + 1 < HIST_WEEKS: pant[w + 1] *= (1 - dip)
                if w + 2 < HIST_WEEKS: pant[w + 2] *= (1 - 0.5 * dip)
            pmult = np.minimum((bp / lp) ** eb * (1 - dsc) ** ep, 12.0) * pant
            cad = cadence[sid]; weekday = int(RNG.choice(odays)); phase = int(RNG.integers(0, cad))
            for dcid in cust_dcs[b]:
                base = m["base"] * cscale * share_lookup[(b, dcid)]
                wq = np.maximum(0, np.round(base * si * pmult * RNG.lognormal(0, 0.15, HIST_WEEKS))).astype(int)
                acc = 0
                for w in range(HIST_WEEKS):
                    acc += int(wq[w])
                    if ((w % cad) == phase or w == HIST_WEEKS - 1) and acc > 0:
                        line_rows.append((b, dcid, sid, w, weekday, acc,
                                          round(float(bp[w]), 2), round(float(dsc[w]), 3), pids[w]))
                        acc = 0

    df = pd.DataFrame(line_rows, columns=["b2b_customer_id", "dc_id", "sku_id", "w", "off",
                                          "order_qty", "base_price_usd", "discount_pct", "promo_id"])
    df["order_date"] = WEEK1_MONDAY + pd.to_timedelta(df.w * 7 + df.off, unit="D")
    df["period"] = (df.w + 1).map(lambda i: period_label(i))
    df["list_price_usd"] = df.sku_id.map(lambda s: round(sku_meta[s]["list_price"], 2))
    df["unit_price_usd"] = (df.base_price_usd * (1 - df.discount_pct)).round(2)
    df["on_promo"] = df.discount_pct > 0
    df["revenue_usd"] = (df.order_qty * df.unit_price_usd).round(2)
    # one order_id per (customer, dc, order_date) = a basket; delivery lead by mode (per order)
    key = df[["b2b_customer_id", "dc_id", "order_date"]].drop_duplicates().reset_index(drop=True)
    key["order_id"] = [f"ORD{i+1:07d}" for i in range(len(key))]
    km = key.b2b_customer_id.map(cust_deliv).values
    lead = np.where(km == "BULK", RNG.integers(2, 7, len(key)), RNG.integers(1, 4, len(key)))
    key["delivery_date"] = key.order_date + pd.to_timedelta(lead, unit="D")
    df = df.merge(key, on=["b2b_customer_id", "dc_id", "order_date"])
    df["order_date"] = df.order_date.dt.strftime("%Y-%m-%d")
    df["delivery_date"] = df.delivery_date.dt.strftime("%Y-%m-%d")
    df["promo_id"] = df.promo_id.fillna("")
    cols = ["order_id", "order_date", "delivery_date", "period", "sku_id", "dc_id", "b2b_customer_id",
            "order_qty", "base_price_usd", "unit_price_usd", "list_price_usd", "discount_pct",
            "on_promo", "promo_id", "revenue_usd"]
    df[cols].to_csv(f"{OUT}/orders.csv", index=False)
    n_orders = df.order_id.nunique()
    print(f"  promotions: {len(promo_rows):,} events | orders: {len(df):,} lines in {n_orders:,} orders "
          f"({len(df)/n_orders:.1f} lines/order) | promo share {df.on_promo.mean():.0%} | daily, 52 wks 2025")


# ============================================================================
# STEP 3: demand_forecast (104 wks: 2025 in-sample w/ actuals + 2026 forward w/o actuals),
#         production (52wk), inventory (52wk)
# ============================================================================
def step3_forecast_production_inventory(ctx):
    RNG = np.random.default_rng(909)
    OUT = OUTPUT_DIR
    sku_meta, seasonal_index = ctx["sku_meta"], ctx["seasonal_index"]
    orders_daily = pd.read_csv(f"{OUT}/orders.csv")
    # roll DAILY order lines up to weekly (sku,dc,customer,period) for forecast/production/inventory
    ws_map = {period_label(i): week_start(i) for i in range(1, TOTAL_WEEKS + 1)}
    orders = orders_daily.groupby(["period", "sku_id", "dc_id", "b2b_customer_id"], as_index=False).order_qty.sum()
    orders["week_start_date"] = orders.period.map(ws_map)
    seg_of = {s: m["segment"] for s, m in sku_meta.items()}

    # ---- IN-SAMPLE forecast (2025): baseline expectation vs actual, forecast misses promo spikes ----
    idx_of = {period_label(i): i for i in range(1, HIST_WEEKS+1)}
    fc = orders[["period", "week_start_date", "sku_id", "dc_id", "b2b_customer_id", "order_qty"]].copy()
    fc.rename(columns={"order_qty": "actual_qty"}, inplace=True)
    seg_arr = fc.sku_id.map(seg_of).values
    wk_arr = fc.period.map(idx_of).values
    sd = np.array([SEGMENT_FC_NOISE[s] for s in seg_arr])
    bias = np.where(np.isin(wk_arr, [48, 49, 50, 51, 52]) & (seg_arr == "Seasonal"), -0.15, 0.0)
    fc["forecast_qty"] = np.maximum(0, np.round(fc.actual_qty.values * (1 + RNG.normal(bias, sd)))).astype(int)
    fc["forecast_error_pct"] = ((fc.forecast_qty - fc.actual_qty) / fc.actual_qty.replace(0, np.nan)).round(4)
    fc["horizon"] = "in_sample"

    # ---- FORWARD forecast (2026): reuse exact (sku,dc,customer) keys; project level x growth x seasonality.
    #      NO actuals -> this is the MEIO execution horizon. ----
    combos = orders.groupby(["sku_id", "dc_id", "b2b_customer_id"], as_index=False).order_qty.sum()
    combos["wk_level"] = combos.order_qty / HIST_WEEKS               # de-seasonalized avg weekly level
    fwd_rows = []
    seas_mean = {s: seasonal_index[s].mean() for s in seasonal_index}
    for r in combos.itertuples():
        si = seasonal_index[r.sku_id]; sm = seas_mean[r.sku_id]
        lvl = r.wk_level * (1 + YOY_GROWTH)
        noise = RNG.normal(0, SEGMENT_FC_NOISE[seg_of[r.sku_id]], HIST_WEEKS)
        qf = np.maximum(0, np.round(lvl * (si / sm) * (1 + noise))).astype(int)
        for j in range(HIST_WEEKS):
            gi = HIST_WEEKS + j + 1                                  # global week idx 53..104
            fwd_rows.append((period_label(gi), week_start(gi), r.sku_id, r.dc_id,
                             r.b2b_customer_id, int(qf[j])))
    fwd = pd.DataFrame(fwd_rows, columns=["period", "week_start_date", "sku_id", "dc_id",
                                          "b2b_customer_id", "forecast_qty"])
    fwd["actual_qty"] = np.nan; fwd["forecast_error_pct"] = np.nan; fwd["horizon"] = "forward"

    cols = ["period", "week_start_date", "horizon", "sku_id", "dc_id", "b2b_customer_id",
            "forecast_qty", "actual_qty", "forecast_error_pct"]
    # forward rows legitimately have blank actuals (float NaN); align dtypes before concat to avoid noise
    fc_out = fc[cols].copy()
    fc_out["actual_qty"] = fc_out["actual_qty"].astype(float)
    fc_out["forecast_error_pct"] = fc_out["forecast_error_pct"].astype(float)
    pd.concat([fc_out, fwd[cols]], ignore_index=True).to_csv(f"{OUT}/demand_forecast.csv", index=False)
    mape = fc.assign(ape=(fc.forecast_qty - fc.actual_qty).abs() / fc.actual_qty.replace(0, np.nan),
                     seg=fc.sku_id.map(seg_of)).groupby("seg").ape.mean()
    print(f"  demand_forecast: in_sample {len(fc):,} + forward {len(fwd):,} = {len(fc)+len(fwd):,} rows (104 wks)")
    print("  in-sample MAPE by segment:", {k: round(v, 3) for k, v in mape.items()})

    # ---- production (2025): SKU x Plant x Week ----
    plants = pd.read_csv(f"{OUT}/plant_master.csv").plant_id.tolist()
    sku_ids = list(sku_meta.keys())
    sku_plant = {s: plants[i % len(plants)] for i, s in enumerate(sku_ids)}
    nat = orders.groupby(["sku_id", "period"], as_index=False).order_qty.sum()
    nat["wk"] = nat.period.map(idx_of); nat["pw"] = (nat.wk - 1).clip(lower=1)
    planned = nat.groupby(["sku_id", "pw"], as_index=False).order_qty.sum().rename(columns={"pw": "wk", "order_qty": "planned_qty"})
    planned["plant_id"] = planned.sku_id.map(sku_plant)
    adh = np.where(RNG.random(len(planned)) < 0.90, np.clip(RNG.normal(0.945, 0.02, len(planned)), 0.85, 1.02), RNG.uniform(0.75, 0.90, len(planned)))
    planned["adherence_pct"] = adh.round(3)
    planned["actual_qty"] = (planned.planned_qty * planned.adherence_pct).round().astype(int)
    planned["period"] = planned.wk.map(period_label); planned["week_start_date"] = planned.wk.map(week_start)
    planned[["period", "week_start_date", "plant_id", "sku_id", "planned_qty", "actual_qty", "adherence_pct"]].to_csv(f"{OUT}/production.csv", index=False)
    print(f"  production rows: {len(planned):,} | mean adherence {planned.adherence_pct.mean():.3f}")

    # ---- inventory (2025): SKU x DC x Week, (s,S) flow sim from each DC's outbound orders ----
    dcd = orders.groupby(["dc_id", "sku_id", "period"], as_index=False).order_qty.sum()
    dcd["wk"] = dcd.period.map(idx_of)
    inv_rows = []
    for (dcid, sid), g in dcd.groupby(["dc_id", "sku_id"]):
        wk_qty = np.zeros(HIST_WEEKS + 1)
        for r in g.itertuples():
            wk_qty[int(r.wk)] = r.order_qty
        avg = wk_qty[1:].mean(); std = wk_qty[1:].std(); lt = 1
        safety = int(max(avg*0.6, 2.05*std*math.sqrt(lt)))   # ~98% cycle service under promo spikes
        reorder = int(avg*lt + safety); S = int(reorder + 3*avg); on_hand = S; pipe = {}
        for wk in range(1, HIST_WEEKS + 1):
            rec = pipe.pop(wk, 0); opening = on_hand + rec
            shipped = min(opening, wk_qty[wk]); closing = opening - shipped
            pos = closing + sum(pipe.values())
            if pos <= reorder:
                pipe[wk+lt] = pipe.get(wk+lt, 0) + (S - pos)
            inv_rows.append((period_label(wk), week_start(wk), sid, dcid, int(opening-rec),
                             int(rec), int(shipped), int(closing), int(safety), int(reorder)))
            on_hand = closing
    inv = pd.DataFrame(inv_rows, columns=["period", "week_start_date", "sku_id", "dc_id",
        "opening_stock", "receipts", "shipments", "closing_stock", "safety_stock", "reorder_point"])
    inv.to_csv(f"{OUT}/inventory.csv", index=False)
    print(f"  inventory rows: {len(inv):,} | zero-closing weeks {(inv.closing_stock==0).mean():.1%}")


# ============================================================================
# STEP 4: referential-integrity + sanity validation (behaves like real client data)
# ============================================================================
def validate():
    OUT = OUTPUT_DIR
    errs = []
    def chk(name, cond):
        print(f"[{'OK' if cond else 'FAIL'}] {name}");  errs.append(name) if not cond else None

    sku = pd.read_csv(f"{OUT}/sku_master.csv")
    orders = pd.read_csv(f"{OUT}/orders.csv")
    fc = pd.read_csv(f"{OUT}/demand_forecast.csv")
    prod = pd.read_csv(f"{OUT}/production.csv")
    inv = pd.read_csv(f"{OUT}/inventory.csv")
    lanes = pd.read_csv(f"{OUT}/network_lanes.csv")
    serve = pd.read_csv(f"{OUT}/customer_serving_dc.csv")
    banners = pd.read_csv(f"{OUT}/b2b_customer_master.csv")
    plants = pd.read_csv(f"{OUT}/plant_master.csv")
    pdc = pd.read_csv(f"{OUT}/plant_dc_master.csv"); rdc = pd.read_csv(f"{OUT}/regional_dc_master.csv")
    cdc = pd.read_csv(f"{OUT}/customer_dc_master.csv")

    sku_ids = set(sku.sku_id); cust_ids = set(banners.b2b_customer_id)
    stock_dcs = set(rdc.regional_dc_id) | set(cdc.customer_dc_id)
    all_nodes = set(plants.plant_id) | set(pdc.plant_dc_id) | stock_dcs

    chk("500 SKUs, 5 x 100", len(sku) == 500 and (sku.category.value_counts() == 100).all())
    chk("no elasticity answer-key column in sku_master", "true_elasticity" not in sku.columns)
    chk("no measured-elasticity file emitted", not os.path.exists(f"{OUT}/price_elasticity_measured.csv"))
    chk("all money columns present in USD", {"list_price_usd", "unit_cost_usd", "margin_usd"}.issubset(sku.columns)
        and "logistics_cost_per_unit_usd" in lanes.columns and "revenue_usd" in orders.columns)
    chk("orders expose base + promo price columns", {"base_price_usd", "unit_price_usd", "on_promo", "discount_pct"}.issubset(orders.columns))
    chk("base/shelf price varies over time (identifies base elasticity)", orders.groupby("sku_id").base_price_usd.nunique().gt(1).mean() > 0.9)
    # daily order structure
    chk("orders are day-level with order + delivery dates", {"order_id", "order_date", "delivery_date"}.issubset(orders.columns))
    _od = pd.to_datetime(orders.order_date); _dd = pd.to_datetime(orders.delivery_date)
    chk("delivery_date >= order_date for all lines", (_dd >= _od).all())
    chk("order lines span day-of-week (not just weekly)", _od.dt.dayofweek.nunique() > 1)
    # promotions table + referential integrity with orders
    promos = pd.read_csv(f"{OUT}/promotions.csv")
    chk("promotions.sku_id -> sku_master", set(promos.sku_id) <= sku_ids)
    chk("promotions.b2b_customer_id in banners or ALL", set(promos.b2b_customer_id) <= (cust_ids | {"ALL"}))
    chk("promotions discount in (0,1)", promos.discount_pct.between(0.0, 1.0).all() and (promos.discount_pct > 0).all())
    chk("on_promo lines carry a promo_id and vice-versa", (orders.on_promo == (orders.promo_id.fillna("") != "")).all())
    chk("order promo_id values resolve to promotions", set(orders.promo_id.fillna("")) - {""} <= set(promos.promo_id))
    chk("all SKU margins positive (USD)", (sku.margin_usd > 0).all())
    chk("shelf life present", (sku.shelf_life_days > 0).all())
    # referential integrity
    chk("orders.sku_id -> sku_master", set(orders.sku_id) <= sku_ids)
    chk("orders.b2b_customer_id -> banners", set(orders.b2b_customer_id) <= cust_ids)
    chk("orders.dc_id -> stocking DCs", set(orders.dc_id) <= stock_dcs)
    chk("forecast keys -> sku/customer/dc", set(fc.sku_id) <= sku_ids and set(fc.b2b_customer_id) <= cust_ids and set(fc.dc_id) <= stock_dcs)
    chk("production.plant_id -> plants", set(prod.plant_id) <= set(plants.plant_id))
    chk("production.sku_id -> sku_master", set(prod.sku_id) <= sku_ids)
    chk("inventory.dc_id -> stocking DCs", set(inv.dc_id) <= stock_dcs)
    chk("lane endpoints -> known nodes", set(lanes.origin_node_id) <= all_nodes and set(lanes.destination_node_id) <= all_nodes)
    chk("serving map dc -> stocking DCs", set(serve.dc_id) <= stock_dcs)
    # forecast horizon structure
    chk("forecast spans 104 weeks", fc.period.nunique() == 104)
    chk("in_sample horizon has actuals", fc[fc.horizon == "in_sample"].actual_qty.notna().all())
    chk("forward horizon has NO actuals", fc[fc.horizon == "forward"].actual_qty.isna().all())
    fwd_keys = set(map(tuple, fc[fc.horizon == "forward"][["sku_id", "dc_id", "b2b_customer_id"]].drop_duplicates().values))
    ord_keys = set(map(tuple, orders[["sku_id", "dc_id", "b2b_customer_id"]].drop_duplicates().values))
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
    regional_dc_master(regional_dc_id) ─┐
    customer_dc_master(customer_dc_id) ─┴─ "stocking DCs"
    b2b_customer_master(b2b_customer_id) ─< customer_serving_dc(b2b_customer_id, dc_id)   dc_id ∈ stocking DCs
    sku_master(sku_id) ──< orders / demand_forecast / production / inventory
    orders(sku_id, dc_id, b2b_customer_id, period)               dc_id ∈ stocking DCs
    demand_forecast(sku_id, dc_id, b2b_customer_id, period, horizon)   forward keys ⊆ historical order keys
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

    ## Real-world grounding (Food & Beverage Sales dataset)

    Demand-side parameters are grounded in an external real dataset (`Food and Beverage Sales/`), with
    **2020 excluded for demand levels** (COVID spike stripped). The 5 synthetic categories are kept but
    repriced/reseasonalized from real product groups:
    * **Per-unit USD prices** drawn from each SKU's mapped real product group (see `subcategory`).
    * **Two elasticities** per SKU: a **base** elasticity = raw empirical per-product-group estimate
        (verbatim, incl. near-zero/positive/extreme) + noise, applied to shelf-price moves; and a
        **promotional** elasticity = base x 1.75 (bounded to the CPG promo band, ~-4.5), applied to discount
        depth. A post-promo **forward-buy dip** (pantry-loading) follows promotions for storable categories.
    * **Seasonality** = normalized monthly SHAPE from mature-year 2020 (Sep-Nov peak, gentle Dec->Jan;
        2019 was a ramp-up whose trend would distort the curve, so it is not used for shape).
    * **Revenue concentration** = real ~80/20 (top ~20% of SKUs ~ 80%+ of revenue).
    Supply-side (unit cost/margin, holding, logistics, network, lead time, inventory, production) has no
    real source here and stays a modelled assumption.

    ## Tables (grain)

    | Table | Grain | Notes |
    |---|---|---|
    | sku_master | SKU | 500 SKUs (100 x 5 categories); USD economics, shelf life, segment, seasonal flag |
    | plant_master / plant_dc_master / regional_dc_master / customer_dc_master | node | Coca-Cola-modelled US network |
    | network_lanes | lane | Truck/Train only; lead-time mean+std (days); USD logistics cost/unit |
    | b2b_customer_master | banner | BULK (warehouse, served from RDCs) vs DSD (served from CDCs) |
    | customer_serving_dc | banner x DC | which stocking DC serves each banner + demand share |
    | cost_parameters | category | holding %, cost of capital %, stockout multiplier |
    | orders | SKU x DC x Customer x **Day** | 52 wks 2025; each line has order_id (basket), order_date, delivery_date, base_price_usd (shelf), discount_pct/on_promo/promo_id, unit_price_usd |
    | promotions | promo event | customer-specific (or ALL) trade-promo calendar: promo_id, sku_id, b2b_customer_id, start_date, end_date, discount_pct, promo_type -- the source of the discounts in orders |
    | demand_forecast | SKU x DC x Customer x Week | 104 wks; horizon in_sample (w/ actuals) or forward (no actuals) |
    | production | SKU x Plant x Week | 52 wks; planned + actual + adherence % |
    | inventory | SKU x DC x Week | 52 wks; opening/receipts/shipments/closing/safety/reorder |

    ## Promotions & daily orders

    `orders` is now **day-level**: each row is an order line, grouped into baskets by `order_id`
    (one per customer x DC x order_date), with an `order_date` and a `delivery_date` (order date +
    a fulfilment lead that depends on mode -- DSD 1-3 days, BULK/warehouse 2-6 days). Weekly demand is
    split across each customer's order-days (BULK order ~weekly, DSD ~twice weekly). A `period`
    (ISO-style week label) is retained on every line for weekly roll-ups.

    `promotions.csv` is the trade-promotion calendar and the single source of the discounts seen in
    orders. Each event discounts a SKU for a specific customer (or ALL) over a date range; events are
    customer-specific because trade promotions are negotiated per account. Every promoted order line
    carries the `promo_id` of the event that set its discount, so orders <-> promotions join cleanly.

    ## PRICE ELASTICITY -- how to calculate BASE and PROMOTIONAL elasticity from orders (METHOD, not shipped)

    The data carries TWO distinct price responses, and no elasticity column is shipped -- you estimate both
    from `orders`, exactly as you would on a real client extract:
    * **base (regular) elasticity** -- response of demand to the everyday **shelf** price
        (`base_price_usd`, which drifts with mild inflation and steps at 1-3 revisions/yr).
    * **promotional (deal) elasticity** -- response to a temporary **discount** (`discount_pct`, ~30% of
        SKU-weeks, 5-35% off); larger in magnitude than base (grounded at ~1.75x; CPG research puts promo
        ~1.5-2x regular). Promotions are followed by a **forward-buy dip** in the next 1-2 weeks for
        storable categories (pantry-loading payback), which is the signature that separates a deal response
        from a base response.

    Recommended method -- one regression with BOTH price terms and SKU + week fixed effects:

        ln(order_qty)_{i,t} = alpha_i + gamma_t
                            + beta_base  * ln(base_price_usd)_{i,t}
                            + beta_promo * ln(1 - discount_pct)_{i,t} + e

    * `alpha_i` (SKU FE) absorbs baseline volume/price; `gamma_t` (week FE) absorbs common seasonality.
    * `beta_base` is identified from shelf-price drift/revisions; `beta_promo` from discount depth.
    * Expect |beta_promo| > |beta_base|. Estimate per category (pool SKUs) for stable numbers.

    Minimal snippet (statsmodels):

        import pandas as pd, numpy as np, statsmodels.formula.api as smf
        o = pd.read_csv("orders.csv").merge(pd.read_csv("sku_master.csv")[["sku_id","category"]], on="sku_id")
        o = o[(o.order_qty>0) & (o.base_price_usd>0)].copy()
        o["ln_q"]=np.log(o.order_qty); o["ln_base"]=np.log(o.base_price_usd); o["ln_promo"]=np.log(1-o.discount_pct)
        for cat, g in o.groupby("category"):
            m = smf.ols("ln_q ~ ln_base + ln_promo + C(sku_id) + C(period)", data=g).fit()
            print(cat, "base=", round(m.params["ln_base"],2), " promo=", round(m.params["ln_promo"],2))

    Pure-numpy alternative: two-way demean ln_q, ln_base, ln_promo by sku_id and period, then OLS of
    demeaned ln_q on [demeaned ln_base, demeaned ln_promo].

    Caveats: (1) to isolate the forward-buy effect, add lagged-promo controls or expect the post-promo dip;
    (2) always include the week FE; (3) both elasticities are negative for most groups, a few real groups
    are near-zero/positive and kept verbatim, so a pooled category value can be pulled toward zero.

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
            shutil.copy2(src, os.path.join(ref_dir, fn)); copied.append(fn)
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
    print(f"\nAll tables + docs written to: {OUTPUT_DIR}\nTotal runtime: {time.time()-t0:.1f}s")
    return ok


if __name__ == "__main__":
    main()