"""
Supply Brain — Dataset Generator
================================

Generates the Supply Brain dataset for the capstone: a four-echelon packaged-
foods supply network, three years of commercial history, a forward planning
horizon, and the inventory record that goes with them.

    Plant --0.5d--> Plant DC --> Regional DC --> Customer DC

The plant is production-only. Plant DCs are the first stock-holding echelon
and are pushed finished goods by production. Regional DCs carry both their own
direct bulk-customer demand and the demand of the Customer DCs beneath them;
both RDCs and CDCs pull on a weekly reorder-point review.


input datasets/ vs output datasets/ — the line is TIME
------------------------------------------------------
    input datasets/     THE PAST AND THE GIVENS. Network and product masters,
                        cost structure, the order history, the promotions that
                        ran, the published forecast, the disruption library,
                        and the inventory record of what actually happened.
                        This is the brief.

    output datasets/    THE FORWARD VIEW. Anything computed from the forecast
                        or describing the planning horizon: the forward policy,
                        the position the horizon opens on, and every scenario
                        run. One folder per run.

So the historical inventory, receipts and production tables live in
`input datasets/` even though the twin produces them — they are the record of
the past, which is input to any analysis. What moves to `output datasets/` is
the part that looks forward.

The horizon boundary is derived, never hardcoded to a calendar year: history is
the weeks covered by the order history, and the forward horizon is everything
after it. Change START_YEAR or the week counts and every label, filename and
split follows.


Where the physics lives
-----------------------
This script simulates nothing. All inventory movement — ordering, allocation,
shipping, production, stockouts — happens in the digital twin at
`models/twin/digital_twin.py`, the project's single physics engine. The same
engine scores candidate policies, so the history a policy is measured against
and the outcome it is scored on come from identical mechanics. Two
implementations of that physics would disagree by a few points of fill rate,
and every optimization result would then carry an unanswerable question about
whether the gain was real.

What this script owns is everything around the physics: the world, the
sourcing map, plant capability, the baseline policy, and orchestration.


Stages
------
    --stage world     build the world into input datasets/ (slow)
    --stage history   policy -> twin -> historical inventory into
                      input datasets/, forward artefacts into
                      output datasets/<run>/, then validate and document
    --stage all       both (default)

The history stage produces the two files a forward scenario consumes:

    python models/twin/digital_twin.py --mode forward --run-name baseline \
        --policy "output datasets/<run>/forward_policy.csv" \
        --state  "output datasets/<run>/opening_position.csv"


The policy is a first-class artefact
------------------------------------
`(safety_stock, reorder_point, order_up_to_level)` per node-SKU is a file, not
something buried in simulation code. It is the only input an optimizer
changes: a candidate policy is the same CSV with different numbers. An
optimized run and the baseline therefore differ by exactly one file, which is
what makes the comparison mean anything.
"""
import os
from pathlib import Path
import argparse
import re
import sys
import importlib.util
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
OUTPUT_DIR = os.environ.get("SUPPLY_BRAIN_OUTPUT", os.path.join(BASE_DIR, "input datasets"))

# ---------------------------------------------------------------------------
# SHELF LIFE
# Every SKU gets 3-4 years, which EXCEEDS the 156-week (1,092-day) simulated span, so stock can
# never age out: write-offs are exactly zero and the perishability constraint is inert by design.
# This keeps MEIO / service-level / buffer development free of that confound.
#
# Consequence to be aware of: the newsvendor critical ratio loses its obsolescence term, so it
# pins near 1 for every SKU. Any service-level ladder built on this data is therefore driven by
# strategic value, NOT by economics.
# ---------------------------------------------------------------------------
SHELF_LIFE_DAYS = (1095, 1460)


# WS-J: a seasonal forecast needs >=2 prior cycles, so 2023-24 are generated as BURN-IN history.
# v3 had only 2025, which is why no method could see seasonality and every segment collapsed to
# roughly the same error. Layout: 2023-24 burn-in | 2025 evaluated | 2026 forward.
# ---------------------------------------------------------------------------
# CALENDAR
# One place defines the horizon. Every period label, filename and
# history/forward split is derived from these, so nothing downstream
# names a calendar year.
# ---------------------------------------------------------------------------
START_YEAR = 2023
WEEKS_PER_YEAR = 52
BURN_WEEKS = 2 * WEEKS_PER_YEAR          # warm-up so the forecaster has history
HIST_WEEKS = WEEKS_PER_YEAR              # the evaluated actuals year
FWD_WEEKS = WEEKS_PER_YEAR               # forward planning horizon
ACTUAL_WEEKS = BURN_WEEKS + HIST_WEEKS   # weeks of real demand (orders)
TOTAL_WEEKS = ACTUAL_WEEKS + FWD_WEEKS
FWD_START_WEEK = ACTUAL_WEEKS + 1        # first week of the forward horizon
WEEK1_MONDAY = pd.Timestamp(f"{START_YEAR}-01-02")
YEARS = [START_YEAR + i for i in range(TOTAL_WEEKS // WEEKS_PER_YEAR)]
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
# (CATEGORY_HOLDING_PCT removed -- holding cost now comes from the facility model,
#  step1c_facility_costs, at DC x subcategory grain.)
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
# ---------------------------------------------------------------------------
# TRADE PROMOTION CALENDAR -- relative promo intensity by week-of-year (week 1 starts 2 Jan).
# Earlier builds drew the start week UNIFORMLY at random, which put ~30% of volume on promo in
# EVERY week (week-to-week CV of promo volume share: 0.128). The spikes then cancel when you
# aggregate, so a baseline forecast and a promo-inclusive forecast looked almost identical at
# portfolio level. Real US trade calendars are strongly clustered into known national windows,
# and that clustering is what makes aggregate demand genuinely lumpy.
#   Super Bowl (wk 5-6) - Easter/spring (13-15) - Memorial Day (21-22) - July 4th (26-27)
#   back-to-school (32-34) - Halloween (42-43) - Thanksgiving (47-48) - Christmas (49-52)
# January is the post-holiday trough. Event COUNT is unchanged, only the timing.
# ---------------------------------------------------------------------------
PROMO_CALENDAR_WEIGHT = [
    0.55, 0.60, 0.65, 0.70, 1.80, 1.80, 0.90, 0.90, 0.90, 0.90, 0.95, 1.00, 1.40,
    1.40, 1.30, 0.80, 0.80, 0.80, 0.85, 0.90, 1.85, 1.85, 1.00, 1.00, 1.05, 1.95,
    1.90, 0.90, 0.85, 0.85, 0.90, 1.60, 1.65, 1.55, 0.85, 0.80, 0.80, 0.85, 0.90,
    1.00, 1.10, 1.70, 1.75, 0.90, 0.90, 1.10, 2.00, 2.00, 1.60, 1.70, 1.75, 1.40,
]
# storability drives the post-promo forward-buy dip (pantry-loadable -> deeper dip; perishable -> ~none)
STORABILITY = {"Beverages": 0.9, "Snacks & Confectionery": 0.9, "Dairy & Bakery": 0.2,
               "Frozen Foods": 1.0, "Grocery": 0.9}
SEGMENT_FC_NOISE = {"Fast-Moving": 0.10, "Seasonal": 0.28, "Intermediate": 0.17}

os.makedirs(OUTPUT_DIR, exist_ok=True)


def woy(idx):                       # absolute week -> week-of-year
    return ((idx - 1) % WEEKS_PER_YEAR) + 1
def year_of(idx):
    return START_YEAR + (idx - 1) // WEEKS_PER_YEAR
def horizon_of(idx):
    return "burn_in" if idx <= BURN_WEEKS else ("in_sample" if idx <= ACTUAL_WEEKS else "forward")
def period_label(idx):
    return f"{year_of(idx)}-W{woy(idx):02d}"
def week_start(idx):
    return (WEEK1_MONDAY + pd.Timedelta(weeks=idx - 1)).strftime("%Y-%m-%d")


# ============================================================================
# FACILITY-BASED HOLDING COST  (cost_parameters.csv, grain = DC x SUBCATEGORY)
# ----------------------------------------------------------------------------
# Earlier versions held a 5-row table keyed on CATEGORY with a single `holding_cost_pct_annual`
# applied as unit_cost x rate. That collapsed two economically different things into one number
# and ignored geography: a pallet in Boston and a pallet in Dallas carried the same holding cost.
#
# This model separates them:
#
#   holding cost per unit-year =  STORAGE cost   (facility-driven, $/unit/yr)
#                              +  CARRYING cost  (unit_cost x carrying %)
#
# ASSUMPTION HELD HERE: every facility is the same kind of building -- a plain ambient
# distribution warehouse -- and no product needs special storage (no freezer, chiller, humidity
# control or bonded store). Every DC can hold every subcategory. cost_parameters is therefore the
# FULL DC x subcategory cross product, and cost is the only thing that differentiates a cell.
#
# Two drivers, and only two:
#   1. WHERE the facility is  -- construction cost, cap rate, power price, labour/tax, and the
#                                size of the building (a plant DC builds cheaper per sqft than a
#                                small last-mile shed)
#   2. HOW DENSE the product is -- units per square foot. A bag of flour occupies far more space
#                                per unit than a sachet of yeast, so it costs more to store.
#
# The build-up:
#   all-in capex $/sqft   = base capex x metro construction index x facility size factor
#   capital charge $/sqft = all-in capex x (market cap rate + building depreciation)
#   energy $/sqft/yr      = kWh/sqft/yr x metro electricity rate
#   other opex $/sqft/yr  = base opex x metro opex index  (labour, maintenance, insurance,
#                                                          property tax)
#   storage $/unit/yr     = (capital charge + energy + other opex) / units per sqft
#
# SOURCED cost bases (cited in the generated DATASET_DOCUMENTATION.md):
#   * dry / ambient turnkey warehouse           $85-150 /sqft
#   * by size: 100k+ sqft $70-110, 25-100k $90-150, 5-25k $125-220  -> the size factors below
#   * regional spread: Pacific NW $200-285, Midwest $100-145, Southwest $90-120; location
#     indices move totals +/-20% of national -> the construction indices below
#   * city ranking (Rider Levett Bucknall): Portland dearest, then Honolulu, Seattle;
#     Dallas and Austin cheapest -> anchors the Seattle 1.28 vs Dallas 0.88 spread
#   * metro populations: US Census Bureau Vintage 2025 metro estimates
#
# BENCHMARK ASSUMPTIONS (not fetched; documented so they can be challenged):
#   market cap rates, commercial electricity rates, kWh/sqft/yr, opex indices, units per sqft,
#   shrink/insurance rate, corporate cost of capital.
# ============================================================================

# A single ambient warehouse specification, applied at every facility.
AMBIENT_CAPEX_PER_SQFT = 110.0   # national baseline, midpoint of the sourced $85-150 turnkey band
AMBIENT_KWH_PER_SQFT_YR = 6.0    # lighting, ventilation, dock equipment
AMBIENT_OPEX_PER_SQFT_YR = 4.50  # labour, maintenance, insurance, property tax (national base)
SHRINK_INSURANCE_PCT = 0.012     # damage, loss and stock insurance, as a share of unit cost

# Units per sqft of building (pallet-racked, inclusive of aisles, dock and staging). This is the
# ONLY product-side driver: pure unit cube, nothing to do with temperature or handling class.
UNITS_PER_SQFT = {
    "Yeasts": 420, "Spices": 380, "Coffee in Capsules": 300, "Tea": 300,
    "Canned Sweet": 260, "Candy": 240, "Powdered Milk": 240, "Ground Coffee": 220,
    "Popcorn": 200, "Tomato Sauce": 200, "Energy Drink": 190, "Fresh Dairy": 170,
    "Oil": 160, "Frozen Dessert": 160, "Dry Wine": 150, "Red Wine": 150,
    "Sugar": 150, "Frozen Vegetable": 150, "Liquor": 140, "Imported Wine": 140,
    "Cassava starch": 140, "Wheat Flour": 130, "Flour": 130, "Manioc Flour": 130,
    "Bakery": 120,
}
# ---- metro profile: demographics and the four cost drivers that vary by location ------------
# pop        = MSA resident estimate, millions (Census Vintage 2025, approximate)
# constr_idx = construction cost index vs US national = 1.00
# cap_rate   = stabilised industrial cap rate, % (benchmark assumption)
# elec       = commercial electricity, USD/kWh (benchmark assumption)
# opex_idx   = labour / maintenance / tax / insurance index vs national = 1.00
METRO = {
    "Newark, NJ":      dict(pop=20.1, constr_idx=1.20, cap_rate=5.0, elec=0.163, opex_idx=1.18,
                            role="NY-NJ port and Northeast consumption gateway; scarcest infill land"),
    "Los Angeles, CA": dict(pop=13.3, constr_idx=1.22, cap_rate=5.0, elec=0.235, opex_idx=1.20,
                            role="San Pedro Bay import gateway; highest power cost of any node"),
    "Chicago, IL":     dict(pop=9.5,  constr_idx=1.06, cap_rate=5.8, elec=0.102, opex_idx=1.05,
                            role="Class-I rail interchange; national inland crossdock"),
    "Dallas, TX":      dict(pop=8.4,  constr_idx=0.88, cap_rate=5.6, elec=0.092, opex_idx=0.92,
                            role="Cheapest large-market build in the network; central road reach"),
    "Houston, TX":     dict(pop=7.8,  constr_idx=0.90, cap_rate=6.0, elec=0.092, opex_idx=0.92,
                            role="Gulf port and Texas triangle demand"),
    "Atlanta, GA":     dict(pop=6.4,  constr_idx=0.96, cap_rate=5.7, elec=0.115, opex_idx=0.95,
                            role="Southeast rail/road hub; overnight to a third of the US population"),
    "Philadelphia, PA":dict(pop=6.3,  constr_idx=1.08, cap_rate=5.9, elec=0.118, opex_idx=1.06,
                            role="I-95 corridor; Northeast overflow to Newark"),
    "Miami, FL":       dict(pop=6.2,  constr_idx=1.05, cap_rate=5.5, elec=0.122, opex_idx=1.02,
                            role="LatAm/Caribbean trade gateway; land-constrained, hurricane-exposed"),
    "Phoenix, AZ":     dict(pop=5.2,  constr_idx=0.92, cap_rate=5.8, elec=0.108, opex_idx=0.94,
                            role="Fast-growing Southwest; cheap land, high summer cooling load"),
    "Boston, MA":      dict(pop=5.0,  constr_idx=1.22, cap_rate=5.4, elec=0.225, opex_idx=1.18,
                            role="Dense New England demand; expensive land, power and labour"),
    "Detroit, MI":     dict(pop=4.3,  constr_idx=1.00, cap_rate=6.9, elec=0.128, opex_idx=1.02,
                            role="Widest cap rate in the network; soft industrial investment demand"),
    "Seattle, WA":     dict(pop=4.1,  constr_idx=1.28, cap_rate=5.3, elec=0.108, opex_idx=1.15,
                            role="Dearest build in the network (seismic code, unionised trades); cheap hydro power"),
    "Minneapolis, MN": dict(pop=3.7,  constr_idx=1.03, cap_rate=6.4, elec=0.115, opex_idx=1.03,
                            role="Upper-Midwest reach; long inbound lanes"),
    "Tampa, FL":       dict(pop=3.4,  constr_idx=0.97, cap_rate=5.9, elec=0.122, opex_idx=0.96,
                            role="Florida west-coast distribution; hurricane hardening premium"),
    "Denver, CO":      dict(pop=3.0,  constr_idx=1.04, cap_rate=6.0, elec=0.108, opex_idx=1.02,
                            role="Mountain-West hub; long thin lanes, high inbound freight"),
    "St. Louis, MO":   dict(pop=2.8,  constr_idx=0.94, cap_rate=6.8, elec=0.092, opex_idx=0.95,
                            role="Low-cost Midwest node; barge and rail access"),
    "Charlotte, NC":   dict(pop=2.8,  constr_idx=0.94, cap_rate=5.9, elec=0.090, opex_idx=0.93,
                            role="Cheapest power in the network; Carolinas growth corridor"),
    "Orlando, FL":     dict(pop=2.8,  constr_idx=0.97, cap_rate=6.0, elec=0.120, opex_idx=0.96,
                            role="Central Florida; foodservice and tourism demand skew"),
    "Sacramento, CA":  dict(pop=2.4,  constr_idx=1.15, cap_rate=5.7, elec=0.212, opex_idx=1.12,
                            role="Northern California inland alternative to the LA basin"),
    "Nashville, TN":   dict(pop=2.1,  constr_idx=0.95, cap_rate=6.1, elec=0.112, opex_idx=0.94,
                            role="Fast-growing Southeast secondary; strong road reach"),
}
# facility size factor: large tilt-up DCs build cheaper per sqft than small last-mile sheds.
# Anchored on the sourced size bands (100k+ sqft $70-110; 25-100k $90-150; 5-25k $125-220).
SIZE_FACTOR = {"PDC": 0.88, "RDC": 0.94, "CDC": 1.15}
# plant_dc_master carries no region column, so PDC rows would otherwise land with a null region
# and silently drop out of any region-level rollup. Derived from state instead.
REGION_OF_STATE = {"NJ": "Northeast", "PA": "Northeast", "MA": "Northeast",
                   "GA": "Southeast", "NC": "Southeast", "FL": "Southeast", "TN": "Southeast",
                   "IL": "Midwest", "MI": "Midwest", "MN": "Midwest", "MO": "Midwest",
                   "TX": "Southwest", "AZ": "Southwest",
                   "CA": "West", "WA": "West", "CO": "West"}
BUILDING_DEPRECIATION_PCT = 2.5      # straight line on the building shell, % per year
CORPORATE_COST_OF_CAPITAL_PCT = 9.0  # WACC applied to inventory value

# What cost_parameters.csv actually ships. Holding cost for a SKU at a DC is then
#   storage_cost_per_unit_year_usd  +  unit_cost_usd * inventory_carrying_pct_annual / 100
LEAN_COST_COLS = ["dc_id", "subcategory", "storage_cost_per_unit_year_usd",
                  "inventory_carrying_pct_annual", "stockout_cost_multiplier"]
FACILITY_DETAIL = None               # set by step1c_facility_costs, read by write_documentation


def step1c_facility_costs(OUT, stockout_mult_by_category):
    """Emit cost_parameters.csv at DC x SUBCATEGORY grain (full cross product), and return the
    national-average storage cost per unit-year plus the carrying rate, used to set
    sku_master.holding_cost_per_unit_year_usd."""
    pdc = pd.read_csv(f"{OUT}/plant_dc_master.csv")
    rdc = pd.read_csv(f"{OUT}/regional_dc_master.csv")
    cdc = pd.read_csv(f"{OUT}/customer_dc_master.csv")
    nodes = []
    for df, idc, tier in ((pdc, "plant_dc_id", "PDC"), (rdc, "regional_dc_id", "RDC"),
                          (cdc, "customer_dc_id", "CDC")):
        for r in df.itertuples():
            nodes.append(dict(dc_id=getattr(r, idc), dc_tier=tier, city=r.city, state=r.state,
                              region=getattr(r, "region", None) or REGION_OF_STATE[r.state],
                              capacity=r.storage_capacity_units))
    cat_of_subcat = {sc: cat for cat, scs in CATEGORY_GROUP_MAP.items() for sc in scs}
    carrying_pct = CORPORATE_COST_OF_CAPITAL_PCT / 100.0 + SHRINK_INSURANCE_PCT

    global FACILITY_DETAIL
    rows = []
    for n in nodes:
        m = METRO[f"{n['city']}, {n['state']}"]
        size = SIZE_FACTOR[n["dc_tier"]]
        capex_sqft = AMBIENT_CAPEX_PER_SQFT * m["constr_idx"] * size
        cap_charge = capex_sqft * (m["cap_rate"] + BUILDING_DEPRECIATION_PCT) / 100.0
        energy = AMBIENT_KWH_PER_SQFT_YR * m["elec"]
        other = AMBIENT_OPEX_PER_SQFT_YR * m["opex_idx"]
        facility_sqft_yr = cap_charge + energy + other
        for subcat, upsf in UNITS_PER_SQFT.items():
            rows.append([
                n["dc_id"], n["dc_tier"], n["city"], n["state"], n["region"],
                int(n["capacity"]), subcat, cat_of_subcat[subcat], m["pop"], m["role"],
                AMBIENT_CAPEX_PER_SQFT, m["constr_idx"], size, round(capex_sqft, 2),
                m["cap_rate"], BUILDING_DEPRECIATION_PCT, round(cap_charge, 3),
                AMBIENT_KWH_PER_SQFT_YR, m["elec"], round(energy, 3),
                round(other, 3), round(facility_sqft_yr, 3),
                upsf, round(facility_sqft_yr / upsf, 5),
                CORPORATE_COST_OF_CAPITAL_PCT, round(SHRINK_INSURANCE_PCT * 100, 2),
                round(carrying_pct * 100, 2),
                stockout_mult_by_category[cat_of_subcat[subcat]],
            ])
    cols = ["dc_id", "dc_tier", "city", "state", "region", "storage_capacity_units",
            "subcategory", "category", "metro_population_millions", "metro_market_role",
            "base_capex_per_sqft_usd", "metro_construction_index", "facility_size_factor",
            "all_in_capex_per_sqft_usd", "cap_rate_pct", "building_depreciation_pct",
            "capital_charge_per_sqft_year_usd", "energy_kwh_per_sqft_year",
            "electricity_usd_per_kwh", "energy_cost_per_sqft_year_usd",
            "other_opex_per_sqft_year_usd", "total_facility_cost_per_sqft_year_usd",
            "units_per_sqft", "storage_cost_per_unit_year_usd",
            "cost_of_capital_pct", "shrink_insurance_pct", "inventory_carrying_pct_annual",
            "stockout_cost_multiplier"]
    cpf = pd.DataFrame(rows, columns=cols)
    FACILITY_DETAIL = cpf                      # full build-up, documented in DATASET_DOCUMENTATION.md
    # The SHIPPED table is deliberately lean: the three fields a planner or an optimiser actually
    # consumes, on the DC x subcategory key. Every intermediate (metro demographics, capex per
    # sqft, cap rate, energy, density) is derivation, not data, so it lives in the generated
    # documentation instead of widening the fact table.
    cpf[LEAN_COST_COLS].to_csv(f"{OUT}/cost_parameters.csv", index=False)

    nat = cpf.groupby("subcategory").storage_cost_per_unit_year_usd.mean().to_dict()
    carry = {sc: carrying_pct for sc in UNITS_PER_SQFT}
    print(f"  cost_parameters: {len(cpf):,} rows at DC x SUBCATEGORY grain "
          f"({cpf.dc_id.nunique()} facilities x {cpf.subcategory.nunique()} subcategories, "
          f"full cross product -- every DC can hold every subcategory)")
    print(f"    all-in capex/sqft ${cpf.all_in_capex_per_sqft_usd.min():.0f}-"
          f"${cpf.all_in_capex_per_sqft_usd.max():.0f} | facility cost/sqft/yr "
          f"${cpf.total_facility_cost_per_sqft_year_usd.min():.2f}-"
          f"${cpf.total_facility_cost_per_sqft_year_usd.max():.2f}")
    print(f"    storage $/unit/yr ${cpf.storage_cost_per_unit_year_usd.min():.4f}-"
          f"${cpf.storage_cost_per_unit_year_usd.max():.4f} "
          f"({cpf.storage_cost_per_unit_year_usd.max()/cpf.storage_cost_per_unit_year_usd.min():.1f}x: "
          f"location x product density)")
    return nat, carry


# ============================================================================
# STEP 1: masters (USD economics, Coke-scaled network, customers, lanes)
# ============================================================================
def step1_masters():
    RNG = np.random.default_rng(2025)
    # Shelf life keeps its OWN RNG stream so changing the band perturbs no other draw.
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
            # --- shelf life: long by design, so perishability never constrains anything
            shelf = int(SHELF_RNG.integers(*SHELF_LIFE_DAYS))   # own RNG stream, see note above
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
            # placeholder: overwritten at the end of this step from the facility cost model
            # (step1c_facility_costs), so there is exactly one source of truth for holding cost.
            hold_year_usd = np.nan
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

    # cost_parameters is now built at DC x SUBCATEGORY grain by step1c_facility_costs(), which
    # runs after the node masters exist. Only the per-category stockout multiplier is drawn here.
    stockout_mult = {c: round(float(RNG.uniform(1.5, 2.5)), 2) for c in CATEGORIES}

    # ---- network: EMBEDDED ideal topology (self-contained; no external network files needed) ----
    # 4 Plants (production-only) -> 4 Plant DCs -> 8 Regional DCs -> 15 Customer DCs.
    # Every Customer DC dual-sourced (primary + failover RDC); every RDC connected to ALL 4 Plant DCs
    # (audit fix 1: sourcing is a SKU-level constraint, so a qualified plant must be reachable);
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
    # AUDIT FIX 1: every Plant DC gets a lane to every Regional DC, not just the two nearest.
    # Sourcing is a SKU-level constraint (only some plants can make a given item), so an RDC
    # buying an Atlanta-only SKU must be able to buy it from Atlanta however far away that is.
    # With only the two nearest lanes the network was forced to source from unqualified plants.
    for rid,_,rc in n_rdc:
        for x in sorted(n_pdc,key=lambda x:_hav(rc,x[2])): _add(x[0],"PLANT_DC",x[2],rid,"REGIONAL_DC",rc)
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

    # ---- facility-based holding cost (needs the node masters, so it runs last) ----
    nat_storage, nat_carry = step1c_facility_costs(OUT, stockout_mult)
    # sku_master's holding cost is now DERIVED from the facility model: the national average
    # storage cost for that subcategory plus the carrying charge on its own unit cost. One source
    # of truth -- the DC-specific value lives in cost_parameters.csv.
    _sm = pd.read_csv(f"{OUT}/sku_master.csv")
    _sm["holding_cost_per_unit_year_usd"] = (
        _sm.subcategory.map(nat_storage)
        + _sm.unit_cost_usd * _sm.subcategory.map(nat_carry)).round(4)
    _sm.to_csv(f"{OUT}/sku_master.csv", index=False)
    print(f"    sku_master holding cost re-derived from the facility model: "
          f"${_sm.holding_cost_per_unit_year_usd.min():.3f}-"
          f"${_sm.holding_cost_per_unit_year_usd.max():.3f}/unit/yr")

    print(f"  SKUs 500 (5x100) | RDCs {len(rdc_ids)} CDCs {len(cdc_ids)} | Lanes {n_lanes} | Banners {len(banners)}")
    print(f"  WS-A per-unit prices: ${min(r[8] for r in sku_rows):.2f}-${max(r[8] for r in sku_rows):.2f} "
          f"(v3 ranged $0.33-$157.60 on a case basis)")
    print(f"  WS-D seasonality: {len(SUBCATEGORY_SEASONALITY_MONTHLY)} subcategory curves | "
          f"WS-E sourcing: {n_dual}/{len(sku_meta)} SKUs dual-sourced ({n_dual/len(sku_meta):.0%})")
    print(f"  shelf life: {min(r[13] for r in sku_rows)}-{max(r[13] for r in sku_rows)} days "
          f"(> the {ACTUAL_WEEKS}-week span, so perishability is inert)")
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
    # start weeks are drawn from the trade calendar, not uniformly -- see PROMO_CALENDAR_WEIGHT
    _cal = np.tile(np.array(PROMO_CALENDAR_WEIGHT, dtype=float), TOTAL_WEEKS // 52 + 1)

    def _promo_start(dur):
        n = TOTAL_WEEKS - dur + 1
        w = _cal[:n]
        return int(RNG.choice(n, p=w / w.sum()))

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
            start = _promo_start(dur)
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
    print(f"  horizon: {ACTUAL_WEEKS} actual weeks "
          f"({period_label(1)}..{period_label(ACTUAL_WEEKS)}) + {FWD_WEEKS} forward "
          f"({period_label(FWD_START_WEEK)}..{period_label(TOTAL_WEEKS)}) | "
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
def step3_forecast_and_routing(ctx):
    RNG = np.random.default_rng(909)
    OUT = OUTPUT_DIR
    sku_meta, seasonal_index = ctx["sku_meta"], ctx["seasonal_index"]
    cust_dcs = ctx["cust_dcs"]; plan_depth = ctx["plan_depth"]; sku_ids = list(sku_meta.keys())
    orders_daily = pd.read_csv(f"{OUT}/orders.csv")
    # roll DAILY order lines up to weekly (sku,dc,customer,period) for forecast/production/inventory
    ws_map = {period_label(i): week_start(i) for i in range(1, TOTAL_WEEKS + 1)}
    orders = orders_daily.groupby(["period", "sku_id", "dc_id", "b2b_customer_id"], as_index=False).order_qty.sum()
    orders["week_start_date"] = orders.period.map(ws_map)
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
    # =====================================================================================
    EVAL_START = 1
    plants_df = pd.read_csv(f"{OUT}/plant_master.csv")
    plant_ids = plants_df.plant_id.tolist()
    lanes_df = pd.read_csv(f"{OUT}/network_lanes.csv")
    _pdcm = pd.read_csv(f"{OUT}/plant_dc_master.csv")
    pdc_of_plant = dict(zip(_pdcm.plant_id, _pdcm.plant_dc_id))
    plant_of_pdc = dict(zip(_pdcm.plant_dc_id, _pdcm.plant_id))
    rdc_ids = pd.read_csv(f"{OUT}/regional_dc_master.csv").regional_dc_id.tolist()

    _src = pd.read_csv(f"{OUT}/sku_plant_sourcing.csv")
    sourcing = {s: [(r.plant_id, bool(r.is_primary), float(r.allocation_share))
                    for r in g.itertuples()] for s, g in _src.groupby("sku_id")}
    primary_plant = {s: next((p for p, pr, _ in rows if pr), rows[0][0])
                     for s, rows in sourcing.items()}

    # ---- lane lead times, by hop ----
    LT_PLANT_PDC, LT_PDC_RDC, LT_RDC_CDC = {}, {}, {}
    for r in lanes_df[lanes_df.origin_node_type == "PLANT"].itertuples():
        LT_PLANT_PDC[r.destination_node_id] = (r.lead_time_mean_days, r.lead_time_std_days)
    for r in lanes_df[(lanes_df.origin_node_type == "PLANT_DC")
                      & (lanes_df.destination_node_type == "REGIONAL_DC")].itertuples():
        LT_PDC_RDC[(r.origin_node_id, r.destination_node_id)] = (r.lead_time_mean_days,
                                                                 r.lead_time_std_days)
    for r in lanes_df[lanes_df.destination_node_type == "CUSTOMER_DC"].itertuples():
        k = (r.origin_node_id, r.destination_node_id)
        if k not in LT_RDC_CDC or r.lead_time_mean_days < LT_RDC_CDC[k][0]:
            LT_RDC_CDC[k] = (r.lead_time_mean_days, r.lead_time_std_days)

    # each CDC replenishes from its nearest RDC (all RDCs are full-range regional buffers)
    rdc_of_cdc = {}
    for (rid, cid), (m, _s) in sorted(LT_RDC_CDC.items()):
        if cid not in rdc_of_cdc or m < LT_RDC_CDC[(rdc_of_cdc[cid], cid)][0]:
            rdc_of_cdc[cid] = rid

    # ---- FIX 1: the supplying Plant DC is SKU-specific and must belong to a qualified plant ----
    supplier_of = {}
    for sid, rows in sourcing.items():
        qual = sorted({pdc_of_plant[p] for p, _pr, _sh in rows if p in pdc_of_plant})
        for rid in rdc_ids:
            cand = [q for q in qual if (q, rid) in LT_PDC_RDC]
            supplier_of[(sid, rid)] = (min(cand, key=lambda q: LT_PDC_RDC[(q, rid)][0])
                                       if cand else pdc_of_plant[primary_plant[sid]])

    # ---- exogenous (true customer) demand by stocking node ----
    od = orders.groupby(["dc_id", "sku_id", "period"], as_index=False).order_qty.sum()
    od["wk"] = od.period.map(idx_of)
    od = od[(od.wk >= EVAL_START) & (od.wk <= ACTUAL_WEEKS)]
    ZERO = np.zeros(TOTAL_WEEKS + 2)
    CUST = {}
    for r in od.itertuples():
        k = (r.dc_id, r.sku_id)
        a = CUST.get(k)
        if a is None:
            a = CUST[k] = np.zeros(TOTAL_WEEKS + 2)
        a[int(r.wk)] += r.order_qty

    # ---- the PLANNER's view, used only to set (s,S) parameters -------------------------------
    # A planner sizes a buffer off a demand forecast, not off an order stream it has not seen
    # yet, so policy is still fitted on rolled-up customer demand. What changed is that the
    # simulation ISSUES against the actual replenishment orders its children place, which are
    # lumpier -- exactly the bullwhip penalty the old build could not express. SIGMA_UPLIFT is
    # the allowance a real planner makes for that lumpiness.
    PLAN = {k: a.copy() for k, a in CUST.items()}
    for (node, sid), a in sorted(CUST.items()):                  # CDC demand -> its RDC
        if not node.startswith("CDC"):
            continue
        k = (rdc_of_cdc[node], sid)
        PLAN[k] = PLAN.get(k, np.zeros(TOTAL_WEEKS + 2)) + a
    for (node, sid), a in sorted([(k, v) for k, v in PLAN.items() if k[0].startswith("RDC")]):
        k = (supplier_of[(sid, node)], sid)                      # RDC throughput -> that SKU's PDC
        PLAN[k] = PLAN.get(k, np.zeros(TOTAL_WEEKS + 2)) + a

    # ---- reconcile sku_plant_sourcing to the routing that is actually simulated --------------
    # Step 1 declared allocation_share from service-area geometry. Now that each RDC buys each
    # SKU from the nearest QUALIFIED plant DC (fix 1), the real split is an OUTCOME of those lane
    # assignments, and the declared share drifted from it: mean gap 0.055 overall but 0.114 on
    # dual-sourced SKUs, with `is_primary` flagging the SMALLER producer for about a quarter of
    # them. A master table that disagrees with the fact tables is a trap, so the share and the
    # primary flag are rewritten from realised volume. The drawn secondary-cost penalty is kept
    # by re-assigning the same cost values cheapest-first, which also keeps the primary the
    # cheapest plant and so keeps sku_master.unit_cost_usd consistent with it.
    _pv = {}
    for (node, sid), a in PLAN.items():
        if node in plant_of_pdc:
            _pv[(sid, plant_of_pdc[node])] = float(a[EVAL_START:ACTUAL_WEEKS + 1].sum())
    _rows = []
    for sid, rows in sorted(sourcing.items()):
        vol = {p: _pv.get((sid, p), 0.0) for p, _pr, _sh in rows}
        tot = sum(vol.values())
        costs = sorted(round(float(c), 2) for c in
                       _src.loc[_src.sku_id == sid, "unit_cost_at_plant_usd"])
        order = sorted(vol, key=lambda p: (-vol[p], p))          # largest realised volume first
        shares = ([round(vol[p] / tot, 4) for p in order] if tot > 0
                  else [round(sh, 4) for _p, _pr, sh in rows])
        shares[0] = round(shares[0] + (1.0 - sum(shares)), 4)    # absorb rounding on the primary
        for i, p in enumerate(order):
            _rows.append([sid, p, i == 0, shares[i], costs[min(i, len(costs) - 1)],
                          sku_meta[sid]["family"]])
    _src = pd.DataFrame(_rows, columns=["sku_id", "plant_id", "is_primary", "allocation_share",
                                        "unit_cost_at_plant_usd", "production_family"])
    _src.to_csv(f"{OUT}/sku_plant_sourcing.csv", index=False)
    sourcing = {s: [(r.plant_id, bool(r.is_primary), float(r.allocation_share))
                    for r in g.itertuples()] for s, g in _src.groupby("sku_id")}
    primary_plant = {s: next(p for p, pr, _ in rows if pr) for s, rows in sourcing.items()}

    # ---- FIX 3a: re-size capacity off realised demand so it can actually bind ----------------
    # Capacity was drawn as net_rng.integers(3.5M, 4.8M) units/week against a network peak of
    # 322k, i.e. 50x headroom and never one tight week. Sizing to the p97 of each plant's own
    # assigned volume leaves a normal week comfortable and makes promotional peaks compete for
    # line time, which is what a capacity-aware MEIO study needs.
    PLANT_CAP_HEADROOM, FAM_CAP_HEADROOM = 1.06, 1.10
    _plant_wk = {p: np.zeros(TOTAL_WEEKS + 2) for p in plant_ids}
    _fam_wk = {}
    for (node, sid), a in PLAN.items():
        if node not in plant_of_pdc:
            continue
        p = plant_of_pdc[node]
        _plant_wk[p] += a
        fk = (p, sku_meta[sid]["family"])
        _fam_wk[fk] = _fam_wk.get(fk, np.zeros(TOTAL_WEEKS + 2)) + a
    _win = slice(EVAL_START, ACTUAL_WEEKS + 1)
    cap_plant = {p: max(1000, int(np.ceil(np.percentile(s[_win], 97) * PLANT_CAP_HEADROOM)))
                 for p, s in _plant_wk.items()}
    cap_fam = {k: max(500, int(np.ceil(np.percentile(s[_win], 97) * FAM_CAP_HEADROOM)))
               for k, s in _fam_wk.items()}
    for p, fams in PLANT_CAPABILITY.items():                     # idle-but-capable lines keep a floor
        for fam in fams:
            cap_fam.setdefault((p, fam), 500)
    plants_df["production_capacity_units_per_week"] = plants_df.plant_id.map(cap_plant)
    plants_df.to_csv(f"{OUT}/plant_master.csv", index=False)
    pd.DataFrame([[p, fam, cap_fam[(p, fam)]] for p, fams in sorted(PLANT_CAPABILITY.items())
                  for fam in sorted(fams)],
                 columns=["plant_id", "production_family", "family_capacity_units_per_week"]
                 ).to_csv(f"{OUT}/plant_capability.csv", index=False)


    # ---- publish the routing as an input table -------------------------------
    # supplier_of and rdc_of_cdc above ARE the network's routing decision: each
    # RDC buys each SKU from the nearest plant DC whose plant is qualified to
    # make it, and each CDC replenishes from its nearest RDC. The twin needs
    # exactly this, so it is published rather than left implicit.
    #
    # It used to be recovered by mining the modal origin out of
    # replenishment_receipts.csv, which made the twin depend on a file the twin
    # itself produces: a clean dataset could not be bootstrapped, and every run
    # silently inherited the previous one's routing.
    _map = []
    for (sid, rid), pdc in sorted(supplier_of.items()):
        _map.append([rid, sid, pdc, "nearest qualified plant DC"])
    for cid, rid in sorted(rdc_of_cdc.items()):
        for sid in sku_ids:
            _map.append([cid, sid, rid, "nearest regional DC"])
    for sid in sku_ids:
        for p, _pr, _sh in sourcing[sid]:
            _map.append([pdc_of_plant[p], sid, p, "parent plant"])
    pd.DataFrame(_map, columns=["dc_id", "sku_id", "origin_node_id", "rule"]
                 ).sort_values(["dc_id", "sku_id"]).to_csv(
        f"{OUT}/sku_sourcing_map.csv", index=False)
    print(f"  sourcing map: {len(_map):,} (node, SKU) routing rows")

    # ---- service-level targets by segment (Service-Level Optimization objective) ----
    seg_targets = {"Fast-Moving": (0.98, 0.96), "Seasonal": (0.95, 0.92), "Intermediate": (0.90, 0.88)}
    pd.DataFrame([[s, sl, cfr] for s, (sl, cfr) in seg_targets.items()],
                 columns=["segment", "service_level_target_pct", "case_fill_rate_target_pct"]
                 ).to_csv(f"{OUT}/sku_customer_service_targets.csv", index=False)
    print(f"  service targets: {len(seg_targets)} segment tiers written")

def validate(opening_path=None):
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

    # ---------------- facility-based holding cost (cost_parameters) ----------------
    cpf = pd.read_csv(f"{OUT}/cost_parameters.csv")
    _dcs = set(rdc.regional_dc_id) | set(cdc.customer_dc_id) | set(pdc.plant_dc_id)
    chk(f"cost_parameters is the full DC x SUBCATEGORY cross product "
        f"({cpf.dc_id.nunique()}x{cpf.subcategory.nunique()}={len(cpf)} rows; was 5 rows on category)",
        len(cpf) == cpf.dc_id.nunique() * cpf.subcategory.nunique()
        and cpf.dc_id.nunique() == len(_dcs) and cpf.subcategory.nunique() == sku.subcategory.nunique())
    chk("cost_parameters covers every stocking facility and every subcategory",
        set(cpf.dc_id) == _dcs and set(cpf.subcategory) == set(sku.subcategory))
    chk(f"cost_parameters ships a lean {len(LEAN_COST_COLS)}-column table "
        f"(derivation is documented, not shipped)", list(cpf.columns) == LEAN_COST_COLS)
    chk("cost_parameters: the two holding-cost legs are usable and positive",
        bool(cpf.storage_cost_per_unit_year_usd.gt(0).all()
             and cpf.inventory_carrying_pct_annual.between(5, 25).all()
             and cpf.stockout_cost_multiplier.gt(1).all()))
    # cost must vary on BOTH axes -- location and product density -- and on nothing else
    _geo = cpf.groupby("dc_id").storage_cost_per_unit_year_usd.mean()
    chk(f"cost_parameters: site-to-site spread on the same product > 1.4x "
        f"({_geo.max()/_geo.min():.2f}x, dearest {_geo.idxmax()} vs cheapest {_geo.idxmin()})",
        (_geo.max() / _geo.min()) > 1.4)
    _den = cpf.groupby("subcategory").storage_cost_per_unit_year_usd.mean()
    chk(f"cost_parameters: product-to-product spread at the same site > 2x "
        f"({_den.max()/_den.min():.2f}x, bulkiest {_den.idxmax()} vs densest {_den.idxmin()})",
        (_den.max() / _den.min()) > 2.0)
    chk("cost_parameters: one uniform warehouse type -- no special-storage differentiation",
        int(cpf.inventory_carrying_pct_annual.nunique()) == 1)
    chk("sku_master holding cost derived from the facility model (no NaN, positive)",
        bool(sku.holding_cost_per_unit_year_usd.notna().all()
             and sku.holding_cost_per_unit_year_usd.gt(0).all()))
    # --- derivation invariants, checked against the in-memory build-up rather than the shipped
    #     table, since the intermediates are documented instead of published
    _fd = FACILITY_DETAIL
    if _fd is None:
        print("[skip] facility build-up not in memory "
              "(world stage ran in a separate process)")
    if _fd is not None:
        _capshare = _fd.capital_charge_per_sqft_year_usd / _fd.total_facility_cost_per_sqft_year_usd
        chk(f"facility model: capital charge is 50-90% of annual facility cost "
            f"(mean {_capshare.mean():.0%}) -- holding cost is capex-driven",
            bool(_capshare.between(0.50, 0.90).all()))
        chk("facility model: demographics resolved for every facility row",
            bool(_fd.metro_population_millions.gt(0).all()
                 and _fd.metro_market_role.notna().all() and _fd.region.notna().all()))
        chk("facility model: storage cost reconciles to (facility $/sqft/yr / units per sqft)",
            bool(((_fd.total_facility_cost_per_sqft_year_usd / _fd.units_per_sqft
                   - _fd.storage_cost_per_unit_year_usd).abs() < 1e-4).all()))

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
    # Safety stock tracks lead time only where lead time actually varies.
    # Pooling all three echelons hides that: plant-DC lanes are all exactly
    # 0.5 days (zero variance, correlation undefined) and customer-DC lanes
    # span 0.5-3.2 days against a one-week review, so the protection interval
    # there barely moves (1.07-1.46 weeks) and no lead-time signal is visible
    # or expected. Regional-DC lanes span 0.5-12.9 days, which is where the
    # policy has something to respond to -- so that is the echelon asserted,
    # with the others reported for transparency.
    _iv["t"] = _iv.dc_id.str[:3]
    _by_ech = {}
    for _t, _g in _iv.groupby("t"):
        _spread = float(_g["lt"].max() - _g["lt"].min())
        _by_ech[_t] = (float(_g[["ss_per_demand", "lt"]].corr().iloc[0, 1])
                       if _spread > 1.0 else float("nan"), _spread)
    _wide = max(_by_ech, key=lambda k: _by_ech[k][1])
    _c = _by_ech[_wide][0]
    chk(f"WS-H safety stock per unit demand tracks lead time where the lane "
        f"spread is material: {_wide} corr {_c:.3f} over a "
        f"{_by_ech[_wide][1]:.1f}d spread "
        f"({', '.join(f'{k} {v[0]:.2f}/{v[1]:.1f}d' for k, v in sorted(_by_ech.items()))})",
        _c > 0.20)
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

    # ---------------- AUDIT FIXES 1-3: lock the three structural fixes in ------------------
    _pdcm = pd.read_csv(f"{OUT}/plant_dc_master.csv")
    _srcq = pd.read_csv(f"{OUT}/sku_plant_sourcing.csv")
    _plant_of = dict(zip(_pdcm.plant_dc_id, _pdcm.plant_id))
    _qual = set(zip(_srcq.sku_id, _srcq.plant_id))
    _pv = inv[inv.dc_id.str.startswith("PDC")].groupby(["sku_id", "dc_id"], as_index=False).receipts.sum()
    _unq = [1 for s, d, r in zip(_pv.sku_id, _pv.dc_id, _pv.receipts)
            if r > 0 and (s, _plant_of[d]) not in _qual]
    chk(f"FIX 1 every Plant DC only ever stocks SKUs its own plant can make "
        f"({len(_unq)} unqualified pairs; was 1,308 of 1,960 = 66.7%)", len(_unq) == 0)
    _npdc = inv[inv.dc_id.str.startswith("PDC")].groupby("sku_id").dc_id.nunique().mean()
    _nqual = _srcq.groupby("sku_id").plant_id.nunique().mean()
    chk(f"FIX 1 PDCs per SKU tracks qualified plants per SKU ({_npdc:.2f} vs {_nqual:.2f}; "
        f"was 3.92 vs 1.32)", abs(_npdc - _nqual) < 0.05)

    _iwk = {p: i + 1 for i, p in enumerate(sorted(inv.period.unique()))}
    _rc2 = rcpt.copy(); _rc2["wk"] = _rc2.period.map(_iwk)       # period == DISPATCH week
    _inv2 = inv.copy(); _inv2["wk"] = _inv2.period.map(_iwk)
    _disp = _rc2.groupby(["origin_node_id", "sku_id", "wk"], as_index=False).qty_received.sum()
    _par = _disp.merge(_inv2[["dc_id", "sku_id", "wk", "opening_stock", "receipts", "shipments"]],
                       left_on=["origin_node_id", "sku_id", "wk"],
                       right_on=["dc_id", "sku_id", "wk"], how="inner")
    chk(f"FIX 2 no parent ever dispatches more than it had on hand "
        f"(0 of {len(_par):,} parent-SKU-weeks)",
        bool((_par.qty_received <= _par.opening_stock + _par.receipts + 0.5).all()))
    chk("FIX 2 no parent ever dispatches more than its own recorded shipments",
        bool((_par.qty_received <= _par.shipments + 0.5).all()))
    _sent = float((inv.demand_replen_qty - inv.unmet_replen_qty).sum())
    _got = float(_rc2[_rc2.origin_node_id.str.startswith(("PDC", "RDC"))].qty_received.sum())
    chk(f"FIX 2 internal shipments reconcile to internal receipts exactly "
        f"({_got:,.0f} == {_sent:,.0f}; was +19.9% at the CDC hop)", abs(_got - _sent) < 1.0)
    chk(f"FIX 2 short PO lines exist -- upstream shortfall is real "
        f"({(rcpt.short_qty > 0).mean():.1%} of lines; was 0%)",
        0.02 <= float((rcpt.short_qty > 0).mean()) <= 0.60)
    chk(f"FIX 2 customer demand reconciles EXACTLY to orders "
        f"({inv.demand_customer_qty.sum():,} units) -- no echelon double counting",
        int(inv.demand_customer_qty.sum()) == int(orders.order_qty.sum()))
    _tw = (_inv2.assign(t=_inv2.dc_id.str[:3])
           .groupby(["t", "wk"], as_index=False).demand_qty.sum())
    _cvt = {t: float(g.demand_qty.std() / g.demand_qty.mean()) for t, g in _tw.groupby("t")}
    chk(f"FIX 2 bullwhip: variability AMPLIFIES upstream, CDC {_cvt['CDC']:.3f} -> "
        f"RDC {_cvt['RDC']:.3f} -> PDC {_cvt['PDC']:.3f} (was flat/damping 0.192/0.198/0.198)",
        _cvt["PDC"] > _cvt["CDC"] * 1.05 and _cvt["RDC"] >= _cvt["CDC"])

    _prd2 = pd.read_csv(f"{OUT}/production.csv")
    _plm2 = pd.read_csv(f"{OUT}/plant_master.csv")
    _pu2 = (_prd2.groupby(["plant_id", "period"]).actual_qty.sum().reset_index()
            .merge(_plm2[["plant_id", "production_capacity_units_per_week"]], on="plant_id"))
    _u2 = _pu2.actual_qty / _pu2.production_capacity_units_per_week
    chk(f"FIX 3 plant utilisation is realistic, mean {_u2.mean():.1%} peak {_u2.max():.1%} "
        f"(was 1.3% mean / 3.4% peak, i.e. 50x headroom)",
        0.35 <= float(_u2.mean()) <= 0.90 and float(_u2.max()) <= 1.0)
    chk(f"FIX 3 capacity actually binds on the plan "
        f"({_prd2.capacity_limited_flag.mean():.1%} of SKU-weeks clipped; was 0%)",
        0.01 <= float(_prd2.capacity_limited_flag.mean()) <= 0.40)
    _cum = (_inv2[_inv2.dc_id.str.startswith("PDC")].groupby(["sku_id", "wk"], as_index=False).receipts.sum()
            .merge(_prd2.assign(wk=_prd2.period.map(_iwk)).groupby(["sku_id", "wk"], as_index=False).actual_qty.sum(),
                   on=["sku_id", "wk"], how="outer").fillna(0).sort_values(["sku_id", "wk"]))
    _cum["cp"] = _cum.groupby("sku_id").actual_qty.cumsum()
    _cum["cr"] = _cum.groupby("sku_id").receipts.cumsum()
    chk(f"FIX 3 Plant-DC inflow is never more than the plant has ever built "
        f"(0 of {len(_cum):,} SKU-weeks; was 225)", bool((_cum.cr <= _cum.cp + 0.5).all()))
    # the sourcing master must agree with the volume the plants actually build
    _as = _srcq.merge(_prd2.groupby(["sku_id", "plant_id"], as_index=False).actual_qty.sum(),
                      on=["sku_id", "plant_id"], how="left").fillna({"actual_qty": 0})
    _as["realised"] = _as.actual_qty / _as.groupby("sku_id").actual_qty.transform("sum")
    _gap = (_as.allocation_share - _as.realised).abs()
    # allocation_share is the planner's declared intent. `realised` is what the
    # twin actually built, after family-line and plant capacity clipping and
    # schedule adherence. A small gap is the point -- it is the difference
    # between plan and execution. Rewriting the master to match the outcome
    # would be circular, since the twin reads that master to split production.
    chk(f"sourcing allocation_share tracks realised production (mean gap "
        f"{_gap.mean():.3f}, max {_gap.max():.3f}; was 0.055 / 0.369)",
        float(_gap.mean()) < 0.02 and float(_gap.max()) < 0.15)
    _big = _as.sort_values("realised").groupby("sku_id").last()
    chk(f"is_primary flags the largest realised producer for >=95% of SKUs "
        f"({_big.is_primary.mean():.1%}; was 76% among dual-sourced)",
        float(_big.is_primary.mean()) >= 0.95)
    _oi = pd.read_csv(opening_path)
    chk(f"the forward opening state carries the in-transit pipeline "
        f"({_oi.in_transit_qty.sum():,} units; previously destroyed at the cut-over)",
        {"in_transit_qty", "inventory_position"}.issubset(_oi.columns) and _oi.in_transit_qty.sum() > 0)
    chk("WS-L both fulfilment types occur (DSD loses the sale, BULK backorders)",
        inv.fulfilment_type.nunique() == 2)

    # WS-K: FEFO / write-offs -- profile-dependent by design
    _dos = inv[inv.shipments > 0].merge(sku[["sku_id", "category", "shelf_life_days"]], on="sku_id")
    _viol = float((_dos.closing_stock / (_dos.shipments / 7.0) > _dos.shelf_life_days).mean())
    chk(f"WS-K SKU-weeks holding more stock-days than shelf life < 5% "
        f"(got {_viol:.1%}; v3: 66.6% for Dairy & Bakery)", _viol < 0.05)
    # Shelf life exceeds the simulated span, so the FEFO expiry path must never fire.
    _wr = float(wo.units_written_off.sum()) / max(float(inv.demand_qty.sum()), 1.0)
    chk(f"WS-K shelf life cannot bind, so write-offs are nil "
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
    # --- the two published forecasts must be genuinely different series, not just at row level
    #     but on the aggregate a planner would actually look at. Uniformly-random promo timing
    #     made them near-identical after aggregation (CV 0.135 vs 0.152); a clustered trade
    #     calendar is what separates them.
    _wp = orders.groupby("period").apply(
        lambda g: g.loc[g.on_promo, "order_qty"].sum() / max(g.order_qty.sum(), 1),
        include_groups=False)
    _wpcv = _wp.std() / _wp.mean()
    chk(f"promo calendar is clustered, not flat: week-to-week CV of promo volume share > 0.25 "
        f"(got {_wpcv:.3f}; uniform-random timing gave 0.128)", _wpcv > 0.25)
    # The two published forecasts are tested against the RIGHT reference: the promo-inclusive one
    # should track the actual series' lumpiness, and the baseline should be visibly smoother than
    # it. A raw CV ratio between the two forecasts is the wrong test -- promotional lift is only
    # ~15% of volume (30% of volume at ~2x lift), so no realistic promo effect moves aggregate CV
    # by a large multiple.
    _ni = fc[fc.horizon == "in_sample"].groupby("period")
    _cb = _ni.forecast_baseline_qty.sum().std() / _ni.forecast_baseline_qty.sum().mean()
    _ct = _ni.forecast_qty.sum().std() / _ni.forecast_qty.sum().mean()
    _ca = _ni.actual_qty.sum().std() / _ni.actual_qty.sum().mean()
    chk(f"promo-inclusive forecast tracks actual lumpiness (CV {_ct:.3f} vs actual {_ca:.3f})",
        abs(_ct / _ca - 1.0) < 0.20)
    chk(f"baseline forecast is visibly smoother than actual (CV {_cb:.3f} vs {_ca:.3f}, "
        f"ratio {_cb/_ca:.2f}) -- it is a de-promoted series", (_cb / _ca) < 0.92)
    _mp = orders.groupby(["period", "sku_id", "dc_id", "b2b_customer_id"],
                         as_index=False).on_promo.max()
    _mm = fc[fc.horizon == "in_sample"].merge(
        _mp, on=["period", "sku_id", "dc_id", "b2b_customer_id"], how="left")
    _mm["on_promo"] = _mm.on_promo.fillna(False).astype(bool)
    _mm = _mm[_mm.actual_qty > 0]
    _pw = _mm[_mm.on_promo]
    _wb = (_pw.forecast_baseline_qty - _pw.actual_qty).abs().sum() / _pw.actual_qty.sum()
    _wt = (_pw.forecast_qty - _pw.actual_qty).abs().sum() / _pw.actual_qty.sum()
    chk(f"applying promotions materially improves promo-week accuracy "
        f"(WMAPE {_wb:.3f} baseline-only -> {_wt:.3f} with promotions, {1-_wt/_wb:.0%} better)",
        (1 - _wt / _wb) > 0.20)
    chk("WS-J forward rows carry a promo-lift component (v3 had no forward promo calendar)",
        float((_fwd.forecast_promo_lift_qty > 0).mean()) > 0.02)
    chk(f"WS-J actuals span {ACTUAL_WEEKS} weeks incl. a {BURN_WEEKS}-week burn-in "
        f"(v3: 52 weeks, so no seasonal model was possible)",
        orders.period.nunique() == ACTUAL_WEEKS)
    chk("forward opening position exists for every stocking SKU-DC",
        opening_path is not None and os.path.exists(opening_path)
        and len(pd.read_csv(opening_path)) ==
            inv.groupby(["sku_id", "dc_id"]).ngroups)
    chk("Phase 4: forward price plan emitted for the execution horizon",
        os.path.exists(f"{OUT}/price_plan.csv") and len(pd.read_csv(f"{OUT}/price_plan.csv")) > 1000)

    chk(f"shelf life within {SHELF_LIFE_DAYS} days for every SKU -- beyond the "
        f"{ACTUAL_WEEKS}-week span, so nothing can expire",
        bool(sku.shelf_life_days.between(*SHELF_LIFE_DAYS).all()))

    print(f"\n{len(errs)} check(s) failed" if errs else "\nAll checks passed.")
    return len(errs) == 0


# ============================================================================
# STEP 5: write DATASET_DOCUMENTATION.md (data dictionary + FK map + elasticity METHOD)
# ============================================================================
# Part number the data profile (EDA) takes inside DATASET_DOCUMENTATION.md. The EDA's own
# "2.x" subsections are renumbered to "<part>.x" when it is folded in.
_DOC_PROFILE_PART = 7


def _facility_cost_doc_section():
    """Build the holding-cost appendix for DATASET_DOCUMENTATION.md.

    cost_parameters.csv ships only the four consumable fields; everything explaining HOW those
    numbers were derived -- metro demographics, capex per sqft, cap rates, energy, storage density
    -- is written here instead, so the fact table stays narrow and the reasoning stays auditable.
    """
    d = FACILITY_DETAIL
    if d is None:
        return ""
    L = []
    A = L.append
    A("\n### 6.3 Holding cost — how `cost_parameters.csv` is derived\n")
    A(f"`cost_parameters.csv` has **{len(d):,} rows** on a **dc_id x subcategory** key "
      f"({d.dc_id.nunique()} storage facilities x {d.subcategory.nunique()} subcategories) and "
      f"ships {len(LEAN_COST_COLS)} columns:\n")
    A("| Column | Meaning |")
    A("|---|---|")
    A("| `dc_id` | Storage facility (plant DC, regional DC or customer DC) |")
    A("| `subcategory` | Product subcategory; joins to `sku_master.subcategory` |")
    A("| `storage_cost_per_unit_year_usd` | Cost of occupying space, per unit per year |")
    A("| `inventory_carrying_pct_annual` | Cost of capital + shrink/insurance, % of unit cost |")
    A("| `stockout_cost_multiplier` | Category-level penalty on lost margin |")
    A("\nHolding cost for a SKU at a DC is the sum of the two legs:\n")
    A("```")
    A("holding_cost_per_unit_year = storage_cost_per_unit_year_usd")
    A("                           + unit_cost_usd * inventory_carrying_pct_annual / 100")
    A("```")
    A("\nThe first leg scales with **cube and storage class**, the second with **value**. A single")
    A("percentage-of-cost rate (which is what earlier versions of this table held) models only the")
    A("second and implicitly assumes a pallet of frozen vegetables and a pallet of flour cost the")
    A("same to keep. `sku_master.holding_cost_per_unit_year_usd` is the national average of this")
    A("calculation and exists for convenience; prefer the DC-specific value for anything")
    A("location-aware.\n")

    A("#### The model\n")
    A("```")
    A("all-in capex $/sqft   = class base capex x metro construction index x facility size factor")
    A("capital charge $/sqft = all-in capex x (market cap rate + building depreciation)")
    A("energy $/sqft/yr      = class kWh/sqft/yr x metro electricity rate")
    A("other opex $/sqft/yr  = class base opex x metro opex index")
    A("                        (labour, maintenance, insurance, property tax, compliance)")
    A("storage $/unit/yr     = (capital charge + energy + other opex)")
    A("                        / (units per sqft x class density factor)")
    A("carrying % per year   = corporate cost of capital + class shrink/insurance")
    A("```")
    _cs = (d.capital_charge_per_sqft_year_usd / d.total_facility_cost_per_sqft_year_usd)
    _en = (d.energy_cost_per_sqft_year_usd / d.total_facility_cost_per_sqft_year_usd)
    A(f"\nThe **capital charge is {_cs.mean():.0%} of annual facility cost** on average "
      f"(range {_cs.min():.0%}-{_cs.max():.0%}), so holding cost is dominated by the amortised")
    A(f"cost of having built the building rather than by running it -- energy is only "
      f"{_en.mean():.1%} of the annual total.\n")

    A("#### Assumption: one warehouse type, no special storage\n")
    A("Every facility is modelled as the same plain ambient distribution warehouse. No product is")
    A("assumed to need a freezer, chiller, humidity control or a bonded store, and **every DC can")
    A("hold every subcategory** -- which is why the table is the full cross product rather than a")
    A("capability-filtered subset.\n")
    A(f"The single warehouse specification, applied everywhere: "
      f"**${AMBIENT_CAPEX_PER_SQFT:.0f}/sqft** base capex, "
      f"**{AMBIENT_KWH_PER_SQFT_YR:.0f} kWh/sqft/yr** energy, "
      f"**${AMBIENT_OPEX_PER_SQFT_YR:.2f}/sqft/yr** other opex, and a flat "
      f"**{d.inventory_carrying_pct_annual.iloc[0]:.1f}%** carrying rate "
      f"({CORPORATE_COST_OF_CAPITAL_PCT:.1f}% cost of capital + "
      f"{SHRINK_INSURANCE_PCT*100:.1f}% shrink/insurance).\n")
    A("Cost therefore varies on exactly two axes:\n")
    A("1. **Where the facility is** -- construction cost index, cap rate, power price, labour and")
    A("   tax index, and building size (a plant DC builds cheaper per sqft than a small last-mile shed).")
    A("2. **How dense the product is** -- units per square foot, driven purely by unit cube. A bag")
    A("   of flour occupies far more space per unit than a sachet of yeast.\n")
    A("Units per sqft by subcategory (pallet-racked, incl. aisles / dock / staging):\n")
    A("| Units/sqft | Subcategories |")
    A("|---|---|")
    _byd = {}
    for _sc, _v in UNITS_PER_SQFT.items():
        _byd.setdefault(_v, []).append(_sc)
    for _v in sorted(_byd, reverse=True):
        A(f"| {_v} | {', '.join(sorted(_byd[_v]))} |")
    A("")

    A("\n#### Facility demographics and location cost drivers\n")
    A(f"Facilities sit in **{d.groupby(['city','state']).ngroups} metros across "
      f"{d.region.nunique()} regions**. Population is the MSA resident estimate in millions.\n")
    A("| Metro | Region | Facilities | Pop (M) | Constr. index | Cap rate | Elec $/kWh | Market role |")
    A("|---|---|---|---|---|---|---|---|")
    g = (d.groupby(["city", "state", "region"])
          .agg(dcs=("dc_id", "nunique"), pop=("metro_population_millions", "first"),
               ci=("metro_construction_index", "first"), cr=("cap_rate_pct", "first"),
               el=("electricity_usd_per_kwh", "first"),
               role=("metro_market_role", "first")).reset_index()
          .sort_values("pop", ascending=False, kind="stable"))   # stable: ties keep metro order
    for r in g.itertuples():
        A(f"| {r.city}, {r.state} | {r.region} | {r.dcs} | {r.pop} | {r.ci:.2f} | "
          f"{r.cr:.1f}% | {r.el:.3f} | {r.role} |")
    A(f"\nFacility scale matters as much as metro: larger sites build cheaper per square foot.\n")
    A("| Tier | Facilities | Size factor | Sourced size band |")
    A("|---|---|---|---|")
    for t, band in (("PDC", "100k+ sqft, tilt-up: $70-110/sqft"),
                    ("RDC", "25k-100k sqft: $90-150/sqft"),
                    ("CDC", "5k-25k sqft, no economies of scale: $125-220/sqft")):
        A(f"| {t} | {d[d.dc_tier==t].dc_id.nunique()} | {SIZE_FACTOR[t]:.2f} | {band} |")

    A("\n#### Resulting spread\n")
    A("| Measure | Range |")
    A("|---|---|")
    A(f"| All-in capex per sqft | ${d.all_in_capex_per_sqft_usd.min():.0f} - "
      f"${d.all_in_capex_per_sqft_usd.max():.0f} "
      f"({d.all_in_capex_per_sqft_usd.max()/d.all_in_capex_per_sqft_usd.min():.1f}x) |")
    A(f"| Annual facility cost per sqft | ${d.total_facility_cost_per_sqft_year_usd.min():.2f} - "
      f"${d.total_facility_cost_per_sqft_year_usd.max():.2f} |")
    A(f"| Storage cost per unit-year | ${d.storage_cost_per_unit_year_usd.min():.4f} - "
      f"${d.storage_cost_per_unit_year_usd.max():.4f} "
      f"({d.storage_cost_per_unit_year_usd.max()/d.storage_cost_per_unit_year_usd.min():.0f}x) |")
    _mx = d.loc[d.storage_cost_per_unit_year_usd.idxmax()]
    _mn = d.loc[d.storage_cost_per_unit_year_usd.idxmin()]
    A(f"| Dearest cell | {_mx.subcategory} @ {_mx.dc_id} ({_mx.city}) "
      f"-- ${_mx.storage_cost_per_unit_year_usd:.4f}/unit/yr |")
    A(f"| Cheapest cell | {_mn.subcategory} @ {_mn.dc_id} ({_mn.city}) "
      f"-- ${_mn.storage_cost_per_unit_year_usd:.4f}/unit/yr |")
    _g = d.groupby("dc_id").storage_cost_per_unit_year_usd.mean()
    _pp = d.groupby("subcategory").storage_cost_per_unit_year_usd.mean()
    A(f"| Location axis (same product, site to site) | {_g.max()/_g.min():.2f}x "
      f"-- dearest {_g.idxmax()}, cheapest {_g.idxmin()} |")
    A(f"| Density axis (same site, product to product) | {_pp.max()/_pp.min():.2f}x "
      f"-- bulkiest {_pp.idxmax()}, densest {_pp.idxmin()} |")
    A("\nAnnual facility cost per sqft by site -- the location axis in full:\n")
    A("| DC | Tier | Metro | Capex $/sqft | Capital charge | Energy | Other opex | Total $/sqft/yr |")
    A("|---|---|---|---|---|---|---|---|")
    k = (d.groupby(["dc_id", "dc_tier", "city"]).agg(
        cx=("all_in_capex_per_sqft_usd", "first"), cc=("capital_charge_per_sqft_year_usd", "first"),
        en=("energy_cost_per_sqft_year_usd", "first"), oo=("other_opex_per_sqft_year_usd", "first"),
        tt=("total_facility_cost_per_sqft_year_usd", "first"))
        .reset_index().sort_values("tt", ascending=False))
    for r in k.itertuples():
        A(f"| {r.dc_id} | {r.dc_tier} | {r.city} | {r.cx:.0f} | {r.cc:.2f} | {r.en:.2f} | "
          f"{r.oo:.2f} | **{r.tt:.2f}** |")

    A("\n#### Cost basis: what is sourced and what is assumed\n")
    A("**Sourced** (published 2025/2026 construction-cost guides and Census estimates):\n")
    A("| Input | Value | Source |")
    A("|---|---|---|")
    A("| Dry / ambient turnkey warehouse | $85-150 /sqft | Brown Integrated Logistics, 2026 |")
    A("| Size bands | 100k+ $70-110 - 25-100k $90-150 - 5-25k $125-220 /sqft | Brown Integrated Logistics, 2026 |")
    A("| Regional spread | Pacific NW $200-285 - Midwest $100-145 - Southwest $90-120; location indices +/-20% of national | Red Stag Fulfillment |")
    A("| City ranking | Portland dearest, then Honolulu, Seattle; Dallas and Austin cheapest | Rider Levett Bucknall Q1 2026 (via Statista) |")
    A("| Site work share | 20-25% of construction budget | Brown Integrated Logistics, 2026 |")
    A("| Metro populations | MSA resident estimates | US Census Bureau, Vintage 2025 |")
    A("\nThe Seattle / Dallas construction-index spread is anchored on the Rider Levett Bucknall")
    A("city ranking plus the published regional bands. RLB's per-city dollar figures sit behind a")
    A("paywall, so only the ranking was available.\n")
    A("**Benchmark assumptions** -- not fetched, documented so they can be challenged:\n")
    A("| Input | Basis |")
    A("|---|---|")
    A("| Market cap rates (5.0-6.9%) | Stabilised industrial cap rates; tighter prime coastal, wider secondary Midwest |")
    A("| Commercial electricity $/kWh | State-level commercial averages |")
    A("| Energy intensity (6 kWh/sqft/yr) | Ambient warehouse lighting, ventilation, dock equipment |")
    A("| Other opex ($4.50/sqft/yr national base) | Labour, maintenance, insurance, property tax |")
    A("| Metro opex index (0.92-1.20) | Relative metro labour and tax cost |")
    A("| Units per sqft (120-420) | Unit cube, pallet-racked, incl. aisles / dock / staging -- the only product-side driver |")
    A(f"| Building depreciation | {BUILDING_DEPRECIATION_PCT}% per year, straight line on shell |")
    A(f"| Corporate cost of capital | {CORPORATE_COST_OF_CAPITAL_PCT}% WACC on inventory value |")
    A("| Shrink + insurance (1.2%) | Damage, loss and stock insurance as a share of unit cost |")
    A("\n**Two cautions on this dataset.** Shelf life is deliberately 3-4 years, so obsolescence")
    A("cost is zero everywhere and holding cost is the *only* overstock cost in a newsvendor")
    A("calculation -- which makes this model more influential here than on a perishable catalogue.")
    A("And because no product is assumed to need special storage, the spread understates a real")
    A("multi-temperature network, where refrigerated space costs roughly 2-3x ambient to build.\n")
    return "\n".join(L) + "\n"


# Bounds applied to the generated promotional elasticity (step1_masters:
# np.clip(elast * PROMO_MULT, -4.5, 3.0)). Documentation only -- keep in step.
_PROMO_ELAST_CLIP = (-4.5, 3.0)


def _price_elasticity_doc_section(st):
    """Section 6.1. `st` holds the figures computed by _doc_stats() and the
    category-level elasticity recovery from generate_eda()."""
    P = _DOC_PROFILE_PART
    er = st.get("elasticity") or []
    if er:
        _rat = [e[3] for e in er if e[3] == e[3]]
        _n_gt = sum(1 for e in er if e[2] <= e[1])
        recovered = (f" Recovered from the data (§{P}.8), promo/base runs "
                     f"{min(_rat):.2f}-{max(_rat):.2f}x by category, larger than\n"
                     f"    base in {_n_gt} of {len(er)}.")
    else:
        recovered = ""
    lo, hi = _PROMO_ELAST_CLIP
    return f'''
### 6.1 Price elasticity — how to estimate it from `orders`

The data carries TWO distinct price responses, and no elasticity column is shipped -- you estimate both
from `orders`, exactly as you would on a real client extract:
* **base (regular) elasticity** -- response of demand to the everyday **shelf** price
    (`base_price_usd`, which drifts with ~4%/yr inflation plus a small random walk and steps at
    3-6 revisions/yr).
* **promotional (deal) elasticity** -- response to a temporary **discount** (`discount_pct`,
    ~{st["promo_vol"]*100:.0f}% of VOLUME; v3's documentation claimed ~30% of SKU-weeks but the shipped data reached
    only 11.6% of volume). It is generated as {PROMO_MULT}x the base elasticity (CPG research puts promo
    ~1.5-2x regular), clipped to [{lo}, +{hi}]. The clip inverts the ordering for very elastic
    groups (base below about {lo / PROMO_MULT:.1f}), where the promotional response ends up *weaker* than the
    base.{recovered} Promotions are followed by a **forward-buy dip** in the next
    1-2 weeks for storable categories (pantry-loading payback), which is the signature that
    separates a deal response from a base response.
* Estimate at **`subcategory`** level ({st["n_subcat"]} groups), not `category` ({st["n_cat"]}). The subcategory IS
    the real product group each SKU's elasticity was drawn from; pooling to category mixes
    genuinely different responses together. (The snippet below and §{P}.8 pool to category
    for stable numbers, and inherit that mixing.)
* `off_invoice_pct` and the other trade-term columns give **cross-sectional** price variation
    on top of the time-series variation in `base_price_usd`; both are needed to separate base
    from promotional response.
'''


def _forecast_doc_section(st):
    """Section 6.2 plus the elasticity-estimation method. Figures come from
    _doc_stats(); spans are derived from the calendar constants."""
    P = _DOC_PROFILE_PART
    span = st["span"].replace("–", "-")
    burn = st["burn_span"].replace("–", "-")
    ev = st["last_yr"]
    er = st.get("elasticity") or []
    _n_gt = sum(1 for e in er if e[2] <= e[1])
    holds = (f", and in this instance it holds in {_n_gt} of {len(er)} categories (§{P}.8)"
             if er else "")
    red = 1 - st["wmape_promo_incl"] / st["wmape_promo_base"] if st["wmape_promo_base"] else 0.0
    _rw = _frac_words(red, tol=0.03)
    red_words = f"roughly {_rw}" if not _rw.endswith("%") else f"about {_rw}"
    return f'''
### 6.2 The two forecasts — how `demand_forecast` is built

v3 set `forecast_qty = actual_qty * (1 + noise)`, which made the forecast a relabelled copy of
the actual: `corr(forecast, actual) = 0.981`, and 97.6% of the WMAPE variance was explained by
the `segment` column alone. It also meant promo weeks were **no harder** than normal weeks,
directly contradicting v3's own documentation.

v4 forecasts in three layers. The baseline is ex ante; the lift and the customer split are not
strictly so:
* **Layer 0 -- de-batch.** Forecast at SKU x DC x week, then disaggregate to customer by each
  customer's share of the full {span} history (which includes the evaluated year). At customer
  grain about {st["zero_cells"]*100:.0f}% of in-sample weekly cells have no order at all, because slower items are
  ordered every two or three weeks. Much of the apparent volatility there is the order
  calendar, not demand.
* **Layer 1 -- baseline.** De-promote the history (promo weeks masked and interpolated), then
  seasonal-naive from t-52 rescaled by the trailing 13-week level ratio. Apart from that
  interpolation, only weeks before t are used. The forward year applies one year-over-year
  ratio, fixed at the cut-over, to the de-promoted {ev} shape. This is why {burn} exist: a
  seasonal model needs at least two prior cycles.
* **Layer 2 -- lift.** Uplift per series is the median observed uplift over the promo weeks of
  the whole {ACTUAL_WEEKS}-week history, including the {ev} weeks it is then scored on. It is applied to
  the PLANNED promo calendar with ~35% planner misestimation. Applying the true lift would
  rebuild the leak.
#### The two published series

`demand_forecast` publishes **two usable forecasts** on the same rows, plus the bridge between
them:

| Column | What it is | Use it for |
|---|---|---|
| `forecast_baseline_qty` | **Forecast 1 -- baseline.** Trend + seasonality on the de-promoted
  history. Contains no promotional uplift at all. | Base demand planning, capacity, trend and
  seasonality diagnostics, and as the denominator when sizing promotional lift |
| `forecast_promo_lift_qty` | The incremental uplift attributed to the **planned** promo
  calendar | Trade-spend planning; isolating promo-planning quality from baseline quality |
| `forecast_qty` | **Forecast 2 -- promo-inclusive.** `baseline + lift`. This is the number to
  replenish against | Safety stock, MEIO, service-level and fill-rate work |

They are genuinely different series, not a cosmetic split. On the national weekly total for the
evaluated year:

| Series | Weekly CV |
|---|---|
| `forecast_baseline_qty` | ~{st["cv_baseline"]:.2f} -- deliberately smooth |
| `forecast_qty` | ~{st["cv_forecast"]:.2f} |
| **actual demand** | **~{st["cv_actual"]:.2f}** |

The promo-inclusive forecast tracks the lumpiness of actual demand; the baseline does not,
because promotional weeks have been stripped out of it. On promotional weeks specifically
(rows with a planned promotion, `forecast_promo_lift_qty > 0`), applying the promo calendar cuts
WMAPE by {red_words} (about {st["wmape_promo_base"]:.2f} baseline-only versus {st["wmape_promo_incl"]:.2f} with promotions).

**If the two look nearly identical in your own test, check what you aggregated.** Promotional
lift is only about {st["lift_share"]*100:.0f}% of forecast volume in the evaluated year (roughly {st["promo_vol"]*100:.0f}% of volume moves on
deal, and only the incremental part of it is lift), so it cannot move a portfolio-level CV by a
large multiple. It shows up clearly in three places: the
week-of-year promo profile (promo volume share swings from about {st["promo_woy_lo"]*100:.0f}% in the January trough to
about {st["promo_woy_hi"]*100:.0f}% in the Thanksgiving/Christmas window), promo-week accuracy, and any single SKU-DC
series. It will look muted if you aggregate across the whole portfolio and the whole year at
once.

Both panels are **complete**: every (SKU, DC, customer, week) cell exists, with explicit
zero-demand rows. v3's in-sample panel was 66% filled with no zero-demand rows at all, while
its forward panel was 100% dense.

#### Estimating the two elasticities (method for §6.1)

Recommended method -- one regression with BOTH price terms and SKU + week fixed effects:

    ln(order_qty)_{{i,t}} = alpha_i + gamma_t
                        + beta_base  * ln(base_price_usd)_{{i,t}}
                        + beta_promo * ln(1 - discount_pct)_{{i,t}} + e

* `alpha_i` (SKU FE) absorbs baseline volume/price; `gamma_t` (week FE) absorbs common seasonality.
* `beta_base` is identified from shelf-price drift/revisions; `beta_promo` from discount depth.
* Do not assume |beta_promo| > |beta_base| everywhere: the clip described in §6.1 reverses it for
  very elastic groups{holds}. Estimate per
  category (pool SKUs) for stable numbers.

Minimal snippet (statsmodels; if `orders.csv` in your checkout is a Git LFS pointer, read
`orders.parquet` instead):

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
'''


# Nominal cycle service level each service factor implies under a normal
# demand distribution at a single location.
_Z_NOMINAL = {2.05: 98, 1.64: 95, 1.28: 90}


def _policy_doc_section(st):
    """Plain-words data dictionary for the policy table.

    Numbers are read off the table that was just written (and, via `st`, off
    the order history and lanes), so the prose cannot drift away from the data.
    """
    OUT = OUTPUT_DIR
    b = pd.read_csv(f"{OUT}/baseline_policy.csv")
    b["t"] = b.dc_id.str[:3]
    ech = b.t.value_counts().to_dict()
    seg = b.segment.value_counts().to_dict()
    zmap = b.groupby("segment").z.first().to_dict()
    med = b[["safety_stock", "reorder_point", "order_up_to_level"]].median()
    mx = b[["safety_stock", "reorder_point", "order_up_to_level"]].max()
    n_zero = int((b.safety_stock == 0).sum())
    n_zero_cdc = int(((b.safety_stock == 0) & (b.t == "CDC")).sum())
    batch = {t: (g.order_up_to_level - g.reorder_point).median()
             for t, g in b.groupby("t")}
    ratio = {t: (g.order_up_to_level / g.reorder_point.replace(0, np.nan)).median()
             for t, g in b.groupby("t")}
    fams = ", ".join(f"`{f}`" for f in sorted(b.production_family.unique()))

    # a SKU carried at many nodes makes the clearest worked example
    _cnt = b.groupby("sku_id").size()
    _ex_sku = b[b.sku_id.isin(_cnt[_cnt == _cnt.max()].index)] \
        .groupby("sku_id").order_up_to_level.sum().idxmax()
    ex = (b[b.sku_id == _ex_sku]
          .sort_values("order_up_to_level", ascending=False)
          [["dc_id", "safety_stock", "reorder_point", "order_up_to_level", "z"]])
    ex_rows = "\n".join(
        f"| {r.dc_id} | {r.safety_stock:,} | {r.reorder_point:,} | "
        f"{r.order_up_to_level:,} | {r.z} |" for r in ex.itertuples(index=False))
    ex_seg = b.loc[b.sku_id == _ex_sku, "segment"].iloc[0]
    ex_fam = b.loc[b.sku_id == _ex_sku, "production_family"].iloc[0]

    # prose that depends on the order history, lanes and sourcing map
    _gap = st["mu_gap"]
    if abs(_gap) < 0.005:
        mu_level = (f"In aggregate `mu` sits at about the {st['last_yr']} weekly level.")
    elif _gap < 0:
        mu_level = (f"Because `mu`\naverages in the lower {st['burn_span']} volumes, in aggregate it sits "
                    f"about {abs(_gap)*100:.0f}% below the\n{st['last_yr']} weekly level.")
    else:
        mu_level = (f"Because `mu`\naverages in the higher {st['burn_span']} volumes, in aggregate it sits "
                    f"about {_gap*100:.0f}% above the\n{st['last_yr']} weekly level.")
    if st["n_fb1"] == 0:
        fb = "No row in this instance needs\neither fallback."
    else:
        fb = (f"In this instance {st['n_fb1']:,} rows have no sourcing entry"
              f" ({st['n_fb2']:,} of them also have no inbound lane).")
    _L = st["plant_pdc_lt"]
    pdc_L = f"{_L[0]:g}" if _L[0] == _L[1] else f"{_L[0]:g}-{_L[1]:g}"
    _MIN_S = f"{MIN_S_MULTIPLE:g}"

    return f"""
---

## Part 5 — The policy table, in plain words

`baseline_policy.csv` is one row per **(stocking location, SKU)** carrying three
numbers: how much cushion to hold, when to reorder, and how far to top back up.
That is the whole inventory decision, and it is the only thing an optimizer
changes.

The same shape appears three times in the project. Only the first is part of
this dataset; the other two are forward artefacts and are described in
`output datasets/OUTPUT_DOCUMENTATION.md`.

| File | Rows | Sized on | Role |
|---|---|---|---|
| `baseline_policy.csv` (here) | {len(b):,} | the **order history** | ran the past, so it is part of the record |
| `forward_policy.csv` | — | the **forecast** | runs forward scenarios |
| `policy.csv` (inside a run) | — | whatever was fed in | audit trail of what a run executed |

### Column by column

**`dc_id`** — where the stock sits. The prefix tells you which kind of place:

| Prefix | What it is | Policy rows |
|---|---|---|
| `PDC` | Plant DC — the warehouse at a factory | {ech.get('PDC', 0):,} |
| `RDC` | Regional DC — serves big customers directly *and* restocks the customer DCs | {ech.get('RDC', 0):,} |
| `CDC` | Customer DC — the last stop before the retailer | {ech.get('CDC', 0):,} |

**`sku_id`** — which product. With `dc_id` this is the unique key; there are no
duplicate pairs.

**`safety_stock`** — the cushion. Stock held purely to absorb surprises: demand
arriving higher than expected, or a delivery turning up late. In a perfectly
predictable world it would be zero. Median {med.safety_stock:,.0f} units, largest
{mx.safety_stock:,.0f} — the spread is wide because it scales with how much of a
product a location sells. {n_zero} rows are **zero**, {n_zero_cdc} of them at
customer DCs: products selling a unit or two a year there, where the arithmetic
honestly says hold nothing.

**`reorder_point`** — the trigger, often written `s`. When stock on hand plus
anything already in transit falls to this number, order. It has to cover demand
across the whole exposed window, which is *not* just the delivery time: reviews
happen weekly, so a location that has just missed this week's review waits up to
seven days before it can even order, and then still waits out transit. Median
{med.reorder_point:,.0f} units. Always at least `safety_stock`, because it is the
cushion plus expected demand over that window.

**`order_up_to_level`** — the target, often written `S`. When you order, order
enough to bring the position up to this; the order size is `S` minus what you
have. Median {med.order_up_to_level:,.0f} units, and always at least
`reorder_point`.

The gap `S - s` is the **order batch** — how much you buy at a time — and it
differs by echelon on purpose:

| Echelon | median `S - s` | median `S / s` |
|---|---|---|
| CDC | {batch.get('CDC', 0):,.0f} u | {ratio.get('CDC', float('nan')):.2f} |
| RDC | {batch.get('RDC', 0):,.0f} u | {ratio.get('RDC', float('nan')):.2f} |
| PDC | {batch.get('PDC', 0):,.0f} u | {ratio.get('PDC', float('nan')):.1f} |

Plant DCs look like outliers because they work differently — see below.

**`production_family`** — which factory line makes it: {fams}. Carried for
reference; lines have their own capacity limits, so this is how a shortage is
traced back to a bottleneck.

**`segment`** — how the product behaves, and the reason two SKUs at the same
site get different protection:

| Segment | Rows | Behaviour |
|---|---|---|
{chr(10).join(f"| `{k}` | {v:,} | " + {"Fast-Moving": "sells steadily, high volume", "Seasonal": "predictable peaks and troughs", "Intermediate": "sporadic and lumpy, hard to forecast"}.get(k, "") + " |" for k, v in sorted(seg.items(), key=lambda kv: -kv[1]))}

**`z`** — how hard the product is protected: the service factor. Higher means a
bigger cushion, fewer stockouts, and more cash tied up. It is set once per
segment, which makes this single column the differentiated-service policy:

| Segment | `z` | Protection a single-site formula implies |
|---|---|---|
{chr(10).join(f"| `{k}` | **{v}** | ~{_Z_NOMINAL.get(round(v, 2), '')}% |" for k, v in sorted(zmap.items(), key=lambda kv: -kv[1]))}

Read "implies" carefully. Those are the targets a textbook single-location
formula produces. Realised fill lands lower, because that formula cannot see
shortfall arriving from upstream or the timing of demand inside a week.

### How the three numbers are computed

```
protection window   P  = 1 week (the review) + lead time of the lane this SKU travels
safety_stock           = z x sqrt( P x sigma_demand^2 + mu_demand^2 x sigma_leadtime^2 )
reorder_point    s     = mu_demand x P + safety_stock
order_up_to_level S    = max( s + mu_demand x {ORDER_COVER_WEEKS} weeks , {_MIN_S} x s )
```

The `{_MIN_S} x s` floor binds in about {st['floor_share']*100:.0f}% of regional and customer DC rows.

`mu` and `sigma` come from that location's weekly orders for that SKU over the
**full {st['span']} order history**, not the {st['last_yr']} year alone. They are on different
bases. `mu` is total units divided by all {st['n_weeks']} weeks, zero weeks included.
`sigma` is the standard deviation over only the weeks in which the location
actually ordered the SKU. Both include seasonality and trend. {mu_level}

Demand here means orders placed **at that location only**. A regional DC's
`mu` and `sigma` are its own bulk customers' orders. They do not include the
replenishment flow it passes on to its customer DCs.

The lane lead time comes from `sku_sourcing_map.csv` — the route the SKU
actually travels, which matters a great deal: one plant-DC-to-regional-DC lane
runs {_days(st['pdc_rdc_lt'][0])} and another {_days(st['pdc_rdc_lt'][1])}. A pair without a sourcing entry
would fall back to the shortest lane into the node, and failing that to a
3.0-day mean and 1.0-day standard deviation. {fb}

Sigma is uplifted at regional DCs (x{SIGMA_UPLIFT['RDC']}; customer DCs x{SIGMA_UPLIFT['CDC']}) as an allowance
for the order lumpiness a parent absorbs from its children, even though, as
noted above, the children's flow itself is not in the regional DC's `mu`. The
plant-DC factor (x{SIGMA_UPLIFT['PDC']}) multiplies throughput rather than a sigma (below).

**Plant DCs are the exception.** They are not *pulled* by a reorder point —
production *pushes* finished goods into them. Their throughput `mu` is the sum
of the `mu` of every regional DC that sources the SKU from them. That again
counts regional DCs' own customer demand only, not the customer-DC flow behind
them. The three numbers are heuristics proportional to throughput, not to its
variability:

```
safety_stock       = z x sqrt(L / 7) x mu x {SIGMA_UPLIFT['PDC']} x 0.5     L = plant-to-plant-DC lead time, days ({pdc_L})
reorder_point  s   = safety_stock + mu x L / 7
order_up_to    S   = mu x {PDC_COVER_WEEKS} weeks + safety_stock
```

`S` is a holding ceiling sized on throughput (about {PDC_COVER_WEEKS} weeks of it), not a trigger,
which is what the {ratio.get('PDC', float('nan')):.1f}x `S / s` ratio above is showing.

### Worked example — `{_ex_sku}`, {ex_seg.lower()}, {ex_fam}

One product, {len(ex)} locations, {len(ex)} different policies:

| dc_id | safety_stock | reorder_point | order_up_to_level | z |
|---|---|---|---|---|
{ex_rows}

`z` is identical everywhere, because it is a property of the product. Everything
else varies with **local demand** and **lane length**. The largest regional DC
carries orders of magnitude more than the smallest customer DC because its own
bulk customers order far more. Its policy does not include the customer DCs it
restocks. A row of
zeros, if one appears above, is a location that barely sells this product at all
— it is stocked on paper but the arithmetic says hold nothing and never reorder.

### What consumes it

The digital twin reads exactly five columns — `dc_id`, `sku_id`,
`safety_stock`, `reorder_point`, `order_up_to_level` — and ignores everything
else, including `production_family`, `segment`, `z` and any extra columns an
optimizer chooses to emit. A missing required column raises an error rather than
quietly defaulting to zero.

Echelon positioning needs no extra input either: "push buffer to the customer
DCs" is simply higher CDC `S` and lower RDC `S` in this same table.

---

## Part 6 — How each layer is generated
"""


def write_schema_doc():
    """Emit SCHEMA.md — the project-level data contract.

    Deliberately short. It says where things live and why, and points at the
    two detailed documents rather than repeating them:

        input datasets/DATASET_DOCUMENTATION.md     the given data (this script)
        output datasets/OUTPUT_DOCUMENTATION.md     the twin and its runs
    """
    OUT = OUTPUT_DIR
    root = os.path.dirname(os.path.abspath(OUT))
    world = sorted(os.path.basename(f) for f in os.listdir(OUT)
                   if f.endswith(".csv"))
    # calendar facts that do NOT follow START_YEAR automatically
    _trend_yrs = f"{min(YEAR_TREND)}-{max(YEAR_TREND)}"
    _w1 = f"{WEEK1_MONDAY.day} {WEEK1_MONDAY.strftime('%B')}"
    _w53 = [y for y in YEARS if dt.date(y, 12, 28).isocalendar()[1] == 53]
    _w53_note = (f"; ISO {', '.join(f'{y}-W53' for y in _w53)} "
                 f"{'is' if len(_w53) == 1 else 'are'} not generated" if _w53 else "")
    doc = f"""# Supply Brain — data layout

*Auto-generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} by
`supply_brain_generator_v5.py`. Do not edit by hand.*

The line between the two folders is **time**.

```
input datasets/      THE PAST AND THE GIVENS — read-only
                     Network and product masters, cost structure, the order
                     history, the promotions that ran, the published forecast,
                     the disruption library, and the inventory record of what
                     actually happened. {len(world)} tables.

output datasets/     THE FORWARD VIEW — one folder per run
                     The forward policy, the position the horizon opens on,
                     and every scenario run with its summaries.
```

The historical `inventory.csv`, `replenishment_receipts.csv` and
`production.csv` sit in `input datasets/` even though the digital twin produces
them: they are the record of the past, which is input to any analysis. Only
what looks *forward* moves to `output datasets/`. The generator prints a
warning if a forward artefact ever appears in the input folder.

The horizon boundary is derived from the generator's horizon constants. History is
the span the order history covers ({period_label(1)}..{period_label(ACTUAL_WEEKS)},
{ACTUAL_WEEKS} weeks) and the forward horizon is everything after it
({period_label(FWD_START_WEEK)}..{period_label(TOTAL_WEEKS)}, {FWD_WEEKS} weeks).
Period labels and the history/forward split follow `START_YEAR` and the week
counts, but not everything does: `YEAR_TREND` is keyed by calendar year
({_trend_yrs}) and `WEEK1_MONDAY` is fixed to {_w1} of `START_YEAR`. Changing the
start year means updating those as well.

The generator writes CSV. Every input table also has a `.parquet` copy in the
repository, and the final MEIO pipeline and fast twin read those. `orders.csv`
and `inventory.csv` are Git LFS pointer files, so without LFS only their parquet
copies hold data.

---

## Where the detail lives

| Document | Written by | Covers |
|---|---|---|
| `input datasets/DATASET_DOCUMENTATION.md` | `supply_brain_generator_v5.py` | Every input table, the business assumptions behind it, how each layer is generated, the policy dictionary, and a data profile |
| `output datasets/OUTPUT_DOCUMENTATION.md` | `models/twin/digital_twin.py` (also rewritten by `digital_twin_fast.py`) | What the twin does, its event order and assumptions, and the schema of every artefact a run produces |

---

## Pipeline

```
--stage world     masters, orders, forecast, routing, capability
                        └───────────────────────────► input datasets/
--stage history   baseline_policy ──────────────────► input datasets/
                  twin (history mode)
                        ├─ inventory, receipts, production, write_offs
                        │                           ─► input datasets/
                        └─ forward_policy, opening_position, summaries
                                                    ─► output datasets/<run>/
                  scenarios, validate, document    ─► input datasets/
                                                       (SCHEMA.md ─► project root,
                                                        OUTPUT_DOCUMENTATION.md ─► output datasets/)

forward scenario  twin (forward mode)               ─► output datasets/projections/baseline/
                     --policy <forward_policy.csv>        (run name "baseline"),
                     --state  <opening_position.csv>      output datasets/<run-name>/ otherwise,
                                                          output datasets/SCENARIO_####_Name/
                                                          with --scenario-config

MEIO pipeline     run_meio_then_twin.py (reads scenario_config.json)
                    1. Clark-Scarf v2 MEIO          ─► output datasets/MEIO/SCENARIO_####_Name/
                                                         rolling_2026/meio_policy_rolling.parquet
                    2. digital_twin_fast.py         ─► output datasets/SCENARIO_####_Name/<run_name>/
                       (forward, same opening_position.csv)
```

Build the dataset:

```bash
python supply_brain_generator_v5.py --stage all --run-name history
```

Score a forward scenario against it:

```bash
python models/twin/digital_twin.py --mode forward --run-name baseline \
    --policy "output datasets/history/forward_policy.csv" \
    --state  "output datasets/history/opening_position.csv"
```

Stages run independently: `--stage world` rebuilds the given data, `--stage
history` re-simulates the past against it (`--stage` accepts only `world`,
`history` and `all`). `--skip-docs` omits validation, documentation and the data
profile. `--seed` (default 2026) seeds the history-mode twin run only; the seeds
that build the world are fixed in code.

Run the final MEIO policy build and score it in the fast twin:

```bash
python run_meio_then_twin.py --scenario-config scenario_config.json
```

---

## Conventions

| Field | Meaning |
|---|---|
| `period` | ISO week label `YYYY-Www` ({WEEKS_PER_YEAR} weeks per year{_w53_note}) |
| `week_start_date` | Monday of that week |
| `dc_id` | `PDC01`…`RDC08`…`CDC15` — plant DC, regional DC, customer DC |
| `sku_id` | `SKU0001`… |
| units | whole units unless the column name ends `_usd` |
| `*_day` | integer day index, day 0 = first Monday of the horizon |

All monetary fields are USD and suffixed `_usd`. Percentages and multipliers
are rates, not currency.

One simulation step is one day; the reporting grain is one week. Demand and
shortfall are always split into **customer** (external) and **replen**
(internal, ordered by a child node), because the fulfilment rules differ:
customer shortfalls at a customer DC are lost sales, internal shortfalls are
cancelled at week close and re-ordered at the next review.

---

## The optimization seam

One standalone table, five required columns:

```
dc_id, sku_id, safety_stock, reorder_point, order_up_to_level
```

That is the only thing an optimizer changes. Extra columns are ignored, so an
optimizer's native output can be handed over verbatim. The exception is an
optional `period` column: when it is present, the policy is time-varying, with one
row per week. The MEIO rolling policy (`meio_policy_rolling.parquet`, {period_label(FWD_START_WEEK)}..W{woy(TOTAL_WEEKS):02d})
uses this. The canonical twin reads the policy as CSV; the fast twin
(`digital_twin_fast.py`) reads CSV or parquet. Opening state comes from
a separate `--state` file and is never touched by a policy swap, so two runs
start from an identical physical position and the difference between them
measures the decision rather than the starting point.

Full dictionary: Part 5 of `input datasets/DATASET_DOCUMENTATION.md`.
"""
    with open(os.path.join(root, "SCHEMA.md"), "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"  wrote SCHEMA.md ({doc.count(chr(10)):,} lines)")


_TIER_WORD = {"PLANT": "plant", "PLANT_DC": "plant-DC", "REGIONAL_DC": "regional-DC",
              "CUSTOMER_DC": "customer-DC"}
_MODE_WORD = {"Train": "rail", "Truck": "truck"}
_POSTURE_WORD = {"EDLC": "everyday-low-cost", "HIGH_LOW": "high-low", "CONTRACT": "contract"}
_NUM_WORD = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
             9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
             15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
             20: "twenty"}


def _frac_words(x, tol=0.02):
    """A share in words when it sits close to a simple fraction ("half", "a third"),
    otherwise as a percentage. Keeps computed figures readable in prose."""
    for v, w in ((0.5, "half"), (1 / 3, "a third"), (2 / 3, "two-thirds"),
                 (0.25, "a quarter"), (0.75, "three-quarters"), (0.2, "a fifth")):
        if abs(x - v) <= tol:
            return w
    return f"{x*100:.0f}%"


def _one_in(x, noun):
    """'one <noun> in five' when 1/x is close to a whole number, else 'N% of <noun>s'."""
    if x > 0:
        n = round(1 / x)
        if 2 <= n <= 20 and abs(1 / x - n) / n < 0.05:
            return f"one {noun} in {_NUM_WORD[n]}"
    return f"{x*100:.0f}% of {noun}s"


def _days(x):
    """A lead time in words: 'half a day', 'nearly thirteen days', or '3.2 days'."""
    if abs(x - 0.5) < 0.05:
        return "half a day"
    if x >= 2 and x - math.floor(x) >= 0.75 and math.ceil(x) in _NUM_WORD:
        return f"nearly {_NUM_WORD[math.ceil(x)]} days"
    return f"{x:.1f} days"


def _doc_stats(OUT, sku, orders, inv, fcst, rcpt, ban):
    """Figures the narrative in DATASET_DOCUMENTATION.md quotes, computed from the tables
    just written so the prose cannot drift from the data. Read-only: nothing here feeds
    back into any generated table."""
    st = {}
    st["first_yr"] = period_label(1)[:4]
    st["burn_last_yr"] = period_label(BURN_WEEKS)[:4]
    st["last_yr"] = period_label(ACTUAL_WEEKS)[:4]
    st["fwd_yr"] = period_label(FWD_START_WEEK)[:4]
    st["span"] = f"{st['first_yr']}–{st['last_yr'][2:]}"
    st["burn_span"] = f"{st['first_yr']}–{st['burn_last_yr'][2:]}"
    st["n_subcat"] = sku.subcategory.nunique()
    st["n_cat"] = sku.category.nunique()
    st["promo_vol"] = float(orders.loc[orders.on_promo, "order_qty"].sum()
                            / orders.order_qty.sum())
    st["margin_lo"], st["margin_hi"] = float(sku.margin_pct.min()), float(sku.margin_pct.max())

    # --- commercial postures: promotional exposure by pricing strategy ------
    ob = orders[["b2b_customer_id", "order_qty", "on_promo", "discount_pct"]].merge(
        ban[["b2b_customer_id", "pricing_strategy"]], on="b2b_customer_id")
    _tot = ob.groupby("pricing_strategy").order_qty.sum()
    _pq = ob[ob.on_promo].groupby("pricing_strategy").order_qty.sum().reindex(_tot.index).fillna(0)
    st["posture_share"] = (_pq / _tot).to_dict()
    st["posture_depth"] = ob[ob.on_promo].groupby("pricing_strategy").discount_pct.mean().to_dict()
    promos = pd.read_csv(f"{OUT}/promotions.csv", usecols=["b2b_customer_id"])
    st["promo_all_share"] = float((promos.b2b_customer_id == "ALL").mean())
    _contract = set(ban.loc[ban.pricing_strategy == "CONTRACT", "b2b_customer_id"])
    st["contract_targeted"] = bool(promos.b2b_customer_id.isin(_contract).any())
    _wk = orders.period.str[-2:].astype(int)
    _woy = (orders.order_qty.where(orders.on_promo, 0).groupby(_wk).sum()
            / orders.order_qty.groupby(_wk).sum())
    st["promo_woy_lo"], st["promo_woy_hi"] = float(_woy.min()), float(_woy.max())

    # --- lanes actually used (the sourcing map is the routing the twin runs) -
    lanes = pd.read_csv(f"{OUT}/network_lanes.csv")
    smap = pd.read_csv(f"{OUT}/sku_sourcing_map.csv")
    _used = set(zip(smap.origin_node_id, smap.dc_id))
    lu = pd.Series([(o, d) in _used for o, d in
                    zip(lanes.origin_node_id, lanes.destination_node_id)], index=lanes.index)
    lat = (lanes.origin_node_type == "REGIONAL_DC") & (lanes.destination_node_type == "REGIONAL_DC")
    st["n_lanes"] = len(lanes)
    st["n_unused"] = int((~lu).sum())
    st["n_lat"] = int(lat.sum())
    st["n_fail_unused"] = int(((lanes.destination_node_type == "CUSTOMER_DC") & ~lu).sum())
    st["n_other_unused"] = st["n_unused"] - st["n_fail_unused"] - int((lat & ~lu).sum())
    st["cdc_single"] = bool((smap[smap.dc_id.str.startswith("CDC")]
                             .groupby("dc_id").origin_node_id.nunique() <= 1).all())
    ul = lanes[lu]
    _lo = ul.lead_time_mean_days.min()
    _at_lo = set(ul.loc[ul.lead_time_mean_days == _lo, "destination_node_type"])
    _lo_words = {"PLANT_DC": "a co-located plant DC",
                 "REGIONAL_DC": "a regional DC in the same metro as its plant DC",
                 "CUSTOMER_DC": "a customer DC in the same metro as its regional DC"}
    st["lt_lo"] = float(_lo)
    _lw = [w for t, w in _lo_words.items() if t in _at_lo]
    st["lt_lo_where"] = (", ".join(_lw[:-1]) + ("," if len(_lw) > 2 else "") + " or " + _lw[-1]
                         if len(_lw) > 1 else "".join(_lw))
    _hi = ul.loc[ul.lead_time_mean_days.idxmax()]
    st["lt_hi"] = float(_hi.lead_time_mean_days)
    st["lt_hi_lane"] = (f"{_TIER_WORD.get(_hi.origin_node_type, _hi.origin_node_type)}-to-"
                        f"{_TIER_WORD.get(_hi.destination_node_type, _hi.destination_node_type)} "
                        f"{_MODE_WORD.get(_hi.transport_mode, _hi.transport_mode)} lane")
    _c = ul[ul.destination_node_type == "CUSTOMER_DC"].lead_time_mean_days
    st["cdc_lt"] = (float(_c.min()), float(_c.max()))
    _p = ul[(ul.origin_node_type == "PLANT_DC")
            & (ul.destination_node_type == "REGIONAL_DC")].lead_time_mean_days
    st["pdc_rdc_lt"] = (float(_p.min()), float(_p.max()))
    _pl = lanes[lanes.destination_node_type == "PLANT_DC"].lead_time_mean_days
    st["plant_pdc_lt"] = (float(_pl.min()), float(_pl.max()))

    # --- service by echelon ---------------------------------------------------
    _t = inv.dc_id.str[:3]

    def _fill(kind, tier):
        m = _t == tier
        d = inv.loc[m, f"demand_{kind}_qty"].sum()
        return float(1 - inv.loc[m, f"unmet_{kind}_qty"].sum() / d) if d > 0 else float("nan")
    st["fill_rdc_cust"] = _fill("customer", "RDC")
    st["fill_cdc_cust"] = _fill("customer", "CDC")
    st["fill_pdc_replen"] = _fill("replen", "PDC")
    st["fill_rdc_replen"] = _fill("replen", "RDC")
    _wf = inv.groupby("period")[["demand_customer_qty", "unmet_customer_qty"]].sum().sort_index()
    _wf = 1 - _wf.unmet_customer_qty / _wf.demand_customer_qty.replace(0, np.nan)
    st["wk1_fill"] = float(_wf.iloc[0])
    _r90 = np.flatnonzero(_wf.to_numpy() >= 0.90)
    st["wk_reach90"] = int(_r90[0]) + 1 if len(_r90) else None
    st["cold_start"] = bool(inv.loc[inv.period == period_label(1), "opening_stock"].sum() == 0)

    # --- a short receipt vs a customer stockout the following week -----------
    # Receipt arrival week (day 0 is the Monday of week 1); compare the next week at the
    # same location, on location-weeks that have customer demand.
    r = rcpt[["dc_id", "sku_id", "arrival_day", "short_qty"]].copy()
    r["wk"] = r.arrival_day // 7 + 2
    r = (r[r.wk <= ACTUAL_WEEKS].groupby(["dc_id", "sku_id", "wk"]).short_qty.max()
         .gt(0).rename("short").reset_index())
    r["period"] = r.wk.map({w: period_label(int(w)) for w in r.wk.unique()})
    _nx = inv.loc[inv.demand_customer_qty > 0, ["dc_id", "sku_id", "period", "unmet_customer_qty"]]
    j = r.merge(_nx, on=["dc_id", "sku_id", "period"])
    _p1 = (j.loc[j.short, "unmet_customer_qty"] > 0).mean()
    _p0 = (j.loc[~j.short, "unmet_customer_qty"] > 0).mean()
    st["short_ratio"] = float(_p1 / _p0) if _p0 > 0 else float("nan")

    # --- forecast quality over the evaluated year ------------------------------
    ins = fcst[fcst.horizon == "in_sample"]
    st["bias_all"] = float(ins.forecast_qty.sum() / ins.actual_qty.sum() - 1)
    nz = ins[ins.actual_qty > 0].merge(sku[["sku_id", "segment", "category"]], on="sku_id")
    st["bias_nz"] = float(nz.forecast_qty.sum() / nz.actual_qty.sum() - 1)
    _gb = pd.concat([nz.groupby(c)[["forecast_qty", "actual_qty"]].sum()
                     for c in ("segment", "category")])
    _gbias = _gb.forecast_qty / _gb.actual_qty - 1
    st["bias_groups_neg"] = bool((_gbias < 0).all())
    st["bias_groups_pos"] = bool((_gbias > 0).all())
    st["zero_cells"] = float((ins.actual_qty == 0).mean())
    pr = ins[ins.forecast_promo_lift_qty > 0]
    _a = pr.actual_qty.sum()
    st["wmape_promo_base"] = float((pr.forecast_baseline_qty - pr.actual_qty).abs().sum() / _a) if _a else float("nan")
    st["wmape_promo_incl"] = float((pr.forecast_qty - pr.actual_qty).abs().sum() / _a) if _a else float("nan")
    st["lift_share"] = float(ins.forecast_promo_lift_qty.sum() / ins.forecast_qty.sum())
    _nat = ins.groupby("period")[["forecast_baseline_qty", "forecast_qty", "actual_qty"]].sum()
    _cv = _nat.std() / _nat.mean()
    st["cv_baseline"], st["cv_forecast"], st["cv_actual"] = (
        float(_cv.forecast_baseline_qty), float(_cv.forecast_qty), float(_cv.actual_qty))

    # --- baseline policy facts (mirrors derive_baseline_policy, basis="history")
    b = pd.read_csv(f"{OUT}/baseline_policy.csv")
    wk = orders.groupby(["period", "dc_id", "sku_id"], as_index=False).order_qty.sum()
    st["n_weeks"] = int(wk.period.nunique())
    mu = (wk.groupby(["dc_id", "sku_id"]).order_qty.sum() / st["n_weeks"]).rename("mu").reset_index()
    nb = b[~b.dc_id.str.startswith("PDC")].merge(mu, on=["dc_id", "sku_id"], how="left")
    nb["mu"] = nb.mu.fillna(0.0)
    st["floor_share"] = float((nb.mu * max(REVIEW_WEEKS, ORDER_COVER_WEEKS)
                               < (MIN_S_MULTIPLE - 1) * nb.reorder_point).mean())
    _last = orders[orders.period.str[:4] == st["last_yr"]]
    st["mu_gap"] = float((orders.order_qty.sum() / st["n_weeks"])
                         / (_last.order_qty.sum() / _last.period.nunique()) - 1)
    _pairs = set(zip(smap.dc_id, smap.sku_id))
    _miss = nb[[(d, s) not in _pairs for d, s in zip(nb.dc_id, nb.sku_id)]]
    st["n_fb1"] = len(_miss)
    st["n_fb2"] = int((~_miss.dc_id.isin(set(lanes.destination_node_id))).sum())
    return st


def write_documentation():
    """Write the single unified dataset document: business assumptions first, then schema,
    then generation methodology, then the data profile (EDA) folded in from generate_eda()."""
    OUT = OUTPUT_DIR
    sku = pd.read_csv(f"{OUT}/sku_master.csv")
    orders = pd.read_csv(f"{OUT}/orders.csv")
    inv = pd.read_csv(f"{OUT}/inventory.csv")
    fcst = pd.read_csv(f"{OUT}/demand_forecast.csv")
    rcpt = pd.read_csv(f"{OUT}/replenishment_receipts.csv")
    ban = pd.read_csv(f"{OUT}/b2b_customer_master.csv")
    terms = pd.read_csv(f"{OUT}/b2b_customer_terms.csv")
    cpf = pd.read_csv(f"{OUT}/cost_parameters.csv")
    _promo_vol = orders.loc[orders.on_promo, "order_qty"].sum() / orders.order_qty.sum()
    _pocket = (orders.groupby("b2b_customer_id")
               .apply(lambda g: (g.net_price_usd * g.order_qty).sum()
                      / (g.list_price_usd * g.order_qty).sum(), include_groups=False))
    _fill = 1 - inv.unmet_qty.sum() / inv.demand_qty.sum()
    _otd = rcpt.on_time_flag.mean()
    # customer-facing and internal-replenishment service are now separable (audit fix 2)
    _cfill = 1 - inv.unmet_customer_qty.sum() / max(inv.demand_customer_qty.sum(), 1)
    _rfill = 1 - inv.unmet_replen_qty.sum() / max(inv.demand_replen_qty.sum(), 1)
    _prd = pd.read_csv(f"{OUT}/production.csv")
    _plm = pd.read_csv(f"{OUT}/plant_master.csv")
    _caplim = float(_prd.capacity_limited_flag.mean())
    _capclip = 1 - _prd.planned_qty.sum() / max(_prd.plan_uncapped_qty.sum(), 1)
    _pu = (_prd.groupby(["plant_id", "period"]).actual_qty.sum().reset_index()
           .merge(_plm[["plant_id", "production_capacity_units_per_week"]], on="plant_id"))
    _putil = float((_pu.actual_qty / _pu.production_capacity_units_per_week).mean())
    _pupeak = float((_pu.actual_qty / _pu.production_capacity_units_per_week).max())

    # ---- figures and data-dependent sentences quoted in the narrative ----------
    P = _DOC_PROFILE_PART
    st = _doc_stats(OUT, sku, orders, inv, fcst, rcpt, ban)
    # The profile runs first so the elasticity it recovers can be quoted in Part 6.
    _eda = generate_eda(OUT, as_section=True, stats=st)
    _channels = ", ".join(f"`{c}`" for c in ban.channel_type.unique())

    _ps, _pd = st["posture_share"], st["posture_depth"]
    _pw = [k for k in ("EDLC", "HIGH_LOW", "CONTRACT") if k in _ps]
    if st["contract_targeted"]:
        contract_line = ("- **Contract accounts** (foodservice) take a flat negotiated price; about "
                         f"{_ps.get('CONTRACT', 0)*100:.0f}% of their\n  volume moved on a discount in the history.")
    else:
        contract_line = ("- **Contract accounts** (foodservice) take a flat negotiated price and are never targeted by\n"
                         "  account-specific promotions. Promotions aimed at every banner still reach them, though, so about\n"
                         f"  {_ps.get('CONTRACT', 0)*100:.0f}% of their volume moved on a discount in the history.")
    _shares = ", ".join(f"{_ps[k]*100:.0f}% of {_POSTURE_WORD[k]} volume" for k in _pw[:-1])
    _shares += f" and {_ps[_pw[-1]]*100:.0f}% of {_POSTURE_WORD[_pw[-1]]} volume"
    _dlo, _dhi = min(_pd.values()) * 100, max(_pd.values()) * 100
    _depth = f"{_dlo:.0f}%" if round(_dlo) == round(_dhi) else f"{_dlo:.0f}–{_dhi:.0f}%"
    _spread = max(_ps.values()) - min(_ps.values())
    if _spread < 0.05:
        posture_para = (
            "In the shipped data the postures differ mainly in everyday terms (off-invoice allowance and\n"
            f"trade-fund rates), not in promotional exposure. {_frac_words(st['promo_all_share']).capitalize()} "
            "of all promotion events target every banner\n"
            f"at once, so {_shares}\nmoved on a discount, at a similar average depth of {_depth}.")
    else:
        posture_para = (
            f"Promotional exposure differs by posture: {_shares} moved on a discount, at an\n"
            f"average depth of {_depth}. {_frac_words(st['promo_all_share']).capitalize()} of all "
            "promotion events target every banner at once.")

    if FACILITY_DETAIL is not None:
        _d = FACILITY_DETAIL
        _cs = (_d.groupby("dc_id").capital_charge_per_sqft_year_usd.first()
               / _d.groupby("dc_id").total_facility_cost_per_sqft_year_usd.first())
        capital_line = (f"On average {_cs.mean()*100:.0f}% of a warehouse's\nannual cost "
                        f"({_cs.min()*100:.0f}–{_cs.max()*100:.0f}% by site, §6.3) is the amortised cost "
                        "of having built it, not the cost of\nrunning it.")
        cost_deriv = "Derivation in Part 6 (§6.3)."
    else:   # facility build-up only exists in a run that also built the world
        capital_line = ("A large part of a warehouse's\nannual cost is the amortised cost of having "
                        "built it, not the cost of running it.")
        cost_deriv = "Derivation of the rates is documented by a full (world + history) run."

    _lo_where = f" ({st['lt_lo_where']})" if st["lt_lo_where"] else ""
    if st["short_ratio"] == st["short_ratio"] and st["short_ratio"] > 1.05:
        short_line = ("customer-facing stockout at that same location **more likely the week after**. "
                      f"In this instance it\nis about {st['short_ratio']:.1f} times as likely as after "
                      "a complete receipt.")
    else:
        short_line = ("customer-facing stockout at that same location more likely the week after, "
                      "although in this\ninstance the effect is not material.")

    _rc, _cc = st["fill_rdc_cust"], st["fill_cdc_cust"]
    _big = abs(_rc - _cc) >= 0.05
    _rdc_only = "only " if st["fill_rdc_replen"] < st["fill_pdc_replen"] - 0.05 else ""
    echelon_para = (
        f"**That headline hides a {'large ' if _big else ''}split by echelon.** Regional DCs fill "
        f"{_rc*100:.1f}% of their bulk\ncustomers' demand. Customer DCs fill {'only ' if _big and _cc < _rc else ''}"
        f"{_cc*100:.1f}% of direct-store-delivery demand, and because those\naccounts lose the sale, "
        f"about {_one_in(1 - _cc, 'DSD unit')} goes unserved. Internally, plant DCs fill "
        f"{st['fill_pdc_replen']*100:.1f}%\nof regional DC orders, while regional DCs fill "
        f"{_rdc_only}{st['fill_rdc_replen']*100:.1f}% of customer DC orders.")

    _b = st["bias_all"]
    if abs(_b) < 0.02:
        bias_head = "**The published forecast is close to unbiased.**"
    else:
        bias_head = f"**The published forecast is biased {'low' if _b < 0 else 'high'}.**"
    _grp = (" and the bias is negative in every segment and every\ncategory" if st["bias_groups_neg"] else
            " and the bias is positive in every segment and every\ncategory" if st["bias_groups_pos"] else
            " and its sign varies across segments and\ncategories")
    def _sgn(x):                                  # signed percentage with a true minus sign
        return f"{x*100:+.1f}%".replace("-", "−")
    bias_para = (f"{bias_head} Over the evaluated year it totals {abs(_b)*100:.1f}% "
                 f"{'below' if _b < 0 else 'above'} actual\ndemand ({_sgn(st['bias_nz'])} on weeks "
                 f"with non-zero demand),{_grp} (§{P}.9).")

    if st["n_unused"]:
        _parts = []
        if st["n_fail_unused"]:
            _parts.append(f"the {st['n_fail_unused']} failover lanes")
        if st["n_lat"]:
            _parts.append(f"the {st['n_lat']} laterals")
        if st["n_other_unused"]:
            _parts.append(f"{st['n_other_unused']} others")
        unused_line = (f"{st['n_unused']} of the {st['n_lanes']} lanes ({' and '.join(_parts)})\n"
                       "  carry nothing")
    else:
        unused_line = f"none of the {st['n_lanes']} lanes is idle"

    # Part 8 rows that depend on the data
    _wk90 = (f" and first passes 90% in week {st['wk_reach90']}" if st["wk_reach90"] else
             " and never reaches 90% in the history")
    if st["cold_start"]:
        warmup_row = (f"| **Week 1 starts every location with zero stock** (a cold start). | Customer fill is "
                      f"{st['wk1_fill']*100:.0f}% in the first week of {st['first_yr']}{_wk90}. The "
                      f"{BURN_WEEKS}-week burn-in absorbs this warm-up; evaluate on {st['last_yr']}. |")
    else:
        warmup_row = (f"| **The first weeks are a warm-up.** | Customer fill is {st['wk1_fill']*100:.0f}% in "
                      f"the first week of {st['first_yr']}{_wk90}. The {BURN_WEEKS}-week burn-in absorbs "
                      f"it; evaluate on {st['last_yr']}. |")
    bias_row_head = ("**The published forecast is biased low**" if _b <= -0.02 else
                     "**The published forecast is biased high**" if _b >= 0.02 else
                     "**The published forecast is roughly unbiased**")
    _grp_short = (", negative in every segment and category" if st["bias_groups_neg"] else
                  ", positive in every segment and category" if st["bias_groups_pos"] else "")
    bias_row = (f"| {bias_row_head} ({_sgn(_b)} in total over the evaluated year{_grp_short}, §{P}.9), "
                "and its promotional lift and customer split use information from the evaluated year (§2.10). | "
                "Check and correct bias before using it as a demand signal, and treat its "
                f"{st['last_yr']} accuracy as slightly optimistic. |")
    regional_row = ("| **Regional and plant DC policies ignore the customer-DC flow they pass on.** A regional DC's "
                    "`mu` and `sigma` are its own bulk customers' orders only, and a plant DC's throughput is the "
                    "sum of those regional `mu` values (Part 5). | Regional DC buffers are sized for bulk demand "
                    f"alone, and regional DCs fill {_rdc_only}{st['fill_rdc_replen']*100:.1f}% of customer DC orders. Do not "
                    "read them as echelon-sized buffers. |\n")
    cdc_row = (f"| **Customer-DC service is much weaker than the headline.** The {_cfill*100:.1f}% customer fill "
               f"blends {_rc*100:.1f}% at regional DCs with {_cc*100:.1f}% at customer DCs (§2.9). | Report "
               "service by echelon; a network average mostly reflects the bulk channel. |\n"
               if _cc < _cfill - 0.05 else "")
    lanes_row = ("| **Failover and lateral lanes are never used** (§2.13). | Resilience or transshipment "
                 "results that rely on those lanes cannot be validated against history. |\n"
                 if st["n_unused"] else "")

    _scn_path = f"{OUT}/scenarios.csv"
    _scn = pd.read_csv(_scn_path) if os.path.exists(_scn_path) else None
    _n_scn = len(_scn) if _scn is not None else 0
    scn_note = ""
    if _scn is not None and len(_scn):
        _sd = (pd.to_datetime(_scn.end_date) - pd.to_datetime(_scn.start_date)).dt.days + 1
        if (_sd == 7 * (_scn.duration_weeks + 1)).all():
            scn_note = (". `end_date` falls one week after the event's last week (the span is "
                        "`duration_weeks` + 1 weeks), so take the event window as `start_date` plus "
                        "`duration_weeks` weeks")
        elif not (_sd == 7 * _scn.duration_weeks).all():
            scn_note = (". `end_date` and `duration_weeks` do not always agree; take the event window "
                        "as `start_date` plus `duration_weeks` weeks")

    doc = f"""# Supply Brain — Synthetic CPG Supply-Chain Dataset

*Auto-generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} by `supply_brain_generator_v5.py`.
This document and the data are produced in the same run, so the figures below always describe the
current instance. It supersedes the separate EDA report, whose content is folded in as Part {P}.*

---

## Scope

This document covers **`input datasets/` only** — the given data: the
network, the products and their economics, the commercial history, the
published forecast, the disruption library, and the inventory record of
what happened over the historical span.

It does **not** cover the forward view. The digital twin documents its own
mechanics and every artefact a run produces in
`output datasets/OUTPUT_DOCUMENTATION.md`. `SCHEMA.md` at the project root
explains how the two folders divide.

## How to read this document

| Part | Contents | Read it if you want to… |
|---|---|---|
| **1** | What this dataset represents | Orient yourself in one minute |
| **2** | **Business assumptions, in plain language** | Understand what world this data describes, and what it deliberately does not |
| **3** | Time horizons | Know which years hold what |
| **4** | Table reference and key map | Join the tables correctly |
| **5** | The policy table, in plain words | Understand the (s, S) policy that ran the history |
| **6** | How each layer is generated | Trust, challenge or re-derive a number |
| **{P}** | Data profile | See the actual shape of this instance |
| **{P + 1}** | Known limitations | Avoid drawing a conclusion the data cannot support |
| **{P + 2}** | Reproducing and modifying | Change an assumption and re-run |

**Start with Part 2.** Every number in Parts 5 to {P} is a consequence of an assumption stated there.

---

## Part 1 — What this dataset represents

A complete operating picture of one mid-size **US consumer packaged goods (CPG) food and beverage
manufacturer**, at weekly grain, across three years of history and one year of forward plan.

It is designed to behave like a real client extract: every key resolves, the money adds up, and
the operational tensions a planner would recognise are present — demand that is hard to forecast in
some places and easy in others, replenishment that is sometimes late, stock that sits in the wrong
place, and promotions that move volume at the expense of margin.

| | |
|---|---|
| Portfolio | {sku.sku_id.nunique():,} SKUs across {sku.category.nunique()} categories and {sku.subcategory.nunique()} subcategories |
| Network | 4 plants → 4 plant DCs → 8 regional DCs → 15 customer DCs, {cpf.dc_id.nunique()} storage facilities in 20 US metros |
| Customers | {len(ban)} B2B retail banners across {ban.channel_type.nunique()} channel types ({_channels}), covering the grocery, mass, club, convenience and foodservice banners of §2.1 |
| History | {orders.period.nunique()} weeks of actual demand ({orders.period.min()} to {orders.period.max()}) |
| Forward plan | {fcst[fcst.horizon == 'forward'].period.nunique()} weeks |
| Annual revenue | ${orders.revenue_usd.sum()/len(orders.period.unique())*52/1e6:,.0f}M gross (run-rate) |
| Order lines | {len(orders):,} day-level lines in {orders.order_id.nunique():,} baskets |

**It is synthetic.** No real company's data is in here. The value is that the *structure* and the
*tensions* are realistic, so analytics built on it will transfer. Specific figures will not.

---

## Part 2 — Business assumptions, in plain language

This part contains no column names and no code. It is the description of the business you would
give a new planner on their first day.

### 2.1 The company and its portfolio

We are a branded CPG food and beverage manufacturer selling into US grocery, mass, club,
convenience and foodservice. We carry {sku.sku_id.nunique():,} active SKUs in five categories —
Beverages, Snacks & Confectionery, Dairy & Bakery, Frozen Foods and Grocery — subdivided into
{sku.subcategory.nunique()} subcategories such as liquor, wine, energy drinks, candy, popcorn,
flour, sugar, coffee and frozen vegetables.

Each category carries three brands at different price positions — a premium brand, a mainstream
brand and a value brand — and the premium-to-value price gap is large, roughly {max(m for t in CATEGORY_BRANDS.values() for _, m in t)/min(m for t in CATEGORY_BRANDS.values() for _, m in t):.1f}x, as it
is in real branded CPG.

Volume is heavily concentrated: a small share of SKUs generates most of the revenue. That
concentration is deliberate and it is the single most important fact for any segmentation or
service-level work.

### 2.2 What we sell it for, and what we actually keep

**List price is national.** We publish one list price per SKU across the whole country. This is
both commercially normal and legally significant: in the US, the Robinson-Patman Act restricts
discriminating on price between competing buyers of the same goods.

**Customer differentiation happens below list, through trade spend.** Each retail banner negotiates
its own package of everyday off-invoice allowances, promotional trade funds, volume rebates and
logistics allowances. What we actually keep after all of that is the **pocket price**, and it
differs by customer by roughly {(_pocket.max()-_pocket.min())*100:.0f} percentage points of list —
from about {_pocket.min()*100:.0f}% to {_pocket.max()*100:.0f}%.

Three commercial postures are modelled:

- **Everyday-low-cost accounts** (mass and club) take a permanently lower net price with little
  promotional funding.
- **High-low accounts** (conventional grocery) take a higher everyday price with deep, frequent
  temporary reductions.
{contract_line}

{posture_para}

Convenience-store accounts take the smallest deductions of any channel, so we realise the highest
pocket price from them — that is the direct-store-delivery service premium.

**Gross margin is set wide enough to absorb trade spend.** List prices are struck at roughly
{st['margin_lo']*100:.0f}-{st['margin_hi']*100:.0f}% gross margin so that promotional depth and trade allowances fit inside it. A small share of
volume still sells below cost on the deepest deals; that is a real loss-leader effect, not an error.

### 2.3 Where the products physically are

Four plants produce, each feeding its own on-site plant distribution centre. Product then moves to
eight regional distribution centres, and from there to fifteen customer-facing distribution
centres. Regional DCs are also cross-connected to each other by lateral lanes, but no rebalancing
flow is simulated across them (§2.13).

A regional DC does **not** simply buy from its nearest plant DC. Because only some plants are
qualified to make a given item (§2.4), each regional DC buys **each SKU from the nearest plant DC
whose plant can actually make it** (nearest by lane lead time) — so a Chicago regional DC stocking an Atlanta-only item pays
the Atlanta lane. Every plant DC therefore has a lane to every regional DC, and a plant DC only
ever holds SKUs its own plant produces.

Warehouse-delivered accounts (mass, club, large grocery) are served from regional DCs. Direct-store
-delivery accounts (convenience, smaller grocery, foodservice) are served from customer DCs.{" Each" + chr(10) + "customer DC is replenished from a single regional DC, the one on its shortest-lead-time lane." if st["cdc_single"] else ""}

**Every distribution centre can store every subcategory.** We assume one uniform warehouse type — a
plain ambient building. No product is assumed to need a freezer, a chiller, humidity control or a
bonded store. This is a simplifying assumption, and it is the biggest single departure from a real
food and beverage network (see Part {P + 1}).

### 2.4 How production is sourced

Plants are not interchangeable. Each is built around particular process technologies — liquid
filling, dry blending, confectionery, coffee roasting, chilled and frozen lines — and a plant can
only make what its lines support.

Within that constraint, each SKU is produced at the plant whose natural service area covers most of
its demand, so freight is minimised. High-volume SKUs are **dual-sourced** across two plants for
resilience, with the secondary plant costing slightly more per unit. Roughly a third of the
portfolio is dual-sourced.

Production plans are set from recent demand plus whatever it takes to restore about two and a
half weeks of finished-goods cover, and they are **constrained by capacity** — first by the individual
production line for that family, then by the plant as a whole. Around {_caplim*100:.0f}% of
SKU-weeks get clipped, at a mean plant utilisation of about {_putil*100:.0f}% and peaks near
{_pupeak*100:.0f}%.

Plans are then not hit perfectly — schedule attainment runs around
{_prd.adherence_pct.mean()*100:.0f}%, and **misses do not silently disappear**. What a plant
actually builds goes into a finished-goods pool, and plant DCs can only draw replenishment from
that pool. Under-build shows up as a plant DC that cannot fill its regional DCs, which in turn
becomes a delay further down the network.

### 2.5 How demand behaves

Demand at a given customer and DC in a given week is the product of several forces:

- **A base rate per SKU**, heavily skewed so a few SKUs dominate.
- **Seasonality driven by consumption occasion**, set at subcategory level rather than category
  level. This matters: hot tea peaks in winter while energy drinks peak in summer, and both are
  "Beverages". Baking staples — flour, yeast, sugar, spices — all peak together in the Oct–Dec
  holiday window even though they sit in different categories.
- **Regional consumption differences.** Sweet tea over-indexes in the Southeast, coffee in the
  Northeast, baking staples in the Midwest, wine on both coasts, and cassava and manioc flour in
  Florida and Texas. Regions are genuinely different markets, not scaled copies of each other.
- **Price response.** Everyday shelf prices drift and step, and demand responds with a
  subcategory-specific elasticity. Promotional discounts are given a stronger response than
  everyday price moves ({PROMO_MULT} times the base elasticity), as CPG research finds. The exception is
  the most price-sensitive subcategories: there a cap on promotional elasticity makes the
  promotional response the weaker of the two (§6.1).
- **A promotional trade calendar** (below).
- **Structural events.** Some SKUs gain or lose distribution mid-history, some take a permanent
  step price change, and a few launch part-way through. These are what make forecasting genuinely
  hard in places rather than uniformly noisy.
- **Order batching.** Customers do not order every SKU every week. Slower items are ordered every
  two or three weeks, so the order pattern is lumpier than the underlying consumption.

### 2.6 Promotions

About {_promo_vol*100:.0f}% of volume moves on promotion — in the normal range for US grocery.

Promotions are **not spread evenly through the year**. They cluster into the national trade windows
a US category manager would recognise: Super Bowl, Easter and spring, Memorial Day, July 4th,
back-to-school, Halloween, Thanksgiving and Christmas, with January as the post-holiday trough.
Promotional volume share swings from roughly {st['promo_woy_lo']*100:.0f}% in the January trough to about {st['promo_woy_hi']*100:.0f}% in the
Thanksgiving–Christmas peak.

Promotions are negotiated per account, so an event targets one banner or all of them. Deals are
**planned in advance and then executed imperfectly** — the depth that actually runs differs from the
depth that was planned. Storable categories show a post-promotion dip as customers work through
pantry-loaded stock.

### 2.7 Shelf life — deliberately switched off

**Every SKU is given a three-to-four year shelf life.** That is longer than the entire simulated
period, so nothing can ever expire.

This is intentional. It lets inventory, safety-stock and multi-echelon optimisation work be
developed and debugged without perishability confounding the results. The consequence is that
the write-off table is written empty and waste is not a measurable outcome in this dataset.

**One knock-on to be aware of:** with no obsolescence cost, holding cost becomes the only cost of
over-stocking. In a newsvendor calculation the economically optimal service level then pins near
100% for almost everything, so any service-level ladder built on this data is driven by strategic
value rather than by economics.

### 2.8 What it costs to hold stock

Holding cost has two parts, and we model them separately:

- **The cost of occupying space**, which depends on where the warehouse is and how bulky the
  product is.
- **The cost of capital tied up in the stock**, which depends on what the stock is worth.

The first part is built up from what a warehouse actually costs to own: construction cost per
square foot in that metro, the local property cap rate, building depreciation, electricity price,
and local labour, maintenance, insurance and property tax. {capital_line}

That means holding cost varies by **location** — a pallet in Boston costs materially more to keep
than the same pallet at the Dallas plant DC — and by **product density**, because a bag of flour
occupies far more space per unit than a sachet of yeast.

### 2.9 How we replenish, and how reliable it is

Each stocking location runs a reorder-point policy. Safety stock is sized from **both** sources of
uncertainty — how variable the demand is and how variable the replenishment lead time is — with a
service factor that differs by SKU segment: fast-moving items get the highest protection, seasonal
items less, intermediate items least. (Plant DCs are the exception: they use a simpler rule
proportional to throughput, Part 5.)

Replenishment lead times are real network lead times. Each location buffers **its own inbound
lane** — the leg from its immediate supplier — and we also publish the cumulative plant-to-node
lead time for echelon work. Inbound lanes in use range from about {_days(st['lt_lo'])}{_lo_where}
to {_days(st['lt_hi'])} on the longest {st['lt_hi_lane']}. Customer DCs' inbound lanes run
{st['cdc_lt'][0]:.1f}–{st['cdc_lt'][1]:.1f} days. **Each individual receipt arrives at its own realised lead time**,
not the average.

Suppliers quote a committed lead time above their average — the 90th percentile — so on-time
delivery runs around {_otd*100:.0f}% with a genuine late tail rather than a coin flip.

**Every receipt is a real shipment from a real parent.** A location can only ship what it has on
hand. When a regional DC cannot cover both its bulk customers and its customer DCs, customer orders
are served first and the remaining stock is split pro-rata across the customer DCs; the unfilled
balance of the internal order is cancelled, and the customer DC simply orders again the following
week. So an upstream stockout becomes a downstream delay: a short inbound receipt makes a
{short_line}
This is why the dataset distinguishes four demand and shortfall columns — customer versus internal
replenishment — on every inventory row.

When demand cannot be met from stock, the shortfall is recorded rather than discarded. Warehouse
accounts backorder; direct-store-delivery accounts lose the sale. The customer-facing unit fill
rate is about {_cfill*100:.1f}%, and internal replenishment between echelons fills about
{_rfill*100:.1f}%.

{echelon_para}

Plant DCs are supplied out of **finished goods that the plant actually produced**, and only for
SKUs that plant is qualified to make. Plants build to a rolling plan — a smoothed recent demand
signal (an exponentially weighted average of the replenishment orders placed on the plant DC)
plus whatever it takes to restore about two and a half weeks of finished-goods cover — and that plan is
clipped by production-line and plant-wide capacity. Capacity therefore binds in peak weeks
(roughly {_caplim*100:.0f}% of SKU-weeks are clipped, at a mean plant utilisation of about
{_putil*100:.0f}%), instead of being a number nobody consumes.

### 2.10 Two demand forecasts

We publish **two** forecasts, not one:

1. **A baseline forecast** — what we would expect to sell with no promotional activity at all. This
   is the trend-and-seasonality view, built on history with promotional weeks stripped out.
2. **A promotion-inclusive forecast** — the baseline plus the expected uplift from the *planned*
   promotional calendar. This is the number to replenish against.

The baseline is produced **ex ante**: at any week it is built from earlier weeks only (apart from
the interpolation that fills in de-promoted weeks). Two parts are not strictly ex ante. First, the
per-series promotional uplift is estimated from every promotional week in the {st['span']} history,
including the {st['last_yr']} weeks being scored. Second, the split from DC to customer uses each customer's
share of the whole {st['span']} history. Both leak a little information from the evaluated year. The
forecaster sees the *planned* promotional calendar but not the depth that actually ran, and its
assumed uplift is deliberately imperfect. So promotional weeks remain harder to forecast than
normal weeks, which is exactly the real-world condition.

{bias_para}

Publishing both separates two different failure modes: a poor baseline means the seasonal model is
wrong, while a poor uplift means the promotional plan is wrong. They need different fixes, and no
amount of safety stock fixes either.

### 2.11 Service-level targets

Three tiers are set by SKU segment — fast-moving, seasonal and intermediate — with cycle service
level targets of 98%, 95% and 90% and matching case fill rate targets. These are the *stated
policy*; whether the network achieves them is an outcome you can measure in the data.

### 2.12 Disruptions

A library of {_n_scn} labelled disruption scenarios covers demand surges, forecast misses, lane outages,
lead-time spikes, plant shortfalls and regional events. Every one is dated into the **forward**
year, so they stress the plan rather than replay history, and each carries a numeric magnitude,
time-to-recover, time-to-survive and a probability.

### 2.13 What is deliberately not modelled

Being explicit about the gaps matters more than the inclusions:

- **No special storage requirements.** No freezers, chillers, humidity control or bonded stores.
- **No waste or obsolescence.** Shelf life cannot bind (§2.7).
- **No binding warehouse or lane capacity.** *Production* capacity does bind (§2.9), but storage
  and transport are sized generously; nothing runs out of physical room or truck space.
- **No lateral transshipment and no failover.** `network_lanes` declares {st['n_lat']} regional-DC-to-regional
  -DC lateral lanes and a second, longer inbound lane for every customer DC, but **no flow is
  simulated across either** — {unused_line}. They are there so a resilience or
  transshipment model has somewhere to route to; they are not exercised in the history.
- **No lot or batch traceability**, and therefore no true first-expiry-first-out testing.
- **No returns, damages or reverse logistics.**
- **No customer-specific list pricing** beyond trade terms (§2.2).
- **Only ten B2B customers**, which is thin for customer-level segmentation.

---

## Part 3 — Time horizons

`period` is `YYYY-Www` and `week_start_date` is that week's Monday. Note that period `{fcst[fcst.horizon=='forward'].period.min()}`
begins in late December of the prior calendar year, as ISO-style week numbering does.

| Horizon | Weeks | What exists |
|---|---|---|
| Burn-in ({BURN_WEEKS} weeks) | 2023–2024 | Actual demand only. Exists so the forecaster has at least two prior seasonal cycles to learn from. |
| Evaluated year ({HIST_WEEKS} weeks) | 2025 | Actual demand **and** both forecasts, so accuracy is measurable. |
| Forward plan ({FWD_WEEKS} weeks) | 2026 | Both forecasts, a promotional calendar and a price plan. No actuals. (The opening inventory position is a run artefact in `output datasets/`.) |

`orders`, `production` and `inventory` cover all {ACTUAL_WEEKS} actual weeks. `demand_forecast`
covers the evaluated year and the forward plan.

## Part 4 — Table reference

### Key map

```
plant_master(plant_id) ─┬─ plant_dc_master(plant_dc_id, plant_id)
                        ├─ plant_capability(plant_id, production_family)
                        └─ sku_plant_sourcing(sku_id, plant_id)
regional_dc_master(regional_dc_id) ─┐
customer_dc_master(customer_dc_id) ─┴─ "stocking DCs"
network_lanes(origin_node_id, destination_node_id)   every endpoint is a plant / plant DC /
                                                     regional DC / customer DC
b2b_customer_master(b2b_customer_id) ─┬─ b2b_customer_terms(b2b_customer_id)
                                      └─ customer_serving_dc(b2b_customer_id, dc_id)
sku_master(sku_id) ─┬─ orders / demand_forecast / production / inventory
                    ├─ promotions(sku_id, b2b_customer_id)
                    └─ price_plan(sku_id, period)
cost_parameters(dc_id, subcategory)          holding-cost rates
seasonality_master(subcategory, month)       demand seasonality
regional_demand_index(region, subcategory)   regional consumption
replenishment_receipts(sku_id, dc_id)        ordered vs received, promised vs actual lead time
write_offs(sku_id, dc_id, period)            empty by design (§2.7)
sku_sourcing_map(dc_id, sku_id)              which parent serves each node, per SKU
baseline_policy(dc_id, sku_id)               the (s,S) that ran the history (Part 5)
```

All foreign keys are validated at generation time.

### Tables

| Table | Grain | Notes |
|---|---|---|
| sku_master | SKU | Catalogue and per-unit economics. `case_price_usd` / `units_per_case` / `list_price_usd` make the case-versus-unit basis explicit. `holding_cost_per_unit_year_usd` is the national average from the facility model; prefer `cost_parameters` for location-aware work. |
| plant_master, plant_dc_master, regional_dc_master, customer_dc_master | node | US network with real cities and coordinates |
| plant_capability | plant × production family | Which process lines each plant has |
| sku_plant_sourcing | SKU × plant | Primary and secondary source, allocation share, per-plant unit cost |
| network_lanes | lane | Truck/train, lead-time mean and standard deviation in days, distance, cost, capacity |
| b2b_customer_master | banner | Channel type, delivery mode, pricing strategy |
| b2b_customer_terms | banner | Off-invoice, trade fund, volume rebate, logistics allowance, promo frequency and depth multipliers |
| customer_serving_dc | banner × DC | Which DC serves each banner, and its demand share |
| cost_parameters | **DC × subcategory** (full cross product) | Storage cost per unit-year, inventory carrying %, stockout multiplier. {cost_deriv} |
| seasonality_master | subcategory × month | Occasion-driven seasonal index plus the shared macro calendar |
| regional_demand_index | region × subcategory | Regional consumption multiplier |
| orders | SKU × DC × customer × **day** | Order lines grouped into baskets by `order_id`. Full price waterfall: list → base → invoice → net (pocket), plus `net_margin_usd`. |
| promotions | promo event | Trade calendar. `planned_discount_pct` versus executed `discount_pct`; `plan_status` marks forward-year plans. |
| price_plan | SKU × forward period | Forward shelf-price path for the execution year |
| demand_forecast | SKU × DC × customer × week | **Two forecasts** (§2.10) plus the lift bridge. Complete panel: every cell exists, including explicit zero-demand rows. |
| production | SKU × plant × week | The uncapped ask (`plan_uncapped_qty`), the capacity-clipped plan, actual output, schedule attainment and a `capacity_limited_flag`, by production family |
| inventory | SKU × DC × week | Opening, receipts, demand, shipments, unmet, lost margin, closing, safety stock, reorder point, order-up-to, and lead time. Demand and shortfall are **split into customer versus internal replenishment** — `demand_customer_qty` sums exactly to `orders`, while `demand_qty` is an echelon total and counts a unit once per tier it passes through. Lead time appears twice: `lead_time_mean_days` is the node's own inbound lane (what its safety stock covers) and `cum_lead_time_mean_days` is the plant-to-node total (for echelon work). |
| replenishment_receipts | receipt | Promised versus actual lead time, an on-time flag, and `qty_ordered` versus `qty_received` with the `short_qty` balance — every line is a real shipment from a real parent, so internal fill rate is measurable |
| write_offs | SKU × DC × period | Empty by design (§2.7): written as a header-only table, not computed from the simulation |
| sku_sourcing_map | DC × SKU | The routing the twin executes: which parent serves each node for each SKU, and the `rule` that assigned it. Network structure, so it is an input — never recovered from simulated receipts |
| baseline_policy | DC × SKU | Safety stock, reorder point and order-up-to level that ran the historical simulation, with the segment and service factor behind them. Full dictionary in Part 5 |
| scenarios | scenario | {_n_scn} labelled disruptions in the forward year{scn_note} |

### Currency

All monetary fields are USD and suffixed `_usd`. `inventory_carrying_pct_annual`,
`stockout_cost_multiplier`, `cap_rate_pct` and the trade-term percentages are rates, not currency.
`storage_cost_per_unit_year_usd` is USD per unit per year.

"""
    doc += _policy_doc_section(st)
    doc += _price_elasticity_doc_section(st)
    doc += _forecast_doc_section(st)
    doc += _facility_cost_doc_section()
    doc += f"""
---

## Part {P} — Data profile

*Computed from this instance in the same run. This replaces the separate EDA report.*

"""
    doc += _eda
    doc += f"""
---

## Part {P + 1} — Known limitations

Read these before drawing a conclusion the data cannot support.

| Limitation | Consequence |
|---|---|
| **Synthetic data.** No real company's figures are in here. | The pipeline and the structure transfer; the specific numbers do not. Re-derive on live sell-through before committing to targets. |
| **Shelf life cannot bind** (§2.7). | Zero write-offs. Waste is not measurable, and newsvendor service levels pin near 100%. |
| **No special storage** (§2.3). | Holding-cost spread understates a real multi-temperature network, where refrigerated space costs roughly 2–3x ambient to build. |
| **Production capacity binds, but only lightly.** It clips about {_capclip*100:.1f}% of the volume plants ask for, and the unmet finished-goods cover feeds back into the following week's plan. | Capacity is a real constraint you can model, but it is not yet the dominant driver of lost sales. Tighten `plant_master.production_capacity_units_per_week` if you want it to dominate. |
| **Warehouse and lane capacity never bind.** | Storage and transport impose no physical trade-off; add those limits before claiming a network plan is feasible. |
| **Replenishment policy is static.** One safety stock, reorder point and order-up-to level per SKU-DC for all 156 weeks, fitted on the whole span. | The 2023 position already reflects 2025 demand. Refit on a rolling window before backtesting a policy. |
{regional_row}{warmup_row}
| **On-time delivery is roughly constant** across SKUs, because every node's promised lead time is set to its own 90th percentile. | Do not use OTD as a discriminating feature. A fixed contractual promise by transport mode would fix this. |
| **Order batching inflates apparent demand variability** at customer grain. | Measure variability and intermittency on aggregated DC-week or national-week series, not on the customer order calendar. |
{cdc_row}{bias_row}
{lanes_row}| **Ten B2B customers.** | Too few for meaningful customer segmentation. |
| **Elasticities are subcategory-level**, drawn from a small number of source groups. | Fine for portfolio work; do not read a single SKU's elasticity as an estimate. |
| **One actuals path.** The world's random seeds are fixed in code, so there is one realisation of history. The `--seed` option changes only the digital twin's lead-time and production draws, not demand. | For distributional questions about demand, change the world seeds in the generator and regenerate rather than treating this path as the mean. |

---

## Part {P + 2} — Reproducing and modifying

```bash
python supply_brain_generator_v5.py        # writes every table plus this document
```

Seeds are fixed in code, so a re-run is designed to regenerate the same realisation of the data. The generated
documents are not byte-identical between runs, because they embed the generation timestamp. Set
`SUPPLY_BRAIN_OUTPUT` to write elsewhere.

The assumptions in Part 2 map to named constants at the top of the generator, so any one of them
can be changed and the effect traced through:

| To change… | Edit |
|---|---|
| Shelf life (turn perishability back on) | `SHELF_LIFE_DAYS` |
| Portfolio size or category mix | `SKUS_PER_CATEGORY`, `CATEGORY_GROUP_MAP` |
| Brand price positions | `CATEGORY_BRANDS` |
| Seasonality by subcategory | `SUBCATEGORY_SEASONALITY_MONTHLY`, `MACRO_CALENDAR_MONTHLY` |
| Regional consumption | `REGION_DEMAND_INDEX` |
| Promotional intensity and depth | promo event count and depth draws in the order-generation step (not top-level constants) |
| Promotional vs base price response | `PROMO_MULT` |
| Promotional timing | `PROMO_CALENDAR_WEIGHT` |
| Customer trade terms | `CUSTOMER_TERMS` |
| Plant process lines and sourcing | `PRODUCTION_FAMILY`, `PLANT_CAPABILITY` |
| Warehouse cost basis and metro drivers | `AMBIENT_CAPEX_PER_SQFT`, `METRO`, `SIZE_FACTOR` |
| Product storage density | `UNITS_PER_SQFT` |
| Service-level tiers | `Z_BY_SEGMENT` and the segment target table |
| Forecast horizon and burn-in | `BURN_WEEKS`, `HIST_WEEKS`, `FWD_WEEKS` |
| Forecast uplift misestimation | `LIFT_MISEST` (local to the forecast step) |

Every generated table is checked at build time — referential integrity, no-gap coverage, and a set
of behavioural checks that flag when an assumption in Part 2 stops holding. A failed check is
printed and reported in the run's closing summary, but it does not stop the run or prevent the
tables and this document from being written. Read the validation output after every regeneration.

---

*End of document.*
"""
    with open(f"{OUT}/DATASET_DOCUMENTATION.md", "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"  wrote DATASET_DOCUMENTATION.md ({doc.count(chr(10)):,} lines, EDA merged in as Part {P})")


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

def generate_eda(OUT, as_section=False, stats=None):
    """Data profile. Standalone it writes EDA_REPORT.md with "2.x" sections; with
    as_section=True it returns the body renumbered as Part _DOC_PROFILE_PART of
    DATASET_DOCUMENTATION.md. If `stats` is a dict, figures other parts of the
    document quote (the category elasticity recovery) are stored in it."""
    R=lambda n: pd.read_csv(f"{OUT}/{n}.csv")
    _sec = str(_DOC_PROFILE_PART) if as_section else "2"   # cross-reference prefix
    sku=R("sku_master"); orders=R("orders"); fc=R("demand_forecast"); prod=R("production")
    inv=R("inventory"); lanes=R("network_lanes"); ban=R("b2b_customer_master"); serve=R("customer_serving_dc")
    costp=R("cost_parameters"); plants=R("plant_master"); pdc=R("plant_dc_master")
    rdc=R("regional_dc_master"); cdc=R("customer_dc_master"); promos=R("promotions")
    orders["_od"]=pd.to_datetime(orders.order_date,errors="coerce")
    ordw=orders.groupby(["sku_id","dc_id","b2b_customer_id","period"],as_index=False).order_qty.sum()
    sku_rev=orders.groupby("sku_id").revenue_usd.sum()
    unit_cost=sku.set_index("sku_id").unit_cost_usd
    m=[]; A=m.append

    _o25=orders[orders.period.str[:4]=="2025"]
    rev=orders.revenue_usd.sum(); cogs=(orders.order_qty*orders.sku_id.map(unit_cost)).sum()
    A("# Supply Brain - Comprehensive Dataset and Network EDA\n")
    A(f"*Auto-generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}. Single-file build: this report is produced in "
      "the same run that generates the data, so it always matches the current instance.*\n")
    A("## Executive snapshot\n")
    A(_kv([("SKUs",f"{sku.sku_id.nunique():,}"),
           ("Network",f"{len(plants)} plants, {len(pdc)} plant DCs, {len(rdc)} regional DCs, {len(cdc)} customer DCs, {len(lanes)} lanes"),
           ("B2B customers (banners)",len(ban)),
           ("Order lines (day-level, full 2023-2025 span)",f"{len(orders):,}"),
           ("Distinct orders (baskets)",f"{orders.order_id.nunique():,}"),
           ("Total demand (units, full span)",f"{orders.order_qty.sum():,}"),
           ("Total demand (units, 2025 only)",f"{_o25.order_qty.sum():,}"),
           ("Total revenue (full span)",f"${rev:,.0f}"),
           ("Total revenue (2025 only)",f"${_o25.revenue_usd.sum():,.0f}"),
           ("Gross margin",f"${rev-cogs:,.0f} ({(rev-cogs)/rev*100:.1f} pct)"),
           ("Forecast horizon",f"{fc.period.nunique()} weeks (2025 in-sample + 2026 forward)")])+"\n")

    _sps=R("sku_plant_sourcing"); smap=R("sku_sourcing_map")
    _dual_share=float((_sps.groupby("sku_id").plant_id.nunique()>=2).mean())
    _used=set(zip(smap.origin_node_id,smap.dc_id))
    _lane_used=np.array([(o,d) in _used for o,d in zip(lanes.origin_node_id,lanes.destination_node_id)])
    _idle_lanes=int((~_lane_used).sum())
    A("\n---\n\n# Part 1 - Parameters and Assumptions\n")
    A("Synthetic CPG Food and Beverage supply chain, USD, grounded in an external real F&B sales dataset "
      "(per-unit prices, per-group elasticity, 2020-shape seasonality, ~80/20 concentration; 2020 COVID spike "
      "excluded). 5 categories x 100 SKUs, segmented ~50/30/20 Fast-Moving/Seasonal/Intermediate. Two price "
      "elasticities per SKU (base shelf-price and promotional). Demand is day-level orders on a US network "
      f"(Plant -> Plant DC -> Regional DC -> Customer DC); about {_frac_words(_dual_share)} of SKUs are dual-sourced across plants"
      + (", and failover RDC-to-CDC and lateral RDC-to-RDC lanes are declared but carry no simulated flow. "
         if _idle_lanes else ". ")
      + "Supply-side parameters (cost/margin, holding, lead time, capacities) are modelled assumptions; demand-side is grounded.\n")

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
    if stats is not None:
        stats["elasticity"]=er          # filled below; quoted in Part 6 of the main document
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
    _acc_seg=acc("segment"); _acc_cat=acc("category")
    A("**By segment:**\n"+_tab(_acc_seg)+"\n")
    A("\n**By category:**\n"+_tab(_acc_cat)+"\n")
    # YoY compares the forward year with the LAST in-sample year, not the whole multi-year history
    _ly=period_label(ACTUAL_WEEKS)[:4]; _fy=period_label(FWD_START_WEEK)[:4]
    histu=orders.loc[orders.period.str[:4]==_ly,"order_qty"].sum(); fwdu=fc[fc.horizon=="forward"].forecast_qty.sum()
    A(f"\nForward {_fy} total forecast units {fwdu:,} vs {_ly} actual {histu:,}, YoY {(fwdu/histu-1)*100:+.1f} pct.\n")
    _ins_all=fc[fc.horizon=="in_sample"]
    _bias_full=(_ins_all.forecast_qty.sum()-_ins_all.actual_qty.sum())/_ins_all.actual_qty.sum()*100
    _bias_nz=(ins.forecast_qty.sum()-ins.actual_qty.sum())/ins.actual_qty.sum()*100
    _gb=pd.concat([_acc_seg.bias_pct,_acc_cat.bias_pct])
    _bias_sign="negative" if (_gb<0).all() else ("positive" if (_gb>0).all() else "")
    if _bias_sign:
        A(f"Bias is {_bias_sign} in every segment and category above: the forecast "
          f"{'under' if _bias_sign=='negative' else 'over'}-calls demand throughout the evaluated year "
          f"({_bias_nz:+.1f} pct on these non-zero-actual rows; {_bias_full:+.1f} pct over the full panel, zero weeks included).\n")
    else:
        A(f"Bias varies in sign across segments and categories ({_bias_nz:+.1f} pct on these non-zero-actual rows; "
          f"{_bias_full:+.1f} pct over the full panel, zero weeks included).\n")
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
    # Turns were `inv.shipments.sum() / avg_units` = 64.8x, wrong twice over: it treated 156
    # weeks of shipments as a single year, and it summed shipments across all three echelons so
    # every unit counted about 2.2 times. The correct basis is one year of COGS on goods actually
    # sold to a CUSTOMER, over the average value of stock held anywhere in the network.
    _yrs=ACTUAL_WEEKS/52.0
    _cogs_yr=float(((invc.demand_customer_qty-invc.unmet_customer_qty)*invc.unit_cost_usd).sum()/_yrs)
    turns=_cogs_yr/wk_val.mean() if wk_val.mean()>0 else float("nan")
    avail=(1-(inv.closing_stock==0).mean())*100
    _cfill=(1-inv.unmet_customer_qty.sum()/max(inv.demand_customer_qty.sum(),1))*100
    A(f"\nNetwork-wide: avg inventory value ~ **${wk_val.mean():,.0f}** (working capital) against "
      f"~**${_cogs_yr:,.0f}** of annual COGS, so ~ **{turns:.1f}x** turns -- the same unit is held "
      f"at up to three echelons, so network turns sit well below the per-echelon figure above.\n\n"
      f"Availability: **{_cfill:.1f} pct** customer-facing unit fill rate. On a raw DC-SKU-week "
      f"basis {avail:.1f} pct of cells hold stock, but that mixes customer service with internal "
      f"replenishment -- use `unmet_customer_qty` for a service number.\n")
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
    _cdc_par=smap[smap.dc_id.astype(str).str.startswith("CDC")].groupby("dc_id").origin_node_id.nunique()
    _cdc_single=bool((_cdc_par<=1).all())
    _fail_idle=int(((lanes.destination_node_type=="CUSTOMER_DC").to_numpy()&~_lane_used).sum())
    _lat_idle=int((((lanes.origin_node_type=="REGIONAL_DC")&(lanes.destination_node_type=="REGIONAL_DC")).to_numpy()&~_lane_used).sum())
    A(f"\nDual-sourcing: {int((cdc_src>=2).sum())}/{len(cdc)} Customer DCs have >=2 inbound Regional DCs (failover)"
      f"{' on paper' if _cdc_single else ''}; lateral RDC-to-RDC lanes: {n_lat}."
      + (f" In the simulated history every Customer DC is replenished from a single Regional DC, so the "
         f"{_fail_idle} failover lanes and the {_lat_idle} lateral lanes carry no flow "
         f"({_idle_lanes} of {len(lanes)} lanes unused)." if _cdc_single else
         f" {_idle_lanes} of {len(lanes)} lanes carry no simulated flow.")
      + "\n")
    lm=lanes.groupby(["origin_node_type","destination_node_type","transport_mode"]).agg(
        lanes=("lane_id","count"),avg_lead_d=("lead_time_mean_days","mean"),avg_mi=("distance_miles","mean"),
        avg_cost=("logistics_cost_per_unit_usd","mean")).round(2).reset_index()
    A("\n**Lanes by tier and mode:**\n"+_tab(lm)+"\n")

    # 2.13 capacity utilization
    A("## 2.13 Capacity utilization\n")
    # This averaged closing_stock over SKU-DC-WEEK rows, i.e. the stock of a single SKU, and so
    # reported warehouse utilization as "0.0 pct" -- out by roughly the SKU count. It also left
    # the Plant DCs out of the map entirely, and those are the largest holders in the network.
    capmap=pd.concat([pdc.rename(columns={"plant_dc_id":"dc_id"})[["dc_id","storage_capacity_units"]],
                      rdc.rename(columns={"regional_dc_id":"dc_id"})[["dc_id","storage_capacity_units"]],
                      cdc.rename(columns={"customer_dc_id":"dc_id"})[["dc_id","storage_capacity_units"]]])
    du=(inv.groupby(["dc_id","period"]).closing_stock.sum()      # DC total, then average the weeks
        .groupby("dc_id").mean().reset_index().merge(capmap,on="dc_id"))
    du["util_pct"]=(du.closing_stock/du.storage_capacity_units*100).round(1)
    A(f"DC storage utilization (whole-site on-hand / capacity, averaged over weeks): "
      f"mean {du.util_pct.mean():.1f} pct, max {du.util_pct.max():.1f} pct. Storage is not a "
      f"binding constraint in this dataset.\n")
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
    A("## 2.14 Financial summary (full 2023-2025 actuals span)\n")
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
        _cls=", ".join(t.replace("leadtime","lead-time").replace("_"," ") for t in sorted(scn.scenario_type.unique()))
        _sd=(pd.to_datetime(scn.end_date)-pd.to_datetime(scn.start_date)).dt.days+1
        _scn_note=(" `end_date` runs one week past each event's last week; use `start_date` plus "
                   "`duration_weeks` for the window." if (_sd==7*(scn.duration_weeks+1)).all() else "")
        A(f"\nTotal scenarios: {len(scn)} across {scn.scenario_type.nunique()} classes ({_cls}).{_scn_note}\n")
    else:
        A("*scenarios.csv not present.*\n")

    # 2.16 key findings
    A("## 2.16 Key findings\n")
    top_cat=bc.sort_values("revenue",ascending=False).iloc[0]
    most_el=min(er,key=lambda x:x[1]); least_el=max(er,key=lambda x:x[1])
    _s=_acc_seg.set_index("segment")
    _bw=_s.WAPE_pct.idxmin(); _ww=_s.WAPE_pct.idxmax(); _wm=_s.MAPE_pct.idxmax()
    _worst=(f"{_ww} SKUs are worst on both WAPE ({_s.WAPE_pct[_ww]} pct) and MAPE ({_s.MAPE_pct[_ww]} pct)" if _ww==_wm else
            f"{_ww} SKUs are worst on WAPE ({_s.WAPE_pct[_ww]} pct) and {_wm} on MAPE ({_s.MAPE_pct[_wm]} pct)")
    _bias_clause=(f", and bias is {_bias_sign} everywhere" if _bias_sign else "")
    _fc_finding=(f"Forecast accuracy is best for {_bw} SKUs (WAPE {_s.WAPE_pct[_bw]} pct); {_worst}{_bias_clause} "
                 f"(see {_sec}.9); the forward year grows ~{(fwdu/histu-1)*100:+.0f} pct over {_ly} actuals.")
    _t3=inv.dc_id.astype(str).str[:3]
    _ech_fill={t:(1-inv.loc[_t3==t,"unmet_customer_qty"].sum()/inv.loc[_t3==t,"demand_customer_qty"].sum())*100
               for t in ("RDC","CDC") if inv.loc[_t3==t,"demand_customer_qty"].sum()>0}
    findings=[
      f"Revenue is led by {top_cat['category']} ({top_cat['rev_pct']} pct of sales); demand is steeply concentrated (top 20 pct SKUs ~ {top20:.0f} pct of revenue).",
      f"Most price-elastic category (base): {most_el[0]} ({most_el[1]}); least: {least_el[0]} ({least_el[1]}). Promo elasticity exceeds base in {sum(1 for e in er if e[2]<=e[1])}/{len(er)} categories.",
      _fc_finding,
      (f"Every Customer DC has a declared failover lane ({int((cdc_src>=2).sum())}/{len(cdc)}) and there are {n_lat} lateral lanes, but in the simulated history each Customer DC is single-sourced and failover and lateral lanes carry no flow; whole-site storage utilization averages {du.util_pct.mean():.0f} pct."
       if _cdc_single else
       f"{int((cdc_src>=2).sum())}/{len(cdc)} Customer DCs have a declared failover lane and there are {n_lat} lateral lanes; {_idle_lanes} of {len(lanes)} lanes carry no simulated flow; whole-site storage utilization averages {du.util_pct.mean():.0f} pct."),
      f"Customer-facing unit fill rate ~ {_cfill:.1f} pct (Regional DCs {_ech_fill.get('RDC', float('nan')):.1f} pct, Customer DCs {_ech_fill.get('CDC', float('nan')):.1f} pct) at ${wk_val.mean():,.0f} avg working capital and {turns:.1f}x annual turns; {100-avail:.0f} pct of DC-SKU-weeks close at zero.",
      f"No-gap coverage: every Customer DC has demand, inventory is modelled at all echelons (Plant DC, Regional DC, Customer DC), and a {len(pd.read_csv(f'{OUT}/scenarios.csv')) if os.path.exists(f'{OUT}/scenarios.csv') else 0}-scenario disruption library covers demand- and supply-side stresses.",
    ]
    A("\n".join(f"- {x}" for x in findings)+"\n")
    A("\n---\n*End of report.*\n")

    body = "\n".join(m) + "\n"
    if as_section:
        # Folded into DATASET_DOCUMENTATION.md as Part _DOC_PROFILE_PART: drop the standalone
        # title, timestamp and footer, renumber "2.x" to "<part>.x", and demote the headings so
        # they nest under the parent part (the standalone "# Part 1/2" banners become plain
        # subsection headings rather than clashing with the main document's own Parts).
        body = re.sub(r"^# Supply Brain - Comprehensive.*?\n", "", body, flags=re.M)
        body = re.sub(r"^\*Auto-generated.*?\n", "", body, flags=re.M)
        body = body.replace("\n---\n*End of report.*\n", "")
        body = re.sub(r"^# Part 1 - Parameters and Assumptions", "### Parameters and assumptions (profile summary)", body, flags=re.M)
        body = re.sub(r"^# Part 2 - Exploratory Data Analysis", "### Exploratory data analysis", body, flags=re.M)
        body = re.sub(r"^## 2\.(\d+) ", rf"### {_DOC_PROFILE_PART}.\1 ", body, flags=re.M)
        body = re.sub(r"^## Executive snapshot", f"### {_DOC_PROFILE_PART}.0 Data snapshot", body, flags=re.M)
        body = re.sub(r"^## ", "### ", body, flags=re.M)
        return body
    with open(f"{OUT}/EDA_REPORT.md", "w", encoding="utf-8") as f:
        f.write(body)
    print(f"  EDA report written: {OUT}/EDA_REPORT.md")



# ---------------------------------------------------------------------------
# Baseline policy
# ---------------------------------------------------------------------------
# These are the tuning knobs. When the twin misses its target operating
# profile, this is what moves — not the physics. Values below were fitted
# against the acceptance profile in TARGET_PROFILE.

# Service factor by SKU segment. This ladder is the differentiated-service
# policy: fast movers get the most protection, intermittent SKUs the least.
# Realised fill lands well below the nominal service level these imply,
# because the twin also loses service to upstream shortfall and to within-week
# timing that a single-node formula cannot see.
Z_BY_SEGMENT = {"Fast-Moving": 2.05, "Seasonal": 1.64, "Intermediate": 1.28}
# A parent absorbs its children's order variability on top of its own demand,
# so sigma is uplifted at the upstream echelons.
SIGMA_UPLIFT = {"PDC": 1.45, "RDC": 1.25, "CDC": 1.00}
REVIEW_WEEKS = 1.0
PDC_COVER_WEEKS = 4.5          # plant DCs are pushed; S is a holding ceiling
MIN_S_MULTIPLE = 1.2           # S must exceed the reorder point by this much

# Order batch, in weeks of demand: S = s + mu * ORDER_COVER_WEEKS.
# Setting this to the review period alone (1 week) makes every replenishment
# a hand-to-mouth top-up, which collapses cycle stock — the network then holds
# only safety stock and fill rate falls into the 70s. Shipping roughly three
# weeks at a time is what the observed receipt cadence implies: 26% of
# node-SKU-weeks see a receipt, i.e. an order about every four weeks.
#
# Tuned to 1.5 alongside the protection-interval fix: once s correctly covers
# review + lead time, a 3-week batch on top pushes network inventory past the
# acceptance ceiling without buying any more service.
ORDER_COVER_WEEKS = 1.5


def derive_baseline_policy(data_dir: Path,
                           basis: str = "history") -> pd.DataFrame:
    """Compute (safety_stock, reorder_point, order_up_to_level) per node-SKU.

    `basis` selects the demand the policy is sized against:

      "history"  2025 in-sample actuals. Used for the 2023-25 regeneration.
      "forward"  the 2026 forecast, with sigma scaled to the new demand level.
                 Used for the operating policy carried into 2026.

    Sizing the 2026 policy on 2025 actuals is a mis-sizing, not a conservative
    choice: the forecast is 8.8% higher overall and 15.3% higher for Frozen
    Foods, and fill tracks that growth almost rank-for-rank by category.
    Re-basing costs almost nothing in extra safety stock (+$0.11M) because it
    corrects where the buffer sits rather than adding more, and it lifted
    customer fill 5.3pp while cutting landed cost $2.5M.

    Classic (R,s,S) sizing under demand *and* lead-time uncertainty, over the
    PROTECTION INTERVAL P = R + L:

        SS  = z * sqrt(P_wk * sigma_d^2 + mu_d^2 * sigma_LT_wk^2)
        s   = mu_d * P_wk + SS
        S   = s + mu_d * ORDER_COVER_WEEKS

    Under periodic review the node is exposed for the whole review period
    *plus* the lead time: once inventory drops below s it cannot reorder until
    the next review, and then must still wait out transit. Sizing on L alone
    undersizes the trigger badly wherever the review dominates the lead time —
    at the customer DCs, L is about 0.4 weeks against a 1-week review, so
    protection is understated by roughly 70% and the node runs dry between
    reviews no matter how much stock sits upstream.

    z is set per SKU segment, and sigma is uplifted at the upstream echelons
    because a parent absorbs its children's order variability on top of its
    own demand.

    Demand statistics come from the in-sample window. Lead times come from
    the lane the SKU actually travels, per sku_sourcing_map.csv — the same
    routing the twin will execute, so the policy is sized against the
    network it actually runs on.
    """
    sku = pd.read_csv(data_dir / "sku_master.csv")
    lanes = pd.read_csv(data_dir / "network_lanes.csv")
    dfc = pd.read_csv(data_dir / "demand_forecast.csv")
    smap = pd.read_csv(data_dir / "sku_sourcing_map.csv")
    plant_dc = pd.read_csv(data_dir / "plant_dc_master.csv")

    seg = sku.set_index("sku_id").segment.to_dict()
    fam = sku.set_index("sku_id").production_family.to_dict()

    # --- demand stats at RDC / CDC ----------------------------------------
    ins = (dfc[dfc.horizon == "in_sample"]
           .groupby(["period", "dc_id", "sku_id"], as_index=False)
           .actual_qty.sum())
    hist = (ins.groupby(["dc_id", "sku_id"])
            .actual_qty.agg(mu="mean", sd="std").reset_index().fillna(0.0))

    if basis == "history":
        # Sized on the FULL order history, not just the in-sample year. A pair
        # that sold in the early years and went quiet later still has real
        # demand in the record; sizing off the in-sample window alone dropped
        # 136 such pairs from the policy, which made their demand invisible in
        # the inventory tables and broke reconciliation against orders.csv.
        oh = pd.read_csv(data_dir / "orders.csv",
                         usecols=["period", "dc_id", "sku_id", "order_qty"])
        wk = (oh.groupby(["period", "dc_id", "sku_id"], as_index=False)
              .order_qty.sum())
        n_weeks = wk.period.nunique()
        agg = wk.groupby(["dc_id", "sku_id"]).order_qty.agg(["sum", "std"])
        stats = pd.DataFrame({
            "dc_id": [i[0] for i in agg.index],
            "sku_id": [i[1] for i in agg.index],
            "mu": (agg["sum"] / n_weeks).to_numpy(),
            "sd": agg["std"].fillna(0.0).to_numpy(),
        })
    else:
        fwd = (dfc[dfc.horizon == "forward"]
               .groupby(["period", "dc_id", "sku_id"], as_index=False)
               .forecast_qty.sum()
               .groupby(["dc_id", "sku_id"]).forecast_qty.mean()
               .rename("fm").reset_index())
        m = fwd.merge(hist, on=["dc_id", "sku_id"], how="left").fillna(0.0)
        # scale sigma with the demand level: a SKU forecast 15% higher is
        # assumed 15% more variable in absolute terms, holding CV constant
        scale = np.where(m.mu > 0, m.fm / m.mu.replace(0, np.nan), 1.0)
        stats = pd.DataFrame({"dc_id": m.dc_id, "sku_id": m.sku_id,
                              "mu": m.fm,
                              "sd": m.sd * np.nan_to_num(scale, nan=1.0)})
        stats = stats.fillna(0.0)

    # --- sourcing: the lane each (dc, sku) actually travels ----------------
    parent = smap.set_index(["dc_id", "sku_id"]).origin_node_id.to_dict()
    min_lane = (lanes.sort_values("lead_time_mean_days")
                .drop_duplicates("destination_node_id")
                .set_index("destination_node_id").origin_node_id.to_dict())
    lane_lt = {(r.origin_node_id, r.destination_node_id):
               (r.lead_time_mean_days, r.lead_time_std_days)
               for r in lanes.itertuples(index=False)}

    def lane_for(dc, s):
        return parent.get((dc, s)) or min_lane.get(dc)

    rows = []
    for r in stats.itertuples(index=False):
        dc, s, mu, sd = r.dc_id, r.sku_id, float(r.mu), float(r.sd)
        if mu <= 0 and sd <= 0:
            continue
        o = lane_for(dc, s)
        lt_m, lt_s = lane_lt.get((o, dc), (3.0, 1.0))
        lt_wk, lts_wk = lt_m / 7.0, lt_s / 7.0
        prot_wk = REVIEW_WEEKS + lt_wk          # protection interval P = R + L
        z = Z_BY_SEGMENT.get(seg.get(s, "Intermediate"), 1.28)
        sigma = sd * SIGMA_UPLIFT[dc[:3]]
        ss = z * math.sqrt(prot_wk * sigma ** 2 + mu ** 2 * lts_wk ** 2)
        rop = mu * prot_wk + ss
        S = max(rop + mu * max(REVIEW_WEEKS, ORDER_COVER_WEEKS),
                rop * MIN_S_MULTIPLE)
        rows.append((dc, s, round(ss), round(rop), round(S)))

    pol = pd.DataFrame(rows, columns=[
        "dc_id", "sku_id", "safety_stock", "reorder_point",
        "order_up_to_level"])

    # --- plant DCs: pushed, not pulled -------------------------------------
    # A PDC's S is a holding ceiling sized on the throughput it must pass to
    # its RDCs, not a pull trigger. Roll up the demand of every RDC that
    # sources each SKU from that PDC.
    pdc_rows = []
    rdc = pol[pol.dc_id.str.startswith("RDC")]
    thru = {}
    for r in rdc.itertuples(index=False):
        o = lane_for(r.dc_id, r.sku_id)
        if o and o.startswith("PDC"):
            m = stats[(stats.dc_id == r.dc_id) & (stats.sku_id == r.sku_id)]
            thru[(o, r.sku_id)] = thru.get((o, r.sku_id), 0.0) + (
                float(m.mu.iloc[0]) if len(m) else 0.0)
    for (pdc, s), mu in thru.items():
        if mu <= 0:
            continue
        lt_m, lt_s = lane_lt.get((_plant_of(pdc, plant_dc), pdc), (0.5, 0.07))
        z = Z_BY_SEGMENT.get(seg.get(s, "Intermediate"), 1.28)
        ss = z * math.sqrt(lt_m / 7.0) * mu * SIGMA_UPLIFT["PDC"] * 0.5
        S = mu * PDC_COVER_WEEKS + ss
        pdc_rows.append((pdc, s, round(ss), round(ss + mu * lt_m / 7.0),
                         round(S)))
    pol = pd.concat([pol, pd.DataFrame(pdc_rows, columns=pol.columns)],
                    ignore_index=True)

    pol["production_family"] = pol.sku_id.map(fam)
    pol["segment"] = pol.sku_id.map(seg)
    pol["z"] = pol.segment.map(Z_BY_SEGMENT).fillna(1.28)
    return pol.sort_values(["dc_id", "sku_id"]).reset_index(drop=True)


def _plant_of(pdc_id, plant_dc):
    m = plant_dc[plant_dc.plant_dc_id == pdc_id]
    return m.plant_id.iloc[0] if len(m) else None


# ---------------------------------------------------------------------------
# Opening state for the forward year
# ---------------------------------------------------------------------------

def build_opening_position(inv: pd.DataFrame, rcpt: pd.DataFrame,
                           pol: pd.DataFrame) -> pd.DataFrame:
    """The position the network ends history on, which is the position the
    forward horizon opens from.

    Deriving it from the twin's own terminal state rather than a separate code
    path means the opening stock a forward scenario starts with is exactly the
    stock history left behind — there is no seam for the two to drift across.
    """
    last = inv.period.max()
    end = (inv[inv.period == last]
           [["dc_id", "sku_id", "closing_stock"]]
           .rename(columns={"closing_stock": "opening_stock"}))

    w0 = week_start(FWD_START_WEEK)
    horizon_end = ACTUAL_WEEKS * 7
    it = (rcpt[rcpt.arrival_day >= horizon_end]
          .groupby(["dc_id", "sku_id"], as_index=False)
          .agg(in_transit_qty=("qty_received", "sum"),
               eta=("arrival_day", "min")))
    it["in_transit_eta_week_start"] = [
        (WEEK1_MONDAY + pd.Timedelta(days=int(d)))
        .strftime("%Y-%m-%d") for d in it.eta]

    lt = inv.drop_duplicates(["dc_id", "sku_id"])[
        ["dc_id", "sku_id", "lead_time_mean_days", "lead_time_std_days",
         "cum_lead_time_mean_days", "cum_lead_time_std_days"]]

    # Driven off the terminal inventory, NOT off the forward policy: a pair the
    # forward policy does not cover (no forecast demand) still holds real stock
    # at the cut-over, and joining the other way round would silently destroy
    # it. Such pairs simply open with a zero policy and never reorder.
    out = (end
           .merge(it[["dc_id", "sku_id", "in_transit_qty",
                      "in_transit_eta_week_start"]],
                  on=["dc_id", "sku_id"], how="left")
           .merge(lt, on=["dc_id", "sku_id"], how="left")
           .merge(pol[["dc_id", "sku_id", "safety_stock", "reorder_point",
                       "order_up_to_level"]],
                  on=["dc_id", "sku_id"], how="left"))
    for c in ("safety_stock", "reorder_point", "order_up_to_level"):
        out[c] = out[c].fillna(0)
    out["opening_stock"] = out.opening_stock.fillna(0).astype(int)
    out["in_transit_qty"] = out.in_transit_qty.fillna(0).astype(int)
    out["in_transit_eta_week_start"] = out.in_transit_eta_week_start.fillna("")
    out["period"] = period_label(FWD_START_WEEK)
    out["week_start_date"] = w0
    out["inventory_position"] = out.opening_stock + out.in_transit_qty
    return out[["period", "week_start_date", "sku_id", "dc_id",
                "opening_stock", "safety_stock", "reorder_point",
                "order_up_to_level", "lead_time_mean_days",
                "lead_time_std_days", "cum_lead_time_mean_days",
                "cum_lead_time_std_days", "in_transit_qty",
                "in_transit_eta_week_start", "inventory_position"]]


TARGET_PROFILE = {
    "customer_fill_rate": (0.90, 0.96),
    "internal_replen_fill_rate": (0.85, 0.96),
    "avg_units_on_hand_network": (8.0e5, 1.3e6),
    "receipt_on_time_rate": (0.85, 0.95),
    "pct_receipts_short": (0.10, 0.30),
    "pct_skuweeks_capacity_limited": (0.02, 0.15),
}


def acceptance(kpi: dict, rcpt: pd.DataFrame, prod: pd.DataFrame) -> pd.DataFrame:
    obs = {
        "customer_fill_rate": kpi["customer_fill_rate"],
        "internal_replen_fill_rate": kpi["internal_replen_fill_rate"],
        "avg_units_on_hand_network": kpi["avg_units_on_hand_network"],
        "receipt_on_time_rate": float(rcpt.on_time_flag.mean()),
        "pct_receipts_short": float((rcpt.short_qty > 0).mean()),
        "pct_skuweeks_capacity_limited": float(
            prod.capacity_limited_flag.mean()) if len(prod) else 0.0,
    }
    rows = []
    for k, (lo, hi) in TARGET_PROFILE.items():
        v = obs[k]
        rows.append({"metric": k, "observed": round(v, 4), "lo": lo, "hi": hi,
                     "pass": bool(lo <= v <= hi)})
    return pd.DataFrame(rows)



# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# Forward-looking artefacts. These describe the planning horizon, so they
# belong with the run that produced them, never in input datasets/.
FORWARD_ARTEFACTS = ["forward_policy.csv", "opening_position.csv"]

# The historical record. The twin produces these, but they are the past, so
# they are part of the brief and live in input datasets/.
HISTORY_TABLES = ["inventory.csv", "replenishment_receipts.csv",
                  "production.csv", "write_offs.csv"]


def ramp_capability(data_dir: Path, uplift: float) -> int:
    """Scale plant and family line capacity in the world tables.

    Capacity is sized off the p97 of realised demand with a little headroom,
    which leaves it binding as soon as demand grows: the forward year hit a
    capacity limit in 11.3% of SKU-weeks against 6.3% in history. It was
    concentrated rather than general -- one plant ran at 96.5% mean utilisation
    and exceeded its cap in 21% of weeks, and one family line was chronically
    undersized at 171% of its cap.

    +15% across every plant and line removes the constraint almost entirely
    (capacity-limited SKU-weeks 11.3% -> 0.6%) and pays for itself, because the
    lost margin it recovers exceeds the holding cost of the extra throughput.
    +30% buys a further 0.15pp of fill and is not worth it: past roughly +15%
    capacity stops being the binding constraint and the limiter moves to how
    buffer is positioned between the RDC and CDC echelons.

    Applied while the world is built, so the ramped number is the only capacity
    the project ever sees and there is no pre-ramp copy to drift from.
    """
    pm = pd.read_csv(data_dir / "plant_master.csv")
    pc = pd.read_csv(data_dir / "plant_capability.csv")
    pm["production_capacity_units_per_week"] = np.ceil(
        pm.production_capacity_units_per_week * uplift).astype(int)
    pc["family_capacity_units_per_week"] = np.ceil(
        pc.family_capacity_units_per_week * uplift).astype(int)
    pm.to_csv(data_dir / "plant_master.csv", index=False)
    pc.to_csv(data_dir / "plant_capability.csv", index=False)
    return int(pm.production_capacity_units_per_week.sum())


CAPACITY_UPLIFT = 1.15


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["world", "history", "all"],
                    default="all")
    ap.add_argument("--run-name", default="history")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--skip-docs", action="store_true",
                    help="skip validation, documentation and EDA")
    args = ap.parse_args()

    t0 = time.time()
    DATA = Path(OUTPUT_DIR)
    OUTROOT = Path(BASE_DIR) / "output datasets"
    twin_path = Path(BASE_DIR) / "models" / "twin" / "digital_twin.py"
    if not twin_path.exists():
        raise FileNotFoundError(
            f"Digital twin not found at expected path: {twin_path}. "
            "Check that the project layout still matches models/twin/digital_twin.py."
        )
    spec = importlib.util.spec_from_file_location("supply_brain_digital_twin", twin_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load digital twin module from {twin_path}")
    tw = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = tw
    spec.loader.exec_module(tw)

    print("Supply Brain — dataset generator")
    print(f"  horizon:        {period_label(1)}..{period_label(ACTUAL_WEEKS)} "
          f"actual + {period_label(FWD_START_WEEK)}..{period_label(TOTAL_WEEKS)} forward")
    print(f"  physics engine: models/twin/digital_twin.py")
    print(f"  margin basis:   {tw.MARGIN_COLUMN}")

    # ---- the world ---------------------------------------------------------
    if args.stage in ("world", "all"):
        print("\n=== STAGE 1: build the world -> input datasets/ ===")
        DATA.mkdir(parents=True, exist_ok=True)
        ctx = step1_masters()
        step2_orders(ctx)
        step3_forecast_and_routing(ctx)
        cap = ramp_capability(DATA, CAPACITY_UPLIFT)
        print(f"  capability ramp x{CAPACITY_UPLIFT}: "
              f"{cap:,} units/week across {len(YEARS) and 4} plants")
        for name in HISTORY_TABLES + FORWARD_ARTEFACTS:
            f = DATA / name
            if f.exists():
                f.unlink()
        print(f"  {len(list(DATA.glob('*.csv')))} world tables")
        if args.stage == "world":
            print(f"\nTotal runtime: {time.time() - t0:.0f}s")
            return

    out = OUTROOT / args.run_name
    out.mkdir(parents=True, exist_ok=True)

    # ---- policy ------------------------------------------------------------
    print("\n=== STAGE 2: derive policy ===")
    pol = derive_baseline_policy(DATA, basis="history")
    pol.to_csv(DATA / "baseline_policy.csv", index=False)
    print(f"  baseline_policy.csv -> input   {len(pol):>7,} rows  "
          f"(ran the past, so it is part of the record)")
    fwd_pol = derive_baseline_policy(DATA, basis="forward")
    fwd_pol.to_csv(out / "forward_policy.csv", index=False)
    print(f"  forward_policy.csv  -> output  {len(fwd_pol):>7,} rows  "
          f"safety stock {fwd_pol.safety_stock.sum():,.0f} u "
          f"(history basis {pol.safety_stock.sum():,.0f} u)")

    # ---- history through the twin -----------------------------------------
    print(f"\n=== STAGE 3: simulate {period_label(1)}.."
          f"{period_label(ACTUAL_WEEKS)} ===")
    inp, notes, twin, inv, rcpt, prod = tw.run_twin(
        DATA, mode="history", seed=args.seed,
        policy_csv=DATA / "baseline_policy.csv")

    # shelf life exceeds the simulated span by design, so nothing can expire
    wo = pd.DataFrame(columns=["period", "week_start_date", "sku_id", "dc_id",
                               "units_written_off", "write_off_cost_usd",
                               "reason"])
    opening = build_opening_position(inv, rcpt, fwd_pol)

    # ---- emit: the past to input, the forward view to output ---------------
    print("\n=== STAGE 4: emit ===")
    for name, df in {"inventory.csv": inv,
                     "replenishment_receipts.csv": rcpt,
                     "production.csv": prod,
                     "write_offs.csv": wo}.items():
        df.to_csv(DATA / name, index=False)
        print(f"  {name:<30} -> input   {len(df):>9,} rows")
    opening.to_csv(out / "opening_position.csv", index=False)
    print(f"  {'opening_position.csv':<30} -> output  {len(opening):>9,} rows"
          f"  ({opening.opening_stock.sum():,} u on hand, "
          f"{opening.in_transit_qty.sum():,} u in transit)")

    args.mode, args.policy, args.label = (
        "history", str(DATA / "baseline_policy.csv"),
        f"{period_label(1)}..{period_label(ACTUAL_WEEKS)} history")
    kpi, svc_node, costs = tw.write_run(out, inv, rcpt, prod, twin, inp,
                                        notes, args, t0)
    for n in ("inventory.csv", "replenishment_receipts.csv",
              "production.csv", "policy.csv"):
        f = out / n
        if f.exists():
            f.unlink()          # the record lives in input; do not duplicate

    # ---- acceptance --------------------------------------------------------
    print("\n=== STAGE 5: acceptance against the target operating profile ===")
    acc = acceptance(kpi, rcpt, prod)
    acc.to_csv(out / "acceptance.csv", index=False)
    print(acc.to_string(index=False))
    print(f"\n  {int(acc['pass'].sum())}/{len(acc)} metrics inside target band")

    # ---- disruption library ------------------------------------------------
    # Scenario magnitudes are calibrated against realised days of supply, so
    # this runs once the inventory record exists. The library itself is a
    # given -- a stress catalogue to test plans against -- so it is an input.
    print(chr(10) + "=== STAGE 6: disruption scenario library ===")
    generate_scenarios(str(DATA))

    # ---- validate + document ----------------------------------------------
    if not args.skip_docs:
        print("\n=== STAGE 7: validate + document the complete dataset ===")
        ok = validate(opening_path=out / "opening_position.csv")
        write_documentation()
        write_schema_doc()
        # the twin documents the other half: its own mechanics and the
        # artefacts this run just wrote into output datasets/
        _n = tw.write_output_documentation(OUTROOT)
        print(f"  wrote output datasets/OUTPUT_DOCUMENTATION.md ({_n:,} lines)")
        print(f"  validation: {'all checks passed' if ok else 'FAILURES above'}")

    # ---- guard: no forward artefact may sit in the input folder ------------
    leaked = [n for n in FORWARD_ARTEFACTS if (DATA / n).exists()]
    print(f"\n  input datasets/: {len(list(DATA.glob('*.csv')))} tables"
          + (f"  WARNING forward artefacts present: {leaked}" if leaked
             else "  (no forward artefacts)"))

    print(f"\nWrote {out}")
    print("\nScore a forward scenario from here:")
    print(f'  python models/twin/digital_twin.py --mode forward '
          f'--run-name baseline \\\n'
          f'      --policy "{out / "forward_policy.csv"}" \\\n'
          f'      --state  "{out / "opening_position.csv"}"')
    print(f"\nTotal runtime: {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

