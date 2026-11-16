"""API behavior against isolated, hand-calculated serving snapshots.

These tests do not run Spark or claim pipeline integration. They exercise the
actual HTTP handlers, DuckDB queries, filters, and source evidence responses.
"""

from datetime import date, datetime
from decimal import Decimal
import hashlib
import json

from fastapi.testclient import TestClient
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from meridian import api


MODEL_NAMES = ("silver.sql", "daily_sales.sql")


@pytest.fixture
def client(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    working_sql = project_root / "sql"
    working_sql.mkdir(parents=True)
    for name in MODEL_NAMES:
        (working_sql / name).write_bytes((api.ROOT / "sql" / name).read_bytes())
    data = project_root / "data"
    runtime = project_root / "runtime"
    monkeypatch.setattr(api, "ROOT", project_root)
    monkeypatch.setattr(api, "DATA", data)
    monkeypatch.setattr(api, "RUNTIME", runtime)
    with TestClient(api.app) as test_client:
        yield test_client, data, runtime


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def published(client):
    test_client, data, runtime = client
    folder = data / "serving" / "snapshots" / "api-fixture-snapshot"
    source_sha = "a" * 64
    # Values below are hand-calculated independently of production SQL.
    specifications = [
        (2, "10001", "A001", 2, "10.00", "20.00", "12345", "United Kingdom", "2009-12-01"),
        (3, "10001", "A001", 2, "10.00", "20.00", "12345", "United Kingdom", "2009-12-01"),
        (4, "10002", "A002", 3, "2.50", "7.50", None, "United Kingdom", "2009-12-01"),
        (5, "C10003", "A001", -1, "10.00", "-10.00", "12345", "United Kingdom", "2009-12-02"),
        (6, "10004", "A003", -2, "1.25", "-2.50", "12346", "France", "2009-12-02"),
        (7, "10005", "A004", 4, "0.00", "0.00", None, "United Kingdom", "2009-12-02"),
        (14, "10012", "A009", 3, "0.335", "1.01", "12347", "France", "2009-12-03"),
        (15, "C10013", "A009", -3, "0.335", "-1.01", "12347", "France", "2009-12-03"),
    ]
    records = []
    for source_row, invoice, stock, qty, price, amount, customer, country, day in specifications:
        records.append({
            "line_id": hashlib.sha256(f"{source_sha}|Fixture|{source_row}".encode()).hexdigest(),
            "invoice": invoice, "stock_code": stock, "description": f"Fixture {stock}",
            "quantity": qty, "unit_price": Decimal(price), "line_amount": Decimal(amount),
            "customer_id": customer, "country": country, "date": date.fromisoformat(day),
            "event_time": datetime.fromisoformat(day + "T09:00:00"),
            "source_sha256": source_sha, "source_sheet": "Fixture", "source_row": source_row,
            "classification": "negative_adjustment" if qty < 0 else "positive_merchandise",
        })
    fact = pa.Table.from_pylist(records)
    table_data = {
        "fact_invoice_line": fact,
        "daily_sales": pa.Table.from_pylist([
            {"date": date(2009, 12, 1), "net_recorded_gbp": Decimal("47.50")},
            {"date": date(2009, 12, 2), "net_recorded_gbp": Decimal("-12.50")},
            {"date": date(2009, 12, 3), "net_recorded_gbp": Decimal("0.00")},
        ]),
        "product_performance": pa.Table.from_pylist([
            {"stock_code": "A001", "net_recorded_gbp": Decimal("30.00")},
        ]),
        "country_performance": pa.Table.from_pylist([
            {"country": "United Kingdom", "net_recorded_gbp": Decimal("37.50")},
            {"country": "France", "net_recorded_gbp": Decimal("-2.50")},
        ]),
    }
    for name, table in table_data.items():
        path = folder / name / "part-00000.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
    models = folder / "models"
    models.mkdir()
    sql_hashes = {}
    for name in MODEL_NAMES:
        content = (api.ROOT / "sql" / name).read_bytes()
        (models / name).write_bytes(content)
        sql_hashes[name] = hashlib.sha256(content).hexdigest()
    manifest = {
        "id": "api-fixture-snapshot", "published_at": "2026-11-16T19:00:00Z",
        "mode": "synthetic", "profile": "fixture", "source_sha256": source_sha,
        "source_rows": 16, "accepted_rows": 8, "quarantined_rows": 7, "excluded_rows": 1,
        "repeat_deliveries": 2, "received_deliveries": 18,
        "date_min": "2009-12-01", "date_max": "2009-12-03",
        "delta_versions": {"bronze": 0, "silver": 0, "fact_invoice_line": 0},
        "metrics": {"gross_positive_gbp": "48.51", "negative_adjustments_gbp": "-13.51",
                    "net_recorded_gbp": "35.00", "positive_units": 14, "known_customer_rows": 6},
        "validation": "passed", "sql_sha256": sql_hashes,
    }
    _json(folder / "manifest.json", manifest)
    _json(data / "serving" / "current.json", {"snapshot_id": manifest["id"]})
    _json(data / "converted" / "manifest.json", {"source_sha256": source_sha, "rows": 16})
    _json(runtime / "runs" / "20261116T190000-passed.json", {
        "run_id": "20261116T190000-passed", "stage": "batch", "state": "passed",
    })
    return test_client, data, runtime, manifest


def _money(value):
    return Decimal(str(value))


def test_unpublished_snapshot_returns_actionable_service_unavailable(client):
    test_client, _, _ = client
    assert test_client.get("/api/health").json()["has_snapshot"] is False
    response = test_client.get("/api/overview")
    assert response.status_code == 503
    assert "validated snapshot" in response.json()["detail"]


def test_overview_reports_exact_hand_calculated_totals_and_manifest(published):
    test_client, _, _, manifest = published
    response = test_client.get("/api/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] == manifest
    assert body["snapshot"]["mode"] == "synthetic"
    assert _money(body["metrics"]["gross_positive_gbp"]) == Decimal("48.51")
    assert _money(body["metrics"]["negative_adjustments_gbp"]) == Decimal("-13.51")
    assert _money(body["metrics"]["net_recorded_gbp"]) == Decimal("35.00")
    assert body["metrics"]["positive_units"] == 14
    assert body["metrics"]["known_customer_rows"] == 6
    assert body["selected_rows"] == 8
    assert body["filters"]["countries"] == ["France", "United Kingdom"]
    assert [_money(row["net_recorded_gbp"]) for row in body["daily"]] == [Decimal("47.50"), Decimal("-12.50"), Decimal("0")]
    assert sum((_money(row["net_recorded_gbp"]) for row in body["products"]), Decimal(0)) == Decimal("35.00")


def test_country_filter_keeps_missing_customer_sales_in_uk_totals(published):
    test_client, _, _, manifest = published
    body = test_client.get("/api/overview", params={"country": "United Kingdom"}).json()
    assert _money(body["metrics"]["net_recorded_gbp"]) == Decimal("37.50")
    assert body["metrics"]["known_customer_rows"] == 3
    assert body["metrics"]["positive_units"] == 11
    assert body["selected_rows"] == 5
    assert [row["country"] for row in body["countries"]] == ["United Kingdom"]
    # Active filters affect selected metrics, while publication provenance stays global.
    assert body["snapshot"] == manifest


def test_inclusive_date_and_country_filters_use_business_dates(published):
    test_client, _, _, _ = published
    body = test_client.get("/api/overview", params={
        "country": "United Kingdom", "start": "2009-12-02", "end": "2009-12-02",
    }).json()
    assert body["selected_rows"] == 2
    assert _money(body["metrics"]["net_recorded_gbp"]) == Decimal("-10.00")
    assert body["metrics"]["positive_units"] == 4
    assert [row["date"] for row in body["daily"]] == ["2009-12-02"]


def test_valid_empty_range_has_zero_totals_and_empty_result_tables(published):
    test_client, _, _, _ = published
    response = test_client.get("/api/overview", params={"start": "2012-01-01", "end": "2012-01-31"})
    assert response.status_code == 200
    body = response.json()
    assert body["selected_rows"] == 0
    assert all(_money(value) == 0 for value in body["metrics"].values())
    assert body["daily"] == body["products"] == body["countries"] == []
    assert body["filters"]["countries"] == ["France", "United Kingdom"]


def test_country_is_a_bound_value_and_not_sql(published):
    test_client, _, _, _ = published
    response = test_client.get("/api/overview", params={"country": "' OR TRUE --"})
    assert response.status_code == 200
    assert response.json()["selected_rows"] == 0


@pytest.mark.parametrize("metric,params,expected_rows", [
    ("net_recorded_gbp", {}, [2, 3, 4, 5, 6, 7, 14, 15]),
    ("gross_positive_gbp", {"country": "United Kingdom"}, [2, 3, 4]),
    ("negative_adjustments_gbp", {"country": "France"}, [6, 15]),
    ("positive_units", {}, [2, 3, 4, 7, 14]),
    ("known_customer_rows", {}, [2, 3, 5, 6, 14, 15]),
    ("net_recorded_gbp", {"start": "2009-12-03", "end": "2009-12-03"}, [14, 15]),
])
def test_lineage_contains_only_contributing_source_rows(published, metric, params, expected_rows):
    test_client, _, _, manifest = published
    response = test_client.get("/api/lineage", params={"metric": metric, **params})
    assert response.status_code == 200
    body = response.json()
    assert body["metric"] == metric
    assert body["snapshot"] == manifest
    assert body["total_rows"] == len(expected_rows)
    assert [row["source_row"] for row in body["rows"]] == expected_rows
    assert all(row["source_sheet"] == "Fixture" for row in body["rows"])
    assert all(row["source_sha256"] == "a" * 64 for row in body["rows"])
    assert "source_lines" in body["sql"]


def test_lineage_limit_does_not_change_contributing_row_denominator(published):
    test_client, _, _, _ = published
    body = test_client.get("/api/lineage", params={"metric": "known_customer_rows", "limit": 2}).json()
    assert body["total_rows"] == 6
    assert [row["source_row"] for row in body["rows"]] == [2, 3]


def test_lineage_rejects_unknown_metric_and_invalid_limit(published):
    test_client, _, _, _ = published
    assert test_client.get("/api/lineage", params={"metric": "profit"}).status_code == 400
    assert test_client.get("/api/lineage", params={"limit": 0}).status_code == 422
    assert test_client.get("/api/lineage", params={"limit": 201}).status_code == 422


def test_lineage_serves_published_sql_after_working_model_changes(published):
    test_client, data, _, manifest = published
    folder = data / "serving" / "snapshots" / manifest["id"]
    expected_sql = "\n\n".join((folder / "models" / name).read_text(encoding="utf-8") for name in MODEL_NAMES)
    (api.ROOT / "sql" / "silver.sql").write_text("SELECT 'MUTATED WORKING COPY';", encoding="utf-8")
    response = test_client.get("/api/lineage")
    assert response.status_code == 200
    assert response.json()["sql"] == expected_sql
    assert "MUTATED WORKING COPY" not in response.json()["sql"]
    assert response.json()["snapshot"]["sql_sha256"] == manifest["sql_sha256"]


def test_lineage_refuses_corrupted_published_sql(published):
    test_client, data, _, manifest = published
    model = data / "serving" / "snapshots" / manifest["id"] / "models" / "silver.sql"
    model.write_text("SELECT 'corrupted immutable artifact';", encoding="utf-8")
    response = test_client.get("/api/lineage")
    assert response.status_code == 409
    assert "checksum mismatch" in response.json()["detail"]


@pytest.mark.parametrize("endpoint", ["/api/overview", "/api/lineage"])
@pytest.mark.parametrize("params", [{"start": "not-a-date"}, {"end": "2009-02-30"}])
def test_malformed_dates_return_validation_errors(published, endpoint, params):
    test_client, _, _, _ = published
    response = test_client.get(endpoint, params=params)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][0] == "query"


def test_trust_preserves_valid_snapshot_and_reports_later_failed_attempt(published):
    test_client, _, runtime, manifest = published
    _json(runtime / "runs" / "20261116T191000-failed.json", {
        "run_id": "20261116T191000-failed", "stage": "batch", "state": "failed",
        "error": "Controlled failure before publication pointer",
    })
    response = test_client.get("/api/trust")
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"] == manifest
    assert body["source"]["source_sha256"] == manifest["source_sha256"]
    assert body["publication"]["state"] == "failed"
    assert body["publication"]["last_validated_at"] == manifest["published_at"]
    assert body["publication"]["last_attempt"]["run_id"] == "20261116T191000-failed"
    assert test_client.get("/api/overview").json()["snapshot"]["id"] == manifest["id"]


def test_forecasts_are_explicitly_unexecuted_until_evidence_exists(published):
    test_client, _, _, _ = published
    body = test_client.get("/api/forecasts").json()
    assert body["state"] == "not_executed"
    _json(api.ROOT / "analysis" / "forecast" / "serving.json", {"state": "passed", "promoted_model": "seasonal_naive"})
    body = test_client.get("/api/forecasts").json()
    assert body["state"] == "passed" and body["promoted_model"] == "seasonal_naive"


def test_demo_controls_require_local_header_and_allow_listed_jobs(published):
    test_client, _, runtime, _ = published
    assert test_client.get("/api/replay").json()["state"] == "not_initialised"
    assert test_client.post("/api/demo/jobs", json={"kind": "incident"}).status_code == 403
    headers = {"X-Meridian-Demo": "local"}
    assert test_client.post("/api/demo/jobs", json={"kind": "rm -rf"}, headers=headers).status_code == 400
    from meridian import worker
    worker.JOBS = runtime / "jobs"
    created = test_client.post("/api/demo/jobs", json={"kind": "reset"}, headers=headers)
    assert created.status_code == 200 and created.json()["state"] == "queued"
    assert test_client.post("/api/demo/jobs", json={"kind": "reset"}, headers=headers).status_code == 409
    assert test_client.get("/api/demo/jobs/..%2Fsecret").status_code in (400, 404)


def test_unknown_api_route_is_not_mistaken_for_frontend(published):
    test_client, _, _, _ = published
    assert test_client.get("/api/not-a-real-route").status_code == 404
