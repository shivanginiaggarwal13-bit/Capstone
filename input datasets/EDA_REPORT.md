# Supply Brain - Comprehensive Dataset and Network EDA

*Auto-generated 2026-09-08 23:05. Single-file build: this report is produced in the same run that generates the data, so it always matches the current instance.*

## Executive snapshot

| Field | Value |
| --- | --- |
| SKUs | 500 |
| Network | 4 plants, 4 plant DCs, 8 regional DCs, 15 customer DCs, 60 lanes |
| B2B customers (banners) | 10 |
| Order lines (day-level, 2025) | 306,824 |
| Distinct orders (baskets) | 3,796 |
| Total demand (units, 2025) | 8,088,718 |
| Total revenue (2025) | $128,203,237 |
| Gross margin | $40,383,456 (31.5 pct) |
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
| orders | 306,824 | 15 | 289,481 |
| promotions | 1,741 | 7 | 0 |
| demand_forecast | 772,120 | 9 | 930,592 |
| production | 24,838 | 7 | 0 |
| inventory | 512,408 | 10 | 0 |
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
| Fast-Moving | 250 | 50.00 | 67.30 |
| Seasonal | 150 | 30.00 | 26.60 |
| Intermediate | 100 | 20.00 | 6.10 |


**ABC by revenue (A=top 80pct, B=next 15pct, C=last 5pct):**
| cls | skus | sku_pct | rev_pct |
| --- | --- | --- | --- |
| A | 74 | 14.80 | 79.90 |
| B | 135 | 27.00 | 15.10 |
| C | 291 | 58.20 | 5.00 |


Concentration: top 20 pct of SKUs = 84.7 pct of revenue (steep, ~80/20).


![abc_pareto.png](report_assets/abc_pareto.png)

## 2.5 Demand analysis

**By category:**
| category | units | revenue | rev_pct |
| --- | --- | --- | --- |
| Beverages | 2,020,004 | 72,790,267.75 | 56.80 |
| Snacks & Confectionery | 1,549,088 | 23,679,377.84 | 18.50 |
| Grocery | 1,542,299 | 14,983,711.81 | 11.70 |
| Dairy & Bakery | 1,607,430 | 8,583,038.68 | 6.70 |
| Frozen Foods | 1,369,897 | 8,166,840.49 | 6.40 |


**By fulfilment mode:**
| delivery_mode | units | revenue | lines |
| --- | --- | --- | --- |
| BULK | 6,331,014 | 99,165,463.02 | 162,038 |
| DSD | 1,757,704 | 29,037,773.55 | 144,786 |


**By customer (banner):**
| customer_name | revenue_usd |
| --- | --- |
| MegaMart Stores | 34,564,620.51 |
| ValueClub Warehouse | 23,809,649.62 |
| GreenGrocer National | 21,856,284.23 |
| BullsEye Retail | 18,934,908.66 |
| SunBelt Grocers | 6,912,551.79 |
| FreshFoods Regional | 5,774,562.19 |
| QuickStop Convenience | 4,834,863.89 |
| CampusDining Foodservice | 4,134,826.81 |
| Coastal Marts | 4,080,023.54 |
| MetroPetro C-Stores | 3,300,945.33 |


![revenue_by_banner.png](report_assets/revenue_by_banner.png)


![seasonality_by_category.png](report_assets/seasonality_by_category.png)

## 2.6 Demand variability and intermittency

Weekly demand coefficient of variation (CV) and intermittency by segment - the inputs safety-stock sizing keys off:

| segment | avg_cv | avg_zero_week_pct |
| --- | --- | --- |
| Fast-Moving | 0.46 | 1.40 |
| Intermediate | 0.48 | 6.00 |
| Seasonal | 0.51 | 2.70 |


![demand_cv_hist.png](report_assets/demand_cv_hist.png)

## 2.7 Promotions and daily-order structure

| Field | Value |
| --- | --- |
| Promo events | 1,741 |
| Customer-specific vs ALL | 66 pct / 34 pct |
| Avg discount depth | 19.8 pct |
| Order lines on promo | 5.7 pct |
| Avg lines/order | 80.8 |
| Avg delivery lead (days) | 3.1 |


**Promo lift (avg line qty on-promo / off-promo), by category:**
| category | base_qty | on_promo_qty | promo_lift_x |
| --- | --- | --- | --- |
| Beverages | 32.10 | 41.50 | 1.29 |
| Dairy & Bakery | 24.67 | 48.80 | 1.98 |
| Frozen Foods | 20.83 | 77.22 | 3.71 |
| Grocery | 22.70 | 50.76 | 2.24 |
| Snacks & Confectionery | 22.80 | 54.43 | 2.39 |


![discount_depth.png](report_assets/discount_depth.png)

## 2.8 Price variation and elasticity (base vs promotional)

Two-elasticity recovery from orders (no elasticity shipped): regress ln(order_qty) on ln(base_price_usd) and ln(1-discount) with SKU and week fixed effects. Base = shelf-price response; promo = deal response.

| category | base_elasticity | promo_elasticity | promo_over_base | n_obs |
| --- | --- | --- | --- | --- |
| Beverages | -1.09 | -1.31 | 1.20 | 61,978 |
| Dairy & Bakery | -1.25 | -2.40 | 1.92 | 61,622 |
| Frozen Foods | -3.45 | -3.47 | 1.00 | 56,619 |
| Grocery | -1.86 | -2.17 | 1.17 | 63,600 |
| Snacks & Confectionery | -3.58 | -3.61 | 1.01 | 63,005 |

## 2.9 Forecast accuracy and forward horizon

**By segment:**
| segment | MAPE_pct | WAPE_pct | bias_pct |
| --- | --- | --- | --- |
| Fast-Moving | 6.60 | 7.80 | 0.00 |
| Intermediate | 11.90 | 13.40 | 0.10 |
| Seasonal | 21.60 | 22.60 | -2.00 |


**By category:**
| category | MAPE_pct | WAPE_pct | bias_pct |
| --- | --- | --- | --- |
| Beverages | 11.80 | 11.00 | -0.40 |
| Dairy & Bakery | 12.20 | 13.20 | -0.40 |
| Frozen Foods | 11.20 | 10.90 | -0.30 |
| Grocery | 11.90 | 14.40 | -1.50 |
| Snacks & Confectionery | 12.50 | 13.40 | -0.30 |


Forward 2026 total forecast units 8,489,372 vs 2025 actual 8,088,718, YoY +5.0 pct.


![forecast_error_hist.png](report_assets/forecast_error_hist.png)


![hist_vs_forward.png](report_assets/hist_vs_forward.png)

## 2.10 Production (planned vs actual)

| Field | Value |
| --- | --- |
| Planned units | 8,088,718 |
| Actual units | 7,548,398 |
| Mean schedule adherence | 93.3 pct |
| Weeks below 90 pct adherence | 11.3 pct |


| plant_id | planned | actual | adherence |
| --- | --- | --- | --- |
| PLANT01 | 1,259,538 | 1,175,315 | 93.40 |
| PLANT02 | 1,939,571 | 1,809,674 | 93.20 |
| PLANT03 | 3,127,724 | 2,919,467 | 93.30 |
| PLANT04 | 1,761,885 | 1,643,942 | 93.30 |


![adherence_hist.png](report_assets/adherence_hist.png)

## 2.11 Inventory, service and working capital

| ech | avg_closing | avg_safety | days_of_supply | stockout_wk_pct | avg_inv_value_usd |
| --- | --- | --- | --- | --- | --- |
| Customer DC | 24.30 | 9.50 | 20.20 | 8.20 | 254.10 |
| Plant DC | 216.60 | 77.90 | 19.50 | 0.60 | 2,184.00 |
| Regional DC | 115.00 | 43.20 | 20.10 | 1.10 | 1,159.40 |


Network-wide: avg weekly inventory value ~ **$9,846,031** (working capital), annualized turns ~ **18.4x**, availability ~ **96.1 pct** (3.9 pct of DC-SKU-weeks stock out).


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


Last-mile lane utilization (CDC weekly flow / primary inbound lane capacity): mean 0.6 pct, max 1.4 pct (approx).


**Plant production utilization (avg weekly output / capacity):**
| plant_id | actual_qty | production_capacity_units_per_week | util_pct |
| --- | --- | --- | --- |
| PLANT01 | 23,045.39 | 4,607,407 | 0.50 |
| PLANT02 | 35,483.80 | 3,732,615 | 1.00 |
| PLANT03 | 57,244.45 | 3,534,342 | 1.60 |
| PLANT04 | 32,234.16 | 4,331,887 | 0.70 |


![dc_utilization.png](report_assets/dc_utilization.png)

## 2.14 Financial summary (2025)

| Field | Value |
| --- | --- |
| Revenue | $128,203,237 |
| COGS (units x unit cost) | $87,819,781 |
| Gross margin | $40,383,456 (31.5 pct) |
| Avg working capital (inventory value) | $9,846,031 |
| Inventory turns (annualized) | 18.4x |
| Est. last-mile logistics cost (approx/yr) | $2,150,719 |

## 2.15 Disruption scenario library (digital-twin / bandit input)

A labeled library of demand-side and supply-side disruptions with Time-to-Recover (TTR) and Time-to-Survive (TTS) estimates - the what-if inputs the digital twin, bandit reward, and agentic layer consume. Each row names the affected node/SKU/region and window.

| scenario_type | count | avg_ttr_days | avg_tts_days |
| --- | --- | --- | --- |
| demand_surge | 6 | 0.00 | 18.30 |
| forecast_miss | 5 | 0.00 | 0.00 |
| geographic_disruption | 4 | 10.80 | 0.00 |
| lane_outage | 6 | 4.30 | 19.00 |
| leadtime_spike | 5 | 0.00 | 19.40 |
| plant_shortfall | 4 | 8.00 | 0.00 |


Total scenarios: 30 across 6 classes (supplier, geographic, systemic, market-driven).

## 2.16 Key findings

- Revenue is led by Beverages (56.8 pct of sales); demand is steeply concentrated (top 20 pct SKUs ~ 85 pct of revenue).
- Most price-elastic category (base): Snacks & Confectionery (-3.58); least: Beverages (-1.09). Promo elasticity exceeds base in 5/5 categories.
- Forecast accuracy degrades from fast-movers to seasonal SKUs (see 2.9); forward year grows ~5 pct YoY.
- Network is fully dual-sourced (15/15 Customer DCs with failover) with 10 lateral lanes; storage utilization averages 0 pct.
- Availability ~ 96 pct at $9,846,031 avg working capital and 18.4x turns.
- No-gap coverage: every Customer DC has demand, inventory is modelled at all echelons (Plant DC, Regional DC, Customer DC), and a 30-scenario disruption library covers demand- and supply-side stresses.


---
*End of report.*

