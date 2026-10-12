"""
Policy builds: one pass of Monte Carlo draws per RNG stream, every variant on it
===============================================================================

The research scripts build walk-forward policies (validate_real_data's real-data basis:
105 warm-up weeks on the baseline policy, then 51 weeks each refit as of the week
before) and rolling policies (one fixed as-of week, 52 forward weeks). The originals
built one policy per call, re-deriving the same demand estimates and redrawing the same
gamma variates for every alpha, netting_discount and z map they swept.

Here a build takes a dict of variants that share one RNG stream and walks the weeks
once: each week's demand plan and Monte Carlo draws are made once, in the original's
order, and every variant is read off them (clark_scarf_v2.WeekPlan). Each variant's
policy file is identical to what the original would have written for it alone with a
fresh generator at the same seed. A sweep that shared ONE advancing generator across
builds (validate_real_data's alpha sweep, segment_alpha_explore) is a chain: each link
is its own build, handed the generator state the previous link ended on, so the chain
reproduces too.

`walkforward_task` / `rolling_task` are the process-pool entry points; a job is a plain
dict so it pickles, and the result carries the generator state for the next link.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import polars as pl

import runtime as rt
import meio_weekly_lib as lib
import clark_scarf_v2 as csv2

POLICY_COLS = csv2.POLICY_COLS


class Inputs:
    """Everything a build reads, loaded once per process and reused by every build."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.dfc = lib.load_demand_forecast(data_dir)
        self.period_index = self.dfc.period_index
        self.active_rdc, self.active_cdc = lib.active_nodes(self.dfc)
        rdc_lt, cdc_lt, self.cdc_primary_rdc = lib.build_leadtime_graph(data_dir, self.active_rdc, self.active_cdc)
        self.leadtime_by_dc = {**rdc_lt, **cdc_lt}
        self.cost = lib.load_sku_cost(data_dir)
        self.seasonal_index = lib.build_seasonal_index(data_dir)
        self.sku_subcat = lib.load_sku_subcategory(data_dir)
        self._pdc: dict = {}
        self._plans: dict = {}

    def week_num(self, period: str) -> int:
        return lib.week_num_of(self.period_index, period)

    def pdc(self, as_of: int, target: int) -> lib.PdcStatic:
        """solve_pdc_policy's variant-independent part for one week (no scenario)."""
        key = (as_of, target)
        if key not in self._pdc:
            sku, dc, mean = lib.smoothed_rdc_arrays(self.dfc, as_of, self.seasonal_index, self.sku_subcat,
                                                    self.active_rdc, target)
            self._pdc[key] = lib.PdcStatic(sku.tolist(), dc.tolist(), mean, self.data_dir, self.cost)
        return self._pdc[key]

    def plan(self, as_of: int, target: int, scenario_changes=None, affected=None) -> csv2.WeekPlan:
        """The week's variant-independent WeekPlan; cached when no scenario bumps demand
        (every link of an RNG chain sizes the same weeks against the same demand)."""
        if scenario_changes:
            return csv2.week_plan(self.dfc, as_of, self.period_index, self.seasonal_index, self.sku_subcat,
                                  self.leadtime_by_dc, self.cost, self.active_rdc, self.active_cdc,
                                  self.cdc_primary_rdc, target, scenario_changes=scenario_changes,
                                  scenario_affected_nodes=affected)
        key = (as_of, target)
        if key not in self._plans:
            self._plans[key] = csv2.week_plan(self.dfc, as_of, self.period_index, self.seasonal_index,
                                              self.sku_subcat, self.leadtime_by_dc, self.cost, self.active_rdc,
                                              self.active_cdc, self.cdc_primary_rdc, target)
        return self._plans[key]


_INPUTS: dict = {}


def inputs(data_dir: str) -> Inputs:
    key = str(Path(data_dir).resolve())
    if key not in _INPUTS:
        _INPUTS[key] = Inputs(data_dir)
    return _INPUTS[key]


def make_rng(spec) -> np.random.Generator:
    """A generator from a seed, a saved bit-generator state, or a generator itself."""
    if isinstance(spec, np.random.Generator):
        return spec
    if isinstance(spec, dict):
        bg = np.random.PCG64()
        bg.state = spec
        return np.random.Generator(bg)
    return np.random.default_rng(spec)


class _Rows:
    """One variant's policy rows, accumulated week by week."""

    def __init__(self):
        self.parts: list = []

    def add(self, period, dc, sku, ss, rop, oul):
        self.parts.append((period, dc, sku, ss, rop, oul))

    def frame(self) -> pl.DataFrame:
        if not self.parts:
            return pl.DataFrame({c: [] for c in ["period"] + POLICY_COLS})
        n = [len(p[1]) for p in self.parts]
        return pl.DataFrame({
            "period": np.repeat([p[0] for p in self.parts], n),
            "dc_id": np.concatenate([np.asarray(p[1], dtype=object) for p in self.parts]),
            "sku_id": np.concatenate([np.asarray(p[2], dtype=object) for p in self.parts]),
            "safety_stock": np.concatenate([p[3] for p in self.parts]).astype(float),
            "reorder_point": np.concatenate([p[4] for p in self.parts]).astype(float),
            "order_up_to_level": np.concatenate([p[5] for p in self.parts]).astype(float),
        }, schema_overrides={"period": pl.String, "dc_id": pl.String, "sku_id": pl.String})


def _warmup_frame(data_dir: str, baseline_policy_csv: str, warmup_start: str, warmup_end: str) -> pl.DataFrame:
    """validate_real_data's warm-up: the baseline policy repeated for every order-history
    week in [warmup_start, warmup_end]."""
    base = rt.read_table(baseline_policy_csv).select(POLICY_COLS).with_columns(
        [pl.col(c).cast(pl.Float64) for c in POLICY_COLS[2:]])
    periods = sorted(rt.read_table(f"{data_dir}/orders.parquet", columns=["period"])["period"].unique().to_list())
    weeks = [p for p in periods if warmup_start <= p <= warmup_end]
    if not weeks:
        return pl.DataFrame(schema={"period": pl.String, **{c: base.schema[c] for c in POLICY_COLS}})
    rep = pl.concat([base] * len(weeks))
    return rep.with_columns(pl.Series("period", np.repeat(weeks, base.height), dtype=pl.String)).select(
        ["period"] + POLICY_COLS)


def walkforward_policies(data_dir: str, variants: dict, rng, n_sims: int = 1200, *, model: str = "clark_scarf_v2",
                         baseline_policy_csv: str, warmup: tuple, test: tuple, log=None,
                         threads: int = 1) -> dict:
    """validate_real_data's walk-forward policy for every variant in `variants`
    ({key: (Variant, out_path)}), all on one generator. Week wn is sized as of wn - 1.
    model 'meio_weekly' is the closed-form MEIO Weekly tool (no draws, variants ignored
    beyond their output path)."""
    t0 = time.time()
    inp = inputs(data_dir)
    rng = make_rng(rng)
    rows = {k: _Rows() for k in variants}
    test_start, test_end = inp.week_num(test[0]), inp.week_num(test[1])
    with (ThreadPoolExecutor(threads) if threads > 1 else nullcontext()) as pool:
        _walk(inp, model, variants, rows, rng, n_sims, test_start, test_end, pool)
    t_build = time.time() - t0
    warm = _warmup_frame(data_dir, baseline_policy_csv, *warmup)
    paths = {}
    for key, (_, out_path) in variants.items():
        paths[key] = rt.write_policy(pl.concat([warm, rows[key].frame()]), out_path)
    msg = (f"[walk-forward {model}] {len(variants)} variant(s) on one stream: built in {t_build:.0f}s, "
           f"wrote {len(paths)} file(s) in {time.time() - t0 - t_build:.0f}s")
    if log:
        log(msg)
    return {"paths": paths, "rng_state": rng.bit_generator.state, "log": [msg]}


def _finish_week(plan, endpoints, pdc, variants) -> dict:
    """Every variant's (dc, sku, ss, rop, oul) arrays for one week, PDC rows appended."""
    out = {}
    for key, (variant, _) in variants.items():
        arrays = plan.policy_arrays(endpoints, variant)
        out[key] = (arrays, pdc.policy_arrays(variant.z_by_sku) if not pdc.empty else (None,) * 4)
    return out


class _Weeks:
    """Week-by-week variant finishing, overlapped with the next week's draws when a
    thread pool is given: the main thread keeps making the draws in order while one
    background thread reads the previous week's variants off them. Rows are appended in
    week order at the end, so the output is the same either way."""

    def __init__(self, pool):
        self.finisher = ThreadPoolExecutor(1) if pool is not None else None
        self.pending: list = []

    def add(self, period, plan, endpoints, pdc, variants):
        if self.finisher is None:
            self.pending.append((period, pdc, _finish_week(plan, endpoints, pdc, variants)))
        else:
            self.pending.append((period, pdc, self.finisher.submit(_finish_week, plan, endpoints, pdc, variants)))

    def drain(self, rows) -> list:
        sizes = []
        for period, pdc, res in self.pending:
            res = res.result() if self.finisher is not None else res
            for key, (arrays, (_, pss, prop, poul)) in res.items():
                _append(rows[key], period, arrays, pdc, pss, prop, poul)
            sizes.append((period, len(next(iter(res.values()))[0][0]) + (0 if pdc.empty else len(pdc.dc))))
        if self.finisher is not None:
            self.finisher.shutdown()
        self.pending = []
        return sizes


def _walk(inp, model, variants, rows, rng, n_sims, test_start, test_end, pool):
    weeks = _Weeks(pool)
    for wn in range(test_start, test_end + 1):
        as_of = wn - 1
        period = inp.dfc.periods[wn]
        pdc = inp.pdc(as_of, wn)
        if model == "meio_weekly":
            demand = lib.target_week_demand(inp.dfc, as_of, None, inp.seasonal_index, inp.sku_subcat)
            pol = lib.solve_policy(demand, inp.leadtime_by_dc, inp.cost, inp.active_rdc, inp.active_cdc,
                                   inp.cdc_primary_rdc)
            dc, sku = pol["dc_id"].to_numpy(), pol["sku_id"].to_numpy()
            ss, rop, oul = (pol[c].to_numpy() for c in POLICY_COLS[2:])
            for key, (variant, _) in variants.items():
                _, pss, prop, poul = pdc.policy_arrays(variant.z_by_sku) if not pdc.empty else (None,) * 4
                _append(rows[key], period, (dc, sku, ss, rop, oul), pdc, pss, prop, poul)
            continue
        plan = inp.plan(as_of, wn)
        weeks.add(period, plan, plan.endpoints("monte_carlo", rng, n_sims, pool), pdc, variants)
    weeks.drain(rows)


def _append(rows: _Rows, period, arrays, pdc, pss, prop, poul):
    dc, sku, ss, rop, oul = arrays
    if pdc.empty:
        rows.add(period, dc, sku, ss, rop, oul)
        return
    rows.add(period, np.concatenate([np.asarray(dc, dtype=object), pdc.dc]),
             np.concatenate([np.asarray(sku, dtype=object), pdc.sku]),
             np.concatenate([ss, pss]), np.concatenate([rop, prop]), np.concatenate([oul, poul]))


def rolling_policies(data_dir: str, as_of_week: str, variants: dict, rng, n_sims: int, *,
                     scenario_changes: dict | None = None, log=None, threads: int = 1,
                     max_weeks: int | None = None) -> dict:
    """clark_scarf_v2.build_rolling_policy for every variant in `variants`
    ({key: (Variant, out_path)}) on one generator: all weeks after `as_of_week`, sized as
    of it. A scenario's demand surge (legacy `changes`) bumps what each week is sized
    against; PDC smoothing is left on normal demand, as in the original."""
    t0 = time.time()
    inp = inputs(data_dir)
    rng = make_rng(rng)
    affected = None
    if scenario_changes:
        import scenario_engine
        affected = scenario_engine.resolve_affected_nodes(Path(data_dir), scenario_changes)
    as_of = inp.week_num(as_of_week)
    forward = [w for w in range(inp.dfc.n_weeks) if w > as_of][:max_weeks]   # max_weeks: tests only
    rows = {k: _Rows() for k in variants}
    lines = []
    pool = ThreadPoolExecutor(threads) if threads > 1 else None
    weeks = _Weeks(pool)
    for wk in forward:
        plan = inp.plan(as_of, wk, scenario_changes, affected)
        endpoints = plan.endpoints("monte_carlo", rng, n_sims, pool)
        weeks.add(inp.dfc.periods[wk], plan, endpoints, inp.pdc(as_of, wk), variants)
        del endpoints
    for wk, (period, n_lines) in zip(forward, weeks.drain(rows)):
        if wk % 10 == 0 or wk == forward[-1]:
            lines.append(f"  {period}: {n_lines:,} SKU x DC lines")
    if pool is not None:
        pool.shutdown()
    paths = {}
    for key, (_, out_path) in variants.items():
        frame = rows[key].frame()
        paths[key] = rt.write_policy(frame, out_path)
        lines.append(f"Wrote {frame.height:,} rows -> {paths[key]}")
    lines.append(f"[rolling] {len(variants)} variant(s) on one stream in {time.time() - t0:.0f}s")
    if log:
        for line in lines:
            log(line)
    return {"paths": paths, "rng_state": rng.bit_generator.state, "log": lines}


# ------------------------------------------------------------ pool tasks --
def _variants(job: dict) -> dict:
    return {key: (csv2.Variant(**v["variant"]), v["out_path"]) for key, v in job["variants"].items()}


def walkforward_task(job: dict) -> dict:
    return walkforward_policies(job["data_dir"], _variants(job), job["rng"], job.get("n_sims", 1200),
                                model=job.get("model", "clark_scarf_v2"),
                                baseline_policy_csv=job["baseline_policy_csv"], warmup=tuple(job["warmup"]),
                                test=tuple(job["test"]), threads=job.get("threads", 1))


def rolling_task(job: dict) -> dict:
    return rolling_policies(job["data_dir"], job["as_of_week"], _variants(job), job["rng"], job["n_sims"],
                            scenario_changes=job.get("scenario_changes"), threads=job.get("threads", 1),
                            max_weeks=job.get("max_weeks"))


def variant_spec(out_path, alpha=0.2, netting_discount=1.0, alpha_by_sku=None, z_by_sku=None) -> dict:
    return {"out_path": str(out_path), "variant": {"alpha": alpha, "netting_discount": netting_discount,
                                                   "alpha_by_sku": alpha_by_sku, "z_by_sku": z_by_sku}}
