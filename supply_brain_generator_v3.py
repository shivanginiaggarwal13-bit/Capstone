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
import datetime as dt
try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt; HAVE_MPL = True
except Exception:
    HAVE_MPL = False

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

    # ---- network: EMBEDDED ideal topology (self-contained; no external network files needed) ----
    # 4 Plants (production-only) -> 4 Plant DCs -> 8 Regional DCs -> 15 Customer DCs.
    # Every Customer DC dual-sourced (primary + failover RDC); every RDC dual-sourced from 2 Plant DCs;
    # plus Regional-DC to Regional-DC lateral rebalancing lanes. Truck/Train only. A dedicated net_rng is
    # used so the demand-side RNG stream (SKUs, serving map) is unchanged and the demand profile is preserved.
    net_rng = np.random.default_rng(2026)
    NCITY = {
        "Atlanta, GA": (33.75,-84.39,"Southeast"), "Dallas, TX": (32.78,-96.80,"Southwest"),
        "Chicago, IL": (41.88,-87.63,"Midwest"), "Los Angeles, CA": (34.05,-118.24,"West"),
        "Newark, NJ": (40.74,-74.17,"Northeast"), "Charlotte, NC": (35.23,-80.84,"Southeast"),
        "Tampa, FL": (27.95,-82.46,"Southeast"), "Denver, CO": (39.74,-104.99,"West"),
        "Seattle, WA": (47.61,-122.33,"West"), "Boston, MA": (42.36,-71.06,"Northeast"),
        "Philadelphia, PA": (39.95,-75.17,"Northeast"), "Nashville, TN": (36.16,-86.78,"Southeast"),
        "Miami, FL": (25.76,-80.19,"Southeast"), "Orlando, FL": (28.54,-81.38,"Southeast"),
        "Minneapolis, MN": (44.98,-93.27,"Midwest"), "St. Louis, MO": (38.63,-90.20,"Midwest"),
        "Detroit, MI": (42.33,-83.05,"Midwest"), "Houston, TX": (29.76,-95.37,"Southwest"),
        "Phoenix, AZ": (33.45,-112.07,"Southwest"), "Sacramento, CA": (38.58,-121.49,"West")}
    def _sp(c): return c.split(", ")[0], c.split(", ")[1]
    def _hav(a,b):
        (la1,lo1,_),(la2,lo2,_)=NCITY[a],NCITY[b]; R=3958.8
        p1,p2=math.radians(la1),math.radians(la2); dphi=math.radians(la2-la1); dl=math.radians(lo2-lo1)
        h=math.sin(dphi/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return R*2*math.atan2(math.sqrt(h),math.sqrt(1-h))
    def _lane(dist, lateral=False):
        if dist<25:      mode,mean,cv="Truck",0.5,0.15
        elif dist<600:   mode,mean,cv="Truck",net_rng.uniform(2,4),net_rng.uniform(0.2,0.35)
        elif dist<1200:  mode,mean,cv=("Truck" if lateral else ("Train" if net_rng.random()<0.5 else "Truck")),net_rng.uniform(4,7),net_rng.uniform(0.3,0.45)
        else:            mode,mean,cv="Train",net_rng.uniform(7,13),net_rng.uniform(0.4,0.6)
        return mode,round(mean,2),round(mean*cv,2),round(dist,1),round(0.0018*dist+net_rng.uniform(0.4,1.2),2),int(net_rng.integers(200_000,550_000))
    n_plants=[("PLANT01","Atlanta Production Center","Atlanta, GA"),("PLANT02","Dallas Production Center","Dallas, TX"),
              ("PLANT03","Chicago Production Center","Chicago, IL"),("PLANT04","Los Angeles Production Center","Los Angeles, CA")]
    n_pdc=[("PDC01","PLANT01","Atlanta, GA"),("PDC02","PLANT02","Dallas, TX"),("PDC03","PLANT03","Chicago, IL"),("PDC04","PLANT04","Los Angeles, CA")]
    n_rdc=[("RDC01","Northeast RDC","Newark, NJ"),("RDC02","Southeast RDC","Charlotte, NC"),("RDC03","Florida RDC","Tampa, FL"),
           ("RDC04","Midwest RDC","Chicago, IL"),("RDC05","South-Central RDC","Dallas, TX"),("RDC06","Mountain RDC","Denver, CO"),
           ("RDC07","West Coast RDC","Los Angeles, CA"),("RDC08","Pacific NW RDC","Seattle, WA")]
    n_cdc=[("CDC01","Boston Sales DC","Boston, MA"),("CDC02","Philadelphia Sales DC","Philadelphia, PA"),("CDC03","Atlanta Sales DC","Atlanta, GA"),
           ("CDC04","Nashville Sales DC","Nashville, TN"),("CDC05","Miami Sales DC","Miami, FL"),("CDC06","Orlando Sales DC","Orlando, FL"),
           ("CDC07","Minneapolis Sales DC","Minneapolis, MN"),("CDC08","St. Louis Sales DC","St. Louis, MO"),("CDC09","Detroit Sales DC","Detroit, MI"),
           ("CDC10","Houston Sales DC","Houston, TX"),("CDC11","Phoenix Sales DC","Phoenix, AZ"),("CDC12","Denver Sales DC","Denver, CO"),
           ("CDC13","Los Angeles Sales DC","Los Angeles, CA"),("CDC14","Sacramento Sales DC","Sacramento, CA"),("CDC15","Seattle Sales DC","Seattle, WA")]
    pd.DataFrame([[pid,nm,*_sp(c),int(net_rng.integers(3_500_000,4_800_000)),"TRUE"] for pid,nm,c in n_plants],
        columns=["plant_id","plant_name","city","state","production_capacity_units_per_week","no_inventory_flag"]).to_csv(f"{OUT}/plant_master.csv",index=False)
    pd.DataFrame([[d,pid,*_sp(c),int(net_rng.integers(800_000,1_200_000))] for d,pid,c in n_pdc],
        columns=["plant_dc_id","plant_id","city","state","storage_capacity_units"]).to_csv(f"{OUT}/plant_dc_master.csv",index=False)
    pd.DataFrame([[d,nm,*_sp(c),NCITY[c][2],int(net_rng.integers(400_000,700_000))] for d,nm,c in n_rdc],
        columns=["regional_dc_id","dc_name","city","state","region","storage_capacity_units"]).to_csv(f"{OUT}/regional_dc_master.csv",index=False)
    pd.DataFrame([[d,nm,*_sp(c),NCITY[c][2],int(net_rng.integers(90_000,190_000))] for d,nm,c in n_cdc],
        columns=["customer_dc_id","dc_name","city","state","region","storage_capacity_units"]).to_csv(f"{OUT}/customer_dc_master.csv",index=False)
    _rcity={r[0]:r[2] for r in n_rdc}
    _lanes=[]; _ln=1
    def _add(o_,ot,oc,d_,dt_,dc,lat=False):
        nonlocal _ln
        mode,mean,std,dist,logi,cap=_lane(_hav(oc,dc),lat)
        _lanes.append([f"LANE{_ln:03d}",o_,ot,d_,dt_,mode,mean,std,dist,logi,cap]); _ln+=1
    for pid,_,c in n_plants:
        pdx=next(x for x in n_pdc if x[1]==pid); _add(pid,"PLANT",c,pdx[0],"PLANT_DC",pdx[2])
    for rid,_,rc in n_rdc:
        for x in sorted(n_pdc,key=lambda x:_hav(rc,x[2]))[:2]: _add(x[0],"PLANT_DC",x[2],rid,"REGIONAL_DC",rc)
    for cid,_,cc in n_cdc:
        for rr in sorted(n_rdc,key=lambda x:_hav(cc,x[2]))[:2]: _add(rr[0],"REGIONAL_DC",rr[2],cid,"CUSTOMER_DC",cc)
    _pairs=set()
    for rid,_,rc in n_rdc:
        for r2 in sorted(n_rdc,key=lambda x:_hav(rc,x[2]))[1:3]: _pairs.add(tuple(sorted([rid,r2[0]])))
    for a,b in sorted(_pairs): _add(a,"REGIONAL_DC",_rcity[a],b,"REGIONAL_DC",_rcity[b],lat=True)
    pd.DataFrame(_lanes,columns=["lane_id","origin_node_id","origin_node_type","destination_node_id","destination_node_type",
        "transport_mode","lead_time_mean_days","lead_time_std_days","distance_miles","logistics_cost_per_unit_usd",
        "capacity_units_per_week"]).to_csv(f"{OUT}/network_lanes.csv",index=False)
    rdc_ids=[r[0] for r in n_rdc]; cdc_ids=[c[0] for c in n_cdc]; n_lanes=len(_lanes)

    banners = [
        ("BNR01", "MegaMart Stores", "RT", "BULK", 6.0), ("BNR02", "ValueClub Warehouse", "RT", "BULK", 4.5),
        ("BNR03", "GreenGrocer National", "RT", "BULK", 4.0), ("BNR04", "BullsEye Retail", "RT", "BULK", 3.2),
        ("BNR05", "QuickStop Convenience", "Brick-and-Mortar", "DSD", 1.3), ("BNR06", "SunBelt Grocers", "RT", "DSD", 1.6),
        ("BNR07", "Coastal Marts", "Brick-and-Mortar", "DSD", 1.1), ("BNR08", "MetroPetro C-Stores", "Brick-and-Mortar", "DSD", 0.9),
        ("BNR09", "CampusDining Foodservice", "Foodservice", "DSD", 1.2), ("BNR10", "FreshFoods Regional", "RT", "DSD", 1.5),
    ]
    pd.DataFrame([[b, nm, ch, dm] for b, nm, ch, dm, _ in banners],
                 columns=["b2b_customer_id", "customer_name", "channel_type", "delivery_mode"]).to_csv(f"{OUT}/b2b_customer_master.csv", index=False)

    serve_rows = []; cust_dcs = {}
    for b, nm, ch, dm, scale in banners:
        pool = rdc_ids if dm == "BULK" else cdc_ids
        k = int(RNG.integers(2, 4)); chosen = list(RNG.choice(pool, size=k, replace=False))
        cust_dcs[b] = chosen
        for dcid, sh in zip(chosen, RNG.dirichlet(np.ones(k))):
            serve_rows.append([b, dcid, "REGIONAL_DC" if dm == "BULK" else "CUSTOMER_DC", round(float(sh), 4)])
    pd.DataFrame(serve_rows, columns=["b2b_customer_id", "dc_id", "dc_type", "demand_share"]).to_csv(f"{OUT}/customer_serving_dc.csv", index=False)

    print(f"  SKUs 500 (5x100) | RDCs {len(rdc_ids)} CDCs {len(cdc_ids)} | Lanes {n_lanes} | Banners {len(banners)} | network=embedded ideal (8 RDC/15 CDC)")
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
    plant_master(plant_id) < plant_dc_master(plant_dc_id, plant_id)
                            network_lanes(origin_node_id, destination_node_id)   every endpoint is a
                            plant_id / plant_dc_id / regional_dc_id / customer_dc_id
    regional_dc_master(regional_dc_id) 
    customer_dc_master(customer_dc_id)  "stocking DCs"
    b2b_customer_master(b2b_customer_id) < customer_serving_dc(b2b_customer_id, dc_id)   dc_id  stocking DCs
    sku_master(sku_id) < orders / demand_forecast / production / inventory
    orders(sku_id, dc_id, b2b_customer_id, period)               dc_id  stocking DCs
    demand_forecast(sku_id, dc_id, b2b_customer_id, period, horizon)   forward keys subset of historical order keys
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
# ============================================================================
# COMPREHENSIVE EDA REPORT (in-process; reads the CSVs just generated in OUT)
# ============================================================================
def _tab(df, floatfmt="{:,.2f}"):
    cols=[str(c) for c in df.columns]
    def f(v):
        if v is None or (isinstance(v,float) and math.isnan(v)): return ""
        if isinstance(v,float): return floatfmt.format(v)
        if isinstance(v,(int,np.integer)): return f"{int(v):,}"
        return str(v)
    out=["| "+" | ".join(cols)+" |","| "+" | ".join(["---"]*len(cols))+" |"]
    for _,r in df.iterrows(): out.append("| "+" | ".join(f(r[c]) for c in df.columns)+" |")
    return "\n".join(out)

def _kv(pairs):
    return "\n".join(["| Field | Value |","| --- | --- |"]+[f"| {k} | {v} |" for k,v in pairs])

def _chart(fig, out, fname):
    d=os.path.join(out,"report_assets"); os.makedirs(d,exist_ok=True)
    fig.savefig(os.path.join(d,fname),dpi=115,bbox_inches="tight"); plt.close(fig)
    return f"![{fname}](report_assets/{fname})"

def _demean(v,keys,passes=6):
    v=v.astype(float)
    for _ in range(passes):
        for k in keys: v=v-pd.Series(v).groupby(k).transform("mean").values
    return v

def generate_eda(OUT):
    R=lambda n: pd.read_csv(f"{OUT}/{n}.csv")
    sku=R("sku_master"); orders=R("orders"); fc=R("demand_forecast"); prod=R("production")
    inv=R("inventory"); lanes=R("network_lanes"); ban=R("b2b_customer_master"); serve=R("customer_serving_dc")
    costp=R("cost_parameters"); plants=R("plant_master"); pdc=R("plant_dc_master")
    rdc=R("regional_dc_master"); cdc=R("customer_dc_master"); promos=R("promotions")
    orders["_od"]=pd.to_datetime(orders.order_date,errors="coerce")
    ordw=orders.groupby(["sku_id","dc_id","b2b_customer_id","period"],as_index=False).order_qty.sum()
    sku_rev=orders.groupby("sku_id").revenue_usd.sum()
    unit_cost=sku.set_index("sku_id").unit_cost_usd
    m=[]; A=m.append

    rev=orders.revenue_usd.sum(); cogs=(orders.order_qty*orders.sku_id.map(unit_cost)).sum()
    A("# Supply Brain - Comprehensive Dataset and Network EDA\n")
    A(f"*Auto-generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}. Single-file build: this report is produced in "
      "the same run that generates the data, so it always matches the current instance.*\n")
    A("## Executive snapshot\n")
    A(_kv([("SKUs",f"{sku.sku_id.nunique():,}"),
           ("Network",f"{len(plants)} plants, {len(pdc)} plant DCs, {len(rdc)} regional DCs, {len(cdc)} customer DCs, {len(lanes)} lanes"),
           ("B2B customers (banners)",len(ban)),
           ("Order lines (day-level, 2025)",f"{len(orders):,}"),
           ("Distinct orders (baskets)",f"{orders.order_id.nunique():,}"),
           ("Total demand (units, 2025)",f"{orders.order_qty.sum():,}"),
           ("Total revenue (2025)",f"${rev:,.0f}"),
           ("Gross margin",f"${rev-cogs:,.0f} ({(rev-cogs)/rev*100:.1f} pct)"),
           ("Forecast horizon",f"{fc.period.nunique()} weeks (2025 in-sample + 2026 forward)")])+"\n")

    A("\n---\n\n# Part 1 - Parameters and Assumptions\n")
    A("Synthetic CPG Food and Beverage supply chain, USD, grounded in an external real F&B sales dataset "
      "(per-unit prices, per-group elasticity, 2020-shape seasonality, ~80/20 concentration; 2020 COVID spike "
      "excluded). 5 categories x 100 SKUs, segmented ~50/30/20 Fast-Moving/Seasonal/Intermediate. Two price "
      "elasticities per SKU (base shelf-price and promotional). Demand is day-level orders on a dual-sourced "
      "US network (Plant -> Plant DC -> Regional DC -> Customer DC) with lateral RDC-to-RDC rebalancing lanes. "
      "Supply-side parameters (cost/margin, holding, lead time, capacities) are modelled assumptions; demand-side is grounded.\n")

    A("\n---\n\n# Part 2 - Exploratory Data Analysis\n")

    # 2.1 overview + data quality
    A("## 2.1 Dataset overview and data quality\n")
    tbls=["sku_master","orders","promotions","demand_forecast","production","inventory","network_lanes",
          "b2b_customer_master","customer_serving_dc","cost_parameters","plant_master","plant_dc_master",
          "regional_dc_master","customer_dc_master"]
    rows=[[t,len(R(t)),R(t).shape[1],int(R(t).isna().sum().sum())] for t in tbls]
    A(_tab(pd.DataFrame(rows,columns=["table","rows","cols","null_cells"]))+"\n")
    dq=[("orders order_qty > 0",bool((orders.order_qty>0).all())),
        ("orders unit_price_usd > 0",bool((orders.unit_price_usd>0).all())),
        ("sku margins positive",bool((sku.margin_usd>0).all())),
        ("inventory closing_stock >= 0",bool((inv.closing_stock>=0).all())),
        ("delivery_date >= order_date",bool((pd.to_datetime(orders.delivery_date)>=orders._od).all())),
        ("sku_id unique",bool(sku.sku_id.is_unique))]
    A("\n**Data-quality checks:** "+"; ".join(f"{n} = {'PASS' if c else 'FAIL'}" for n,c in dq)+".\n")

    # 2.2 referential integrity
    A("## 2.2 Referential integrity\n")
    sku_ids=set(sku.sku_id); cust=set(ban.b2b_customer_id)
    stock=set(rdc.regional_dc_id)|set(cdc.customer_dc_id); nodes=set(plants.plant_id)|set(pdc.plant_dc_id)|stock
    chk=[("orders.sku_id in sku_master",set(orders.sku_id)<=sku_ids),
         ("orders.dc_id in stocking DCs",set(orders.dc_id)<=stock),
         ("orders.b2b_customer_id in banners",set(orders.b2b_customer_id)<=cust),
         ("orders.promo_id in promotions",set(orders.promo_id.fillna(""))-{""}<=set(promos.promo_id)),
         ("forecast keys in masters",set(fc.sku_id)<=sku_ids and set(fc.dc_id)<=stock and set(fc.b2b_customer_id)<=cust),
         ("production.plant_id in plants",set(prod.plant_id)<=set(plants.plant_id)),
         ("inventory.dc_id in stocking DCs",set(inv.dc_id)<=stock),
         ("lane endpoints in nodes",set(lanes.origin_node_id)<=nodes and set(lanes.destination_node_id)<=nodes),
         ("serving map dc in stocking DCs",set(serve.dc_id)<=stock),
         ("forward forecast has no actuals",fc[fc.horizon=='forward'].actual_qty.isna().all())]
    A(_tab(pd.DataFrame([[n,"PASS" if c else "FAIL"] for n,c in chk],columns=["check","result"]))+"\n")

    # 2.3 product & economics
    A("## 2.3 Product catalog and economics (USD, per-unit)\n")
    g=sku.groupby("category").agg(skus=("sku_id","count"),median_price=("list_price_usd","median"),
        min_price=("list_price_usd","min"),max_price=("list_price_usd","max"),avg_margin_pct=("margin_pct","mean"),
        median_shelf_life_d=("shelf_life_days","median")).reset_index()
    g.avg_margin_pct=(g.avg_margin_pct*100).round(1)
    A(_tab(g)+"\n")
    A("\nPrice tiers: "+", ".join(f"{k} {v}" for k,v in sku.price_tier.value_counts().items())+
      f". Seasonal-flagged SKUs: {int(sku.seasonal_flag.sum())}/{len(sku)}.\n")
    if HAVE_MPL:
        fig,ax=plt.subplots(figsize=(7,3.6)); sku.boxplot(column="list_price_usd",by="category",ax=ax,rot=20)
        ax.set_title("Per-unit list price by category (USD)"); ax.set_xlabel(""); ax.set_ylabel("USD"); plt.suptitle("")
        A("\n"+_chart(fig,OUT,"price_by_category.png")+"\n")

    # 2.4 segmentation & ABC
    A("## 2.4 SKU segmentation and ABC classification\n")
    seg=sku.segment.value_counts(); segr=orders.merge(sku[["sku_id","segment"]],on="sku_id").groupby("segment").revenue_usd.sum()
    st=pd.DataFrame({"segment":seg.index,"sku_count":seg.values,"sku_pct":(seg.values/len(sku)*100).round(1),
                     "revenue_pct":(segr.reindex(seg.index).values/segr.sum()*100).round(1)})
    A(_tab(st)+"\n")
    r=sku_rev.sort_values(ascending=False); cum=r.cumsum()/r.sum()
    cls=np.where(cum<=0.8,"A",np.where(cum<=0.95,"B","C"))
    abc=pd.DataFrame({"sku_id":r.index,"cls":cls,"rev":r.values})
    at=abc.groupby("cls").agg(skus=("sku_id","count"),rev=("rev","sum")).reset_index()
    at["sku_pct"]=(at.skus/at.skus.sum()*100).round(1); at["rev_pct"]=(at.rev/at.rev.sum()*100).round(1)
    A("\n**ABC by revenue (A=top 80pct, B=next 15pct, C=last 5pct):**\n"+_tab(at[["cls","skus","sku_pct","rev_pct"]])+"\n")
    top20=cum.iloc[int(0.2*len(cum))-1]*100
    A(f"\nConcentration: top 20 pct of SKUs = {top20:.1f} pct of revenue (steep, ~80/20).\n")
    if HAVE_MPL:
        fig,ax=plt.subplots(figsize=(6.5,3.4)); x=np.arange(1,len(cum)+1)/len(cum)*100
        ax.plot(x,cum.values*100,color="#1f77b4"); ax.axhline(80,color="grey",ls=":",lw=0.8); ax.axvline(20,color="grey",ls=":",lw=0.8)
        ax.set_title("Pareto: cumulative revenue vs SKU rank"); ax.set_xlabel("pct of SKUs"); ax.set_ylabel("cum pct revenue")
        A("\n"+_chart(fig,OUT,"abc_pareto.png")+"\n")

    # 2.5 demand analysis
    A("## 2.5 Demand analysis\n")
    o2=orders.merge(sku[["sku_id","category","segment"]],on="sku_id").merge(ban[["b2b_customer_id","customer_name","delivery_mode"]],on="b2b_customer_id")
    bc=o2.groupby("category").agg(units=("order_qty","sum"),revenue=("revenue_usd","sum")).reset_index()
    bc["rev_pct"]=(bc.revenue/bc.revenue.sum()*100).round(1); A("**By category:**\n"+_tab(bc.sort_values("revenue",ascending=False))+"\n")
    bm=o2.groupby("delivery_mode").agg(units=("order_qty","sum"),revenue=("revenue_usd","sum"),lines=("order_qty","count")).reset_index()
    A("\n**By fulfilment mode:**\n"+_tab(bm)+"\n")
    bb=o2.groupby("customer_name").revenue_usd.sum().sort_values(ascending=False).reset_index()
    A("\n**By customer (banner):**\n"+_tab(bb)+"\n")
    if HAVE_MPL:
        fig,ax=plt.subplots(figsize=(7,3.4)); bb2=bb.set_index("customer_name").revenue_usd
        ax.barh(bb2.index[::-1],bb2.values[::-1],color="#4C78A8"); ax.set_title("Revenue by customer (USD)")
        A("\n"+_chart(fig,OUT,"revenue_by_banner.png")+"\n")
        oc=orders.merge(sku[["sku_id","category"]],on="sku_id")
        fig,ax=plt.subplots(figsize=(8,3.6))
        for c in sorted(oc.category.unique()):
            s=oc[oc.category==c].groupby(oc._od.dt.to_period("W").dt.start_time).order_qty.sum()
            ax.plot(s.index,s.values,lw=1.1,label=c)
        ax.legend(fontsize=7); ax.set_title("Weekly demand by category (units) - seasonality"); ax.set_ylabel("units")
        A("\n"+_chart(fig,OUT,"seasonality_by_category.png")+"\n")

    # 2.6 demand variability
    A("## 2.6 Demand variability and intermittency\n")
    piv=ordw.pivot_table(index="sku_id",columns="period",values="order_qty",fill_value=0)
    cv=(piv.std(axis=1)/piv.mean(axis=1)).replace([np.inf,-np.inf],np.nan)
    intermit=(piv==0).mean(axis=1)
    vt=pd.DataFrame({"sku_id":piv.index,"cv":cv.values,"zero_week_frac":intermit.values}).merge(sku[["sku_id","segment"]],on="sku_id")
    vs=vt.groupby("segment").agg(avg_cv=("cv","mean"),avg_zero_week_pct=("zero_week_frac","mean")).reset_index()
    vs.avg_cv=vs.avg_cv.round(2); vs.avg_zero_week_pct=(vs.avg_zero_week_pct*100).round(1)
    A("Weekly demand coefficient of variation (CV) and intermittency by segment - the inputs safety-stock sizing keys off:\n")
    A(_tab(vs)+"\n")
    if HAVE_MPL:
        fig,ax=plt.subplots(figsize=(6.5,3.3)); ax.hist(cv.dropna(),bins=30,color="#72B7B2")
        ax.set_title("Distribution of per-SKU weekly demand CV"); ax.set_xlabel("CV"); A("\n"+_chart(fig,OUT,"demand_cv_hist.png")+"\n")

    # 2.7 promotions & daily orders
    A("## 2.7 Promotions and daily-order structure\n")
    cs=(promos.b2b_customer_id!="ALL").mean()*100
    lead=(pd.to_datetime(orders.delivery_date)-orders._od).dt.days
    A(_kv([("Promo events",f"{len(promos):,}"),("Customer-specific vs ALL",f"{cs:.0f} pct / {100-cs:.0f} pct"),
           ("Avg discount depth",f"{promos.discount_pct.mean()*100:.1f} pct"),
           ("Order lines on promo",f"{orders.on_promo.mean()*100:.1f} pct"),
           ("Avg lines/order",f"{len(orders)/orders.order_id.nunique():.1f}"),
           ("Avg delivery lead (days)",f"{lead.mean():.1f}")])+"\n")
    pl=o2.groupby(["category","on_promo"]).order_qty.mean().unstack()
    if True in pl.columns and False in pl.columns:
        pl2=pl.rename(columns={True:"on_promo_qty",False:"base_qty"}).copy()
        pl2["promo_lift_x"]=(pl2.on_promo_qty/pl2.base_qty).round(2)
        A("\n**Promo lift (avg line qty on-promo / off-promo), by category:**\n"+_tab(pl2.reset_index())+"\n")
    if HAVE_MPL:
        fig,ax=plt.subplots(figsize=(6.3,3.2)); ax.hist(orders.loc[orders.discount_pct>0,"discount_pct"]*100,bins=20,color="#E45756")
        ax.set_title("Promo discount depth (pct)"); ax.set_xlabel("pct off"); A("\n"+_chart(fig,OUT,"discount_depth.png")+"\n")

    # 2.8 price & elasticity
    A("## 2.8 Price variation and elasticity (base vs promotional)\n")
    A("Two-elasticity recovery from orders (no elasticity shipped): regress ln(order_qty) on ln(base_price_usd) "
      "and ln(1-discount) with SKU and week fixed effects. Base = shelf-price response; promo = deal response.\n")
    w=orders.groupby(["sku_id","dc_id","b2b_customer_id","period"],as_index=False).agg(
        q=("order_qty","sum"),bp=("base_price_usd","mean"),d=("discount_pct","mean")).merge(sku[["sku_id","category"]])
    w=w[(w.q>0)&(w.bp>0)]
    er=[]
    for c,gg in w.groupby("category"):
        keys=(gg.sku_id.values,gg.period.values); y=_demean(np.log(gg.q.values),keys)
        X=np.column_stack([_demean(np.log(gg.bp.values),keys),_demean(np.log(1-gg.d.values),keys)])
        b,*_=np.linalg.lstsq(X,y,rcond=None)
        er.append([c,round(float(b[0]),2),round(float(b[1]),2),round(float(b[1]/b[0]),2) if b[0] else np.nan,int(len(gg))])
    A(_tab(pd.DataFrame(er,columns=["category","base_elasticity","promo_elasticity","promo_over_base","n_obs"]))+"\n")

    # 2.9 forecast accuracy
    A("## 2.9 Forecast accuracy and forward horizon\n")
    ins=fc[fc.horizon=="in_sample"].merge(sku[["sku_id","category","segment"]],on="sku_id")
    ins=ins[ins.actual_qty>0].copy(); ins["ae"]=(ins.forecast_qty-ins.actual_qty).abs(); ins["ape"]=ins.ae/ins.actual_qty
    def acc(gcol):
        return ins.groupby(gcol).apply(lambda x:pd.Series({"MAPE_pct":x.ape.mean()*100,
            "WAPE_pct":x.ae.sum()/x.actual_qty.sum()*100,
            "bias_pct":(x.forecast_qty.sum()-x.actual_qty.sum())/x.actual_qty.sum()*100})).round(1).reset_index()
    A("**By segment:**\n"+_tab(acc("segment"))+"\n")
    A("\n**By category:**\n"+_tab(acc("category"))+"\n")
    histu=orders.order_qty.sum(); fwdu=fc[fc.horizon=="forward"].forecast_qty.sum()
    A(f"\nForward 2026 total forecast units {fwdu:,} vs 2025 actual {histu:,}, YoY {(fwdu/histu-1)*100:+.1f} pct.\n")
    if HAVE_MPL:
        fig,ax=plt.subplots(figsize=(6.5,3.3)); ax.hist(ins.forecast_error_pct.clip(-1,1)*100,bins=40,color="#F58518")
        ax.set_title("In-sample forecast error pct (clipped +/-100)"); ax.set_xlabel("(forecast-actual)/actual pct")
        A("\n"+_chart(fig,OUT,"forecast_error_hist.png")+"\n")
        hs=orders.groupby(orders._od.dt.to_period("W").dt.start_time).order_qty.sum()
        ffw=fc[fc.horizon=="forward"].assign(_dt=pd.to_datetime(fc[fc.horizon=="forward"].week_start_date)).groupby("_dt").forecast_qty.sum()
        fig,ax=plt.subplots(figsize=(9,3.6)); ax.plot(hs.index,hs.values,label="2025 actual",lw=1.2)
        ax.plot(ffw.index,ffw.values,"--",label="2026 forecast",lw=1.2); ax.legend(fontsize=8); ax.set_title("Actual vs forward forecast (weekly units)")
        A("\n"+_chart(fig,OUT,"hist_vs_forward.png")+"\n")

    # 2.10 production
    A("## 2.10 Production (planned vs actual)\n")
    A(_kv([("Planned units",f"{prod.planned_qty.sum():,}"),("Actual units",f"{prod.actual_qty.sum():,}"),
           ("Mean schedule adherence",f"{prod.adherence_pct.mean()*100:.1f} pct"),
           ("Weeks below 90 pct adherence",f"{(prod.adherence_pct<0.90).mean()*100:.1f} pct")])+"\n")
    bp=prod.groupby("plant_id").agg(planned=("planned_qty","sum"),actual=("actual_qty","sum"),adherence=("adherence_pct","mean")).reset_index()
    bp.adherence=(bp.adherence*100).round(1); A("\n"+_tab(bp)+"\n")
    if HAVE_MPL:
        fig,ax=plt.subplots(figsize=(6.3,3.2)); ax.hist(prod.adherence_pct*100,bins=25,color="#54A24B")
        ax.set_title("Manufacturing schedule adherence (pct)"); ax.set_xlabel("pct adherence"); A("\n"+_chart(fig,OUT,"adherence_hist.png")+"\n")

    # 2.11 inventory & service
    A("## 2.11 Inventory, service and working capital\n")
    invc=inv.merge(sku[["sku_id","unit_cost_usd"]],on="sku_id"); invc["inv_value"]=invc.closing_stock*invc.unit_cost_usd
    wk_val=invc.groupby("period").inv_value.sum()
    invc["ech"]=np.where(invc.dc_id.astype(str).str.startswith("RDC"),"Regional DC",
                 np.where(invc.dc_id.astype(str).str.startswith("CDC"),"Customer DC","Other"))
    def dos(x):
        wsh=x.shipments.mean()/7.0; return (x.closing_stock.mean()/wsh) if wsh>0 else np.nan
    ech=invc.groupby("ech").apply(lambda x:pd.Series({"avg_closing":x.closing_stock.mean(),"avg_safety":x.safety_stock.mean(),
        "days_of_supply":dos(x),"stockout_wk_pct":(x.closing_stock==0).mean()*100,"avg_inv_value_usd":x.inv_value.mean()})).round(1).reset_index()
    A(_tab(ech)+"\n")
    avg_units=inv.groupby("period").closing_stock.sum().mean(); turns=inv.shipments.sum()/avg_units if avg_units>0 else float("nan")
    avail=(1-(inv.closing_stock==0).mean())*100
    A(f"\nNetwork-wide: avg weekly inventory value ~ **${wk_val.mean():,.0f}** (working capital), annualized turns "
      f"~ **{turns:.1f}x**, availability ~ **{avail:.1f} pct** ({100-avail:.1f} pct of DC-SKU-weeks stock out).\n")
    if HAVE_MPL:
        fig,ax=plt.subplots(figsize=(6.5,3.2)); e=ech.set_index("ech").days_of_supply
        ax.bar(e.index,e.values,color="#B279A2"); ax.set_title("Avg days of supply by echelon"); ax.set_ylabel("days")
        A("\n"+_chart(fig,OUT,"days_of_supply.png")+"\n")
        ws=wk_val.reset_index(); ws["_dt"]=pd.to_datetime([week_start(int(p.split("-W")[1])) for p in ws.period]); ws=ws.sort_values("_dt")
        fig,ax=plt.subplots(figsize=(8,3.2)); ax.plot(ws._dt,ws.inv_value,color="#9D755D"); ax.set_title("Weekly inventory value (working capital, USD)")
        A("\n"+_chart(fig,OUT,"inventory_value.png")+"\n")

    # 2.12 network structure & cardinality
    A("## 2.12 Network structure and cardinality\n")
    A(_kv([("Plants",len(plants)),("Plant DCs",len(pdc)),("Regional DCs",len(rdc)),("Customer DCs",len(cdc)),
           ("Lanes",len(lanes)),("Transport modes",", ".join(sorted(lanes.transport_mode.unique())))])+"\n")
    card=[]
    for (ot,dt_),s in lanes.groupby(["origin_node_type","destination_node_type"]):
        fo=s.groupby("origin_node_id").destination_node_id.nunique(); fi=s.groupby("destination_node_id").origin_node_id.nunique()
        card.append([f"{ot} -> {dt_}",len(s),f"{fo.min()}-{fo.max()}",f"{fi.min()}-{fi.max()}"])
    A("\n**Cardinality per hop (fan-out per upstream, fan-in per downstream):**\n"+
      _tab(pd.DataFrame(card,columns=["hop","lanes","fan_out","fan_in"]))+"\n")
    cdc_src=lanes[lanes.destination_node_type=="CUSTOMER_DC"].groupby("destination_node_id").origin_node_id.nunique()
    n_lat=int(((lanes.origin_node_type=="REGIONAL_DC")&(lanes.destination_node_type=="REGIONAL_DC")).sum())
    A(f"\nDual-sourcing: {int((cdc_src>=2).sum())}/{len(cdc)} Customer DCs have >=2 inbound Regional DCs (failover); "
      f"lateral RDC-to-RDC lanes: {n_lat}.\n")
    lm=lanes.groupby(["origin_node_type","destination_node_type","transport_mode"]).agg(
        lanes=("lane_id","count"),avg_lead_d=("lead_time_mean_days","mean"),avg_mi=("distance_miles","mean"),
        avg_cost=("logistics_cost_per_unit_usd","mean")).round(2).reset_index()
    A("\n**Lanes by tier and mode:**\n"+_tab(lm)+"\n")

    # 2.13 capacity utilization
    A("## 2.13 Capacity utilization\n")
    capmap=pd.concat([rdc.rename(columns={"regional_dc_id":"dc_id"})[["dc_id","storage_capacity_units"]],
                      cdc.rename(columns={"customer_dc_id":"dc_id"})[["dc_id","storage_capacity_units"]]])
    du=inv.groupby("dc_id").closing_stock.mean().reset_index().merge(capmap,on="dc_id")
    du["util_pct"]=(du.closing_stock/du.storage_capacity_units*100).round(1)
    A(f"DC storage utilization (avg on-hand / capacity): mean {du.util_pct.mean():.1f} pct, max {du.util_pct.max():.1f} pct.\n")
    prim=lanes[lanes.destination_node_type=="CUSTOMER_DC"].sort_values("lead_time_mean_days").groupby("destination_node_id").first()
    cdc_ship=inv[inv.dc_id.astype(str).str.startswith("CDC")].groupby(["dc_id","period"]).shipments.sum().groupby("dc_id").mean()
    lu=pd.DataFrame({"dc_id":cdc_ship.index,"wk_flow":cdc_ship.values}).merge(
        prim[["capacity_units_per_week","logistics_cost_per_unit_usd"]].reset_index().rename(columns={"destination_node_id":"dc_id"}),on="dc_id")
    lu["util_pct"]=(lu.wk_flow/lu.capacity_units_per_week*100).round(1)
    A(f"\nLast-mile lane utilization (CDC weekly flow / primary inbound lane capacity): mean {lu.util_pct.mean():.1f} pct, "
      f"max {lu.util_pct.max():.1f} pct (approx).\n")
    pu=prod.groupby(["plant_id","period"]).actual_qty.sum().groupby("plant_id").mean().reset_index().merge(
        plants[["plant_id","production_capacity_units_per_week"]],on="plant_id")
    pu["util_pct"]=(pu.actual_qty/pu.production_capacity_units_per_week*100).round(1)
    A("\n**Plant production utilization (avg weekly output / capacity):**\n"+
      _tab(pu[["plant_id","actual_qty","production_capacity_units_per_week","util_pct"]])+"\n")
    if HAVE_MPL:
        fig,ax=plt.subplots(figsize=(7,3.2)); du2=du.sort_values("util_pct")
        ax.bar(du2.dc_id,du2.util_pct,color="#4C78A8"); ax.axhline(90,color="red",ls=":",lw=0.8)
        ax.set_title("DC storage utilization (pct)"); ax.tick_params(axis="x",rotation=90,labelsize=6); A("\n"+_chart(fig,OUT,"dc_utilization.png")+"\n")

    # 2.14 financials
    A("## 2.14 Financial summary (2025)\n")
    gm=rev-cogs; est_logi=float((lu.wk_flow*lu.logistics_cost_per_unit_usd).sum()*52)
    A(_kv([("Revenue",f"${rev:,.0f}"),("COGS (units x unit cost)",f"${cogs:,.0f}"),
           ("Gross margin",f"${gm:,.0f} ({gm/rev*100:.1f} pct)"),
           ("Avg working capital (inventory value)",f"${wk_val.mean():,.0f}"),
           ("Inventory turns (annualized)",f"{turns:.1f}x"),
           ("Est. last-mile logistics cost (approx/yr)",f"${est_logi:,.0f}")])+"\n")

    # 2.15 key findings
    A("## 2.15 Key findings\n")
    top_cat=bc.sort_values("revenue",ascending=False).iloc[0]
    most_el=min(er,key=lambda x:x[1]); least_el=max(er,key=lambda x:x[1])
    findings=[
      f"Revenue is led by {top_cat['category']} ({top_cat['rev_pct']} pct of sales); demand is steeply concentrated (top 20 pct SKUs ~ {top20:.0f} pct of revenue).",
      f"Most price-elastic category (base): {most_el[0]} ({most_el[1]}); least: {least_el[0]} ({least_el[1]}). Promo elasticity exceeds base in {sum(1 for e in er if e[2]<=e[1])}/{len(er)} categories.",
      f"Forecast accuracy degrades from fast-movers to seasonal SKUs (see 2.9); forward year grows ~{(fwdu/histu-1)*100:.0f} pct YoY.",
      f"Network is fully dual-sourced ({int((cdc_src>=2).sum())}/{len(cdc)} Customer DCs with failover) with {n_lat} lateral lanes; storage utilization averages {du.util_pct.mean():.0f} pct.",
      f"Availability ~ {avail:.0f} pct at ${wk_val.mean():,.0f} avg working capital and {turns:.1f}x turns.",
    ]
    A("\n".join(f"- {x}" for x in findings)+"\n")
    A("\n---\n*End of report.*\n")

    with open(f"{OUT}/EDA_REPORT.md","w") as f: f.write("\n".join(m)+"\n")
    print(f"  EDA report written: {OUT}/EDA_REPORT.md  ({'charts on' if HAVE_MPL else 'tables only'})")


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
    print("\n=== STEP 7: comprehensive EDA report ===")
    generate_eda(OUTPUT_DIR)
    print(f"\nAll tables + docs + EDA written to: {OUTPUT_DIR}\nTotal runtime: {time.time()-t0:.1f}s")
    return ok


if __name__ == "__main__":
    main()
