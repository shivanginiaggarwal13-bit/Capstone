# Supply Brain â€“ Data Schema, Table Register & Synthetic Data Assumptions
*Capstone project: Accenture Supply Chain & Engineering â€“ AI and Data | AMPBA 2026S*
*Last updated: 2026-08-04*

## 1. Status Legend
| Status | Meaning |
| --- | --- |
| ðŸŸ¢ Real | Exists today in `retail_clean_dataset` / `retail_contaminated_dataset`. Used as-is. |
| ðŸ”µ Derived | Computed/estimated **from** real data (e.g. regression, aggregation). Not randomly fabricated. |
| ðŸ”´ Synthetic | Does not exist in any source today. Generated from scratch, anchored to real keys and calibrated to researched CPG benchmarks where possible. |

## 2. Key Scope Decisions Baked Into This Schema
* Postponement Stage Optimization module dropped from scope (decided 2026-08-04) â€“ no tables for it below.
* Plant is production-only and holds no inventory; Plant DC is the first stock-holding echelon (Plant -> Plant DC -> Regional DC -> Customer DC).
* New network facilities (plants, plant DCs, regional DCs, customer DCs) are grounded in US geography. The existing real retail dataset's 30 stores sit in South Asian cities â€“ the two layers are geographically decoupled by design and connect only through the SKU/customer key structure, not physical geography.
* All cross-node movement lives in a single unified `network_lanes` edge table (not one table per echelon pair), matching the shape needed for the Neo4j graph edges.
* "Customer" is two different entities across the data: `customer_master.csv` (real) = individual shopper/loyalty member. `b2b_customer_master` (synthetic, new) = the retail banner/distributor the CPG ships to. Do not conflate them.
* RT = Retail. Target demand channel split is RT 70% / brick-and-mortar 30% (sponsor-provided); confirm exact definition before final calibration â€“ it does not map cleanly to the existing `preferred_channel` field.
* Price elasticity is DERIVED (regression on real promo/discount data), not synthesized â€“ real price variation already exists in `sales_transactions` via promotions (~21% of transactions, 5-50% discount range).
* Feedback/recalibration-loop data (actual-vs-forecast tracking, drift detection) is still an open gap not covered by any table below â€“ flagged in prior review, not yet resolved.

## 3. Table Inventory
| Table                          | Status         | Grain                                | Represents                                                                                                                                                                                                          | Description                                                                    | Source / Generation Method |
| ------------------------------ | -------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | -------------------------- |
| `plant_master`                 | ðŸ”´ Synthetic | Plant                                | Production-only node. No inventory attributes â€“ plant does not hold stock.                                                                                                                                        | US-based synthetic facility generation                                         |                            |
| `plant_dc_master`              | ðŸ”´ Synthetic | Plant DC                             | First stock-holding echelon, one per plant.                                                                                                                                                                         | US-based synthetic facility generation, linked to plant_master                 |                            |
| `regional_dc_master`           | ðŸ”´ Synthetic | Regional DC                          | Mid-network stocking echelon.                                                                                                                                                                                       | US-based synthetic facility generation                                         |                            |
| `customer_dc_master`           | ðŸ”´ Synthetic | Customer DC (CFC)                    | Customer-facing fulfillment center; last echelon before the B2B customer.                                                                                                                                           | US-based synthetic facility generation                                         |                            |
| `network_lanes`                | ðŸ”´ Synthetic | Edge / Lane                          | Single unified edge table for every hop in the network (Plant->PlantDC, PlantDC->RegionalDC, RegionalDC->CustomerDC). Doubles as the Neo4j edge list.                                                               | Synthetic, lead time/cost calibrated to APQC & F&B benchmarks                  |                            |
| `b2b_customer_master`          | ðŸ”´ Synthetic | Customer                             | B2B Customer (banner/distributor). The retail banner or distributor the CPG company ships to. NOT the same entity as customer_master.csv (individual shopper).                                                      | Synthetic, ~3-5 banners                                                        |                            |
| `store_to_customer_map`        | ðŸ”´ Synthetic | Bridge                               | Maps each of the 30 real stores up to a B2B banner, so POS demand can roll up to customer-level orders.                                                                                                             | Synthetic assignment over real store_master                                    |                            |
| `customer_dc_to_customer_map`  | ðŸ”´ Synthetic | Bridge                               | Many-to-many CFC->Customer relationship; ~80% forced to 1:1 per sponsor guidance, remainder deliberately many-to-many.                                                                                              | Synthetic assignment                                                           |                            |
| `store_master`                 | ðŸŸ¢ Real      | Retail Store                         | 30 stores, 12 South Asian cities. Existing retail_clean_dataset table â€“ geographically decoupled from the new US-based upstream network by design.                                                                | `retail_clean_dataset/store_master.csv`                                        |                            |
| `sku_master`                   | ðŸŸ¢ Real      | SKU                                  | 5,000 SKUs with category/subcategory/brand/pricing.                                                                                                                                                                 | `retail_clean_dataset/sku_master.csv`                                          |                            |
| `sku_supply_attributes`        | ðŸ”´ Synthetic | SKU                                  | SKU extension (synthetic). Adds shelf life, criticality, segment flags â€“ fields sku_master doesn't have.                                                                                                          | Synthetic, keyed to real sku_id, category-anchored                             |                            |
| `cost_parameters`              | ðŸ”´ Synthetic | Category                             | Category lookup holding cost %, cost of capital % multiplier.                                                                                                                                                       | Synthetic, calibrated to CPG holding-cost benchmarks                           |                            |
| `price_elasticity`             | ðŸ”µ Derived   | SKU / category                       | Elasticity coefficient. NOT randomly generated â€“ estimated via regression on real discount_pct/quantity variation already in sales_transactions.                                                                  | Derived: regression on retail_clean_dataset/sales_transactions.csv             |                            |
| `sku_customer_service_targets` | ðŸ”´ Synthetic | SKU                                  | Policy target service level / case fill rate by SKU/segment.                                                                                                                                                        | Synthetic policy assumption, calibrated to CPG fill-rate benchmarks            |                            |
| `safety_stock_policy_upstream` | ðŸ”´ Synthetic | Plant DC / Regional DC / Customer DC | Current-state safety stock baseline above the store echelon â€“ doesn't exist anywhere today.                                                                                                                       | Synthetic baseline                                                             |                            |
| `inventory_snapshot`           | ðŸŸ¢ Real      | Store-SKU                            | Store-level stock on hand, reorder point, safety stock. Single echelon only (store).                                                                                                                                | `retail_clean_dataset/inventory_snapshot.csv`                                  |                            |
| `customer_master`              | ðŸŸ¢ Real      | Individual shopper                   | End-consumer/loyalty table. Different entity from b2b_customer_master â€“ do not conflate.                                                                                                                          | `retail_clean_dataset/customer_master.csv`                                     |                            |
| `promotions`                   | ðŸŸ¢ Real      | Promotion                            | Promo campaigns with discount %, type, targeting.                                                                                                                                                                   | `retail_clean_dataset/promotions.csv`                                          |                            |
| `sales_transactions`           | ðŸŸ¢ Real      | POS transaction line                 | ~10M rows, 4 years, day/store/SKU/customer grain.                                                                                                                                                                   | `retail_clean_dataset/sales_transactions.csv`                                  |                            |
| `sku_inventory_flags`          | ðŸŸ¢ Real      | SKU anomaly flag                     | Ground-truth STOCKOUT_RISK / SLOW_MOVER answer key. Anomaly version only.                                                                                                                                           | `retail_contaminated_dataset/sku_inventory_flags.csv`                          |                            |
| `demand_forecast`              | ðŸ”´ Synthetic | SKU-Customer-Period                  | Forecast qty is synthetic noise applied on top of real/derived actual demand â€“ forecast error is a first-class input, not a data-quality flaw. Synthetic bias/noise layered on real sales_transactions aggregates |                                                                                |                            |
| `production_plan`              | ðŸ”´ Synthetic | Plant-SKU-Period                     | Planned production for pre-built (make-to-stock) SKUs.                                                                                                                                                              | Synthetic                                                                      |                            |
| `manufacturing_adherence`      | ðŸ”´ Synthetic | Plant-SKU-Period                     | Actual vs. planned production â€“ the supply-side variability source distinct from lead time.                                                                                                                       | Synthetic, calibrated to schedule-adherence benchmarks (90-100% healthy range) |                            |
## 4. Data Dictionary (by table)

### `plant_master` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `plant_id` | text | ðŸ”´ Synthetic | PK | Unique plant identifier | Synthetic sequence | |
| `plant_name` | text | ðŸ”´ Synthetic | | Plant display name | Synthetic | |
| `city` | text | ðŸ”´ Synthetic | | US city | Synthetic â€“ US geography per sponsor direction | |
| `state` | text | ðŸ”´ Synthetic | | US state | Synthetic | |
| `production_capacity_units_per_period` | number | ðŸ”´ Synthetic | | Manufacturing capacity constraint (per product line, not per SKU) | Synthetic | Assign per plant/product line â€“ CPG capacity is rarely SKU-specific |
| `no_inventory_flag` | boolean | ðŸ”´ Synthetic | | Always TRUE â€“ plant holds no stock | Synthetic (fixed) | Structural rule confirmed by sponsor: plant is production-only |

### `plant_dc_master` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `plant_dc_id` | text | ðŸ”´ Synthetic | PK | Unique plant DC identifier | Synthetic sequence | |
| `plant_id` | text | ðŸ”´ Synthetic | FK | Parent plant (1 plant DC per plant) | Synthetic | |
| `city` | text | ðŸ”´ Synthetic | | US city, typically co-located with parent plant | Synthetic | |
| `state` | text | ðŸ”´ Synthetic | | US state | Synthetic | |
| `storage_capacity_units` | number | ðŸ”´ Synthetic | | Warehouse capacity constraint | Synthetic | First stock-holding echelon in the network |

### `regional_dc_master` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `regional_dc_id` | text | ðŸ”´ Synthetic | PK | Unique regional DC identifier | Synthetic sequence | |
| `dc_name` | text | ðŸ”´ Synthetic | | Display name | Synthetic | |
| `city` | text | ðŸ”´ Synthetic | | US city | Synthetic | |
| `state` | text | ðŸ”´ Synthetic | | US state / census region | Synthetic | Suggest 4-6 regional DCs clustered by US census region |
| `storage_capacity_units` | number | ðŸ”´ Synthetic | | Warehouse capacity constraint | Synthetic | |

### `customer_dc_master` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `customer_dc_id` | text | ðŸ”´ Synthetic | PK | Unique customer DC / CFC identifier | Synthetic sequence | |
| `dc_name` | text | ðŸ”´ Synthetic | | Display name | Synthetic | |
| `city` | text | ðŸ”´ Synthetic | | US city | Synthetic | |
| `state` | text | ðŸ”´ Synthetic | | US state | Synthetic | |
| `storage_capacity_units` | number | ðŸ”´ Synthetic | | Warehouse capacity constraint | Synthetic | |

### `network_lanes` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `lane_id` | text | ðŸ”´ Synthetic | PK | Unique lane identifier | Synthetic sequence | |
| `origin_node_id` | text | ðŸ”´ Synthetic | FK | References plant_id / plant_dc_id / regional_dc_id | Synthetic | |
| `origin_node_type` | text | ðŸ”´ Synthetic | | PLANT / PLANT_DC / REGIONAL_DC | Synthetic | |
| `destination_node_id` | text | ðŸ”´ Synthetic | FK | References plant_dc_id / regional_dc_id / customer_dc_id | Synthetic | |
| `destination_node_type` | text | ðŸ”´ Synthetic | | PLANT_DC / REGIONAL_DC / CUSTOMER_DC | Synthetic | |
| `transport_mode` | text | ðŸ”´ Synthetic | | Truck / Rail / Intermodal | Synthetic | |
| `lead_time_mean_days` | number | ðŸ”´ Synthetic | | Mean transit lead time for the lane | Synthetic, benchmark-calibrated | Domestic/regional lanes ~3-7 days; longer lanes ~10-20 days |
| `lead_time_std_days` | number | ðŸ”´ Synthetic | | Lead time standard deviation for the lane | Synthetic, benchmark-calibrated | CV 0.2-0.4 domestic/regional, 0.4-0.6 longer lanes |
| `distance_miles` | number | ðŸ”´ Synthetic | | Lane distance | Synthetic, from US city coordinates | |
| `cost_per_unit` | number | ðŸ”´ Synthetic | | Logistics/transport cost per unit shipped | Synthetic | |
| `capacity_units_per_period` | number | ðŸ”´ Synthetic | | Lane availability constraint | Synthetic | Only lanes that should exist are synthesized â€“ absence of a lane IS the constraint |

### `b2b_customer_master` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `b2b_customer_id` | text | ðŸ”´ Synthetic | PK | Unique banner/distributor identifier | Synthetic sequence | |
| `customer_name` | text | ðŸ”´ Synthetic | | Banner/distributor display name | Synthetic, ~3-5 banners | |
| `channel_type` | text | ðŸ”´ Synthetic | | RT (Retail) / Brick-and-Mortar | Synthetic | Confirm RT vs brick-and-mortar definition; target ~70/30 split per sponsor |
| `region` | text | ðŸ”´ Synthetic | | US region served | Synthetic | |
### `store_to_customer_map` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `store_id` | text | ðŸŸ¢ Real / ðŸ”´ Synthetic | FK | References real store_master.store_id | `retail_clean_dataset/store_master.csv` | Key is real; the mapping/assignment itself is synthetic |
| `b2b_customer_id` | text | ðŸ”´ Synthetic | FK | References b2b_customer_master | Synthetic assignment | Each store assigned to exactly one banner |

### `customer_dc_to_customer_map` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `customer_dc_id` | text | ðŸ”´ Synthetic | FK | References customer_dc_master | Synthetic | |
| `b2b_customer_id` | text | ðŸ”´ Synthetic | FK | References b2b_customer_master | Synthetic | |
| `mapping_type` | text | ðŸ”´ Synthetic | | ONE_TO_ONE / MANY_TO_MANY | Synthetic | ~80% forced 1:1 per sponsor note; remainder deliberately many-to-many so the exception path gets exercised |

### `store_master` â€“ ðŸŸ¢ Real
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `store_id` | text | ðŸŸ¢ Real | PK | Unique store identifier | `retail_clean_dataset/store_master.csv` | |
| `store_name` | text | ðŸŸ¢ Real | | Store display name | `retail_clean_dataset/store_master.csv` | |
| `city` | text | ðŸŸ¢ Real | | City (South Asian geography) | `retail_clean_dataset/store_master.csv` | Geographically decoupled from the new US-based upstream network â€“ Flagged, not reconciled |
| `store_type` | text | ðŸŸ¢ Real | | Hypermarket/Supermarket/Convenience/Express | `retail_clean_dataset/store_master.csv` | |
| `opening_date` | date | ðŸŸ¢ Real | | Store opening date | `retail_clean_dataset/store_master.csv` | |

### `sku_master` â€“ ðŸŸ¢ Real
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `sku_id` | text | ðŸŸ¢ Real | PK | Unique product identifier | `retail_clean_dataset/sku_master.csv` | |
| `sku_name` | text | ðŸŸ¢ Real | | Product display name | `retail_clean_dataset/sku_master.csv` | |
| `category` | text | ðŸŸ¢ Real | | Top-level category | `retail_clean_dataset/sku_master.csv` | |
| `subcategory` | text | ðŸŸ¢ Real | | Subcategory | `retail_clean_dataset/sku_master.csv` | |
| `unit_price` | number | ðŸŸ¢ Real | | Retail selling price | `retail_clean_dataset/sku_master.csv` | |
| `cost_price` | number | ðŸŸ¢ Real | | Cost to retailer | `retail_clean_dataset/sku_master.csv` | |
| `brand` | text | ðŸŸ¢ Real | | Brand | `retail_clean_dataset/sku_master.csv` | |

### `sku_supply_attributes` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `sku_id` | text | ðŸ”´ Synthetic | FK | References real sku_master.sku_id | `retail_clean_dataset/sku_master.csv` | |
| `shelf_life_days` | number | ðŸ”´ Synthetic | | Perishability window | Synthetic, anchored to sku_master.category | Fresh categories: days; packaged: months/years; non-food: N/A |
| `criticality_tier` | text | ðŸ”´ Synthetic | | Strategic importance tier | Synthetic, composite of margin x volume + manual override flags | Add deliberate overrides (e.g. new launches) so it isn't 100% mechanical |
| `segment` | text | ðŸ”´ Synthetic | | Fast-moving / Seasonal / Intermediate | Derived tagging over real sales_transactions history, calibrated to ~50/30/20 mix | Should reflect real observed volume/seasonality, not random assignment |
| `seasonal_flag` | boolean | ðŸ”´ Synthetic | | Regular vs seasonal demand pattern | Derived from real 4-yr transaction history | Cheap to get right â€“ seasonality is already present in the real data |
| `make_to_stock_flag` | boolean | ðŸ”´ Synthetic | | Pre-built (MTS) vs make-to-order | Synthetic, correlated with fast-moving segment | |
### `cost_parameters` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `category` | text | ðŸ”´ Synthetic | FK | References sku_master.category | `retail_clean_dataset/sku_master.csv` | |
| `holding_cost_pct_annual` | number | ðŸ”´ Synthetic | | Annual holding/carrying cost as % of cost_price | Synthetic, benchmark-calibrated | ~30-35%/yr perishable, ~20-25%/yr shelf-stable |
| `cost_of_capital_pct` | number | ðŸ”´ Synthetic | | Working capital cost of capital rate | Synthetic assumption, pending sponsor validation | |
| `stockout_cost_multiplier` | number | ðŸ”´ Synthetic | | Penalty factor applied to lost margin on stockout | Synthetic assumption | |

### `price_elasticity` â€“ ðŸ”µ Derived
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `sku_id_or_category` | text | ðŸ”µ Derived | FK | SKU or category+price-tier key | `retail_clean_dataset/sku_master.csv` | |
| `elasticity_coefficient` | number | ðŸ”µ Derived | | Own-price elasticity estimate | Derived: regression on real discount_pct/quantity in sales_transactions | Do not fabricate â€“ real promo variation exists (~21% of txns). Benchmarks: staples -0.3 to -0.8, mainstream FMCG -1.5 to -2.5, elastic/value tier -2.5 to -3.5 |
| `estimation_method` | text | ðŸ”µ Derived | | Model used (e.g. log-log regression) | Documentation field | |

### `sku_customer_service_targets` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `segment_or_sku_id` | text | ðŸ”´ Synthetic | FK | References sku_supply_attributes.segment or sku_id | Synthetic policy | |
| `service_level_target_pct` | number | ðŸ”´ Synthetic | | Target cycle service level | Synthetic policy, benchmark-informed | 97%+ top tier, ~90% tail â€“ do not anchor to 99% aspirational ceiling |
| `case_fill_rate_target_pct` | number | ðŸ”´ Synthetic | | Target case fill rate (CFR) | Synthetic policy, benchmark-informed | Real-world achieved CPG median is ~89.7% (GMA/BCG study) â€“ use as current-state baseline, not target |

### `safety_stock_policy_upstream` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `echelon_type` | text | ðŸ”´ Synthetic | | PLANT_DC / REGIONAL_DC / CUSTOMER_DC | Synthetic | |
| `node_id` | text | ðŸ”´ Synthetic | FK | References the relevant DC master table | Synthetic | |
| `sku_id` | text | ðŸ”´ Synthetic | FK | References real sku_master.sku_id | `retail_clean_dataset/sku_master.csv` | |
| `current_safety_stock_units` | number | ðŸ”´ Synthetic | | Current-state baseline (doesn't exist upstream of the store today) | Synthetic baseline for before/after comparison | |

### `inventory_snapshot` â€“ ðŸŸ¢ Real
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `store_id` | text | ðŸŸ¢ Real | FK | References store_master | `retail_clean_dataset/inventory_snapshot.csv` | |
| `sku_id` | text | ðŸŸ¢ Real | FK | References sku_master | `retail_clean_dataset/inventory_snapshot.csv` | |
| `stock_on_hand` | number | ðŸŸ¢ Real | | Current units in stock (store echelon) | `retail_clean_dataset/inventory_snapshot.csv` | |
| `reorder_point` | date | ðŸŸ¢ Real | | Reorder trigger level | `retail_clean_dataset/inventory_snapshot.csv` | |
| `safety_stock` | number | ðŸŸ¢ Real | | Current safety stock buffer (store echelon only) | `retail_clean_dataset/inventory_snapshot.csv` | Only echelon with a real current-state baseline |
| `last_restock_date` | date | ðŸŸ¢ Real | | Most recent restock | `retail_clean_dataset/inventory_snapshot.csv` | |

### `customer_master` â€“ ðŸŸ¢ Real
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `cust_id` | text | ðŸŸ¢ Real | PK | Unique shopper identifier | `retail_clean_dataset/customer_master.csv` | Individual end-consumer = distinct entity from b2b_customer_master |
| `age` | number | ðŸŸ¢ Real | | Shopper age | `retail_clean_dataset/customer_master.csv` | |
| `gender` | text | ðŸŸ¢ Real | | Shopper gender | `retail_clean_dataset/customer_master.csv` | |
| `city` | text | ðŸŸ¢ Real | | Shopper city | `retail_clean_dataset/customer_master.csv` | |
| `loyalty_segment` | text | ðŸŸ¢ Real | | Bronze/Silver/Gold/Platinum | `retail_clean_dataset/customer_master.csv` | |
| `preferred_channel` | text | ðŸŸ¢ Real | | In-Store/Online/Mobile App | `retail_clean_dataset/customer_master.csv` | Does not map cleanly to sponsor's RT vs brick-and-mortar split â€“ confirm before reusing |
| `registration_date` | date | ðŸŸ¢ Real | | Loyalty registration date | `retail_clean_dataset/customer_master.csv` | |
### `promotions` â€“ ðŸŸ¢ Real
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `promo_id` | text | ðŸŸ¢ Real | PK | Unique promo identifier | `retail_clean_dataset/promotions.csv` | |
| `promo_name` | text | ðŸŸ¢ Real | | Campaign name | `retail_clean_dataset/promotions.csv` | |
| `start_date` | date | ðŸŸ¢ Real | | Promo start | `retail_clean_dataset/promotions.csv` | |
| `end_date` | date | ðŸŸ¢ Real | | Promo end | `retail_clean_dataset/promotions.csv` | |
| `discount_pct` | number | ðŸŸ¢ Real | | Discount percentage | `retail_clean_dataset/promotions.csv` | |
| `promo_type` | text | ðŸŸ¢ Real | | Pct Discount/Flat/BOGO/Bundle/Clearance | `retail_clean_dataset/promotions.csv` | |
| `target_type` | text | ðŸŸ¢ Real | | Category/Brand/SKU/All | `retail_clean_dataset/promotions.csv` | |
| `target_value` | text | ðŸŸ¢ Real | | Specific target value | `retail_clean_dataset/promotions.csv` | |

### `sales_transactions` â€“ ðŸŸ¢ Real
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `date` | date | ðŸŸ¢ Real | | Transaction date | `retail_clean_dataset/sales_transactions.csv` | |
| `unit_price` | number | ðŸŸ¢ Real | | Price at time of sale | `retail_clean_dataset/sales_transactions.csv` | Base unit_price is fixed per SKU; all price variation comes through discount_pct/promotions |
| `total_value` | number | ðŸŸ¢ Real | | quantity x unit_price x (1-discount_pct/100) | `retail_clean_dataset/sales_transactions.csv` | |
| `channel` | text | ðŸŸ¢ Real | | In-Store/Online/Mobile App | `retail_clean_dataset/sales_transactions.csv` | |
| `discount_pct` | number | ðŸŸ¢ Real | | Discount applied (0 if none) | `retail_clean_dataset/sales_transactions.csv` | |
| `promo_id` | text | ðŸŸ¢ Real | FK | References promotions, blank if none | `retail_clean_dataset/sales_transactions.csv` | ~21% of txns carry a promo, 5-50% discount range (verified) |

### `sku_inventory_flags` â€“ ðŸŸ¢ Real
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `sku_id` | text | ðŸŸ¢ Real | FK | Flagged product | `retail_contaminated_dataset/sku_inventory_flags.csv` | |
| `flag` | text | ðŸŸ¢ Real | | STOCKOUT_RISK / SLOW_MOVER | `retail_contaminated_dataset/sku_inventory_flags.csv` | Direct match for Digital Twin's required failure signals |
| `affected_stores` | text | ðŸŸ¢ Real | | |-separated store_id list | `retail_contaminated_dataset/sku_inventory_flags.csv` | |
| `window_start` | date | ðŸŸ¢ Real | | Stockout window start (blank for SLOW_MOVER) | `retail_contaminated_dataset/sku_inventory_flags.csv` | |
| `window_end` | date | ðŸŸ¢ Real | | Stockout window end (blank for SLOW_MOVER) | `retail_contaminated_dataset/sku_inventory_flags.csv` | |
| `notes` | text | ðŸŸ¢ Real | | Plain-language flag reason | `retail_contaminated_dataset/sku_inventory_flags.csv` | |

### `demand_forecast` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `sku_id` | text | ðŸ”´ Synthetic | FK | References real sku_master.sku_id | `retail_clean_dataset/sku_master.csv` | |
| `b2b_customer_id` | text | ðŸ”´ Synthetic | FK | References b2b_customer_master | Synthetic | |
| `forecast_date` | date | ðŸ”´ Synthetic | | Forecast period | Synthetic | |
| `forecast_qty` | number | ðŸ”´ Synthetic | | Forecasted demand | Synthetic: actual demand (real, aggregated) + deliberate bias/noise | Calibrate so realized MAPE ~8-12% fast-movers, ~15-20% intermediate, ~25-40% long tail |
| `actual_qty` | number | ðŸ”µ Derived | | Realized demand for the same period | Derived: aggregated from real sales_transactions via store_to_customer_map | |
| `forecast_error_pct` | number | ðŸ”µ Derived | | (forecast-actual)/actual | Derived calculation | |

### `production_plan` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `plant_id` | text | ðŸ”´ Synthetic | FK | References plant_master | Synthetic | |
| `sku_id` | text | ðŸ”´ Synthetic | FK | References real sku_master.sku_id (MTS SKUs only) | `retail_clean_dataset/sku_master.csv` | |
| `planned_date` | date | ðŸ”´ Synthetic | | Planned production date | Synthetic | |
| `planned_qty` | number | ðŸ”´ Synthetic | | Planned production quantity | Synthetic, driven off demand_forecast for make-to-stock SKUs | |

### `manufacturing_adherence` â€“ ðŸ”´ Synthetic
| Field | Type | Status | Key | Description | Source / Generation Method | Notes / Benchmark Assumption |
| --- | --- | --- | --- | --- | --- | --- |
| `plant_id` | text | ðŸ”´ Synthetic | FK | References plant_master | Synthetic | |
| `sku_id` | text | ðŸ”´ Synthetic | FK | References real sku_master.sku_id | `retail_clean_dataset/sku_master.csv` | |
| `period` | date | ðŸ”´ Synthetic | | Production period | Synthetic | |
| `planned_production_units` | number | ðŸ”´ Synthetic | | From production_plan | Synthetic (linked) | |
| `actual_production_units` | number | ðŸ”´ Synthetic | | Simulated actual output | Synthetic: planned x adherence_pct | Model adherence ~92-97% typical with a fat left tail for disruption events |
| `adherence_pct` | number | ðŸ”´ Synthetic | | actual/planned | Synthetic, benchmark-calibrated | Track separately from any customer-facing OTIF/fill-rate metric â€“ the two can diverge |
## 5. Knowledge Graph Schema (Neo4j)

### Node Types
| Node Label | Source Table | Key Properties |
| --- | --- | --- |
| Plant | `plant_master` | plant_id, city, state, production_capacity_units_per_period |
| PlantDC | `plant_dc_master` | plant_dc_id, city, state, storage_capacity_units |
| RegionalDC | `regional_dc_master` | regional_dc_id, city, state, storage_capacity_units |
| CustomerDC | `customer_dc_master` | customer_dc_id, city, state, storage_capacity_units |
| Customer | `b2b_customer_master` | b2b_customer_id, customer_name, channel_type |
| Store | `store_master (real)` | store_id, city, store_type |
| SKU | `sku_master (real) + sku_supply_attributes` | sku_id, category, segment, criticality_tier, shelf_life_days |

### Edge Types
| Edge (relationship) | Source Table | Key Properties |
| --- | --- | --- |
| (Plant)-[:PRODUCES]->(SKU) | `production_plan` | planned_qty, planned_date |
| (Plant)-[:SHIPS_TO]->(PlantDC) | `network_lanes` | lead_time_mean_days, lead_time_std_days, transport_mode, cost_per_unit, capacity_units_per_period |
| (PlantDC)-[:SHIPS_TO]->(RegionalDC) | `network_lanes` | same as above |
| (RegionalDC)-[:SHIPS_TO]->(CustomerDC) | `network_lanes` | same as above |
| (CustomerDC)-[:SERVES]->(Customer) | `customer_dc_to_customer_map` | mapping_type (ONE_TO_ONE / MANY_TO_MANY) |
| (Store)-[:BELONGS_TO]->(Customer) | `store_to_customer_map` | |
| (Store)-[:STOCKS]->(SKU) | `inventory_snapshot (real)` | stock_on_hand, reorder_point, safety_stock |
| (SKU)-[:FLAGGED_AS]->(SKU) | `sku_inventory_flags (real)` | flag, window_start, window_end â€“ model as SKU property or self-referential flag node |

## 6. Benchmark Research: Synthetic Parameter Assumptions
Researched CPG/F&B industry ranges used to calibrate the synthetic parameters above, rather than arbitrary numbers. None of these are client-specific facts â€“ label them as assumptions pending sponsor validation.

| Parameter                             | What the research shows                                                                                                                                                                                                                                                                                                                        | Recommended synthetic assumption                                                                                                                                                                                                                                                                        |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Lead time variability                 | No single published CPG-specific figure exists â€“ APQC's cross-industry data shows top performers averaging 4 days supplier lead time vs. 11 days for bottom performers, with food/perishables domestic lead times typically 2-7 days. Standard practice tracks mean, P90, and coefficient of variation (CV = SD/mean) rather than a flat SD. | Assign lead time by lane and category rather than one global number: domestic/regional lanes ~3-7 day mean with CV 0.2-0.4; longer or imported lanes ~10-20 day mean with CV 0.4-0.6. Treat the original '2 days' note as a possible mean for short domestic lanes specifically, not a global constant. |
| Manufacturing adherence variability   | Industry sources treat 90-100% schedule attainment as healthy; below 90% signals real production problems. Schedule adherence (did the plant execute its own plan) and OTIF (did the customer get what was promised) are tracked separately and can diverge.                                                                                   | Model as attainment ~92-97% most of the time, with a fat left tail producing occasional worse misses (75-90%) to represent real disruption events â€“ not a symmetric normal distribution. Track separately from any customer-facing fill-rate metric.                                                  |
| Holding/carrying cost                 | CPG industry average clusters at 20-30% of inventory value per year. Perishable food & beverage runs 30-40%; shelf-stable CPG runs 18-25%.                                                                                                                                                                                                     | Use category-differentiated rates: ~30-35%/year for short-shelf-life/perishable SKUs, ~20-25%/year for shelf-stable/mainstream SKUs.                                                                                                                                                                    |
| Service level / case fill rate        | Aspirational targets in grocery/CPG literature cite 95-99%, but a GMA/BCG benchmarking study of 30+ US CPG manufacturers found a median *achieved* on-time case fill rate of only 89.7%.                                                                                                                                                       | Don't set the "current state" baseline at 99% â€“ that's the aspirational ceiling, not reality. Use ~88-91% as the realistic current-state achieved fill rate, with differentiated targets by segment (97%+ top tier, ~90% tail) representing the improvement opportunity.                              |
| Price elasticity                      | Meta-analysis puts mainstream FMCG brand-level elasticity at -1.5 to -2.5; premium tiers -0.8 to -1.5; value/private label -2.5 to -3.5. Staples like fruits/vegetables are most inelastic (-0.26 to -0.8); sugar-sweetened beverages/alcohol most elastic (-0.7 to -3.9). Brand-level elasticity runs 2-3x category-level.                    | Assign elasticity by category and price tier rather than one system-wide number â€“ staple/private-label SKUs toward -2.5 to -3.5, premium/differentiated SKUs toward -0.8 to -1.5, everything else in between.                                                                                         |
| Forecast error ("1st point of error") | CPG typically targets 10-25% MAPE at SKU-week level (top performers under 10% on high-velocity items); overall monthly accuracy runs 75-85% for important SKUs, notably worse for the long tail. SKU-level MAPE can be 38x higher than the same data aggregated to category level.                                                             | Generate forecast = actual demand Â± noise calibrated so realized MAPE lands around 8-12% for the fast-moving segment, 15-20% for intermediate, and 25-40% for the long tail/seasonal segment.                                                                                                          |
### Sources
* [Average supplier lead time in days on purchased materials | APQC](https://www.apqc.org/resources/benchmarking/open-standards-benchmarking/measures/average-supplier-lead-time-days)
* [Logistics Key Benchmarks: Consumer Products/Packaged Goods Industry | APQC](https://www.apqc.org/resource-library/resource/logistics-key-benchmarks-consumer-products-packaged-goods-industry)
* [Manufacturing Key Benchmarks: Consumer Products/Packaged Goods Industry | APQC](https://www.apqc.org/resource-library/resource/manufacturing-key-benchmarks-consumer-products-packaged-goods-industry)
* [AI-Driven Optimization of Procurement Lead Time Variability in Food and Beverage Manufacturing](https://www.planettogether.com/blog/ai-driven-optimization-of-procurement-lead-time-variability-in-food-and-beverage-manufacturing)
* [Why Food Production Schedules Break Down Faster Than Other Industries](https://softengine.com/why-food-production-schedules-break-down-faster-than-other-industries)
* [The True Cost of Inventory: A Financial Breakdown for CPG Brands](https://cfoanalytics.com/cfo-vckt/cpg/the-true-cost-of-inventory-a-financial-breakdown-for-cpg-brands/)
* [Inventory Carrying Cost by Vertical: 2025 Benchmarks | Eightv](https://eightx.co/blog/average-inventory-carrying-cost-by-vertical)
* [How CPG Supply Chains Are Preparing for Seismic Change | BCG](https://www.bcg.com/publications/2018/consumer-packaged-goods-supply-chains-preparing-seismic-change)
* [Service Level vs. Fill Rate: Understanding the Difference | Planster](https://www.planster.io/blog/service-level-vs-fill-rate)
* [Price Elasticity of Demand: Formula & CPG Benchmarks | RGM Academy](https://rgmacademy.app/concepts/price-elasticity)
* [Commodity and Food Elasticities | USDA Economic Research Service](https://www.ers.usda.gov/data-products/commodity-and-food-elasticities)
* [Forecast Accuracy Benchmarks: What's Good Enough? | Planster](https://www.planster.io/blog/forecast-accuracy-benchmarks)
* [Demand Forecast Accuracy: Metrics, Tools & Industry Benchmarks | EasyReplenish]