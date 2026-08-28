# Supply Brain - Comprehensive Dataset and Network EDA

*Auto-generated 2026-08-27 21:46. Single-file build: this report is produced in the same run that generates the data, so it always matches the current instance.*

## Executive snapshot

| Field | Value |
| --- | --- |
| SKUs | 500 |
| Network | 4 plants, 4 plant DCs, 8 regional DCs, 15 customer DCs, 60 lanes |
| B2B customers (banners) | 10 |
| Order lines (day-level, 2025) | 306,945 |
| Distinct orders (baskets) | 3,796 |
| Total demand (units, 2025) | 8,078,208 |
| Total revenue (2025) | $128,098,053 |
| Gross margin | $40,374,211 (31.5 pct) |
| Forecast horizon | 104 weeks (2025 in-sample + 2026 forward) |


---

# Part 1 - Parameters and Assumptions

Synthetic CPG Food and Beverage supply chain, USD, grounded in an external real F&B sales dataset (per-unit prices, per-group elasticity, 2020-shape seasonality, ~80/20 concentration; 2020 COVID spike excluded). 5 categories x 100 SKUs, segmented ~50/30/20 Fast-Moving/Seasonal/Intermediate. Two price elasticities per SKU (base shelf-price and promotional). Demand is day-level orders on a dual-sourced US network (Plant -> Plant DC -> Regional DC -> Customer DC) with lateral RDC-to-RDC rebalancing lanes. Supply-side parameters (cost/margin, holding, lead time, capacities) are modelled assumptions; demand-side is grounded.


---

# Part 2 - Exploratory Data Analysis

## 2.1 Dataset overview and data quality

| table | rows | cols | null_cells |
| --- | --- | --- | --- |
| sku_master | 500 | 14 | 0 |
| orders | 306,945 | 15 | 289,537 |
| promotions | 1,741 | 7 | 0 |
| demand_forecast | 772,241 | 9 | 930,592 |
| production | 24,690 | 7 | 0 |
| inventory | 512,200 | 10 | 0 |
| scenarios | 30 | 13 | 59 |
| sku_customer_service_targets | 3 | 3 | 0 |
| network_lanes | 60 | 11 | 0 |
| b2b_customer_master | 10 | 4 | 0 |
| customer_serving_dc | 28 | 4 | 0 |
| cost_parameters | 5 | 4 | 0 |
| plant_master | 4 | 6 | 0 |
| plant_dc_master | 4 | 5 | 0 |
| regional_dc_master | 8 | 6 | 0 |
| customer_dc_master | 15 | 6 | 0 |


**Data-quality checks:** orders order_qty > 0 = PASS; orders unit_price_usd > 0 = PASS; sku margins positive = PASS; inventory closing_stock >= 0 = PASS; delivery_date >= order_date = PASS; sku_id unique = PASS.

## 2.2 Referential integrity

| check | result |
| --- | --- |
| orders.sku_id in sku_master | PASS |
| orders.dc_id in stocking DCs | PASS |
| orders.b2b_customer_id in banners | PASS |
| orders.promo_id in promotions | PASS |
| forecast keys in masters | PASS |
| production.plant_id in plants | PASS |
| inventory.dc_id in stocking + plant DCs | PASS |
| lane endpoints in nodes | PASS |
| serving map dc in stocking DCs | PASS |
| forward forecast has no actuals | PASS |

## 2.3 Product catalog and economics (USD, per-unit)

| category | skus | median_price | min_price | max_price | avg_margin_pct | median_shelf_life_d |
| --- | --- | --- | --- | --- | --- | --- |
| Beverages | 100 | 12.96 | 1.33 | 157.60 | 31.90 | 251.00 |
| Dairy & Bakery | 100 | 3.70 | 0.41 | 60.11 | 30.10 | 12.00 |
| Frozen Foods | 100 | 6.12 | 2.09 | 17.38 | 30.90 | 374.00 |
| Grocery | 100 | 5.83 | 0.33 | 118.09 | 32.90 | 374.50 |
| Snacks & Confectionery | 100 | 3.87 | 2.00 | 46.78 | 34.00 | 180.50 |


Price tiers: Premium 262, Value 238. Seasonal-flagged SKUs: 150/500.


![price_by_category.png](report_assets/price_by_category.png)

## 2.4 SKU segmentation and ABC classification

| segment | sku_count | sku_pct | revenue_pct |
| --- | --- | --- | --- |
| Fast-Moving | 250 | 50.00 | 67.40 |
| Seasonal | 150 | 30.00 | 26.60 |
| Intermediate | 100 | 20.00 | 6.00 |


**ABC by revenue (A=top 80pct, B=next 15pct, C=last 5pct):**
| cls | skus | sku_pct | rev_pct |
| --- | --- | --- | --- |
| A | 74 | 14.80 | 79.80 |
| B | 135 | 27.00 | 15.20 |
| C | 291 | 58.20 | 5.00 |


Concentration: top 20 pct of SKUs = 84.7 pct of revenue (steep, ~80/20).


![abc_pareto.png](report_assets/abc_pareto.png)

## 2.5 Demand analysis

**By category:**
| category | units | revenue | rev_pct |
| --- | --- | --- | --- |
| Beverages | 2,015,938 | 72,768,633.54 | 56.80 |
| Snacks & Confectionery | 1,545,822 | 23,612,600.37 | 18.40 |
| Grocery | 1,541,544 | 14,973,492.44 | 11.70 |
| Dairy & Bakery | 1,606,423 | 8,588,446.80 | 6.70 |
| Frozen Foods | 1,368,481 | 8,154,879.64 | 6.40 |


**By fulfilment mode:**
| delivery_mode | units | revenue | lines |
| --- | --- | --- | --- |
| BULK | 6,323,781 | 99,122,368.15 | 162,035 |
| DSD | 1,754,427 | 28,975,684.64 | 144,910 |


**By customer (banner):**
| customer_name | revenue_usd |
| --- | --- |
| MegaMart Stores | 34,630,416.39 |
| ValueClub Warehouse | 23,722,121.61 |
| GreenGrocer National | 21,862,624.87 |
| BullsEye Retail | 18,907,205.28 |
| SunBelt Grocers | 6,900,263.65 |
| FreshFoods Regional | 5,766,086.81 |
| QuickStop Convenience | 4,840,606.01 |
| CampusDining Foodservice | 4,100,306.94 |
| Coastal Marts | 4,056,694.63 |
| MetroPetro C-Stores | 3,311,726.60 |


![revenue_by_banner.png](report_assets/revenue_by_banner.png)


![seasonality_by_category.png](report_assets/seasonality_by_category.png)

## 2.6 Demand variability and intermittency

Weekly demand coefficient of variation (CV) and intermittency by segment - the inputs safety-stock sizing keys off:

| segment | avg_cv | avg_zero_week_pct |
| --- | --- | --- |
| Fast-Moving | 0.47 | 2.70 |
| Intermediate | 0.48 | 5.80 |
| Seasonal | 0.50 | 2.50 |


![demand_cv_hist.png](report_assets/demand_cv_hist.png)

## 2.7 Promotions and daily-order structure

| Field | Value |
| --- | --- |
| Promo events | 1,741 |
| Customer-specific vs ALL | 66 pct / 34 pct |
| Avg discount depth | 19.8 pct |
| Order lines on promo | 5.7 pct |
| Avg lines/order | 80.9 |
| Avg delivery lead (days) | 3.0 |


**Promo lift (avg line qty on-promo / off-promo), by category:**
| category | base_qty | on_promo_qty | promo_lift_x |
| --- | --- | --- | --- |
| Beverages | 32.05 | 40.80 | 1.27 |
| Dairy & Bakery | 24.67 | 48.31 | 1.96 |
| Frozen Foods | 20.77 | 76.88 | 3.70 |
| Grocery | 22.73 | 49.57 | 2.18 |
| Snacks & Confectionery | 22.78 | 54.25 | 2.38 |


![discount_depth.png](report_assets/discount_depth.png)

## 2.8 Price variation and elasticity (base vs promotional)

Two-elasticity recovery from orders (no elasticity shipped): regress ln(order_qty) on ln(base_price_usd) and ln(1-discount) with SKU and week fixed effects. Base = shelf-price response; promo = deal response.

| category | base_elasticity | promo_elasticity | promo_over_base | n_obs |
| --- | --- | --- | --- | --- |
| Beverages | -1.06 | -1.34 | 1.26 | 62,008 |
| Dairy & Bakery | -1.25 | -2.43 | 1.94 | 61,625 |
| Frozen Foods | -3.50 | -3.50 | 1.00 | 56,675 |
| Grocery | -1.83 | -2.23 | 1.22 | 63,632 |
| Snacks & Confectionery | -3.62 | -3.58 | 0.99 | 63,005 |

## 2.9 Forecast accuracy and forward horizon

**By segment:**
| segment | MAPE_pct | WAPE_pct | bias_pct |
| --- | --- | --- | --- |
| Fast-Moving | 6.60 | 7.80 | -0.00 |
| Intermediate | 11.90 | 13.50 | 0.10 |
| Seasonal | 21.50 | 22.40 | -1.80 |


**By category:**
| category | MAPE_pct | WAPE_pct | bias_pct |
| --- | --- | --- | --- |
| Beverages | 11.80 | 11.00 | -0.20 |
| Dairy & Bakery | 12.10 | 13.10 | -0.40 |
| Frozen Foods | 11.20 | 10.90 | -0.40 |
| Grocery | 11.90 | 14.40 | -1.30 |
| Snacks & Confectionery | 12.50 | 13.50 | -0.50 |


Forward 2026 total forecast units 8,471,279 vs 2025 actual 8,078,208, YoY +4.9 pct.


![forecast_error_hist.png](report_assets/forecast_error_hist.png)


![hist_vs_forward.png](report_assets/hist_vs_forward.png)

## 2.10 Production (planned vs actual)

| Field | Value |
| --- | --- |
| Planned units | 8,078,208 |
| Actual units | 7,532,221 |
| Mean schedule adherence | 93.3 pct |
| Weeks below 90 pct adherence | 11.3 pct |


| plant_id | planned | actual | adherence |
| --- | --- | --- | --- |
| PLANT01 | 1,256,446 | 1,169,796 | 93.30 |
| PLANT02 | 1,936,908 | 1,803,674 | 93.20 |
| PLANT03 | 3,123,748 | 2,913,112 | 93.30 |
| PLANT04 | 1,761,106 | 1,645,639 | 93.30 |


![adherence_hist.png](report_assets/adherence_hist.png)

## 2.11 Inventory, service and working capital

| ech | avg_closing | avg_safety | days_of_supply | stockout_wk_pct | avg_inv_value_usd |
| --- | --- | --- | --- | --- | --- |
| Customer DC | 24.30 | 9.50 | 20.30 | 8.20 | 253.90 |
| Plant DC | 216.70 | 78.40 | 19.50 | 0.60 | 2,185.40 |
| Regional DC | 114.60 | 43.00 | 20.00 | 1.30 | 1,153.90 |


Network-wide: avg weekly inventory value ~ **$9,821,546** (working capital), annualized turns ~ **18.4x**, availability ~ **96.1 pct** (3.9 pct of DC-SKU-weeks stock out).


![days_of_supply.png](report_assets/days_of_supply.png)


![inventory_value.png](report_assets/inventory_value.png)

## 2.12 Network structure and cardinality

| Field | Value |
| --- | --- |
| Plants | 4 |
| Plant DCs | 4 |
| Regional DCs | 8 |
| Customer DCs | 15 |
| Lanes | 60 |
| Transport modes | Train, Truck |


**Cardinality per hop (fan-out per upstream, fan-in per downstream):**
| hop | lanes | fan_out | fan_in |
| --- | --- | --- | --- |
| PLANT -> PLANT_DC | 4 | 1-1 | 1-1 |
| PLANT_DC -> REGIONAL_DC | 16 | 3-5 | 2-2 |
| REGIONAL_DC -> CUSTOMER_DC | 30 | 2-6 | 2-2 |
| REGIONAL_DC -> REGIONAL_DC | 10 | 1-2 | 1-2 |


Dual-sourcing: 15/15 Customer DCs have >=2 inbound Regional DCs (failover); lateral RDC-to-RDC lanes: 10.


**Lanes by tier and mode:**
| origin_node_type | destination_node_type | transport_mode | lanes | avg_lead_d | avg_mi | avg_cost |
| --- | --- | --- | --- | --- | --- | --- |
| PLANT | PLANT_DC | Truck | 4 | 0.50 | 0.00 | 0.71 |
| PLANT_DC | REGIONAL_DC | Train | 8 | 6.47 | 968.26 | 2.62 |
| PLANT_DC | REGIONAL_DC | Truck | 8 | 2.27 | 315.24 | 1.35 |
| REGIONAL_DC | CUSTOMER_DC | Train | 6 | 5.12 | 713.98 | 2.03 |
| REGIONAL_DC | CUSTOMER_DC | Truck | 24 | 2.96 | 329.60 | 1.45 |
| REGIONAL_DC | REGIONAL_DC | Truck | 10 | 4.80 | 752.13 | 2.12 |

## 2.13 Capacity utilization

DC storage utilization (avg on-hand / capacity): mean 0.0 pct, max 0.0 pct.


Last-mile lane utilization (CDC weekly flow / primary inbound lane capacity): mean 0.7 pct, max 2.1 pct (approx).


**Plant production utilization (avg weekly output / capacity):**
| plant_id | actual_qty | production_capacity_units_per_week | util_pct |
| --- | --- | --- | --- |
| PLANT01 | 22,937.18 | 4,607,407 | 0.50 |
| PLANT02 | 35,366.16 | 3,732,615 | 0.90 |
| PLANT03 | 57,119.84 | 3,534,342 | 1.60 |
| PLANT04 | 32,267.43 | 4,331,887 | 0.70 |


![dc_utilization.png](report_assets/dc_utilization.png)

## 2.14 Financial summary (2025)

| Field | Value |
| --- | --- |
| Revenue | $128,098,053 |
| COGS (units x unit cost) | $87,723,841 |
| Gross margin | $40,374,211 (31.5 pct) |
| Avg working capital (inventory value) | $9,821,546 |
| Inventory turns (annualized) | 18.4x |
| Est. last-mile logistics cost (approx/yr) | $2,178,364 |

## 2.15 Disruption scenario library (digital-twin / bandit input)

A labeled library of demand-side and supply-side disruptions with Time-to-Recover (TTR) and Time-to-Survive (TTS) estimates - the what-if inputs the digital twin, bandit reward, and agentic layer consume. Each row names the affected node/SKU/region and window.

| scenario_type | count | avg_ttr_days | avg_tts_days |
| --- | --- | --- | --- |
| demand_surge | 6 | 0.00 | 18.50 |
| forecast_miss | 5 | 0.00 | 0.00 |
| geographic_disruption | 4 | 10.80 | 0.00 |
| lane_outage | 6 | 4.30 | 18.20 |
| leadtime_spike | 5 | 0.00 | 19.40 |
| plant_shortfall | 4 | 8.00 | 0.00 |


Total scenarios: 30 across 6 classes (supplier, geographic, systemic, market-driven).

## 2.16 Key findings

- Revenue is led by Beverages (56.8 pct of sales); demand is steeply concentrated (top 20 pct SKUs ~ 85 pct of revenue).
- Most price-elastic category (base): Snacks & Confectionery (-3.62); least: Beverages (-1.06). Promo elasticity exceeds base in 4/5 categories.
- Forecast accuracy degrades from fast-movers to seasonal SKUs (see 2.9); forward year grows ~5 pct YoY.
- Network is fully dual-sourced (15/15 Customer DCs with failover) with 10 lateral lanes; storage utilization averages 0 pct.
- Availability ~ 96 pct at $9,821,546 avg working capital and 18.4x turns.
- No-gap coverage: every Customer DC has demand, inventory is modelled at all echelons (Plant DC, Regional DC, Customer DC), and a 30-scenario disruption library covers demand- and supply-side stresses.


---
*End of report.*

