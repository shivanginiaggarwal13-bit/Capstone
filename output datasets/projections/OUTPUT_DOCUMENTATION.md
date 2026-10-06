# Supply Brain — output datasets

*Auto-generated 2026-10-06 20:50 by
`models/twin/digital_twin.py`. Do not edit by hand.*

## Scope

This document covers **`output datasets/` only** — the forward view. Everything
here is produced by the digital twin: the position the planning horizon opens
on, the policy each run executed, and the inventory, production and service
that policy caused.

It does **not** cover the given data. That is documented in
`input datasets/DATASET_DOCUMENTATION.md`, written by the generator.
`SCHEMA.md` at the project root explains how the two folders divide.

The dividing line is **time**, not authorship: the twin also writes the
*historical* inventory record, and that lives in `input datasets/` because it
is the past. Only forward-looking output lands here.

---

## Part 1 — What the twin is

A discrete-event simulation of the four-echelon network:

```
Plant --0.5d--> Plant DC --> Regional DC --> Customer DC
```

The plant is production-only and holds no stock. Plant DCs are the first
stock-holding echelon and are **pushed** finished goods by production, not
pulled by a reorder point. Regional DCs carry both their own direct
bulk-customer demand and the demand of the customer DCs beneath them. Regional
and customer DCs **pull** on a weekly reorder-point review.

It is the project's only physics engine. The same code generates the historical
record and scores candidate policies, so the history a policy is measured
against and the outcome it is scored on come from identical mechanics. Two
separate implementations would disagree by a few points of fill rate, and every
optimization result would then carry an unanswerable question about whether the
gain was real.

Design follows ISOMORPH (github.com/tuhinsahai/ISOMORPH): one step is one day,
a fixed event order inside each step, an explicit in-transit pipeline keyed by
arrival day, and a run directory that describes itself.

### What happens in a day

Every simulated day runs the same six steps in the same order:

1. **Receive** — shipments whose arrival day is today land and become on-hand.
2. **Clear backlog** — outstanding backorders are filled from what just arrived.
3. **Serve customer demand** — from stock on hand. A shortfall at a customer DC
   is a **lost sale** (margin forgone); at a plant or regional DC it
   **backorders**.
4. **Review** (weekly) — every pulling node compares its inventory position
   against its reorder point and places an order up to its order-up-to level.
   Plants run their production plan in the same step.
5. **Fulfil** — parents ship against the open order book. This runs *every*
   day, not only on review day: a parent picks across the week as stock frees
   up. When it cannot cover everything its children asked for, the remainder is
   split **pro rata**.
6. **Snapshot** — on-hand is accumulated for the week's holding cost.

At week close, any internal order still open is cancelled and booked as
internal shortfall; the child re-orders at the next review. That is what makes
`demand_qty = shipments + unmet_qty` hold on every published row.

### Modes

| Mode | Horizon | Demand | Opening state |
|---|---|---|---|
| `history` | the span the order history covers | replayed from `orders.csv` | cold start, zero stock |
| `forward` | the planning horizon | forecast x a forecast-error ratio resampled from in-sample history | from `--state` |

### Assumptions

Every run records these in its own `run_manifest.json`.

1. Plants build to a rolling plan (demand signal + restore ~1.5 weeks of finished-goods cover), clipped by production-family line capacity then plant-wide capacity; executed quantity = plan x schedule adherence drawn uniformly in [0.90, 1.00].
2. Plant DCs are PUSHED stock by production; only RDCs and CDCs pull on a weekly (R,s,S) review with R = 1 week.
3. Lead times are drawn per order line from each lane's own mean and std in network_lanes.csv, so every receipt arrives on its own realised transit rather than the average. The committed lead time quoted to the child is that lane's ~90th percentile.
4. CDC shortfalls are lost sales; PDC/RDC shortfalls backorder, following the fulfilment_type split in the dataset, which is clean by node type.
5. An internal replenishment order the parent cannot cover is cancelled, not backlogged; the child re-orders at the next weekly review.
6. A parent holds back its own expected demand for the remainder of the review period before releasing stock downstream, implementing 'customer orders served first'.
7. Routing comes from sku_sourcing_map.csv, an input table derived from network structure: each RDC draws a SKU from the nearest plant DC whose plant is qualified to make it, and each CDC from its min-lead-time inbound RDC. It is not mined from simulated receipts.
8. Holding cost = (storage_cost_per_unit_year + carrying_pct x unit_cost) / 52, keyed on (dc_id, subcategory).
9. No shelf life, obsolescence or write-offs; no warehouse or lane capacity limits. Production capacity DOES bind.
10. Demand, lead-time and production RNG streams are independent, so two runs at the same seed differ only by the policy under test.

---

## Part 2 — The optimization seam

One standalone CSV, five required columns:

```
dc_id, sku_id, safety_stock, reorder_point, order_up_to_level
```

Those five are all the twin reads. **Extra columns are ignored**, so an
optimizer's native output — cluster id, target service level, arm label,
anything — can be handed over verbatim. A missing required column raises an
error rather than quietly defaulting to zero.

```bash
python models/twin/digital_twin.py --mode forward --run-name meio_v2     --policy "output datasets/meio_v2/policy.csv"     --state  "output datasets/history/opening_position.csv"
```

**Comparison contract.** Two runs are comparable only when they share a seed
and an opening state and differ solely in the policy. `--policy` and `--state`
are separate arguments precisely so a policy swap cannot move the starting
position. Demand, lead-time and production random draws come from independent
streams, so changing the policy does not shift the demand the policy faces.

Echelon positioning needs no structural input: "push buffer to the customer
DCs" is simply a higher CDC order-up-to level and a lower RDC one, in the same
table.

Vary `--seed` for replicates of one policy. A bandit layer needs this — it has
to average reward over several demand and lead-time draws before it can tell
two arms apart.

### Standardized scenario runs

Pass `--scenario-config` to create a self-describing scenario folder named
`SCENARIO_####_Name`. The JSON file must contain `scenario_name` and a
machine-readable `changes` object. It may also contain `scenario_number`,
`description`, and a `details` object for ownership, hypotheses, assumptions,
or other relevant context. If `scenario_number` is omitted, the next available
number is assigned automatically.

```bash
python models/twin/digital_twin.py --mode forward \
    --scenario-config models/twin/scenario_config.example.json \
    --policy "output datasets/history/forward_policy.csv" \
    --state "output datasets/history/opening_position.csv"
```

The run writes `scenario_config.json`, `run_manifest.json`, and all simulation
artefacts into the same standardized folder. The resolved execution settings
are added to the saved scenario config, while the original `--run-name` flow
continues to work for existing automation.

The policy table's full column-by-column dictionary is Part 5 of
`input datasets/DATASET_DOCUMENTATION.md`.

---

## Part 3 — Runs present

| Folder | Mode | Seed | Label | Size |
|---|---|---|---|---|
| `baseline/` | forward | 2026 | baseline | 85.06 MB |
| `SCENARIO_0999_MEIO_Weekly_Rolling_52W/` | forward | 2026 | SCENARIO_0999_MEIO_Weekly_Rolling_52W | 89.82 MB |

A run folder is self-describing: `run_manifest.json` records the exact knobs,
seed, input paths and assumption set, so two folders can be diffed without
reference to the code that produced them.

---

## Part 4 — Artefact reference

### `opening_position.csv` — history runs only

The position the forward horizon opens on, which is the terminal state of
history. This is what a forward run consumes via `--state`.

| Column | Meaning |
|---|---|
| `period`, `week_start_date` | the first week of the forward horizon |
| `dc_id`, `sku_id` | location and product |
| `opening_stock` | units on hand at the cut-over |
| `in_transit_qty` | units already dispatched but not yet arrived |
| `in_transit_eta_week_start` | the week that pipeline stock lands |
| `inventory_position` | `opening_stock + in_transit_qty` — what a reorder point is compared against |
| `safety_stock`, `reorder_point`, `order_up_to_level` | policy, for reference |
| `lead_time_*`, `cum_lead_time_*` | inbound lane and plant-to-node totals |

Built off the terminal inventory rather than the forward policy, so a
(location, SKU) pair the forward policy does not cover still carries its real
stock across the cut-over instead of having it silently destroyed.

### `forward_policy.csv` — history runs only

The operating policy for the planning horizon, sized on the **forecast**. Same
five-column contract as above, plus `production_family`, `segment` and `z` for
reference. Its sibling `baseline_policy.csv` — sized on the order history and
used to simulate the past — lives in `input datasets/` because it ran the past.

### `policy.csv` — every run

Exactly the `(safety_stock, reorder_point, order_up_to_level)` that run
executed, normalised to the five-column contract. The audit trail: whatever was
fed in via `--policy`, this is what the engine actually used.

### `inventory.csv` — forward runs

Grain: **period x dc x sku**. The primary physics output.

| Column | Meaning |
|---|---|
| `opening_stock` | on hand at the start of the week |
| `receipts` | units received from the parent |
| `demand_qty` | total demand = customer + internal |
| `demand_customer_qty`, `demand_replen_qty` | external / internal split |
| `unmet_customer_qty` | external demand not served from stock |
| `unmet_replen_qty` | internal order balance still open at week close, then cancelled |
| `shipments` | units shipped to satisfy demand — customer served plus internal orders filled |
| `lost_margin_usd` | margin forgone on lost sales |
| `fulfilment_type` | `lost_sale` (customer DC) or `backorder` (plant / regional DC) |
| `closing_stock`, `backlog` | end-of-week position |
| `safety_stock`, `reorder_point`, `order_up_to_level` | the policy in force |
| `avg_on_hand` | mean daily on-hand — the holding-cost base |
| `holding_cost_usd` | `avg_on_hand x (storage + carrying% x unit_cost) / 52` |
| `lead_time_*`, `cum_lead_time_*` | inbound lane and plant-to-node |

`demand_qty = shipments + unmet_qty` holds on every row. Note that `demand_qty`
is an echelon total and counts a unit once per tier it passes through;
`demand_customer_qty` is the external figure that reconciles to real orders.

### `replenishment_receipts.csv` — forward runs

Grain: **one row per (ship day, lane, sku)**.

`receipt_id`, `period`, `order_week_start`, `sku_id`, `dc_id`,
`origin_node_id`, `qty_ordered`, `qty_received`, `promised_lead_days`,
`actual_lead_days`, `ship_day`, `arrival_day`, `on_time_flag`, `short_qty`.

Each order line gets its own realised transit time drawn from its lane's mean
and standard deviation — receipts arrive on their own draw, never the average.
`promised_lead_days` is the lane's ~90th percentile, the committed lead time
quoted to the child, which is what puts on-time delivery near 90% with a
genuine late tail rather than a coin flip. `short_qty` is the balance the parent
could not cover.

### `production.csv` — forward runs

Grain: **period x plant x sku**.

`plan_uncapped_qty` (what the rolling plan asked for) -> `planned_qty` (after
family line capacity, then plant-wide capacity) -> `actual_qty` (after schedule
adherence). `capacity_limited_flag` marks a clipped plant-week, and
`adherence_pct` records the attainment draw.

### `service_summary.csv` / `service_by_node.csv`

Per SKU and per node: demand, units served, unmet, customer fill rate, internal
fill rate, average on-hand and holding cost.

### `cost_summary.csv`

Per node: `avg_units_on_hand`, `avg_inventory_value_usd`, `holding_cost_usd`,
`stockout_cost_usd`, `transport_cost_usd`, `total_landed_cost_usd`.

`total_landed_cost_usd` is the scalar an optimizer minimises. Stockout cost is
margin-per-unit x units short, on the margin basis named in the manifest.

### `run_kpis.json`

Headline metrics for cross-run comparison: `customer_fill_rate` with its
customer-DC and regional-DC split, `pct_demand_weeks_in_full`,
`internal_replen_fill_rate`, inventory value, the three cost components, total
landed cost, and `margin_basis`.

**Read `pct_demand_weeks_in_full` alongside the fill rate.** Unit fill rate
counts a week served 90% as 0.9 of a unit fill; in-full counts it as a failed
order line. The two typically differ by about 5 points, and if the objective is
on-time-in-full then in-full is the honest number.

### `scenario_config.json`

The normalized scenario contract. Required fields are `scenario_name` and
`changes`; `details` is a JSON object for relevant scenario context. The
`execution` object records the mode, seed, policy, state, horizon, and output
folder actually used.

### `run_manifest.json`

`schema_version`, `run_name`, `label`, `mode`, `generated_at`, `seed`,
`policy_source`, `state_source`, `margin_basis`, `runtime_seconds`,
`assumptions` in plain language, `build_notes` (resolved routing counts,
demand basis, realised-to-forecast ratio), and the complete `scenario` object
when the run was launched with `--scenario-config`.

### `acceptance.csv` — history runs only

Each operating metric against its declared target band, with a pass flag. The
physics is fixed; when a metric misses, the policy knobs in the generator are
what move.

---

## Part 5 — Known limitations

- **No shelf life, obsolescence or write-offs.** Shelf life exceeds the
  simulated span by design, so perishability cannot bind and write-offs are nil.
  Any result here will not transfer directly to a chilled or frozen network.
- **No warehouse or lane capacity limits.** Production capacity *does* bind.
- **No lateral transshipment and no failover.** The lane table declares
  regional-DC-to-regional-DC lanes and second inbound lanes, but no flow is
  routed across them.
- **Single-sourcing per (node, SKU).** Routing is one parent per pair, taken
  from `sku_sourcing_map.csv`.
- **Disruption scenarios are not applied.** The library is generated but no run
  currently overlays it.
- **Demand is aggregated to (node, SKU, day).** Forward mode synthesises demand
  from the forecast, so there are no discrete order lines and no true
  order-level on-time-in-full; `pct_demand_weeks_in_full` is measured at
  node-SKU-week grain, which is coarser than an order line.
