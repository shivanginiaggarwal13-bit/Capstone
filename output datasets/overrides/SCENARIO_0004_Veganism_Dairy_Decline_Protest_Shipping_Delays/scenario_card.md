# SCENARIO_0004_Veganism_Dairy_Decline_Protest_Shipping_Delays -- scenario card

**Request:** I foresee a decline in dairy demand by 25 percent due to the rise in the Veganism movement. I also expect there to be shipping delays due to rise in protests across the country. | Dairy+Powdered Milk, assume delay to be for mean lead time to widen by 10 percent, and the protests to last for about 6 weeks. The protests are unrelated to the Vegan Movement, so assume structural change in milk consumption

**Hypothesis:** With a structural ~25% decline in Fresh Dairy + Powdered Milk demand and a 6-week +10% widening of mean lead times from protests, does the current policy hold customer fill rate and cost, or does it drive lost sales / inventory buildup versus baseline -- and does planning ahead (prepared) outperform reacting week by week (reactive) once the protests hit?

**Compared against:** `SCENARIO_0002_MEIO_CLARK_SCARF_V2/meio_policy` (seed 2026; MEIO as defined in the config: `models/MEIO/MEIO_Weekly/final_submission_optimized/roll_forward_policy.py`)

**Runs:** prepared, unprepared, reactive -- prepared = MEIO re-plans with the plan-side changes; unprepared = today's policy meets the actual-side changes; reactive = MEIO is not told and re-plans every week from what the Twin has simulated so far.

## Overrides

| ID | Measure | Change | Scope | Weeks | MEIO plans for it | Twin simulates it | Why |
|---|---|---|---|---|---|---|---|
| OVR-0001 | `demand.volume` | x0.75 | category=Dairy & Bakery; subcategory=Fresh Dairy, Powdered Milk | 2026-W01..2026-W52 | yes | yes | Veganism-driven structural decline in dairy/milk consumption. Planner foresees this trend and expects lower demand across Fresh Dairy and Powdered Milk throughout 2026. Applies to both MEIO planning and Twin simulation. |
| OVR-0002 | `supply.lead_time_mean` | x1.1 | all | 2026-W01..2026-W06 | yes | yes | Protest-driven nationwide widening of shipping lead times across all lanes, 6-week duration. Known disruption both planned and actual sides. Standalone override unrelated to separate dairy-demand decline (distinct from demand_agent changes). |

## Size of each change (inputs, not outcomes)

- **OVR-0001** -82,179 forecast units (-25.0% of scope, -0.62% of network demand in window); -350,325 USD at list
- **OVR-0002** 76 lane(s), 100.0% of network demand moves over them

## Engine support

Ready: engine 1.0 applies every measure in this scenario.

## Approval

config sha256 `3c9bc2555f88a1fbfd4e2ce28ab88bfe93826c96a110c00c885b9540b145ef6c`

Approving runs MEIO and the Digital Twin for this scenario (minutes of compute) and writes new scenario folders under `output datasets/`. To approve:

```bash
python -m Agents.tools approve SCENARIO_0004_Veganism_Dairy_Decline_Protest_Shipping_Delays --by "<your name>"
```
