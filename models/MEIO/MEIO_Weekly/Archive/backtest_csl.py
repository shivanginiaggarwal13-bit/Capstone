"""
Supply Brain — MEIO Weekly Statistical CSL Backtest
=====================================================

Answers a narrower question than a full inventory simulation: **if this
week's recommended reorder point had been in force, would realized demand
during the replenishment lead time actually have stayed within it?**

No lookahead: at each as-of week in the sweep, the reorder point is
recomputed using only data at or before that week (the exact same
`meio_weekly_lib` functions the live weekly optimizer uses), then checked
against demand that was realized *after* that as-of week -- data the
recommendation could not have seen.

    python backtest_csl.py --start-week 2025-W14 --end-week 2025-W48

Lead-time-demand window: lt_mean_wk weeks starting at the target week, with
the final partial week weighted by its fraction (a uniform-within-week
approximation -- demand data is weekly grain, so this is the closest
achievable match to the same mean_demand x lt_mean_wk assumption the
newsvendor sizing in meio_weekly_lib.solve_policy uses).

Outputs:
    backtest_output/backtest_detail.csv   (gitignored; one row per sku x dc x as-of week)
    calibration_summary.csv               (repo-tracked; achieved vs target CSL, by category/DC/overall)
    chart_csl_calibration.png             (repo-tracked)
    backtest_report.md                    (repo-tracked)
"""
import argparse
import math
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meio_weekly_lib as lib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "input datasets"))


def df_to_md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r.values) + " |")
    return "\n".join(lines)


def realized_lt_demand(dfc_agg: pd.DataFrame, period_index: pd.DataFrame,
                        target_week_num: int, lt_weeks_by_dc: pd.Series) -> pd.DataFrame:
    """Realized demand over the fractional lead-time window starting at the
    target week, per (sku_id, dc_id) -- using EACH DC'S OWN lead time, not a
    portfolio-wide max. Most DCs in this network have lt_mean_wk well under
    1 week; weekly demand_forecast.csv has no sub-week grain, so a
    lead time of e.g. 0.65 weeks is approximated as 0.65 x that week's
    demand (uniform-within-week assumption) rather than rounded up to a
    full week -- rounding up would check a policy sized for 0.65 weeks of
    demand against a full week of realized demand and understate achieved
    CSL for every short-lead-time DC. A lead time > 1 week sums whole
    weeks plus a fractional final week the same way. Rows whose window
    runs past the last available in_sample week are dropped (can't check
    them -- not penalized either way)."""
    frames = []
    for dc, lt in lt_weeks_by_dc.items():
        full_weeks = int(np.floor(lt))
        frac = lt - full_weeks
        week_nums = list(range(target_week_num, target_week_num + full_weeks))
        weights = [1.0] * full_weeks
        if frac > 1e-9 or not weights:
            week_nums.append(target_week_num + full_weeks)
            weights.append(frac if frac > 1e-9 else 1.0)
        wk_map = dict(zip(week_nums, weights))

        periods_needed = period_index[period_index.week_num.isin(week_nums)][["week_num", "period"]]
        w = dfc_agg[(dfc_agg.horizon == "in_sample") & (dfc_agg.dc_id == dc) &
                     (dfc_agg.period.isin(periods_needed.period))]
        w = w.merge(periods_needed, on="period")
        w["weighted_qty"] = w["actual_qty"] * w["week_num"].map(wk_map)

        got_weeks = w.groupby("sku_id")["period"].nunique().rename("weeks_observed")
        realized = w.groupby("sku_id")["weighted_qty"].sum().rename("realized_lt_demand")
        sub = pd.concat([realized, got_weeks], axis=1).reset_index()
        sub["dc_id"] = dc
        sub["weeks_needed"] = len(week_nums)
        frames.append(sub)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["sku_id", "realized_lt_demand", "weeks_observed", "dc_id", "weeks_needed"])


def run(data_dir, start_week, end_week, short_window, long_window):
    dfc = lib.load_demand_forecast(data_dir)
    period_index = lib.load_period_index(data_dir)
    active_rdc, active_cdc = lib.active_nodes(dfc)
    rdc_lt, cdc_lt, cdc_primary_rdc = lib.build_leadtime_graph(data_dir, active_rdc, active_cdc)
    leadtime_by_dc = {**rdc_lt, **cdc_lt}
    cost = lib.load_sku_cost(data_dir)
    seasonal_index = lib.build_seasonal_index(data_dir)
    sku_subcat = lib.load_sku_subcategory(data_dir)

    lt_wk = pd.Series({dc: leadtime_by_dc[dc][0] / 7.0 for dc in leadtime_by_dc})

    start_num = int(period_index.loc[period_index.period == start_week, "week_num"].iloc[0])
    end_num = int(period_index.loc[period_index.period == end_week, "week_num"].iloc[0])

    detail_rows = []
    for as_of_num in range(start_num, end_num + 1):
        as_of_period = period_index.loc[period_index.week_num == as_of_num, "period"].iloc[0]
        try:
            demand = lib.target_week_demand(dfc, as_of_num, period_index, seasonal_index, sku_subcat)
        except ValueError:
            continue
        target_period = demand["target_period"].iloc[0]
        target_num = as_of_num + 1

        policy = lib.solve_policy(demand, leadtime_by_dc, cost, active_rdc, active_cdc, cdc_primary_rdc)
        realized = realized_lt_demand(dfc, period_index, target_num, lt_wk)

        chk = policy.merge(realized, on=["sku_id", "dc_id"], how="inner")
        chk = chk[chk["weeks_observed"] >= chk["weeks_needed"]]  # only fully-observed windows
        chk["as_of_week"] = as_of_period
        chk["target_week"] = target_period
        chk["within_rop"] = chk["realized_lt_demand"] <= chk["reorder_point"]
        detail_rows.append(chk[["as_of_week", "target_week", "sku_id", "dc_id", "category",
                                 "lt_mean_wk", "target_csl", "reorder_point", "realized_lt_demand",
                                 "within_rop"]])
        print(f"  as-of {as_of_period} -> target {target_period}: {len(chk):,} checkable lines")

    detail = pd.concat(detail_rows, ignore_index=True)
    os.makedirs(os.path.join(SCRIPT_DIR, "backtest_output"), exist_ok=True)
    detail.to_csv(os.path.join(SCRIPT_DIR, "backtest_output", "backtest_detail.csv"), index=False)
    print(f"\nWrote {len(detail):,} detail rows to backtest_output/backtest_detail.csv")

    overall = pd.DataFrame([{
        "scope": "portfolio",
        "lines": len(detail),
        "achieved_csl": detail.within_rop.mean(),
        "avg_target_csl": detail.target_csl.mean(),
    }])
    by_cat = detail.groupby("category").agg(
        lines=("within_rop", "size"),
        achieved_csl=("within_rop", "mean"),
        avg_target_csl=("target_csl", "mean"),
    ).reset_index()
    by_dc = detail.groupby("dc_id").agg(
        lines=("within_rop", "size"),
        achieved_csl=("within_rop", "mean"),
        avg_target_csl=("target_csl", "mean"),
    ).reset_index()

    summary = pd.concat([
        overall.rename(columns={"scope": "key"}),
        by_cat.rename(columns={"category": "key"}).assign(scope="category")[["scope", "key", "lines", "achieved_csl", "avg_target_csl"]],
        by_dc.rename(columns={"dc_id": "key"}).assign(scope="dc")[["scope", "key", "lines", "achieved_csl", "avg_target_csl"]],
    ], ignore_index=True)
    summary.to_csv(os.path.join(SCRIPT_DIR, "calibration_summary.csv"), index=False)

    fig, ax = plt.subplots(figsize=(8, 5))
    cats = by_cat.sort_values("achieved_csl")
    x = np.arange(len(cats))
    ax.barh(x, cats.achieved_csl * 100, color="#2b6cb0", label="Achieved CSL")
    ax.scatter(cats.avg_target_csl * 100, x, color="#e53e3e", zorder=5, label="Avg target CSL")
    ax.set_yticks(x)
    ax.set_yticklabels(cats.category)
    ax.set_xlabel("Cycle service level (%)")
    ax.set_title("Backtest: Achieved vs Target CSL, by Category")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(SCRIPT_DIR, "chart_csl_calibration.png"), dpi=150)
    plt.close(fig)

    achieved = overall.achieved_csl.iloc[0]
    target = overall.avg_target_csl.iloc[0]
    report = f"""# MEIO Weekly — Statistical CSL Backtest
Supply Brain capstone | generated by `backtest_csl.py`

## Window
As-of weeks {start_week} .. {end_week} ({end_num - start_num + 1} weeks), no lookahead:
every reorder point checked here was computed from data at or before its
as-of week only, using the same estimation functions
(`meio_weekly_lib.target_week_demand`, bias-corrected, blended-CV) the live
weekly optimizer uses.

## Result
- **Achieved portfolio CSL: {achieved:.1%}** vs average target {target:.1%}
  across {len(detail):,} checkable (sku, dc, as-of-week) lines.
- Lead-time-demand window = lt_mean_wk weeks from the target week, with the
  final partial week weighted by its fraction (uniform-within-week
  approximation, since demand data is weekly grain); rows whose window
  runs past the last available in_sample week are excluded, not penalized.

## By category
{df_to_md(by_cat.round(3))}

## By DC
{df_to_md(by_dc.round(3))}

## Files
- `backtest_output/backtest_detail.csv` (gitignored, ~{len(detail):,} rows)
- `calibration_summary.csv`, `chart_csl_calibration.png`
"""
    with open(os.path.join(SCRIPT_DIR, "backtest_report.md"), "w") as f:
        f.write(report)
    print(f"\nAchieved CSL: {achieved:.1%}  vs target {target:.1%}")
    print("Wrote calibration_summary.csv, chart_csl_calibration.png, backtest_report.md")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start-week", default="2025-W14")
    ap.add_argument("--end-week", default="2025-W48")
    ap.add_argument("--data-dir", default=DEFAULT_DATA)
    ap.add_argument("--short-window", type=int, default=lib.SHORT_WINDOW_WEEKS)
    ap.add_argument("--long-window", type=int, default=lib.LONG_WINDOW_WEEKS)
    args = ap.parse_args()
    run(args.data_dir, args.start_week, args.end_week, args.short_window, args.long_window)


if __name__ == "__main__":
    main()
