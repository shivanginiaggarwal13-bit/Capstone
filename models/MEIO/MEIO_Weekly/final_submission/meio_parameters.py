"""
MEIO parameters -- link run_all.py's tuning evidence to the policy the pipeline builds
=====================================================================================

run_all.py's research steps (validate_real_data, netting_discount_sweep,
segment_alpha_real_data, service_level_real_data) sweep Clark-Scarf v2's parameters
against replayed real demand and leave their evidence as CSVs under
`<artifacts.scenario>/results/`. This module reads that evidence, applies the
selection rule the config names, and returns the parameters roll_forward_policy.py
and meio_optimizer_weekly.py build the policy with. Every decision, and the files and
hashes it rests on, goes into a receipt (`meio_parameters.json`, next to the policy).

Config (`scenario_config.json`):

    "meio": {..., "parameters": {
        "model": "clark_scarf_v2",          # or "meio_weekly"; no section = meio_weekly
        "selection": "validated",           # or "fixed": take `fixed` as given
        "min_seeds": 5,
        "evidence": ["output datasets/MEIO artifacts/SCENARIO_0002_MEIO_CLARK_SCARF_V2/results",
                     "models/MEIO/MEIO_Weekly/final_submission/results"],
        "clusters": "Clustering & Segmentation/segmentation_v4/sku_clusters.csv",
        "ddlt": "quadrature",               # or "monte_carlo" (the research scripts' method)
        "n_sims": 4000, "seed": 2026,
        "fixed": {"alpha": 0.2, "netting_discount": 0.0}}}

Evidence folders are searched in order, file by file: the first folder that holds a
file supplies it. run_all.py writes the first folder; the second is the committed copy.

The "validated" rule
--------------------
A parameter moves off its reference value only on paired multi-seed evidence (at
least `min_seeds` shared seeds) that the move raises fill by more than two standard
errors without raising landed cost by more than two standard errors.

1. netting_discount. Reference 1.0, the original model. Candidates come from
   netting_discount_multiseed*.csv at the reference alpha.
2. alpha. Reference 0.2, the alpha the netting test was run at. Candidates are alphas
   with multi-seed evidence at the chosen netting_discount. Evidence at any other
   netting_discount does not count: multiseed_alpha_04_06.csv was run at 1.0, before
   the netting fix.
3. Per-cluster service level and per-cluster alpha. These are used only when the sweep
   is complete (every candidate has min_seeds seeds) and was run at the chosen alpha
   and netting_discount.
   - Service level. The sweep's reference is the segmentation deliverable's cluster
     CSL ladder. That ladder must first beat today's segment ladder (the netting
     evidence at the chosen settings) on portfolio fill.
   - Moves. A cluster then moves off its reference only if its best candidate passes
     the paired test on the cluster's own fill, with portfolio fill and cost not
     significantly worse.

Each sweep's script fixes some settings without writing them to its CSV. CONTEXT
below records them, and a column in the file, when present, takes precedence.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd
from scipy.stats import norm

REPO_ROOT = Path(__file__).resolve().parents[4]

REFERENCE_ALPHA = 0.2          # validate_real_data.HEADLINE_ALPHA, segment_alpha_explore.REFERENCE_ALPHA
REFERENCE_NETTING = 1.0        # clark_scarf_v2.solve's original behaviour
CLUSTERS = ("Flagship Promo-Driven", "Mid-Tier Core", "Long-Tail Erratic", "Long-Tail Supply-Risk")
CLUSTER_ALPHAS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)     # segment_alpha_explore.ALPHAS
CLUSTER_CSLS = (0.90, 0.95, 0.97, 0.99)             # service_level_real_data.CANDIDATE_CSL
DEFAULTS = {"selection": "validated", "min_seeds": 5, "ddlt": "quadrature", "n_sims": 4000, "seed": 2026,
            "evidence": ["models/MEIO/MEIO_Weekly/final_submission/results"],
            "clusters": "Clustering & Segmentation/segmentation_v4/sku_clusters.csv"}

# name: (file glob, required columns, settings the script fixed but did not write)
EVIDENCE = {
    "netting_multiseed": ("netting_discount_multiseed*.csv",
                          {"netting_discount", "seed", "fill_delta_pp", "cost_delta_usd"}, {"alpha": 0.2}),
    "alpha_multiseed": ("multiseed_alpha_*.csv",
                        {"alpha", "seed", "fill_delta_pp", "cost_delta_usd"}, {"netting_discount": 1.0}),
    "cluster_alpha": ("segment_alpha_real_data_multiseed.csv",
                      {"cluster", "alpha", "seed", "own_fill_rate", "portfolio_fill_rate", "cost_delta_usd"},
                      {"netting_discount": 1.0}),
    "cluster_csl": ("service_level_real_data_multiseed.csv",
                    {"cluster", "csl", "seed", "own_fill_rate", "portfolio_fill_rate", "fill_delta_vs_actual_pp",
                     "cost_delta_usd"}, {"alpha": 0.2, "netting_discount": 0.0}),
}


class ParameterError(ValueError):
    """The evidence or the config's `meio.parameters` section can't be used as written."""


@dataclass
class Parameters:
    model: str
    selection: str = "none"
    alpha: float = REFERENCE_ALPHA
    netting_discount: float = REFERENCE_NETTING
    ddlt: str = "monte_carlo"
    n_sims: int = 4000
    seed: int = 2026
    alpha_by_sku: dict = field(default_factory=dict)
    z_by_sku: dict = field(default_factory=dict)
    decisions: list = field(default_factory=list)
    sources: list = field(default_factory=list)

    def decide(self, parameter: str, value, reason: str, **evidence) -> None:
        self.decisions.append({"parameter": parameter, "value": value, "reason": reason, **evidence})

    def receipt(self) -> dict:
        out = asdict(self)
        out["alpha_by_sku"] = len(self.alpha_by_sku)       # counts; the maps are rebuilt from the decisions
        out["z_by_sku"] = len(self.z_by_sku)
        return out

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.receipt(), indent=2, default=float) + "\n", encoding="utf-8")
        return path

    def summary(self) -> str:
        if self.model != "clark_scarf_v2":
            return f"MEIO model {self.model} (no tuned parameters)"
        extra = []
        if self.alpha_by_sku:
            extra.append(f"per-cluster alpha on {len(self.alpha_by_sku)} SKUs")
        if self.z_by_sku:
            extra.append(f"cluster service ladder on {len(self.z_by_sku)} SKUs")
        return (f"MEIO model clark_scarf_v2 [{self.selection}]: alpha={self.alpha:g} "
                f"netting_discount={self.netting_discount:g} ddlt={self.ddlt}"
                + (f"; {', '.join(extra)}" if extra else ""))


# ------------------------------------------------------------------ evidence --
def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_evidence(folders: list[str], params: Parameters | None = None) -> dict[str, pd.DataFrame]:
    """One frame per EVIDENCE kind, with its CONTEXT columns filled where the file has none.
    Folders are searched in order, file by file; a file whose columns don't match its
    schema is an error, not a silent skip."""
    found: dict[str, list[pd.DataFrame]] = {}
    for kind, (pattern, required, context) in EVIDENCE.items():
        taken: set[str] = set()
        for folder in folders:
            for path in sorted(_path(folder).glob(pattern)):
                if path.name in taken:
                    continue
                taken.add(path.name)
                df = pd.read_csv(path)
                missing = required - set(df.columns)
                if missing:
                    raise ParameterError(f"{path}: {kind} evidence is missing columns {sorted(missing)}")
                assumed = {c: v for c, v in context.items() if c not in df.columns}
                df = df.assign(**assumed)
                found.setdefault(kind, []).append(df)
                if params is not None:
                    params.sources.append({"kind": kind, "file": path.relative_to(REPO_ROOT).as_posix()
                                           if path.is_relative_to(REPO_ROOT) else str(path),
                                           "sha256": _sha(path), "rows": int(len(df)),
                                           "context_assumed": assumed})
    return {kind: pd.concat(frames, ignore_index=True) for kind, frames in found.items()}


# ---------------------------------------------------------------- the test --
def paired_test(cand: pd.DataFrame, ref: pd.DataFrame, fill: str, cost: str = "cost_delta_usd",
                guards: tuple = (), min_seeds: int = 5) -> dict:
    """Is `cand` better than `ref`? Paired by seed: the mean fill gain must exceed two
    standard errors; mean cost (and every guard column) must not move the wrong way by
    more than two standard errors."""
    m = cand.merge(ref, on="seed", suffixes=("", "_ref"))
    n = len(m)
    if n < min_seeds:
        return {"passes": False, "seeds": n, "why": f"{n} shared seeds (< {min_seeds})"}

    def stat(col, sign=1.0):
        d = sign * (m[col] - m[f"{col}_ref"])
        se = float(d.std(ddof=1) / math.sqrt(n)) if n > 1 else 0.0
        return float(d.mean()), se

    gain, gain_se = stat(fill)
    dcost, cost_se = stat(cost)
    out = {"seeds": n, "fill_gain": round(gain, 4), "fill_gain_se": round(gain_se, 4),
           "cost_delta_usd": round(dcost, 0), "cost_delta_se": round(cost_se, 0)}
    ok = gain > 2 * gain_se and gain > 0 and dcost <= 2 * cost_se
    for g in guards:
        d, se = stat(g)
        out[f"{g}_delta"] = round(d, 4)
        ok = ok and d >= -2 * se
    out["passes"] = bool(ok)
    return out


def _best(candidates: dict, ref_value, **test_args):
    """The candidate with the largest fill gain among those passing the paired test."""
    best, best_gain, tests = ref_value, 0.0, {}
    ref = candidates.get(ref_value)
    for value, frame in sorted(candidates.items()):
        if value == ref_value:
            continue
        t = paired_test(frame, ref, **test_args) if ref is not None else {"passes": False, "why": "no reference arm"}
        tests[str(value)] = t
        if t["passes"] and t["fill_gain"] > best_gain:
            best, best_gain = value, t["fill_gain"]
    return best, tests


# ---------------------------------------------------------------- the rule --
def _portfolio(ev: dict, params: Parameters, min_seeds: int) -> None:
    frames = [ev[k][["alpha", "netting_discount", "seed", "fill_delta_pp", "cost_delta_usd"]]
              for k in ("netting_multiseed", "alpha_multiseed") if k in ev]
    if not frames:
        params.decide("netting_discount", REFERENCE_NETTING, "no multi-seed evidence found; model default")
        params.decide("alpha", REFERENCE_ALPHA, "no multi-seed evidence found; model default")
        return
    port = pd.concat(frames, ignore_index=True).drop_duplicates(["alpha", "netting_discount", "seed"])
    port[["alpha", "netting_discount"]] = port[["alpha", "netting_discount"]].astype(float).round(4)

    at_ref = port[port.alpha == REFERENCE_ALPHA]
    nd, tests = _best({v: g for v, g in at_ref.groupby("netting_discount")}, REFERENCE_NETTING,
                      fill="fill_delta_pp", min_seeds=min_seeds)
    params.netting_discount = float(nd)
    params.decide("netting_discount", params.netting_discount,
                  "moved off the reference: better fill, cost not worse, on paired seeds" if nd != REFERENCE_NETTING
                  else "no candidate beat the reference on paired seeds",
                  reference=REFERENCE_NETTING, at_alpha=REFERENCE_ALPHA, tests=tests)

    at_nd = port[port.netting_discount == params.netting_discount]
    alpha, tests = _best({v: g for v, g in at_nd.groupby("alpha")}, REFERENCE_ALPHA,
                         fill="fill_delta_pp", min_seeds=min_seeds)
    elsewhere = sorted(set(port.alpha) - set(at_nd.alpha))
    params.alpha = float(alpha)
    params.decide("alpha", params.alpha,
                  "moved off the reference: better fill, cost not worse, on paired seeds" if alpha != REFERENCE_ALPHA
                  else ("no other alpha has multi-seed evidence at this netting_discount" if not tests
                        else "no candidate beat the reference on paired seeds"),
                  reference=REFERENCE_ALPHA, at_netting_discount=params.netting_discount, tests=tests,
                  not_counted={str(a): f"evidence only at netting_discount "
                                       f"{sorted(set(port[port.alpha == a].netting_discount))}" for a in elsewhere})


def _complete(df: pd.DataFrame, key: str, grid: tuple, min_seeds: int) -> tuple[bool, str]:
    counts = df.groupby(["cluster", key]).seed.nunique()
    have = sum(int(counts.get((c, v), 0) >= min_seeds) for c in CLUSTERS for v in grid)
    total = len(CLUSTERS) * len(grid)
    return have == total, f"{have} of {total} (cluster, {key}) combinations have >= {min_seeds} seeds"


def _context_matches(df: pd.DataFrame, params: Parameters) -> tuple[bool, str]:
    alphas, nds = sorted(set(df.alpha.astype(float))), sorted(set(df.netting_discount.astype(float)))
    ok = alphas == [params.alpha] and nds == [params.netting_discount]
    return ok, f"run at alpha {alphas}, netting_discount {nds}; selected alpha {params.alpha:g}, " \
               f"netting_discount {params.netting_discount:g}"


def _clusters(ev: dict, params: Parameters, clusters_csv: Path, min_seeds: int) -> None:
    skus = pd.read_csv(clusters_csv)[["sku_id", "archetype", "recommended_csl", "safety_factor_z"]]
    guards = ("portfolio_fill_rate",)

    df = ev.get("cluster_csl")
    if df is None:
        params.decide("service_level_by_cluster", None, "no service-level sweep found; segment ladder kept")
    else:
        ok_ctx, ctx = _context_matches(df, params)
        complete, how = _complete(df, "csl", CLUSTER_CSLS, min_seeds)
        if not (ok_ctx and complete):
            params.decide("service_level_by_cluster", None, "not used; segment ladder kept: "
                          + "; ".join(w for w, bad in ((how, not complete), (ctx, not ok_ctx)) if bad))
        else:
            rec = dict(zip(skus.archetype, skus.recommended_csl.astype(float)))
            ladder = df[[rec.get(c) == csl for c, csl in zip(df.cluster, df.csl)]].drop_duplicates("seed")
            seg = ev.get("netting_multiseed")
            seg = None if seg is None else seg[(seg.alpha == params.alpha)
                                               & (seg.netting_discount == params.netting_discount)]
            t = (paired_test(ladder.rename(columns={"fill_delta_vs_actual_pp": "fill_delta_pp"}), seg,
                             fill="fill_delta_pp", min_seeds=min_seeds)
                 if seg is not None and len(seg) else {"passes": False, "why": "no segment-ladder arm to compare"})
            if not t["passes"]:
                params.decide("service_level_by_cluster", None,
                              "cluster ladder did not beat the segment ladder; segment ladder kept", test=t)
            else:
                chosen = {}
                for cluster in CLUSTERS:
                    sub = df[df.cluster == cluster]
                    best, tests = _best({v: g for v, g in sub.groupby("csl")}, rec[cluster],
                                        fill="own_fill_rate", guards=guards, min_seeds=min_seeds)
                    chosen[cluster] = float(best)
                    params.decide(f"service_level[{cluster}]", float(best), "paired test on own fill",
                                  reference=rec[cluster], tests=tests)
                params.z_by_sku = {s: (float(z) if chosen[a] == r else float(norm.ppf(chosen[a])))
                                   for s, a, r, z in skus.itertuples(index=False) if a in chosen}
                params.decide("service_level_by_cluster", chosen, "cluster ladder adopted", ladder_test=t)

    df = ev.get("cluster_alpha")
    if df is None:
        params.decide("alpha_by_cluster", None, "no per-cluster alpha sweep found; portfolio alpha everywhere")
        return
    df = df.assign(alpha=df.alpha.astype(float))
    ref_ok = params.alpha == REFERENCE_ALPHA
    nds = sorted(set(df.netting_discount.astype(float)))
    complete, how = _complete(df, "alpha", CLUSTER_ALPHAS, min_seeds)
    reasons = [how] if not complete else []
    if nds != [params.netting_discount]:
        reasons.append(f"run at netting_discount {nds}; selected {params.netting_discount:g}")
    if not ref_ok:
        reasons.append(f"run around alpha {REFERENCE_ALPHA:g}; selected {params.alpha:g}")
    if reasons:
        params.decide("alpha_by_cluster", None, "not used; portfolio alpha everywhere: " + "; ".join(reasons))
        return
    chosen = {}
    for cluster in CLUSTERS:
        sub = df[df.cluster == cluster]
        best, tests = _best({v: g for v, g in sub.groupby("alpha")}, REFERENCE_ALPHA,
                            fill="own_fill_rate", guards=guards, min_seeds=min_seeds)
        chosen[cluster] = float(best)
        params.decide(f"alpha[{cluster}]", float(best), "paired test on own fill", reference=REFERENCE_ALPHA,
                      tests=tests)
    params.alpha_by_sku = {s: chosen[a] for s, a in zip(skus.sku_id, skus.archetype)
                           if a in chosen and chosen[a] != params.alpha}
    params.decide("alpha_by_cluster", chosen, "per-cluster alpha adopted (measured one cluster at a time, "
                                              "not jointly)")


# ------------------------------------------------------------------- entry --
def resolve(config: dict) -> Parameters:
    """The parameters the config's MEIO builds its policy with."""
    import scenario
    model = scenario.meio_model(config)
    if model != "clark_scarf_v2":
        p = Parameters(model=model)
        p.decide("model", model, "MEIO Weekly has no tuned parameters; run_all.py's evidence is for Clark-Scarf v2")
        return p
    cfg = {**DEFAULTS, **(config["meio"].get("parameters") or {})}
    if cfg["ddlt"] not in ("quadrature", "monte_carlo"):
        raise ParameterError(f"meio.parameters.ddlt must be 'quadrature' or 'monte_carlo', got {cfg['ddlt']!r}")
    p = Parameters(model=model, selection=cfg["selection"], ddlt=cfg["ddlt"], n_sims=int(cfg["n_sims"]),
                   seed=int(cfg["seed"]))
    if cfg["selection"] == "fixed":
        fixed = cfg.get("fixed") or {}
        p.alpha = float(fixed.get("alpha", REFERENCE_ALPHA))
        p.netting_discount = float(fixed.get("netting_discount", REFERENCE_NETTING))
        p.decide("alpha", p.alpha, "fixed in meio.parameters")
        p.decide("netting_discount", p.netting_discount, "fixed in meio.parameters")
        return p
    if cfg["selection"] != "validated":
        raise ParameterError(f"meio.parameters.selection must be 'validated' or 'fixed', got {cfg['selection']!r}")
    min_seeds = int(cfg["min_seeds"])
    ev = load_evidence(list(cfg["evidence"]), p)
    _portfolio(ev, p, min_seeds)
    _clusters(ev, p, _path(cfg["clusters"]), min_seeds)
    return p
