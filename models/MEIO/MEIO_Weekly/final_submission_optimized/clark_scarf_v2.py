"""
Clark-Scarf v2 -- Continuous Pooling Factor + Monte Carlo DDLT (optimized port)
===============================================================================

The same model as final_submission/clark_scarf_v2.py -- continuous pooling factor
alpha, Gamma-mixture demand-during-lead-time by Monte Carlo or by deterministic
quadrature, the netting discount, scenario hooks -- with `solve()` split in two:

  WeekPlan         everything that does not depend on the variant being sized: the
                   target week's demand, the CDC and RDC row layout, downstream pooling
                   sums, lead times, cost.
  Endpoints        the three DDLT distributions (CDC, RDC independent, RDC pooled).
                   For Monte Carlo these are the gamma draws; they depend on the RNG
                   stream and the demand, never on alpha, netting_discount or z.
  plan.finish()    a variant's alpha / netting_discount / z map applied on top: the
                   service-level quantile, the alpha blend, the netting credit.

That split is what makes the research sweeps cheap. Every variant built from the same
fresh seed consumed exactly the same gamma draws in the original (netting_discount and
alpha only rescale them after the fact, z only picks a different quantile), so one pass
of draws now serves all of them: netting_discount_sweep's five values, the twenty-one
(cluster, alpha) combos per seed of segment_alpha_real_data, and so on. Results are
bit-identical to building each variant separately -- same draws, same order, same
arithmetic (tests/test_equivalence.py).

Also faster: the per-column np.quantile loop is one vectorised call per distinct
service level (same order statistics, same interpolation, so identical values), and
the quadrature bisection runs in column chunks across threads (scipy's special
functions release the GIL; each column's arithmetic is unchanged).

Why not CUDA: the gamma draws must come from numpy's own stream to reproduce the
published results, and that stream is sequential. For the quadrature path the laptop
RTX A2000 (weak FP64) measured 0.42 s per bisection against 0.27 s for 8 CPU threads,
so the CPU threads are used.
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime as rt                       # noqa: E402  (sets sys.path for models/, models/twin/)
import meio_weekly_lib as lib              # noqa: E402

N_SIMS = 4000

ALPHA_OVERRIDE_BY_SKU: dict = {}

QUAD_NODES = 64       # equiprobable lead-time nodes for ddlt="quadrature"
QUAD_ITERS = 40       # bisection steps on the mixture CDF (bracket / 2^40)

KEEP = ["dc_id", "sku_id", "category", "subcategory", "segment", "safety_stock",
        "reorder_point", "order_up_to_level", "target_csl", "target_period", "target_mean", "target_std",
        "lt_mean_wk", "lt_std_wk", "z", "cycle_stock", "unit_cost_usd"]
POLICY_COLS = ["dc_id", "sku_id", "safety_stock", "reorder_point", "order_up_to_level"]


def resolve_alpha(sku_ids, default_alpha: float, alpha_by_sku: dict | None = None) -> np.ndarray:
    """Per-row alpha: alpha_by_sku (or, when None, ALPHA_OVERRIDE_BY_SKU) where set, else
    default_alpha."""
    mapping = ALPHA_OVERRIDE_BY_SKU if alpha_by_sku is None else alpha_by_sku
    if mapping:
        a = np.array([mapping.get(s, np.nan) for s in sku_ids], dtype=float)
        return np.where(np.isnan(a), default_alpha, a)
    return np.full(len(sku_ids), default_alpha, dtype=float)


def _gamma_params(mean, std):
    """Method-of-moments Gamma(shape, scale) from (mean, std), vectorized; a point mass
    at `mean` wherever std<=0 or mean<=0."""
    mean = np.asarray(mean, dtype=float)
    std = np.asarray(std, dtype=float)
    safe = (mean > 1e-9) & (std > 1e-9)
    shape = np.where(safe, (mean / np.maximum(std, 1e-9)) ** 2, 1e6)
    scale = np.where(safe, (std ** 2) / np.maximum(mean, 1e-9), np.maximum(mean, 1e-9) / 1e6)
    return shape, scale


# ------------------------------------------------------------------ DDLT --
def _quantiles(samples: np.ndarray, csl: np.ndarray) -> np.ndarray:
    """np.quantile(samples[:, i], csl[i]) for every column i, one call per distinct
    csl. np.quantile along axis 0 takes the same order statistics and the same linear
    interpolation per column, so the values are identical to the per-column loop."""
    out = np.empty(samples.shape[1])
    for u in np.unique(csl):
        cols = np.flatnonzero(csl == u)
        out[cols] = np.quantile(samples[:, cols], u, axis=0)
    return out


def _blocks(n: int, parts: int) -> list:
    return [b for b in np.array_split(np.arange(n), parts) if len(b)]


class McEndpoint:
    """Monte Carlo DDLT for every line at once: the draws monte_carlo_ddlt() makes, kept,
    so any number of service-level vectors can be read off them.

    `threads` > 1 splits the elementwise parameter arithmetic by rows and the quantiles
    by columns across a thread pool (numpy releases the GIL); every element and column
    is computed exactly as before. The two gamma draws stay single calls on `rng`."""

    def __init__(self, target_mean, target_std, lt_mean_wk, lt_std_wk, rng: np.random.Generator,
                 n_sims: int = N_SIMS, pool: ThreadPoolExecutor | None = None):
        target_mean = np.asarray(target_mean, dtype=float)
        target_std = np.asarray(target_std, dtype=float)
        n = len(target_mean)
        self.pool = pool
        lt_shape, lt_scale = _gamma_params(lt_mean_wk, lt_std_wk)
        L = rng.gamma(lt_shape[None, :], lt_scale[None, :], size=(n_sims, n))
        if pool is None or n_sims * n < 200_000:
            P = lib.REVIEW_WEEKS + L
            del L
            d_shape, d_scale = _gamma_params(target_mean[None, :] * P, target_std[None, :] * np.sqrt(P))
            del P
        else:
            d_shape, d_scale = np.empty_like(L), np.empty_like(L)

            def params(rows):
                P = lib.REVIEW_WEEKS + L[rows[0]:rows[-1] + 1]
                d_shape[rows[0]:rows[-1] + 1], d_scale[rows[0]:rows[-1] + 1] = _gamma_params(
                    target_mean[None, :] * P, target_std[None, :] * np.sqrt(P))
            list(pool.map(params, _blocks(n_sims, pool._max_workers * 2)))
            del L
        self.samples = rng.gamma(d_shape, d_scale)
        del d_shape, d_scale
        self.cycle_stock = self.samples.mean(axis=0)
        self._cache: dict = {}

    def _quantile(self, cols: np.ndarray, u: float) -> np.ndarray:
        if self.pool is None or len(cols) < 256:
            return np.quantile(self.samples[:, cols], u, axis=0)
        parts = _blocks(len(cols), self.pool._max_workers * 2)
        return np.concatenate(list(self.pool.map(lambda b: np.quantile(self.samples[:, cols[b]], u, axis=0), parts)))

    def result(self, csl: np.ndarray):
        """(reorder_point, safety_stock, cycle_stock) at per-row target_csl."""
        csl = np.asarray(csl, dtype=float)
        n = len(csl)
        rop = np.empty(n)
        for u in np.unique(csl):
            cols = np.flatnonzero(csl == u)
            done, vals = self._cache.get(u, (None, None))
            if done is None:
                done, vals = np.zeros(n, dtype=bool), np.empty(n)
                self._cache[u] = (done, vals)
            need = cols[~done[cols]]
            if len(need):
                vals[need] = self._quantile(need, u)
                done[need] = True
            rop[cols] = vals[cols]
        return rop, np.maximum(0.0, rop - self.cycle_stock), self.cycle_stock


def monte_carlo_ddlt(target_mean, target_std, lt_mean_wk, lt_std_wk, target_csl,
                     rng: np.random.Generator, n_sims: int = N_SIMS):
    """Vectorized Monte Carlo DDLT for every (sku, dc) line at once.
    Returns (reorder_point, safety_stock, cycle_stock) arrays."""
    ep = McEndpoint(target_mean, target_std, lt_mean_wk, lt_std_wk, rng, n_sims)
    if np.isscalar(target_csl):
        rop = np.quantile(ep.samples, target_csl, axis=0)
        return rop, np.maximum(0.0, rop - ep.cycle_stock), ep.cycle_stock
    return ep.result(target_csl)


def _quadrature_chunk(mean, std, lt_mean_wk, lt_std_wk, csl, nodes, iters):
    from scipy.special import gammainc
    from scipy.stats import gamma
    lt_shape, lt_scale = _gamma_params(lt_mean_wk, lt_std_wk)
    u = (np.arange(nodes) + 0.5) / nodes
    P = lib.REVIEW_WEEKS + gamma.ppf(u[:, None], lt_shape[None, :], scale=lt_scale[None, :])
    shape, scale = _gamma_params(mean[None, :] * P, std[None, :] * np.sqrt(P))
    q = gamma.ppf(csl[None, :], shape, scale=scale)
    lo, hi = q.min(axis=0), q.max(axis=0)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        below = gammainc(shape, mid[None, :] / scale).mean(axis=0) < csl
        lo, hi = np.where(below, mid, lo), np.where(below, hi, mid)
    return 0.5 * (lo + hi)


def quadrature_ddlt(target_mean, target_std, lt_mean_wk, lt_std_wk, target_csl,
                    nodes: int = QUAD_NODES, iters: int = QUAD_ITERS, threads: int | None = None):
    """monte_carlo_ddlt's distribution without sampling: the target_csl root of the
    Gamma mixture CDF over `nodes` equiprobable lead-time quantiles, by bisection.
    Columns are independent, so chunks run on `threads` threads (default: the machine,
    or 1 inside a worker pool) with each column's arithmetic unchanged."""
    mean = np.asarray(target_mean, dtype=float)
    std = np.asarray(target_std, dtype=float)
    ltm = np.asarray(lt_mean_wk, dtype=float)
    lts = np.asarray(lt_std_wk, dtype=float)
    csl = np.array(np.broadcast_to(np.asarray(target_csl, dtype=float), mean.shape))
    n = len(mean)
    threads = threads or rt.intra_threads()
    if threads <= 1 or n < 512:
        rop = _quadrature_chunk(mean, std, ltm, lts, csl, nodes, iters) if n else np.zeros(0)
    else:
        chunks = [c for c in np.array_split(np.arange(n), threads * 2) if len(c)]
        with ThreadPoolExecutor(threads) as ex:
            parts = list(ex.map(lambda c: _quadrature_chunk(mean[c], std[c], ltm[c], lts[c], csl[c], nodes, iters),
                                chunks))
        rop = np.concatenate(parts)
    cycle_stock = mean * (lib.REVIEW_WEEKS + ltm)
    return rop, np.maximum(0.0, rop - cycle_stock), cycle_stock


class QuadEndpoint:
    def __init__(self, target_mean, target_std, lt_mean_wk, lt_std_wk):
        self.args = (np.asarray(target_mean, dtype=float), np.asarray(target_std, dtype=float),
                     np.asarray(lt_mean_wk, dtype=float), np.asarray(lt_std_wk, dtype=float))
        self._cache: dict = {}

    def result(self, csl: np.ndarray):
        key = np.asarray(csl, dtype=float).tobytes()
        if key not in self._cache:
            self._cache[key] = quadrature_ddlt(*self.args, csl)
        return self._cache[key]


def _ddlt(method: str, *args, rng=None, n_sims: int = N_SIMS):
    if method == "quadrature":
        return quadrature_ddlt(*args)
    if method == "monte_carlo":
        return monte_carlo_ddlt(*args, rng, n_sims=n_sims)
    raise ValueError(f"ddlt must be 'monte_carlo' or 'quadrature', got {method!r}")


# ------------------------------------------------------------- the week --
@dataclass(frozen=True)
class Variant:
    """What a sweep varies on top of one week's demand and draws."""
    alpha: float = 0.2
    netting_discount: float = 1.0
    alpha_by_sku: dict | None = field(default=None, hash=False, compare=False)
    z_by_sku: dict | None = field(default=None, hash=False, compare=False)


def apply_demand_surge(demand: lib.WeekDemand, affected_nodes: list, changes: dict, as_of_week_num: int,
                       target_week_num: int) -> lib.WeekDemand:
    """scenario_engine.apply_demand_surge_to_target_demand on the week's arrays: scale
    target_mean/target_std for affected nodes inside the scenario's window."""
    if "demand_surge_pct" not in changes or not affected_nodes:
        return demand
    duration_weeks = int(changes.get("duration_weeks", 10 ** 6))
    start_week_offset = int(changes.get("start_week_offset", 0))
    wk_lo = as_of_week_num + start_week_offset + 1
    wk_hi = as_of_week_num + start_week_offset + duration_weeks
    if not (wk_lo <= target_week_num <= wk_hi):
        return demand
    nodes = set(affected_nodes)
    mask = np.array([d in nodes for d in demand.dc_id], dtype=bool)
    if not mask.any():
        return demand
    multiplier = 1.0 + float(changes["demand_surge_pct"]) / 100.0
    mean, std = demand.target_mean.copy(), demand.target_std.copy()
    mean[mask] = mean[mask] * multiplier
    std[mask] = std[mask] * multiplier
    return demand.with_target(mean, std)


class WeekPlan:
    """The variant-independent part of solve() for one target week."""

    def __init__(self, demand: lib.WeekDemand, leadtime_by_dc: dict, cost: pl.DataFrame, active_rdc, active_cdc,
                 cdc_primary_rdc: dict, overrides=None):
        self.period = demand.period if len(demand) else None
        self.overrides = overrides
        ci = lib.cost_index(cost)
        dc_all, sku_all = demand.dc_id, demand.sku_id

        # --- CDC base echelon: demand rows at active CDCs, in demand order (sku, dc)
        cdc_set = set(active_cdc)
        rows = np.flatnonzero(np.array([d in cdc_set for d in dc_all], dtype=bool))
        self.c_dc, self.c_sku = dc_all[rows], sku_all[rows]
        self.c_mean, self.c_std = demand.target_mean[rows], demand.target_std[rows]
        self.c_lt_mean, self.c_lt_std = lib.leadtime_weeks(self.c_dc, leadtime_by_dc)
        self.c_cost = ci.gather(self.c_dc, self.c_sku)
        self.c_std_up = self.c_std * lib.SIGMA_UPLIFT["CDC"]

        # --- RDC: own demand outer-joined (sorted by sku) to its served CDCs' sums
        r_dc, r_sku, own_mean, own_std = [], [], [], []
        row_of: dict = {}
        by_dc: dict = {}
        for i, d in enumerate(dc_all):
            by_dc.setdefault(d, []).append(i)
        c_row_codes = np.full(len(rows), -1, dtype=np.int64)
        for r in active_rdc:
            own = by_dc.get(r, [])
            own_by_sku = {sku_all[i]: i for i in own}
            served = {c for c, p in cdc_primary_rdc.items() if p == r}
            down_rows = [k for k, d in enumerate(self.c_dc) if d in served]
            skus = sorted(set(own_by_sku) | {self.c_sku[k] for k in down_rows})
            for s in skus:
                row_of[(r, s)] = len(r_dc)
                r_dc.append(r)
                r_sku.append(s)
                i = own_by_sku.get(s)
                own_mean.append(demand.target_mean[i] if i is not None else 0.0)
                own_std.append(demand.target_std[i] if i is not None else 0.0)
            for k in down_rows:
                c_row_codes[k] = row_of[(r, self.c_sku[k])]
        self.r_dc = np.array(r_dc, dtype=object)
        self.r_sku = np.array(r_sku, dtype=object)
        self.own_mean = np.array(own_mean, dtype=float)
        self.own_std = np.array(own_std, dtype=float)
        n_r = len(self.r_dc)
        self._down = c_row_codes >= 0
        self._down_codes = c_row_codes[self._down]
        self.down_mean = self._down_sum(self.c_mean)
        self.down_var = rt.numpy_sum(self.c_std[self._down] ** 2, self._down_codes, n_r) if n_r else np.zeros(0)
        self.r_lt_mean, self.r_lt_std = (lib.leadtime_weeks(self.r_dc, leadtime_by_dc) if n_r
                                         else (np.zeros(0), np.zeros(0)))
        self.r_cost = ci.gather(self.r_dc, self.r_sku)
        self.own_std_up = self.own_std * lib.SIGMA_UPLIFT["RDC"]
        self.mu_echelon = self.own_mean + self.down_mean
        self.sigma_echelon = np.sqrt(self.own_std_up ** 2 + self.down_var)

    def _down_sum(self, values: np.ndarray) -> np.ndarray:
        """groupby(sku).agg('sum') over each RDC's served CDC rows: Kahan, CDC row order."""
        if not len(self.r_dc):
            return np.zeros(0)
        s, _ = rt.kahan_sum(values[self._down], self._down_codes, len(self.r_dc))
        return s

    # ---------------------------------------------------------- endpoints --
    def endpoints(self, ddlt: str, rng=None, n_sims: int = N_SIMS, pool: ThreadPoolExecutor | None = None):
        """(CDC, RDC independent, RDC pooled) DDLT endpoints, drawn in the original order."""
        if ddlt == "monte_carlo":
            c = McEndpoint(self.c_mean, self.c_std_up, self.c_lt_mean, self.c_lt_std, rng, n_sims, pool)
            i = McEndpoint(self.own_mean, self.own_std_up, self.r_lt_mean, self.r_lt_std, rng, n_sims, pool)
            p = McEndpoint(self.mu_echelon, self.sigma_echelon, self.r_lt_mean, self.r_lt_std, rng, n_sims, pool)
            return c, i, p
        if ddlt == "quadrature":
            return (QuadEndpoint(self.c_mean, self.c_std_up, self.c_lt_mean, self.c_lt_std),
                    QuadEndpoint(self.own_mean, self.own_std_up, self.r_lt_mean, self.r_lt_std),
                    QuadEndpoint(self.mu_echelon, self.sigma_echelon, self.r_lt_mean, self.r_lt_std))
        raise ValueError(f"ddlt must be 'monte_carlo' or 'quadrature', got {ddlt!r}")

    # -------------------------------------------------------------- finish --
    def _z(self, dc, sku, segment, z_by_sku):
        z = lib.z_values(sku, segment, z_by_sku)
        if self.overrides:
            z = lib._hook_z(self.overrides, pl.DataFrame({"dc_id": dc, "sku_id": sku}), z, self.period)
        return z

    def finish(self, endpoints, variant: Variant) -> dict:
        """The variant's CDC and RDC rows as unrounded arrays (the original's frames
        just before `out`)."""
        ep_c, ep_i, ep_p = endpoints
        cover_wk = max(lib.REVIEW_WEEKS, lib.ORDER_COVER_WEEKS)

        z_c = self._z(self.c_dc, self.c_sku, self.c_cost["segment"], variant.z_by_sku)
        csl_c = lib.target_csl(z_c)
        rop_c, ss_c, cyc_c = ep_c.result(csl_c)
        S_c = np.maximum(rop_c + self.c_mean * cover_wk, rop_c * lib.MIN_S_MULTIPLE)

        down_ss = self._down_sum(ss_c)
        down_S = self._down_sum(S_c)
        z_r = self._z(self.r_dc, self.r_sku, self.r_cost["segment"], variant.z_by_sku)
        csl_r = lib.target_csl(z_r)
        rop_i, ss_i, cyc_i = ep_i.result(csl_r)
        rop_p, ss_p_raw, cyc_p = ep_p.result(csl_r)
        ss_p = np.maximum(0.0, ss_p_raw - variant.netting_discount * down_ss)

        a = resolve_alpha(self.r_sku, variant.alpha, variant.alpha_by_sku)
        if self.overrides:                 # policy.pooling_alpha
            a = self.overrides.alpha(pl.DataFrame({"dc_id": self.r_dc, "sku_id": self.r_sku}).to_pandas(),
                                     a, self.period)
        ss_r = a * ss_p + (1 - a) * ss_i
        cyc_r = a * cyc_p + (1 - a) * cyc_i
        rop_r = cyc_r + ss_r
        s_endpoint_p = np.maximum(rop_r + self.mu_echelon * cover_wk - down_S, rop_r)
        s_endpoint_i = np.maximum(rop_r + self.own_mean * cover_wk, rop_r * lib.MIN_S_MULTIPLE)
        S_r = a * s_endpoint_p + (1 - a) * s_endpoint_i
        return {"cdc": (z_c, csl_c, rop_c, ss_c, cyc_c, S_c), "rdc": (z_r, csl_r, rop_r, ss_r, cyc_r, S_r)}

    def policy_arrays(self, endpoints, variant: Variant):
        """(dc_id, sku_id, safety_stock, reorder_point, order_up_to_level) for the
        twin's policy file, CDC rows then RDC rows, rounded as the original."""
        f = self.finish(endpoints, variant)
        _, _, rop_c, ss_c, _, S_c = f["cdc"]
        _, _, rop_r, ss_r, _, S_r = f["rdc"]
        return (np.concatenate([self.c_dc, self.r_dc]), np.concatenate([self.c_sku, self.r_sku]),
                np.round(np.concatenate([ss_c, ss_r]), 1), np.round(np.concatenate([rop_c, rop_r]), 1),
                np.round(np.concatenate([S_c, S_r]), 1))

    def frame(self, endpoints, variant: Variant) -> pl.DataFrame:
        """solve()'s output columns, CDC rows then RDC rows."""
        f = self.finish(endpoints, variant)
        z_c, csl_c, rop_c, ss_c, cyc_c, S_c = f["cdc"]
        z_r, csl_r, rop_r, ss_r, cyc_r, S_r = f["rdc"]
        nc, nr = len(self.c_dc), len(self.r_dc)

        def cat(a, b):
            return np.concatenate([np.asarray(a), np.asarray(b)])
        ss = np.round(cat(ss_c, ss_r), 1)
        unit_cost = cat(self.c_cost["unit_cost_usd"], self.r_cost["unit_cost_usd"]).astype(float)
        out = pl.DataFrame({
            "dc_id": cat(self.c_dc, self.r_dc), "sku_id": cat(self.c_sku, self.r_sku),
            "category": cat(self.c_cost["category"], self.r_cost["category"]),
            "subcategory": cat(self.c_cost["subcategory"], self.r_cost["subcategory"]),
            "segment": cat(self.c_cost["segment"], self.r_cost["segment"]),
            "safety_stock": ss, "reorder_point": np.round(cat(rop_c, rop_r), 1),
            "order_up_to_level": np.round(cat(S_c, S_r), 1), "target_csl": cat(csl_c, csl_r),
            "target_period": [self.period] * (nc + nr), "target_mean": cat(self.c_mean, self.own_mean),
            "target_std": cat(self.c_std, self.own_std), "lt_mean_wk": cat(self.c_lt_mean, self.r_lt_mean),
            "lt_std_wk": cat(self.c_lt_std, self.r_lt_std), "z": cat(z_c, z_r),
            "cycle_stock": np.round(cat(cyc_c, cyc_r), 1), "unit_cost_usd": unit_cost,
            "safety_stock_usd": np.round(ss * unit_cost, 2),
        }, schema_overrides={"category": pl.String, "subcategory": pl.String, "segment": pl.String,
                             "target_period": pl.String}, strict=False)
        return out


def week_plan(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc, cost, active_rdc,
              active_cdc, cdc_primary_rdc, target_week_num, overrides=None, scenario_changes=None,
              scenario_affected_nodes=None) -> WeekPlan:
    demand = lib.target_week_demand(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat,
                                    target_week_num=target_week_num, overrides=overrides)
    if scenario_changes and scenario_affected_nodes:
        demand = apply_demand_surge(demand, scenario_affected_nodes, scenario_changes, as_of_week_num,
                                    target_week_num)
    return WeekPlan(demand, leadtime_by_dc, cost, active_rdc, active_cdc, cdc_primary_rdc, overrides)


def solve(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc,
          cost, active_rdc, active_cdc, cdc_primary_rdc, target_week_num, alpha: float, rng,
          n_sims: int = N_SIMS, netting_discount: float = 1.0,
          scenario_changes: dict = None, scenario_affected_nodes: list = None,
          overrides=None, ddlt: str = "monte_carlo", alpha_by_sku: dict | None = None,
          z_by_sku: dict | None = None) -> pl.DataFrame:
    """One target week's RDC+CDC policy (see the original's docstring). Same arguments,
    same rows in the same order; returns a polars frame."""
    plan = week_plan(dfc, as_of_week_num, period_index, seasonal_index, sku_subcat, leadtime_by_dc, cost,
                     active_rdc, active_cdc, cdc_primary_rdc, target_week_num, overrides, scenario_changes,
                     scenario_affected_nodes)
    endpoints = plan.endpoints(ddlt, rng, n_sims)
    return plan.frame(endpoints, Variant(alpha, netting_discount, alpha_by_sku, z_by_sku))


def build_rolling_policy(data_dir, as_of_week, alpha, n_sims, rng, out_path, netting_discount: float = 1.0,
                         scenario_changes: dict = None):
    """Full 52-week rolling policy from a single fixed as-of point (the synthetic-2026
    basis). See policy_builds.rolling_policies for the many-variants-per-stream form."""
    import policy_builds
    variant = Variant(alpha, netting_discount)
    res = policy_builds.rolling_policies(data_dir, as_of_week, {"policy": (variant, out_path)}, rng, n_sims,
                                         scenario_changes=scenario_changes, log=print)
    return res["paths"]["policy"]
