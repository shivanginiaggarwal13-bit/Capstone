"""
Master data and scope resolution for the override engine.

Turns an override's scope into the concrete rows it touches. MEIO, the Digital Twin
and the agent tools all resolve scopes through this one class, so an agent's preview
and the models' application can never disagree about what "Southeast beverages" means.

Each measure resolves against one UNIVERSE of rows (catalog.Measure.universe):

  demand_lines      forward demand lines (dc x sku x customer)  -- demand.volume
  demand_pairs      (dc, sku) pairs with forward demand         -- demand.volatility
  stock_pairs       (dc, sku) pairs in the opening position     -- inventory, service, policy
  skus              the SKU master                              -- pooling alpha, unit economics
  dc_subcategories  cost_parameters rows (dc x subcategory)     -- holding and storage cost
  lanes             network_lanes rows; node keys filter on the DESTINATION
  plant_families    plant_capability rows (plant x production family)

Plant DCs carry no region in their master table; theirs is derived from the state they
sit in, so a regional scope reaches the plant DC located in that region.
"""
from __future__ import annotations

import difflib
import json
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from . import catalog

ECHELONS = {"PDC": "PLANT_DC", "RDC": "REGIONAL_DC", "CDC": "CUSTOMER_DC"}
FIRST_WEEKS_FOR_COVER = 4
SKU_COLS = ["sku_id", "category", "subcategory", "brand", "segment", "price_tier",
            "production_family", "cluster", "archetype"]
NODE_COLS = ["dc_id", "region", "echelon"]


class MasterData:
    """The given world, read from one data folder (plus the segmentation clusters and the
    opening position). Tables load lazily and are cached per instance."""

    def __init__(self, data_dir, clusters_csv=None, state_csv=None):
        self.data_dir = Path(data_dir)
        self.clusters_csv = Path(clusters_csv) if clusters_csv else None
        self.state_csv = Path(state_csv) if state_csv else None
        self._universes: dict = {}
        self._matches: dict = {}
        self._planning: dict = {}

    def _read(self, name: str, **kw) -> pd.DataFrame:
        return pd.read_csv(self.data_dir / name, **kw)

    # ------------------------------------------------------------- masters --
    @cached_property
    def skus(self) -> pd.DataFrame:
        sm = self._read("sku_master.csv")
        if self.clusters_csv and self.clusters_csv.exists():
            cl = pd.read_csv(self.clusters_csv, usecols=["sku_id", "cluster", "archetype", "recommended_csl"])
            sm = sm.merge(cl, on="sku_id", how="left")
        else:
            sm = sm.assign(cluster=pd.NA, archetype=pd.NA, recommended_csl=np.nan)
        sm["cluster"] = sm["cluster"].astype("Int64").astype(str)
        return sm

    @cached_property
    def nodes(self) -> pd.DataFrame:
        cdc = self._read("customer_dc_master.csv").rename(columns={"customer_dc_id": "dc_id"})
        rdc = self._read("regional_dc_master.csv").rename(columns={"regional_dc_id": "dc_id"})
        pdc = self._read("plant_dc_master.csv").rename(columns={"plant_dc_id": "dc_id"})
        state_region = dict(zip(pd.concat([cdc.state, rdc.state]), pd.concat([cdc.region, rdc.region])))
        pdc["region"] = pdc.state.map(state_region)
        pdc["dc_name"] = pdc.city + " Plant DC"
        out = pd.concat([pdc, rdc, cdc], ignore_index=True)
        out["echelon"] = out.dc_id.str[:3]
        out["node_type"] = out.echelon.map(ECHELONS)
        return out[["dc_id", "echelon", "node_type", "dc_name", "city", "state", "region",
                    "plant_id", "storage_capacity_units"]]

    @cached_property
    def lanes(self) -> pd.DataFrame:
        ln = self._read("network_lanes.csv")
        nd = self.nodes.set_index("dc_id")
        ln["dc_id"] = ln.destination_node_id
        ln["region"] = ln.dc_id.map(nd.region)
        ln["echelon"] = ln.dc_id.map(nd.echelon)
        return ln

    @cached_property
    def plants(self) -> pd.DataFrame:
        pdc = self.nodes[self.nodes.echelon == "PDC"][["dc_id", "plant_id", "region"]]
        return self._read("plant_master.csv").merge(pdc.rename(columns={"dc_id": "plant_dc_id"}),
                                                     on="plant_id", how="left")

    @cached_property
    def plant_families(self) -> pd.DataFrame:
        cols = ["plant_id", "region", "production_capacity_units_per_week"]
        return self._read("plant_capability.csv").merge(self.plants[cols], on="plant_id", how="left")

    @cached_property
    def customers(self) -> pd.DataFrame:
        return self._read("b2b_customer_master.csv").merge(
            self._read("b2b_customer_terms.csv"), on=["b2b_customer_id", "pricing_strategy"], how="left")

    @cached_property
    def cost_parameters(self) -> pd.DataFrame:
        cp = self._read("cost_parameters.csv")
        cat = self.skus.drop_duplicates("subcategory").set_index("subcategory").category
        cp["category"] = cp.subcategory.map(cat)
        return cp.merge(self.nodes[["dc_id", "region", "echelon"]], on="dc_id", how="left")

    @cached_property
    def sourcing_map(self) -> pd.DataFrame:
        return self._read("sku_sourcing_map.csv")

    @cached_property
    def plant_sourcing(self) -> pd.DataFrame:
        return self._read("sku_plant_sourcing.csv")

    @cached_property
    def opening_position(self) -> pd.DataFrame:
        if self.state_csv is None:
            raise ValueError("this measure needs the opening position (digital_twin.state)")
        return pd.read_csv(self.state_csv)

    # ------------------------------------------------------------- horizon --
    @cached_property
    def forward_demand(self) -> pd.DataFrame:
        """Forward-horizon forecast lines: (period, dc, sku, customer) with promo lift."""
        d = self._read("demand_forecast.csv",
                       usecols=["period", "week_start_date", "horizon", "sku_id", "dc_id", "b2b_customer_id",
                                "forecast_qty", "forecast_promo_lift_qty"])
        return d[d.horizon == "forward"].drop(columns="horizon").reset_index(drop=True)

    @cached_property
    def forward_periods(self) -> tuple[str, ...]:
        p = self.forward_demand[["period", "week_start_date"]].drop_duplicates().sort_values("week_start_date")
        return tuple(p.period)

    def window_periods(self, window: dict | None) -> list[str]:
        periods = self.forward_periods
        if not window:
            return list(periods)
        i, j = periods.index(window["start_period"]), periods.index(window["end_period"])
        return list(periods[i:j + 1])

    def planning_demand(self, periods: tuple[str, ...]) -> pd.Series:
        """Average weekly demand per (dc, sku) on MEIO's own basis: each CDC and RDC is
        sized against its own demand rows, and a plant DC against its throughput -- the
        own demand of every RDC that sources the SKU from it (meio_weekly_lib.solve_pdc_policy).
        Weeks of cover divides by this, so a plant DC's cover is not zero just because it
        serves no customers directly."""
        key = tuple(periods)
        if key not in self._planning:
            fd = self.forward_demand
            own = fd[fd.period.isin(set(periods))].groupby(["dc_id", "sku_id"]).forecast_qty.sum() / len(periods)
            rdc = own[own.index.get_level_values("dc_id").str.startswith("RDC")].rename("q").reset_index()
            thru = (rdc.merge(self.sourcing_map[["dc_id", "sku_id", "origin_node_id"]], on=["dc_id", "sku_id"])
                    .groupby(["origin_node_id", "sku_id"]).q.sum())
            thru.index.names = ["dc_id", "sku_id"]
            self._planning[key] = pd.concat([own, thru.rename("forecast_qty")])
        return self._planning[key]

    def cover_basis(self) -> pd.Series:
        """Weekly planning demand over the first forward weeks -- the denominator for
        opening-position weeks of cover."""
        return self.planning_demand(self.forward_periods[:FIRST_WEEKS_FOR_COVER])

    # --------------------------------------------------------------- scope --
    def _with_attrs(self, pairs: pd.DataFrame) -> pd.DataFrame:
        out = pairs.merge(self.skus[SKU_COLS], on="sku_id", how="left")
        return out.merge(self.nodes[NODE_COLS], on="dc_id", how="left")

    def universe(self, name: str) -> pd.DataFrame:
        if name not in self._universes:
            fd = self.forward_demand
            if name == "demand_lines":
                frame = self._with_attrs(fd[["dc_id", "sku_id", "b2b_customer_id"]].drop_duplicates()
                                         .reset_index(drop=True))
            elif name == "demand_pairs":
                frame = self._with_attrs(fd[["dc_id", "sku_id"]].drop_duplicates().reset_index(drop=True))
            elif name == "stock_pairs":
                frame = self._with_attrs(self.opening_position[["dc_id", "sku_id"]].drop_duplicates()
                                         .reset_index(drop=True))
            elif name == "skus":
                frame = self.skus[SKU_COLS].copy()
            elif name == "dc_subcategories":
                frame = self.cost_parameters
            elif name == "lanes":
                frame = self.lanes
            elif name == "plant_families":
                frame = self.plant_families
            else:
                raise KeyError(f"unknown universe {name!r}")
            self._universes[name] = frame
        return self._universes[name]

    @cached_property
    def key_domains(self) -> dict[str, tuple[str, ...]]:
        def u(series):
            return tuple(sorted(series.dropna().astype(str).unique()))
        s, n, ln = self.skus, self.nodes, self.lanes
        return {
            "sku_id": u(s.sku_id), "category": u(s.category), "subcategory": u(s.subcategory),
            "brand": u(s.brand), "segment": u(s.segment), "price_tier": u(s.price_tier),
            "production_family": u(s.production_family), "cluster": u(s.cluster), "archetype": u(s.archetype),
            "dc_id": u(n.dc_id), "region": u(n.region), "echelon": tuple(ECHELONS),
            "b2b_customer_id": u(self.customers.b2b_customer_id), "plant_id": u(self.plants.plant_id),
            "lane_id": u(ln.lane_id), "origin_node_id": u(ln.origin_node_id),
            "destination_node_id": u(ln.destination_node_id), "transport_mode": u(ln.transport_mode),
        }

    def canonicalize(self, key: str, values) -> tuple[list[str], list[str]]:
        """Map loosely-typed scope values ('southeast', 'cdc03', 'CUSTOMER_DC') to the
        canonical master-data value, with close-match hints for anything unknown."""
        domain = self.key_domains[key]
        lookup = {v.casefold(): v for v in domain}
        if key == "echelon":
            lookup.update({alias.casefold(): code for code, alias in ECHELONS.items()})
        out, errors = [], []
        for raw in values:
            value = lookup.get(str(raw).strip().casefold())
            if value is None:
                hint = difflib.get_close_matches(str(raw), domain, n=3, cutoff=0.6)
                shown = list(domain[:12]) + (["..."] if len(domain) > 12 else [])
                errors.append(f"{key}={raw!r} is not a known value; "
                              + (f"did you mean {hint}?" if hint else f"valid values include {shown}"))
            elif value not in out:
                out.append(value)
        return out, errors

    def match(self, measure: str, scope: dict) -> pd.DataFrame:
        """Rows of the measure's universe selected by an (already canonical) scope. The
        universe's own index is kept, so two scopes' matches can be intersected."""
        key = (measure, json.dumps(scope, sort_keys=True))
        if key not in self._matches:
            frame = self.universe(catalog.MEASURES[measure].universe)
            if scope.get("all"):
                self._matches[key] = frame
            else:
                mask = np.ones(len(frame), dtype=bool)
                for k, values in scope.items():
                    mask &= frame[k].astype(str).isin(set(values)).to_numpy()
                self._matches[key] = frame[mask]
        return self._matches[key]
