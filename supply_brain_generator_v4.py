"""
Supply Brain -- Synthetic Data Generator v4 (single-file, standalone, no LLM, all costs in USD)
================================================================================================
Generates a fully-synthetic, referentially-consistent CPG supply-chain dataset that behaves like
a real client extract, PLUS a forward planning-forecast horizon used to execute MEIO and the other
Supply Brain features. Running this one file produces every CSV table AND the documentation.

WHAT'S NEW IN v4 (vs v3) -- see input datasets/V4_IMPLEMENTATION_PLAN.md
------------------------------------------------------------------------
PHASE 0 (masters) and the demand-model wiring are implemented in this revision:

  WS-A  PER-UNIT PRICES. v3 inherited CASE prices from the source dataset under a per-unit label
        (Energy Drink median $108/unit). Every subcategory now carries UNITS_PER_CASE; the drawn
        figure becomes `case_price_usd` and `list_price_usd = case_price / units_per_case`.
        unit_cost / margin / holding cost all inherit the corrected basis.
  WS-D  SUBCATEGORY SEASONALITY. v3 gave all 5 categories one Sep-Nov shape (mean pairwise shape
        correlation 0.825). Seasonality now lives at SUBCATEGORY level, with genuinely
        anti-correlated pairs (hot Tea peaks winter vs Energy Drink summer, both "Beverages") and
        complement clusters (Flour/Yeasts/Sugar/Spices co-peak Oct-Dec on the baking occasion).
        Emitted as seasonality_master.csv.
  WS-F  REGIONAL DEMAND. v3's five regions were statistically identical (min pairwise
        subcategory-mix correlation 0.984). regional_demand_index.csv now sets region x
        subcategory consumption multipliers (sweet-tea Southeast, coffee Northeast, baking
        Midwest, wine West/Northeast, manioc/cassava Southeast+Southwest), renormalised so
        national volume is preserved.
  WS-G  CUSTOMER-SPECIFIC NET PRICING. List price stays national (Robinson-Patman); customer
        differentiation happens below list via trade terms, as in real CPG. b2b_customer_terms.csv
        gives each banner an EDLC / HIGH_LOW / CONTRACT strategy with off-invoice, trade-fund,
        volume-rebate and logistics-allowance rates. orders now carries a price waterfall down to
        net_price_usd (pocket price), and the demand model responds to NET price.
  WS-E  MULTI-SOURCING BY PRODUCTION FAMILY. v3 assigned plants round-robin by index (24.4%
        geographic match, every SKU single-sourced). SKUs now belong to a production family
        (LIQUID_FILL / DRY_BLEND / CONFECTIONERY / BEVERAGE_DRY / FROZEN); plants have capability
        sets; SKUs source from the nearest CAPABLE plant, with A-items dual-sourced.
        Emits plant_capability.csv and sku_plant_sourcing.csv.
  Housekeeping: `seasonal_flag` dropped (was 100% collinear with segment=='Seasonal'); holding
        cost single-sourced from cost_parameters; the Canned Sweet hierarchy break fixed (Frozen
        Foods gets its own subcategories); `brand` is now category-owned with a price position;
        the shelf-price band is widened so BASE elasticity is identifiable.
  WS-J multi-year 2023-24 burn-in + ex-ante baseline/lift forecast; WS-C forward promo calendar;
  WS-H lead time in the inventory sim; WS-I replenishment receipts/OTD; WS-K FEFO + write-offs;
  WS-L unmet demand; WS-M complete in-sample panel; WS-B scenario re-dating.
  Until those land, steps 2b-3 keep v3 behaviour and the known v3 caveats still apply to the
  forecast and inventory tables.

SHELF-LIFE PROFILE (produces the two requested datasets)
--------------------------------------------------------
  SHELF_LIFE_PROFILE = "realistic"  -> v4.1, true CPG shelf lives incl. 5-21 day Dairy & Bakery
  SHELF_LIFE_PROFILE = "relaxed"    -> v4.2, every SKU 1095-1460 days (3-4 years). That exceeds
                                       the 156-week simulated span, so nothing can expire and the
                                       algorithms can be developed with perishability switched off
  Set via the SUPPLY_BRAIN_SHELF_PROFILE env var, or run `python supply_brain_generator_v4.py both`
  to produce both datasets in one go.
  
INHERITED FROM v3
-----------------
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
import sys
from collections import deque
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

# ---------------------------------------------------------------------------
# SHELF-LIFE PROFILE -- selects which of the two datasets this run produces.
#   "realistic": true CPG shelf lives (Dairy & Bakery 5-21 days) -> "input datasets v4.1"
#   "relaxed"  : every SKU 365-730 days, perishability never binds -> "input datasets v4.2"
# The relaxed variant exists so the MEIO / service-level / buffer algorithms can be developed
# and tested WITHOUT the shelf-life constraint confounding results; switch to realistic once
# the algorithms are stable to bring perishability back in.
# ---------------------------------------------------------------------------
SHELF_LIFE_PROFILE = os.environ.get("SUPPLY_BRAIN_SHELF_PROFILE", "realistic")
_PROFILE_DIR = {"realistic": "input datasets v4.1", "relaxed": "input datasets"}
if SHELF_LIFE_PROFILE not in _PROFILE_DIR:
    raise SystemExit(f"SHELF_LIFE_PROFILE must be one of {list(_PROFILE_DIR)}, got {SHELF_LIFE_PROFILE!r}")
OUTPUT_DIR = os.environ.get("SUPPLY_BRAIN_OUTPUT",
                            os.path.join(BASE_DIR, _PROFILE_DIR[SHELF_LIFE_PROFILE]))
# Every SKU gets a shelf life in this band when the profile is "relaxed": 3-4 years, which
# EXCEEDS the 156-week (1,092-day) simulated span, so stock can never age out and the
# perishability constraint is guaranteed inert. A 1-2 year band was tried first and still bound
# for the very slowest movers over three years.
RELAXED_SHELF_LIFE_DAYS = (1095, 1460)

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

# WS-J: a seasonal forecast needs >=2 prior cycles, so 2023-24 are generated as BURN-IN history.
# v3 had only 2025, which is why no method could see seasonality and every segment collapsed to
# roughly the same error. Layout: 2023-24 burn-in | 2025 evaluated | 2026 forward.
BURN_WEEKS = 104                         # 2023 + 2024, warm-up for the forecaster
HIST_WEEKS = 52                          # 2025, the evaluated actuals year
FWD_WEEKS = 52                           # 2026 forward planning horizon
ACTUAL_WEEKS = BURN_WEEKS + HIST_WEEKS   # 156 weeks of real demand (orders)
TOTAL_WEEKS = ACTUAL_WEEKS + FWD_WEEKS   # 208
WEEK1_MONDAY = pd.Timestamp("2023-01-02")
YEARS = [2023, 2024, 2025, 2026]
YEAR_TREND = {2023: 0.00, 2024: 0.04, 2025: 0.05, 2026: 0.05}   # per-year level growth
SHAPE_JITTER = 0.08                      # +/-8% perturbation of each year's seasonal shape
YOY_GROWTH = 0.05                        # retained for the forward level projection
# WS-J structural events: without them, a repeated seasonal shape makes seasonal-naive trivially
# accurate and the forecast becomes leak-free but degenerate in a new way.
P_DIST_CHANGE = 0.05                     # SKU gains/loses a banner mid-history
P_PRICE_STEP = 0.03                      # SKU takes a permanent step price change
P_LATE_LAUNCH = 0.02                     # SKU launches mid-history

CATEGORIES = ["Beverages", "Snacks & Confectionery", "Dairy & Bakery", "Frozen Foods", "Grocery"]
SKUS_PER_CATEGORY = 100

# ---------------------------------------------------------------------------
# REAL-GROUNDED PARAMETERS -- derived from the external "Food and Beverage Sales" dataset
# (Product.xlsx + SalesData.xlsx), 2020 EXCLUDED (COVID spike stripped). Prices are PER-UNIT USD.
# The 5 synthetic categories are repriced/reseasonalized by mapping each to real product groups;
# each SKU inherits its group's per-unit price distribution and RAW empirical price elasticity.
# Seasonality uses the real calendar SHAPE from mature-year 2020 (Sep-Nov peak). See REALDATA_OPPORTUNITIES.md.
# ---------------------------------------------------------------------------
# WS-housekeeping (§9.3): v3 gave Frozen Foods ["Vegetable", "Canned Sweet"], which put
# "Canned Sweet" under BOTH Frozen Foods and Snacks & Confectionery -- so subcategory was not
# nested inside category and any subcategory rollup double-counted. Frozen Foods now has its own
# two subcategory names, which still inherit the real source-group price/elasticity parameters.
CATEGORY_GROUP_MAP = {
    "Beverages": ["Liquor", "Dry Wine", "Red Wine", "Imported Wine", "Energy Drink", "Tea"],
    "Snacks & Confectionery": ["Candy", "Popcorn", "Canned Sweet"],
    # v3's "Dairy & Bakery" held ONLY dry shelf-stable goods (powdered milk, flour, yeast) yet was
    # given a 5-21 day shelf life -- flour that expires in 5 days, and cannot survive the 7.7-day
    # lane to a far CDC. Fresh Dairy and Bakery added so the category name is honest and so the
    # perishability constraint has genuinely short-life products to bind on.
    "Dairy & Bakery": ["Fresh Dairy", "Bakery", "Powdered Milk", "Wheat Flour", "Flour", "Yeasts"],
    "Grocery": ["Oil", "Sugar", "Spices", "Manioc Flour", "Cassava starch", "Ground Coffee", "Coffee in Capsules", "Tomato Sauce"],
    "Frozen Foods": ["Frozen Vegetable", "Frozen Dessert"],
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
    # Frozen Foods' own subcategories, inheriting the real source-group parameters they were
    # previously borrowing (Vegetable, Canned Sweet) -- see CATEGORY_GROUP_MAP note above.
    "Frozen Vegetable": dict(n=40, mu=1.9404, sigma=0.4511, lo=2.80, hi=17.38, elast=-4.488),
    "Frozen Dessert": dict(n=7, mu=1.2689, sigma=0.3196, lo=1.65, hi=4.41, elast=-3.412),
    # fresh tier (no source group; priced/parameterised from US retail norms and labelled as an
    # assumption -- fluid dairy and packaged bakery are mid-elasticity staples)
    "Fresh Dairy": dict(n=25, mu=1.20, sigma=0.35, lo=1.20, hi=8.00, elast=-1.850),
    "Bakery": dict(n=20, mu=1.05, sigma=0.40, lo=1.00, hi=7.00, elast=-2.100),
}

# WS-K: shelf life belongs at SUBCATEGORY level, like seasonality -- a category band cannot be
# right for a group holding both fluid dairy and bagged flour.
SUBCATEGORY_SHELF_LIFE = {
    "Liquor": (1095, 3650), "Dry Wine": (730, 3650), "Red Wine": (730, 3650),
    "Imported Wine": (730, 3650), "Energy Drink": (270, 540), "Tea": (540, 1095),
    "Candy": (180, 365), "Popcorn": (150, 300), "Canned Sweet": (365, 730),
    "Fresh Dairy": (14, 30), "Bakery": (12, 24),                 # the genuinely perishable tier
    "Powdered Milk": (365, 730), "Wheat Flour": (240, 365), "Flour": (240, 365),
    "Yeasts": (540, 730), "Oil": (365, 540), "Sugar": (730, 1095), "Spices": (540, 1095),
    "Manioc Flour": (240, 365), "Cassava starch": (365, 730), "Ground Coffee": (270, 540),
    "Coffee in Capsules": (365, 540), "Tomato Sauce": (540, 730),
    "Frozen Vegetable": (365, 540), "Frozen Dessert": (365, 540),
}

# ---------------------------------------------------------------------------
# WS-A: pack structure. The GROUP_PARAMS draws above are CASE / multipack prices inherited from
# the source dataset. v3 labelled them per-unit, which put Energy Drink at a $108 "unit" price and
# propagated into unit_cost, margin_usd, holding cost and every inventory valuation.
#   case_price_usd = the GROUP_PARAMS draw
#   list_price_usd = case_price_usd / units_per_case      <- the true per-unit price
# ---------------------------------------------------------------------------
# NOTE: only SOME source groups carry case/multipack prices -- the rest were already per-unit.
# Applying a blanket divisor drove Yeasts and Oil to a few cents. Divisor is therefore 1 wherever
# the source median was already plausible as a consumer unit price, and >1 only for the groups
# whose median was clearly a case (Energy Drink $108, Imported Wine $69, Manioc Flour $38,
# Candy $32). Liquor stays at 1: a ~$32 median is a realistic per-BOTTLE spirits price.
UNITS_PER_CASE = {
    "Liquor": 1, "Dry Wine": 1, "Red Wine": 1, "Imported Wine": 6,
    "Energy Drink": 24, "Tea": 6, "Candy": 12, "Popcorn": 1, "Canned Sweet": 1,
    "Powdered Milk": 1, "Wheat Flour": 1, "Flour": 2, "Yeasts": 1, "Oil": 1,
    "Sugar": 1, "Spices": 2, "Manioc Flour": 10, "Cassava starch": 12,
    "Ground Coffee": 6, "Coffee in Capsules": 1, "Tomato Sauce": 1,
    "Frozen Vegetable": 1, "Frozen Dessert": 1, "Fresh Dairy": 1, "Bakery": 1,
}
# validate() asserts the per-subcategory MEDIAN sits in this band -- that catches the real defect
# (a $108 median "unit" price) while still allowing a premium spirits tail above it.
SUBCAT_MEDIAN_PRICE_BAND = (0.50, 40.00)
# documented exceptions: distilled spirits and imported wine are sold by the bottle at prices well
# above the grocery band, so they are asserted against their own range rather than relaxing it.
PREMIUM_SUBCATS = ("Liquor", "Imported Wine")
PREMIUM_PRICE_BAND = (5.00, 120.00)

# ---------------------------------------------------------------------------
# WS-D: seasonality at SUBCATEGORY level (monthly index, Jan..Dec, mean ~1).
# v3 gave all five CATEGORIES one Sep-Nov shape (mean pairwise correlation 0.825). Real CPG
# seasonality is driven by consumption OCCASION, which lives at subcategory level -- and
# subcategories inside one category are often anti-correlated (hot Tea peaks winter, Energy Drink
# peaks summer, both "Beverages"), while complements co-peak ACROSS categories (Flour, Yeasts,
# Sugar and Spices all spike Oct-Dec on the holiday-baking occasion).
# ---------------------------------------------------------------------------
SUBCATEGORY_SEASONALITY_MONTHLY = {
    # Beverages
    "Liquor":             [0.82, 0.85, 0.92, 0.95, 1.05, 1.05, 1.08, 1.02, 0.98, 1.02, 1.18, 1.58],
    "Dry Wine":           [0.85, 0.90, 0.92, 0.95, 1.00, 1.00, 1.02, 1.00, 1.00, 1.05, 1.25, 1.46],
    "Red Wine":           [0.90, 0.95, 0.95, 0.92, 0.92, 0.88, 0.85, 0.88, 0.98, 1.10, 1.30, 1.57],
    "Imported Wine":      [0.82, 0.88, 0.90, 0.92, 0.95, 0.95, 0.98, 0.95, 1.00, 1.10, 1.35, 1.60],
    "Energy Drink":       [0.85, 0.88, 0.95, 1.02, 1.12, 1.22, 1.28, 1.20, 1.02, 0.92, 0.85, 0.82],
    "Tea":                [1.28, 1.22, 1.10, 0.98, 0.88, 0.78, 0.75, 0.78, 0.92, 1.08, 1.20, 1.28],
    # Snacks & Confectionery
    "Candy":              [0.78, 0.95, 0.92, 0.95, 0.85, 0.82, 0.82, 0.90, 1.00, 1.72, 1.05, 1.28],
    "Popcorn":            [1.05, 1.20, 0.95, 0.92, 0.92, 0.95, 1.00, 0.95, 0.98, 1.05, 1.08, 1.15],
    "Canned Sweet":       [0.85, 0.85, 0.90, 0.92, 0.92, 0.90, 0.90, 0.92, 1.00, 1.18, 1.55, 1.31],
    # Dairy & Bakery
    "Powdered Milk":      [1.00, 0.98, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.01, 1.02, 0.99],
    "Wheat Flour":        [0.88, 0.85, 0.90, 0.95, 0.90, 0.85, 0.82, 0.88, 1.00, 1.22, 1.68, 1.37],
    "Flour":              [0.88, 0.85, 0.90, 0.95, 0.90, 0.85, 0.82, 0.88, 1.00, 1.22, 1.68, 1.37],
    "Yeasts":             [0.90, 0.88, 0.92, 0.95, 0.90, 0.85, 0.82, 0.88, 1.00, 1.20, 1.62, 1.38],
    # Grocery
    "Oil":                [0.92, 0.90, 0.95, 0.98, 1.00, 0.98, 0.98, 0.98, 1.02, 1.10, 1.25, 1.15],
    "Sugar":              [0.90, 0.88, 0.92, 0.96, 0.95, 0.90, 0.88, 0.90, 1.00, 1.18, 1.55, 1.28],
    "Spices":             [0.82, 0.82, 0.88, 0.90, 0.90, 0.85, 0.85, 0.90, 1.00, 1.25, 1.95, 1.28],
    "Manioc Flour":       [1.00, 0.98, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.01, 1.02, 0.99],
    "Cassava starch":     [1.00, 0.98, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.01, 1.02, 0.99],
    "Ground Coffee":      [1.12, 1.08, 1.02, 0.98, 0.92, 0.88, 0.85, 0.90, 1.00, 1.05, 1.08, 1.12],
    "Coffee in Capsules": [1.08, 1.05, 1.02, 1.00, 0.95, 0.92, 0.90, 0.93, 1.00, 1.04, 1.06, 1.05],
    "Tomato Sauce":       [1.12, 1.10, 1.05, 0.98, 0.92, 0.88, 0.88, 0.90, 1.00, 1.08, 1.05, 1.04],
    # Frozen Foods
    "Frozen Vegetable":   [1.15, 1.12, 1.05, 0.98, 0.90, 0.85, 0.82, 0.85, 0.95, 1.05, 1.15, 1.13],
    "Frozen Dessert":     [0.88, 0.88, 0.95, 1.02, 1.12, 1.22, 1.28, 1.22, 1.05, 0.92, 0.85, 0.85],
    # fresh tier: near-flat staples (this is what makes them the easy-to-forecast SKUs)
    "Fresh Dairy":        [1.02, 1.00, 1.00, 1.00, 1.00, 0.98, 0.97, 0.98, 1.01, 1.01, 1.02, 1.01],
    "Bakery":             [0.97, 0.96, 0.99, 1.00, 1.00, 0.99, 0.98, 0.99, 1.01, 1.02, 1.06, 1.03],
}
# a gentle shared US-food calendar effect on top of the occasion curves (mean ~1)
MACRO_CALENDAR_MONTHLY = [0.96, 0.96, 0.99, 1.00, 1.01, 1.00, 1.00, 1.00, 1.01, 1.02, 1.05, 1.04]

# ---------------------------------------------------------------------------
# WS-F: regional consumption index (region x subcategory multiplier, ~1.0 = national average).
# v3's five regions were statistically identical (min pairwise subcategory-mix correlation 0.984),
# so there was no reason to position different stock at different RDCs. Grounded in US CPG
# consumption patterns; renormalised at build time so national volume is unchanged.
# ---------------------------------------------------------------------------
REGION_DEMAND_INDEX = {
    #                     Northeast Southeast Midwest Southwest  West
    "Tea":               dict(Northeast=0.90, Southeast=1.55, Midwest=0.85, Southwest=1.05, West=0.75),
    "Ground Coffee":     dict(Northeast=1.35, Southeast=0.80, Midwest=1.00, Southwest=0.85, West=1.20),
    "Coffee in Capsules":dict(Northeast=1.30, Southeast=0.85, Midwest=0.95, Southwest=0.85, West=1.25),
    "Spices":            dict(Northeast=0.85, Southeast=1.10, Midwest=0.80, Southwest=1.45, West=1.05),
    "Wheat Flour":       dict(Northeast=0.85, Southeast=1.15, Midwest=1.30, Southwest=1.00, West=0.80),
    "Flour":             dict(Northeast=0.85, Southeast=1.15, Midwest=1.30, Southwest=1.00, West=0.80),
    "Yeasts":            dict(Northeast=0.85, Southeast=1.10, Midwest=1.30, Southwest=1.00, West=0.85),
    "Sugar":             dict(Northeast=0.90, Southeast=1.20, Midwest=1.20, Southwest=1.00, West=0.80),
    "Energy Drink":      dict(Northeast=0.85, Southeast=1.00, Midwest=0.90, Southwest=1.20, West=1.30),
    "Dry Wine":          dict(Northeast=1.40, Southeast=0.65, Midwest=0.85, Southwest=0.75, West=1.50),
    "Red Wine":          dict(Northeast=1.40, Southeast=0.65, Midwest=0.85, Southwest=0.75, West=1.50),
    "Imported Wine":     dict(Northeast=1.45, Southeast=0.60, Midwest=0.80, Southwest=0.70, West=1.50),
    "Liquor":            dict(Northeast=1.20, Southeast=0.90, Midwest=1.00, Southwest=0.85, West=1.10),
    "Frozen Vegetable":  dict(Northeast=1.10, Southeast=0.85, Midwest=1.25, Southwest=0.90, West=0.95),
    "Frozen Dessert":    dict(Northeast=0.95, Southeast=1.20, Midwest=1.00, Southwest=1.05, West=1.00),
    "Manioc Flour":      dict(Northeast=0.75, Southeast=1.50, Midwest=0.55, Southwest=1.35, West=1.05),
    "Cassava starch":    dict(Northeast=0.75, Southeast=1.45, Midwest=0.55, Southwest=1.35, West=1.05),
    "Tomato Sauce":      dict(Northeast=1.40, Southeast=0.85, Midwest=1.00, Southwest=0.90, West=0.95),
    "Oil":               dict(Northeast=0.90, Southeast=1.20, Midwest=1.00, Southwest=1.15, West=0.85),
    "Candy":             dict(Northeast=1.05, Southeast=1.00, Midwest=1.10, Southwest=0.95, West=0.90),
    "Popcorn":           dict(Northeast=1.00, Southeast=1.00, Midwest=1.15, Southwest=0.95, West=0.95),
    "Canned Sweet":      dict(Northeast=0.95, Southeast=1.15, Midwest=1.05, Southwest=0.95, West=0.95),
    "Powdered Milk":     dict(Northeast=1.00, Southeast=1.00, Midwest=1.00, Southwest=1.00, West=1.00),
    "Fresh Dairy":       dict(Northeast=1.05, Southeast=0.95, Midwest=1.15, Southwest=0.95, West=0.95),
    "Bakery":            dict(Northeast=1.15, Southeast=0.95, Midwest=1.05, Southwest=0.95, West=0.95),
}

# ---------------------------------------------------------------------------
# WS-E: production families. Plants are built around a PROCESS technology -- a bottling line
# cannot make candy -- so SKUs are grouped by process and sourced from the nearest CAPABLE plant.
# v3 assigned plants round-robin by index (24.4% geographic match, every SKU single-sourced).
# ---------------------------------------------------------------------------
PRODUCTION_FAMILY = {
    "LIQUID_FILL":   ["Liquor", "Dry Wine", "Red Wine", "Imported Wine", "Energy Drink", "Oil", "Tomato Sauce"],
    "DRY_BLEND":     ["Wheat Flour", "Flour", "Yeasts", "Sugar", "Manioc Flour", "Cassava starch",
                      "Powdered Milk", "Spices"],
    "CONFECTIONERY": ["Candy", "Popcorn", "Canned Sweet"],
    "BEVERAGE_DRY":  ["Ground Coffee", "Coffee in Capsules", "Tea"],
    "FROZEN":        ["Frozen Vegetable", "Frozen Dessert"],
    "CHILLED":       ["Fresh Dairy", "Bakery"],
}
FAMILY_OF_SUBCAT = {sc: fam for fam, scs in PRODUCTION_FAMILY.items() for sc in scs}
PLANT_CAPABILITY = {                       # each family producible at 2-3 plants -> dual sourcing
    "PLANT01": ["LIQUID_FILL", "CONFECTIONERY", "CHILLED"],      # Atlanta
    "PLANT02": ["DRY_BLEND", "LIQUID_FILL", "CHILLED"],          # Dallas
    "PLANT03": ["DRY_BLEND", "FROZEN", "BEVERAGE_DRY", "CHILLED"],   # Chicago
    "PLANT04": ["LIQUID_FILL", "FROZEN", "BEVERAGE_DRY", "CONFECTIONERY"],   # Los Angeles
}
SECONDARY_COST_PENALTY = (0.02, 0.05)      # secondary plant costs 2-5% more -> real trade-off

# ---------------------------------------------------------------------------
# WS-G: customer trade terms. List price stays NATIONAL (Robinson-Patman Act 1936 restricts
# discriminating on list between competing buyers). Real CPG differentiation happens BELOW list
# via trade spend, so each banner gets off-invoice / trade-fund / rebate / logistics rates and a
# pricing strategy. The resulting "pocket price" is what the demand model responds to.
#   EDLC     = everyday low cost (mass, club): low everyday net, little promo funding
#   HIGH_LOW = conventional grocery: higher everyday, deep frequent promos
#   CONTRACT = foodservice: flat negotiated net, no promotions
# Negative logistics_allowance = DSD service premium (small frequent drops cost more to serve,
# so the manufacturer realises a HIGHER net price).
# ---------------------------------------------------------------------------
CUSTOMER_TERMS = {
    # banner:      (strategy,    off_invoice, trade_fund, vol_rebate, logistics, promo_freq_x, promo_depth_x)
    "BNR01": ("EDLC",     0.080, 0.020, 0.030,  0.015, 0.6, 0.7),   # MegaMart (mass)
    "BNR02": ("EDLC",     0.110, 0.005, 0.035,  0.025, 0.3, 0.6),   # ValueClub (club)
    "BNR03": ("HIGH_LOW", 0.030, 0.060, 0.015,  0.010, 1.3, 1.2),   # GreenGrocer
    "BNR04": ("HIGH_LOW", 0.035, 0.065, 0.020,  0.010, 1.4, 1.3),   # BullsEye
    # c-stores take the smallest deductions of any channel, so they realise the HIGHEST pocket
    # price -- that is the DSD service premium. (Modelled as near-zero allowances rather than a
    # negative one, so the waterfall stays monotone: net <= invoice <= base.)
    "BNR05": ("HIGH_LOW", 0.010, 0.020, 0.000,  0.000, 0.8, 0.9),   # QuickStop (c-store)
    "BNR06": ("HIGH_LOW", 0.025, 0.070, 0.010,  0.005, 1.5, 1.4),   # SunBelt
    "BNR07": ("HIGH_LOW", 0.010, 0.020, 0.000,  0.000, 0.8, 0.9),   # Coastal Marts
    "BNR08": ("HIGH_LOW", 0.005, 0.015, 0.000,  0.000, 0.7, 0.9),   # MetroPetro (c-store)
    "BNR09": ("CONTRACT", 0.060, 0.000, 0.010,  0.000, 0.0, 0.0),   # CampusDining (foodservice)
    "BNR10": ("HIGH_LOW", 0.020, 0.060, 0.010,  0.005, 1.4, 1.3),   # FreshFoods
}

# WS-housekeeping (§9.4): brand was f"Brand{1+i%8}", so all 8 brands appeared in all 5 categories
# with no price position (eta-squared on price 0.022). Brands are now category-owned with a tier.
# Premium-to-value gaps in CPG are large (a premium brand commonly carries a 60-80% price
# premium over a value label in the same subcategory), so the tier multipliers are wide enough
# for brand to be a real price signal rather than noise.
CATEGORY_BRANDS = {
    "Beverages":              [("Crestline", 1.60), ("Harborview", 1.00), ("Bluepeak", 0.62)],
    "Snacks & Confectionery": [("Goldcrisp", 1.60), ("Snackwell", 0.99), ("Pennywise", 0.62)],
    "Dairy & Bakery":         [("Meadowlane", 1.60), ("Bakerstone", 1.00), ("Dailybest", 0.62)],
    "Frozen Foods":           [("Glacierpeak", 1.60), ("Frostfield", 1.00), ("Chillmart", 0.62)],
    "Grocery":                [("Pantrycraft", 1.60), ("Homestead", 1.00), ("Thriftrow", 0.62)],
}
# (v3's CATEGORY_SEASONALITY_MONTHLY removed -- superseded by
#  SUBCATEGORY_SEASONALITY_MONTHLY above, see WS-D.)
# margin stays a modelled assumption (real data has no cost): unit_cost = price x cost_ratio
# v3's cost ratios implied a ~32% gross margin on list, which cannot absorb the ~11% trade
# deductions plus promotional depth introduced in v4: 43% of volume ended up priced BELOW unit
# cost and 89 of 500 SKUs showed a negative net margin. Branded CPG gross margin is 40-50% on
# list precisely so that trade spend fits inside it.
CATEGORY_COST_RATIO = {"Beverages": 0.55, "Snacks & Confectionery": 0.53, "Dairy & Bakery": 0.58,
                       "Frozen Foods": 0.57, "Grocery": 0.54}
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


def woy(idx):                       # 1..208 -> week-of-year 1..52
    return ((idx - 1) % 52) + 1
def year_of(idx):                   # 2023, 2024, 2025, 2026
    return 2023 + (idx - 1) // 52
def horizon_of(idx):
    return "burn_in" if idx <= BURN_WEEKS else ("in_sample" if idx <= ACTUAL_WEEKS else "forward")
def period_label(idx):
    return f"{year_of(idx)}-W{woy(idx):02d}"
def week_start(idx):
    return (WEEK1_MONDAY + pd.Timedelta(weeks=idx - 1)).strftime("%Y-%m-%d")


# ============================================================================
# STEP 1: masters (USD economics, Coke-scaled network, customers, lanes)
# ============================================================================
def step1_masters():
    RNG = np.random.default_rng(2025)
    # Shelf life is drawn from its OWN stream so that switching SHELF_LIFE_PROFILE does not
    # perturb any other draw. v4.1 and v4.2 are then identical in every respect EXCEPT shelf
    # life, which is what makes them a clean A/B for testing algorithms with and without the
    # perishability constraint.
    SHELF_RNG = np.random.default_rng(31337)
    OUT = OUTPUT_DIR

    # seasonality shapes: zero-mean weekly wave per category (52 weeks)
    # real-calendar seasonality: calendar-month index (0..11) for each of the 52 historical weeks
    week_month = np.array([(WEEK1_MONDAY + pd.Timedelta(weeks=k)).month - 1 for k in range(TOTAL_WEEKS)])

    seg_pool = []
    for lab, frac in SEGMENT_MIX:
        seg_pool += [lab] * round(SKUS_PER_CATEGORY * frac)
    seg_pool = (seg_pool + ["Fast-Moving"] * SKUS_PER_CATEGORY)[:SKUS_PER_CATEGORY]

    macro = np.array(MACRO_CALENDAR_MONTHLY)
    sku_rows = []
    seasonal_index = {}          # sku_id -> np.array(TOTAL_WEEKS), seasonality x per-year trend
    sku_events = {}              # sku_id -> structural events (distribution / price / launch)
    sku_meta = {}                # sku_id -> internal dict (incl. hidden elasticity)
    n = 1
    for cat in CATEGORIES:
        groups = CATEGORY_GROUP_MAP[cat]
        gweights = np.array([GROUP_PARAMS[g]["n"] for g in groups], dtype=float); gweights /= gweights.sum()
        cost_ratio = CATEGORY_COST_RATIO[cat]
        brands = CATEGORY_BRANDS[cat]
        segs = seg_pool.copy(); RNG.shuffle(segs)
        for i in range(SKUS_PER_CATEGORY):
            sid = f"SKU{n:04d}"; n += 1
            seg = segs[i]
            grp = str(RNG.choice(groups, p=gweights))                 # assign SKU to a real product group
            gp = GROUP_PARAMS[grp]
            # --- WS-A: the GROUP_PARAMS draw is a CASE price; divide to get the true per-unit price
            case_price = round(float(np.clip(np.exp(RNG.normal(gp["mu"], gp["sigma"])), gp["lo"], gp["hi"])), 2)
            upc = UNITS_PER_CASE[grp]
            # --- brand positioning (was f"Brand{1+i%8}", which carried no price signal)
            brand, brand_mult = brands[i % len(brands)]
            tier = ("Premium" if brand_mult > 1.05 else ("Value" if brand_mult < 0.95 else "Mainstream"))
            list_price = round(max(0.05, case_price / upc * brand_mult), 2)
            unit_cost = round(min(list_price * cost_ratio * float(RNG.uniform(0.97, 1.03)), list_price * 0.95), 2)
            margin = round(list_price - unit_cost, 2)
            elast = round(float(gp["elast"] + RNG.normal(0, 0.10)), 3)  # RAW per-group elasticity + noise (kept verbatim)
            # --- shelf life: profile-driven so v4.2 can run without a perishability constraint
            if SHELF_LIFE_PROFILE == "relaxed":
                slo, shi = RELAXED_SHELF_LIFE_DAYS
            else:
                slo, shi = SUBCATEGORY_SHELF_LIFE[grp]
            shelf = int(SHELF_RNG.integers(slo, shi + 1))    # dedicated stream -- see note above
            pop = float(RNG.lognormal(0.0, POP_SIGMA)) * SEGMENT_VOL_FACTOR[seg]   # heavy tail -> ~80/20
            base = BASE_SCALE * pop
            # --- WS-D: seasonality now comes from the SUBCATEGORY occasion curve x a gentle macro
            #     calendar, not one shared category shape.
            base_monthly = np.array(SUBCATEGORY_SEASONALITY_MONTHLY[grp]) * macro
            idx = np.zeros(TOTAL_WEEKS); lvl = np.ones(TOTAL_WEEKS)
            for yi, yr in enumerate(YEARS):
                # each YEAR gets its own jittered shape -- if every year repeated the same curve,
                # seasonal-naive would be trivially accurate and the forecast degenerate again
                my = base_monthly * (1.0 + RNG.normal(0, SHAPE_JITTER, 12))
                my = my / my.mean()
                sl = slice(yi * 52, (yi + 1) * 52)
                dev = my[week_month[sl]] - 1.0
                idx[sl] = np.clip(1.0 + SEGMENT_SEASON_AMP[seg] * dev
                                  + RNG.normal(0, 0.03, 52), 0.10, None)
                lvl[sl] = np.prod([1 + YEAR_TREND[y] for y in YEARS[1:yi + 1]]) if yi else 1.0
            # structural events: a distribution gain/loss, a step price change, a late launch
            ev = {}
            if RNG.random() < P_DIST_CHANGE:
                ev["dist_week"] = int(RNG.integers(30, ACTUAL_WEEKS - 10))
                ev["dist_mult"] = float(RNG.choice([0.55, 1.45]))
            if RNG.random() < P_PRICE_STEP:
                ev["price_week"] = int(RNG.integers(30, ACTUAL_WEEKS - 10))
                ev["price_mult"] = float(RNG.uniform(0.82, 1.22))
            if RNG.random() < P_LATE_LAUNCH:
                ev["launch_week"] = int(RNG.integers(20, BURN_WEEKS + 20))
            seasonal_index[sid] = idx * lvl
            sku_events[sid] = ev
            elast_promo = round(float(np.clip(elast * PROMO_MULT, -4.5, 3.0)), 3)  # deal elasticity (grounded band)
            family = FAMILY_OF_SUBCAT[grp]
            sku_meta[sid] = dict(base=base, list_price=list_price, elast_base=elast, elast_promo=elast_promo,
                                 storability=STORABILITY[cat], category=cat, segment=seg,
                                 subcategory=grp, family=family, unit_cost=unit_cost, margin=margin)
            # holding cost is DERIVED from the single category rate in cost_parameters.csv --
            # retained here for convenience, but it is not an independent attribute (see §9.2).
            hold_year_usd = round(unit_cost * CATEGORY_HOLDING_PCT[cat], 2)
            sku_rows.append([sid, f"{cat[:3].upper()}-{n-1:04d}", cat, grp, family,
                             brand, case_price, upc, list_price, unit_cost, margin,
                             round(margin / list_price, 4), hold_year_usd, shelf, seg, tier])
    # `seasonal_flag` dropped: it was 100% identical to (segment == 'Seasonal') -- see §9.2
    pd.DataFrame(sku_rows, columns=[
        "sku_id", "sku_code", "category", "subcategory", "production_family", "brand",
        "case_price_usd", "units_per_case", "list_price_usd", "unit_cost_usd", "margin_usd",
        "margin_pct", "holding_cost_per_unit_year_usd", "shelf_life_days", "segment",
        "price_tier"]).to_csv(f"{OUT}/sku_master.csv", index=False)

    # ---- WS-D: publish the seasonality curves so they are tunable, not buried in code ----
    pd.DataFrame([{"subcategory": sc, "month": m + 1, "seasonal_index": round(v, 4),
                   "macro_calendar_index": MACRO_CALENDAR_MONTHLY[m],
                   "combined_index": round(v * MACRO_CALENDAR_MONTHLY[m], 4)}
                  for sc, vals in SUBCATEGORY_SEASONALITY_MONTHLY.items()
                  for m, v in enumerate(vals)]).to_csv(f"{OUT}/seasonality_master.csv", index=False)

    # ---- WS-F: publish the regional consumption index ----
    pd.DataFrame([{"region": rg, "subcategory": sc, "demand_index": v}
                  for sc, d in REGION_DEMAND_INDEX.items() for rg, v in d.items()]
                 ).to_csv(f"{OUT}/regional_demand_index.csv", index=False)

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
    cust_scale_map = {b: s for b, _, _, _, s in banners}
    pd.DataFrame([[b, nm, ch, dm, CUSTOMER_TERMS[b][0]] for b, nm, ch, dm, _ in banners],
                 columns=["b2b_customer_id", "customer_name", "channel_type", "delivery_mode",
                          "pricing_strategy"]).to_csv(f"{OUT}/b2b_customer_master.csv", index=False)

    # coverage-constrained assignment: guarantee EVERY RDC and CDC is attached to >=1 banner (no orphan DCs),
    # then top up each banner to 2-3 DCs. Per-banner demand is unchanged (shares sum to 1) so the demand
    # PROFILE (totals, seasonality, elasticity, mix, concentration) is preserved -- only the DC split changes.
    bulk = [x for x in banners if x[3] == "BULK"]; dsd = [x for x in banners if x[3] == "DSD"]
    assign = {x[0]: set() for x in banners}
    for i, dcid in enumerate(rdc_ids): assign[bulk[i % len(bulk)][0]].add(dcid)     # cover all RDCs
    for i, dcid in enumerate(cdc_ids): assign[dsd[i % len(dsd)][0]].add(dcid)       # cover all CDCs
    serve_rows = []; cust_dcs = {}
    for bid, nm, ch, dm, scale in banners:
        pool = rdc_ids if dm == "BULK" else cdc_ids
        cur = sorted(assign[bid]); target = max(len(cur), int(RNG.integers(2, 4)))   # sorted: see carries[] note
        extra = [d for d in pool if d not in assign[bid]]; RNG.shuffle(extra)
        chosen = cur + extra[:max(0, target - len(cur))]
        cust_dcs[bid] = chosen
        for dcid, sh in zip(chosen, RNG.dirichlet(np.ones(len(chosen)))):
            serve_rows.append([bid, dcid, "REGIONAL_DC" if dm == "BULK" else "CUSTOMER_DC", round(float(sh), 4)])
    pd.DataFrame(serve_rows, columns=["b2b_customer_id", "dc_id", "dc_type", "demand_share"]).to_csv(f"{OUT}/customer_serving_dc.csv", index=False)

    # ---- WS-G: customer trade terms (list price stays national; differentiation is below list) ----
    term_rows = []
    for bid, (strategy, offinv, fund, rebate, logi, pf, pd_) in CUSTOMER_TERMS.items():
        term_rows.append([bid, strategy, offinv, fund, rebate, logi, pf, pd_,
                          round(offinv + fund + rebate + logi, 4)])
    pd.DataFrame(term_rows, columns=[
        "b2b_customer_id", "pricing_strategy", "everyday_off_invoice_pct", "trade_fund_pct",
        "volume_rebate_pct", "logistics_allowance_pct", "promo_frequency_mult",
        "promo_depth_mult", "total_trade_rate_pct"]).to_csv(f"{OUT}/b2b_customer_terms.csv", index=False)

    # ---- region lookup for every stocking DC (WS-F needs it in step 2) ----
    dc_city = {d: c for d, _, c in n_rdc}; dc_city.update({d: c for d, _, c in n_cdc})
    dc_region = {d: NCITY[c][2] for d, c in dc_city.items()}
    plant_city = {p: c for p, _, c in n_plants}

    # ---- WS-E: multi-sourcing. Rank each subcategory's CAPABLE plants by demand-weighted
    #      distance (DC demand share x that subcategory's regional index), then dual-source the
    #      A items. v3 used plants[i % 4], giving a 24.4% geographic match and no dual sourcing.
    pd.DataFrame([[p, fam, int(net_rng.integers(900_000, 1_600_000))]
                  for p, fams in PLANT_CAPABILITY.items() for fam in fams],
                 columns=["plant_id", "production_family", "family_capacity_units_per_week"]
                 ).to_csv(f"{OUT}/plant_capability.csv", index=False)

    dc_weight = {}                                    # dc_id -> aggregate demand share
    for bid, dcid, _dt, sh in serve_rows:
        dc_weight[dcid] = dc_weight.get(dcid, 0.0) + sh * cust_scale_map[bid]

    def ranked_plants(subcat):
        """Capable plants ranked by the SERVICE AREA each would win, largest first.

        Ranking by *average* weighted distance (the obvious approach) always favours the most
        central plant, so a coastal plant never wins: PLANT04 (Los Angeles) took zero primary
        assignments because every family it can make is also made at a more central site. Real
        networks assign each DC to its nearest capable plant and the plant with the biggest
        resulting service area becomes primary -- which is what this does, and it also yields
        allocation shares that reflect geography instead of a flat 70/30.
        """
        fam = FAMILY_OF_SUBCAT[subcat]
        capable = [p for p, fams in PLANT_CAPABILITY.items() if fam in fams]
        ridx = REGION_DEMAND_INDEX[subcat]
        won = {p: 0.0 for p in capable}
        for dcid, w in dc_weight.items():
            ww = w * ridx.get(dc_region[dcid], 1.0)
            nearest = min(capable, key=lambda p: _hav(plant_city[p], dc_city[dcid]))
            won[nearest] += ww
        tot = sum(won.values()) or 1.0
        order = sorted(capable, key=lambda p: -won[p])
        return order, {p: won[p] / tot for p in capable}

    rank_cache = {sc: ranked_plants(sc) for sc in FAMILY_OF_SUBCAT}
    bases = sorted(((m["base"], s) for s, m in sku_meta.items()), reverse=True)
    abc_of = {}
    for r, (_b, s) in enumerate(bases):
        abc_of[s] = "A" if r < 0.20 * len(bases) else ("B" if r < 0.50 * len(bases) else "C")

    # ---- CAPACITY-CONSTRAINED primary assignment -------------------------------------------
    # Service area alone still hands every family to the single most central plant (Dallas is
    # central to this network, so PLANT04/Los Angeles ended up idle). Real sourcing is capacity
    # constrained: once a line is full, volume moves to the next-best plant. Budgets are set from
    # each family's own demand at a ~78% target utilisation and split by service area with a
    # floor, so every capable plant carries load and capacity actually binds (audit fix #9).
    TARGET_UTIL, AREA_FLOOR = 0.88, 0.34
    fam_skus = {}
    for sid, m in sku_meta.items():
        fam_skus.setdefault(m["family"], []).append(sid)
    budget = {}
    for fam, sids in fam_skus.items():
        capable = [p for p, fams in PLANT_CAPABILITY.items() if fam in fams]
        # one representative subcategory per family carries the service-area split
        _, area = rank_cache[sku_meta[sids[0]]["subcategory"]]
        wts = {p: max(area.get(p, 0.0), AREA_FLOOR) for p in capable}
        tw = sum(wts.values())
        fam_demand = sum(sku_meta[s]["base"] for s in sids)
        for p in capable:
            budget[(p, fam)] = fam_demand / TARGET_UTIL * wts[p] / tw

    src_rows = []
    for _b, sid in bases:                                  # largest SKUs choose first
        m = sku_meta[sid]; fam = m["family"]
        ranked, area = rank_cache[m["subcategory"]]
        # nearest capable plant that still has budget; fall back to the least-loaded one
        avail = [p for p in ranked if budget.get((p, fam), 0.0) >= m["base"]]
        p1 = avail[0] if avail else max(ranked, key=lambda p: budget.get((p, fam), 0.0))
        others = [p for p in ranked if p != p1]
        dual = (abc_of[sid] == "A" and others) or (abc_of[sid] == "B" and others and RNG.random() < 0.35)
        if dual:
            p2 = others[0]
            s1 = float(np.clip(area.get(p1, .5) / max(area.get(p1, .5) + area.get(p2, .5), 1e-9), 0.55, 0.85))
            pen = 1 + float(RNG.uniform(*SECONDARY_COST_PENALTY))
            src_rows.append([sid, p1, True, round(s1, 2), m["unit_cost"]])
            src_rows.append([sid, p2, False, round(1 - s1, 2), round(m["unit_cost"] * pen, 2)])
            budget[(p1, fam)] = budget.get((p1, fam), 0.0) - m["base"] * s1
            budget[(p2, fam)] = budget.get((p2, fam), 0.0) - m["base"] * (1 - s1)
        else:
            src_rows.append([sid, p1, True, 1.00, m["unit_cost"]])
            budget[(p1, fam)] = budget.get((p1, fam), 0.0) - m["base"]
    src = pd.DataFrame(src_rows, columns=["sku_id", "plant_id", "is_primary",
                                          "allocation_share", "unit_cost_at_plant_usd"])
    src["production_family"] = src.sku_id.map(lambda s: sku_meta[s]["family"])
    src.to_csv(f"{OUT}/sku_plant_sourcing.csv", index=False)
    n_dual = int((src.groupby("sku_id").size() > 1).sum())

    print(f"  SKUs 500 (5x100) | RDCs {len(rdc_ids)} CDCs {len(cdc_ids)} | Lanes {n_lanes} | Banners {len(banners)}")
    print(f"  WS-A per-unit prices: ${min(r[8] for r in sku_rows):.2f}-${max(r[8] for r in sku_rows):.2f} "
          f"(v3 ranged $0.33-$157.60 on a case basis)")
    print(f"  WS-D seasonality: {len(SUBCATEGORY_SEASONALITY_MONTHLY)} subcategory curves | "
          f"WS-E sourcing: {n_dual}/{len(sku_meta)} SKUs dual-sourced ({n_dual/len(sku_meta):.0%})")
    print(f"  shelf-life profile: {SHELF_LIFE_PROFILE} "
          f"({min(r[13] for r in sku_rows)}-{max(r[13] for r in sku_rows)} days)")
    return dict(sku_meta=sku_meta, seasonal_index=seasonal_index, cust_dcs=cust_dcs,
                cust_scale=cust_scale_map, cust_deliv={b: dm for b, _, _, dm, _ in banners},
                dc_region=dc_region, abc_of=abc_of, sku_events=sku_events,
                sourcing={s: g[["plant_id", "is_primary", "allocation_share"]].values.tolist()
                          for s, g in src.groupby("sku_id")})


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
    disc = {s: {b: np.zeros(TOTAL_WEEKS) for b in banners} for s in sku_ids}   # actual depth
    plan = {s: {b: np.zeros(TOTAL_WEEKS) for b in banners} for s in sku_ids}   # PLANNED depth
    pidx = {s: {b: np.array([""] * TOTAL_WEEKS, dtype=object) for b in banners} for s in sku_ids}
    # WS-C (partial): promo intensity raised toward CPG-realistic levels (v3 reached only 11.6%
    # of volume against a documented "~30%"), and depth is now scaled by each customer's trade
    # terms so an EDLC club account gets shallow/rare deals while a high-low grocer gets deep
    # frequent ones. Planned vs actual depth is split so execution variance exists.
    for sid in sku_ids:
        n_events = int(RNG.integers(8, 17)) * (TOTAL_WEEKS // 52)   # per YEAR, over 4 years
        for _ in range(n_events):
            dur = int(RNG.integers(1, 4))                        # 1-3 weeks
            start = int(RNG.integers(0, TOTAL_WEEKS - dur + 1))
            target_all = RNG.random() < 0.45
            tgt = "ALL" if target_all else str(RNG.choice(banners))
            targets = banners if target_all else [tgt]
            # depth scaled by the (average) depth multiplier of the targeted accounts
            dmult = float(np.mean([CUSTOMER_TERMS[b][6] for b in targets]))
            fmult = float(np.mean([CUSTOMER_TERMS[b][5] for b in targets]))
            if fmult <= 0 or RNG.random() > min(1.0, fmult):     # CONTRACT accounts get no promos
                continue
            planned = round(float(np.clip(RNG.uniform(0.05, 0.30) * dmult, 0.02, 0.32)), 3)
            actual = round(float(np.clip(planned * RNG.normal(1.0, 0.12), 0.02, 0.36)), 3)
            pid = f"PR{pid_seq:05d}"; pid_seq += 1
            sd = week_start(start + 1)
            ed = (WEEK1_MONDAY + pd.Timedelta(weeks=start + dur - 1) + pd.Timedelta(days=6)).strftime("%Y-%m-%d")
            # forward-year events are a PLAN: depth is committed but not yet executed, and the
            # forecaster/MEIO may only see planned_discount_pct for them.
            fwd = start + 1 > ACTUAL_WEEKS
            promo_rows.append([pid, sid, tgt, sd, ed, planned,
                               (np.nan if fwd else actual),
                               str(RNG.choice(PROMO_TYPES)),
                               "planned" if fwd else "executed"])
            for b in targets:
                for w in range(start, start + dur):
                    if planned > plan[sid][b][w]:
                        plan[sid][b][w] = planned
                    if (not fwd) and actual > disc[sid][b][w]:   # deepest promo wins that week
                        disc[sid][b][w] = actual; pidx[sid][b][w] = pid
    _pr = pd.DataFrame(promo_rows, columns=["promo_id", "sku_id", "b2b_customer_id", "start_date",
                                            "end_date", "planned_discount_pct", "discount_pct",
                                            "promo_type", "plan_status"])
    _pr.to_csv(f"{OUT}/promotions.csv", index=False)

    # ---------------- base/shelf price path per SKU (slow drift + 1-3 step revisions) ------------
    # §9.5: v3 clipped the shelf-price path to [0.85, 1.20] x list, giving a mean range of only
    # 9.1% of list -- sd of ln(base_price) 0.027 vs 0.051 for ln(1-discount). BASE elasticity was
    # therefore weakly identified while promo elasticity was well identified, and the charter's
    # exchange rate depends on the weaker one. Band widened and more revision steps added.
    weeks = np.arange(TOTAL_WEEKS)
    sku_events = ctx.get("sku_events", {})
    base_price_path = {}
    for sid in sku_ids:
        lp = sku_meta[sid]["list_price"]
        drift = (1.04) ** (weeks / 52.0)                          # ~4%/yr inflation
        walk = 1.0 + np.cumsum(RNG.normal(0, 0.010, TOTAL_WEEKS))
        rev = np.ones(TOTAL_WEEKS)
        for _ in range(int(RNG.integers(3, 7)) * (TOTAL_WEEKS // 52)):
            wk = int(RNG.integers(4, TOTAL_WEEKS)); step = float(RNG.uniform(0.04, 0.12)) * RNG.choice([-1, 1])
            rev[wk:] *= (1 + step)
        ev = sku_events.get(sid, {})
        if "price_week" in ev:                                    # structural step change
            rev[ev["price_week"]:] *= ev["price_mult"]
        # floor kept above unit cost: an everyday shelf price below cost is not a real price.
        # The band is still wide enough to identify base elasticity (ln-sd well above 0.05).
        base_price_path[sid] = np.clip(lp * drift * walk * rev, lp * 0.82, lp * 1.60)

    # WS-C: the forward shelf-price plan MEIO / price-buffer routing acts on
    pd.DataFrame([{"period": period_label(w + 1), "week_start_date": week_start(w + 1),
                   "sku_id": sid, "planned_base_price_usd": round(float(base_price_path[sid][w]), 2),
                   "list_price_usd": sku_meta[sid]["list_price"]}
                  for sid in sku_ids for w in range(ACTUAL_WEEKS, TOTAL_WEEKS)]
                 ).to_csv(f"{OUT}/price_plan.csv", index=False)

    carries = {}
    for b in banners:
        frac = 0.85 if cust_deliv[b] == "BULK" else 0.55
        # sorted(), not set(): iterating a set of strings has PROCESS-DEPENDENT order because
        # CPython salts string hashing per interpreter. v3 iterated the set directly, so the
        # order in which SKUs consumed the RNG stream changed run to run -- the generator was
        # never byte-reproducible despite the fixed seeds. Sorting makes it deterministic.
        carries[b] = sorted(RNG.choice(sku_ids, size=int(len(sku_ids) * frac), replace=False).tolist())

    # order cadence: faster SKUs (higher base) are ordered every week; slower ones every 2-3 weeks
    bases = np.array([sku_meta[s]["base"] for s in sku_ids]); p60, p20 = np.percentile(bases, [60, 20])
    cadence = {s: (1 if sku_meta[s]["base"] >= p60 else (2 if sku_meta[s]["base"] >= p20 else 3)) for s in sku_ids}

    # ---------------- DAILY orders: each (customer,SKU) has an order weekday + cadence; a week's demand
    # (accumulated over the cadence) is placed on that weekday -> realistic B2B baskets grouped by order_id.
    ORDER_DAYS = {"BULK": [1, 3], "DSD": [0, 2, 4]}     # weekday offsets from Monday
    # ---- WS-F: renormalise the regional indices against actual DC weights so NATIONAL volume is
    #      unchanged and only the geographic MIX shifts (keeps v4 comparable to v3 in total).
    dc_region = ctx["dc_region"]
    dcw = {}
    for (b_, dcid_), sh in share_lookup.items():
        dcw[dcid_] = dcw.get(dcid_, 0.0) + sh * cust_scale[b_]
    tot_w = sum(dcw.values())
    region_norm = {sc: sum(w * d.get(dc_region[dc], 1.0) for dc, w in dcw.items()) / tot_w
                   for sc, d in REGION_DEMAND_INDEX.items()}

    line_rows = []
    for b in banners:
        mode = cust_deliv[b]; cscale = cust_scale[b]; odays = ORDER_DAYS[mode]
        off_inv = CUSTOMER_TERMS[b][1]                 # WS-G: permanent off-invoice deduction
        for sid in carries[b]:
            m = sku_meta[sid]; lp = m["list_price"]; si = seasonal_index[sid]
            eb, ep, stor = m["elast_base"], m["elast_promo"], m["storability"]
            subcat = m["subcategory"]; ridx = REGION_DEMAND_INDEX[subcat]; rnorm = region_norm[subcat]
            bp = base_price_path[sid]; dsc = disc[sid][b]; pids = pidx[sid][b]
            pant = np.ones(ACTUAL_WEEKS)               # post-promo forward-buy dip (this customer's promos)
            for w in np.nonzero(dsc[:ACTUAL_WEEKS] > 0)[0]:
                dip = stor * min(dsc[w] / 0.30, 1.0) * 0.20
                if w + 1 < ACTUAL_WEEKS: pant[w + 1] *= (1 - dip)
                if w + 2 < ACTUAL_WEEKS: pant[w + 2] *= (1 - 0.5 * dip)
            # WS-G: demand responds to the EVERYDAY NET price this account pays (shelf price less
            # its off-invoice allowance), so an EDLC account's permanently lower net price lifts
            # its baseline -- which is what makes customer-level elasticity identifiable.
            everyday_net = bp[:ACTUAL_WEEKS] * (1 - off_inv)
            pmult = np.minimum((everyday_net / lp) ** eb
                               * (1 - dsc[:ACTUAL_WEEKS]) ** ep, 12.0) * pant
            # structural events (WS-J): distribution gain/loss and late launch
            ev = sku_events.get(sid, {})
            struct = np.ones(ACTUAL_WEEKS)
            if "dist_week" in ev:
                struct[ev["dist_week"]:] *= ev["dist_mult"]
            if "launch_week" in ev:
                struct[:min(ev["launch_week"], ACTUAL_WEEKS)] = 0.0
            cad = cadence[sid]; weekday = int(RNG.choice(odays)); phase = int(RNG.integers(0, cad))
            for dcid in cust_dcs[b]:
                # WS-F: regional consumption multiplier for this subcategory at this DC's region
                rmult = ridx.get(dc_region[dcid], 1.0) / rnorm
                base = m["base"] * cscale * share_lookup[(b, dcid)] * rmult
                wq = np.maximum(0, np.round(base * si[:ACTUAL_WEEKS] * pmult * struct
                                            * RNG.lognormal(0, 0.15, ACTUAL_WEEKS))).astype(int)
                acc = 0
                for w in range(ACTUAL_WEEKS):
                    acc += int(wq[w])
                    if ((w % cad) == phase or w == ACTUAL_WEEKS - 1) and acc > 0:
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
    # ---- WS-G price waterfall: list -> invoice -> pocket (net). List stays national; every
    #      customer-level difference sits in trade terms, as in real CPG practice.
    _t = {b: CUSTOMER_TERMS[b] for b in CUSTOMER_TERMS}
    df["off_invoice_pct"] = df.b2b_customer_id.map(lambda b: _t[b][1])
    df["trade_fund_pct"] = df.b2b_customer_id.map(lambda b: _t[b][2])
    df["volume_rebate_pct"] = df.b2b_customer_id.map(lambda b: _t[b][3])
    df["logistics_allowance_pct"] = df.b2b_customer_id.map(lambda b: _t[b][4])
    df["invoice_price_usd"] = (df.base_price_usd * (1 - df.off_invoice_pct)
                               * (1 - df.discount_pct)).round(4)
    _below = df.trade_fund_pct + df.volume_rebate_pct + df.logistics_allowance_pct
    df["net_price_usd"] = (df.invoice_price_usd * (1 - _below)).round(4)    # pocket price
    df["net_revenue_usd"] = (df.order_qty * df.net_price_usd).round(2)
    df["unit_cost_usd"] = df.sku_id.map(lambda s: sku_meta[s]["unit_cost"])
    df["net_margin_usd"] = ((df.net_price_usd - df.unit_cost_usd) * df.order_qty).round(2)
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
            "on_promo", "promo_id", "revenue_usd",
            "off_invoice_pct", "trade_fund_pct", "volume_rebate_pct", "logistics_allowance_pct",
            "invoice_price_usd", "net_price_usd", "net_revenue_usd", "net_margin_usd"]
    df[cols].to_csv(f"{OUT}/orders.csv", index=False)
    n_orders = df.order_id.nunique()
    promo_vol = df.loc[df.on_promo, "order_qty"].sum() / df.order_qty.sum()
    pocket = df.groupby("b2b_customer_id").apply(
        lambda g: (g.net_price_usd * g.order_qty).sum() / (g.list_price_usd * g.order_qty).sum(),
        include_groups=False)
    print(f"  promotions: {len(promo_rows):,} events | orders: {len(df):,} lines in {n_orders:,} orders "
          f"({len(df)/n_orders:.1f} lines/order)")
    print(f"  horizon: {ACTUAL_WEEKS} actual weeks (2023-2025) + {FWD_WEEKS} forward (2026) | "
          f"promo events: {int((_pr.plan_status=='executed').sum()):,} executed, "
          f"{int((_pr.plan_status=='planned').sum()):,} planned-forward")
    print(f"  promo share of volume {promo_vol:.1%} (v3: 11.6%) | "
          f"pocket price as % of list: {pocket.min():.1%}-{pocket.max():.1%} (spread {pocket.max()-pocket.min():.1%})")
    # hand the PLANNED promo calendar to step 3 -- the forecaster is allowed to see the plan,
    # never the realised depth (that is what keeps the forecast honest).
    ctx["plan_depth"] = plan


# ============================================================================
# STEP 3: demand_forecast (2025 in-sample ex-ante + 2026 forward, both COMPLETE panels),
#         production + inventory over the evaluated 2025 window
# ============================================================================
def step3_forecast_production_inventory(ctx):
    RNG = np.random.default_rng(909)
    OUT = OUTPUT_DIR
    sku_meta, seasonal_index = ctx["sku_meta"], ctx["seasonal_index"]
    cust_dcs = ctx["cust_dcs"]; plan_depth = ctx["plan_depth"]; sku_ids = list(sku_meta.keys())
    orders_daily = pd.read_csv(f"{OUT}/orders.csv")
    # roll DAILY order lines up to weekly (sku,dc,customer,period) for forecast/production/inventory
    ws_map = {period_label(i): week_start(i) for i in range(1, TOTAL_WEEKS + 1)}
    orders = orders_daily.groupby(["period", "sku_id", "dc_id", "b2b_customer_id"], as_index=False).order_qty.sum()
    orders["week_start_date"] = orders.period.map(ws_map)
    seg_of = {s: m["segment"] for s, m in sku_meta.items()}
    idx_of = {period_label(i): i for i in range(1, TOTAL_WEEKS + 1)}   # period label -> week 1..208

    # =====================================================================================
    # WS-J + WS-M: EX-ANTE baseline/lift forecast on a COMPLETE panel.
    #
    # v3 set forecast_qty = actual_qty x (1 + noise): corr(fc, actual) = 0.981 and 97.6% of the
    # WMAPE variance was explained by the `segment` label alone, because the noise level WAS the
    # label. Promo weeks were no harder than any other week, contradicting the documentation.
    #
    # Three layers, chosen after backtesting alternatives on the v3 data:
    #   Layer 0  DE-BATCH  forecast at SKU x DC x week, not SKU x DC x CUSTOMER x week. 62% of the
    #                      apparent volatility at customer grain is order batching (34% of cells
    #                      have no order line; mean inter-order gap 1.9 weeks), so at that grain
    #                      you are forecasting the order calendar rather than demand.
    #   Layer 1  BASELINE  de-promote the history, then seasonal-naive from t-52 rescaled by the
    #                      trailing level ratio. Strictly ex-ante: only weeks < t are ever used.
    #   Layer 2  LIFT      uplift estimated from PRIOR promo weeks, applied to the PLANNED promo
    #                      calendar, with deliberate misestimation -- applying the TRUE lift
    #                      rebuilds the leak (measured: promo APE ratio 1.01x vs 1.11x).
    # =====================================================================================
    LIFT_MISEST = 0.35            # the planner's uplift assumption is off by ~35%
    LVL_WIN = 13                  # trailing weeks used for the level estimate

    # rebuild from the DAILY lines so on_promo survives the weekly roll-up
    ow = (orders_daily.groupby(["period", "sku_id", "dc_id", "b2b_customer_id"], as_index=False)
          .agg(order_qty=("order_qty", "sum"), on_promo=("on_promo", "max")))
    ow["wk"] = ow.period.map(idx_of)
    ow = ow.dropna(subset=["wk"]); ow["wk"] = ow["wk"].astype(int)

    # --- Layer 0: SKU x DC panel over the 156 actual weeks
    pan = (ow.groupby(["sku_id", "dc_id", "wk"]).order_qty.sum().unstack("wk")
             .reindex(columns=range(1, ACTUAL_WEEKS + 1)).fillna(0.0))
    prm = (ow.groupby(["sku_id", "dc_id", "wk"]).on_promo.max().unstack("wk")
             .reindex(columns=range(1, ACTUAL_WEEKS + 1)).fillna(False).astype(bool))
    keys = pan.index
    kpos = {k: i for i, k in enumerate(keys)}
    A = pan.values.astype(float)                     # actuals
    P = prm.values                                   # promo state

    # --- Layer 1: de-promote, then a trailing level
    Bse = pd.DataFrame(A).mask(pd.DataFrame(P)).interpolate(axis=1, limit_direction="both").values
    Bse = np.nan_to_num(Bse, nan=0.0)
    lvl = pd.DataFrame(Bse).T.rolling(LVL_WIN, min_periods=3).mean().T.values

    # PLANNED promo depth per SKU-DC-week, taken from the promo calendar -- never from actuals
    dep = np.zeros((len(keys), TOTAL_WEEKS))
    for sid in sku_ids:
        for b in cust_dcs:
            pv = plan_depth[sid][b]
            if not pv.any():
                continue
            for dcid in cust_dcs[b]:
                i = kpos.get((sid, dcid))
                if i is not None:
                    dep[i] = np.maximum(dep[i], pv)

    # --- Layer 2: uplift per series, estimated from PRIOR promo weeks only
    _d = dep[:, :ACTUAL_WEEKS]
    with np.errstate(divide="ignore", invalid="ignore"):
        obs = np.where(P & (Bse > 0) & (_d > 0),
                       (A / np.where(Bse > 0, Bse, np.nan) - 1.0) / np.where(_d > 0, _d, np.nan),
                       np.nan)
    lift_hat = np.nanmedian(obs, axis=1)
    lift_hat = np.clip(np.nan_to_num(lift_hat, nan=1.8, posinf=1.8, neginf=1.8), 0.0, 8.0)
    lift_hat = np.clip(lift_hat * (1.0 + RNG.normal(0, LIFT_MISEST, len(lift_hat))), 0.0, 8.0)

    # --- in-sample (2025) one-step-ahead; forward (2026) projected from the cutover level
    FC_BASE = np.zeros((len(keys), TOTAL_WEEKS))
    for t in range(BURN_WEEKS, ACTUAL_WEEKS):                 # 0-based -> weeks 105..156
        prev, prev52 = lvl[:, t - 1], lvl[:, t - 53]
        ratio = np.divide(prev, prev52, out=np.ones_like(prev), where=prev52 > 0)
        ratio = np.clip(np.nan_to_num(ratio, nan=1.0), 0.5, 2.0)
        FC_BASE[:, t] = np.maximum(0.0, Bse[:, t - 52] * ratio)
    cut, cut52 = lvl[:, ACTUAL_WEEKS - 1], lvl[:, ACTUAL_WEEKS - 53]
    yoy = np.divide(cut, cut52, out=np.ones_like(cut), where=cut52 > 0)
    yoy = np.clip(np.nan_to_num(yoy, nan=1.0), 0.5, 2.0)
    for t in range(ACTUAL_WEEKS, TOTAL_WEEKS):                # weeks 157..208 (2026)
        FC_BASE[:, t] = np.maximum(0.0, Bse[:, t - 52] * yoy)
    FC_LIFT = FC_BASE * (lift_hat[:, None] * dep)             # planned-promo uplift
    FC_TOT = FC_BASE + FC_LIFT

    # --- disaggregate SKU x DC back to the published SKU x DC x CUSTOMER grain by history share
    cs = ow.groupby(["sku_id", "dc_id", "b2b_customer_id"], as_index=False).order_qty.sum()
    cs["share"] = cs.order_qty / cs.groupby(["sku_id", "dc_id"]).order_qty.transform("sum")
    cs = cs[cs.share > 0].copy()
    cs["_i"] = [kpos.get((r.sku_id, r.dc_id), -1) for r in cs.itertuples()]
    cs = cs[cs._i >= 0]

    # WS-M: COMPLETE panel with explicit zeros. v3 built the in-sample side by grouping orders, so
    # 34% of cells simply did not exist and there were ZERO actual_qty==0 rows in all of history
    # -- "we forecast 10 and sold 0" was unrepresentable, while the forward panel was 100% dense.
    act_lookup = ow.groupby(["sku_id", "dc_id", "b2b_customer_id", "wk"]).order_qty.sum()
    blocks = []
    for lo, hi, hz in ((BURN_WEEKS, ACTUAL_WEEKS, "in_sample"), (ACTUAL_WEEKS, TOTAL_WEEKS, "forward")):
        wk = np.arange(lo + 1, hi + 1)
        nw = len(wk)
        rep = np.repeat(cs["_i"].values, nw)
        wcol = np.tile(wk, len(cs))
        sh = np.repeat(cs["share"].values, nw)
        blocks.append(pd.DataFrame({
            "wk": wcol,
            "sku_id": np.repeat(cs.sku_id.values, nw),
            "dc_id": np.repeat(cs.dc_id.values, nw),
            "b2b_customer_id": np.repeat(cs.b2b_customer_id.values, nw),
            "horizon": hz,
            "forecast_baseline_qty": np.round(FC_BASE[rep, wcol - 1] * sh).astype(int),
            "forecast_promo_lift_qty": np.round(FC_LIFT[rep, wcol - 1] * sh).astype(int),
        }))
    fcast = pd.concat(blocks, ignore_index=True)
    fcast["forecast_qty"] = fcast.forecast_baseline_qty + fcast.forecast_promo_lift_qty
    fcast["period"] = [period_label(int(i)) for i in fcast.wk]
    fcast["week_start_date"] = [week_start(int(i)) for i in fcast.wk]
    _k = pd.MultiIndex.from_arrays([fcast.sku_id, fcast.dc_id, fcast.b2b_customer_id, fcast.wk])
    fcast["actual_qty"] = act_lookup.reindex(_k).values
    _is_ins = fcast.horizon == "in_sample"
    fcast.loc[_is_ins, "actual_qty"] = fcast.loc[_is_ins, "actual_qty"].fillna(0.0)   # explicit zeros
    fcast.loc[~_is_ins, "actual_qty"] = np.nan
    fcast["forecast_error_pct"] = ((fcast.forecast_qty - fcast.actual_qty)
                                   / fcast.actual_qty.replace(0, np.nan)).round(4)
    cols = ["period", "week_start_date", "horizon", "sku_id", "dc_id", "b2b_customer_id",
            "forecast_qty", "forecast_baseline_qty", "forecast_promo_lift_qty",
            "actual_qty", "forecast_error_pct"]
    fcast[cols].to_csv(f"{OUT}/demand_forecast.csv", index=False)

    _ins = fcast[_is_ins]
    _nz = _ins[_ins.actual_qty > 0]
    _wm = (_nz.forecast_qty - _nz.actual_qty).abs().sum() / max(_nz.actual_qty.sum(), 1)
    _corr = float(_ins[["forecast_qty", "actual_qty"]].corr().iloc[0, 1])
    print(f"  demand_forecast: in_sample {int(_is_ins.sum()):,} + forward {int((~_is_ins).sum()):,} "
          f"= {len(fcast):,} rows (complete panel, "
          f"{int((_ins.actual_qty == 0).sum()):,} explicit zero-demand rows)")
    print(f"  WS-J ex-ante: WMAPE {_wm:.3f} | corr(fc,actual) {_corr:.3f} (v3: 0.981) | "
          f"{BURN_WEEKS}-week burn-in feeds it")

    # ---- production (2025): SKU x Plant x Week ----
    plants = pd.read_csv(f"{OUT}/plant_master.csv").plant_id.tolist()
    sku_ids = list(sku_meta.keys())
    # WS-E: production follows the family/geography sourcing built in step 1, not a round-robin
    # index. Dual-sourced SKUs split output across their primary and secondary plant.
    sourcing = ctx.get("sourcing", {})
    sku_plant = {s: (sourcing[s][0][0] if s in sourcing else plants[i % len(plants)])
                 for i, s in enumerate(sku_ids)}
    # Production and inventory are simulated over the FULL actual span (2023-2025, weeks 1-156),
    # so the twin and any policy backtest have three years of multi-echelon history rather than
    # one. Planning parameters (avg/std) are fitted on the whole span; the year-over-year trend
    # is only ~4-5%/yr so the distortion at the ends is small.
    EVAL_START = 1
    nat = orders.groupby(["sku_id", "period"], as_index=False).order_qty.sum()
    nat["wk"] = nat.period.map(idx_of)
    nat = nat[(nat.wk >= EVAL_START) & (nat.wk <= ACTUAL_WEEKS)]
    nat["pw"] = (nat.wk - 1).clip(lower=EVAL_START)
    planned = nat.groupby(["sku_id", "pw"], as_index=False).order_qty.sum().rename(columns={"pw": "wk", "order_qty": "planned_qty"})
    # WS-E: explode onto each SKU's sourcing plants, splitting volume by allocation_share so a
    # dual-sourced A item is genuinely produced at two plants.
    if sourcing:
        alloc = pd.DataFrame([{"sku_id": s, "plant_id": p, "is_primary": bool(pr), "allocation_share": a}
                              for s, rows in sourcing.items() for p, pr, a in rows])
        planned = planned.merge(alloc, on="sku_id", how="left")
        planned["planned_qty"] = (planned.planned_qty * planned.allocation_share).round().astype(int)
        planned = planned[planned.planned_qty > 0].drop(columns=["allocation_share"])
    else:
        planned["plant_id"] = planned.sku_id.map(sku_plant); planned["is_primary"] = True
    adh = np.where(RNG.random(len(planned)) < 0.90, np.clip(RNG.normal(0.945, 0.02, len(planned)), 0.85, 1.02), RNG.uniform(0.75, 0.90, len(planned)))
    planned["adherence_pct"] = adh.round(3)
    planned["actual_qty"] = (planned.planned_qty * planned.adherence_pct).round().astype(int)
    planned["period"] = planned.wk.map(period_label); planned["week_start_date"] = planned.wk.map(week_start)
    planned["production_family"] = planned.sku_id.map(lambda s: sku_meta[s]["family"])
    planned[["period", "week_start_date", "plant_id", "sku_id", "production_family", "is_primary",
             "planned_qty", "actual_qty", "adherence_pct"]].to_csv(f"{OUT}/production.csv", index=False)
    print(f"  production rows: {len(planned):,} | mean adherence {planned.adherence_pct.mean():.3f}")

    # ---- inventory (2025): MULTI-ECHELON (s,S) flow sim. Demand cascades UP the network so every echelon
    #      holds stock: Customer DC outbound = its orders; Regional DC = its direct BULK orders + replenishment
    #      to the Customer DCs it primarily serves; Plant DC = replenishment to the Regional DCs it serves.
    #      This gives RDC and Plant-DC nodes real throughput/inventory (no orphan-in-inventory nodes).
    lanes_df = pd.read_csv(f"{OUT}/network_lanes.csv")
    rdc_of_cdc = (lanes_df[lanes_df.destination_node_type == "CUSTOMER_DC"]
                  .sort_values("lead_time_mean_days").groupby("destination_node_id").origin_node_id.first())
    pdc_of_rdc = (lanes_df[(lanes_df.origin_node_type == "PLANT_DC") & (lanes_df.destination_node_type == "REGIONAL_DC")]
                  .sort_values("lead_time_mean_days").groupby("destination_node_id").origin_node_id.first())
    od = orders.groupby(["dc_id", "sku_id", "period"], as_index=False).order_qty.sum()
    od["wk"] = od.period.map(idx_of)
    od = od[(od.wk >= EVAL_START) & (od.wk <= ACTUAL_WEEKS)]
    is_cdc = od.dc_id.astype(str).str.startswith("CDC"); is_rdc = od.dc_id.astype(str).str.startswith("RDC")
    cdc_dem = od[is_cdc][["dc_id", "sku_id", "wk", "order_qty"]].copy()
    rdc_direct = od[is_rdc][["dc_id", "sku_id", "wk", "order_qty"]].copy()
    tmp = cdc_dem.copy(); tmp["dc_id"] = tmp.dc_id.map(rdc_of_cdc)          # roll CDC demand up to its primary RDC
    rdc_all = (pd.concat([rdc_direct, tmp.dropna(subset=["dc_id"])])
               .groupby(["dc_id", "sku_id", "wk"], as_index=False).order_qty.sum())
    tmp2 = rdc_all.copy(); tmp2["dc_id"] = tmp2.dc_id.map(pdc_of_rdc)       # roll RDC throughput up to its plant DC
    pdc_all = tmp2.dropna(subset=["dc_id"]).groupby(["dc_id", "sku_id", "wk"], as_index=False).order_qty.sum()
    node_dem = pd.concat([cdc_dem, rdc_all, pdc_all], ignore_index=True)

    # =====================================================================================
    # WS-H: traced replenishment lead time per stocking node, PLANT -> PDC -> RDC -> CDC.
    # v3 hardcoded lt = 1 week for every node, so network_lanes.lead_time_* was decorative:
    # corr(safety_stock, lane lead time) = -0.05 while real lane lead times span 0.5-10.7 days.
    # Measured effect of fixing it: safety-stock value -51%, with 73% of SKU-DCs over-buffered
    # (notably the plant DCs, 0.5 days from their plant yet holding a full week of cover).
    # =====================================================================================
    NODE_LT = {}                                    # node -> (mean days, std days)
    for _, r in lanes_df[lanes_df.origin_node_type == "PLANT"].iterrows():
        NODE_LT[r.destination_node_id] = (r.lead_time_mean_days, r.lead_time_std_days)
    for dest, r in (lanes_df[(lanes_df.origin_node_type == "PLANT_DC")
                             & (lanes_df.destination_node_type == "REGIONAL_DC")]
                    .sort_values("lead_time_mean_days").groupby("destination_node_id").first().iterrows()):
        m0, s0 = NODE_LT[r.origin_node_id]
        NODE_LT[dest] = (m0 + r.lead_time_mean_days, math.hypot(s0, r.lead_time_std_days))
    for dest, r in (lanes_df[lanes_df.destination_node_type == "CUSTOMER_DC"]
                    .sort_values("lead_time_mean_days").groupby("destination_node_id").first().iterrows()):
        m0, s0 = NODE_LT[r.origin_node_id]
        NODE_LT[dest] = (m0 + r.lead_time_mean_days, math.hypot(s0, r.lead_time_std_days))
    UPSTREAM = {}                                   # node -> where it replenishes from
    for _, r in lanes_df[lanes_df.origin_node_type == "PLANT"].iterrows():
        UPSTREAM[r.destination_node_id] = r.origin_node_id
    UPSTREAM.update(pdc_of_rdc.to_dict()); UPSTREAM.update(rdc_of_cdc.to_dict())

    # z by segment, from the tiers the generator already publishes -- v3 used a single 2.05 for
    # every SKU while also writing a 3-tier target file, so the data contradicted its own policy.
    Z_BY_SEGMENT = {"Fast-Moving": 2.05, "Seasonal": 1.64, "Intermediate": 1.28}
    seg_of_sku = {s: m["segment"] for s, m in sku_meta.items()}
    _sm = pd.read_csv(f"{OUT}/sku_master.csv").set_index("sku_id")
    shelf_of = _sm.shelf_life_days.to_dict()
    cost_of = _sm.unit_cost_usd.to_dict()
    marg_of = _sm.margin_usd.to_dict()
    # WS-L: DSD / c-store channels lose the sale; warehouse (BULK) channels backorder
    _deliv = ctx["cust_deliv"]
    dsd_dcs = set(orders_daily.loc[orders_daily.b2b_customer_id.map(_deliv) == "DSD", "dc_id"].unique())

    inv_rows, rcpt_rows, wo_rows = [], [], []
    rcpt_seq = 0
    for (dcid, sid), g in node_dem.groupby(["dc_id", "sku_id"]):
        wk_qty = np.zeros(TOTAL_WEEKS + 2)
        for r in g.itertuples():
            w = int(r.wk)
            if EVAL_START <= w <= ACTUAL_WEEKS: wk_qty[w] += r.order_qty
        _win = wk_qty[EVAL_START:ACTUAL_WEEKS + 1]
        avg = _win.mean(); std = _win.std()
        L_d, sL_d = NODE_LT.get(dcid, (7.0, 1.0))
        L, sL = L_d / 7.0, sL_d / 7.0                        # weeks
        # The COMMITTED lead time a supplier quotes sits above the mean, or half of all
        # deliveries are late by construction. Quoting the p90 gives a realistic ~90% OTD with
        # a genuine late tail -- which is what makes OTD worth measuring.
        promised_d = L_d + 1.28 * sL_d
        z = Z_BY_SEGMENT.get(seg_of_sku.get(sid, "Seasonal"), 1.64)
        # safety stock now carries BOTH risk sources; the v3 `max(avg*0.6, ...)` days-of-supply
        # floor is gone (it bound for 11.6% of SKU-DCs and had no service-level meaning).
        safety = int(z * math.sqrt(L * std ** 2 + (avg ** 2) * (sL ** 2)))
        reorder = int(avg * L + safety)
        S = int(reorder + 3 * avg)
        # WS-K: cap the order-up-to level at what shelf life can physically carry. In the
        # "relaxed" profile shelf_wk is huge so this never binds -- which is the point of v4.2.
        shelf_wk = max(2, int(shelf_of.get(sid, 365) / 7))   # floor 2: weekly buckets cannot express <1wk
        S = min(S, max(reorder + 1, int(avg * shelf_wk * 0.8))) if avg > 0 else S

        cohorts = deque([[0, S]])                            # FEFO queue: [age_weeks, units]
        pipe = {}
        for wk in range(EVAL_START, ACTUAL_WEEKS + 1):
            rec = pipe.pop(wk, 0)
            if rec > 0:
                cohorts.append([0, rec])
            # --- WS-K: age, then expire anything past shelf life
            for c in cohorts:
                c[0] += 1
            expired = 0
            while cohorts and cohorts[0][0] > shelf_wk:
                expired += cohorts.popleft()[1]
            if expired > 0:
                wo_rows.append((period_label(wk), week_start(wk), sid, dcid, int(expired),
                                round(expired * cost_of.get(sid, 0.0), 2), "expiry"))
            opening = sum(c[1] for c in cohorts)
            # --- FEFO issue: consume the oldest cohort first
            need = wk_qty[wk]; shipped = 0.0
            while need > 0 and cohorts:
                take = min(need, cohorts[0][1])
                cohorts[0][1] -= take; shipped += take; need -= take
                if cohorts[0][1] <= 0:
                    cohorts.popleft()
            unmet = max(0.0, wk_qty[wk] - shipped)           # WS-L
            closing = sum(c[1] for c in cohorts)
            pos = closing + sum(pipe.values())
            if pos <= reorder:
                qty = S - pos
                # WS-H: realized lead time varies per receipt, so late deliveries are real.
                # Floor scales with the lane: a fixed 0.15-week floor exceeded the actual lead
                # time of the short plant->PDC lanes (0.5 days) and made them permanently late.
                L_act = max(L * 0.35, RNG.normal(L, sL))
                arrive = wk + int(math.ceil(L_act))
                if qty > 0:
                    pipe[arrive] = pipe.get(arrive, 0) + qty
                    rcpt_seq += 1
                    rcpt_rows.append((f"RCP{rcpt_seq:07d}", period_label(wk), week_start(wk),
                                      sid, dcid, UPSTREAM.get(dcid, "PLANT01"),
                                      int(round(qty)), int(round(qty)),
                                      round(promised_d, 2), round(L_act * 7, 2),
                                      week_start(min(wk + int(math.ceil(promised_d / 7)), ACTUAL_WEEKS)),
                                      week_start(min(arrive, ACTUAL_WEEKS)),
                                      int(L_act * 7 <= promised_d + 1e-9)))
            inv_rows.append((period_label(wk), week_start(wk), sid, dcid, int(opening - rec),
                             int(rec), int(round(wk_qty[wk])), int(round(shipped)),
                             int(round(unmet)),
                             round(unmet * marg_of.get(sid, 0.0), 2),
                             "lost_sale" if dcid in dsd_dcs else "backorder",
                             int(closing), int(safety), int(reorder), int(S),
                             round(L_d, 2), round(sL_d, 2)))
    inv = pd.DataFrame(inv_rows, columns=[
        "period", "week_start_date", "sku_id", "dc_id", "opening_stock", "receipts",
        "demand_qty", "shipments", "unmet_qty", "lost_margin_usd", "fulfilment_type",
        "closing_stock", "safety_stock", "reorder_point", "order_up_to_level",
        "lead_time_mean_days", "lead_time_std_days"])
    inv.to_csv(f"{OUT}/inventory.csv", index=False)

    # WS-I: the replenishment fact table -- promised vs actual lead time makes lead-time
    # variability and OTD MEASURABLE from history rather than a parameter nobody consumes.
    rcpt = pd.DataFrame(rcpt_rows, columns=[
        "receipt_id", "period", "order_week_start", "sku_id", "dc_id", "origin_node_id",
        "qty_ordered", "qty_received", "promised_lead_days", "actual_lead_days",
        "promised_date", "actual_date", "on_time_flag"])
    rcpt.to_csv(f"{OUT}/replenishment_receipts.csv", index=False)

    # WS-K: the write-off table -- the charter's third impact area, absent from v3 entirely.
    wo = pd.DataFrame(wo_rows, columns=["period", "week_start_date", "sku_id", "dc_id",
                                        "units_written_off", "write_off_cost_usd", "reason"])
    wo.to_csv(f"{OUT}/write_offs.csv", index=False)

    # Carry the 2025-W52 closing position into a 2026-W01 opening state. v3 had no initial
    # condition at all, so a MEIO run or twin replay on the forward horizon had nothing to
    # start from.
    _last = inv[inv.period == period_label(ACTUAL_WEEKS)]
    _open = _last[["sku_id", "dc_id", "closing_stock", "safety_stock", "reorder_point",
                   "order_up_to_level", "lead_time_mean_days", "lead_time_std_days"]].copy()
    _open = _open.rename(columns={"closing_stock": "opening_stock"})
    _open.insert(0, "week_start_date", week_start(ACTUAL_WEEKS + 1))
    _open.insert(0, "period", period_label(ACTUAL_WEEKS + 1))
    _open.to_csv(f"{OUT}/opening_inventory_2026.csv", index=False)
    print(f"  opening position for {period_label(ACTUAL_WEEKS+1)}: {len(_open):,} SKU-DC rows "
          f"({_open.opening_stock.sum():,.0f} units) -> the MEIO execution starting state")

    _ssf = inv.groupby(["sku_id", "dc_id"], as_index=False).safety_stock.first()
    _ss_val = float((_ssf.safety_stock * _ssf.sku_id.map(cost_of)).sum())
    _fill = 1 - inv.unmet_qty.sum() / max(inv.demand_qty.sum(), 1)
    print(f"  inventory rows: {len(inv):,} | echelons: {inv.dc_id.str[:3].nunique()} node-types | "
          f"zero-closing weeks {(inv.closing_stock==0).mean():.1%}")
    print(f"  WS-H lead time {inv.lead_time_mean_days.min():.1f}-{inv.lead_time_mean_days.max():.1f}d "
          f"(v3: flat 7d) | safety-stock value ${_ss_val/1e6:.2f}M")
    print(f"  WS-I receipts: {len(rcpt):,} | OTD {rcpt.on_time_flag.mean():.1%} | "
          f"actual lead {rcpt.actual_lead_days.min():.1f}-{rcpt.actual_lead_days.max():.1f}d")
    print(f"  WS-K write-offs: {len(wo):,} rows, {wo.units_written_off.sum():,.0f} units, "
          f"${wo.write_off_cost_usd.sum()/1e6:.2f}M | WS-L unit fill rate {_fill:.1%}")

    # ---- service-level targets by segment (Service-Level Optimization objective) ----
    seg_targets = {"Fast-Moving": (0.98, 0.96), "Seasonal": (0.95, 0.92), "Intermediate": (0.90, 0.88)}
    pd.DataFrame([[s, sl, cfr] for s, (sl, cfr) in seg_targets.items()],
                 columns=["segment", "service_level_target_pct", "case_fill_rate_target_pct"]
                 ).to_csv(f"{OUT}/sku_customer_service_targets.csv", index=False)
    print(f"  service targets: {len(seg_targets)} segment tiers written")


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
    _ex = promos[promos.plan_status == "executed"]
    chk("promotions: executed depth in (0,1); forward PLANS carry planned depth but no actual",
        bool(_ex.discount_pct.between(0.0, 1.0).all() and (_ex.discount_pct > 0).all()
             and promos.planned_discount_pct.between(0.0, 1.0).all()
             and promos.loc[promos.plan_status == "planned", "discount_pct"].isna().all()))
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
    chk("inventory.dc_id -> stocking + plant DCs", set(inv.dc_id) <= (stock_dcs | set(pdc.plant_dc_id)))
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

    # ---- no-gap coverage audit (every objective must have complete data) ----
    served = set(serve.dc_id)
    feeds_cdc = set(lanes[(lanes.origin_node_type == "REGIONAL_DC") & (lanes.destination_node_type == "CUSTOMER_DC")].origin_node_id)
    inv_nodes = set(inv.dc_id)
    all_dc = set(rdc.regional_dc_id) | set(cdc.customer_dc_id) | set(pdc.plant_dc_id)
    chk("no-gap: every Customer DC has a customer attached", set(cdc.customer_dc_id) <= served)
    chk("no-gap: every Regional DC serves or feeds demand", set(rdc.regional_dc_id) <= (served | feeds_cdc))
    chk("no-gap: every stocking + plant DC has inventory flow (multi-echelon)", all_dc <= inv_nodes)
    chk("no-gap: every criticality tier has a service target", pd.read_csv(f"{OUT}/sku_customer_service_targets.csv").shape[0] >= 3)
    if os.path.exists(f"{OUT}/scenarios.csv"):
        scn = pd.read_csv(f"{OUT}/scenarios.csv")
        need = {"demand_surge","forecast_miss","lane_outage","leadtime_spike","plant_shortfall","geographic_disruption"}
        chk("no-gap: scenario library covers all disruption classes", need <= set(scn.scenario_type))
        _sd = pd.to_datetime(scn.start_date)
        # compare against the forward window's own first Monday: period 2026-W01 begins on
        # 2025-12-29, so a hardcoded Jan-1 cutoff would wrongly reject week 157.
        _fwd0 = pd.Timestamp(week_start(ACTUAL_WEEKS + 1))
        chk(f"WS-B every scenario is dated in the FORWARD horizon, from {_fwd0.date()} "
            f"(v3: 0 of 30 were)", bool((_sd >= _fwd0).all()))
        chk("WS-B magnitude is numeric with a unit column (v3: a string like '+105pct')",
            bool(pd.api.types.is_numeric_dtype(scn.magnitude_value)
                 and scn.magnitude_value.notna().all()
                 and set(scn.magnitude_unit) <= {"pct_uplift","pct_shortfall","days_added","nodes_offline"}))
        chk("WS-B impact_dimension categorical and probability populated",
            bool(set(scn.impact_dimension) <= {"service","cost","working_capital","waste"}
                 and scn.probability.between(0, 1).all()))

    # =====================================================================
    # v4 PHASE-0/1 ASSERTIONS -- each one stops a specific audit finding from regressing.
    # v3's value is shown in the label so a failure is self-explanatory.
    # =====================================================================
    print("\n--- v4 Phase 0/1 assertions ---")
    seas = pd.read_csv(f"{OUT}/seasonality_master.csv")
    ridx = pd.read_csv(f"{OUT}/regional_demand_index.csv")
    terms = pd.read_csv(f"{OUT}/b2b_customer_terms.csv")
    srcg = pd.read_csv(f"{OUT}/sku_plant_sourcing.csv")
    cap = pd.read_csv(f"{OUT}/plant_capability.csv")

    # WS-A: per-unit price basis (v3: Energy Drink median $108.37/unit)
    med = sku.groupby("subcategory").list_price_usd.median()
    lo, hi = SUBCAT_MEDIAN_PRICE_BAND
    _gen = med.drop(index=[c for c in PREMIUM_SUBCATS if c in med.index])
    chk(f"WS-A grocery subcategory median unit price within ${lo}-${hi} "
        f"(v3: Energy Drink $108.37/unit)", bool(_gen.between(lo, hi).all()))
    _prem = med.reindex([c for c in PREMIUM_SUBCATS if c in med.index]).dropna()
    chk(f"WS-A premium alcohol within ${PREMIUM_PRICE_BAND[0]}-${PREMIUM_PRICE_BAND[1]} "
        f"(documented exception: spirits are genuinely $20-80/bottle)",
        bool(_prem.between(*PREMIUM_PRICE_BAND).all()))
    # exact reconciliation: list = round(case / units_per_case x brand multiplier, 2). A ratio
    # band cannot be used here because 2-decimal rounding on sub-dollar SKUs is a large
    # relative error.
    _bmult = {b: m for tiers in CATEGORY_BRANDS.values() for b, m in tiers}
    _exp = (sku.case_price_usd / sku.units_per_case * sku.brand.map(_bmult)).round(2).clip(lower=0.05)
    chk("WS-A list_price == round(case_price / units_per_case x brand multiplier, 2)",
        bool((sku.list_price_usd - _exp).abs().le(0.011).all()))

    # housekeeping
    chk("hierarchy: every subcategory maps to exactly one category (v3: 'Canned Sweet' -> 2)",
        int(sku.groupby("subcategory").category.nunique().max()) == 1)
    chk("seasonal_flag dropped (was 100% collinear with segment)", "seasonal_flag" not in sku.columns)
    _bv = 1 - (sku.groupby("brand").list_price_usd.transform(lambda s: s - s.mean()).var()
               / sku.list_price_usd.var())
    chk(f"WS-brand explains >20% of price variance (eta2={_bv:.3f}; v3: 0.022)", _bv > 0.20)

    # WS-D: subcategory seasonality with a genuinely anti-correlated pair
    piv = seas.pivot(index="month", columns="subcategory", values="combined_index")
    cmat = piv.corr().values
    off = cmat[np.triu_indices(len(cmat), 1)]
    chk(f"WS-D at least one anti-correlated seasonal pair (min corr {off.min():.2f}; v3 min +0.57)",
        bool(off.min() < -0.5))
    chk("WS-D seasonality_master covers every subcategory",
        set(seas.subcategory) == set(sku.subcategory))

    # WS-F: regional differentiation actually shows up in ORDERS
    _dcst = pd.concat([rdc.rename(columns={"regional_dc_id": "dc_id"})[["dc_id", "region"]],
                       cdc.rename(columns={"customer_dc_id": "dc_id"})[["dc_id", "region"]]])
    _m = orders.merge(sku[["sku_id", "subcategory"]], on="sku_id").merge(_dcst, on="dc_id")
    _mix = _m.groupby(["region", "subcategory"]).order_qty.sum().unstack(fill_value=0)
    _mix = (_mix.T / _mix.sum(axis=1)).T
    # Measure in INDEX space (each region's share relative to the national share). Raw share
    # correlation is dominated by subcategory SIZE -- Frozen Vegetable has 91 SKUs and Tea has 1,
    # so raw correlation stays ~0.99 however strong the regional effect is.
    _nat = _m.groupby("subcategory").order_qty.sum(); _nat = _nat / _nat.sum()
    _rel = np.log((_mix / _nat).replace(0, np.nan))
    _rc = _rel.T.corr().values
    _rmax = _rc[np.triu_indices(len(_rc), 1)].max()
    chk(f"WS-F max pairwise region correlation in index space < 0.75 (got {_rmax:.3f}; v3: ~1.0)",
        _rmax < 0.75)
    _dev = float((_mix / _nat).max().max())
    chk(f"WS-F at least one subcategory 1.4x over-indexed in some region (max {_dev:.2f}x)", _dev > 1.4)
    chk("WS-F regional_demand_index covers every subcategory",
        set(ridx.subcategory) == set(sku.subcategory))

    # WS-G: customer net pricing
    _pk = orders.groupby("b2b_customer_id").apply(
        lambda g: (g.net_price_usd * g.order_qty).sum() / (g.list_price_usd * g.order_qty).sum(),
        include_groups=False)
    _spread = float(_pk.max() - _pk.min())
    chk(f"WS-G pocket-price spread across customers 5-30% (got {_spread:.1%}; v3: 0.4%)",
        0.05 <= _spread <= 0.30)
    chk("WS-G net_price <= invoice_price <= base_price on every line",
        bool((orders.net_price_usd <= orders.invoice_price_usd + 1e-6).all()
             and (orders.invoice_price_usd <= orders.base_price_usd + 1e-6).all()))
    chk("WS-G every banner has trade terms", set(terms.b2b_customer_id) == set(banners.b2b_customer_id))

    # WS-C (partial): promo intensity
    _pv = orders.loc[orders.on_promo, "order_qty"].sum() / orders.order_qty.sum()
    chk(f"WS-C promo share of volume 20-45% (got {_pv:.1%}; v3: 11.6%)", 0.20 <= _pv <= 0.45)
    chk("WS-C planned vs actual promo depth differ for >50% of events",
        float((promos.planned_discount_pct != promos.discount_pct).mean()) > 0.50)

    # §9.5: base elasticity identifiable
    _nm = orders.groupby("sku_id").apply(
        lambda g: (g.net_margin_usd.sum() / g.net_revenue_usd.sum()) if g.net_revenue_usd.sum() else 0.0,
        include_groups=False)
    chk(f"economics: <10% of SKUs have a negative NET margin after trade spend "
        f"(got {(_nm < 0).mean():.1%})", float((_nm < 0).mean()) < 0.10)
    _below = orders.net_price_usd < orders.sku_id.map(sku.set_index("sku_id").unit_cost_usd)
    _bv = float(orders.loc[_below, "order_qty"].sum() / orders.order_qty.sum())
    chk(f"economics: <10% of VOLUME priced below unit cost (loss-leaders only; got {_bv:.1%})",
        _bv < 0.10)
    _sd = float(orders.groupby("sku_id").base_price_usd.apply(lambda s: np.log(s).std()).mean())
    chk(f"base-price ln-sd > 0.05 so base elasticity is identifiable (got {_sd:.3f}; v3: 0.027)",
        _sd > 0.05)

    # WS-E: multi-sourcing
    _dual = float((srcg.groupby("sku_id").size() > 1).mean())
    chk(f"WS-E dual-sourced SKU share 15-40% (got {_dual:.0%}; v3: 0%)", 0.15 <= _dual <= 0.40)
    chk("WS-E every SKU has exactly one primary plant",
        bool((srcg[srcg.is_primary].groupby("sku_id").size() == 1).all()))
    chk("WS-E allocation shares sum to 1 per SKU",
        bool(srcg.groupby("sku_id").allocation_share.sum().round(2).eq(1.00).all()))
    _fam = sku.set_index("sku_id").production_family.to_dict()
    _capset = {(r.plant_id, r.production_family) for r in cap.itertuples()}
    chk("WS-E every sourcing assignment respects plant capability",
        all((r.plant_id, _fam[r.sku_id]) in _capset for r in srcg.itertuples()))
    # Geographic quality is measured as demand-weighted freight distance from the assigned plant,
    # against v3's round-robin baseline. "Does the plant sit in the top-demand region" is NOT a
    # valid test here: the West holds 7 of 27 DCs so it is the top region for most SKUs, and
    # there is no Northeast plant at all, so the metric is degenerate.
    _pl = pd.read_csv(f"{OUT}/plant_master.csv")
    _lanes_pd = lanes[(lanes.origin_node_type == "PLANT")]
    _dcst2 = pd.concat([rdc.rename(columns={"regional_dc_id": "dc_id"})[["dc_id", "city", "state"]],
                        cdc.rename(columns={"customer_dc_id": "dc_id"})[["dc_id", "city", "state"]]])
    _pcity = dict(zip(_pl.plant_id, _pl.city + ", " + _pl.state))
    _dcity = dict(zip(_dcst2.dc_id, _dcst2.city + ", " + _dcst2.state))
    _CO = {"Atlanta, GA": (33.75, -84.39), "Dallas, TX": (32.78, -96.80), "Chicago, IL": (41.88, -87.63),
           "Los Angeles, CA": (34.05, -118.24), "Newark, NJ": (40.74, -74.17), "Charlotte, NC": (35.23, -80.84),
           "Tampa, FL": (27.95, -82.46), "Denver, CO": (39.74, -104.99), "Seattle, WA": (47.61, -122.33),
           "Boston, MA": (42.36, -71.06), "Philadelphia, PA": (39.95, -75.17), "Nashville, TN": (36.16, -86.78),
           "Miami, FL": (25.76, -80.19), "Orlando, FL": (28.54, -81.38), "Minneapolis, MN": (44.98, -93.27),
           "St. Louis, MO": (38.63, -90.20), "Detroit, MI": (42.33, -83.05), "Houston, TX": (29.76, -95.37),
           "Phoenix, AZ": (33.45, -112.07), "Sacramento, CA": (38.58, -121.49)}

    def _d(a, b):
        (la1, lo1), (la2, lo2) = _CO[a], _CO[b]
        p1, p2 = math.radians(la1), math.radians(la2)
        h = (math.sin(math.radians(la2 - la1) / 2) ** 2
             + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2)
        return 3958.8 * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))

    _dw = orders.groupby(["sku_id", "dc_id"]).order_qty.sum().reset_index()
    _prim = srcg[srcg.is_primary].set_index("sku_id").plant_id.to_dict()
    _plist = _pl.plant_id.tolist()
    _rr = {s: _plist[i % len(_plist)] for i, s in enumerate(sorted(sku.sku_id))}   # v3 baseline
    _new = _base = _tw = 0.0
    for r in _dw.itertuples():
        if r.dc_id not in _dcity:
            continue
        d_new = _d(_pcity[_prim[r.sku_id]], _dcity[r.dc_id])
        d_old = _d(_pcity[_rr[r.sku_id]], _dcity[r.dc_id])
        _new += d_new * r.order_qty; _base += d_old * r.order_qty; _tw += r.order_qty
    _impr = 1 - (_new / _tw) / (_base / _tw)
    # Threshold is 10%, not more: with 4 plants and these capability sets the choice set per
    # family is only 2-3 sites, and capacity budgets deliberately spread load rather than always
    # taking the closest option. A ~12% freight reduction is the realistic ceiling here.
    chk(f"WS-E demand-weighted plant->DC distance beats v3 round-robin by >10% "
        f"({_new/_tw:.0f} vs {_base/_tw:.0f} mi, {_impr:.1%} better)", _impr > 0.10)
    chk(f"WS-E no plant is left idle as a primary source "
        f"({srcg[srcg.is_primary].plant_id.nunique()}/{len(_plist)} plants used)",
        srcg[srcg.is_primary].plant_id.nunique() == len(_plist))

    # ---------------- Phase 2 (WS-H/I/K/L) ----------------
    rcpt = pd.read_csv(f"{OUT}/replenishment_receipts.csv")
    wo = pd.read_csv(f"{OUT}/write_offs.csv") if os.path.getsize(f"{OUT}/write_offs.csv") > 40 \
        else pd.DataFrame(columns=["sku_id", "units_written_off", "write_off_cost_usd"])

    # WS-H: lead time now drives the inventory numbers (v3: corr -0.05, ROP implied a flat 0.97wk)
    _iv = inv.groupby(["sku_id", "dc_id"], as_index=False).agg(
        ss=("safety_stock", "first"), rop=("reorder_point", "first"),
        lt=("lead_time_mean_days", "first"), shp=("shipments", "mean"))
    # Correlate safety stock PER UNIT OF DEMAND against lead time. Raw safety stock is dominated
    # by demand scale (lognormal popularity spans orders of magnitude) while lead time spans only
    # 0.5-7.7 days, so the raw correlation is ~0 whatever the lead-time effect is.
    _iv["ss_per_demand"] = _iv.ss / _iv.shp.replace(0, np.nan)
    _c = float(_iv[["ss_per_demand", "lt"]].corr().iloc[0, 1])
    chk(f"WS-H corr(safety_stock per unit demand, lane lead time) > 0.20 "
        f"(got {_c:.3f}; v3: -0.05 on the raw measure)", _c > 0.20)
    _impl = ((_iv.rop - _iv.ss) / _iv.shp.replace(0, np.nan)).dropna()
    chk(f"WS-H implied lead time in ROP varies across nodes, sd > 0.10 wk "
        f"(got {_impl.std():.3f}; v3: ~0 -- every node was 0.97)", _impl.std() > 0.10)
    chk("WS-H lead time recorded on every inventory row",
        bool(inv.lead_time_mean_days.notna().all() and inv.lead_time_mean_days.nunique() > 5))
    _zs = inv.merge(sku[["sku_id", "segment"]], on="sku_id").groupby("segment").safety_stock.mean()
    chk(f"WS-H safety stock differs by segment tier (z 2.05/1.64/1.28, not a flat 2.05)",
        len(_zs) >= 3 and _zs.std() > 0)

    # WS-I: OTD and realized lead time are now measurable from history
    chk(f"WS-I replenishment_receipts emitted ({len(rcpt):,} rows)", len(rcpt) > 1000)
    _otd = float(rcpt.on_time_flag.mean())
    chk(f"WS-I OTD between 80% and 98% (got {_otd:.1%}; v3: not computable at all)",
        0.80 <= _otd <= 0.98)
    chk("WS-I promised and actual lead time both populated and differing",
        bool(rcpt.promised_lead_days.notna().all() and rcpt.actual_lead_days.notna().all()
             and (rcpt.promised_lead_days != rcpt.actual_lead_days).mean() > 0.5))
    _lsd = rcpt.groupby("dc_id").actual_lead_days.std().fillna(0)
    chk(f"WS-I actual lead time varies within every node (min sd {_lsd.min():.2f})", _lsd.min() > 0)

    # WS-L: unmet demand is recorded rather than silently dropped
    chk("WS-L unmet_qty / lost_margin_usd / fulfilment_type present on inventory",
        {"unmet_qty", "lost_margin_usd", "fulfilment_type", "demand_qty"}.issubset(inv.columns))
    _fill = 1 - inv.unmet_qty.sum() / max(inv.demand_qty.sum(), 1)
    chk(f"WS-L unit fill rate between 85% and 99% (got {_fill:.1%})", 0.85 <= _fill <= 0.99)
    chk("WS-L demand = shipments + unmet on every row",
        bool((inv.demand_qty - inv.shipments - inv.unmet_qty).abs().le(1).all()))
    chk("WS-L both fulfilment types occur (DSD loses the sale, BULK backorders)",
        inv.fulfilment_type.nunique() == 2)

    # WS-K: FEFO / write-offs -- profile-dependent by design
    _dos = inv[inv.shipments > 0].merge(sku[["sku_id", "category", "shelf_life_days"]], on="sku_id")
    _viol = float((_dos.closing_stock / (_dos.shipments / 7.0) > _dos.shelf_life_days).mean())
    chk(f"WS-K SKU-weeks holding more stock-days than shelf life < 5% "
        f"(got {_viol:.1%}; v3: 66.6% for Dairy & Bakery)", _viol < 0.05)
    if SHELF_LIFE_PROFILE == "realistic":
        _wr = float(wo.units_written_off.sum()) / max(float(inv.demand_qty.sum()), 1.0)
        chk(f"WS-K write-offs occur and are material ({len(wo):,} rows, "
            f"${wo.write_off_cost_usd.sum()/1e6:.2f}M, {_wr:.2%} of units; v3: zero waste)",
            len(wo) > 0 and _wr > 0.001)
        _fresh = set(sku.loc[sku.subcategory.isin(["Fresh Dairy", "Bakery"]), "sku_id"])
        _share = float(wo.sku_id.isin(_fresh).mean()) if len(wo) else 0.0
        chk(f"WS-K write-offs concentrate in the short-shelf-life tier ({_share:.0%} of rows)",
            _share > 0.50)
    else:
        # Over a 156-week simulation even a 365-730 day shelf life expires for the very slowest
        # movers, so "relaxed" is not literally zero -- it is negligible. Measured against total
        # demand rather than a row count, which does not scale with the sim length.
        _wr = float(wo.units_written_off.sum()) / max(float(inv.demand_qty.sum()), 1.0)
        chk(f"WS-K profile 'relaxed': shelf life cannot bind, so write-offs are nil "
            f"({len(wo):,} rows, {_wr:.4%} of units)", len(wo) <= 5)

    # ---------------- Phase 3 (WS-J/M) and Phase 4 (WS-B) ----------------
    _ins = fc[fc.horizon == "in_sample"]; _fwd = fc[fc.horizon == "forward"]
    _combos_i = _ins.groupby(["sku_id", "dc_id", "b2b_customer_id"]).ngroups
    _combos_f = _fwd.groupby(["sku_id", "dc_id", "b2b_customer_id"]).ngroups
    chk(f"WS-M in-sample panel is COMPLETE ({len(_ins)/(_combos_i*HIST_WEEKS):.0%} filled; v3: 66%)",
        abs(len(_ins) / (_combos_i * HIST_WEEKS) - 1.0) < 0.01)
    chk("WS-M in-sample and forward panels have the same shape (v3: 66% vs 100%)",
        _combos_i == _combos_f and len(_ins) == len(_fwd))
    _nz = int((_ins.actual_qty == 0).sum())
    chk(f"WS-M explicit zero-demand rows exist in history ({_nz:,}; v3: 0 -- unrepresentable)",
        _nz > 1000)
    _c2 = float(_ins[["forecast_qty", "actual_qty"]].corr().iloc[0, 1])
    chk(f"WS-J forecast is not the actual: corr < 0.95 (got {_c2:.3f}; v3: 0.981)", _c2 < 0.95)
    _w = _ins[_ins.actual_qty > 0]
    _wmp = (_w.forecast_qty - _w.actual_qty).abs().groupby(_w.sku_id).sum() /            _w.groupby("sku_id").actual_qty.sum()
    _wmp = _wmp.rename("wmape").reset_index().merge(sku[["sku_id", "segment"]], on="sku_id")
    _g = _wmp.wmape.var(ddof=0)
    _wi = _wmp.groupby("segment").wmape.transform(lambda x: x - x.mean()).var(ddof=0)
    _eta = 1 - _wi / _g if _g > 0 else 1.0
    chk(f"WS-J forecast error is NOT the segment label: eta2 < 0.25 (got {_eta:.3f}; v3: 0.974)",
        _eta < 0.25)
    _pm = orders.groupby(["period", "sku_id", "dc_id", "b2b_customer_id"], as_index=False).on_promo.max()
    _mm = _ins.merge(_pm, on=["period", "sku_id", "dc_id", "b2b_customer_id"], how="left")
    _mm = _mm[(_mm.actual_qty > 0)]
    _ape = (_mm.forecast_qty - _mm.actual_qty).abs() / _mm.actual_qty
    _rat = _ape[_mm.on_promo == True].mean() / max(_ape[_mm.on_promo != True].mean(), 1e-9)
    # Threshold is 1.10, not higher, and that is by design. A naive level forecast leaves promo
    # weeks 1.71x harder (measured), but the baseline/lift model is SUPPOSED to absorb planned
    # promotions -- so the residual gap is only the lift misestimation (+/-35%). What matters is
    # that the gap is real (v3's 1.02x meant the forecast saw the promo spike it claimed to miss).
    chk(f"WS-J promo weeks are genuinely harder to forecast: APE ratio > 1.10 "
        f"(got {_rat:.2f}x; v3: 1.02x, i.e. no gap at all)", _rat > 1.10)
    chk("WS-J baseline + lift == total on every forecast row",
        bool((fc.forecast_baseline_qty + fc.forecast_promo_lift_qty - fc.forecast_qty)
             .abs().le(1).all()))
    chk("WS-J forward rows carry a promo-lift component (v3 had no forward promo calendar)",
        float((_fwd.forecast_promo_lift_qty > 0).mean()) > 0.02)
    chk(f"WS-J actuals span {ACTUAL_WEEKS} weeks incl. a {BURN_WEEKS}-week burn-in "
        f"(v3: 52 weeks, so no seasonal model was possible)",
        orders.period.nunique() == ACTUAL_WEEKS)
    chk("Phase 4: 2026 opening inventory position exists for every stocking SKU-DC",
        os.path.exists(f"{OUT}/opening_inventory_2026.csv")
        and len(pd.read_csv(f"{OUT}/opening_inventory_2026.csv")) ==
            inv.groupby(["sku_id", "dc_id"]).ngroups)
    chk("Phase 4: forward price plan emitted for the execution horizon",
        os.path.exists(f"{OUT}/price_plan.csv") and len(pd.read_csv(f"{OUT}/price_plan.csv")) > 1000)

    # shelf-life profile behaves as configured
    if SHELF_LIFE_PROFILE == "relaxed":
        chk(f"profile 'relaxed': every shelf life within {RELAXED_SHELF_LIFE_DAYS} days",
            bool(sku.shelf_life_days.between(*RELAXED_SHELF_LIFE_DAYS).all()))
    else:
        # Dairy & Bakery legitimately spans BOTH the fresh tier and dry goods now, so the check
        # is on the fresh subcategories rather than the whole category.
        _ft = sku[sku.subcategory.isin(["Fresh Dairy", "Bakery"])]
        chk(f"profile 'realistic': fresh tier is genuinely short-life "
            f"({len(_ft)} SKUs, {_ft.shelf_life_days.min()}-{_ft.shelf_life_days.max()}d)",
            len(_ft) > 0 and int(_ft.shelf_life_days.max()) <= 30)
        chk("profile 'realistic': dry goods in the same category are NOT short-life "
            "(v3 gave flour a 5-day shelf life)",
            int(sku[sku.subcategory.isin(["Wheat Flour", "Yeasts", "Powdered Milk"])]
                .shelf_life_days.min()) >= 180)

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
    156-week actuals period (2023-2025, of which 2023-24 are burn-in for the forecaster and 2025
    is the evaluated year) and a 52-week forward planning horizon (2026) with no actuals -- the
    horizon used to execute MEIO and the other Supply Brain features.

    ## What changed in v4 (see input datasets/V4_IMPLEMENTATION_PLAN.md and DATA_QUALITY_AUDIT.md)

    * **Per-unit prices.** v3 inherited CASE prices under a per-unit label (Energy Drink median
      $108/unit). `case_price_usd` and `units_per_case` are now explicit; `list_price_usd` is the
      derived per-unit price. Spirits/imported wine are a documented high-price exception.
    * **Seasonality at SUBCATEGORY level** (`seasonality_master.csv`), with genuinely
      anti-correlated pairs -- hot Tea peaks in winter, Energy Drink in summer, both "Beverages" --
      and complement clusters (Flour/Yeasts/Sugar/Spices co-peak Oct-Dec).
    * **Regional demand** (`regional_demand_index.csv`): region x subcategory consumption
      multipliers, renormalised so national volume is unchanged. v3's five regions were
      statistically identical.
    * **Customer-specific NET pricing** (`b2b_customer_terms.csv`). List price stays national
      (Robinson-Patman); differentiation is below list via off-invoice, trade funds, rebates and
      logistics allowances. `orders` carries the waterfall down to `net_price_usd` (pocket price).
    * **Multi-sourcing by production family** (`plant_capability.csv`, `sku_plant_sourcing.csv`).
      v3 assigned plants round-robin by index; sourcing is now capacity-constrained and
      geographic.
    * **Lead time drives inventory.** v3 hardcoded 1 week everywhere, so `network_lanes` lead
      times were decorative. Safety stock is now `z * sqrt(L*sd_demand^2 + demand^2*sd_L^2)` with
      a segment-tiered z, and each receipt draws a realised lead time.
    * **`replenishment_receipts.csv`** -- promised vs actual lead time per receipt, so lead-time
      variability and OTD are measurable from history rather than read off a parameter.
    * **FEFO cohorts, `write_offs.csv`, and unmet demand.** Stock now ages and expires; the
      order-up-to level is capped by shelf life; `inventory` records `demand_qty`, `unmet_qty`,
      `lost_margin_usd` and `fulfilment_type` instead of silently dropping the shortfall.
    * **Ex-ante baseline/lift forecast** (see the FORECAST section below).
    * **Scenarios dated into 2026** with numeric `magnitude_value` + `magnitude_unit`.
    * **`opening_inventory_2026.csv`** and **`price_plan.csv`** give the forward horizon an
      initial state and a price plan.
    * `seasonal_flag` dropped (it was 100% identical to `segment == 'Seasonal'`); the
      `Canned Sweet` hierarchy break fixed; `brand` is now category-owned with a price position.

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
    `orders` covers all 156 actual weeks (2023-2025); `production` and `inventory` cover the
    evaluated 2025 window only. `demand_forecast` has a `horizon` column:
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
    * **promotional (deal) elasticity** -- response to a temporary **discount** (`discount_pct`,
        ~30% of VOLUME; v3's documentation claimed ~30% of SKU-weeks but the shipped data reached
        only 11.6% of volume); larger in magnitude than base (grounded at ~1.75x; CPG research
        puts promo ~1.5-2x regular). Promotions are followed by a **forward-buy dip** in the next
        1-2 weeks for storable categories (pantry-loading payback), which is the signature that
        separates a deal response from a base response.
    * Estimate at **`subcategory`** level (22-25 groups), not `category` (5). The subcategory IS
        the real product group each SKU's elasticity was drawn from; pooling to category mixes
        genuinely different responses together.
    * `off_invoice_pct` and the other trade-term columns give **cross-sectional** price variation
        on top of the time-series variation in `base_price_usd`; both are needed to separate base
        from promotional response.

    ## FORECAST -- how `demand_forecast` is built (v4)

    v3 set `forecast_qty = actual_qty * (1 + noise)`, which made the forecast a relabelled copy of
    the actual: `corr(forecast, actual) = 0.981`, and 97.6% of the WMAPE variance was explained by
    the `segment` column alone. It also meant promo weeks were **no harder** than normal weeks,
    directly contradicting v3's own documentation.

    v4 forecasts **ex ante** in three layers:
    * **Layer 0 -- de-batch.** Forecast at SKU x DC x week, then disaggregate to customer by
      historical share. 62% of the apparent volatility at customer grain is order batching, not
      demand.
    * **Layer 1 -- baseline.** De-promote the history, then seasonal-naive from t-52 rescaled by
      the trailing 13-week level ratio. Only weeks strictly before t are ever used. This is why
      2023-24 exist: a seasonal model needs at least two prior cycles.
    * **Layer 2 -- lift.** Uplift per series estimated from PRIOR promo weeks, applied to the
      PLANNED promo calendar with ~35% planner misestimation. Applying the true lift would rebuild
      the leak.
    Three columns are published so error decomposes: `forecast_baseline_qty` (trend/seasonality
    quality) + `forecast_promo_lift_qty` (promo-planning quality) = `forecast_qty`.

    Both panels are **complete**: every (SKU, DC, customer, week) cell exists, with explicit
    zero-demand rows. v3's in-sample panel was 66% filled with no zero-demand rows at all, while
    its forward panel was 100% dense.

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
# LAYER 3: labeled disruption SCENARIO LIBRARY (digital-twin / bandit / agentic input)
# ============================================================================
def generate_scenarios(OUT):
    """WS-B: labelled disruption library, dated into the FORWARD horizon with machine-readable
    magnitudes.

    v3 put all 30 scenarios in 2025 -- the year that already has actuals -- so the digital twin
    could only replay history, never stress the horizon it exists to test. `magnitude` was also a
    free-text string ('+105pct') and `expected_impact` prose, so nothing could consume them
    without a parser. Both are fixed here: every scenario now falls inside 2026, magnitude is
    split into a numeric value plus a unit, and impact is a categorical dimension with the prose
    kept in `notes`.
    """
    rng = np.random.default_rng(77)
    sku = pd.read_csv(f"{OUT}/sku_master.csv"); rdc = pd.read_csv(f"{OUT}/regional_dc_master.csv")
    cdc = pd.read_csv(f"{OUT}/customer_dc_master.csv"); plants = pd.read_csv(f"{OUT}/plant_master.csv")
    inv = pd.read_csv(f"{OUT}/inventory.csv")
    cap = pd.read_csv(f"{OUT}/plant_capability.csv")
    dos = (inv.groupby("dc_id")
           .apply(lambda x: (x.closing_stock.mean() / (x.shipments.mean() / 7.0))
                  if x.shipments.mean() > 0 else 7.0, include_groups=False))
    skus = sku.sku_id.tolist(); rdcs = rdc.regional_dc_id.tolist(); cdcs = cdc.customer_dc_id.tolist()
    plist = plants.plant_id.tolist(); regions = sorted(set(rdc.region) | set(cdc.region))
    fams = sorted(cap.production_family.unique())

    # the forward (2026) window, in absolute week indices
    FWD_LO, FWD_HI = ACTUAL_WEEKS + 1, TOTAL_WEEKS

    def fwd_week(latest_len=1):
        return int(rng.integers(FWD_LO, max(FWD_LO + 1, FWD_HI - latest_len + 1)))

    def win(ws, wl):
        return week_start(ws), (WEEK1_MONDAY + pd.Timedelta(weeks=ws + wl - 1)
                                + pd.Timedelta(days=6)).strftime("%Y-%m-%d")
    rows = []; sid = 1

    def add(typ, node, ntype, sk, region, fam, ws, wl, mag_v, mag_u, ttr, tts, dim, prob, notes):
        nonlocal sid
        sd, ed = win(ws, wl)
        rows.append([f"SCN{sid:04d}", typ, node, ntype, sk, region, fam, sd, ed, int(wl),
                     round(float(mag_v), 3), mag_u, int(ttr), int(tts), dim,
                     round(float(prob), 3), notes])
        sid += 1

    for _ in range(6):
        c = str(rng.choice(cdcs)); wl = int(rng.integers(1, 4))
        add("demand_surge", c, "CUSTOMER_DC", str(rng.choice(skus)), "", "", fwd_week(wl), wl,
            rng.uniform(0.50, 1.50), "pct_uplift", 0, int(dos.get(c, 7)), "service",
            rng.uniform(0.10, 0.35), "unplanned market-driven demand spike")
    for _ in range(5):
        wl = 2
        add("forecast_miss", "", "", str(rng.choice(skus)), "", "", fwd_week(wl), wl,
            0.20, "pct_shortfall", 0, 0, "service", rng.uniform(0.15, 0.40),
            "seasonal peak under-called by the baseline forecast")
    for _ in range(6):
        c = str(rng.choice(cdcs)); wl = int(rng.integers(1, 3))
        add("lane_outage", c, "CUSTOMER_DC", "", "", "", fwd_week(wl), wl,
            1.0, "nodes_offline", int(rng.integers(3, 8)), int(dos.get(c, 7)), "service",
            rng.uniform(0.05, 0.20),
            "primary inbound lane down; requires failover RDC or lateral transfer")
    for _ in range(5):
        r = str(rng.choice(rdcs)); wl = int(rng.integers(2, 5))
        add("leadtime_spike", r, "REGIONAL_DC", "", "", "", fwd_week(wl), wl,
            rng.uniform(4.0, 10.0), "days_added", 0, int(dos.get(r, 10)), "service",
            rng.uniform(0.10, 0.30), "carrier / rail delay on the inbound lane")
    for _ in range(4):
        p = str(rng.choice(plist))
        fam = str(rng.choice([f for f in fams if f in set(cap[cap.plant_id == p].production_family)]))
        wl = int(rng.integers(1, 4))
        add("plant_shortfall", p, "PLANT", "", "", fam, fwd_week(wl), wl,
            rng.uniform(0.20, 0.40), "pct_shortfall", int(rng.integers(5, 14)), 0, "cost",
            rng.uniform(0.08, 0.25),
            "schedule-attainment drop on one production family at this plant")
    for _ in range(4):
        reg = str(rng.choice(regions)); wl = int(rng.integers(2, 6))
        add("geographic_disruption", "", "REGION", "", reg, "", fwd_week(wl), wl,
            rng.uniform(0.30, 0.70), "pct_shortfall", int(rng.integers(7, 21)), 0, "service",
            rng.uniform(0.03, 0.12), "natural disaster / infrastructure / regulatory")

    pd.DataFrame(rows, columns=[
        "scenario_id", "scenario_type", "affected_node_id", "affected_node_type",
        "affected_sku_id", "region", "affected_family", "start_date", "end_date",
        "duration_weeks", "magnitude_value", "magnitude_unit", "ttr_days", "tts_days",
        "impact_dimension", "probability", "notes"]).to_csv(f"{OUT}/scenarios.csv", index=False)
    print(f"  scenarios: {len(rows)} labelled disruptions across "
          f"{len(set(r[1] for r in rows))} classes, all dated in the 2026 forward horizon, "
          f"magnitude numeric")


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
    tbls=["sku_master","orders","promotions","demand_forecast","production","inventory","scenarios","sku_customer_service_targets","network_lanes",
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
         ("inventory.dc_id in stocking + plant DCs",set(inv.dc_id)<=(stock|set(pdc.plant_dc_id))),
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
      f". Seasonal segment SKUs: {int((sku.segment=='Seasonal').sum())}/{len(sku)}"
      f". Brands: {sku.brand.nunique()} (category-owned). "
      f"Production families: {sku.production_family.nunique()}.\n")
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
                 np.where(invc.dc_id.astype(str).str.startswith("CDC"),"Customer DC",
                 np.where(invc.dc_id.astype(str).str.startswith("PDC"),"Plant DC","Other")))
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

    # 2.15 disruption scenario library
    A("## 2.15 Disruption scenario library (digital-twin / bandit input)\n")
    if os.path.exists(f"{OUT}/scenarios.csv"):
        scn=pd.read_csv(f"{OUT}/scenarios.csv")
        A("A labeled library of demand-side and supply-side disruptions with Time-to-Recover (TTR) and "
          "Time-to-Survive (TTS) estimates - the what-if inputs the digital twin, bandit reward, and agentic "
          "layer consume. Each row names the affected node/SKU/region and window.\n")
        sc=scn.groupby("scenario_type").agg(count=("scenario_id","count"),avg_ttr_days=("ttr_days","mean"),
            avg_tts_days=("tts_days","mean")).round(1).reset_index()
        A(_tab(sc)+"\n")
        A(f"\nTotal scenarios: {len(scn)} across {scn.scenario_type.nunique()} classes (supplier, geographic, "
          "systemic, market-driven).\n")
    else:
        A("*scenarios.csv not present.*\n")

    # 2.16 key findings
    A("## 2.16 Key findings\n")
    top_cat=bc.sort_values("revenue",ascending=False).iloc[0]
    most_el=min(er,key=lambda x:x[1]); least_el=max(er,key=lambda x:x[1])
    findings=[
      f"Revenue is led by {top_cat['category']} ({top_cat['rev_pct']} pct of sales); demand is steeply concentrated (top 20 pct SKUs ~ {top20:.0f} pct of revenue).",
      f"Most price-elastic category (base): {most_el[0]} ({most_el[1]}); least: {least_el[0]} ({least_el[1]}). Promo elasticity exceeds base in {sum(1 for e in er if e[2]<=e[1])}/{len(er)} categories.",
      f"Forecast accuracy degrades from fast-movers to seasonal SKUs (see 2.9); forward year grows ~{(fwdu/histu-1)*100:.0f} pct YoY.",
      f"Network is fully dual-sourced ({int((cdc_src>=2).sum())}/{len(cdc)} Customer DCs with failover) with {n_lat} lateral lanes; storage utilization averages {du.util_pct.mean():.0f} pct.",
      f"Availability ~ {avail:.0f} pct at ${wk_val.mean():,.0f} avg working capital and {turns:.1f}x turns.",
      f"No-gap coverage: every Customer DC has demand, inventory is modelled at all echelons (Plant DC, Regional DC, Customer DC), and a {len(pd.read_csv(f'{OUT}/scenarios.csv')) if os.path.exists(f'{OUT}/scenarios.csv') else 0}-scenario disruption library covers demand- and supply-side stresses.",
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
    print("\n=== STEP 3b: labeled disruption scenario library ===")
    generate_scenarios(OUTPUT_DIR)
    print("\n=== STEP 4: referential-integrity + no-gap coverage validation ===")
    ok = validate()
    print("\n=== STEP 5: documentation ===")
    write_documentation()
    print("\n=== STEP 6: bundle reference docs (optional, guarded) ===")
    bundle_reference_docs()
    print("\n=== STEP 7: comprehensive EDA report ===")
    generate_eda(OUTPUT_DIR)
    print(f"\nAll tables + docs + EDA written to: {OUTPUT_DIR}\nTotal runtime: {time.time()-t0:.1f}s")
    return ok


def _run_profile(profile):
    """Re-exec this script for one shelf-life profile, so module-level config is rebuilt cleanly."""
    import subprocess
    env = dict(os.environ, SUPPLY_BRAIN_SHELF_PROFILE=profile)
    env.pop("SUPPLY_BRAIN_OUTPUT", None)
    print(f"\n{'#'*78}\n### SHELF_LIFE_PROFILE = {profile}  ->  {_PROFILE_DIR[profile]}\n{'#'*78}")
    return subprocess.run([sys.executable, os.path.abspath(__file__)], env=env).returncode


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "both":
        # produce BOTH requested datasets: v4.1 (realistic shelf life) and v4.2 (relaxed)
        rc = 0
        for prof in ("realistic", "relaxed"):
            rc |= _run_profile(prof)
        print(f"\nBoth datasets written. v4.1 = realistic shelf life, "
              f"v4.2 = every SKU {RELAXED_SHELF_LIFE_DAYS[0]}-{RELAXED_SHELF_LIFE_DAYS[1]} days.")
        raise SystemExit(rc)
    main()

