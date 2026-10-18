"""
Shared loaders and estimation logic for the weekly MEIO tools -- optimized port.

Same estimator as final_submission/meio_weekly_lib.py (the numbered method notes 1-11
there apply unchanged and are reproduced at the end of this docstring), rebuilt for
speed:

* **polars for I/O and table work, numpy for the arithmetic.** No pandas on the hot
  path; pandas appears only at the boundary with the scenario override engine
  (models/overrides), which is shared with the Digital Twin and left untouched.
* **A dense demand panel.** demand_forecast.parquet is a complete (sku, dc) x week panel
  (7,110 pairs x 104 weeks). `DemandPanel` holds it as [pair, week] arrays, so a
  trailing window is a column slice instead of a filter over 739k rows.
* **Cached by as-of week.** bias_factor, blended_cv and deseasonalized_level depend on
  the as-of week only, never on the target week. The original recomputed them on
  every target_week_demand call -- six times per walk-forward week (the target week
  plus the five weeks PDC smoothing averages). Here they are computed once per as-of
  week and the per-(as-of, target) demand frame is cached too, so every policy build
  in a process after the first reuses them.
* **Bit-identical.** pandas' groupby sums are Kahan-compensated and its std is
  Welford's one-pass update; the loops below reproduce both in the same row order
  (runtime.py, tests/test_exactness.py), so every estimate matches the original to
  the last bit.

Public names and signatures follow the original; frames are polars DataFrames, and
`load_demand_forecast` returns a `DemandPanel` (its `.frame` is the aggregated table).

Method notes from the original module (unchanged)
-------------------------------------------------

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

import os

import numpy as np
import polars as pl
from scipy.stats import norm

import runtime as rt

REVIEW_WEEKS = 1.0               # weekly review cadence
ORDER_COVER_WEEKS = 1.5          # matches generator's calibrated order batch size
MIN_S_MULTIPLE = 1.2             # order-up-to must exceed reorder point by this much
PDC_COVER_WEEKS = 4.5            # plant DCs are pushed; order-up-to is a holding ceiling
PDC_SMOOTH_WEEKS = 5             # PDC throughput sizes off a window of RDC pull CENTERED on
                                 # the target week (docstring point 11)
MEAN_BLEND_ALPHA = 0.6           # weight on the target week's own (bias-corrected) forecast
SIGMA_UPLIFT = {"PDC": 1.45, "RDC": 1.25, "CDC": 1.00}
Z_BY_SEGMENT = {"Fast-Moving": 2.05, "Seasonal": 1.64, "Intermediate": 1.28}
Z_DEFAULT = 1.28
Z_MIN, Z_MAX = 0.84, 3.10        # clip service level to [80%, 99.9%] (diagnostic only, doesn't drive z)
SHORT_WINDOW_WEEKS = 13
LONG_WINDOW_WEEKS = 52

# Per-SKU z override research seam (see the original); empty by default.
Z_OVERRIDE_BY_SKU: dict = {}

_CLUSTER_Z_CACHE: dict | None = None


def _cluster_z_by_sku() -> dict:
    """sku_id -> safety_factor_z from the CANONICAL, LIVE segmentation output
    (output datasets/segmentation/sku_clusters.csv) -- adopted as MEIO's live
    default service-level source (project decision, 2026-10-18). Ported forward
    from final_submission/meio_weekly_lib.py so the optimized engine matches it."""
    global _CLUSTER_Z_CACHE
    if _CLUSTER_Z_CACHE is None:
        path = os.path.join(rt.REPO_ROOT, "output datasets", "segmentation", "sku_clusters.csv")
        c = rt.read_table(path, columns=["sku_id", "safety_factor_z"])
        _CLUSTER_Z_CACHE = dict(zip(c["sku_id"].to_list(), [float(v) for v in c["safety_factor_z"].to_list()]))
    return _CLUSTER_Z_CACHE

DEMAND_COLUMNS = ["sku_id", "dc_id", "forecast_qty", "horizon", "bias_factor", "cv", "deseason_level",
                  "si_target", "seasonal_est", "target_mean", "target_std", "target_period"]


def _map(keys, mapping: dict, dtype=np.float64) -> np.ndarray:
    """mapping.get per key, NaN where absent (pandas Series.map on a dict)."""
    return np.array([mapping.get(k, np.nan) for k in keys], dtype=dtype)


def z_values(sku_ids, segments, z_by_sku: dict | None = None) -> np.ndarray:
    """z per row before any scenario hook: z_by_sku (or, when None, Z_OVERRIDE_BY_SKU)
    where set, else the canonical live segmentation cluster ladder, else the old
    3-way segment ladder, else Z_DEFAULT. Priority matches
    final_submission/meio_weekly_lib.py::assign_z()."""
    mapping = Z_OVERRIDE_BY_SKU if z_by_sku is None else z_by_sku
    seg = _map(segments, Z_BY_SEGMENT)
    cluster = _map(sku_ids, _cluster_z_by_sku())
    base = np.where(np.isnan(cluster), seg, cluster)
    if mapping:
        z = _map(sku_ids, mapping)
        z = np.where(np.isnan(z), base, z)
    else:
        z = base
    return np.where(np.isnan(z), Z_DEFAULT, z)


def assign_z(df: pl.DataFrame, overrides=None, period: str | None = None,
             z_by_sku: dict | None = None) -> np.ndarray:
    """z per row of `df` (needs sku_id, segment; dc_id when `overrides` is set). The
    override engine's service.target_csl hook runs on a pandas view, as before."""
    z = z_values(df["sku_id"].to_list(), df["segment"].to_list(), z_by_sku)
    if overrides:
        return _hook_z(overrides, df.select(["dc_id", "sku_id"]), z, period)
    return z


def _hook_z(overrides, keys: pl.DataFrame, z: np.ndarray, period) -> np.ndarray:
    import pandas as pd
    out = overrides.z(keys.to_pandas(), pd.Series(z), period)
    return np.asarray(out, dtype=np.float64)


def target_csl(z: np.ndarray) -> np.ndarray:
    return norm.cdf(np.clip(z, Z_MIN, Z_MAX))


# --------------------------------------------------------------------- IO ---
def load_period_index(data_dir: str) -> pl.DataFrame:
    """Distinct (period, week_start_date) pairs from demand_forecast.parquet, in order."""
    dfc = rt.read_table(f"{data_dir}/demand_forecast.parquet", columns=["period", "week_start_date"])
    idx = dfc.unique().sort("week_start_date", maintain_order=True)
    return idx.with_columns(pl.int_range(pl.len(), dtype=pl.Int64).alias("week_num"))


def week_of_year(period: str) -> int:
    """'2025-W01' -> 1. Every year in this dataset is exactly 52 weeks."""
    return int(period.split("-W")[1])


def period_of(period_index: pl.DataFrame, week_num: int) -> str | None:
    hit = period_index.filter(pl.col("week_num") == week_num)
    return hit["period"][0] if hit.height else None


def week_num_of(period_index: pl.DataFrame, period: str) -> int:
    return int(period_index.filter(pl.col("period") == period)["week_num"][0])


class WeekDemand:
    """One target week's demand estimate, as arrays aligned to the panel's row order
    (sku_id, dc_id). `frame()` gives the original column layout as a polars frame."""

    __slots__ = ("panel", "period", "pair", "forecast_qty", "horizon", "bias_factor", "cv",
                 "deseason_level", "si_target", "seasonal_est", "target_mean", "target_std", "_frame")

    def __init__(self, panel, period, pair, **cols):
        self.panel, self.period, self.pair = panel, period, pair
        for k, v in cols.items():
            setattr(self, k, v)
        self._frame = None

    def __len__(self):
        return len(self.pair)

    @property
    def sku_id(self) -> np.ndarray:
        return self.panel.pair_sku[self.pair]

    @property
    def dc_id(self) -> np.ndarray:
        return self.panel.pair_dc[self.pair]

    def with_target(self, target_mean, target_std) -> "WeekDemand":
        cols = {k: getattr(self, k) for k in ("forecast_qty", "horizon", "bias_factor", "cv", "deseason_level",
                                               "si_target", "seasonal_est")}
        return WeekDemand(self.panel, self.period, self.pair, target_mean=np.asarray(target_mean, dtype=float),
                          target_std=np.asarray(target_std, dtype=float), **cols)

    def frame(self) -> pl.DataFrame:
        if self._frame is None:
            self._frame = pl.DataFrame({
                "sku_id": self.sku_id, "dc_id": self.dc_id, "forecast_qty": self.forecast_qty,
                "horizon": [self.horizon] * len(self), "bias_factor": self.bias_factor, "cv": self.cv,
                "deseason_level": self.deseason_level, "si_target": self.si_target,
                "seasonal_est": self.seasonal_est, "target_mean": self.target_mean,
                "target_std": self.target_std, "target_period": [self.period] * len(self),
            }, schema_overrides={"horizon": pl.String, "target_period": pl.String})
        return self._frame


class DemandPanel:
    """demand_forecast.parquet at true (period, sku_id, dc_id) grain, as dense arrays.

    The original's first fix (module docstring point 1) is unchanged: B2B-customer rows
    are summed to (period, week_start_date, horizon, sku_id, dc_id) immediately on load,
    and every quantity is an integer, so those sums are exact in any order."""

    def __init__(self, data_dir: str):
        raw = rt.read_table(f"{data_dir}/demand_forecast.parquet")
        keys = ["period", "week_start_date", "horizon", "sku_id", "dc_id"]
        agg = (raw.group_by(keys)
               .agg(pl.col("forecast_qty").sum(), pl.col("forecast_baseline_qty").sum(),
                    pl.col("forecast_promo_lift_qty").sum(),
                    pl.col("actual_qty").fill_null(0.0).fill_nan(0.0).sum().cast(pl.Float64))
               .sort(keys))
        self.frame = agg
        self.period_index = load_period_index(data_dir)
        periods = self.period_index["period"].to_list()
        self.periods = periods
        self.week_of = {p: i for i, p in enumerate(periods)}
        W = len(periods)

        pairs = agg.select(["sku_id", "dc_id"]).unique().sort(["sku_id", "dc_id"])
        self.pair_sku = np.array(pairs["sku_id"].to_list(), dtype=object)
        self.pair_dc = np.array(pairs["dc_id"].to_list(), dtype=object)
        P = len(pairs)
        pair_of = {(s, d): i for i, (s, d) in enumerate(zip(self.pair_sku, self.pair_dc))}

        p_idx = np.array([pair_of[(s, d)] for s, d in zip(agg["sku_id"].to_list(), agg["dc_id"].to_list())])
        w_idx = np.array([self.week_of[p] for p in agg["period"].to_list()])
        if len(set(zip(p_idx.tolist(), w_idx.tolist()))) != len(p_idx):
            raise ValueError("demand_forecast.parquet has more than one horizon/week_start_date for a period")
        self.present = np.zeros((P, W), dtype=bool)
        self.forecast = np.zeros((P, W), dtype=np.int64)
        self.actual = np.zeros((P, W))
        self.present[p_idx, w_idx] = True
        self.forecast[p_idx, w_idx] = agg["forecast_qty"].to_numpy()
        self.actual[p_idx, w_idx] = agg["actual_qty"].to_numpy()
        horizon = dict(zip(agg["period"].to_list(), agg["horizon"].to_list()))
        self.horizon = [horizon.get(p) for p in periods]
        self.in_sample = np.array([h == "in_sample" for h in self.horizon])
        self.n_pairs, self.n_weeks = P, W
        self.dc_ids = sorted(set(self.pair_dc.tolist()))

        self._trailing: dict = {}
        self._weeks: dict = {}
        self._si_ctx = None

    # ------------------------------------------------------------ helpers --
    def _window(self, as_of: int, window: int) -> list[int]:
        lo = max(0, as_of - window + 1)
        return [w for w in range(lo, as_of + 1) if 0 <= w < self.n_weeks and self.in_sample[w]]

    def _season(self, seasonal_index: pl.DataFrame, sku_subcat: dict):
        """Per-pair subcategory codes and a [subcategory, week_of_year] index table, rebuilt
        only when a different seasonal_index / sku_subcat object is passed."""
        ctx = self._si_ctx
        if ctx is not None and ctx[0] is seasonal_index and ctx[1] is sku_subcat:
            return ctx[2], ctx[3]
        subcats = sorted(set(seasonal_index["subcategory"].to_list()))
        code = {s: i for i, s in enumerate(subcats)}
        table = np.full((len(subcats) + 1, 54), np.nan)      # last row: subcategory unknown
        for s, w, v in seasonal_index.select(["subcategory", "week_of_year", "seasonal_index"]).iter_rows():
            table[code[s], int(w)] = v
        pair_code = np.array([code.get(sku_subcat.get(s), len(subcats)) for s in self.pair_sku])
        self._si_ctx = (seasonal_index, sku_subcat, pair_code, table)
        self._trailing.clear()
        self._weeks.clear()
        return pair_code, table

    def trailing(self, as_of: int, seasonal_index: pl.DataFrame, sku_subcat: dict):
        """(bias_factor, cv, deseason_level) per pair as of `as_of`, NaN where the original's
        left merge found no row -- bias_factor(), blended_cv() and deseasonalized_level()."""
        pair_code, table = self._season(seasonal_index, sku_subcat)
        hit = self._trailing.get(as_of)
        if hit is not None:
            return hit
        P = self.n_pairs

        # bias_factor: trailing actual/forecast over SHORT_WINDOW_WEEKS, forecast > 0 rows
        sa, sf, n = np.zeros(P), np.zeros(P), np.zeros(P, dtype=np.int64)
        for w in self._window(as_of, SHORT_WINDOW_WEEKS):
            m = self.present[:, w] & (self.forecast[:, w] > 0)
            sa[m] += self.actual[m, w]                   # integer-valued: exact in any order
            sf[m] += self.forecast[m, w]
            n[m] += 1
        with np.errstate(invalid="ignore", divide="ignore"):
            bf = np.where(n > 0, np.clip(sa / np.where(n > 0, sf, 1.0), 0.5, 2.0), np.nan)

        # blended_cv: mean (Kahan) / std (Welford) of actual over short and long windows
        cv_s, has_s = self._cv(as_of, SHORT_WINDOW_WEEKS)
        cv_l, has_l = self._cv(as_of, LONG_WINDOW_WEEKS)
        cs = np.where(has_s, cv_s, cv_l)
        cl = np.where(has_l, cv_l, cs)
        cv = np.where(has_s | has_l, (cs + cl) / 2.0, np.nan)
        cv = np.where((has_s | has_l) & np.isnan(cv), 0.0, cv)

        # deseasonalized_level: Kahan mean of actual / seasonal_index over the long window
        s, comp, n = np.zeros(P), np.zeros(P), np.zeros(P, dtype=np.int64)
        for w in self._window(as_of, LONG_WINDOW_WEEKS):
            m = self.present[:, w]
            si = table[pair_code[m], week_of_year(self.periods[w])]
            si = np.where(np.isnan(si), 1.0, si)
            val = self.actual[m, w] / si
            n[m] += 1
            sm = s[m]
            y = val - comp[m]
            t = sm + y
            c = (t - sm) - y
            c[c != c] = 0.0
            comp[m] = c
            s[m] = t
        with np.errstate(invalid="ignore", divide="ignore"):
            dlvl = np.where(n > 0, s / np.maximum(n, 1), np.nan)

        out = (bf, cv, dlvl)
        self._trailing[as_of] = out
        return out

    def _cv(self, as_of: int, window: int):
        """_trailing_stats(): cv = std/mean (0 where mean is 0 or std undefined), clip 3."""
        P = self.n_pairs
        s, comp = np.zeros(P), np.zeros(P)
        mean, m2 = np.zeros(P), np.zeros(P)
        n = np.zeros(P, dtype=np.int64)
        for w in self._window(as_of, window):
            m = self.present[:, w]
            val = self.actual[m, w]
            n[m] += 1
            sm = s[m]
            y = val - comp[m]
            t = sm + y
            c = (t - sm) - y
            c[c != c] = 0.0
            comp[m] = c
            s[m] = t
            old = mean[m]
            new = old + (val - old) / n[m]
            mean[m] = new
            m2[m] = m2[m] + (val - new) * (val - old)
        has = n > 0
        with np.errstate(invalid="ignore", divide="ignore"):
            mu = np.where(has, s / np.maximum(n, 1), np.nan)
            std = np.where(n > 1, np.sqrt(m2 / np.maximum(n - 1, 1)), np.nan)
            cv = std / np.where(mu == 0, np.nan, mu)
        cv = np.where(np.isnan(cv), 0.0, cv)
        cv = np.minimum(cv, 3.0)
        return np.where(has, cv, np.nan), has

    def week(self, as_of: int, target: int, seasonal_index: pl.DataFrame, sku_subcat: dict) -> WeekDemand:
        """target_week_demand() before any scenario hook, cached per (as_of, target)."""
        pair_code, table = self._season(seasonal_index, sku_subcat)
        key = (as_of, target)
        hit = self._weeks.get(key)
        if hit is not None:
            return hit
        if not 0 <= target < self.n_weeks:
            raise ValueError(f"No period at week_num {target} (end of available data)")
        bf_all, cv_all, dl_all = self.trailing(as_of, seasonal_index, sku_subcat)
        pair = np.flatnonzero(self.present[:, target])
        period = self.periods[target]
        fq = self.forecast[pair, target]
        bf = np.where(np.isnan(bf_all[pair]), 1.0, bf_all[pair])
        cv = cv_all[pair]
        ok = ~np.isnan(cv)
        cv = np.where(ok, cv, np.median(cv[ok]) if ok.any() else 0.5)
        dl = np.where(np.isnan(dl_all[pair]), 0.0, dl_all[pair])
        si = table[pair_code[pair], week_of_year(period)]
        si = np.where(np.isnan(si), 1.0, si)
        seasonal_est = dl * si
        raw_target_mean = fq * bf
        target_mean = MEAN_BLEND_ALPHA * raw_target_mean + (1 - MEAN_BLEND_ALPHA) * seasonal_est
        wd = WeekDemand(self, period, pair, forecast_qty=fq, horizon=self.horizon[target], bias_factor=bf,
                        cv=cv, deseason_level=dl, si_target=si, seasonal_est=seasonal_est,
                        target_mean=target_mean, target_std=target_mean * cv)
        self._weeks[key] = wd
        return wd


def load_demand_forecast(data_dir: str) -> DemandPanel:
    """demand_forecast.parquet aggregated to true (period, horizon, sku_id, dc_id) grain."""
    return DemandPanel(data_dir)


def load_sku_cost(data_dir: str) -> pl.DataFrame:
    """SKU master joined to DC-specific cost parameters via (dc_id, subcategory): one row
    per (sku_id, dc_id), holding_per_unit_week and stockout_cost_unit DC-specific."""
    sku = rt.read_table(f"{data_dir}/sku_master.parquet")
    cost_p = rt.read_table(f"{data_dir}/cost_parameters.parquet")
    sku_c = sku.join(cost_p, on="subcategory", how="left", coalesce=True)
    # numpy, not a polars expression: polars may fold "/ 100.0" into "* 0.01", one ULP off
    f = {c: sku_c[c].cast(pl.Float64).to_numpy() for c in ("storage_cost_per_unit_year_usd", "unit_cost_usd",
                                                         "inventory_carrying_pct_annual", "margin_usd",
                                                         "stockout_cost_multiplier")}
    sku_c = sku_c.with_columns(
        pl.Series("holding_per_unit_week", (f["storage_cost_per_unit_year_usd"] + f["unit_cost_usd"]
                                            * f["inventory_carrying_pct_annual"] / 100.0) / 52.0),
        pl.Series("stockout_cost_unit", f["margin_usd"] * f["stockout_cost_multiplier"]))
    return sku_c.select(["sku_id", "dc_id", "category", "subcategory", "segment", "unit_cost_usd",
                         "holding_per_unit_week", "stockout_cost_unit"])


def load_sku_subcategory(data_dir: str) -> dict:
    sku = rt.read_table(f"{data_dir}/sku_master.parquet", columns=["sku_id", "subcategory"])
    return dict(zip(sku["sku_id"].to_list(), sku["subcategory"].to_list()))


def build_seasonal_index(data_dir: str) -> pl.DataFrame:
    """Multiplicative seasonal index per (subcategory, week_of_year) pooled across every
    SKU in the subcategory over orders.parquet's 156 weeks (docstring point 8)."""
    oh = rt.read_table(f"{data_dir}/orders.parquet", columns=["period", "sku_id", "order_qty"])
    sku_subcat = rt.read_table(f"{data_dir}/sku_master.parquet", columns=["sku_id", "subcategory"])
    oh = oh.join(sku_subcat, on="sku_id", how="left")
    oh = oh.with_columns(pl.col("period").str.split("-W").list.get(1).cast(pl.Int64).alias("week_of_year"))
    wk = (oh.filter(pl.col("subcategory").is_not_null())
          .group_by(["subcategory", "week_of_year"]).agg(pl.col("order_qty").sum())
          .sort(["subcategory", "week_of_year"]))
    # integer sums: the subcategory mean is exact (sum / count) in any order
    wk = wk.with_columns((pl.col("order_qty").cast(pl.Float64)
                          / (pl.col("order_qty").sum().over("subcategory").cast(pl.Float64)
                             / pl.len().over("subcategory").cast(pl.Float64)))
                         .clip(0.2, 5.0).alias("seasonal_index"))
    return wk.select(["subcategory", "week_of_year", "seasonal_index"])


def target_week_demand(dfc: DemandPanel, as_of_week_num: int, period_index, seasonal_index: pl.DataFrame,
                       sku_subcat: dict, target_week_num: int | None = None, overrides=None) -> WeekDemand:
    """Bias-corrected mean, blended toward a de-seasonalized estimate, and blended-CV std
    for a target week (default as_of + 1), per (sku_id, dc_id). Scenario demand
    overrides (models/overrides PlanHooks) change this estimate itself, as before."""
    if target_week_num is None:
        target_week_num = as_of_week_num + 1
    wd = dfc.week(as_of_week_num, target_week_num, seasonal_index, sku_subcat)
    if not overrides:
        return wd
    out = overrides.demand(wd.frame().to_pandas(), wd.period)
    return wd.with_target(out["target_mean"].to_numpy(dtype=float), out["target_std"].to_numpy(dtype=float))


# ------------------------------------------------------------ lead times ---
def _lane_table(lanes) -> pl.DataFrame:
    if lanes is None or isinstance(lanes, pl.DataFrame):
        return lanes
    return pl.from_pandas(lanes)


def build_leadtime_graph(data_dir: str, active_rdc, active_cdc, lanes=None):
    """Cumulative (mean_days, var_days) from Plant to every RDC and CDC, walking the
    single fastest inbound lane at each node. `lanes` (None by default) carries a
    scenario's lead-time overrides; the fastest lane is still chosen on the published
    table and its mean/std are then read from `lanes`."""
    base = rt.read_table(f"{data_dir}/network_lanes.parquet")
    over = _lane_table(lanes)
    values = None if over is None else {
        lid: (m, s) for lid, m, s in over.select(["lane_id", "lead_time_mean_days", "lead_time_std_days"]).iter_rows()}

    def primary_upstream(dest_ids, dest_type, allowed_origins=None):
        sub = base.filter(pl.col("destination_node_type") == dest_type)
        if allowed_origins is not None:
            sub = sub.filter(pl.col("origin_node_id").is_in(list(allowed_origins)))
        sub = sub.filter(pl.col("destination_node_id").is_in(list(dest_ids)))
        sub = sub[rt.pandas_argsort(sub["lead_time_mean_days"].to_numpy()).tolist()]
        best, seen = {}, set()
        for lid, dest, orig, m, s in sub.select(["lane_id", "destination_node_id", "origin_node_id",
                                                 "lead_time_mean_days", "lead_time_std_days"]).iter_rows():
            if dest in seen:
                continue
            seen.add(dest)
            if values is not None:
                m, s = values.get(lid, (np.nan, np.nan))
            best[dest] = (orig, np.float64(m), np.float64(s))      # numpy scalars, as iterrows gave
        return best

    pdc_ids = base.filter(pl.col("destination_node_type") == "PLANT_DC")["destination_node_id"].unique(
        maintain_order=True).to_list()
    plant_to_pdc = primary_upstream(pdc_ids, "PLANT_DC")
    pdc_to_rdc = primary_upstream(active_rdc, "REGIONAL_DC", allowed_origins=pdc_ids)
    rdc_to_cdc = primary_upstream(active_cdc, "CUSTOMER_DC", allowed_origins=active_rdc)

    rdc_leadtime = {}
    for rdc, (pdc, m, s) in pdc_to_rdc.items():
        _, pm, ps = plant_to_pdc[pdc]
        rdc_leadtime[rdc] = (pm + m, ps ** 2 + s ** 2)
    cdc_leadtime, cdc_primary_rdc = {}, {}
    for cdc, (rdc, m, s) in rdc_to_cdc.items():
        cdc_primary_rdc[cdc] = rdc
        r_mean, r_var = rdc_leadtime[rdc]
        cdc_leadtime[cdc] = (r_mean + m, r_var + s ** 2)
    return rdc_leadtime, cdc_leadtime, cdc_primary_rdc


def leadtime_weeks(dc_ids, leadtime_by_dc: dict):
    """(lt_mean_wk, lt_std_wk) per row: mean / 7 and sqrt(var) / 7, as the original's maps."""
    mean = np.array([leadtime_by_dc[d][0] for d in dc_ids], dtype=float) / 7.0
    std = np.sqrt(np.array([leadtime_by_dc[d][1] for d in dc_ids], dtype=float)) / 7.0
    return mean, std


# ------------------------------------------------------------- cost index --
class CostIndex:
    """load_sku_cost() as a (dc_id, sku_id) lookup: the original's left merge, gathered."""

    COLS = ("category", "subcategory", "segment", "unit_cost_usd", "holding_per_unit_week", "stockout_cost_unit")

    def __init__(self, cost: pl.DataFrame):
        self.cost = cost
        keys = list(zip(cost["dc_id"].to_list(), cost["sku_id"].to_list()))
        self.row = {}
        for i, k in enumerate(keys):
            self.row.setdefault(k, i)                 # a left merge keeps every match; keys are unique
        self.cols = {c: np.array(cost[c].to_list(), dtype=object if cost.schema[c] == pl.String else float)
                     for c in self.COLS}

    def gather(self, dc_ids, sku_ids) -> dict:
        idx = np.array([self.row.get(k, -1) for k in zip(dc_ids, sku_ids)], dtype=np.int64)
        miss = idx < 0
        out = {}
        for c, arr in self.cols.items():
            v = arr[np.where(miss, 0, idx)] if len(arr) else np.empty(len(idx), dtype=arr.dtype)
            if arr.dtype == object:
                v = np.where(miss, None, v)
            else:
                v = np.where(miss, np.nan, v)
            out[c] = v
        return out


_COST_INDEX: dict = {}


def cost_index(cost: pl.DataFrame) -> CostIndex:
    hit = _COST_INDEX.get(id(cost))
    if hit is None or hit.cost is not cost:
        hit = CostIndex(cost)
        _COST_INDEX[id(cost)] = hit
    return hit


# --------------------------------------------------------------- solving ---
def solve_policy(demand: WeekDemand, leadtime_by_dc: dict, cost: pl.DataFrame,
                 active_rdc, active_cdc, cdc_primary_rdc: dict = None, overrides=None) -> pl.DataFrame:
    """Classic (R,s,S) sizing over P = R + L (MEIO Weekly), matched to the dataset's
    derive_baseline_policy():  SS = z sqrt(P sigma_up^2 + mu^2 sigma_LT^2),
    s = mu P + SS,  S = max(s + mu * cover, s * MIN_S_MULTIPLE)."""
    nodes = set(active_rdc) | set(active_cdc)
    dc_all = demand.dc_id
    keep = np.array([d in nodes for d in dc_all], dtype=bool)
    rows = np.flatnonzero(keep)
    dc, sku = dc_all[rows], demand.sku_id[rows]
    mean, std = demand.target_mean[rows], demand.target_std[rows]
    lt_mean_wk, lt_std_wk = leadtime_weeks(dc, leadtime_by_dc)
    uplift = np.array([SIGMA_UPLIFT.get(d[:3], np.nan) for d in dc], dtype=float)
    uplift = np.where(np.isnan(uplift), 1.0, uplift)
    std_up = std * uplift
    c = cost_index(cost).gather(dc, sku)
    period = demand.period if overrides and len(rows) else None
    z = z_values(sku, c["segment"])
    if overrides:
        z = _hook_z(overrides, pl.DataFrame({"dc_id": dc, "sku_id": sku}), z, period)

    prot_wk = REVIEW_WEEKS + lt_mean_wk
    sigma_lt_demand = np.sqrt(prot_wk * std_up ** 2 + (mean ** 2) * lt_std_wk ** 2)
    safety_stock = np.round(z * sigma_lt_demand, 1)
    cycle_stock = np.round(mean * prot_wk, 1)
    reorder_point = np.round(cycle_stock + safety_stock, 1)
    cover_wk = max(REVIEW_WEEKS, ORDER_COVER_WEEKS)
    order_up_to = np.round(np.maximum(reorder_point + mean * cover_wk, reorder_point * MIN_S_MULTIPLE), 1)
    return pl.DataFrame({
        "sku_id": sku, "dc_id": dc, "forecast_qty": demand.forecast_qty[rows],
        "horizon": [demand.horizon] * len(rows), "bias_factor": demand.bias_factor[rows], "cv": demand.cv[rows],
        "deseason_level": demand.deseason_level[rows], "si_target": demand.si_target[rows],
        "seasonal_est": demand.seasonal_est[rows], "target_mean": mean, "target_std": std,
        "target_period": [demand.period] * len(rows), "lt_mean_wk": lt_mean_wk, "lt_std_wk": lt_std_wk,
        "sigma_uplift": uplift, "target_std_uplifted": std_up, **c, "z": z,
        "sigma_lt_demand": sigma_lt_demand, "safety_stock": safety_stock, "cycle_stock": cycle_stock,
        "reorder_point": reorder_point, "order_up_to_level": order_up_to,
        "safety_stock_usd": np.round(safety_stock * c["unit_cost_usd"], 2), "target_csl": target_csl(z),
    }, schema_overrides={"horizon": pl.String, "target_period": pl.String, "category": pl.String,
                         "subcategory": pl.String, "segment": pl.String}, strict=False)


def load_sourcing_map(data_dir: str) -> pl.DataFrame:
    """sku_sourcing_map.parquet: the routing each (dc_id, sku_id) travels."""
    return rt.read_table(f"{data_dir}/sku_sourcing_map.parquet")


def build_pdc_leadtime(data_dir: str, lanes=None) -> dict:
    """(mean_days, std_days) for the Plant -> PDC lane feeding each PDC."""
    ln = rt.read_table(f"{data_dir}/network_lanes.parquet") if lanes is None else _lane_table(lanes)
    plant_dc = rt.read_table(f"{data_dir}/plant_dc_master.parquet")
    plant_of_pdc = dict(zip(plant_dc["plant_dc_id"].to_list(), plant_dc["plant_id"].to_list()))
    lane_lt = {(o, d): (m, s) for o, d, m, s in ln.select(
        ["origin_node_id", "destination_node_id", "lead_time_mean_days", "lead_time_std_days"]).iter_rows()}
    return {pdc: lane_lt.get((plant, pdc), (0.5, 0.07)) for pdc, plant in plant_of_pdc.items()}


def load_sku_production_family(data_dir: str) -> dict:
    sku = rt.read_table(f"{data_dir}/sku_master.parquet", columns=["sku_id", "production_family"])
    return dict(zip(sku["sku_id"].to_list(), sku["production_family"].to_list()))


def load_plant_capacity(data_dir: str):
    """(family_capacity {(plant, family): units/wk}, plant_capacity {plant: units/wk},
    plant_of_pdc {pdc: plant}) -- the twin's two-stage production clip, by key."""
    fam_cap = rt.read_table(f"{data_dir}/plant_capability.parquet")
    family_capacity = {(p, f): c for p, f, c in fam_cap.select(
        ["plant_id", "production_family", "family_capacity_units_per_week"]).iter_rows()}
    pm = rt.read_table(f"{data_dir}/plant_master.parquet", columns=["plant_id", "production_capacity_units_per_week"])
    plant_capacity = dict(zip(pm["plant_id"].to_list(), pm["production_capacity_units_per_week"].to_list()))
    plant_dc = rt.read_table(f"{data_dir}/plant_dc_master.parquet", columns=["plant_dc_id", "plant_id"])
    plant_of_pdc = dict(zip(plant_dc["plant_dc_id"].to_list(), plant_dc["plant_id"].to_list()))
    return family_capacity, plant_capacity, plant_of_pdc


def _codes(keys: list) -> tuple[np.ndarray, int]:
    """Group codes for a list of hashable keys, -1 where any part is missing (pandas
    groupby drops NaN keys)."""
    table, codes = {}, np.empty(len(keys), dtype=np.int64)
    for i, k in enumerate(keys):
        if k is None or (isinstance(k, tuple) and any(v is None for v in k)):
            codes[i] = -1
            continue
        codes[i] = table.setdefault(k, len(table))
    return codes, len(table)


def _transform_sum(values: np.ndarray, keys: list) -> np.ndarray:
    """groupby(keys)[col].transform('sum'): Kahan per group, NaN for missing keys."""
    codes, n = _codes(keys)
    out = np.full(len(values), np.nan)
    ok = codes >= 0
    if ok.any():
        sums, _ = rt.kahan_sum(values[ok], codes[ok], n)
        out[ok] = sums[codes[ok]]
    return out


def clip_to_plant_capacity(dc_ids, sku_ids, mu: np.ndarray, data_dir: str, overrides=None,
                           period: str | None = None):
    """Scale PDC throughput `mu` down so no plant is asked for more than it can build:
    family line capacity, then plant-wide (docstring point 10). Returns
    (mu, mu_requested, capacity_constrained)."""
    family_capacity, plant_capacity, plant_of_pdc = load_plant_capacity(data_dir)
    if overrides:
        family_capacity, plant_capacity = overrides.capacity(family_capacity, plant_capacity, period)
    sku_family = load_sku_production_family(data_dir)
    mu_requested = mu.copy()
    plant = [plant_of_pdc.get(d) for d in dc_ids]
    fam = [sku_family.get(s) for s in sku_ids]

    fam_required = _transform_sum(mu, list(zip(plant, fam)))
    fam_cap = np.array([family_capacity.get((p, f)) if p is not None and f is not None else None
                        for p, f in zip(plant, fam)], dtype=float)
    fam_cap = np.where(np.isnan(fam_cap), np.inf, fam_cap)
    with np.errstate(invalid="ignore", divide="ignore"):
        fam_scale = np.minimum(1.0, fam_cap / np.where(fam_required == 0, np.nan, fam_required))
    mu = mu * np.where(np.isnan(fam_scale), 1.0, fam_scale)

    plant_required = _transform_sum(mu, plant)
    plant_cap = np.array([plant_capacity.get(p, np.nan) if p is not None else np.nan for p in plant], dtype=float)
    plant_cap = np.where(np.isnan(plant_cap), np.inf, plant_cap)
    with np.errstate(invalid="ignore", divide="ignore"):
        plant_scale = np.minimum(1.0, plant_cap / np.where(plant_required == 0, np.nan, plant_required))
    mu = mu * np.where(np.isnan(plant_scale), 1.0, plant_scale)
    return mu, mu_requested, mu < mu_requested - 1e-6


def smoothed_rdc_demand_for_pdc(dfc: DemandPanel, as_of_week_num: int, period_index, seasonal_index,
                                sku_subcat: dict, active_rdc, target_week_num: int,
                                window_weeks: int = PDC_SMOOTH_WEEKS, overrides=None) -> pl.DataFrame:
    """Per (sku_id, RDC): target_mean averaged over `window_weeks` CENTERED on the target
    week (docstring point 11), fewer at the edges of the horizon."""
    sku, dc, mean = smoothed_rdc_arrays(dfc, as_of_week_num, seasonal_index, sku_subcat, active_rdc,
                                        target_week_num, window_weeks, overrides)
    return pl.DataFrame({"sku_id": sku, "dc_id": dc, "target_mean": mean})


def smoothed_rdc_arrays(dfc: DemandPanel, as_of_week_num: int, seasonal_index, sku_subcat: dict, active_rdc,
                        target_week_num: int, window_weeks: int = PDC_SMOOTH_WEEKS, overrides=None):
    half = window_weeks // 2
    lo, hi = target_week_num - half, target_week_num - half + window_weeks - 1
    rdc = set(active_rdc)
    pairs, vals = [], []
    for wk in range(lo, hi + 1):
        if not 0 <= wk < dfc.n_weeks:
            continue
        d = target_week_demand(dfc, as_of_week_num, None, seasonal_index, sku_subcat, target_week_num=wk,
                               overrides=overrides)
        m = np.array([x in rdc for x in d.dc_id], dtype=bool)
        pairs.append(d.pair[m])
        vals.append(d.target_mean[m])
    if not pairs:
        raise ValueError("No objects to concatenate")
    pair = np.concatenate(pairs)
    val = np.concatenate(vals)
    uniq, codes = np.unique(pair, return_inverse=True)       # pair order == (sku_id, dc_id) order
    mean = rt.kahan_mean(val, codes, len(uniq))
    return dfc.pair_sku[uniq], dfc.pair_dc[uniq], mean


class PdcStatic:
    """The variant-independent part of solve_pdc_policy for one target week: throughput,
    the capacity clip, lead times and cost. `finish(z)` adds the z-dependent sizing."""

    def __init__(self, sku, dc, mean, data_dir: str, cost: pl.DataFrame, overrides=None, period=None, lanes=None):
        smap = load_sourcing_map(data_dir)
        src = smap.filter(pl.col("dc_id").str.starts_with("RDC"))
        src_of = {}
        for rdc_id, s, pdc in src.select(["dc_id", "sku_id", "origin_node_id"]).iter_rows():
            src_of.setdefault((s, rdc_id), []).append(pdc)
        # inner merge, left order; then groupby (pdc, sku) sum, sorted by key
        m_pdc, m_sku, m_val = [], [], []
        for s, d, v in zip(sku, dc, mean):
            for pdc in src_of.get((s, d), ()):
                m_pdc.append(pdc)
                m_sku.append(s)
                m_val.append(v)
        keys = sorted(set(zip(m_pdc, m_sku)))
        code_of = {k: i for i, k in enumerate(keys)}
        codes = np.array([code_of[k] for k in zip(m_pdc, m_sku)], dtype=np.int64)
        mu, _ = rt.kahan_sum(np.array(m_val, dtype=float), codes, len(keys)) if keys else (np.zeros(0), None)
        keep = mu > 0
        self.dc = np.array([k[0] for k in keys], dtype=object)[keep]
        self.sku = np.array([k[1] for k in keys], dtype=object)[keep]
        self.empty = not keep.any()
        self.overrides, self.period = overrides, period
        if self.empty:
            return
        mu, self.mu_requested, self.constrained = clip_to_plant_capacity(
            self.dc, self.sku, mu[keep], data_dir, overrides, period)
        self.mu = mu
        pdc_lt = build_pdc_leadtime(data_dir, lanes)
        self.lt_mean_days = np.array([pdc_lt.get(p, (0.5, 0.07))[0] for p in self.dc], dtype=float)
        self.lt_std_days = np.array([pdc_lt.get(p, (0.5, 0.07))[1] for p in self.dc], dtype=float)
        self.lt_mean_wk = self.lt_mean_days / 7.0
        self.lt_std_wk = self.lt_std_days / 7.0
        self.c = cost_index(cost).gather(self.dc, self.sku)
        self.cycle_stock = np.round(self.mu * self.lt_mean_wk, 1)
        self._sqrt_lt = np.sqrt(self.lt_mean_wk)

    def z(self, z_by_sku: dict | None) -> np.ndarray:
        z = z_values(self.sku, self.c["segment"], z_by_sku)
        if self.overrides:
            z = _hook_z(self.overrides, pl.DataFrame({"dc_id": self.dc, "sku_id": self.sku}), z, self.period)
        return z

    def policy_arrays(self, z_by_sku: dict | None):
        """(safety_stock, reorder_point, order_up_to_level), rounded as the original."""
        z = self.z(z_by_sku)
        ss = np.round(z * self._sqrt_lt * self.mu * SIGMA_UPLIFT["PDC"] * 0.5, 1)
        rop = np.round(ss + self.cycle_stock, 1)
        oul = np.round(self.mu * PDC_COVER_WEEKS + ss, 1)
        return z, ss, rop, oul

    def frame(self, z_by_sku: dict | None) -> pl.DataFrame:
        cols = ["dc_id", "sku_id", "mu_requested", "capacity_constrained", "lt_mean_days", "lt_std_days",
                "lt_mean_wk", "lt_std_wk", "category", "subcategory", "segment", "unit_cost_usd",
                "holding_per_unit_week", "stockout_cost_unit", "z", "safety_stock", "cycle_stock", "reorder_point",
                "order_up_to_level", "safety_stock_usd", "target_csl", "target_mean", "target_std"]
        if self.empty:
            return pl.DataFrame({c: [] for c in cols})
        z, ss, rop, oul = self.policy_arrays(z_by_sku)
        return pl.DataFrame({
            "dc_id": self.dc, "sku_id": self.sku, "mu_requested": self.mu_requested,
            "capacity_constrained": self.constrained, "lt_mean_days": self.lt_mean_days,
            "lt_std_days": self.lt_std_days, "lt_mean_wk": self.lt_mean_wk, "lt_std_wk": self.lt_std_wk,
            **self.c, "z": z, "safety_stock": ss, "cycle_stock": self.cycle_stock, "reorder_point": rop,
            "order_up_to_level": oul, "safety_stock_usd": np.round(ss * self.c["unit_cost_usd"], 2),
            "target_csl": target_csl(z), "target_mean": self.mu, "target_std": np.full(len(self.mu), np.nan),
        }, schema_overrides={"category": pl.String, "subcategory": pl.String, "segment": pl.String}, strict=False)


def solve_pdc_policy(rdc_demand: pl.DataFrame, data_dir: str, cost: pl.DataFrame, overrides=None,
                     period: str | None = None, lanes=None, z_by_sku: dict | None = None) -> pl.DataFrame:
    """PDC (plant DC) policy, matching derive_baseline_policy()'s PDC branch:
        mu(pdc, sku)  = sum of target_mean over every RDC-SKU sourcing from that PDC
        safety_stock  = z * sqrt(lt_wk) * mu * SIGMA_UPLIFT["PDC"] * 0.5
        reorder_point = safety_stock + mu * lt_wk
        order_up_to   = mu * PDC_COVER_WEEKS + safety_stock
    with mu clipped to plant capacity first (docstring points 6, 9-11)."""
    st = PdcStatic(rdc_demand["sku_id"].to_list(), rdc_demand["dc_id"].to_list(),
                   rdc_demand["target_mean"].to_numpy(), data_dir, cost, overrides, period, lanes)
    return st.frame(z_by_sku)


def active_nodes(dfc: DemandPanel):
    return ([d for d in dfc.dc_ids if d.startswith("RDC")],
            [d for d in dfc.dc_ids if d.startswith("CDC")])
