"""Hand-checkable oracles for meridian.insights on a tiny synthetic fact table."""
from datetime import date, datetime, timedelta

import duckdb
import pytest

from meridian import insights as ins


def fact(rows):
    con = duckdb.connect()
    con.execute("""CREATE TABLE fact_invoice_line (invoice VARCHAR, stock_code VARCHAR, description VARCHAR, quantity BIGINT, unit_price DECIMAL(18,4),
        line_amount DECIMAL(20,2), event_time TIMESTAMP, date DATE, customer_id VARCHAR, country VARCHAR, classification VARCHAR)""")
    con.executemany("INSERT INTO fact_invoice_line VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (inv, code, f"Item {code}", qty, price, round(qty * price, 2), ts, ts.date(), cust, "United Kingdom",
         "positive_merchandise" if qty > 0 else "negative_adjustment") for inv, code, qty, price, ts, cust in rows])
    ins.prepare(con)
    return con


def test_market_basket_lift_matches_hand_calculation():
    t = datetime(2011, 1, 3, 10)
    # 10 invoices: A+B together in 4, A alone in 1, B alone in 1, C alone in 4.
    baskets = [["A", "B"]] * 4 + [["A"], ["B"]] + [["C"]] * 4
    rows = [(f"I{i}", code, 1, 1.0, t, "c1") for i, b in enumerate(baskets) for code in b]
    out = ins.market_basket(fact(rows), min_support=0.1)
    rule = next(r for r in out["rules"] if r["lhs"] == "A" and r["rhs"] == "B")
    assert out["invoices"] == 10
    assert rule["support"] == pytest.approx(0.4)
    assert rule["confidence"] == pytest.approx(4 / 5)
    assert rule["lift"] == pytest.approx((4 / 5) / (5 / 10))
    assert not any("C" in (r["lhs"], r["rhs"]) for r in out["rules"])


def test_anomaly_flags_only_the_planted_spike():
    rows, start = [], datetime(2010, 1, 4, 9)   # a Monday
    for d in range(12 * 7):
        day = start + timedelta(days=d)
        if day.isoweekday() == 6:
            continue
        amount = 1000 + (d % 5) * 10
        if d == 70:
            amount = 9000
        rows.append((f"I{d}", "A", 1, amount, day, "c1"))
    out = ins.anomalies(fact(rows))
    assert [a["date"] for a in out["anomalies"]] == [str((start + timedelta(days=70)).date())]
    assert out["anomalies"][0]["direction"] == "spike"


def test_return_features_never_see_rows_after_the_cutoff():
    cutoff = date(2011, 6, 9)
    before, after = datetime(2011, 5, 1, 10), datetime(2011, 7, 1, 10)
    rows = [("I1", "A", 1, 10.0, before, "c1"), ("I2", "A", 100, 10.0, after, "c1"), ("I3", "A", 1, 5.0, before, "c2")]
    frame = {r["customer_id"]: r for r in ins.feature_frame(fact(rows), cutoff)}
    assert frame["c1"]["frequency"] == 1 and frame["c1"]["monetary_gbp"] == pytest.approx(10.0)
    assert frame["c1"]["recency_days"] == (cutoff - before.date()).days
    assert frame["c1"]["returned"] == 1 and frame["c2"]["returned"] == 0
