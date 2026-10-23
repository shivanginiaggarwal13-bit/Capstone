# Supply Brain — output datasets

*Auto-generated 2026-10-23 20:59 by `write_output_documentation()` in
`models/twin/digital_twin.py`, which both twins call. Do not edit by hand.*

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

Two implementations share these mechanics. The original, `models/twin/digital_twin.py`,
reads CSV and writes CSV. The optimized `digital_twin_fast.py` is the one
`scenario_config.json`'s `digital_twin.entrypoint` runs. It reads Parquet inputs, writes
Parquet detail tables and adds the scenario override engine. Differences are noted
where they matter.

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
2. **Clear backlog** — outstanding backorders are filled from *all* on-hand
   stock, not only from what just arrived. Units used to clear backlog are not
   counted in `shipments`.
3. **Serve customer demand** — from stock on hand. A shortfall at a customer DC
   is a **lost sale** (margin forgone); at a plant DC or regional DC it
   **backorders**.
4. **Review** (weekly, on the first day of the week) — every active pulling
   pair (RDC/CDC with a policy row) computes its inventory position as
   `on_hand − backlog + on_order`. If that is at or below its reorder point, it
   orders up to its order-up-to level. Plants run their production plan in the
   same step, after the orders are placed.
5. **Fulfil** — parents ship against the open order book, plant DCs first,
   then regional DCs. This runs *every* day, not only on review day: a parent
   picks across the week as stock frees up. A parent releases only stock above
   its own policy `safety_stock`. When it cannot cover everything its children
   asked for, the rest is split **pro rata** to the quantities requested, and
   each grant is floored to whole units.
6. **Snapshot** — end-of-day on-hand is accumulated for the week's holding cost.

At week close, any internal order still open is cancelled and booked as
internal shortfall; the child re-orders at the next review. That is what makes
`demand_qty = shipments + unmet_qty` hold on every published row.

### Modes

| Mode | Horizon | Demand | Opening state |
|---|---|---|---|
| `history` | the span the order history covers (156 weeks by default) | replayed from `orders` at delivery date | cold start, zero stock |
| `forward` | the planning horizon (52 weeks from 2026-W01 by default) | forecast x a forecast-error ratio resampled from in-sample history, weighted by volume and capped at 10, split over 7 days | from `--state` (original: a cold start if omitted; optimized: defaults to the config's `digital_twin.state`) |

`--horizon-weeks` overrides either default.

### Assumptions

Every run records this list (the `ASSUMPTIONS` constant in
`models/twin/digital_twin.py`, which the optimized twin imports) in its own
`run_manifest.json`. All 12 run manifests present (top level and one level down) were
written with an earlier wording of this list, so their `assumptions` differ from the
text below.

1. Plants build to a rolling plan (demand signal + restore 2.5 weeks of finished-goods cover, `FG_COVER_WEEKS = 2.5`), clipped by production-family line capacity then plant-wide capacity; executed quantity = plan x schedule adherence drawn uniformly in [0.90, 1.00]. The demand signal is an EMA (alpha 0.25, starting at 0) of the internal orders placed on the plant's DC. Both the signal and the position are multiplied by the plant's `allocation_share` (see Part 5).
2. Plant DCs are PUSHED stock by production; only RDCs and CDCs pull on a weekly (R,s,S) review with R = 1 week.
3. Lead times are drawn per order line from each lane's own mean and std in network_lanes, as max(0.5, Normal(mean, max(std, 0.05))), so every receipt arrives on its own realised transit rather than the average. Arrival day = ship day + max(1, ceil(draw)). The committed lead time quoted to the child is that lane's ~90th percentile (mean + 1.2816 x std).
4. CDC shortfalls are lost sales; PDC/RDC shortfalls backorder, following the fulfilment_type split in the dataset, which is clean by node type.
5. An internal replenishment order the parent cannot cover is cancelled, not backlogged; the child re-orders at the next weekly review.
6. A parent holds back its own policy safety_stock for the current week before releasing stock downstream, implementing 'customer orders served first' (`reserve_own_demand=True`). Its realised customer demand is already served earlier in the day. Plant DCs have no customers, so their safety stock is never released.
7. Routing comes from sku_sourcing_map.csv, an input table derived from network structure: each RDC draws a SKU from the nearest plant DC whose plant is qualified to make it, and each CDC from its min-lead-time inbound RDC. It is not mined from simulated receipts.
8. Holding cost = (storage_cost_per_unit_year + carrying_pct x unit_cost) / 52, keyed on (dc_id, subcategory).
9. No shelf life, obsolescence or write-offs; no warehouse or lane capacity limits. Production capacity DOES bind.
10. Demand, lead-time and production RNG streams are independent. Two runs at the same seed therefore face identical demand. The lead-time stream is consumed once per shipped SKU line, so it stays in step only while both runs ship the same lines. The production stream (one adherence draw per producing plant per week) stays aligned.

---

## Part 2 — The optimization seam

One standalone table, five required columns. The original twin reads it as CSV; the
optimized twin reads CSV or Parquet by file suffix:

```
dc_id, sku_id, safety_stock, reorder_point, order_up_to_level
```

Those five are all the twin reads. **Extra columns are ignored**, so an
optimizer's native output — cluster id, target service level, arm label,
anything — can be handed over verbatim. A missing required column raises an
error rather than quietly defaulting to zero. An optional `period` column makes
the policy time-varying: a pair with no row in a week keeps its last seen values,
and weeks before its first row are 0. A (node, SKU) with no policy row is
*inactive*. It never orders and has no `inventory` row, but it still faces
customer demand and accrues holding and stockout cost.

```bash
python models/twin/digital_twin.py --mode forward --run-name meio_v2 \
    --policy "output datasets/meio_v2/policy.csv" \
    --state  "output datasets/history/opening_position.csv"
```

**Comparison contract.** Two runs are comparable only when they share a seed
and an opening state and differ solely in the policy. `--policy` and `--state`
are separate arguments precisely so a policy swap cannot move the starting
position. Demand, lead-time and production random draws come from independent
streams, so changing the policy does not shift the demand the policy faces. Lead-time
draws do diverge once two policies ship different lines (see assumption 10).

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

If `changes` contains `demand_surge_pct`, it is actually APPLIED to the
simulated demand (see `scenario_engine.py` and Part 5's known limitations) --
scaled for `affected_region` (or an explicit `affected_nodes` list) over
`duration_weeks` weeks starting `start_week_offset` weeks into the horizon
(both optional; default to the whole horizon starting immediately). Any
other `changes` key is saved for context but has no simulation effect yet.

```bash
python models/twin/digital_twin.py --mode forward \
    --scenario-config models/twin/scenario_config.example.json \
    --policy "output datasets/history/forward_policy.csv" \
    --state "output datasets/history/opening_position.csv"
```

The run writes `scenario_config.json`, `run_manifest.json`, and all simulation
artefacts into the same standardized folder. The resolved execution settings
are added to the saved scenario config, while the original `--run-name` flow
continues to work for existing automation. Without `--scenario-config`, the
original writes to `<out-root>/<run-name>/`, or to `<out-root>/projections/baseline/`
when the run name is `baseline`.

The paragraphs above describe the original twin. **The optimized twin
(`digital_twin_fast.py`) differs:**

- It always runs under a scenario config: `--scenario-config`, or the repo-root
  `scenario_config.json` by default. Any of `--run-name`, `--mode`, `--seed`,
  `--policy`, `--state`, `--data-dir` and `--out-root` that you omit comes from the
  config's `digital_twin` block; `--state` is defaulted only in forward mode.
- The run folder is `<output_root>/SCENARIO_####_Name/<run_name>/`. A raw copy of the
  config also goes in `SCENARIO_####_Name/`.
- `changes.overrides` (schema `models/overrides/override.schema.json`) is compiled by
  the override engine, and the *actual*-side overrides are applied in forward mode.
  These cover economics (unit cost, margin, holding rate, storage cost, logistics
  cost), demand volume and volatility, opening on-hand and in-transit stock, lane
  lead-time mean and std, lane outages, plant/family capacity and schedule adherence. A
  receipt goes to `<overrides.output_root>/SCENARIO_####_Name/receipts/twin_<run_name>.json`.
- Legacy `demand_surge_pct` keys still go through `scenario_engine.py`. Any other
  unknown `changes` key raises an error instead of being ignored, and any `changes`
  in history mode stop the run.
- `--lean-outputs` skips the three large detail tables. `--skip-output-documentation`
  leaves this file alone.

The policy table's full column-by-column dictionary is Part 5 of
`input datasets/DATASET_DOCUMENTATION.md`.

---

## Part 3 — Runs present

| Folder | Mode | Seed | Label | Size |
|---|---|---|---|---|
| `agent output/` | ? | ? | ? | 0.02 MB |
| `history/` | history | 2026 | 2023-W01..2025-W52 history | 1.34 MB |
| `MEIO/` | ? | ? | ? | 27.24 MB |
| `MEIO artifacts/` | ? | ? | ? | 0.07 MB |
| `overrides/` | ? | ? | ? | 0.05 MB |
| `projections/` | ? | ? | ? | 84.46 MB |
| `SCENARIO_0001_Baseline_Forward_Plan/` | ? | ? | ? | 9.50 MB |
| `SCENARIO_0002_MEIO_CLARK_SCARF_V2/` | ? | ? | ? | 45.41 MB |
| `SCENARIO_0003_Veganism_Dairy_Decline_Protest_Shipping_Delays/` | ? | ? | ? | 45.40 MB |
| `SCENARIO_0004_Veganism_Dairy_Decline_Protest_Shipping_Delays/` | ? | ? | ? | 45.39 MB |
| `segmentation/` | ? | ? | ? | 0.69 MB |

The table reads only a `run_manifest.json` sitting directly in each top-level folder. A
`SCENARIO_####_Name/` folder written by the optimized twin keeps its runs one level
down, in `<run_name>/`, so it shows `?`. The original twin likewise writes a run named
`baseline` to `projections/baseline/`. Folders holding runs one level down here:
`projections/`, `SCENARIO_0001_Baseline_Forward_Plan/`,
`SCENARIO_0002_MEIO_CLARK_SCARF_V2/`,
`SCENARIO_0003_Veganism_Dairy_Decline_Protest_Shipping_Delays/` and
`SCENARIO_0004_Veganism_Dairy_Decline_Protest_Shipping_Delays/` (run folders
`baseline/`, `counterfactual_no_scenario/`, `counterfactual_reactive_no_scenario/`,
`meio_policy/`, `prepared/`, `reactive/` and `unprepared/`). Not every folder here is a
twin run: `agent output/`, `MEIO/`, `MEIO artifacts/`, `overrides/` and `segmentation/`
come from other tools (no `run_manifest.json` at either level).

A run folder is self-describing: `run_manifest.json` records the exact knobs,
seed, input paths and assumption set, so two folders can be diffed without
reference to the code that produced them.

---

## Part 4 — Artefact reference

The artefacts below marked "history runs only" (`opening_position`, `forward_policy`,
`acceptance`) are written by the dataset generator (`supply_brain_generator_v5.py`)
into `output datasets/history/` around its history-mode twin run, not by the twin
itself. That run's inventory, receipts and production go to `input datasets/`, and
their run-folder copies are deleted. File names below use the original twin's `.csv`
names. The optimized twin writes `inventory`, `replenishment_receipts`, `production`
and `policy` as `.parquet`, keeps `cost_summary`, `service_summary` and
`service_by_node` as `.csv`, and with `--lean-outputs` skips the first three.

### `opening_position.csv` — history runs only

The position the forward horizon opens on, which is the terminal state of
history. This is what a forward run consumes via `--state`.

| Column | Meaning |
|---|---|
| `period`, `week_start_date` | the first week of the forward horizon |
| `dc_id`, `sku_id` | location and product |
| `opening_stock` | units on hand at the cut-over |
| `in_transit_qty` | units already dispatched but not yet arrived |
| `in_transit_eta_week_start` | date of the earliest pending arrival (blank if none); the twin lands the whole `in_transit_qty` on that day |
| `inventory_position` | `opening_stock + in_transit_qty`. The twin's review uses `on_hand − backlog + on_order`. That equals this column at the cut-over only because a forward run opens with zero backlog: history's terminal backlog, open orders and production EMA are not carried over |
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

The `(safety_stock, reorder_point, order_up_to_level)` the run used, normalised
to the five-column contract, one row per active (policy) pair. For a
time-varying policy (with a `period` column) this is only the **first-week
snapshot**. The full input is recorded at `build_notes.policy_source` in
`run_manifest.json`.

### `inventory.csv` — forward runs

Grain: **period x dc x sku**, active (policy) pairs only. The primary physics output.

| Column | Meaning |
|---|---|
| `period`, `week_start_date`, `sku_id`, `dc_id` | keys |
| `opening_stock` | on hand at the start of the week |
| `receipts` | units received from the parent (from the plant, for a plant DC) |
| `demand_qty` | total demand = customer + internal |
| `demand_customer_qty`, `demand_replen_qty` | external / internal split; internal demand is booked at the parent |
| `unmet_qty` | `unmet_customer_qty + unmet_replen_qty` |
| `unmet_customer_qty` | external demand not served from stock on the day it arrived (RDC backorders count here even if filled later) |
| `unmet_replen_qty` | internal order balance still open at week close, then cancelled |
| `shipments` | computed as `demand_qty − unmet_qty` (customer served plus internal orders filled); units used to clear backlog are not included |
| `lost_margin_usd` | margin forgone on lost sales (customer DCs only) |
| `fulfilment_type` | `lost_sale` (customer DC) or `backorder` (plant / regional DC) |
| `closing_stock` | end-of-week on hand. No backlog column is written |
| `safety_stock`, `reorder_point`, `order_up_to_level` | the policy in force that week |
| `avg_on_hand` | mean end-of-day on-hand over the week — the holding-cost base |
| `holding_cost_usd` | `avg_on_hand x (storage + carrying% x unit_cost) / 52` |
| `lead_time_mean_days`, `lead_time_std_days`, `cum_lead_time_mean_days`, `cum_lead_time_std_days` | inbound lane and plant-to-node |

`demand_qty = shipments + unmet_qty` holds on every row by construction. A stock
identity (`opening + receipts − shipments = closing`) does not hold on rows where
backlog was cleared. Note that `demand_qty` is an echelon total and counts a unit once
per tier it passes through; `demand_customer_qty` is the external figure that reconciles
to real orders.

### `replenishment_receipts.csv` — forward runs

Grain: **one row per (ship day, lane, sku)**, including plant-to-plant-DC
production shipments, whose `origin_node_id` is `PLANT` and `short_qty` 0.

`receipt_id`, `period`, `order_week_start`, `sku_id`, `dc_id`,
`origin_node_id`, `qty_ordered`, `qty_received`, `promised_lead_days`,
`actual_lead_days`, `ship_day`, `arrival_day`, `on_time_flag`, `short_qty`.

Each order line gets its own realised transit time drawn from its lane's mean
and standard deviation — receipts arrive on their own draw, never the average.
`actual_lead_days` is that continuous draw (rounded to 2 dp), while
`arrival_day = ship_day + max(1, ceil(draw))`. `promised_lead_days` is the lane's
~90th percentile (mean + 1.2816 x std), the committed lead time quoted to the child.
`on_time_flag` compares the *continuous* draw with the promise, so on-time
delivery sits near 90% by construction, whatever the policy. `period` and
`order_week_start` are the ship week. `short_qty` is the requested balance the parent
could not cover on that ship day, and `qty_ordered = qty_received + short_qty`.
Both repeat the outstanding balance on every partial-ship day, so summing them
overstates. In the optimized twin, a dispatch into a lane closed by a
`supply.lane_outage` override departs the day after the outage ends. That wait is
in `arrival_day` but not in `actual_lead_days` or `on_time_flag`.

### `production.csv` — forward runs

Grain: **period x plant x sku**, for rows where the plan asked for more than zero.
Columns: `period`, `week_start_date`, `plant_id`, `sku_id`, `production_family`,
`is_primary`, `plan_uncapped_qty`, `planned_qty`, `actual_qty`, `adherence_pct`,
`capacity_limited_flag`.

`plan_uncapped_qty` (what the rolling plan asked for) -> `planned_qty` (after
family line capacity, then plant-wide capacity) -> `actual_qty` (after schedule
adherence, floored). `capacity_limited_flag` marks a plant-week clipped by the
**plant-wide** limit; a family-line clip alone does not set it. `adherence_pct`
records the U(0.90, 1.00) draw. In the optimized twin, a `supply.schedule_adherence`
override multiplies that draw, capped at 100%, but `adherence_pct` still shows the
raw draw. `is_primary` marks the plant with the largest `allocation_share` for the
SKU; ties produce more than one primary.

### `service_summary.csv` / `service_by_node.csv`

Per SKU and per node, over active pairs only: demand, units served, unmet, customer
fill rate, internal fill rate, average on-hand and holding cost.

- `service_summary` columns: `sku_id`, `total_demand`, `unmet`,
  `replen_demand`, `replen_unmet`, `avg_on_hand`, `holding_cost_usd`,
  `served_from_stock`, `fill_rate_customer`, `fill_rate_replen`, `category`.
- `service_by_node` columns: `dc_id`, `node_type`, `skus`, `total_demand`, `unmet`,
  `replen_demand`, `replen_unmet`, `avg_on_hand`, `holding_cost_usd`,
  `fill_rate_customer`, `fill_rate_replen`.

### `cost_summary.csv`

Per node: `dc_id`, `node_type`, `avg_units_on_hand`, `avg_inventory_value_usd`,
`holding_cost_usd`, `stockout_cost_usd`, `transport_cost_usd`,
`total_landed_cost_usd`, sorted by total landed cost, highest first.

`total_landed_cost_usd` is the scalar an optimizer minimises:
holding + stockout + transport. The three cost components accumulate over **every**
(node, SKU) cell, inactive pairs included. The two inventory columns come from the
`inventory` rows, which cover active pairs only.

- **Holding** is charged on end-of-day on-hand. In-transit (pipeline) stock is not
  charged.
- **Stockout cost** is margin-per-unit (`margin_usd`, the margin basis named in the
  manifest) x units short. It is charged at every node, including RDC backorders that
  are filled later, so it does not reconcile with `lost_margin_usd` (CDC lost sales
  only).
- **Transport** is qty x lane `logistics_cost_per_unit_usd`, charged to the receiving
  node. Plant-to-plant-DC freight is not included.

### `run_kpis.json`

Headline metrics for cross-run comparison: `horizon_weeks`, `first_period`,
`last_period`, `customer_demand_units`, `customer_fill_rate` with its
customer-DC and regional-DC split (`customer_fill_rate_cdc`,
`customer_fill_rate_rdc`), `pct_demand_weeks_in_full`,
`internal_replen_fill_rate`, `avg_units_on_hand_network`,
`avg_inventory_value_usd`, `safety_stock_value_usd`, the three cost components,
`total_landed_cost_usd`, `pct_weeks_at_zero_stock` and `margin_basis`.

- **Fill rates** are unit-based and computed over active pairs only.
  `customer_fill_rate` = 1 − unmet / demand for external demand, measured on the day
  of demand, so an RDC backorder counts as unmet even if it is filled later.
  `internal_replen_fill_rate` = 1 − cancelled / requested internal orders, measured at
  the parent.
- **Cost KPIs** cover every cell (see `cost_summary`).
- **`safety_stock_value_usd`** values the policy's safety stock (the mean over weeks
  for a time-varying policy) at unit cost. Plant-DC safety stock, which is never
  released, is included.
- **`pct_weeks_at_zero_stock`** counts all active node-SKU-weeks, including those with
  no demand.

**Read `pct_demand_weeks_in_full` alongside the fill rate.** It is the share of
node-SKU-weeks with customer demand and zero unmet units. Unit fill rate counts a week
served 90% as 0.9 of a unit fill; in-full counts it as a failed order line. The gap
varies by run: 92.7% vs 86.4% on `history/`, but 81.2% vs 81.4% on
`SCENARIO_0001_Baseline_Forward_Plan/baseline` (the largest and smallest gaps among the
12 runs with a `run_kpis.json` present). If the objective is on-time-in-full, in-full is
the honest number.

### `scenario_config.json`

The normalized scenario contract. Required fields are `scenario_name` and
`changes`; `details` is a JSON object for relevant scenario context. The
`execution` object records the mode, seed, policy, state, horizon, and output
folder actually used. The original twin writes it only with `--scenario-config`.
The optimized twin always writes it into the run folder, and also puts a raw copy of
the input config in the parent `SCENARIO_####_Name/` folder.

### `run_manifest.json`

`schema_version`, `run_name`, `label`, `mode`, `generated_at`, `seed`,
`policy_override` (the `--policy` argument), `margin_basis`, `runtime_seconds`,
`assumptions` in plain language (the list in Part 1),
`build_notes`, and the complete `scenario` object when the run was launched with a
scenario config. `build_notes` holds `policy_source`, `policy_rows`,
`policy_time_varying`, `state_source`, resolved routing counts, demand basis,
realised-to-forecast ratio and network sizes. It also holds `scenario_applied` for a
legacy demand surge, and `overrides_applied` for optimized-twin override runs. The
optimized twin adds `lean_outputs.not_written` when run with `--lean-outputs`.

### `acceptance.csv` — history runs only

Written by the generator. Each operating metric (`metric`, `observed`, `lo`,
`hi`, `pass`) against its declared target band. The physics is fixed; when a metric
misses, the policy knobs in the generator are what move.

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
- **Scenario support differs by implementation.**
  - **Original twin**: only `demand_surge_pct` is applied (optionally
    `affected_region`/`affected_nodes`, `duration_weeks`, `start_week_offset` -- see
    `scenario_engine.py`). Any other key in `changes` is saved to the
    self-describing scenario folder for context but has no simulation effect.
  - **Optimized twin**: also applies the override engine's actual-side overrides
    (economics, demand volume/volatility, opening state, lead times, lane outages,
    plant capacity, adherence) in forward mode. None of these override paths is
    covered by the twin equivalence test.
- **The demand-surge multiplier is rounded per daily cell** (half-to-even), so for
  slow movers the realised surge can be below nominal or zero.
- **Production allocation share is applied on top of routing.** The plant DC's
  demand signal already covers only the RDCs routed to it, and the plan then
  multiplies both that signal and the DC's position by the plant's
  `allocation_share`. Each plant therefore plans `allocation_share` x the quantity
  its own DC's signal implies. For SKUs split across plants this is likely a
  double count (open audit item; behaviour shown as coded).
- **Common random numbers hold for demand only** (see assumption 10).
- **Plant-DC `reorder_point` / `order_up_to_level` are never used**; a plant DC's
  policy affects the run only through its safety-stock hold-back.
- **No terminal-state accounting.** Stock still in transit at the end of the
  horizon is never received or valued.
- **Demand is aggregated to (node, SKU, day).** Forward mode synthesises demand
  from the forecast, so there are no discrete order lines and no true
  order-level on-time-in-full; `pct_demand_weeks_in_full` is measured at
  node-SKU-week grain, which is coarser than an order line.
