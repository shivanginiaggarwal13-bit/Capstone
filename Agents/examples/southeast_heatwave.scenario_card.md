# SCENARIO_0003_Southeast_Heatwave -- scenario card

> **Example from 9 Oct 2026, generated before the override engine was installed. Kept to match `southeast_heatwave.scenario_config.json`; out of date in these ways (23 Oct):**
> - **Engine support.** The "Checks" warning no longer holds: `python -m Agents.tools readiness` reports all 19 measures supported. A card built today ends with "Ready: engine 1.0 applies every measure in this scenario."
> - **Runs.** A plan-and-actual scenario now also runs a **reactive** arm (MEIO is not told and re-plans every week from what the Twin has simulated), so today's `arms_for()` gives prepared, unprepared, reactive.
> - **Entrypoints.** The paired config names `models/MEIO/MEIO_Weekly/final_submission/{run_weekly_tools,meio_optimizer_weekly,roll_forward_policy}.py`, which are no longer in that folder, and the Twin `models/twin/digital_twin.py`, which ignores `changes.overrides` (it applies only the legacy `demand_surge_pct`). The current root config uses `final_submission_optimized/` and `digital_twin_fast.py`; rebuild the scenario from it (`build_scenario_config`) before running.
> - **Numbering.** SCENARIO_0003 is now taken by `SCENARIO_0003_Veganism_Dairy_Decline_Protest_Shipping_Delays`, so a rebuild gets the next free number.

**Request:** What happens if a July heatwave pushes Southeast beverage demand up 25%, and at the same time PLANT01's liquid-fill line loses 30% capacity for two weeks mid-July? We'd protect Fast-Moving SKUs at 98.5% through July.

**Hypothesis:** Re-planning for the known heatwave recovers most of the fill-rate loss for a modest increase in working capital

**Compared against:** `SCENARIO_0002_MEIO_CLARK_SCARF_V2/meio_policy` (seed 2026; MEIO as defined in the config: `models/MEIO/MEIO_Weekly/final_submission/roll_forward_policy.py`)

**Runs:** prepared, unprepared -- prepared = MEIO re-plans with the plan-side changes; unprepared = today's policy meets the actual-side changes.

## Overrides

| ID | Measure | Change | Scope | Weeks | MEIO plans for it | Twin simulates it | Why |
|---|---|---|---|---|---|---|---|
| OVR-0001 | `demand.volume` | x1.25 | region=Southeast; category=Beverages | 2026-W28..2026-W31 | yes | yes | Heatwave lifts Southeast beverage demand 25% through July (forecast by the business, so MEIO plans for it) |
| OVR-0002 | `supply.plant_capacity` | x0.7 | plant_id=PLANT01; production_family=LIQUID_FILL | 2026-W29..2026-W30 | yes | yes | PLANT01 liquid-fill line loses 30% capacity for two weeks from 13 July |
| OVR-0003 | `service.target_csl` | = 0.985 | segment=Fast-Moving | 2026-W28..2026-W31 | yes | no | Protect Fast-Moving SKUs at 98.5% cycle service through the July peak |

## Size of each change (inputs, not outcomes)

- **OVR-0001** +7,345 forecast units (+25.0% of scope, +0.81% of network demand in window); +232,329 USD at list
- **OVR-0002** 1 line(s); demand-equivalent load exceeds the new capacity on none (the cut may not bind)
- **OVR-0003** ~+118,178 USD safety stock (3850 DC-SKU pairs)

## Checks

- WARNING (set): the installed pipeline does not yet apply ['demand.volume', 'service.target_csl', 'supply.plant_capacity']; run_scenario will refuse until those engine phases land (see pipeline_readiness)



## Approval

config sha256 `1401ce6f6eaa6e618d3a9de1abe08d3e84ef498158d46a016446ee3d11334007`

Approving runs MEIO and the Digital Twin for this scenario (minutes of compute) and writes new scenario folders under `output datasets/`. To approve:

```bash
python -m Agents.tools approve SCENARIO_0003_Southeast_Heatwave --by "<your name>"
```
