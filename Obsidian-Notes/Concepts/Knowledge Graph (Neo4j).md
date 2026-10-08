The **Knowledge Graph** layer of [[Supply Brain]] models the physical and commercial network as a **Neo4j** graph: nodes (Plant, PlantDC, RegionalDC, CustomerDC, Customer, Store, SKU) connected by typed, attributed edges (`PRODUCES`, `SHIPS_TO`, `SERVES`, `BELONGS_TO`, `STOCKS`, `FLAGGED_AS`).

**Why the underlying table register was designed with the graph in mind from the start.** Every hop in the network — Plant→Plant DC, Plant DC→Regional DC, Regional DC→Customer DC — lives in a **single unified `network_lanes` edge table**, rather than one table per echelon pair. That's a deliberate schema choice: a single table with `origin_node_type` / `destination_node_type` columns maps directly onto Neo4j's edge model, so the same table doubles as the graph's edge list without any reshaping.

**Node and edge summary:**
| Node | Source table | Key properties |
|---|---|---|
| SKU | `sku_master` (real) + `sku_supply_attributes` | category, segment, criticality_tier, shelf_life_days |
| Store | `store_master` (real) | city, store_type |
| Customer | `b2b_customer_master` | customer_name, channel_type |

| Edge | Source table | Key properties |
|---|---|---|
| `(Plant)-[:SHIPS_TO]->(PlantDC)` and onward | `network_lanes` | lead_time_mean_days, lead_time_std_days, transport_mode, cost_per_unit |
| `(Store)-[:STOCKS]->(SKU)` | `inventory_snapshot` (real) | stock_on_hand, reorder_point, safety_stock |
| `(SKU)-[:FLAGGED_AS]->(SKU)` | `sku_inventory_flags` (real) | flag (STOCKOUT_RISK / SLOW_MOVER), window dates |

**Why a graph, not just relational tables.** Questions the system needs to answer routinely are graph-shaped — "which SKUs at this Customer DC are fed by a lane with high lead-time variability?" or "if this Plant DC's capacity is constrained, which downstream Regional DCs and Customer DCs are exposed?" — and are naturally expressed as graph traversals rather than multi-way joins across a growing number of relational tables as the network scales.

**Two entities that must not be conflated.** `customer_master` (real) is the individual end-consumer/loyalty shopper; `b2b_customer_master` (synthetic) is the retail banner or distributor the CPG company ships to. They are structurally different node types in the graph, connected to entirely different parts of the network.

Related: [[Supply Brain]], [[Synthetic-First Data Strategy]], [[Multi-Echelon Inventory Optimization (MEIO)]]
