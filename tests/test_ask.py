"""SQL Lab guard and the /api/ask contract (no model call is billed by these tests)."""
import json

import pytest
from fastapi.testclient import TestClient

from meridian.api import ASK_SPEC, app, check_sql

client = TestClient(app)


@pytest.mark.parametrize("sql", ["SELECT 1", "with a as (select 1) select * from a", "SELECT 'drop table x' AS s", "SELECT 1;"])
def test_guard_allows_single_read_only_select(sql):
    assert check_sql(sql) is None


@pytest.mark.parametrize("sql", ["DROP TABLE sales", "SELECT 1; DROP TABLE sales", "COPY sales TO 'x.csv'", "SELECT * FROM read_csv('http://x')",
                                 "ATTACH 'x.db'", "INSTALL httpfs", "SET threads=1", "", "UPDATE sales SET quantity=0"])
def test_guard_rejects_everything_else(sql):
    assert check_sql(sql) is not None


def test_every_curated_example_passes_the_guard():
    for example in json.loads(ASK_SPEC.read_text())["examples"]:
        assert check_sql(example["sql"]) is None, example["q"]


def test_ask_without_key_is_501(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/ask", json={"question": "monthly sales"})
    assert r.status_code == 501 and "Curated queries" in r.json()["detail"]


def test_ask_validates_question(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    assert client.post("/api/ask", json={"question": "x" * 301}).status_code == 400
