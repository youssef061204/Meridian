"""Gate 6: SIMULATED inventory scenarios on held-out forecast episodes.

Run (after meridian.forecast): PYTHONPATH=src python -m meridian.inventory [--pytest]

SIMULATED - assumptions, not client outcomes. Observed sales are only a demand
proxy (no stock-out data exist), all stock levels, lead times and costs are
ASSUMED, and costs are abstract "cost units", not GBP.

Episodes: each product in the frozen test panel over the 28 test days after the
first test origin. Every policy sees identical demand, starting stock, lead time,
review days, safety stock and costs; only the point forecast differs.
Periodic review (every 7 days, at the end of each test-origin day), order-up-to:
  order = max(0, S - inventory position); arrives at the start of day review+L.
  fixed policy:    S = mu0 * (L + R) + SS              (mu0 frozen at episode start)
  forecast policy: S = sum of forecasts issued that day for days +1..+(L+R) + SS
  SS = z * sigma0 * sqrt(L + R), identical for all policies.
Unmet demand is lost (shortage cost per unit); holding cost accrues on end-of-day
stock. Days with no source records have no recorded sales: demand 0 (ASSUMED).
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from .common import ROOT, atomic_json, now, read_json, run_record, sha256

OUT = ROOT / "analysis" / "forecast"
LABEL = "SIMULATED - assumptions, not client outcomes"
ASSUMPTIONS = dict(
    label=LABEL,
    cost_unit="abstract cost units (not GBP)",
    review_period_days=dict(value=7, status="ASSUMED"),
    lead_time_days=dict(value=3, status="ASSUMED"),
    holding_cost_per_unit_day=dict(value=1.0, status="ASSUMED"),
    shortage_cost_per_unit=dict(value=100.0, status="ASSUMED", note="lost-sale penalty; ratio to holding = 100 (one lost unit costs as much as holding one unit for 100 days)"),
    starting_stock_days_of_cover=dict(value=14, status="ASSUMED", note="starting on-hand = 14 x mu0, identical for every policy"),
    safety_z=dict(value=0.84, status="ASSUMED", note="about an 80% one-sided normal quantile"),
    mu0=dict(status="ASSUMED", note="calendar-day mean of observed sales in the 28 days ending at the episode start (unavailable days count as 0)"),
    sigma0=dict(status="ASSUMED", note="std of daily observed sales on observation-available days in those 28 days"),
    unavailable_day_demand=dict(value=0, status="ASSUMED", note="no source records -> no recorded sales"),
    demand_proxy="observed positive merchandise units (censored by any historical stock-outs; not unconstrained demand)",
    end_of_episode="orders still in transit at the end of the episode are ignored for every policy",
)
GRID = dict(lead_time_days=[1, 3, 7, 14], shortage_to_holding_ratio=[5, 10, 25, 100, 500], safety_z=[0.0, 0.84, 1.28])


def simulate(demand, review_days, S_by_review, start_stock, lead, holding, shortage):
    """demand: array for days 1..T; review_days: 0-based day index after whose end an
    order is placed (0 = episode start, before day 1); S_by_review: order-up-to per review."""
    T = len(demand)
    on_hand, pipeline = float(start_stock), {}
    hold = lost = sold = ordered = 0.0
    on_hand_trace = []
    for t in range(T + 1):            # t = 0 is the review at the end of the origin day
        if t > 0:
            on_hand += pipeline.pop(t, 0.0)
            d = demand[t - 1]
            s = min(on_hand, d)
            on_hand -= s
            sold += s
            lost += d - s
            hold += on_hand
            on_hand_trace.append(on_hand)
        if t in review_days:
            position = on_hand + sum(pipeline.values())
            q = max(0.0, S_by_review[t] - position)
            if q > 0:
                pipeline[t + lead] = pipeline.get(t + lead, 0.0) + q
                ordered += q
    total_demand = float(np.sum(demand))
    return dict(holding_cost=hold * holding, shortage_cost=lost * shortage, total_cost=hold * holding + lost * shortage,
                lost_units=lost, sold_units=sold, demand_units=total_demand, ordered_units=ordered,
                fill_rate=sold / total_demand if total_demand else None, mean_on_hand=float(np.mean(on_hand_trace)))


def episodes(pred, histories):
    """Build identical held-out episodes from preserved test predictions."""
    test = pred[pred.role == "test"].copy()
    origins = sorted(test.origin.unique())
    start = origins[0]
    days = pd.date_range(pd.Timestamp(start) + pd.Timedelta(days=1), periods=28, freq="D")
    out = []
    for sid in sorted(test[(test.scope == "products") & (test.origin == start)].series_id.unique()):
        s0 = test[(test.series_id == sid) & (test.origin == start)].drop_duplicates("target_date").set_index("target_date")
        actual = s0.reindex(days)
        if actual.available.isna().any():
            raise ValueError(f"episode for {sid} not fully inside the test period")
        demand = actual.actual.fillna(0.0).to_numpy()
        hist = history_stats(histories[sid], start)
        forecasts = {m: {pd.Timestamp(o): test[(test.series_id == sid) & (test.origin == o) & (test.model == m)]
                         .set_index("target_date").prediction for o in origins}
                     for m in sorted(test.model.unique())}
        out.append(dict(series_id=sid, start=pd.Timestamp(start), days=days, demand=demand,
                        mu0=hist["mu0"], sigma0=hist["sigma0"], forecasts=forecasts,
                        review_index=[(pd.Timestamp(o) - pd.Timestamp(start)).days for o in origins]))
    return out


def history_stats(history, start, window=28):
    """mu0 / sigma0 from a calendar-aligned history array ending at the episode start (NaN = unavailable)."""
    tail = np.asarray(history, float)[-window:]
    avail = tail[np.isfinite(tail)]
    return dict(mu0=float(np.nansum(tail) / window), sigma0=float(np.std(avail, ddof=1)) if len(avail) > 1 else 0.0)


def load_histories(snapshot_id, codes, start):
    from . import forecast as fc
    _, base = fc.snapshot_paths(snapshot_id)
    cal, prod, _ = fc.load_inputs(base)
    cal_hist = cal[cal.date <= start].reset_index(drop=True)
    series, _ = fc.build_series(cal_hist, prod[prod.date <= start], codes)
    return series


def order_up_to(ep, policy, lead, review, z):
    ss = z * ep["sigma0"] * np.sqrt(lead + review)
    levels = {}
    for idx in ep["review_index"]:
        if policy == "fixed":
            levels[idx] = ep["mu0"] * (lead + review) + ss
        else:
            origin = ep["start"] + pd.Timedelta(days=idx)
            f = ep["forecasts"][policy][origin]
            window = pd.date_range(origin + pd.Timedelta(days=1), periods=lead + review, freq="D")
            if not window.isin(f.index).all():
                raise ValueError("forecast does not cover the protection window")
            levels[idx] = float(f.reindex(window).sum()) + ss
    return levels


def run_grid(eps, policies, assumptions=ASSUMPTIONS, grid=GRID):
    base = {k: assumptions[k]["value"] for k in ("review_period_days", "lead_time_days", "holding_cost_per_unit_day",
                                                 "shortage_cost_per_unit", "starting_stock_days_of_cover", "safety_z")}
    scenarios = [dict(lead_time_days=L, shortage_to_holding_ratio=r, safety_z=base["safety_z"])
                 for L in grid["lead_time_days"] for r in grid["shortage_to_holding_ratio"]]
    scenarios += [dict(lead_time_days=base["lead_time_days"],
                       shortage_to_holding_ratio=base["shortage_cost_per_unit"] / base["holding_cost_per_unit_day"], safety_z=z)
                  for z in grid["safety_z"] if z != base["safety_z"]]
    rows = []
    for sc in scenarios:
        holding = base["holding_cost_per_unit_day"]
        shortage = sc["shortage_to_holding_ratio"] * holding
        for ep in eps:
            for p in policies:
                levels = order_up_to(ep, p, sc["lead_time_days"], base["review_period_days"], sc["safety_z"])
                r = simulate(ep["demand"], set(ep["review_index"]), levels, base["starting_stock_days_of_cover"] * ep["mu0"],
                             sc["lead_time_days"], holding, shortage)
                rows.append(dict(sc, series_id=ep["series_id"], policy=p, **r))
    return pd.DataFrame(rows), base


def summarise(df, selected):
    keys = ["lead_time_days", "shortage_to_holding_ratio", "safety_z"]
    df = df.assign(cost_per_demand_unit=df.total_cost / df.demand_units)
    agg = df.groupby(keys + ["policy"]).agg(total_cost=("total_cost", "sum"), holding_cost=("holding_cost", "sum"),
                                            shortage_cost=("shortage_cost", "sum"), lost_units=("lost_units", "sum"),
                                            demand_units=("demand_units", "sum"), sold_units=("sold_units", "sum"),
                                            mean_cost_per_demand_unit=("cost_per_demand_unit", "mean")).reset_index()
    agg["fill_rate"] = agg.sold_units / agg.demand_units
    out = []
    for k, g in agg.groupby(keys):
        g = g.set_index("policy")
        best = g.total_cost.idxmin()
        row = dict(lead_time_days=int(k[0]), shortage_to_holding_ratio=float(k[1]), safety_z=float(k[2]),
                   lowest_total_cost_policy=best, policies={})
        for p, r in g.iterrows():
            row["policies"][p] = {c: round(float(r[c]), 4) for c in ["total_cost", "holding_cost", "shortage_cost", "lost_units",
                                                                    "fill_rate", "mean_cost_per_demand_unit"]}
        for ref in ("fixed", "seasonal_naive"):
            if ref in g.index and selected in g.index and g.total_cost[ref]:
                row[f"selected_vs_{ref}_cost_change"] = round(float((g.total_cost[selected] - g.total_cost[ref]) / g.total_cost[ref]), 4)
        out.append(row)
    return out


def chart(df, selected, base):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "figure.facecolor": "white", "axes.facecolor": "white",
                         "savefig.facecolor": "white"})
    colors = {"fixed": "#6b7280", "seasonal_naive": "#157f6b", "trailing_mean": "#a16207", "gradient_boosting": "#26547c"}
    names = {"fixed": "Fixed", "seasonal_naive": "Seasonal naive", "trailing_mean": "Trailing mean", "gradient_boosting": "Gradient boosting"}
    sub = df[df.safety_z == base["safety_z"]]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, (dim, fixed_dim, fixed_val, xlabel) in zip(axes, [
            ("lead_time_days", "shortage_to_holding_ratio", base["shortage_cost_per_unit"] / base["holding_cost_per_unit_day"], "Lead time (days)"),
            ("shortage_to_holding_ratio", "lead_time_days", base["lead_time_days"], "Shortage / holding cost ratio")]):
        g = sub[sub[fixed_dim] == fixed_val].groupby([dim, "policy"]).total_cost.sum().unstack()
        for p in [p for p in colors if p in g]:
            ax.plot(g.index, g[p], marker="o", lw=2 if p == selected else 1.6, color=colors[p],
                    label=names[p] + (" (selected model)" if p == selected else ""))
        if dim == "shortage_to_holding_ratio":
            ax.set_xscale("log")
            ax.minorticks_off()
        ax.set_xticks(g.index, [f"{v:g}" for v in g.index])
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Simulated total cost (cost units)")
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.set_title(f"Cost vs {xlabel.split(' (')[0].lower()}", loc="left", pad=20)
        ax.text(0, 1.015, f"{fixed_dim.replace('_', ' ')} = {fixed_val:g}, safety z = {base['safety_z']:g}",
                transform=ax.transAxes, fontsize=8, color="#4b5563", va="bottom")
    axes[0].legend(frameon=False, fontsize=8.5)
    fig.text(.01, .012, f"{LABEL}. Summed over held-out product episodes; ASSUMED costs, stock and lead times.", fontsize=8, color="#4b5563")
    fig.tight_layout(rect=(0, .05, 1, 1))
    for ext in ("svg", "png"):
        fig.savefig(OUT / f"inventory_sensitivity.{ext}", dpi=140)
    plt.close(fig)
    return "analysis/forecast/inventory_sensitivity.png"


def main(argv=None):
    parser = argparse.ArgumentParser(description="SIMULATED inventory scenarios on held-out forecasts")
    parser.add_argument("--pytest", action="store_true")
    args = parser.parse_args(argv)
    t0 = time.perf_counter()
    with run_record("inventory") as record:
        metrics = read_json(OUT / "metrics.json")
        pred = pd.read_csv(OUT / "predictions.csv", parse_dates=["origin", "target_date"], dtype={"series_id": str, "config": str})
        selected = metrics["promoted_model"]
        atomic_json(OUT / "inventory_assumptions.json", dict(ASSUMPTIONS, grid=GRID))
        test = pred[pred.role == "test"]
        start = test.origin.min()
        codes = sorted(test[(test.scope == "products") & (test.origin == start)].series_id.unique())
        eps = episodes(pred, load_histories(metrics["snapshot_id"], codes, start))
        policies = ["fixed"] + sorted(m for m in pred[pred.role == "test"].model.unique())
        df, base = run_grid(eps, policies)
        df.to_csv(OUT / "inventory_runs.csv", index=False, float_format="%.4f")
        scen = summarise(df, selected)
        base_key = (base["lead_time_days"], base["shortage_cost_per_unit"] / base["holding_cost_per_unit_day"], base["safety_z"])
        headline = next(s for s in scen if (s["lead_time_days"], s["shortage_to_holding_ratio"], s["safety_z"]) == base_key)
        wins = pd.Series([s["lowest_total_cost_policy"] for s in scen]).value_counts().to_dict()
        per_episode = df[(df.lead_time_days == base["lead_time_days"]) & (df.safety_z == base["safety_z"])
                         & (df.shortage_to_holding_ratio == base_key[1])]
        result = dict(
            state="passed", label=LABEL, generated_at=now(), snapshot_id=metrics["snapshot_id"],
            forecast_config_sha256=metrics["config_sha256"], selected_model=selected,
            selected_model_reason="promoted forecast model from rolling-origin validation (meridian.forecast)",
            policies=policies, assumptions=dict(ASSUMPTIONS, grid=GRID),
            episodes=[dict(series_id=e["series_id"], start=str(e["start"].date()), days=len(e["days"]),
                           observed_demand_units=float(e["demand"].sum()), mu0=round(e["mu0"], 3), sigma0=round(e["sigma0"], 3),
                           review_days=[str((e["start"] + pd.Timedelta(days=i)).date()) for i in e["review_index"]]) for e in eps],
            base_scenario=headline,
            base_scenario_per_episode=[{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
                                       for r in per_episode.drop(columns=["safety_z"]).to_dict("records")],
            sensitivity=scen, lowest_cost_policy_counts={k: int(v) for k, v in wins.items()},
            chart=chart(df, selected, base),
            limitations=[LABEL + ".",
                         "Observed sales are a censored demand proxy; no stock-out or inventory records exist, so lost sales are simulated, not measured.",
                         "Only held-out test episodes are used (one 28-day window per product); results are small-sample and illustrative.",
                         "Costs are abstract cost units chosen as assumptions; they are not GBP and not savings achieved by any business.",
                         "The fixed policy and safety stock use only information known at the episode start."],
        )
        atomic_json(OUT / "inventory.json", result)
        tests = None
        if args.pytest:
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_inventory.py"], cwd=ROOT,
                                  capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
            tests = dict(command="python -m pytest -q tests/test_inventory.py", returncode=proc.returncode,
                         summary=(proc.stdout.strip().splitlines() or [""])[-1])
        atomic_json(ROOT / "evidence" / "inventory-run.json", dict(
            stage="inventory", label=LABEL, state="passed" if tests is None or tests["returncode"] == 0 else "failed",
            command="PYTHONPATH=src .venv/Scripts/python.exe -m meridian.inventory" + (" --pytest" if args.pytest else ""),
            run_id=record["run_id"], git_sha=record.get("git_sha"), source_tree_sha256=record["source_tree_sha256"],
            duration_s=round(time.perf_counter() - t0, 2), python=platform.python_version(), pandas=pd.__version__, numpy=np.__version__,
            snapshot_id=metrics["snapshot_id"], forecast_config_sha256=metrics["config_sha256"],
            inputs={p: sha256(OUT / p) for p in ("predictions.csv", "metrics.json")},
            outputs={p: sha256(OUT / p) for p in ("inventory.json", "inventory_runs.csv", "inventory_assumptions.json")},
            selected_model=selected, scenarios=len(scen), episodes=len(eps), base_scenario=headline,
            lowest_cost_policy_counts=result["lowest_cost_policy_counts"], tests=tests))
    if tests and tests["returncode"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
