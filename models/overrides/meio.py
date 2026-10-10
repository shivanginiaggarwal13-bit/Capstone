"""
Override application for MEIO -- the PLAN side of a scenario.

MEIO builds one policy per forward week. PlanHooks is handed to meio_weekly_lib's
functions (and to clark_scarf_v2.solve, which uses them) and changes what each week's
policy is sized against:

  seam in meio_weekly_lib / roll_forward_policy    hook          measures
  target_week_demand output                        demand()      demand.volume, demand.volatility
  assign_z                                         z()           service.target_csl
  build_leadtime_graph / build_pdc_leadtime        lanes()       supply.lead_time_mean / _std
  clip_to_plant_capacity                           capacity()    supply.plant_capacity
  clark_scarf_v2.solve, the RDC pooling blend      alpha()       policy.pooling_alpha
  roll_forward_policy, after each week's solve     policy()      policy.safety_stock / reorder_point /
                                                                 order_up_to_level

Demand is changed on MEIO's own weekly estimate, not on the raw forecast: target_mean
blends 0.6 x bias-corrected forecast with 0.4 x a de-seasonalised level, so scaling only
the forecast would plan a +25% event as roughly +15%. Each (dc, sku) estimate is mapped
through the same line-level rule the Twin applies to forecast rows (scales weighted by
each customer line's forecast, equal weights in a zero-forecast week; adds summed over
matched lines), so plan and actual describe one change. CV is held: the demand std moves
with the mean, and demand.volatility then multiplies it.

Every hook is a no-op for weeks with no active override, and the policy builder passes
None when the scenario has no plan-side override, so the baseline policy is unchanged.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from .compile import Override, OverrideSet, apply_op

POLICY_FIELDS = {"policy.safety_stock": "safety_stock", "policy.reorder_point": "reorder_point",
                 "policy.order_up_to_level": "order_up_to_level"}
LINE_KEYS = ["dc_id", "sku_id", "b2b_customer_id"]
PAIR_KEYS = ["dc_id", "sku_id"]


def _in(frame: pd.DataFrame, keys: list[str], rows: pd.DataFrame) -> np.ndarray:
    return pd.MultiIndex.from_frame(frame[keys]).isin(pd.MultiIndex.from_frame(rows[keys].drop_duplicates()))


class PlanHooks:
    def __init__(self, ovs: OverrideSet):
        self.ovs = ovs
        self._affine: dict = {}
        self._lanes: dict = {}
        self._recorded: set = set()

    def __bool__(self) -> bool:
        return bool(self.ovs)

    @property
    def receipts(self):
        return self.ovs.receipts

    def _record(self, o: Override, period: str, tag: str = "", **facts) -> None:
        """One receipt per (override, week, call site): smoothing re-sizes neighbouring
        weeks, and those repeat calls must not double the receipt."""
        key = (o.id, period, tag)
        if key not in self._recorded:
            self._recorded.add(key)
            self.receipts.add(o, period, **facts)

    # ---------------------------------------------------------------- demand --
    def _demand_affine(self, period: str) -> pd.DataFrame | None:
        """Per (dc, sku): target_mean -> M x target_mean + A for this week."""
        if period in self._affine:
            return self._affine[period]
        ops = self.ovs.active(period, "demand.volume")
        result = None
        if ops:
            fd = self.ovs.master.forward_demand
            lines = fd[fd.period == period][LINE_KEYS + ["forecast_qty"]].reset_index(drop=True)
            groups = [lines.dc_id, lines.sku_id]
            total = lines.groupby(PAIR_KEYS).forecast_qty.transform("sum").to_numpy(dtype=float)
            w = np.where(total > 0, lines.forecast_qty.to_numpy(dtype=float), 1.0)
            den = pd.Series(w).groupby(groups).sum()
            m_pair, a_pair = pd.Series(1.0, index=den.index), pd.Series(0.0, index=den.index)
            for o in ops:
                matched = _in(lines, LINE_KEYS, self.ovs.rows(o))
                if not matched.any():
                    continue
                if o.op == "scale":
                    s = pd.Series(w * np.where(matched, o.value, 1.0)).groupby(groups).sum() / den
                    m_pair, a_pair = m_pair * s, a_pair * s
                else:
                    a_pair = a_pair + pd.Series(np.where(matched, o.value, 0.0)).groupby(groups).sum()
            frame = pd.DataFrame({"M": m_pair, "A": a_pair})
            frame = frame[(frame.M != 1.0) | (frame.A != 0.0)]
            frame.index.names = PAIR_KEYS
            result = frame.reset_index() if len(frame) else None
        self._affine[period] = result
        return result

    def demand(self, d: pd.DataFrame, period: str) -> pd.DataFrame:
        """meio_weekly_lib.target_week_demand output for one target week."""
        affine = self._demand_affine(period)
        vol = self.ovs.active(period, "demand.volatility")
        if affine is None and not vol:
            return d
        out = d.copy()
        if affine is not None:
            m = out[PAIR_KEYS].merge(affine, on=PAIR_KEYS, how="left")
            mult, add = m.M.fillna(1.0).to_numpy(), m.A.fillna(0.0).to_numpy()
            mean0 = out.target_mean.to_numpy(dtype=float)
            mean1 = np.maximum(mult * mean0 + add, 0.0)
            ratio = np.divide(mean1, mean0, out=np.ones_like(mean1), where=mean0 > 0)
            out["target_mean"], out["target_std"] = mean1, out.target_std.to_numpy(dtype=float) * ratio
            touched = m.M.notna().to_numpy()
            for o in self.ovs.active(period, "demand.volume"):
                hit = touched & _in(out, PAIR_KEYS, self.ovs.rows(o))
                self._record(o, period, "demand", pair_weeks=int(hit.sum()),
                             target_units_before=float(mean0[hit].sum()), target_units_after=float(mean1[hit].sum()))
        for o in vol:
            hit = _in(out, PAIR_KEYS, self.ovs.rows(o))
            out.loc[hit, "target_std"] = out.loc[hit, "target_std"].to_numpy(dtype=float) * o.value
            self._record(o, period, "volatility", pair_weeks=int(hit.sum()), std_multiplier=o.value)
        return out

    # --------------------------------------------------------------- service --
    def z(self, df: pd.DataFrame, z: pd.Series, period: str | None) -> pd.Series:
        """Service factor per row: CSL overrides replace (set) or shift (add) the segment z."""
        ops = self.ovs.active(period, "service.target_csl") if period else []
        if not ops:
            return z
        values = z.to_numpy(dtype=float).copy()
        tag = "pdc" if len(df) and str(df.dc_id.iloc[0]).startswith("PDC") else "rdc_cdc"
        for o in ops:
            hit = _in(df, PAIR_KEYS, self.ovs.rows(o))
            if not hit.any():
                continue
            before = values[hit].copy()
            values[hit] = (norm.ppf(o.value) if o.op == "set"
                           else norm.ppf(np.clip(norm.cdf(before) + o.value, 0.5, 0.999)))
            self._record(o, period, tag, pair_weeks=int(hit.sum()), z_before_mean=float(before.mean()),
                         z_after_mean=float(values[hit].mean()))
        return pd.Series(values, index=z.index)

    def alpha(self, df: pd.DataFrame, alpha: np.ndarray, period: str | None) -> np.ndarray:
        """Clark-Scarf v2 pooling factor per RDC row: policy.pooling_alpha sets it for the
        matched SKUs (not windowed, so every week)."""
        ops = self.ovs.of("policy.pooling_alpha")
        if not ops:
            return alpha
        out = np.asarray(alpha, dtype=float).copy()
        for o in ops:
            hit = df.sku_id.isin(self.ovs.rows(o).sku_id).to_numpy()
            before = out[hit].copy()
            out[hit] = apply_op(before, o.op, o.value)
            self._record(o, period, "alpha", pair_weeks=int(hit.sum()), alpha_before_mean=float(before.mean())
                         if hit.any() else 0.0, alpha_after_mean=float(o.value))
        return out

    # ---------------------------------------------------------------- policy --
    def policy(self, pol: pd.DataFrame, period: str) -> pd.DataFrame:
        """Planner policy rules after the week's solve. A safety-stock change carries the
        reorder point and order-up-to with it (s = cycle + SS, S = s + cover); a
        reorder-point change carries order-up-to; S >= s is re-imposed on touched rows."""
        ops = self.ovs.active(period, *POLICY_FIELDS)
        if not ops:
            return pol
        out = pol.copy()
        touched = np.zeros(len(out), dtype=bool)
        for o in ops:
            field = POLICY_FIELDS[o.measure]
            hit = _in(out, PAIR_KEYS, self.ovs.rows(o))
            if not hit.any():
                continue
            base = out.loc[hit, field].to_numpy(dtype=float)
            value = (o.value * out.loc[hit, "target_mean"].fillna(0.0).to_numpy(dtype=float)
                     if o.unit == "weeks_of_cover" else o.value)
            new = np.maximum(apply_op(base, o.op, value), 0.0)
            delta = new - base
            out.loc[hit, field] = new
            if field == "safety_stock":
                out.loc[hit, "reorder_point"] = out.loc[hit, "reorder_point"].to_numpy(dtype=float) + delta
            if field in ("safety_stock", "reorder_point"):
                out.loc[hit, "order_up_to_level"] = out.loc[hit, "order_up_to_level"].to_numpy(dtype=float) + delta
            touched |= hit
            self._record(o, period, "policy", pair_weeks=int(hit.sum()), field=field,
                         units_before=float(base.sum()), units_after=float(new.sum()))
        rop = out.loc[touched, "reorder_point"].to_numpy(dtype=float)
        out.loc[touched, "order_up_to_level"] = np.maximum(out.loc[touched, "order_up_to_level"].to_numpy(dtype=float), rop)
        for c in POLICY_FIELDS.values():
            out.loc[touched, c] = out.loc[touched, c].astype(float).round(1)
        return out

    # ---------------------------------------------------------------- supply --
    def lanes(self, period: str) -> pd.DataFrame | None:
        """The lane table with this week's lead-time overrides, or None when none is active."""
        ops = self.ovs.active(period, "supply.lead_time_mean", "supply.lead_time_std")
        if not ops:
            return None
        sig = tuple(o.id for o in ops)
        if sig not in self._lanes:
            ln = self.ovs.master.lanes.copy()
            for o in ops:
                col = "lead_time_mean_days" if o.measure == "supply.lead_time_mean" else "lead_time_std_days"
                hit = ln.lane_id.isin(self.ovs.rows(o).lane_id).to_numpy()
                new = apply_op(ln.loc[hit, col].to_numpy(dtype=float), o.op, o.value)
                ln.loc[hit, col] = np.maximum(new, 0.5 if col == "lead_time_mean_days" else 0.0)
            self._lanes[sig] = ln
        for o in ops:
            self._record(o, period, "lanes", lanes=int(self.ovs.rows(o).lane_id.nunique()))
        return self._lanes[sig]

    def capacity(self, family_capacity: dict, plant_capacity: dict, period: str | None):
        ops = self.ovs.active(period, "supply.plant_capacity") if period else []
        if not ops:
            return family_capacity, plant_capacity
        fam, plant = dict(family_capacity), dict(plant_capacity)
        for o in ops:
            rows = self.ovs.rows(o)
            if "production_family" not in o.scope:
                for p in rows.plant_id.unique():
                    plant[p] = max(0.0, float(apply_op(plant[p], o.op, o.value)))
            lines = 0
            for r in rows.itertuples(index=False):
                key = (r.plant_id, r.production_family)
                if key in fam:
                    fam[key] = max(0.0, float(apply_op(fam[key], o.op, o.value)))
                    lines += 1
            self._record(o, period, "capacity", family_lines=lines)
        return fam, plant
