# SCENARIO_0005_SE_Beverage_July_Surge_25pct -- scenario card

**Request:** What if Southeast beverage demand rises 25% in July?

**Hypothesis:** A 25% July demand surge in Southeast Beverages will strain Southeast DC stock and PDC01 replenishment, lowering CDC/RDC fill rates in the unprepared arm; a prepared run (MEIO anticipates) recovers most of the lost service at the cost of higher inventory.

**Compared against:** `SCENARIO_0002_MEIO_CLARK_SCARF_V2/meio_policy` (seed 2026; MEIO as defined in the config: `models/MEIO/MEIO_Weekly/final_submission/roll_forward_policy.py`)

**Runs:** prepared, unprepared -- prepared = MEIO re-plans with the plan-side changes; unprepared = today's policy meets the actual-side changes.

## Overrides

| ID | Measure | Change | Scope | Weeks | MEIO plans for it | Twin simulates it | Why |
|---|---|---|---|---|---|---|---|
| OVR-0001 | `demand.volume` | x1.25 | category=Beverages; region=Southeast | 2026-W28..2026-W31 | yes | yes | Planned +25% Southeast beverage demand in July (seasonal peak): "What if Southeast beverage demand rises 25% in July?" Assumptions recorded: magnitude exactly +25% (scale 1.25); window = the 4 ISO weeks whose Monday falls in July 2026 (2026-W28..W31, 2026-07-06 to 2026-08-02); scope = all Beverages SKUs at all Southeast DCs (100 SKUs across RDC02, RDC03, CDC03–CDC06, 397 demand lines); visibility = both sides (planner did not say whether the business sees it coming, so MEIO plans for it and the Twin simulates it; the unprepared arm already covers the surprise case). Pure volume surge: no price event, no volatility change, no capacity change. |

## Size of each change (inputs, not outcomes)

- **OVR-0001** +7,345 forecast units (+25.0% of scope, +0.81% of network demand in window); +232,329 USD at list

## Engine support

Ready: engine 1.0 applies every measure in this scenario.

## Approval

config sha256 `b66f291779d4dc96390717aa1528ed9610d6f30a85fd5921ca3d869e0aed5ecf`

Approving runs MEIO and the Digital Twin for this scenario (minutes of compute) and writes new scenario folders under `output datasets/`. To approve:

```bash
python -m Agents.tools approve SCENARIO_0005_SE_Beverage_July_Surge_25pct --by "<your name>"
```
