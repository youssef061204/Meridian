"""Forecast protocol tests on synthetic data (no snapshot required)."""
import numpy as np
import pandas as pd
import pytest

from meridian import forecast as fc

TINY_GRID = {"t": dict(learning_rate=0.1, max_iter=15, max_leaf_nodes=7, min_samples_leaf=20, l2_regularization=0.0)}
KEYS = ["seasonal_naive", "trailing_mean", "gbm:t"]


def synthetic(days=260, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=days, freq="D")
    avail = dates.dayofweek != 5                      # no Saturday records, like the source
    avail[100:104] = False                            # a holiday gap
    rows = []
    for code, level in (("A", 40), ("B", 25), ("C", 10)):
        for d, ok in zip(dates, avail):
            q = rng.poisson(level * (1.5 if d.dayofweek == 3 else 1.0))
            if ok and q > 0:
                rows.append((code, d, float(q)))
    prod = pd.DataFrame(rows, columns=["stock_code", "date", "units"])
    total = prod.groupby("date").units.sum().reindex(dates)
    cal = pd.DataFrame(dict(date=dates, positive_units=np.where(avail, total.fillna(0), np.nan),
                            is_observation_available=avail,
                            observation_status=np.where(avail, "observed", "unavailable_no_source_records")))
    return cal, prod


def run(cal, prod, origin):
    params = dict(fc.PARAMS, n_products=2, selection_window_days=60)
    return fc.run_origin(cal, prod, origin, KEYS, gbm_grid=TINY_GRID, params=params)


def test_future_perturbation_does_not_change_origin_forecasts_or_features():
    cal, prod = synthetic()
    origin = cal.date[200]
    base = run(cal, prod, origin)

    cal2, prod2 = cal.copy(), prod.copy()
    after = cal2.date > origin
    cal2.loc[after, "positive_units"] = cal2.loc[after, "positive_units"] * 50 + 999
    cal2.loc[after, "is_observation_available"] = ~cal2.loc[after, "is_observation_available"]
    late = prod2.date > origin
    prod2.loc[late, "units"] *= 100                   # would flip product ranking if leaked
    prod2 = pd.concat([prod2, pd.DataFrame(dict(stock_code="Z", date=cal.date[after], units=1e6))])
    pert = run(cal2, prod2, origin)

    assert pert["codes"] == base["codes"]
    assert base["train"]["max_target_date"] <= str(origin.date())
    for key, value in base["features"].items():
        np.testing.assert_array_equal(value, pert["features"][key])
    a = base["pred"].sort_values(["model_key", "series_id", "h"]).reset_index(drop=True)
    b = pert["pred"].sort_values(["model_key", "series_id", "h"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_past_perturbation_does_change_forecasts():
    """Control: the leakage test is sensitive (history really feeds the models)."""
    cal, prod = synthetic()
    origin = cal.date[200]
    base = run(cal, prod, origin)
    cal2 = cal.copy()
    before = (cal2.date > origin - pd.Timedelta(days=14)) & (cal2.date <= origin)
    cal2.loc[before, "positive_units"] *= 3
    pert = run(cal2, prod, origin)
    a = base["pred"][base["pred"].series_id == fc.AGG].sort_values(["model_key", "h"]).prediction.to_numpy()
    b = pert["pred"][pert["pred"].series_id == fc.AGG].sort_values(["model_key", "h"]).prediction.to_numpy()
    assert not np.allclose(a, b)


def test_seasonal_naive_uses_last_available_same_weekday_and_skips_gaps():
    cal, prod = synthetic(days=120)
    origin = cal.date[110]
    res = run(cal, prod, origin)
    p = res["pred"]
    sn = p[(p.model_key == "seasonal_naive") & (p.series_id == fc.AGG)].set_index("h")
    hist = cal[(cal.date <= origin) & cal.is_observation_available]
    for h in range(1, 29):
        target = origin + pd.Timedelta(days=h)
        if target.dayofweek == 5:
            assert sn.loc[h, "prediction"] == 0 and sn.loc[h, "expected_non_trading"]
            continue
        same = hist[hist.date.dt.dayofweek == target.dayofweek]
        assert sn.loc[h, "prediction"] == pytest.approx(same.positive_units.iloc[-1])
    # horizons 7, 14, 21, 28 share the same weekday and therefore the same value (no future actuals used)
    assert len({sn.loc[h, "prediction"] for h in (7, 14, 21, 28)}) == 1


def test_trailing_mean_ignores_unavailable_days():
    cal, prod = synthetic(days=120)
    origin = cal.date[110]
    p = run(cal, prod, origin)["pred"]
    tm = p[(p.model_key == "trailing_mean") & (p.series_id == fc.AGG) & ~p.expected_non_trading].prediction.unique()
    window = cal[(cal.date > origin - pd.Timedelta(days=28)) & (cal.date <= origin) & cal.is_observation_available]
    assert tm == pytest.approx([window.positive_units.mean()])


def test_wape_zero_denominator_and_unavailable_rules():
    assert fc.wape([0, 0], [1, 2]) == (None, "zero_actual_sum")
    assert fc.wape([], []) == (None, "no_observation_available_days")
    value, reason = fc.wape([10, 0, 30], [12, 1, 25])
    assert reason is None and value == pytest.approx((2 + 1 + 5) / 40)
    df = pd.DataFrame(dict(actual=[10.0, np.nan, 0.0], prediction=[8.0, 500.0, 1.0], available=[True, False, True],
                           lower=[5.0, 0.0, 0.0], upper=[9.0, 1.0, 2.0]))
    s = fc.score(df)
    assert s["n"] == 2 and s["mae"] == pytest.approx(1.5) and s["wape"] == pytest.approx(0.3)
    assert s["coverage80"] == pytest.approx(0.5) and s["width80"] == pytest.approx(3.0)
    zero = fc.score(df.iloc[[2]])
    assert zero["wape"] is None and zero["wape_null_reason"] == "zero_actual_sum" and zero["mae"] == 1.0


def test_intervals_use_only_earlier_origins_with_past_targets():
    cal, prod = synthetic()
    parts = []
    for i, origin in enumerate(cal.date[[120, 150, 180, 210]]):
        r = run(cal, prod, origin)["pred"].assign(role="warmup" if i < 3 else "test")
        parts.append(r)
    pred = fc.attach_actuals(pd.concat(parts, ignore_index=True), cal, prod)
    out = fc.attach_intervals(pred, dict(fc.PARAMS, min_interval_residuals=5))
    first = out[(out.origin == cal.date[120]) & ~out.expected_non_trading]
    assert first.lower.isna().all()                    # no earlier residuals exist
    last = out[(out.origin == cal.date[210]) & ~out.expected_non_trading]
    assert last.lower.notna().all() and (last.lower <= last.upper).all()
    # perturbing test-period actuals must not move the test intervals (pool excludes them)
    pred2 = pred.copy()
    pred2.loc[pred2.role == "test", "actual"] *= 7
    out2 = fc.attach_intervals(pred2, dict(fc.PARAMS, min_interval_residuals=5))
    np.testing.assert_allclose(out.lower.fillna(-1), out2.lower.fillna(-1))


def test_config_origins_are_chronological_and_test_is_last():
    cfg = fc.build_config("x", "2011-12-08")
    roles = [o["role"] for o in cfg["origins"]]
    dates = [o["origin"] for o in cfg["origins"]]
    assert dates == sorted(dates) and roles.count("validation") >= 4
    val_end = max(pd.Timestamp(o["origin"]) for o in cfg["origins"] if o["role"] == "validation") + pd.Timedelta(days=28)
    assert val_end <= pd.Timestamp(min(o["origin"] for o in cfg["origins"] if o["role"] == "test"))
    assert cfg["origins"][-4]["origin"] == "2011-11-10" and cfg["excluded_partial_day"] == "2011-12-09"
