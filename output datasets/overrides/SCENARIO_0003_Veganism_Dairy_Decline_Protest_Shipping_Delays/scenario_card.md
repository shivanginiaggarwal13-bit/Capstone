# SCENARIO_0003_Veganism_Dairy_Decline_Protest_Shipping_Delays -- scenario card

**Request:** I foresee a decline in dairy demand by 25 percent due to the rise in the Veganism movement. I also expect there to be shipping delays due to rise in protests across the country.

**Hypothesis:** If dairy demand structurally falls ~25% (veganism) while protests lengthen transit lead times, how much does re-planning with MEIO (prepared) improve service and cost versus letting today's policy absorb both changes (unprepared), and is the dairy-only impact material enough to act on now?

**Compared against:** `SCENARIO_0002_MEIO_CLARK_SCARF_V2/meio_policy` (seed 2026; MEIO as defined in the config: `models/MEIO/MEIO_Weekly/final_submission_optimized/roll_forward_policy.py`)

**Runs:** prepared, unprepared -- prepared = MEIO re-plans with the plan-side changes; unprepared = today's policy meets the actual-side changes.

## Overrides

| ID | Measure | Change | Scope | Weeks | MEIO plans for it | Twin simulates it | Why |
|---|---|---|---|---|---|---|---|
| OVR-0001 | `demand.volume` | x0.75 | category=Dairy & Bakery; subcategory=Fresh Dairy, Powdered Milk | all | yes | yes | structural decline in Fresh Dairy / Powdered Milk demand (~25%) due to the rise of the veganism movement |
| OVR-0002 | `supply.lead_time_mean` | x1.1 | all | 2026-W43..2026-W48 | yes | yes | nationwide lane lead-time increase (+10%) from rising protests across the country, 2026-W43..W48, per planner request |
| OVR-0003 | `supply.lead_time_std` | x1.1 | all | 2026-W43..2026-W48 | yes | yes | nationwide lane lead-time variability increase (+10%) from rising protests across the country, 2026-W43..W48, per planner request |

## Size of each change (inputs, not outcomes)

- **OVR-0001** -82,179 forecast units (-25.0% of scope, -0.62% of network demand in window); -350,325 USD at list
- **OVR-0002** 76 lane(s), 100.0% of network demand moves over them
- **OVR-0003** 76 lane(s), 100.0% of network demand moves over them

## Engine support

Ready: engine 1.0 applies every measure in this scenario.

## Approval

config sha256 `836617182500ff11f9bd472956ff1d749c754695c7221ed636cbb8bbb2bc5e6b`

Approving runs MEIO and the Digital Twin for this scenario (minutes of compute) and writes new scenario folders under `output datasets/`. To approve:

```bash
python -m Agents.tools approve SCENARIO_0003_Veganism_Dairy_Decline_Protest_Shipping_Delays --by "<your name>"
```
