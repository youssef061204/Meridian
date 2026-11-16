"""SIMULATED inventory mechanics on hand-checkable inputs."""
import numpy as np
import pandas as pd
import pytest

from meridian import inventory as inv


def test_simulate_hand_calculated_lost_sales_and_holding():
    # start 5 units, demand 3/day for 4 days, one review at t=0 ordering up to 10 with lead 2
    r = inv.simulate(np.array([3.0, 3, 3, 3]), {0}, {0: 10.0}, start_stock=5, lead=2, holding=1.0, shortage=10.0)
    # order q=5 arrives start of day 2. on-hand end: d1 2, d2 4, d3 1, d4 0 (1 sold, 2 lost)
    assert r["ordered_units"] == 5 and r["lost_units"] == 2 and r["sold_units"] == 10
    assert r["holding_cost"] == pytest.approx(7.0) and r["shortage_cost"] == pytest.approx(20.0)
    assert r["fill_rate"] == pytest.approx(10 / 12)


def test_inventory_position_includes_pipeline():
    r = inv.simulate(np.zeros(10), {0, 3}, {0: 10.0, 3: 10.0}, start_stock=0, lead=5, holding=0.0, shortage=1.0)
    assert r["ordered_units"] == 10          # second review sees 10 in transit and orders nothing


def make_episode(policy_forecast):
    start = pd.Timestamp("2021-01-07")
    days = pd.date_range(start + pd.Timedelta(days=1), periods=28)
    idx = pd.date_range(start + pd.Timedelta(days=1), periods=60)
    origins = [start + pd.Timedelta(days=7 * k) for k in range(4)]
    return dict(series_id="X", start=start, days=days, demand=np.full(28, 4.0), mu0=4.0, sigma0=0.0,
                review_index=[0, 7, 14, 21],
                forecasts={"m": {o: pd.Series(policy_forecast, index=idx) for o in origins}})


def test_policies_share_identical_assumptions_and_perfect_forecast_has_no_cost_disadvantage():
    ep = make_episode(4.0)
    fixed = inv.order_up_to(ep, "fixed", 3, 7, 0.84)
    model = inv.order_up_to(ep, "m", 3, 7, 0.84)
    assert fixed == model                     # mu0 equals the (perfect) forecast -> identical levels
    df, _ = inv.run_grid([ep], ["fixed", "m"], grid=dict(lead_time_days=[3], shortage_to_holding_ratio=[10], safety_z=[0.84]))
    a, b = df[df.policy == "fixed"].iloc[0], df[df.policy == "m"].iloc[0]
    assert a.total_cost == b.total_cost and a.demand_units == b.demand_units == 112


def test_order_up_to_refuses_forecast_that_does_not_cover_protection_window():
    ep = make_episode(4.0)
    short = {o: s.iloc[:5] for o, s in ep["forecasts"]["m"].items()}
    ep["forecasts"]["m"] = short
    with pytest.raises(ValueError):
        inv.order_up_to(ep, "m", 7, 7, 0.0)


def test_history_stats_count_unavailable_days_as_zero_for_rate_only():
    h = np.array([np.nan] * 26 + [10.0, 20.0])
    s = inv.history_stats(h, None)
    assert s["mu0"] == pytest.approx(30 / 28) and s["sigma0"] == pytest.approx(np.std([10, 20], ddof=1))


def test_outputs_are_labelled_simulated():
    assert "SIMULATED" in inv.LABEL and "not client outcomes" in inv.LABEL
    assert all(v.get("status") == "ASSUMED" for v in inv.ASSUMPTIONS.values() if isinstance(v, dict))
    assert "GBP" in inv.ASSUMPTIONS["cost_unit"] and "not" in inv.ASSUMPTIONS["cost_unit"]
