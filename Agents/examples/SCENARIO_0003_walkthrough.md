# SCENARIO_0003 walkthrough: from a planner's sentence to MEIO and Twin results

This follows one scenario end to end:
- the request;
- the agents that turned it into overrides;
- the exact override JSON;
- the code in MEIO and the Digital Twin that reads each override and changes the numbers;
- the proof that it did;
- the results.

Everything below comes from the real run on 9 Oct 2026, stored in these folders:

| What | Where |
|---|---|
| Config, card, approval, run status and log, engine receipts | `output datasets/overrides/SCENARIO_0003_Southeast_Heatwave_July_Maintenance_Flood/` |
| The re-planned MEIO policy | `output datasets/MEIO/SCENARIO_0003_…/rolling_2026/meio_policy_rolling.parquet` |
| The two Twin runs | `output datasets/projections/SCENARIO_0003_…/{prepared,unprepared}/` |

> **Baseline changed after this run (9 Oct, later).** The shared config now runs Clark-Scarf v2 with tuned parameters (`meio.parameters`; see `models/MEIO/MEIO_Weekly/final_submission/README.md`), and SCENARIO_0002 was regenerated with it.
> - **This scenario** ran under MEIO Weekly: its config predates `meio.parameters`, and it still rebuilds as it did.
> - **What still holds:** the prepared-vs-unprepared numbers below, which compare its own two arms.
> - **What doesn't:** the "vs baseline" figures refer to the MEIO Weekly baseline in git (commit `6bca22d`).
> - **To compare with the new baseline,** rebuild the scenario from the current config. That changes its config hash, so it needs a new approval.

## 1. The request

> We expect a heatwave in the Southeast in July 2026 that should lift beverage demand by about 25% for the month, and the business will plan for it. PLANT01's liquid-fill line has planned maintenance starting 13 July and will run at 70% capacity for two weeks. Protect our fast-moving SKUs at 98.5% service during July. Also, a flood is expected to write off 30% of the opening stock at RDC03. What happens to service and working capital, and how much does re-planning help?

## 2. The agent flow

```
planner
  │  plain English
  ▼
orchestrator (Sonnet) ── create_draft, resolve_window
  │ delegate (one brief per lever)
  ├─► demand_agent          (Haiku) ── resolve_scope → propose_override  → OVR-0001
  ├─► supply_agent          (Haiku) ── resolve_scope → propose_override  → OVR-0002
  ├─► service_policy_agent  (Haiku) ── resolve_scope → propose_override  → OVR-0003
  └─► inventory_agent       (Haiku) ── resolve_scope → propose_override  → OVR-0004
  │      every proposal: validated against catalog + master data, previewed, nothing saved on error
  ├─► validator_agent       (Haiku) ── evidence node: list_overrides + validate_draft, then review
  │
  ├── build_scenario_config  → output datasets/overrides/SCENARIO_0003_…/scenario_config.json + card
  ├── request_human_approval → LangGraph interrupt(); a person approves → approval.json (config SHA-256)
  ├── run_scenario           → detached `run-job`: prepared arm, then unprepared arm
  │        prepared   = run_meio_then_twin.py: MEIO re-plans with the PLAN side → Twin runs the ACTUAL side
  │        unprepared = Twin only: baseline policy (SCENARIO_0002) meets the ACTUAL side
  ├── get_run_status
  └─► analyst_agent         (Haiku) ── get_application_receipts, compare_runs, service_breakdown, policy_diff
  ▼
final readout to the planner
```

The work ran as two LangGraph sessions on the Claude Desktop bridge. With no API key, each model call was answered by a Desktop subagent: Sonnet 5.5 for the orchestrator and Haiku 4.5 for the workers.

| Session | Calls | What happened |
|---|---|---|
| Authoring (see [live_test_2026-10-09.md](live_test_2026-10-09.md)) | 21 | The four specialists wrote the four overrides. Haiku used a wrong CSL unit once; the tool rejected it and saved nothing, and Haiku corrected it. Then came validation, the config and card, and approval through the interrupt. At that point `run_scenario` correctly **refused**, because the engine did not exist yet. |
| Run (this document) | 7 | 1. The orchestrator called `run_scenario`; the gate checked the SHA-256 and launched both arms. 2. The orchestrator called `get_run_status`. 3. The run had succeeded, so the orchestrator delegated to the analyst. 4–6. The analyst called its tools: 2 + 3 + 7 calls. 7. The orchestrator wrote the final readout. |

## 3. What an override looks like

The agents produce **only** this JSON, under `changes.overrides` in the scenario config. Each specialist owns its own measures, and the registry enforces that. The config's `meio` and `digital_twin` sections are copied verbatim from the root `scenario_config.json`, so the agents never choose a model.

```json
{
  "id": "OVR-0001",
  "measure": "demand.volume",
  "scope": {"region": ["Southeast"], "category": ["Beverages"]},
  "window": {"start_period": "2026-W28", "end_period": "2026-W31"},
  "operation": "scale",
  "value": 1.25,
  "unit": "multiplier",
  "applies_to": ["plan", "actual"],
  "rationale": "Heatwave in Southeast July 2026 lifts beverage demand 25%; known event, planned and simulated",
  "provenance": {"agent": "demand_agent", "created_at": "2026-10-09T09:45:33Z"}
}
```

| Field | Meaning |
|---|---|
| `measure` | One of the 19 measures in [`models/overrides/catalog.py`](../../models/overrides/catalog.py). Unknown measures are refused. |
| `scope` | Business keys (region, category, segment, dc_id, plant_id, lane_id, customer…), resolved against master data. A scope that matches nothing is an error, not a no-op. |
| `window` | Inclusive ISO weeks. With no window, the override applies to the whole horizon, or to the opening position for inventory. |
| `operation` / `value` / `unit` | `scale`, `add`, `set`, `floor`, `cap` or `block`. Bounds and units are checked per measure. |
| `applies_to` | `plan` means MEIO re-plans for it. `actual` means the Twin lives through it. The two sides are what separate a planned event from a surprise. |

The four overrides in SCENARIO_0003:

| ID | Measure | Change | Scope | Weeks | plan (MEIO) | actual (Twin) |
|---|---|---|---|---|---|---|
| OVR-0001 | `demand.volume` | ×1.25 | Southeast · Beverages | W28–W31 | ✔ | ✔ |
| OVR-0002 | `supply.plant_capacity` | ×0.70 | PLANT01 · LIQUID_FILL | W29–W30 | ✔ | ✔ |
| OVR-0003 | `service.target_csl` | = 0.985 | segment Fast-Moving | W28–W31 | ✔ | — |
| OVR-0004 | `inventory.on_hand` | ×0.70 | RDC03 | opening position | — | ✔ |

OVR-0003 is plan-only: a service target is a planning choice, and there is nothing physical for the Twin to simulate. OVR-0004 is actual-only: the flood is a surprise that MEIO does not plan for.

## 4. How the config becomes model behaviour

Adding JSON to a config does nothing by itself. These are the steps that make it real.

**Step 1: load and compile, strictly.** Both models call the same loader, [`models/overrides/compile.py`](../../models/overrides/compile.py), through `override_engine.load(config, …)`. The loader:
- validates every override against the schema and catalog;
- resolves each `scope` against master data into concrete rows, for example OVR-0001 → 397 (DC, SKU, customer) demand lines across 325 DC-SKU pairs;
- turns each `window` into ISO weeks;
- raises `OverrideError` on an unknown or unsupported measure or an empty match, so the run stops instead of silently skipping;
- writes `overrides_resolved.json`, which records every override with its matched rows and weeks.

Each model then takes only its side: `side("plan", "meio")` for MEIO and `side("actual", "twin_<run>")` for the Twin.

**Step 2: MEIO applies the plan side** inside its weekly solve. `roll_forward_policy.run` re-solves each of the 52 target weeks, and the hooks (`PlanHooks` in [`models/overrides/meio.py`](../../models/overrides/meio.py)) are passed into each step ([roll_forward_policy.py:91-112](../../models/MEIO/MEIO_Weekly/final_submission/roll_forward_policy.py:91)):

```python
for wk_num in forward_weeks:
    demand = lib.target_week_demand(..., target_week_num=wk_num, overrides=plan)   # demand.*
    lanes = plan.lanes(target_period) if plan else None                            # supply.lead_time_*
    ...
    policy = lib.solve_policy(demand, week_leadtime, ..., overrides=plan)          # service.target_csl -> z
    rdc_smoothed = lib.smoothed_rdc_demand_for_pdc(..., overrides=plan)            # PDCs see the same demand
    pdc_policy = lib.solve_pdc_policy(rdc_smoothed, ..., overrides=plan, period=target_period, lanes=lanes)
    ...                                                                            # ^ supply.plant_capacity clip
    if plan:
        policy = plan.policy(policy, target_period)                                # policy.* rules, OUL >= ROP
```

| Seam | Code | What changes |
|---|---|---|
| Demand | `target_week_demand` returns `overrides.demand(d, period)` ([meio_weekly_lib.py:512](../../models/MEIO/MEIO_Weekly/final_submission/meio_weekly_lib.py:512)) | The change is applied to MEIO's own weekly estimate, `target_mean`, **after** its 0.6 × forecast + 0.4 × seasonal blend. Scaling the raw forecast instead would plan a +25% event as about +15%. The CV is held, so σ scales with the mean. |
| Service | `assign_z(df, overrides, period)` ([meio_weekly_lib.py:234](../../models/MEIO/MEIO_Weekly/final_submission/meio_weekly_lib.py:234)) | The z for matched pairs becomes Φ⁻¹(0.985) = 2.1701, in RDC/CDC and PDC sizing, in-window only. |
| Capacity | `clip_to_plant_capacity(..., overrides, period)` ([meio_weekly_lib.py:609](../../models/MEIO/MEIO_Weekly/final_submission/meio_weekly_lib.py:609)) | The line's capacity in that week, before PDC throughput is clipped. |
| Lead time | `build_leadtime_graph(..., lanes)` per target week | Lane means and standard deviations for that week (not used in this scenario). |

**Step 3: the Twin applies the actual side** to its inputs and its daily loop ([`models/overrides/twin.py`](../../models/overrides/twin.py), called from [digital_twin.py](../../models/twin/digital_twin.py)):

| Seam | Code | What changes |
|---|---|---|
| Demand | `fwd_rows = override_engine.twin.forecast_rows(overrides, fwd_rows)` ([digital_twin.py:658](../../models/twin/digital_twin.py:658)) | The forward forecast lines, scaled **before** the (period, dc, sku) group-by and before the forecast-error draw. Customer scope is honoured and the random draws are unchanged, so out-of-scope demand is bit-for-bit identical (common random numbers). |
| Opening stock | `state = override_engine.twin.opening_state(overrides, state)` ([digital_twin.py:583](../../models/twin/digital_twin.py:583)) | RDC03's opening position, right after `opening_position.parquet` is read. |
| Capacity | `cap_family_week` from `capacity_weeks(...)` ([digital_twin.py:370](../../models/twin/digital_twin.py:370)); used in `_produce` ([digital_twin.py:1014](../../models/twin/digital_twin.py:1014)) | Line capacity by production week: `inp.cap_family_week.get((wk, p, fam), inp.cap_family.get((p, fam)))`. |
| Lanes, outages, adherence, economics | `lane_week`, `lane_blocks`, `adherence_week`, `economics()` | Not used in this scenario. |

**Step 4: receipts.** Each model writes what it actually applied to `receipts/<model>.json` in the scenario's override folder. The Twin also writes the receipt into `run_manifest.json` under `build_notes.overrides_applied`. The analyst agent is instructed to check the receipts before it reports.

## 5. Proof that each override reached the model

**Receipts from the real run.**

`receipts/meio.json` (plan side):
```json
{"override_id": "OVR-0001", "measure": "demand.volume", "periods": ["2026-W28","2026-W29","2026-W30","2026-W31"],
 "pair_weeks": 1300, "target_units_before": 28708.0, "target_units_after": 35885.0}
{"override_id": "OVR-0003", "measure": "service.target_csl", "pair_weeks": 15840, "z_before_mean": 2.05, "z_after_mean": 2.1701}
{"override_id": "OVR-0002", "measure": "supply.plant_capacity", "periods": ["2026-W29","2026-W30"], "family_lines": 1}
```

`receipts/twin_prepared.json` and `receipts/twin_unprepared.json` (actual side; the two are identical):
```json
{"override_id": "OVR-0001", "demand_lines": 397, "forecast_rows": 1588, "weeks": 4,
 "forecast_units_before": 29381.0, "forecast_units_after": 36726.25}
{"override_id": "OVR-0004", "pairs": 425, "column": "opening_stock", "units_before": 51529.0, "units_after": 36070.3}
{"override_id": "OVR-0002", "window": "2026-W29..2026-W30",
 "line_capacity_per_week_before": 44742.0, "line_capacity_per_week_after": 31319.4}
```

Each model received exactly its own side. OVR-0003 appears only in MEIO's receipt, and OVR-0004 only in the Twin's.

**Checks against the outputs themselves**, independent of the receipts:

| Check | Baseline (SCENARIO_0002) | Unprepared | Prepared |
|---|---|---|---|
| Twin customer demand, Southeast beverages, W28–W31 | 29,444 | 36,810 (**×1.2502**) | 36,810 (×1.2502) |
| Same pairs, other weeks | 334,691 | 334,691 (×1.0000) | 334,691 (×1.0000) |
| All other pairs, W28–W31 | 938,522 | 938,522 (×1.0000) | 938,522 (×1.0000) |
| RDC03 opening stock, W01 | 51,529 | **36,070** | 36,070 |
| Safety stock the Twin executed, Southeast beverages, W28–W31 | 35,049 | 35,049 (baseline policy) | **45,634** (+30%) |
| Same, W20–W23 (outside the window) | 34,688 | 34,688 | 34,688 |

The rows marked ×1.0000 are the common-random-numbers property at work: the scenario changed what it was told to change and nothing else.

**The MEIO policy, week by week**, compared with SCENARIO_0002's policy. The real run's file is also byte-identical to an earlier dry run.

| Week | Rows changed | Network safety stock |
|---|---|---|
| W26, W27 | 100 each | +0.0%, +0.1% |
| **W28** | **3,927** | **+5.0%** |
| **W29** | **3,880** | **+5.1%** |
| **W30** | **3,920** | **+5.2%** |
| **W31** | **3,891** | **+5.2%** |
| W32, W33 | 100 each | +0.1%, +0.0% |
| all other weeks | 0 | 0 |

The 400 changed rows outside the window are all at PDCs. PDC sizing smooths RDC demand over neighbouring weeks, so the plants start building for July from W26. OUL ≥ ROP holds on all 403,936 rows.

**The capacity cut did not bind.** PLANT01 LIQUID_FILL production was never clipped in any arm: the peak plan was 29,440 units/week against the cut capacity of 31,319. MEIO likewise reported 0 of 52 weeks capacity-clipped. The authoring tools had predicted this ("the cut may not bind"). The override was applied, and the receipts prove it, but at this depth of cut it does not constrain anything.

## 6. Results

**Network headline.** This is single-seed, so fill-rate differences under about 0.3 pp are noise.

| KPI | Baseline | Unprepared | Prepared | Impact (U − B) | Re-planning (P − U) |
|---|---|---|---|---|---|
| Customer fill rate | 92.71% | 92.50% | 92.68% | −0.21 pp | +0.18 pp |
| Demand-weeks in full | 85.81% | 85.69% | 85.81% | −0.12 pp | +0.12 pp |
| Lost sales (units) | 473,839 | 476,753 | 475,558 | +2,914 | −1,195 |
| Stockout cost | $3.91M | $4.08M | $3.87M | +$172k | −$211k |
| Avg inventory value | $6.80M | $6.79M | $6.84M | −$7k | +$47k |
| Total landed cost | $36.72M | $36.93M | $36.73M | +$210k | −$201k |

**Inside each event**, from the new `scope_service` tool. The surge is under 1% of network demand, so the headline dilutes it.

| Override scope and window | Baseline fill | Unprepared | Prepared | Re-planning |
|---|---|---|---|---|
| OVR-0001 Southeast beverages, W28–W31 | 97.98% | 95.91% (−2.07 pp) | 96.57% (−1.41 pp) | **+0.66 pp** |
| OVR-0003 Fast-Moving, W28–W31 | 95.14% | 94.96% (−0.18 pp) | 95.22% (+0.08 pp) | +0.26 pp |
| OVR-0004 RDC03, full year | 97.20% | 97.11% (−0.09 pp) | 97.23% (+0.03 pp) | +0.12 pp |

**MEIO's response over W28–W31** (`policy_diff` with the window): safety stock +5% at every echelon, worth about $151k.

| Echelon | Safety stock change |
|---|---|
| RDC | +$107k |
| PDC | +$25k |
| CDC | +$19k |

**Week by week, Southeast beverages** (customer fill; unmet units in brackets):

| Week | Baseline | Unprepared | Prepared |
|---|---|---|---|
| W27 | 96.5% (249) | 97.8% (157) | 97.3% (194) |
| W28 (surge starts) | 97.9% (138) | 97.2% (229) | 96.5% (293) |
| W29 (cut starts) | 97.7% (164) | 95.9% (369) | 96.3% (338) |
| W30 | 98.2% (138) | 95.0% (488) | **97.7% (225)** |
| W31 | 98.0% (154) | 95.7% (417) | 95.8% (407) |
| W32 (after the window) | 95.5% (310) | **90.8% (637)** | **96.3% (259)** |

**Reading.**
- **The cost of the surge.** Unprepared, the heatwave costs about 2 points of fill in Southeast beverages in July. It also leaves the network depleted in W32, the week after the window, when unprepared fill falls to 90.8%.
- **What re-planning recovers.** It wins back a third of the in-window loss (+0.66 pp) and avoids most of the W32 drop. Network-wide that is about $211k of stockout cost, for about $47k more average inventory. The in-window `scope_service` figure therefore understates the benefit; widening the window to W32 would show the after-effect.
- **What re-planning doesn't fix.** It does not help in W28. The policy rises in the surge's first week, but stock ordered then arrives only after the lead time, so W28 has to be ordered earlier. The `plan_lead_weeks` anticipation option for this is not built. W31 is also no better, and why is not established; a second seed would show whether that is noise.
- **The flood.** It costs RDC03 about 0.1 pt over the year, concentrated in the opening weeks: fill falls from 96.13% to 95.48% in W01–W08.

## 7. What the agents got right and wrong, and what changed

- **Right.** The orchestrator ran the gated path correctly and delegated with a clear brief. The analyst's KPI table matches the raw outputs exactly. The Sonnet orchestrator also caught the analyst's over-reading: it called the regional splits noise and marked the capacity claim as interpretation, not proof.
- **Wrong.** The Haiku analyst made three mistakes:
  - it said all four overrides were applied to *both* arms, but they are side-specific;
  - it called the PLANT01 cut "the binding constraint", but production was never clipped;
  - it reported MEIO's safety-stock change as +0.3%, because `policy_diff` averaged a 4-week change over 52 weeks.
- **Fixed after this run:**
  - `policy_diff` takes `start_period`/`end_period`;
  - a new `scope_service` tool reports fill inside an override's own scope and window;
  - the analyst instructions now require checking receipts per side, say that "applied" is not the same as "binding", and lead the breakdown with the in-scope result;
  - MEIO receipts now total `pair_weeks` and units across weeks, and pair-weight `*_mean` values (the first dry run had summed z means to "16.4").

  28 agent tests and 13 engine tests pass after these changes.
- **Not changed:** `scenario_card.md` still shows the "engine not ready" warnings from approval time. It is the record of what the person approved, under the config SHA-256, so it is kept as-is.

## 8. Reproduce it

Run the scenario from its approved config. Run outputs go to `output datasets/MEIO|projections/SCENARIO_0003_…`. The scenario has already run, so the gate refuses until a person re-approves it with `--allow-overwrite`.

```bash
python -m Agents.tools approve SCENARIO_0003_Southeast_Heatwave_July_Maintenance_Flood --by "<your name>" --allow-overwrite
```

```bash
python -m Agents.tools call run_scenario --agent orchestrator --args "{\"scenario_folder\": \"SCENARIO_0003_Southeast_Heatwave_July_Maintenance_Flood\"}"
```

Or one arm by hand: MEIO re-plans with the plan side, then the Twin runs the actual side.

```bash
python run_meio_then_twin.py --scenario-config "output datasets/overrides/SCENARIO_0003_Southeast_Heatwave_July_Maintenance_Flood/scenario_config.json" --yes
```

The engine's own tests: each seam in both models, on the real data.

```bash
python -m pytest models/overrides/tests -q
```
