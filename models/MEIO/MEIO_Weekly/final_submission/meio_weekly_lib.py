"""
Shared loaders and estimation logic for the weekly MEIO tools
(`meio_optimizer_weekly.py`, `backtest_csl.py`, `simulate_policy.py`).

Root-caused fixes baked in here (see MEIO_Weekly history in project memory —
the previous weekly tool was lost from the repo and this is a rebuild):

1. `demand_forecast.parquet` is really `(period, sku_id, dc_id, b2b_customer_id)`
   grain -- ~5% of (period, sku, dc) combos are split across 2+ B2B
   customers. Every function here aggregates to true (period, sku_id, dc_id)
   weekly totals immediately on load; nothing downstream ever sees the
   pre-aggregation rows.
2. Forecast-bias correction: `forecast_qty` under-predicts `actual_qty`
   persistently (found ~7-8% portfolio-wide during backtesting). A z-score
   safety-stock only buffers *random* variance around the mean -- it cannot
   correct a persistent bias. `bias_factor()` below measures the recent
   trailing actual/forecast ratio and the optimizer multiplies the raw
   forecast by it before sizing safety stock.
3. Blended trailing CV: a 13-week trailing window alone understates full-year
   volatility (~0.90 vs ~1.05 portfolio CV, found during backtesting).
   `blended_cv()` averages a short (13wk) and long (52wk) trailing window so
   the safety stock reacts to recent conditions without losing the tails a
   short window misses.
4. `cost_parameters.parquet` is keyed by `(dc_id, subcategory)`, not `category`.
   Holding cost is DC-specific:
       holding_per_unit_week = (storage_cost_per_unit_year_usd
                                 + unit_cost_usd * inventory_carrying_pct_annual / 100) / 52
   The currently-committed `models/MEIO/meio_optimizer.py` still joins on
   `category` and throws `KeyError: 'category'` against this dataset --
   fixed here and back-ported to that script in the same change that added
   this file.

5. **Protection interval, order cover, echelon uplift, and z -- matched to
   the dataset's own answer key.** `supply_brain_generator_v5.py`'s
   `derive_baseline_policy()` (the function that produces
   `output datasets/history/forward_policy.parquet`, the policy this dataset's
   own twin actually hits ~91% fill rate with) sizes stock differently from
   a first pass of this tool did, and its own comments name the exact
   failure mode a first pass reproduced almost exactly:
     - Protection interval is `P = REVIEW_WEEKS + lead_time`, not lead time
       alone -- "sizing on L alone undersizes the trigger badly wherever
       the review dominates the lead time... protection is understated by
       roughly 70%."
     - Order-up-to cover is `ORDER_COVER_WEEKS = 1.5`, not one review
       period -- "setting this to the review period alone makes every
       replenishment a hand-to-mouth top-up... fill rate falls into the
       70s" (a first pass of this tool got 73.4%).
     - Demand uncertainty is uplifted per echelon before sizing --
       `SIGMA_UPLIFT = {PDC: 1.45, RDC: 1.25, CDC: 1.00}` -- because a
       parent absorbs its children's order variability on top of its own.
     - Service level `z` is fixed per SKU segment
       (`Fast-Moving: 2.05, Seasonal: 1.64, Intermediate: 1.28`), not
       derived from a stockout/holding cost ratio.
   `solve_policy()` below now matches all four. It also no longer pools RDC
   demand with its downstream CDCs' demand explicitly -- `derive_baseline_
   policy()` doesn't either, and sizes each node directly against its own
   `demand_forecast.parquet` row (RDC01's own recorded in-sample demand is
   ~8x its downstream CDCs' combined demand, so explicit pooling was a
   modest over-count, not the dominant error, but there is no evidence the
   dataset's RDC-level demand rows exclude downstream replenishment pull,
   so summing both was a real, if secondary, risk of double-counting).

6. **PDC (plant DC) coverage.** The first working version of this tool only
   sized RDC/CDC (pull-review) nodes -- same scope as the annual
   `meio_optimizer.py` -- and fed the digital twin a policy with zero PDC
   rows. The dataset's own baseline covers all 4 PDCs (658 rows) with a
   different formula, because a PDC is pushed by production, not pulled by
   a downstream order: its order-up-to is a holding ceiling sized on the
   throughput it must pass along, not a demand-driven reorder trigger.
   `solve_pdc_policy()` replicates `derive_baseline_policy()`'s PDC branch:
   throughput per (pdc, sku) = sum of every RDC-SKU's demand that sources
   from that PDC (via `sku_sourcing_map.parquet`, the actual routing table --
   not the fastest-lane approximation `build_leadtime_graph()` uses
   elsewhere), using THIS tool's own bias-corrected RDC `target_mean` so
   the whole network stays on one consistent demand basis end to end.
   Omitting PDC coverage was found to matter a lot: `internal_replen_
   fill_rate` in the full twin simulation was *worse* with the RDC/CDC-only
   fixes from point 5 alone than without them, because upstream had no
   instruction to hold anything for the bigger downstream orders the point-5
   fixes now call for.

7. **Single-week forecast zeros.** With PDC coverage added, the full-twin
   simulation still lost badly to baseline -- every node's fill rate was
   worse (uniformly, not concentrated) while `avg_on_hand` was
   simultaneously *higher* almost everywhere (median +8% vs baseline). That
   combination rules out "not enough stock" and points at misallocation:
   found 2,732 of 7,179 active SKU-DC pairs (38%) have a literal $0
   `forecast_qty` for any single given target week despite nonzero annual
   demand (worst for Seasonal-segment SKUs -- median single-week forecast
   is only 39% of their annual average forecast, vs 91% for Fast-Moving).
   `target_week_demand()`'s `target_mean` used to be `forecast_qty *
   bias_factor` outright, so a $0 forecast week zeroed safety_stock,
   reorder_point AND order_up_to_level simultaneously for that SKU-DC --
   fine in real weekly operation (self-corrects next week) but fatal when
   that single static policy is held for a full simulated year, and a real
   risk even operationally if a SKU has a genuinely lumpy weekly forecast.
   Fixed by blending `raw_target_mean` with a fallback estimate
   (`MEAN_BLEND_ALPHA = 0.6` toward the point forecast, 0.4 toward the
   fallback) so one zero-forecast week can no longer collapse the whole
   recommendation for a SKU-DC with real underlying demand.

8. **De-seasonalization, replacing point 7's flat trailing-mean fallback.**
   A flat trailing average fixes the zero-forecast problem but also
   flattens genuine low-season weeks toward the mean -- exactly wrong for
   Seasonal-segment SKUs, which is what point 7 found was worst-affected.
   `build_seasonal_index()` derives a multiplicative seasonal factor per
   (subcategory, week-of-year), pooled across every SKU in the subcategory
   over the full 3-year `orders.parquet` history (a single year of
   `demand_forecast.parquet` in-sample data isn't enough to estimate an
   individual SKU's seasonal shape without memorizing that one year's
   noise -- the SHAPE is borrowed from the subcategory, the LEVEL stays
   SKU-specific via `deseasonalized_level()`). The fallback in
   `target_week_demand()` is now `deseasonalized_level(sku, dc) x
   seasonal_index(subcategory, target week-of-year)` -- properly low for a
   genuinely low-season target week, properly high for an in-season one,
   instead of regressing everything to a flat mean.

9. **PDC look-ahead.** The rolling weekly simulation (v6, see README) beat
   the dataset's own baseline on customer fill rate for the first time, but
   `internal_replen_fill_rate` dropped -8.2pp doing it -- a real tradeoff,
   not a free win. Root cause: `solve_pdc_policy()` was sizing PDC
   throughput off only the CURRENT week's RDC pull. A PDC is pushed by
   production, which has its own lead time and a hard capacity constraint
   (`ASSUMPTIONS: "Production capacity DOES bind"` in
   `models/twin/digital_twin.py`) -- sizing off the current week alone
   means the plant only "notices" a ramp the week it happens and can't
   catch up in time, even though downstream RDC/CDC safety stock is
   correctly sized ahead of the same ramp (which is why customer fill rate
   improved regardless). `lookahead_rdc_demand()` averages RDC target_mean
   over the next `PDC_LOOKAHEAD_WEEKS` (3) weeks instead of just the
   current one, so PDC throughput -- and therefore its
   safety_stock/reorder_point/order_up_to -- builds ahead of a ramp instead
   of reacting to it. In practice this only moved
   internal_replen_fill_rate +0.2pp (81.0% vs the -8.2pp gap) -- nowhere
   near enough, which prompted point 10.

10. **Plant capacity, wired in and ruled out.** Point 9's fix barely
    helping suggested the diagnosis was wrong, not just incomplete: if PDC
    were reacting-not-anticipating, giving it 3 weeks of lead time should
    have helped a lot more. Checking `service_by_node.csv` showed BOTH PDC
    (avg fill_rate_replen -6.2pp) and RDC (-10.1pp) internal fill dropped
    together, with `replen_demand` itself higher almost everywhere -- the
    rolling policy demands more total internal replenishment volume,
    network-wide, not just later than PDC could react to. That reads like
    a genuine production-capacity ceiling, so `clip_to_plant_capacity()`
    wires in `plant_capability.parquet` (per-plant, per-production-family
    weekly capacity) and `plant_master.parquet` (plant-wide weekly capacity),
    mirroring the twin's own two-stage production clip exactly (family
    line, then plant-wide -- see `models/twin/digital_twin.py`
    ASSUMPTIONS). Verified against real numbers, not just "zero clips
    logged": required PDC throughput peaks at ~40-50% of stated plant
    capacity across all 4 plants, every week of the rolling horizon --
    real headroom, not a units bug. So capacity is NOT the constraint;
    ruling it out cleanly is the finding, not a dead end. The
    internal_replen_fill_rate gap most likely comes from the rolling
    policy's week-to-week ORDER-UP-TO SWINGS being bursty relative to a
    fairly thin PDC safety-stock buffer (`SIGMA_UPLIFT["PDC"] * 0.5`) --
    a timing/buffer-sizing problem, not a hard ceiling.

11. **Demand smoothing for PDC only -- bullwhip-effect mitigation, not a
    dataset-specific patch.** `lookahead_rdc_demand()` (point 9) is now
    `smoothed_rdc_demand_for_pdc()`: instead of a forward-only look-ahead
    window, it averages RDC target_mean over `PDC_SMOOTH_WEEKS` (5)
    CENTERED on the target week. This is the standard operations-research
    response to what point 10 actually found -- variability amplifying as
    it moves upstream through a multi-echelon network is the textbook
    bullwhip effect, and the textbook mitigation is to decouple a
    production-constrained node from raw demand noise while leaving
    inventory-pull nodes downstream fully responsive. RDC/CDC's own sizing
    is untouched by this; only the signal fed into `solve_pdc_policy()` is
    smoothed. `PDC_SMOOTH_WEEKS` is deliberately grounded in
    `PDC_COVER_WEEKS` (ceil(4.5)=5) -- the horizon PDC is already sized to
    buffer -- rather than fit to maximize a KPI on this one dataset; before
    trusting any result built on this, re-validate across
    `multi_seed_robustness.py`'s seeds, the same way that script validated
    the base rolling-policy result (see README "v9").

    Re-validated across all 5 seeds: **the customer-fill-rate win held
    exactly** (mean +1.52pp vs +1.48pp pre-smoothing, same 5/5 seeds,
    cost tradeoff unchanged) -- so this cost nothing. But
    internal_replen_fill_rate barely moved either (81.2% vs 81.0%
    pre-smoothing at the default seed, ~80% across all seeds, all still
    well below baseline's ~88-89%). Three independent fixes now
    (look-ahead, capacity-clip, centered smoothing) have each failed to
    move this specific metric more than ~0.2pp. Read that as a real
    result, not a failed attempt to hide: the internal-fill gap most
    likely isn't fixable by resizing PDC's OWN buffer formula at all,
    because PDC's ACTUAL pull volume is generated by RDC's real,
    intentionally-unsmoothed ordering behavior (the volatility that wins
    the customer-fill-rate gain) -- smoothing what PDC is SIZED against
    doesn't reduce what it's actually ASKED for. Closing it further would
    mean either smoothing RDC/CDC's own policy (which would give back the
    fill-rate win) or a substantially larger PDC safety-stock allocation
    (a real cost, not a formula tweak). Left as a documented, seemingly
    structural tradeoff rather than force-fit further.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

REVIEW_WEEKS = 1.0               # weekly review cadence
ORDER_COVER_WEEKS = 1.5          # matches generator's calibrated order batch size
MIN_S_MULTIPLE = 1.2             # order-up-to must exceed reorder point by this much
PDC_COVER_WEEKS = 4.5            # plant DCs are pushed; order-up-to is a holding ceiling
PDC_SMOOTH_WEEKS = 5              # PDC throughput sizes off a window of RDC pull CENTERED on
                                   # the target week, not the current week alone -- a
                                   # bullwhip-mitigation / production-smoothing decoupling
                                   # point (see docstring point 11). Width = ceil(PDC_COVER_
                                   # WEEKS=4.5): the plant is already sized to carry that many
                                   # weeks of buffer, so smoothing its input signal over the
                                   # SAME horizon it's meant to cushion is self-consistent --
                                   # not a number fit to this dataset's KPI. See
                                   # smoothed_rdc_demand_for_pdc().
MEAN_BLEND_ALPHA = 0.6           # weight on the target week's own (bias-corrected) forecast
                                  # vs the trailing long-window mean -- see target_week_demand()
SIGMA_UPLIFT = {"PDC": 1.45, "RDC": 1.25, "CDC": 1.00}
Z_BY_SEGMENT = {"Fast-Moving": 2.05, "Seasonal": 1.64, "Intermediate": 1.28}
Z_DEFAULT = 1.28
Z_MIN, Z_MAX = 0.84, 3.10        # clip service level to [80%, 99.9%] (diagnostic only, doesn't drive z)
SHORT_WINDOW_WEEKS = 13
LONG_WINDOW_WEEKS = 52

# Per-SKU z override, empty by default so every existing call site is
# unaffected. Populated by callers that want to size against a different
# service-level ladder than sku_master.parquet's baked-in 3-way segment (e.g.
# the Clustering & Segmentation deliverable's own 4-cluster CSL ladder,
# which cuts across the original segment boundaries -- see
# `rerun_with_cluster_csl.py`). Keyed by sku_id, not (sku_id, dc_id): the
# ladder this overrides is itself a pure per-SKU attribute.
Z_OVERRIDE_BY_SKU: dict = {}


def assign_z(df: pd.DataFrame, overrides=None, period: str | None = None,
             z_by_sku: dict | None = None) -> pd.Series:
    """z per row: z_by_sku (or, when that is None, the Z_OVERRIDE_BY_SKU research
    seam) where set, else the segment-based Z_BY_SEGMENT lookup (original behavior,
    unchanged when both are empty). `overrides` (models/overrides PlanHooks, None by
    default) then applies the scenario's service.target_csl overrides active in `period`."""
    mapping = Z_OVERRIDE_BY_SKU if z_by_sku is None else z_by_sku
    if mapping:
        overridden = df.sku_id.map(mapping)
        z = overridden.fillna(df.segment.map(Z_BY_SEGMENT)).fillna(Z_DEFAULT)
    else:
        z = df.segment.map(Z_BY_SEGMENT).fillna(Z_DEFAULT)
    return overrides.z(df, z, period) if overrides else z


# --------------------------------------------------------------------- IO ---
def load_period_index(data_dir: str) -> pd.DataFrame:
    """Distinct (period, week_start_date) pairs from demand_forecast.parquet, in order."""
    dfc = pd.read_parquet(f"{data_dir}/demand_forecast.parquet", columns=["period", "week_start_date"])
    idx = dfc.drop_duplicates().sort_values("week_start_date").reset_index(drop=True)
    idx["week_num"] = np.arange(len(idx))
    return idx


def load_demand_forecast(data_dir: str) -> pd.DataFrame:
    """demand_forecast.parquet aggregated to true (period, horizon, sku_id, dc_id) grain."""
    dfc = pd.read_parquet(f"{data_dir}/demand_forecast.parquet")
    agg = (dfc.groupby(["period", "week_start_date", "horizon", "sku_id", "dc_id"], as_index=False)
           .agg(forecast_qty=("forecast_qty", "sum"),
                forecast_baseline_qty=("forecast_baseline_qty", "sum"),
                forecast_promo_lift_qty=("forecast_promo_lift_qty", "sum"),
                actual_qty=("actual_qty", "sum")))
    return agg


def load_sku_cost(data_dir: str) -> pd.DataFrame:
    """SKU master joined to DC-specific cost parameters via (dc_id, subcategory).

    Returns one row per (sku_id, dc_id) with holding_per_unit_week and
    stockout_cost_unit, both DC-specific (a SKU can cost more to hold or
    stock out of at one DC than another).
    """
    sku = pd.read_parquet(f"{data_dir}/sku_master.parquet")
    cost_p = pd.read_parquet(f"{data_dir}/cost_parameters.parquet")
    sku_c = sku.merge(cost_p, on="subcategory", how="left")
    sku_c["holding_per_unit_week"] = (
        sku_c.storage_cost_per_unit_year_usd
        + sku_c.unit_cost_usd * sku_c.inventory_carrying_pct_annual / 100.0
    ) / 52.0
    sku_c["stockout_cost_unit"] = sku_c.margin_usd * sku_c.stockout_cost_multiplier
    return sku_c[["sku_id", "dc_id", "category", "subcategory", "segment", "unit_cost_usd",
                   "holding_per_unit_week", "stockout_cost_unit"]]


def load_sku_subcategory(data_dir: str) -> dict:
    sku = pd.read_parquet(f"{data_dir}/sku_master.parquet", columns=["sku_id", "subcategory"])
    return sku.set_index("sku_id")["subcategory"].to_dict()


def week_of_year(period: str) -> int:
    """'2025-W01' -> 1. Every year in this dataset is exactly 52 weeks
    (verified: in_sample, forward and the 3-year history all run W01-W52
    with no W53), so week number is directly comparable year over year."""
    return int(period.split("-W")[1])


def build_seasonal_index(data_dir: str) -> pd.DataFrame:
    """Multiplicative seasonal index per (subcategory, week_of_year): that
    week's demand relative to the subcategory's average week, pooled across
    every SKU in the subcategory over the full 3-year order history
    (orders.parquet, 156 weeks). A single year of demand_forecast.parquet in-sample
    data isn't enough to estimate an individual SKU's seasonal shape
    without just memorizing that one year's noise, so the SHAPE is borrowed
    from the subcategory and the LEVEL stays SKU-specific
    (see deseasonalized_level()). Read once per script run and passed down
    -- orders.parquet is ~100MB, too large to re-read per as-of week in a
    backtest sweep.
    """
    oh = pd.read_parquet(f"{data_dir}/orders.parquet", columns=["period", "sku_id", "order_qty"])
    sku_subcat = pd.read_parquet(f"{data_dir}/sku_master.parquet", columns=["sku_id", "subcategory"])
    oh = oh.merge(sku_subcat, on="sku_id", how="left")
    oh["week_of_year"] = oh["period"].str.split("-W").str[1].astype(int)
    wk = oh.groupby(["subcategory", "week_of_year"], as_index=False)["order_qty"].sum()
    subcat_mean = wk.groupby("subcategory")["order_qty"].transform("mean")
    wk["seasonal_index"] = (wk["order_qty"] / subcat_mean).clip(0.2, 5.0)
    return wk[["subcategory", "week_of_year", "seasonal_index"]]


def deseasonalized_level(dfc_agg: pd.DataFrame, as_of_week_num: int, period_index: pd.DataFrame,
                          seasonal_index: pd.DataFrame, sku_subcat: dict,
                          window_weeks: int = LONG_WINDOW_WEEKS) -> pd.DataFrame:
    """Per (sku_id, dc_id): mean of (actual_qty / that week's seasonal
    index) over the trailing window -- an estimate of the SKU-DC's
    underlying demand LEVEL with the seasonal wiggle removed, so it can be
    re-seasonalized for any target week (including one the trailing window
    never actually saw a comparable season for)."""
    lo = max(0, as_of_week_num - window_weeks + 1)
    win_periods = set(period_index.loc[(period_index.week_num >= lo) &
                                        (period_index.week_num <= as_of_week_num), "period"])
    w = dfc_agg[(dfc_agg.horizon == "in_sample") & (dfc_agg.period.isin(win_periods))].copy()
    w["subcategory"] = w["sku_id"].map(sku_subcat)
    w["week_of_year"] = w["period"].map(week_of_year)
    w = w.merge(seasonal_index, on=["subcategory", "week_of_year"], how="left")
    w["seasonal_index"] = w["seasonal_index"].fillna(1.0)
    w["deseasonalized_qty"] = w["actual_qty"] / w["seasonal_index"]
    return w.groupby(["sku_id", "dc_id"])["deseasonalized_qty"].mean().rename("deseason_level").reset_index()


# ------------------------------------------------------------ lead times ---
def build_leadtime_graph(data_dir: str, active_rdc, active_cdc, lanes: pd.DataFrame | None = None):
    """Cumulative (mean_days, var_days) from Plant to every RDC and CDC,
    walking the single fastest (min mean lead time) inbound lane at each
    node -- identical method to the annual `meio_optimizer.py`.

    `lanes` (None by default) is a lane table carrying a scenario's lead-time
    overrides for one target week. The fastest lane is still chosen on the
    published lane table -- a delay changes how long a lane takes, not which
    lane the network uses -- and its mean/std are then read from `lanes`."""
    base = pd.read_parquet(f"{data_dir}/network_lanes.parquet")
    values = None if lanes is None else lanes.set_index("lane_id")[["lead_time_mean_days", "lead_time_std_days"]]

    def primary_upstream(dest_ids, dest_type, allowed_origins=None):
        sub = base[base.destination_node_type == dest_type]
        if allowed_origins is not None:
            sub = sub[sub.origin_node_id.isin(allowed_origins)]
        sub = sub[sub.destination_node_id.isin(dest_ids)]
        best = sub.sort_values("lead_time_mean_days").drop_duplicates("destination_node_id", keep="first")
        if values is not None:
            best = best.assign(lead_time_mean_days=best.lane_id.map(values.lead_time_mean_days).to_numpy(),
                               lead_time_std_days=best.lane_id.map(values.lead_time_std_days).to_numpy())
        return best.set_index("destination_node_id")[
            ["origin_node_id", "lead_time_mean_days", "lead_time_std_days"]
        ]

    lanes = base

    pdc_ids = lanes.loc[lanes.destination_node_type == "PLANT_DC", "destination_node_id"].unique()
    plant_to_pdc = primary_upstream(pdc_ids, "PLANT_DC")
    pdc_to_rdc = primary_upstream(active_rdc, "REGIONAL_DC", allowed_origins=pdc_ids)
    rdc_to_cdc = primary_upstream(active_cdc, "CUSTOMER_DC", allowed_origins=active_rdc)

    rdc_leadtime, rdc_of_pdc = {}, {}
    for rdc, row in pdc_to_rdc.iterrows():
        pdc = row.origin_node_id
        rdc_of_pdc[rdc] = pdc
        p2p = plant_to_pdc.loc[pdc]
        mean = p2p.lead_time_mean_days + row.lead_time_mean_days
        var = p2p.lead_time_std_days ** 2 + row.lead_time_std_days ** 2
        rdc_leadtime[rdc] = (mean, var)

    cdc_leadtime, cdc_primary_rdc = {}, {}
    for cdc, row in rdc_to_cdc.iterrows():
        rdc = row.origin_node_id
        cdc_primary_rdc[cdc] = rdc
        r_mean, r_var = rdc_leadtime[rdc]
        mean = r_mean + row.lead_time_mean_days
        var = r_var + row.lead_time_std_days ** 2
        cdc_leadtime[cdc] = (mean, var)

    return rdc_leadtime, cdc_leadtime, cdc_primary_rdc


# --------------------------------------------------------- demand stats ---
def bias_factor(dfc_agg: pd.DataFrame, as_of_week_num: int, period_index: pd.DataFrame,
                 window_weeks: int = SHORT_WINDOW_WEEKS) -> pd.DataFrame:
    """Per (sku_id, dc_id): trailing actual/forecast ratio over the last
    `window_weeks` in_sample weeks ending at as_of_week_num (inclusive).
    Only weeks where both actual_qty and forecast_qty exist and
    forecast_qty > 0 contribute. Falls back to 1.0 (no correction) for
    sku/dc pairs with no usable history."""
    lo = max(0, as_of_week_num - window_weeks + 1)
    win_periods = set(period_index.loc[(period_index.week_num >= lo) &
                                        (period_index.week_num <= as_of_week_num), "period"])
    w = dfc_agg[(dfc_agg.horizon == "in_sample") & (dfc_agg.period.isin(win_periods)) &
                (dfc_agg.forecast_qty > 0)]
    g = w.groupby(["sku_id", "dc_id"]).agg(
        actual_sum=("actual_qty", "sum"), forecast_sum=("forecast_qty", "sum")).reset_index()
    g["bias_factor"] = (g.actual_sum / g.forecast_sum).clip(0.5, 2.0)
    return g[["sku_id", "dc_id", "bias_factor"]]


def _trailing_stats(dfc_agg: pd.DataFrame, as_of_week_num: int, period_index: pd.DataFrame,
                     window_weeks: int) -> pd.DataFrame:
    """Per (sku_id, dc_id): mean/std of actual_qty over a trailing window of
    in_sample weeks up to and including as_of_week_num (no lookahead)."""
    lo = max(0, as_of_week_num - window_weeks + 1)
    win_periods = set(period_index.loc[(period_index.week_num >= lo) &
                                        (period_index.week_num <= as_of_week_num), "period"])
    w = dfc_agg[(dfc_agg.horizon == "in_sample") & (dfc_agg.period.isin(win_periods))]
    s = w.groupby(["sku_id", "dc_id"])["actual_qty"].agg(mean="mean", std="std").reset_index()
    s["cv"] = (s["std"] / s["mean"].replace(0, np.nan)).fillna(0).clip(upper=3)
    return s[["sku_id", "dc_id", "mean", "cv"]]


def blended_cv(dfc_agg: pd.DataFrame, as_of_week_num: int, period_index: pd.DataFrame,
               short_window: int = SHORT_WINDOW_WEEKS, long_window: int = LONG_WINDOW_WEEKS) -> pd.DataFrame:
    """Per (sku_id, dc_id): average of the short- and long-window trailing
    coefficient of variation of actual_qty."""
    short = _trailing_stats(dfc_agg, as_of_week_num, period_index, short_window).rename(columns={"cv": "cv_short"})
    long_ = _trailing_stats(dfc_agg, as_of_week_num, period_index, long_window).rename(columns={"cv": "cv_long"})
    m = short[["sku_id", "dc_id", "cv_short"]].merge(long_[["sku_id", "dc_id", "cv_long"]],
                                                       on=["sku_id", "dc_id"], how="outer")
    m["cv_short"] = m["cv_short"].fillna(m["cv_long"])
    m["cv_long"] = m["cv_long"].fillna(m["cv_short"])
    m["cv"] = ((m["cv_short"] + m["cv_long"]) / 2.0).fillna(0)
    return m[["sku_id", "dc_id", "cv"]]


def target_week_demand(dfc_agg: pd.DataFrame, as_of_week_num: int, period_index: pd.DataFrame,
                        seasonal_index: pd.DataFrame, sku_subcat: dict,
                        target_week_num: int | None = None, overrides=None) -> pd.DataFrame:
    """Bias-corrected mean, blended toward a de-seasonalized estimate, and
    blended-CV std for a target week, per (sku_id, dc_id). This is what the
    optimizer sizes that week's stock against.

    `target_week_num` defaults to `as_of_week_num + 1` (the normal weekly-
    recompute case: as-of this week, size next week). It can be set
    explicitly to size a week further out -- used by
    `roll_forward_policy.py` to build a full 52-week rolling policy from a
    single fixed as-of point (the last real week of history), since
    forward-horizon weeks have no realized actual_qty to trail against
    anyway (verified: bias_factor/blended_cv/deseasonalized_level all
    filter to horizon=="in_sample", which saturates at the same 2025 data
    regardless of how far as_of_week_num reaches into 2026 -- only the
    target week's own forecast_qty and seasonal_index genuinely vary).

    `seasonal_est = deseasonalized_level(sku, dc) * seasonal_index(subcategory, target week-of-year)`
    replaces a flat trailing-mean fallback (module docstring point 7's
    original fix): found 38% of active SKU-DC pairs have a literal $0
    forecast for any single given week despite real annual demand, worst
    for Seasonal-segment SKUs whose demand concentrates in OTHER weeks. A
    flat trailing average corrects the zero but also flattens genuine
    low-season weeks toward the mean, which both overstocks true off-season
    weeks and understocks true in-season ones. Reconstructing from a
    de-seasonalized level x that week's seasonal factor predicts a properly
    low estimate for a genuinely low-season week and a properly high one
    for an in-season week, instead of regressing everything to the mean.
    """
    if target_week_num is None:
        target_week_num = as_of_week_num + 1
    target_row = period_index[period_index.week_num == target_week_num]
    if target_row.empty:
        raise ValueError(f"No period at week_num {target_week_num} (end of available data)")
    target_period = target_row.iloc[0]["period"]
    target_wk_of_year = week_of_year(target_period)

    raw = dfc_agg[dfc_agg.period == target_period][["sku_id", "dc_id", "forecast_qty", "horizon"]].copy()
    bf = bias_factor(dfc_agg, as_of_week_num, period_index)
    cv = blended_cv(dfc_agg, as_of_week_num, period_index)
    dlvl = deseasonalized_level(dfc_agg, as_of_week_num, period_index, seasonal_index, sku_subcat)

    d = (raw.merge(bf, on=["sku_id", "dc_id"], how="left")
         .merge(cv, on=["sku_id", "dc_id"], how="left")
         .merge(dlvl, on=["sku_id", "dc_id"], how="left"))
    d["bias_factor"] = d["bias_factor"].fillna(1.0)
    d["cv"] = d["cv"].fillna(d["cv"].median() if d["cv"].notna().any() else 0.5)
    d["deseason_level"] = d["deseason_level"].fillna(0.0)

    d["subcategory"] = d["sku_id"].map(sku_subcat)
    si_target = seasonal_index[seasonal_index.week_of_year == target_wk_of_year][
        ["subcategory", "seasonal_index"]].rename(columns={"seasonal_index": "si_target"})
    d = d.merge(si_target, on="subcategory", how="left")
    d["si_target"] = d["si_target"].fillna(1.0)
    d["seasonal_est"] = d["deseason_level"] * d["si_target"]

    raw_target_mean = d["forecast_qty"] * d["bias_factor"]
    d["target_mean"] = MEAN_BLEND_ALPHA * raw_target_mean + (1 - MEAN_BLEND_ALPHA) * d["seasonal_est"]
    d["target_std"] = d["target_mean"] * d["cv"]
    d["target_period"] = target_period
    # drop the internal seasonal-lookup helper column -- every downstream
    # caller merges this output against `cost` (from load_sku_cost), which
    # has its own `subcategory` column; leaving this one in produced a
    # silent subcategory_x/subcategory_y split on every such merge instead
    # of a clean `subcategory` column (found while building alt_models/
    # clark_scarf_model.py -- confirmed the sizing math itself was
    # unaffected, only the reporting column in meio_recommendations_weekly.parquet
    # was blank for every RDC/CDC row).
    d = d.drop(columns=["subcategory"])
    # Scenario demand overrides change this estimate itself, not the raw forecast it
    # blends, so a known +25% is planned as +25% (models/overrides/meio.py).
    return overrides.demand(d, target_period) if overrides else d


# --------------------------------------------------------------- solving ---
def solve_policy(demand: pd.DataFrame, leadtime_by_dc: dict, cost: pd.DataFrame,
                  active_rdc, active_cdc, cdc_primary_rdc: dict = None, overrides=None) -> pd.DataFrame:
    """Classic (R,s,S) sizing over the protection interval P = R + L, matched
    to `supply_brain_generator_v5.derive_baseline_policy()` -- the function
    that produces this dataset's own baseline policy:

        SS = z * sqrt(P_wk * sigma_uplifted^2 + mu^2 * sigma_LT_wk^2)
        s  = mu * P_wk + SS
        S  = max(s + mu * max(REVIEW_WEEKS, ORDER_COVER_WEEKS), s * MIN_S_MULTIPLE)

    z is fixed per SKU segment (not a cost-derived critical ratio); sigma is
    uplifted per echelon before sizing (a parent absorbs its children's
    order variability on top of its own). No explicit RDC<-CDC demand
    pooling -- each node is sized directly against its own
    demand_forecast.parquet row, same as the baseline (see module docstring,
    point 5, for why explicit pooling was dropped). `cdc_primary_rdc` is
    accepted for call-site compatibility but unused.
    """
    d = demand[demand.dc_id.isin(list(active_rdc) + list(active_cdc))].copy()
    d["lt_mean_wk"] = d.dc_id.map(lambda x: leadtime_by_dc[x][0] / 7.0)
    d["lt_std_wk"] = d.dc_id.map(lambda x: np.sqrt(leadtime_by_dc[x][1]) / 7.0)
    d["sigma_uplift"] = d.dc_id.str[:3].map(SIGMA_UPLIFT).fillna(1.0)
    d["target_std_uplifted"] = d.target_std * d.sigma_uplift

    d = d.merge(cost, on=["sku_id", "dc_id"], how="left")
    period = d.target_period.iloc[0] if overrides and len(d) else None
    d["z"] = assign_z(d, overrides, period)

    prot_wk = REVIEW_WEEKS + d.lt_mean_wk
    d["sigma_lt_demand"] = np.sqrt(
        prot_wk * d.target_std_uplifted ** 2 + (d.target_mean ** 2) * d.lt_std_wk ** 2
    )
    d["safety_stock"] = (d.z * d.sigma_lt_demand).round(1)
    d["cycle_stock"] = (d.target_mean * prot_wk).round(1)
    d["reorder_point"] = (d.cycle_stock + d.safety_stock).round(1)

    cover_wk = max(REVIEW_WEEKS, ORDER_COVER_WEEKS)
    s_plus_cover = d.reorder_point + d.target_mean * cover_wk
    d["order_up_to_level"] = np.maximum(s_plus_cover, d.reorder_point * MIN_S_MULTIPLE).round(1)
    d["safety_stock_usd"] = (d.safety_stock * d.unit_cost_usd).round(2)

    from scipy.stats import norm
    d["target_csl"] = norm.cdf(d.z.clip(Z_MIN, Z_MAX))  # diagnostic only; z drives sizing, not this
    return d


def load_sourcing_map(data_dir: str) -> pd.DataFrame:
    """sku_sourcing_map.parquet: the actual routing each (dc_id, sku_id) travels
    -- (dc_id, sku_id, origin_node_id, rule). Includes RDC rows (which PDC
    each RDC-SKU sources from), which is what solve_pdc_policy needs."""
    return pd.read_parquet(f"{data_dir}/sku_sourcing_map.parquet")


def build_pdc_leadtime(data_dir: str, lanes: pd.DataFrame | None = None) -> dict:
    """(mean_days, std_days) for the Plant -> PDC lane feeding each PDC. `lanes` (None
    by default) carries a scenario's lead-time overrides for one target week."""
    lanes = pd.read_parquet(f"{data_dir}/network_lanes.parquet") if lanes is None else lanes
    plant_dc = pd.read_parquet(f"{data_dir}/plant_dc_master.parquet")
    plant_of_pdc = plant_dc.set_index("plant_dc_id")["plant_id"].to_dict()
    lane_lt = {(r.origin_node_id, r.destination_node_id):
               (r.lead_time_mean_days, r.lead_time_std_days)
               for r in lanes.itertuples(index=False)}
    return {pdc: lane_lt.get((plant, pdc), (0.5, 0.07)) for pdc, plant in plant_of_pdc.items()}


def load_sku_production_family(data_dir: str) -> dict:
    sku = pd.read_parquet(f"{data_dir}/sku_master.parquet", columns=["sku_id", "production_family"])
    return sku.set_index("sku_id")["production_family"].to_dict()


def load_plant_capacity(data_dir: str):
    """Weekly production capacity, keyed the same two-stage way
    `models/twin/digital_twin.py`'s own `_produce()` clips a build plan:
    first by (plant_id, production_family) line capacity
    (`plant_capability.parquet`), then by plant-wide capacity
    (`plant_master.parquet`). Returns
    (family_capacity: {(plant_id, production_family): units/wk},
     plant_capacity: {plant_id: units/wk},
     plant_of_pdc: {pdc_id: plant_id}, from `plant_dc_master.parquet` -- a PDC
     is co-located 1:1 with the plant that feeds it, verified against
     `network_lanes.parquet`'s Plant->PLANT_DC lanes, also 1:1).
    """
    fam_cap = pd.read_parquet(f"{data_dir}/plant_capability.parquet")
    family_capacity = {(r.plant_id, r.production_family): r.family_capacity_units_per_week
                        for r in fam_cap.itertuples(index=False)}
    plant_master = pd.read_parquet(f"{data_dir}/plant_master.parquet",
                                columns=["plant_id", "production_capacity_units_per_week"])
    plant_capacity = plant_master.set_index("plant_id")["production_capacity_units_per_week"].to_dict()
    plant_dc = pd.read_parquet(f"{data_dir}/plant_dc_master.parquet", columns=["plant_dc_id", "plant_id"])
    plant_of_pdc = plant_dc.set_index("plant_dc_id")["plant_id"].to_dict()
    return family_capacity, plant_capacity, plant_of_pdc


def clip_to_plant_capacity(thru: pd.DataFrame, data_dir: str, overrides=None,
                           period: str | None = None) -> pd.DataFrame:
    """Scale down `thru.mu` (required PDC throughput per sku) so no plant is
    ever asked to sustain more than it can actually build -- mirrors the
    twin's own two-stage production clip (family line, then plant-wide) so
    a PDC recommendation is never a target the plant physically cannot
    reach regardless of buffer or lookahead. Found necessary: PDC lookahead
    alone (point 9) barely moved internal_replen_fill_rate, because BOTH
    PDC and RDC internal fill dropped under the rolling policy roughly in
    proportion to how much MORE total replenishment volume it demanded --
    a real capacity ceiling, not a timing problem. Rationing is
    proportional within whichever group is over capacity (fair-share, not
    first-come), matching how a real plant would allocate a constrained
    line across the SKUs that share it. Adds `mu_requested`,
    `capacity_constrained` (bool) columns for transparency about when a
    recommendation had to be scaled back.
    """
    family_capacity, plant_capacity, plant_of_pdc = load_plant_capacity(data_dir)
    if overrides:   # a scenario's plant_capacity overrides active in this target week
        family_capacity, plant_capacity = overrides.capacity(family_capacity, plant_capacity, period)
    sku_family = load_sku_production_family(data_dir)

    t = thru.copy()
    t["mu_requested"] = t["mu"]
    t["plant_id"] = t["dc_id"].map(plant_of_pdc)
    t["production_family"] = t["sku_id"].map(sku_family)

    # stage 1: (plant, family) line capacity
    fam_required = t.groupby(["plant_id", "production_family"])["mu"].transform("sum")
    fam_cap = t.set_index(["plant_id", "production_family"]).index.map(family_capacity.get)
    fam_cap = pd.Series(fam_cap, index=t.index).fillna(np.inf).astype(float)
    fam_scale = np.minimum(1.0, fam_cap / fam_required.replace(0, np.nan)).fillna(1.0)
    t["mu"] = t["mu"] * fam_scale

    # stage 2: plant-wide capacity, on top of whatever stage 1 already clipped
    plant_required = t.groupby("plant_id")["mu"].transform("sum")
    plant_cap = t["plant_id"].map(plant_capacity).fillna(np.inf).astype(float)
    plant_scale = np.minimum(1.0, plant_cap / plant_required.replace(0, np.nan)).fillna(1.0)
    t["mu"] = t["mu"] * plant_scale

    t["capacity_constrained"] = (t["mu"] < t["mu_requested"] - 1e-6)
    return t.drop(columns=["plant_id", "production_family"])


def smoothed_rdc_demand_for_pdc(dfc_agg: pd.DataFrame, as_of_week_num: int, period_index: pd.DataFrame,
                                 seasonal_index: pd.DataFrame, sku_subcat: dict, active_rdc,
                                 target_week_num: int, window_weeks: int = PDC_SMOOTH_WEEKS,
                                 overrides=None) -> pd.DataFrame:
    """Per (sku_id, dc_id=RDC): target_mean averaged over a `window_weeks`
    window CENTERED on target_week_num (not a forward-only look-ahead --
    see docstring point 11 for why that wasn't enough), for sizing PDC
    throughput on a demand signal smoothed over roughly the same horizon
    the plant is already sized to buffer (PDC_SMOOTH_WEEKS). This is
    standard bullwhip-effect mitigation: decouple a production-constrained
    node from raw week-to-week demand swings while leaving downstream
    inventory-pull nodes (RDC/CDC) fully responsive -- they aren't touched
    by this, only the signal fed to solve_pdc_policy() is. Silently uses
    fewer weeks at either edge of the available horizon rather than
    erroring."""
    half = window_weeks // 2
    lo, hi = target_week_num - half, target_week_num - half + window_weeks - 1
    frames = []
    for wk in range(lo, hi + 1):
        if period_index[period_index.week_num == wk].empty:
            continue
        d = target_week_demand(dfc_agg, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                target_week_num=wk, overrides=overrides)
        frames.append(d[d.dc_id.isin(active_rdc)][["sku_id", "dc_id", "target_mean"]])
    allwk = pd.concat(frames, ignore_index=True)
    return allwk.groupby(["sku_id", "dc_id"], as_index=False)["target_mean"].mean()


def solve_pdc_policy(rdc_demand: pd.DataFrame, data_dir: str, cost: pd.DataFrame, overrides=None,
                     period: str | None = None, lanes: pd.DataFrame | None = None,
                     z_by_sku: dict | None = None) -> pd.DataFrame:
    """PDC (plant DC) policy, matching `derive_baseline_policy()`'s PDC
    branch: a PDC is pushed by production, not pulled -- its order-up-to is
    a holding ceiling sized on throughput, not a demand-driven trigger.

        throughput mu(pdc, sku) = sum of target_mean over every RDC-SKU that
                                   sources from that PDC (sku_sourcing_map.parquet)
        safety_stock = z * sqrt(lt_wk) * mu * SIGMA_UPLIFT["PDC"] * 0.5
        reorder_point = safety_stock + mu * lt_wk
        order_up_to   = mu * PDC_COVER_WEEKS + safety_stock

    `rdc_demand` needs sku_id, dc_id (RDC), target_mean -- pass
    `smoothed_rdc_demand_for_pdc()`'s output, not a single week's
    `solve_policy()` RDC slice: sizing PDC off only the current week's RDC
    pull was found to cost -8.2pp internal_replen_fill_rate in the full
    rolling simulation. Reuses this tool's own bias-corrected demand rather
    than re-deriving throughput from raw actuals, so the whole network
    stays on one consistent demand basis.

    After the raw throughput sum, `mu` is clipped to what the feeding plant
    can actually produce (`clip_to_plant_capacity()`) -- PDC lookahead alone
    did not fix the internal-fill-rate gap because it was never a timing
    problem at PDC specifically; both PDC and RDC internal fill dropped
    together, in proportion to how much extra volume the rolling policy
    demanded, which is a real capacity ceiling the twin's own production
    step enforces regardless of what this tool recommends. Output carries
    `mu_requested` and `capacity_constrained` so a capped recommendation is
    visible, not silently smaller.
    """
    smap = load_sourcing_map(data_dir)
    rdc_src = smap[smap.dc_id.str.startswith("RDC")][["dc_id", "sku_id", "origin_node_id"]].rename(
        columns={"dc_id": "rdc_id", "origin_node_id": "pdc_id"})

    merged = rdc_demand[["sku_id", "dc_id", "target_mean"]].merge(
        rdc_src, left_on=["sku_id", "dc_id"], right_on=["sku_id", "rdc_id"], how="inner")
    thru = merged.groupby(["pdc_id", "sku_id"], as_index=False)["target_mean"].sum().rename(
        columns={"pdc_id": "dc_id", "target_mean": "mu"})
    thru = thru[thru.mu > 0].copy()
    if thru.empty:
        return thru.assign(category=None, subcategory=None, segment=None, unit_cost_usd=None,
                            target_mean=None, target_std=None, lt_mean_wk=None, lt_std_wk=None,
                            target_csl=None, z=None, cycle_stock=None, safety_stock=None,
                            reorder_point=None, order_up_to_level=None, safety_stock_usd=None,
                            mu_requested=None, capacity_constrained=None)

    thru = clip_to_plant_capacity(thru, data_dir, overrides, period)

    pdc_lt = build_pdc_leadtime(data_dir, lanes)
    thru["lt_mean_days"] = thru.dc_id.map(lambda p: pdc_lt.get(p, (0.5, 0.07))[0])
    thru["lt_std_days"] = thru.dc_id.map(lambda p: pdc_lt.get(p, (0.5, 0.07))[1])
    thru["lt_mean_wk"] = thru.lt_mean_days / 7.0
    thru["lt_std_wk"] = thru.lt_std_days / 7.0

    thru = thru.merge(cost, on=["sku_id", "dc_id"], how="left")
    thru["z"] = assign_z(thru, overrides, period, z_by_sku)

    thru["safety_stock"] = (thru.z * np.sqrt(thru.lt_mean_wk) * thru.mu
                             * SIGMA_UPLIFT["PDC"] * 0.5).round(1)
    thru["cycle_stock"] = (thru.mu * thru.lt_mean_wk).round(1)
    thru["reorder_point"] = (thru.safety_stock + thru.cycle_stock).round(1)
    thru["order_up_to_level"] = (thru.mu * PDC_COVER_WEEKS + thru.safety_stock).round(1)
    thru["safety_stock_usd"] = (thru.safety_stock * thru.unit_cost_usd).round(2)

    from scipy.stats import norm
    thru["target_csl"] = norm.cdf(thru.z.clip(Z_MIN, Z_MAX))
    thru["target_mean"] = thru["mu"]
    thru["target_std"] = np.nan  # PDC sizing uses mu directly, not a std estimate -- see docstring
    return thru.drop(columns=["mu"])


def active_nodes(dfc_agg: pd.DataFrame):
    active_dc = sorted(dfc_agg["dc_id"].unique())
    return ([d for d in active_dc if d.startswith("RDC")],
            [d for d in active_dc if d.startswith("CDC")])
