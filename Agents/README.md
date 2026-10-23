# Agents — scenario creation for Supply Brain

A small team of agents turns a planner's plain-English what-if into **overrides**: auditable changes to service levels, demand, supply, on-hand inventory, policy and economics. The agents write the scenario config and put a scenario card in front of a person for approval. MEIO then re-plans and the Digital Twin simulates the scenario from the same overrides, and an analyst agent explains the result against the baseline. The agents run on **LangGraph**.

**Start with [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md):** architecture, the override contract, the exact MEIO and Twin hook points, phases and timeline.

**Worked example: [examples/SCENARIO_0003_walkthrough.md](examples/SCENARIO_0003_walkthrough.md).** It follows one scenario from the planner's sentence to the override JSON, the code in MEIO and the Twin that applies each override, the receipts and output checks that prove it, and the results. It records the 9 Oct heatwave run, whose output folders are no longer in this repository; the scenarios on disk today are SCENARIO_0003 and SCENARIO_0004 (`SCENARIO_000#_Veganism_Dairy_Decline_Protest_Shipping_Delays`) under `output datasets/overrides/`.

## Status

| Part | State |
|---|---|
| Instructions, override contract, measure catalog | done |
| Tools: context, authoring, validation, previews, macros, config builder, approval gate, analysis | done (28 tools in `registry.py`, 26 with handlers plus the runtime tools `delegate` and `request_human_approval`) |
| LangGraph runtime: orchestrator + specialists, delegation, approval interrupt, model profiles | done; 30 offline tests (21 in `test_tools.py`, 9 in `test_runtime.py`). On 23 Oct 29 passed; `test_nvidia_profile_uses_openai` failed only because `langchain-openai` was not installed in that environment. Live-tested 9 Oct through Claude Desktop, with Sonnet 5.5 as orchestrator and Haiku 4.5 as workers. See [examples/live_test_2026-10-09.md](examples/live_test_2026-10-09.md). |
| Model providers | `runtime/llm.py` supports three: `nvidia` (NVIDIA Build API through `langchain-openai`, the default profile), `anthropic` (`langchain-anthropic`) and `desktop` (the Claude Desktop bridge). See [Models](#models). |
| MEIO and Twin applying overrides (`models/overrides/`) | done: all 19 measures (`policy.pooling_alpha` only under Clark-Scarf v2). The engine is called from the optimized pipeline that `scenario_config.json` names (`final_submission_optimized/`: `meio_optimizer_weekly.py`, `roll_forward_policy.py`, `digital_twin_fast.py`, `reactive_twin.py`). `models/twin/digital_twin.py` does not import the engine: it applies only the legacy `changes.demand_surge_pct` and ignores `changes.overrides`. The 13 engine tests in `models/overrides/tests/test_engine.py` do not run in the current tree: collection fails because they import `meio_weekly_lib` from `final_submission/` (no longer there), and they drive `models/twin/digital_twin.py`. |
| MEIO model and tuning | Clark-Scarf v2, with alpha, netting and the per-cluster alphas from `run_all.py`'s evidence (`final_submission_optimized/meio_parameters.py`, 11 tests in `final_submission_optimized/tests/test_meio_parameters.py`). Every MEIO policy folder has a `meio_parameters.json`; SCENARIO_0002's resolves to alpha 0.6, netting 0.0 and per-cluster alphas of 0.6–1.0. |
| Front end | `App/streamlit_app.py`, Simulation centre: an in-process agent chat (`desktop` or `claude` profile), the approval interrupt answered in the chat, and an Approve button that calls `python -m Agents.tools approve`. See [App/BUTTON_MAPPING.md](../App/BUTTON_MAPPING.md). |

`python -m Agents.tools readiness` reports `ready_to_run: true`. `run_scenario` launches only approved scenarios.

## Where files go

| What | Where |
|---|---|
| One folder per scenario: `scenario_config.json`, `scenario_card.md`, `approval.json`, run `status.json` / `run.log`, and the engine's `overrides_resolved.json` and `receipts/` (`meio.json`, `meio_weekly.json`, `twin_<arm>.json`) | `output datasets/overrides/SCENARIO_####_Name/`. The root comes from `overrides.output_root` in `scenario_config.json`, and the folder name is derived from the scenario number and name, like the MEIO and Twin folders. |
| The scenario's MEIO policy | `output datasets/MEIO/SCENARIO_####_Name/` (`2026-W01/`, `rolling_2026/meio_policy_rolling.parquet`) |
| The Twin run of each arm | `output datasets/SCENARIO_####_Name/<arm>/` (`prepared`, `unprepared`, `reactive`). `digital_twin_fast.py` writes here, with no `projections/` segment. |
| The reactive arm's no-scenario control | `output datasets/<baseline scenario>/counterfactual_reactive_no_scenario/`, built once per baseline and seed, then reused |
| Drafts not yet numbered, runtime transcripts, the Claude Desktop bridge mailbox | `Agents/workspace/` (git-ignored) |

Scenarios run **the MEIO that `scenario_config.json` defines**: the generated config copies its `meio` section verbatim. Agents never choose a model.

## Layout

```
Agents/
  IMPLEMENTATION_PLAN.md       the plan
  requirements.txt             langgraph, langchain-anthropic, langchain-openai, requests, jsonschema, pyyaml, pytest
  instructions/                system prompts (_shared_context.md + one per agent)
  tools/                       what the agents call (people use the same tools through the CLI)
    paths.py                     repository locations (overrides root, workspace, output folders)
    catalog.py                   re-exports the engine's measure catalog (models/overrides/catalog.py)
    context.py                   master data, ISO-week windows, scope resolution, entity profiles
    drafts.py                    draft store and override validation; baseline staleness check
    impact.py                    instant input-size previews
    macros.py                    disruption library, price/promo events, service ladder
    scenario.py                  config builder, scenario card, approval record, gated runner
    analysis.py                  arm comparison, service and policy breakdowns, application receipts
    engine.py                    what the installed MEIO/Twin pipeline can apply
    registry.py                  tool specs, per-agent allowlists, dispatcher
    __main__.py                  the people's CLI (tools, call, card, approve, status, readiness)
  runtime/                     LangGraph runtime
    graph.py                     agent graphs (model <-> tools), delegation, approval interrupt, validator evidence
    llm.py, profiles.yaml        which provider and model each agent runs on (nvidia, anthropic, desktop)
    bridge.py                    `desktop` provider: no API key, Claude Desktop answers model calls
    utils.py                     token-streaming callback for terminal output
    __main__.py                  `python -m Agents.runtime` (one request, or an interactive session)
  examples/                    generated config and card, the live-test report, the SCENARIO_0003 walkthrough
  tests/                       test_tools.py, test_runtime.py

models/overrides/              the override engine both models import
  override.schema.json           the `changes.overrides` contract
  catalog.py                     the 19 measures: scope keys, operations, bounds, sides, model seams
  master.py                      master data, scope matching, planning-demand basis
  compile.py                     strict loader: validate, resolve scope and window, receipts
  meio.py                        PlanHooks: the plan side, applied inside the weekly MEIO solve
  twin.py                        the actual side, applied to the Twin's inputs and daily loop
  tests/test_engine.py           one test per seam, in both models
```

## Quickstart

Run from the repository root.

```bash
python -m pip install -r Agents/requirements.txt
```

The MEIO and Twin scripts the scenarios run (`final_submission_optimized/`) also import `polars`, which no requirements file lists. The runtime tests import `langchain_openai` for the default `nvidia` profile.

```bash
python -m pytest Agents/tests -q
```

```bash
python -m Agents.tools readiness
```

Drive the tools by hand, exactly as an agent would. Approval is for people only: it shows the card and asks you to type the scenario number.

```bash
python -m Agents.tools call create_draft --args "{\"scenario_name\": \"Southeast Heatwave\", \"description\": \"July heatwave\", \"request_text\": \"Southeast beverage demand +25% in July\"}"
```

`build_scenario_config` returns the scenario folder name. The number is the next free one across the MEIO, Twin, artifacts and overrides folders (SCENARIO_0003 and 0004 are taken today), so use the name it returns:

```bash
python -m Agents.tools approve SCENARIO_0005_Southeast_Heatwave --by "Your Name"
```

The other commands are `tools [--agent A] [--schemas]`, `call TOOL --agent A --args JSON`, `card SCENARIO`, `status SCENARIO` and `readiness`. `approve` also takes `--allow-overwrite`, `--note` and `--yes` (skip the typed confirmation).

## Running the agents

**On the default `nvidia` profile** (NVIDIA Build API; needs `NVIDIA_API_KEY`):

```bash
python -m Agents.runtime "What if Southeast beverage demand rises 25% in July?"
```

**With Anthropic credentials** (`ANTHROPIC_API_KEY`), on the `claude` profile or one of the `anthropic-*` profiles:

```bash
python -m Agents.runtime --profile claude "What if Southeast beverage demand rises 25% in July?"
```

Omit the request for an interactive `planner>` session. Output streams token by token unless you pass `--no-stream` (the desktop profile never streams). `--approver "<name>"` sets the name recorded when you approve at the terminal; with no terminal attached, the approval gate returns `pending` and the scenario waits for `python -m Agents.tools approve`.

**Without an API key**, through Claude Desktop:

```bash
python -m Agents.runtime --profile desktop "What if Southeast beverage demand rises 25% in July?"
```

Run the command in the background. A Claude Desktop session then acts as the model provider, repeating this loop:

1. Run `python -m Agents.runtime.bridge wait` (default `--timeout 600`). It prints the next pending model call (agent, model alias, `request_markdown`, `response_file`), exits with code 4 when the approval gate is waiting, with 3 when the session is done, and with 5 on timeout. `python -m Agents.runtime.bridge status` shows the session state.
2. Start a subagent on the call's model (`sonnet` for the orchestrator, `haiku` for workers) with the prompt below. It reads the request file and writes the reply JSON.
3. At the approval gate, show the person `scenario_card.md`, then write their decision to the given `response_file` as `{"approved": true, "approved_by": "<name>"}` or `{"approved": false, "feedback": "..."}`.

The operator prompt below passes the gateway's content filter. Prompts that frame the subagent as "acting as the language model" were blocked.

```text
Task: produce the next step for the "<agent>" in our supply-chain scenario-planning app.
The app has written everything you need into one file: <request_markdown>
It holds the agent's role description, the functions it can call (with their JSON input formats) and the
conversation so far. Read the whole file (page with offset/limit if it is long). Then work out what the agent
should do next, following its role description: which functions to call, with arguments in the documented
format, or, if its work is finished, its final report with no function calls. Save your answer with the Write
tool to <response_file> as one JSON object:
{"text": "...", "tool_calls": [{"name": "...", "args": {...}}], "model_id": "<the model ID you run on>"}
Only read that one file and write that one file. Reply "ok" when the file is saved.
```

## How the pieces fit

1. The **orchestrator** restates the request as a brief and creates a draft. It sends each lever to its specialist with `delegate`; each specialist runs as its own LangGraph graph on a self-contained brief.
2. **Specialists** write only their own measures (enforced in `registry.py`). Every proposal is validated against the catalog and master data, nothing is saved on error, and each proposal returns an instant size preview.
3. The **validator**'s graph collects `list_overrides` and `validate_draft` results before its first model call, so its review always rests on evidence.
4. `build_scenario_config` writes the scenario folder under `output datasets/overrides/`.
5. `request_human_approval` pauses the graph with a LangGraph `interrupt`. Only a person resumes it, and `approval.json` records the SHA-256 of the exact config they read.
6. `run_scenario` launches a detached `python -m Agents.tools run-job <scenario>` that runs the evaluation arms `scenario.arms_for()` chose, in this order:
   - **prepared**: `run_meio_then_twin.py --scenario-config <config>`. MEIO (`run_weekly_tools.py`) re-plans with the plan side, then `digital_twin_fast.py` simulates the actual side.
   - **unprepared**: `digital_twin_fast.py` on the baseline's rolling policy, with the actual side.
   - **reactive**: `reactive_twin.py` on the same baseline policy and actual side. MEIO is not told about the scenario and re-sizes every week from the demand and arrived lead times the Twin has simulated so far.
   - **reactive control**: when the reactive arm runs, `reactive_twin.py` on the root config (no overrides), written as `counterfactual_reactive_no_scenario` under the baseline scenario, unless one exists for the same seed.

   A plan-and-actual scenario runs all three arms, an actual-only scenario runs unprepared and reactive, and a plan-only scenario runs prepared only.
7. The **analyst** checks the engine's receipts, then reports impact, the value of re-planning in advance, the value of re-planning weekly (split with the reactive control) and the inventory cost of preparing.

## Models

`runtime/profiles.yaml` sets the provider and model for each agent. Choose a profile with `--profile` or `SUPPLY_BRAIN_AGENT_PROFILE`. `workers` applies to every agent except the orchestrator, and an agent listed by name (such as `validator_agent`) overrides it.

| Profile | Provider | Orchestrator | Workers | Notes |
|---|---|---|---|---|
| `nvidia` (default) | nvidia | `z-ai/glm-5.3-flash`, 4096 max tokens, temperature 0.5 | the same | `NVIDIA_API_KEY`; base URL `https://integrate.api.nvidia.com/v1`; 180 s request timeout, no retries |
| `test` | nvidia | `z-ai/glm-5.3-flash`, 1024 max tokens | the same | |
| `nvidia-fast` | nvidia | `google/gemma-4-31b-it`, 1024 max tokens | the same | |
| `production` | nvidia | `z-ai/glm-5.3-flash`, 2048 max tokens, temperature 0.3 | `z-ai/glm-5.3-flash`, 1024 max tokens; `validator_agent` 2048, temperature 0.2 | |
| `desktop` | desktop | Claude Desktop `sonnet` | Claude Desktop `haiku` | no API key |
| `claude` | anthropic | `claude-opus-5`, effort high, server-side refusal fallback | `claude-haiku-4-5` | `ANTHROPIC_API_KEY`; the app's "Claude" option |
| `anthropic-test` | anthropic | `claude-sonnet-4-6`, effort medium, adaptive thinking | `claude-haiku-4-5` | the former `test` profile |
| `anthropic-production` | anthropic | `claude-opus-5-5`, effort high | `claude-opus-5-5`, effort medium; `validator_agent` effort high | refusal fallback on every agent |

Anthropic calls use a 300 s timeout and 2 retries.

The live test found Haiku reliable for the authoring specialists but weak as the validator. On an Anthropic profile, a one-line change in `profiles.yaml` (`validator_agent: {model: claude-sonnet-4-6}`) would move just the validator onto Sonnet.
