"""Gate 4: observed-sales forecasting with rolling-origin evaluation and an untouched test.

Run: PYTHONPATH=src python -m meridian.forecast [--snapshot ID] [--pytest]

Target: positive merchandise units (sum of quantity > 0 on accepted merchandise
lines) per calendar day for the aggregate series and products selected at each
origin. These are observed sales, not unconstrained demand.

Protocol (written to analysis/forecast/config.json and hashed before any fit):
  * origins every 28 days ending at a test origin 28 days before the last complete
    source day; 4 warm-up origins only seed interval residuals, 6 validation origins
    are used for tuning and promotion, and the test origins are evaluated once with
    the frozen choice;
  * each origin sees only rows dated <= origin (history is truncated first);
  * forecasts cover calendar days 1..28 ahead (direct, horizon as a feature for the
    tabular model; baselines are functions of history only); metrics use only
    observation-available target days;
  * 80% intervals are empirical 10/90% quantiles of scale-normalised residuals from
    earlier origins whose target dates are <= the current origin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "2")

import duckdb
import numpy as np
import pandas as pd

from .common import DATA, ROOT, atomic_json, now, read_json, run_record, sha256

SEED = 20261116
OUT = ROOT / "analysis" / "forecast"
MAX_H = 28
HORIZONS = (7, 28)
AGG = "__ALL__"
MODELS = ("seasonal_naive", "trailing_mean", "gradient_boosting")
PARAMS = dict(
    n_products=5, selection_window_days=182, selection_min_sold_share=0.5,
    scale_window_days=56, trailing_mean_days=28, cold_start_days=28,
    non_trading_weekday_rate=0.10, min_interval_residuals=25,
)
GBM_GRID = {
    "g1": dict(learning_rate=0.05, max_iter=300, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=0.0),
    "g2": dict(learning_rate=0.05, max_iter=300, max_leaf_nodes=31, min_samples_leaf=20, l2_regularization=1.0),
    "g3": dict(learning_rate=0.10, max_iter=150, max_leaf_nodes=7, min_samples_leaf=80, l2_regularization=0.0),
}
WARMUP, VALIDATION = 4, 6


# ---------------------------------------------------------------- configuration
def build_config(snapshot_id, last_complete):
    last = pd.Timestamp(last_complete)
    test0 = last - timedelta(days=MAX_H)
    step = lambda k: (test0 - timedelta(days=MAX_H * k)).date().isoformat()
    origins = [dict(origin=step(k), role="warmup") for k in range(WARMUP + VALIDATION, VALIDATION, -1)]
    origins += [dict(origin=step(k), role="validation") for k in range(VALIDATION, 0, -1)]
    origins += [dict(origin=(test0 + timedelta(days=7 * k)).date().isoformat(), role="test") for k in range(4)]
    return dict(
        snapshot_id=snapshot_id, seed=SEED, target="positive merchandise units (observed sales), calendar day",
        last_complete_day=last.date().isoformat(), excluded_partial_day=(last + timedelta(days=1)).date().isoformat(),
        horizons=list(HORIZONS), max_horizon=MAX_H, origins=origins,
        latest_origin=last.date().isoformat(),
        models={
            "seasonal_naive": "most recent observation-available value on the target weekday at or before the origin; fallback trailing mean",
            "trailing_mean": f"mean of observation-available days in the {PARAMS['trailing_mean_days']} calendar days ending at the origin",
            "gradient_boosting": "sklearn HistGradientBoostingRegressor(loss=absolute_error), direct multi-horizon (horizon is a feature), pooled over aggregate + selected products, target and level features divided by an origin-time scale",
        },
        gbm_grid=GBM_GRID, params=PARAMS,
        test_product_panel="products are selected once at the first test origin and kept for the later weekly test origins (selection uses data <= the first test origin only)",
        product_selection=(f"at each origin: stock codes sold on >= {PARAMS['selection_min_sold_share']:.0%} of observation-available days in the "
                           f"{PARAMS['selection_window_days']} days ending at the origin, ranked by units in that window, top {PARAMS['n_products']}"),
        availability_rule="target days with is_observation_available=false (no source records, quarantined rows, partial final day) are forecast but never scored; observed zero = available day without sales of that product",
        non_trading_rule=f"if a weekday had observation availability < {PARAMS['non_trading_weekday_rate']:.0%} in history at the origin, all models forecast 0 for it (flag expected_non_trading)",
        interval_method=("80%: prediction + scale * empirical 10%/90% quantiles of (actual - prediction)/scale of the same model, pooled over "
                         "earlier non-test origins with target date <= current origin, bucketed by scope (aggregate|product) and horizon (1-7|8-28); "
                         f"falls back to all horizons, then all scopes, if fewer than {PARAMS['min_interval_residuals']} residuals; lower bound clipped at 0. "
                         "The latest (operational) forecast may also use test residuals."),
        tuning="gradient_boosting config chosen by the promotion metric on validation origins only",
        promotion_metric="mean of validation WAPE over {aggregate, products} x {7, 28}; lowest wins, ties go to the simpler model (seasonal_naive, trailing_mean, gradient_boosting)",
        metric_rules="horizon H window = forecasts with h <= H from origins whose full H-day window ends <= last complete day; WAPE = sum|error|/sum actual, null with reason when sum actual is 0",
    )


# ---------------------------------------------------------------- data
def snapshot_paths(snapshot_id=None):
    snapshot_id = snapshot_id or read_json(DATA / "serving" / "full.json")["snapshot_id"]
    return snapshot_id, DATA / "serving" / "snapshots" / snapshot_id


def load_inputs(base):
    con = duckdb.connect(config=dict(memory_limit="512MB", threads=2))
    cal = con.sql(f"""select date, positive_units, is_observation_available, observation_status
        from read_parquet('{(base / 'forecast_inputs').as_posix()}/*.parquet') where series_id='{AGG}' order by date""").df()
    fact = f"read_parquet('{(base / 'fact_invoice_line').as_posix()}/*.parquet')"
    prod = con.sql(f"""select stock_code, date, sum(quantity)::double units from {fact}
        where classification='positive_merchandise' and quantity > 0 group by 1, 2""").df()
    labels = con.sql(f"""select stock_code, mode(description) as label from {fact}
        where classification='positive_merchandise' group by 1""").df()
    con.close()
    cal["date"] = pd.to_datetime(cal["date"])
    prod["date"] = pd.to_datetime(prod["date"])
    full = pd.date_range(cal.date.min(), cal.date.max(), freq="D")
    if len(full) != len(cal):
        raise ValueError("forecast_inputs is not a dense calendar")
    return cal, prod, dict(zip(labels.stock_code, labels.label.fillna("")))


# ---------------------------------------------------------------- series and features
def select_products(cal_hist, prod_hist, params=PARAMS):
    """Top-N products using only rows dated <= the history end."""
    end = cal_hist.date.max()
    start = end - timedelta(days=params["selection_window_days"] - 1)
    window = cal_hist[(cal_hist.date >= start) & cal_hist.is_observation_available]
    n_avail = len(window)
    rows = prod_hist[prod_hist.date.isin(window.date)]
    if not n_avail or rows.empty:
        return []
    g = rows.groupby("stock_code").agg(days=("date", "nunique"), units=("units", "sum")).reset_index()
    g = g[g.days >= params["selection_min_sold_share"] * n_avail]
    return g.sort_values(["units", "stock_code"], ascending=[False, True]).stock_code.head(params["n_products"]).tolist()


def build_series(cal_hist, prod_hist, codes):
    """Calendar-aligned arrays; NaN = unavailable (not zero), 0 = observed no sale."""
    avail = cal_hist.is_observation_available.to_numpy(bool)
    start = cal_hist.date.iloc[0]
    out = {AGG: np.where(avail, cal_hist.positive_units.astype(float).to_numpy(), np.nan)}
    first = {AGG: 0}
    for code in codes:
        rows = prod_hist[prod_hist.stock_code == code]
        arr = np.zeros(len(cal_hist))
        pos = (rows.date - start).dt.days.to_numpy()
        np.add.at(arr, pos, rows.units.to_numpy())
        arr[~avail] = np.nan
        out[code] = arr
        first[code] = int(pos.min()) if len(pos) else len(cal_hist)
    return out, first


def origin_features(y, dates, params=PARAMS):
    """Trailing-only features for every position o (value at o uses y[..o] only)."""
    s = pd.Series(y, dtype=float)
    scale = s.rolling(params["scale_window_days"], min_periods=7).mean().fillna(s.expanding().mean()).fillna(1.0).clip(lower=1.0)
    f = {f"lag{k}": s.shift(k) / scale for k in range(14)}
    m28 = s.rolling(params["trailing_mean_days"], min_periods=1).mean()
    f.update(mean7=s.rolling(7, min_periods=1).mean() / scale, mean28=m28 / scale,
             std28=s.rolling(28, min_periods=2).std() / scale,
             zero28=(s == 0).astype(float).where(s.notna()).rolling(28, min_periods=1).mean(),
             avail28=s.notna().astype(float).rolling(28, min_periods=1).mean(),
             log_scale=np.log(scale))
    dow = pd.DatetimeIndex(dates).dayofweek
    sw_last = np.column_stack([s.where(dow == w).ffill().to_numpy() for w in range(7)])
    sw_mean = np.column_stack([s.where(dow == w).rolling(56, min_periods=1).mean().to_numpy() for w in range(7)])
    frame = pd.DataFrame(f)
    frame["o_dow"] = dow
    return frame, scale.to_numpy(), m28.fillna(s.expanding().mean()).to_numpy(), sw_last, sw_mean


FEATURES = [f"lag{k}" for k in range(14)] + ["mean7", "mean28", "std28", "zero28", "avail28", "log_scale",
                                            "o_dow", "sw_last", "sw_mean", "h", "t_dow", "t_month", "t_dom", "is_aggregate"]


def design(feat, scale, sw_last, sw_mean, dates, pos, h, is_agg):
    pos = np.asarray(pos)
    tdates = pd.DatetimeIndex(dates[pos]) + pd.Timedelta(days=int(h))
    tdow = tdates.dayofweek.to_numpy()
    X = feat.iloc[pos].reset_index(drop=True)
    X["sw_last"] = sw_last[pos, tdow] / scale[pos]
    X["sw_mean"] = sw_mean[pos, tdow] / scale[pos]
    X["h"], X["t_dow"], X["t_month"], X["t_dom"] = h, tdow, tdates.month, tdates.day
    X["is_aggregate"] = float(is_agg)
    return X[FEATURES], tdates


def run_origin(cal, prod, origin, model_keys, gbm_grid=GBM_GRID, params=PARAMS, seed=SEED, codes=None):
    """Forecast h=1..28 for every series at one origin. History is truncated first,
    so nothing dated after the origin can influence selection, features or fits."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    origin = pd.Timestamp(origin)
    cal_hist = cal[cal.date <= origin].reset_index(drop=True)
    prod_hist = prod[prod.date <= origin]
    dates = cal_hist.date.to_numpy()
    P = len(cal_hist) - 1
    codes = select_products(cal_hist, prod_hist, params) if codes is None else list(codes)
    series, first = build_series(cal_hist, prod_hist, codes)
    dows = pd.DatetimeIndex(dates).dayofweek
    avail = cal_hist.is_observation_available.to_numpy(bool)
    weekday_rate = {w: float(avail[dows == w].mean()) if (dows == w).any() else 0.0 for w in range(7)}

    built = {sid: (y, *origin_features(y, dates, params)) for sid, y in series.items()}
    rows, origin_feats = [], {}
    for sid, (y, feat, scale, m28, sw_last, sw_mean) in built.items():
        for h in range(1, MAX_H + 1):
            X, td = design(feat, scale, sw_last, sw_mean, dates, [P], h, sid == AGG)
            origin_feats[(sid, h)] = X.iloc[0].to_numpy()
            tdow = td.dayofweek[0]
            sn = sw_last[P, tdow]
            base = dict(series_id=sid, h=h, target_date=td[0], scale=scale[P],
                        expected_non_trading=weekday_rate[tdow] < params["non_trading_weekday_rate"])
            fallback = m28[P] if np.isfinite(m28[P]) else 0.0
            if "seasonal_naive" in model_keys:
                rows.append(dict(base, model_key="seasonal_naive", prediction=sn if np.isfinite(sn) else fallback))
            if "trailing_mean" in model_keys:
                rows.append(dict(base, model_key="trailing_mean", prediction=fallback))
    gbm_keys = [k for k in model_keys if k.startswith("gbm:")]
    train_info = {}
    if gbm_keys:
        Xs, ys, tmax = [], [], None
        for sid, (y, feat, scale, m28, sw_last, sw_mean) in built.items():
            lo = max(params["scale_window_days"], first[sid] + (0 if sid == AGG else params["cold_start_days"]))
            for h in range(1, MAX_H + 1):
                pos = np.arange(lo, P - h + 1)
                if not len(pos):
                    continue
                target = y[pos + h]
                keep = np.isfinite(target)
                pos = pos[keep]
                if not len(pos):
                    continue
                X, td = design(feat, scale, sw_last, sw_mean, dates, pos, h, sid == AGG)
                Xs.append(X)
                ys.append(target[keep] / scale[pos])
                tmax = td.max() if tmax is None else max(tmax, td.max())
        X_train, y_train = pd.concat(Xs, ignore_index=True), np.concatenate(ys)
        train_info = dict(rows=int(len(y_train)), max_target_date=str(pd.Timestamp(tmax).date()))
        if tmax > origin:
            raise AssertionError("training target after origin")
        X_now = pd.DataFrame([origin_feats[(sid, h)] for sid in built for h in range(1, MAX_H + 1)], columns=FEATURES)
        for key in gbm_keys:
            model = HistGradientBoostingRegressor(loss="absolute_error", random_state=seed, early_stopping=False,
                                                  **gbm_grid[key.split(":", 1)[1]])
            model.fit(X_train, y_train)
            ratio = model.predict(X_now)
            i = 0
            for sid in built:
                for h in range(1, MAX_H + 1):
                    scale_p = built[sid][2][P]
                    td = pd.Timestamp(dates[P]) + pd.Timedelta(days=h)
                    rows.append(dict(series_id=sid, h=h, target_date=td, scale=scale_p, model_key=key,
                                     prediction=float(ratio[i]) * scale_p,
                                     expected_non_trading=weekday_rate[td.dayofweek] < params["non_trading_weekday_rate"]))
                    i += 1
    pred = pd.DataFrame(rows)
    pred["prediction"] = np.where(pred.expected_non_trading, 0.0, pred.prediction.clip(lower=0.0))
    pred["origin"] = origin
    return dict(pred=pred, codes=codes, features=origin_feats, train=train_info)


# ---------------------------------------------------------------- actuals, intervals, metrics
def attach_actuals(pred, cal, prod):
    status = cal.set_index("date")
    d = pred.target_date
    pred["available"] = d.map(status.is_observation_available).eq(True)
    pred["availability_status"] = d.map(status.observation_status).fillna("beyond_source_window")
    agg = d.map(status.positive_units).astype(float)
    key = prod.set_index(["stock_code", "date"]).units
    pairs = list(zip(pred.series_id, d))
    prod_actual = pd.Series([key.get(p, 0.0) for p in pairs], index=pred.index, dtype=float)
    pred["actual"] = np.where(pred.series_id == AGG, agg, prod_actual)
    pred.loc[~pred.available, "actual"] = np.nan
    pred["scope"] = np.where(pred.series_id == AGG, "aggregate", "products")
    return pred


def attach_intervals(pred, params=PARAMS):
    pred = pred.copy()
    pred["bucket"] = np.where(pred.h <= 7, "1-7", "8-28")
    pred["nres"] = (pred.actual - pred.prediction) / pred.scale
    pred["lower"], pred["upper"], pred["interval_method"], pred["interval_n"] = np.nan, np.nan, None, 0
    minimum = params["min_interval_residuals"]
    for (key, origin, role), idx in pred.groupby(["model_key", "origin", "role"]).groups.items():
        pool_roles = ("warmup", "validation", "test") if role == "latest" else ("warmup", "validation")
        pool = pred[(pred.model_key == key) & pred.role.isin(pool_roles) & (pred.origin < origin)
                    & (pred.target_date <= origin) & pred.nres.notna()]
        group = pred.loc[idx]
        for (scope, bucket), sub_idx in group.groupby(["scope", "bucket"]).groups.items():
            for method, cand in (("scope_horizon_bucket", pool[(pool.scope == scope) & (pool.bucket == bucket)]),
                                 ("scope_all_horizons", pool[pool.scope == scope]), ("all_pooled", pool)):
                if len(cand) >= minimum:
                    q10, q90 = np.quantile(cand.nres, [0.1, 0.9])
                    sub = pred.loc[sub_idx]
                    pred.loc[sub_idx, "lower"] = (sub.prediction + q10 * sub.scale).clip(lower=0)
                    pred.loc[sub_idx, "upper"] = (sub.prediction + q90 * sub.scale).clip(lower=0)
                    pred.loc[sub_idx, "interval_method"], pred.loc[sub_idx, "interval_n"] = method, len(cand)
                    break
    zero = pred.expected_non_trading
    pred.loc[zero, ["lower", "upper"]] = 0.0
    return pred.drop(columns=["nres"])


def wape(actual, prediction):
    actual, prediction = np.asarray(actual, float), np.asarray(prediction, float)
    if len(actual) == 0:
        return None, "no_observation_available_days"
    total = actual.sum()
    if total == 0:
        return None, "zero_actual_sum"
    return float(np.abs(actual - prediction).sum() / total), None


def score(df):
    df = df[df.available & df.actual.notna()]
    w, reason = wape(df.actual, df.prediction)
    iv = df[df.lower.notna()]
    inside = (iv.actual >= iv.lower) & (iv.actual <= iv.upper)
    return dict(n=int(len(df)), mae=float(np.abs(df.actual - df.prediction).mean()) if len(df) else None,
                wape=w, wape_null_reason=reason,
                coverage80=float(inside.mean()) if len(iv) else None,
                width80=float((iv.upper - iv.lower).mean()) if len(iv) else None, n_interval=int(len(iv)))


def window(df, H, last_complete):
    full = df.origin + pd.Timedelta(days=H) <= pd.Timestamp(last_complete)
    return df[full & (df.h <= H)]


def metric_table(pred, role, last_complete, per_series=False):
    out = []
    sub = pred[pred.role == role]
    for key, by_model in sub.groupby("model_key"):
        for H in HORIZONS:
            w = window(by_model, H, last_complete)
            groups = [("aggregate", w[w.scope == "aggregate"]), ("products", w[w.scope == "products"])]
            if per_series:
                groups += [(f"series:{sid}", g) for sid, g in w.groupby("series_id")]
            for scope, g in groups:
                if len(g):
                    out.append(dict(model=key, horizon=H, series_scope=scope, n_origins=int(g.origin.nunique()), **score(g)))
    return out


def promotion_score(rows, key):
    vals = [r["wape"] for r in rows if r["model"] == key and r["series_scope"] in ("aggregate", "products")]
    return float(np.mean(vals)) if len(vals) == 4 and all(v is not None for v in vals) else float("inf")


# ---------------------------------------------------------------- charts
def charts(pred, cfg, promoted_key, labels, val_rows, test_rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.titleweight": "bold", "figure.facecolor": "white",
                         "axes.facecolor": "white", "savefig.facecolor": "white"})
    blue, teal, red, grey = "#26547c", "#157f6b", "#ad3f48", "#6b7280"
    names = {"seasonal_naive": "Seasonal naive", "trailing_mean": "Trailing mean", promoted_key: display(promoted_key)}
    test0 = pd.Timestamp(cfg["origins"][-4]["origin"])
    t = pred[(pred.role == "test") & (pred.origin == test0)]
    series = [AGG] + [s for s in t.series_id.unique() if s != AGG]
    saved = []

    def finish(fig, name, footer, top=1.0):
        fig.text(.01, .012, footer, fontsize=8, color="#4b5563", va="bottom")
        fig.tight_layout(rect=(0, .03 if top < 1 else .05, 1, top))
        for ext in ("svg", "png"):
            fig.savefig(OUT / f"{name}.{ext}", dpi=140)
        saved.append(f"analysis/forecast/{name}.png")
        plt.close(fig)

    def panel(ax, sid, legend):
        main = t[(t.series_id == sid) & (t.model_key == promoted_key)].sort_values("target_date")
        sn = t[(t.series_id == sid) & (t.model_key == "seasonal_naive")].sort_values("target_date")
        ax.fill_between(main.target_date, main.lower, main.upper, color=blue, alpha=.15, lw=0, label=f"{names[promoted_key]} 80% interval")
        ax.plot(main.target_date, main.prediction, color=blue, lw=2, label=f"{names[promoted_key]} forecast")
        if promoted_key != "seasonal_naive":
            ax.plot(sn.target_date, sn.prediction, color=grey, lw=1.2, ls="--", label="Seasonal naive")
        av = main[main.available]
        ax.plot(av.target_date, av.actual, color=red, lw=0, marker="o", ms=4, label="Observed sales (available days)")
        un = main[~main.available]
        for d in un.target_date:
            ax.axvspan(d - pd.Timedelta(hours=12), d + pd.Timedelta(hours=12), color="#e5e7eb", lw=0, zorder=0)
        ax.set_ylim(bottom=0)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.tick_params(axis="x", labelsize=8)
        ax.xaxis.set_major_locator(matplotlib.dates.DayLocator(interval=7))
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%a %d %b"))
        if legend:
            ax.legend(frameon=False, fontsize=8, loc="upper left")

    fig, ax = plt.subplots(figsize=(10, 4.6))
    panel(ax, AGG, True)
    ax.set_title(f"Aggregate observed units: untouched test, origin {test0.date()} (h = 1-28)", loc="left")
    ax.set_ylabel("Units per day")
    finish(fig, "test_aggregate", "Grey bands: days without observation-available data (not scored). Observed sales are not unconstrained demand.")

    prods = series[1:]
    if prods:
        fig, axes = plt.subplots(len(prods), 1, figsize=(10, 2.3 * len(prods) + .6), sharex=True)
        axes = np.atleast_1d(axes)
        for ax, sid in zip(axes, prods):
            panel(ax, sid, False)
            ax.set_title(f"{sid}  {labels.get(sid, '')[:48]}", loc="left", fontsize=9.5)
            ax.set_ylabel("Units")
        handles, names_ = axes[0].get_legend_handles_labels()
        fig.legend(handles, names_, loc="upper center", ncol=4, frameon=False, fontsize=8.5)
        finish(fig, "test_products", f"Products selected at the test origin using data <= {test0.date()}. Grey bands: unavailable days.", top=.965)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharey=True)
    keys = ["seasonal_naive", "trailing_mean", promoted_key if promoted_key.startswith("gbm") else best_gbm_key(val_rows)]
    colors = [grey, teal, blue]
    for ax, scope in zip(axes, ("aggregate", "products")):
        x = np.arange(4)
        labels_x = ["Validation 7d", "Validation 28d", "Test 7d", "Test 28d"]
        for j, (key, color) in enumerate(zip(keys, colors)):
            vals = []
            for rows in (val_rows, test_rows):
                for H in HORIZONS:
                    r = next((r for r in rows if r["model"] == key and r["horizon"] == H and r["series_scope"] == scope), None)
                    vals.append(r["wape"] if r and r["wape"] is not None else np.nan)
            ax.bar(x + (j - 1) * .26, vals, .24, color=color, label=display(key))
        ax.set_xticks(x, labels_x, fontsize=8.5)
        ax.set_title(f"WAPE, {scope}", loc="left")
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1, decimals=0))
    axes[0].legend(frameon=False, fontsize=8.5)
    finish(fig, "error_by_model", "WAPE = sum |error| / sum observed units over observation-available days. Lower is better.")
    return saved


def display(key):
    return {"seasonal_naive": "Seasonal naive", "trailing_mean": "Trailing mean"}.get(key, f"Gradient boosting ({key.split(':')[-1]})")


def best_gbm_key(rows):
    keys = sorted({r["model"] for r in rows if r["model"].startswith("gbm:")})
    return min(keys, key=lambda k: promotion_score(rows, k))


# ---------------------------------------------------------------- main
def clean(v):
    if isinstance(v, float) and not np.isfinite(v):
        return None
    if isinstance(v, (np.floating,)):
        return clean(float(v))
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat()
    return v


def rows_json(df, cols):
    return [{c: clean(r[c]) for c in cols} for r in df[cols].to_dict("records")]


def model_name(key):
    return "gradient_boosting" if key.startswith("gbm:") else key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot")
    parser.add_argument("--pytest", action="store_true", help="also run tests/test_forecast.py and record the result")
    args = parser.parse_args(argv)
    snapshot_id, base = snapshot_paths(args.snapshot)
    OUT.mkdir(parents=True, exist_ok=True)
    timings, t0 = {}, time.perf_counter()
    with run_record("forecast", snapshot_id=snapshot_id) as record:
        cal, prod, labels = load_inputs(base)
        avail_days = cal[cal.is_observation_available]
        last_complete = avail_days.date.max().date().isoformat()
        partial = cal[cal.observation_status == "partial_final_source_day"].date
        if len(partial) and partial.max().date().isoformat() <= last_complete:
            raise ValueError("partial final day must be after the last complete day")
        cfg = build_config(snapshot_id, last_complete)
        atomic_json(OUT / "config.json", cfg)
        cfg_hash = sha256(OUT / "config.json")
        timings["load_and_config_s"] = round(time.perf_counter() - t0, 2)

        # Phase 1: warm-up + validation, all models and all gbm configs.
        t1 = time.perf_counter()
        keys = ["seasonal_naive", "trailing_mean"] + [f"gbm:{k}" for k in GBM_GRID]
        parts, selections, train_log = [], {}, {}
        for o in [o for o in cfg["origins"] if o["role"] in ("warmup", "validation")]:
            res = run_origin(cal, prod, o["origin"], keys)
            parts.append(res["pred"].assign(role=o["role"]))
            selections[o["origin"]], train_log[o["origin"]] = res["codes"], res["train"]
            print(f"{o['role']:10s} {o['origin']} products={res['codes']} train_rows={res['train'].get('rows')}", flush=True)
        timings["validation_s"] = round(time.perf_counter() - t1, 2)

        pre = attach_intervals(attach_actuals(pd.concat(parts, ignore_index=True), cal, prod))
        val_rows = metric_table(pre, "validation", last_complete)
        tuning = {k: promotion_score(val_rows, k) for k in keys}
        gbm_key = min([k for k in keys if k.startswith("gbm:")], key=lambda k: tuning[k])
        candidates = ["seasonal_naive", "trailing_mean", gbm_key]
        promoted_key = min(candidates, key=lambda k: (tuning[k], candidates.index(k)))
        promoted = model_name(promoted_key)

        # Phase 2: untouched test, frozen choices, evaluated once.
        t2 = time.perf_counter()
        test_keys, frozen = candidates, None
        for o in [o for o in cfg["origins"] if o["role"] == "test"]:
            # the product panel is selected once at the first test origin (data <= that origin) and kept
            res = run_origin(cal, prod, o["origin"], test_keys, codes=frozen)
            frozen = res["codes"] if frozen is None else frozen
            parts.append(res["pred"].assign(role="test"))
            selections[o["origin"]], train_log[o["origin"]] = res["codes"], res["train"]
            print(f"test       {o['origin']} products={res['codes']}", flush=True)
        timings["test_s"] = round(time.perf_counter() - t2, 2)

        # Phase 3: latest operational forecast from the last complete day.
        t3 = time.perf_counter()
        res = run_origin(cal, prod, cfg["latest_origin"], [promoted_key])
        parts.append(res["pred"].assign(role="latest"))
        selections[cfg["latest_origin"]] = res["codes"]
        timings["latest_s"] = round(time.perf_counter() - t3, 2)

        pred = attach_intervals(attach_actuals(pd.concat(parts, ignore_index=True), cal, prod))
        pred["model"] = pred.model_key.map(model_name)
        pred["config"] = pred.model_key.map(lambda k: k.split(":")[1] if ":" in k else "")
        cols = ["role", "origin", "h", "target_date", "series_id", "scope", "model", "config", "prediction", "lower", "upper",
                "actual", "available", "availability_status", "expected_non_trading", "scale", "interval_method", "interval_n"]
        pred = pred.sort_values(["role", "origin", "model_key", "series_id", "h"])
        pred[cols].to_csv(OUT / "predictions.csv", index=False, date_format="%Y-%m-%d", float_format="%.4f")

        val_rows = metric_table(pred, "validation", last_complete, per_series=True)
        test_rows = metric_table(pred, "test", last_complete, per_series=True)
        for rows in (val_rows, test_rows):
            for r in rows:
                r["model_key"], r["model"] = r["model"], model_name(r["model"])
        val_cmp = [dict(r, model=r["model_key"]) for r in val_rows]
        test_cmp = [dict(r, model=r["model_key"]) for r in test_rows]

        def improvement(rows, key):
            out = []
            for scope in ("aggregate", "products"):
                for H in HORIZONS:
                    a = next(r for r in rows if r["model"] == key and r["horizon"] == H and r["series_scope"] == scope)
                    b = next(r for r in rows if r["model"] == "seasonal_naive" and r["horizon"] == H and r["series_scope"] == scope)
                    rel = None if a["wape"] is None or not b["wape"] else (b["wape"] - a["wape"]) / b["wape"]
                    out.append(dict(series_scope=scope, horizon=H, model=model_name(key), wape=a["wape"],
                                    seasonal_naive_wape=b["wape"], wape_reduction_vs_seasonal_naive=rel))
            return out

        imp = dict(validation=improvement(val_cmp, promoted_key), test=improvement(test_cmp, promoted_key),
                   validation_gbm=improvement(val_cmp, gbm_key), test_gbm=improvement(test_cmp, gbm_key))
        order = sorted(candidates, key=lambda k: tuning[k])
        reason = (f"{display(promoted_key)} has the lowest validation promotion metric "
                  f"(mean WAPE over aggregate/products x 7/28 days) = {tuning[promoted_key]:.4f}; "
                  + "; ".join(f"{display(k)} = {tuning[k]:.4f}" for k in order if k != promoted_key)
                  + ". Chosen on validation origins only; the test period did not influence the choice.")
        if promoted_key == "seasonal_naive":
            reason += " A simple baseline won, so it is promoted: weekly seasonality dominates and the tabular model did not beat it out of sample."
        metrics = dict(state="passed", snapshot_id=snapshot_id, config_sha256=cfg_hash, promoted_model=promoted,
                       promoted_model_key=promoted_key, promotion_reason=reason, tuning_scores=tuning,
                       validation=val_rows, test=test_rows, improvement=imp, selections=selections, training=train_log)
        atomic_json(OUT / "metrics.json", metrics)

        saved = charts(pred, cfg, promoted_key, labels, val_cmp, test_cmp)

        test0 = cfg["origins"][-4]["origin"]
        tf = pred[(pred.role == "test") & (pred.origin == pd.Timestamp(test0)) & pred.model_key.isin({promoted_key, "seasonal_naive"})]
        lt = pred[pred.role == "latest"]
        fc_cols = ["target_date", "actual", "available", "prediction", "lower", "upper", "model"]
        series_ids = list(dict.fromkeys([AGG] + selections[test0] + selections[cfg["latest_origin"]]))
        compact = lambda rows: [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]
        summary_rows = lambda rows: [dict(model=r["model"], model_key=r["model_key"], horizon=r["horizon"], series_scope=r["series_scope"],
                                          mae=r["mae"], wape=r["wape"], wape_null_reason=r["wape_null_reason"],
                                          coverage80=r["coverage80"], width80=r["width80"], n=r["n"])
                                     for r in rows if r["series_scope"] in ("aggregate", "products")
                                     and (r["model_key"] in candidates)]
        serving = dict(
            state="passed", snapshot_id=snapshot_id, generated_at=now(),
            config=dict(sha256=cfg_hash, test_forecast_origin=test0, latest_forecast_origin=cfg["latest_origin"], horizons=cfg["horizons"], origins=cfg["origins"], last_complete_day=last_complete,
                        excluded_partial_day=cfg["excluded_partial_day"], interval_method=cfg["interval_method"],
                        product_selection=cfg["product_selection"], promotion_metric=cfg["promotion_metric"]),
            promoted_model=promoted, promotion_reason=reason,
            models=[dict(model=model_name(k), model_key=k, description=cfg["models"][model_name(k)],
                         validation_promotion_score=tuning[k]) for k in candidates],
            metrics=dict(validation=summary_rows(val_rows), test=summary_rows(test_rows)),
            improvement=dict(validation=imp["validation"], test=imp["test"]),
            series=[dict(series_id=s, label="All merchandise (aggregate)" if s == AGG else labels.get(s, s),
                         in_test=s in selections[test0] or s == AGG, in_latest=s in selections[cfg["latest_origin"]] or s == AGG)
                    for s in series_ids],
            test_forecast={**{sid: compact(rows_json(g.sort_values(["model", "target_date"]).rename(columns={"target_date": "date"}),
                                                                       ["date", "actual", "available", "prediction", "lower", "upper", "model"]))
                                                for sid, g in tf.groupby("series_id")}},
            latest_forecast={**{sid: compact(rows_json(g.sort_values("target_date").rename(columns={"target_date": "date"}),
                                                                                        ["date", "prediction", "lower", "upper", "model", "expected_non_trading"]))
                                                                 for sid, g in lt.groupby("series_id")}},
            charts=saved,
            limitations=[
                "Observed sales are not unconstrained demand: no stock-out, inventory or lost-sales data exist in the source.",
                "No prices, promotions, customer aggregates or other exogenous drivers are used.",
                "Days without source records (including almost every Saturday and holiday gaps) are unavailable, not zero; they are forecast but never scored.",
                f"The final source day {cfg['excluded_partial_day']} is partial and is excluded as an actual.",
                "Only about two years of history: annual seasonality (the November peak) is seen roughly once before the test period.",
                "Holiday closures (e.g. late December) are not modelled; the latest forecast will over-forecast days the business is closed.",
                "Product series are intermittent and contain bulk orders, so product-level WAPE is high and intervals are wide.",
                "Gradient-boosting configuration was chosen among 3 candidates on the same validation folds, which slightly flatters its validation score; the test period is the unbiased check.",
            ],
        )
        atomic_json(OUT / "serving.json", serving)
        timings["total_s"] = round(time.perf_counter() - t0, 2)

        tests = None
        if args.pytest:
            proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_forecast.py"], cwd=ROOT,
                                  capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
            tests = dict(command="python -m pytest -q tests/test_forecast.py", returncode=proc.returncode,
                         summary=(proc.stdout.strip().splitlines() or [""])[-1])
        import matplotlib, sklearn
        evidence = dict(
            stage="forecast", state="passed" if tests is None or tests["returncode"] == 0 else "failed",
            command="PYTHONPATH=src .venv/Scripts/python.exe -m meridian.forecast" + (" --pytest" if args.pytest else ""),
            run_id=record["run_id"], git_sha=record.get("git_sha"), source_tree_sha256=record["source_tree_sha256"],
            seed=SEED, started_at=record["started_at"], durations_s=timings,
            software=dict(python=platform.python_version(), duckdb=duckdb.__version__, pandas=pd.__version__, numpy=np.__version__,
                          scikit_learn=sklearn.__version__, matplotlib=matplotlib.__version__),
            input=dict(snapshot_id=snapshot_id, manifest_sha256=sha256(base / "manifest.json"),
                       source_sha256=read_json(base / "manifest.json").get("source_sha256")),
            config_path="analysis/forecast/config.json", config_sha256=cfg_hash,
            outputs={p: sha256(OUT / p) for p in ("config.json", "predictions.csv", "metrics.json", "serving.json")},
            prediction_rows=int(len(pred)), promoted_model=promoted, promoted_model_key=promoted_key,
            tuning_scores=tuning, test_headline=summary_rows(test_rows), tests=tests,
        )
        atomic_json(ROOT / "evidence" / "forecast-run.json", evidence)
        record.update(promoted_model=promoted, config_sha256=cfg_hash)
    if tests and tests["returncode"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
