# Supply Brain — data layout

*Auto-generated 2026-10-08 18:19 by
`supply_brain_generator_v5.py`. Do not edit by hand.*

The line between the two folders is **time**.

```
input datasets/      THE PAST AND THE GIVENS — read-only
                     Network and product masters, cost structure, the order
                     history, the promotions that ran, the published forecast,
                     the disruption library, and the inventory record of what
                     actually happened. 26 tables.

output datasets/     THE FORWARD VIEW — one folder per run
                     The forward policy, the position the horizon opens on,
                     and every scenario run with its summaries.
```

The historical `inventory.csv`, `replenishment_receipts.csv` and
`production.csv` sit in `input datasets/` even though the digital twin produces
them: they are the record of the past, which is input to any analysis. Only
what looks *forward* moves to `output datasets/`. The generator prints a
warning if a forward artefact ever appears in the input folder.

The horizon boundary is derived, never hardcoded to a calendar year. History is
the span the order history covers (2023-W01..2025-W52,
156 weeks) and the forward horizon is everything after it
(2026-W01..2026-W52, 52 weeks).
Change `START_YEAR` or the week counts in the generator and every label,
filename and split follows.

---

## Where the detail lives

| Document | Written by | Covers |
|---|---|---|
| `input datasets/DATASET_DOCUMENTATION.md` | `supply_brain_generator_v5.py` | Every input table, the business assumptions behind it, how each layer is generated, the policy dictionary, and a data profile |
| `output datasets/OUTPUT_DOCUMENTATION.md` | `models/twin/digital_twin.py` | What the twin does, its event order and assumptions, and the schema of every artefact a run produces |

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

forward scenario  twin (forward mode)               ─► output datasets/<scenario>/
                     --policy <forward_policy.csv>
                     --state  <opening_position.csv>
```

Build the dataset:

```bash
python supply_brain_generator_v5.py --stage all --run-name history
```

Score a forward scenario against it:

```bash
python models/twin/digital_twin.py --mode forward --run-name baseline     --policy "output datasets/history/forward_policy.csv"     --state  "output datasets/history/opening_position.csv"
```

Stages run independently: `--stage world` rebuilds the given data, `--stage
history` re-simulates the past against it. `--skip-docs` omits validation,
documentation and the data profile.

---

## Conventions

| Field | Meaning |
|---|---|
| `period` | ISO week label `YYYY-Www` |
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

One standalone CSV, five required columns:

```
dc_id, sku_id, safety_stock, reorder_point, order_up_to_level
```

That is the only thing an optimizer changes. Extra columns are ignored, so an
optimizer's native output can be handed over verbatim. Opening state comes from
a separate `--state` file and is never touched by a policy swap, so two runs
start from an identical physical position and the difference between them
measures the decision rather than the starting point.

Full dictionary: Part 5 of `input datasets/DATASET_DOCUMENTATION.md`.
