# Supply Brain Synthetic Dataset — EDA Report

*Auto-generated 2026-08-10 21:17 from data in `.`. The dataset is randomly generated, so figures differ between generation runs; this report reflects the current instance on disk.*

## Instance fingerprint

| Field | Value |
| --- | --- |
| SKUs | 500 |
| Order rows (historical) | 398,746 |
| Forecast rows (in-sample + forward) | 797,846 |
| Total historical revenue (USD) | $7,558,562,192 |
| Historical week span | 2025-W01 → 2025-W52 (52 weeks) |
| Forecast week span | 2025-W01 → 2026-W52 (104 weeks) |

---

# Part 1 — Parameters, Assumptions & Interpretation

This dataset is a fully-synthetic CPG (packaged food & beverage) supply chain, built to behave like a real client extract. Every table below is generated from an explicit business model; the parameters and assumptions that shape it are documented here, each checked against the realized data in this instance.

## 1.1 Scope & grain

- **Product scope:** 5 categories × 100 SKUs = 500 SKUs. **Realized:** 500 SKUs across 5 categories.
- **Time scope:** 52 weeks of historical actuals (2025) + 52 weeks of forward forecast (2026). The forward year is the execution horizon for MEIO / buffer optimization and has no actuals.
- **Grain:** orders & forecast at SKU × DC × Customer × Week; production at SKU × Plant × Week; inventory at SKU × DC × Week.
- **Currency:** all monetary values USD (columns suffixed `_usd`).

## 1.2 Product catalog & economics (USD)

SKUs are split across 3 demand **segments** with a target mix of **50% Fast-Moving / 30% Seasonal / 20% Intermediate**, and a **price tier** (Premium vs Value) within each category. Prices are realistic CPG case-level USD values; margin is designed at ~30–34% (cost ratio ~0.66–0.70).

| segment | target_% | realized_% |
| --- | --- | --- |
| Fast-Moving | 50.00 | 50.00 |
| Seasonal | 30.00 | 30.00 |
| Intermediate | 20.00 | 20.00 |


**List price & shelf life — design intent vs realized, by category:**

| category | intended_price_range | intended_median | realized_price_range | realized_median | realized_avg_margin | intended_shelf_life | realized_shelf_life |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Beverages | $6–$32 | $14 | $6.95–$32.00 | $14.30 | 32.0% | 120–365d | 120–365d |
| Snacks & Confectionery | $8–$42 | $18 | $9.30–$42.00 | $18.73 | 34.0% | 90–300d | 90–299d |
| Dairy & Bakery | $9–$48 | $22 | $9.00–$48.00 | $20.43 | 30.0% | 5–21d | 5–21d |
| Frozen Foods | $12–$60 | $28 | $13.88–$60.00 | $28.72 | 31.1% | 180–540d | 182–536d |
| Grocery | $6–$38 | $16 | $7.26–$38.00 | $15.77 | 32.7% | 180–540d | 181–540d |

## 1.3 Network (modelled on the Coca-Cola US bottling system)

Tiered **Production Plant → Plant DC → Regional DC → Customer/Sales DC**, at roughly the real Coca-Cola ratio (~1 plant per 5–6 DCs). Plants are production-only (hold no inventory); the Plant DC is the first stock-holding echelon. Two real delivery patterns are modelled:
- **Warehouse / bulk** national accounts are served from **Regional DCs**.
- **DSD** (direct-store-delivery) small-format / convenience / foodservice banners are served from local **Customer DCs**.
This dual pattern is why orders legitimately reference *any* stocking DC (regional or customer).

| Field | Value |
| --- | --- |
| Production plants | 4 |
| Plant DCs | 4 |
| Regional DCs | 6 |
| Customer/Sales DCs | 12 |
| Plant : DC ratio | 1 : 5.5 |
| B2B customer banners | 10 |
| Delivery modes | BULK, DSD |
| Transport modes | Train, Truck |
| Lanes | 28 |

## 1.4 Demand model, seasonality & promotions

Weekly orders are drawn from an explicit **log-linear demand model**:

```
log(qty) = log(base * customer_scale * dc_share) + elasticity * log(price_week / list_price) + log(seasonal_index[week]) + noise
```

- **Seasonality** is category-specific: Beverages & Frozen peak in summer; Dairy & Bakery and Snacks peak around the holidays; Grocery is flatter. The `Seasonal` segment has the largest amplitude, `Intermediate` the smallest.
- **Promotions** create genuine price variation: ~30% of SKU-weeks are discounted 5–35%. **Realized:** 29.8% of order rows on promo, avg depth 19.8%.
- **Elasticity** is embedded per SKU (by category × price tier) so that demand genuinely responds to price. It is deliberately **not** shipped as a column — Part 2 recovers it from the orders using the documented method.

**Internal elasticity targets by category (for reference; recovered in Part 2):**

| category | internal_elasticity_target |
| --- | --- |
| Beverages | -2.00 |
| Snacks & Confectionery | -2.10 |
| Dairy & Bakery | -0.55 |
| Frozen Foods | -1.30 |
| Grocery | -0.60 |

## 1.5 Forecast horizons

`demand_forecast` spans **104 weeks** with a `horizon` flag:
- **in_sample (2025):** forecast + actual + error — for forecast-accuracy analysis. Forecasts deliberately miss promo spikes and under-call the holiday peak for Seasonal SKUs.
- **forward (2026):** forecast only, **no actuals** — the MEIO execution horizon. Built by projecting each SKU-DC-customer level forward with ~5% YoY growth and the same seasonal shape.

| Field | Value |
| --- | --- |
| `forward` rows | 399,100 |
| `in_sample` rows | 398,746 |

## 1.6 Cost & economic parameters (USD)

- **Holding cost:** annual carrying cost as a % of unit cost, by category (perishables higher). Also expressed per-unit-per-year in USD on `sku_master`.
- **Cost of capital & stockout multiplier:** in `cost_parameters`.
- **Logistics cost:** USD per unit per lane on `network_lanes`, scaling with distance.

| category | holding_cost_pct_annual | cost_of_capital_pct | stockout_cost_multiplier |
| --- | --- | --- | --- |
| Beverages | 0.22 | 0.10 | 1.85 |
| Snacks & Confectionery | 0.21 | 0.10 | 2.36 |
| Dairy & Bakery | 0.34 | 0.08 | 1.94 |
| Frozen Foods | 0.33 | 0.10 | 2.34 |
| Grocery | 0.20 | 0.10 | 2.25 |

## 1.7 Key assumptions & caveats

- Prices, costs, capacities, lead times and cost rates are realistic **assumptions**, not client facts — validate before using in a hard optimization.
- Inventory is a single-echelon (s,S) flow simulation per DC driven by its outbound orders; plant-DC replenishment coupling is simplified.
- Network geography is US (Coca-Cola-modelled) and self-contained; it does not reference any external/real dataset.
- Elasticity is embedded to make demand price-responsive but is **not** provided as ground truth; the estimate in Part 2 is what a modeller would compute from orders alone.


---

# Part 2 — Exploratory Data Analysis

## 2.1 Dataset overview

| table | rows | cols |
| --- | --- | --- |
| sku_master | 500 | 14 |
| orders | 398,746 | 11 |
| demand_forecast | 797,846 | 10 |
| production | 25,500 | 8 |
| inventory | 269,672 | 11 |
| network_lanes | 28 | 11 |
| b2b_customer_master | 10 | 4 |
| customer_serving_dc | 23 | 4 |
| cost_parameters | 5 | 4 |
| plant_master | 4 | 6 |
| plant_dc_master | 4 | 5 |
| regional_dc_master | 6 | 6 |
| customer_dc_master | 12 | 6 |

## 2.2 Referential integrity

| check | result |
| --- | --- |
| orders.sku_id ⊆ sku_master | PASS |
| orders.b2b_customer_id ⊆ banners | PASS |
| orders.dc_id ⊆ stocking DCs | PASS |
| forecast keys ⊆ masters | PASS |
| production.plant_id ⊆ plants | PASS |
| inventory.dc_id ⊆ stocking DCs | PASS |
| lane endpoints ⊆ nodes | PASS |
| forward forecast has no actuals | PASS |
| in_sample forecast has actuals | PASS |

## 2.3 Product & economics

| category | skus | avg_list_usd | avg_cost_usd | avg_margin_pct | avg_shelf_life_d |
| --- | --- | --- | --- | --- | --- |
| Beverages | 100 | 15.47 | 10.54 | 32.00 | 245.08 |
| Dairy & Bakery | 100 | 22.08 | 15.46 | 30.00 | 12.17 |
| Frozen Foods | 100 | 29.81 | 20.57 | 31.10 | 355.36 |
| Grocery | 100 | 17.58 | 11.82 | 32.70 | 370.41 |
| Snacks & Confectionery | 100 | 20.41 | 13.48 | 34.00 | 187.52 |


Price-tier split: Premium 251, Value 249. Seasonal-flagged SKUs: 150 of 500.


![price by category](report_assets/price_by_category.png)

## 2.4 Orders & demand

| Field | Value |
| --- | --- |
| Total units ordered | 395,569,401 |
| Total revenue (USD) | $7,558,562,192 |
| Avg order line (units) | 992.0 |
| Avg selling price (USD) | $19.83 |


**By category:**
| category | units | revenue_usd | revenue_% |
| --- | --- | --- | --- |
| Frozen Foods | 81,762,482 | 2,224,468,918 | 29.40 |
| Snacks & Confectionery | 84,537,745 | 1,521,971,725 | 20.10 |
| Dairy & Bakery | 70,874,850 | 1,454,066,180 | 19.20 |
| Beverages | 85,578,949 | 1,184,536,382 | 15.70 |
| Grocery | 72,815,375 | 1,173,518,988 | 15.50 |


**By demand segment:**
| segment | units | revenue_usd |
| --- | --- | --- |
| Fast-Moving | 273,417,436 | 5,161,481,553 |
| Intermediate | 28,285,937 | 532,126,688 |
| Seasonal | 93,866,028 | 1,864,953,951 |


**By delivery mode (BULK = warehouse via RDC, DSD = via customer DC):**
| delivery_mode | units | revenue_usd | orders |
| --- | --- | --- | --- |
| BULK | 308,790,863 | 5,896,271,930 | 198,900 |
| DSD | 86,778,538 | 1,662,290,262 | 199,846 |


**Concentration (Pareto):** top 20% of SKUs (100) drive 42.9% of revenue.


![seasonality](report_assets/seasonality_by_category.png)


![discounts](report_assets/discount_depth.png)

## 2.5 Price variation & measured elasticity

Elasticity is estimated from the historical orders using the documented method — a two-way (SKU + week) fixed-effects regression of `log(order_qty)` on `log(unit_price_usd)`, pooled per category (pure-numpy iterative demeaning; no `statsmodels` needed). The SKU effect absorbs baseline volume/list price so the slope is identified from within-SKU promo price changes; the week effect absorbs common seasonality. The `internal_target` column is the value baked into the generator, shown only to gauge recovery — a real client dataset would not include it.

| category | measured_elasticity | internal_target | gap_vs_target | n_obs |
| --- | --- | --- | --- | --- |
| Beverages | -1.89 | -2.00 | 0.11 | 80,463 |
| Dairy & Bakery | -0.52 | -0.55 | 0.03 | 78,852 |
| Frozen Foods | -1.20 | -1.30 | 0.10 | 79,981 |
| Grocery | -0.56 | -0.60 | 0.04 | 79,948 |
| Snacks & Confectionery | -1.98 | -2.10 | 0.12 | 79,502 |


*Recovery: 5/5 categories within 0.5 of the embedded target; all elasticities negative as expected. Interpretation: Beverages & Snacks are the most price-elastic, Dairy & Bakery and Grocery the most inelastic — consistent with staple vs. discretionary demand.*

## 2.6 Forecast accuracy & forward horizon

**In-sample forecast accuracy (2025) — MAPE by segment:**
| segment | MAPE_% |
| --- | --- |
| Fast-Moving | 7.80 |
| Intermediate | 13.10 |
| Seasonal | 22.50 |


Overall in-sample forecast bias: -0.5% (negative = under-forecast, e.g. missed promo/holiday peaks).


**Forward horizon (2026):** total forecast units 415,308,820 vs historical actual 395,569,401 → implied YoY change +5.0% (design target ~5%).


![forward](report_assets/hist_vs_forward.png)

## 2.7 Production (planned vs actual)

| Field | Value |
| --- | --- |
| Planned units (total) | 395,569,401 |
| Actual units (total) | 369,272,304 |
| Mean schedule adherence | 93.4% |
| Weeks below 90% adherence | 10.5% |

| plant_id | planned | actual | adherence |
| --- | --- | --- | --- |
| PLANT01 | 98,068,392 | 91,557,006 | 93.30 |
| PLANT02 | 99,456,727 | 92,797,945 | 93.30 |
| PLANT03 | 98,985,034 | 92,418,230 | 93.40 |
| PLANT04 | 99,059,248 | 92,499,123 | 93.40 |

## 2.8 Inventory & service

| Field | Value |
| --- | --- |
| Avg closing stock (units) | 2,729 |
| Avg safety stock (units) | 900 |
| DC-SKU-weeks at zero stock | 0.8% |
| Total shipments (units) | 394,741,571 |

| echelon | avg_closing | avg_safety | zero_stock_pct |
| --- | --- | --- | --- |
| Customer DC | 1,085.40 | 358.30 | 0.80 |
| Regional DC | 4,756.90 | 1,568.80 | 0.70 |

## 2.9 Network & lanes

| transport_mode | lanes | avg_lead_days | avg_dist_mi | avg_logi_usd |
| --- | --- | --- | --- | --- |
| Train | 3 | 5.20 | 850.53 | 2.28 |
| Truck | 25 | 2.62 | 313.85 | 1.37 |


**Lead time by lane tier (origin node type):**
| origin_node_type | lanes | avg_lead_days |
| --- | --- | --- |
| PLANT | 4 | 0.50 |
| PLANT_DC | 12 | 3.94 |
| REGIONAL_DC | 12 | 2.65 |


---
*End of report. Regenerate any time with `python supply_brain_eda_report.py` after re-running the data generator.*

