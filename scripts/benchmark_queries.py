"""Analytical query latency over the validated full snapshot (single machine).

* API: cold = first request after an API container restart (OS file cache NOT dropped);
  warm = REPS sequential requests afterwards.
* DuckDB direct: representative SQL (joins, CTEs, window functions, cohort logic), each
  on a new connection (cold-ish) and then REPS warm repetitions.
* Optimization: daily trend from the pre-aggregated daily_sales mart vs a fact scan,
  with an exact result-equivalence check.
"""
from __future__ import annotations

import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import duckdb

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8010"  # "localhost" on Windows urllib adds a ~2 s IPv6 fallback per request
REPS = 20


def summary(samples):
    ordered = sorted(samples)
    return {"n": len(samples), "p50_ms": round(statistics.median(ordered) * 1000, 2),
            "p95_ms": round(ordered[max(0, int(len(ordered) * .95) - 1)] * 1000, 2), "max_ms": round(ordered[-1] * 1000, 2)}


def timed_get(path):
    started = time.perf_counter()
    with urlopen(BASE + path, timeout=120) as response:
        response.read()
    return time.perf_counter() - started


def main():
    result = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "command": " ".join([sys.executable, *sys.argv]),
              "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "python": platform.python_version(), "duckdb": duckdb.__version__, "scope": "single-machine measurements; not a distributed benchmark"}
    snapshot_id = json.loads((ROOT / "data/serving/full.json").read_text())["snapshot_id"]
    folder = ROOT / "data/serving/snapshots" / snapshot_id
    result["snapshot_id"] = snapshot_id
    endpoints = ["/api/overview", "/api/overview?country=France", "/api/overview?start=2011-01-01&end=2011-06-30",
                 "/api/lineage?metric=net_recorded_gbp&limit=50", "/api/trust", "/api/forecasts", "/api/replay"]
    subprocess.run(["docker", "compose", "restart", "api"], cwd=ROOT, check=True, capture_output=True)
    for _ in range(60):
        try:
            timed_get("/api/health"); break
        except Exception:
            time.sleep(1)
    api = {}
    for path in endpoints:
        cold = timed_get(path)
        warm = [timed_get(path) for _ in range(REPS)]
        api[path] = {"cold_first_request_ms": round(cold * 1000, 2), "warm": summary(warm)}
    result["api"] = {"cache_condition": "API process restarted before each endpoint's first request series; OS page cache not dropped", "endpoints": api}

    views = "".join(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{(folder / t).as_posix()}/*.parquet');"
                    for t in ["fact_invoice_line", "daily_sales", "dim_product", "dim_customer", "cohort_retention"])
    queries = {
        "headline_kpis": "SELECT sum(CASE WHEN line_amount>0 THEN line_amount END), sum(CASE WHEN line_amount<0 THEN line_amount END), sum(line_amount) FROM fact_invoice_line",
        "top_products_join_dimension": "SELECT p.stock_code, p.description, sum(f.line_amount) net FROM fact_invoice_line f JOIN dim_product p USING (stock_code) GROUP BY 1,2 ORDER BY net DESC LIMIT 20",
        "monthly_trend_window_growth": """WITH m AS (SELECT date_trunc('month', date) AS month_start, sum(line_amount) AS net FROM fact_invoice_line GROUP BY 1)
            SELECT month_start, net, net - lag(net, 12) OVER (ORDER BY month_start) AS yoy_change FROM m ORDER BY month_start""",
        "cohort_first_purchase_cte": """WITH first AS (SELECT customer_id, min(date_trunc('month', date)) AS cohort FROM fact_invoice_line WHERE customer_id IS NOT NULL AND quantity > 0 GROUP BY 1),
            act AS (SELECT DISTINCT customer_id, date_trunc('month', date) AS m FROM fact_invoice_line WHERE customer_id IS NOT NULL AND quantity > 0)
            SELECT cohort, date_diff('month', cohort, m) k, count(*) FROM act JOIN first USING (customer_id) GROUP BY 1,2""",
        "country_rank_window": "SELECT country, net, rank() OVER (ORDER BY net DESC) FROM (SELECT country, sum(line_amount) net FROM fact_invoice_line GROUP BY 1)",
    }
    direct = {}
    for name, sql in queries.items():
        con = duckdb.connect(); con.execute("SET threads=2"); con.execute("SET memory_limit='512MB'"); con.execute(views)
        started = time.perf_counter(); con.execute(sql).fetchall(); cold = time.perf_counter() - started
        warm = []
        for _ in range(REPS):
            started = time.perf_counter(); con.execute(sql).fetchall(); warm.append(time.perf_counter() - started)
        con.close()
        direct[name] = {"new_connection_first_ms": round(cold * 1000, 2), "warm": summary(warm)}
    result["duckdb_direct"] = {"threads": 2, "memory_limit": "512MB", "queries": direct}

    con = duckdb.connect(); con.execute("SET threads=2"); con.execute(views)
    scan_sql = "SELECT date, sum(line_amount) net, sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) units FROM fact_invoice_line GROUP BY date ORDER BY date"
    mart_sql = "SELECT date, net_recorded_gbp net, positive_units units FROM daily_sales ORDER BY date"
    scan_rows, mart_rows = con.execute(scan_sql).fetchall(), con.execute(mart_sql).fetchall()
    equivalent = [(str(d), str(n), int(u)) for d, n, u in scan_rows] == [(str(d), str(n), int(u)) for d, n, u in mart_rows]
    timings = {}
    for label, sql in (("fact_scan", scan_sql), ("daily_sales_mart", mart_sql)):
        samples = []
        for _ in range(REPS):
            started = time.perf_counter(); con.execute(sql).fetchall(); samples.append(time.perf_counter() - started)
        timings[label] = summary(samples)
    con.close()
    result["optimization_pre_aggregated_daily_mart"] = {"result_equivalent": equivalent, "rows": len(mart_rows), **timings,
                                                         "speedup_p50": round(timings["fact_scan"]["p50_ms"] / timings["daily_sales_mart"]["p50_ms"], 1)}
    assert equivalent, "Pre-aggregated mart differs from fact scan"
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = ROOT / "evidence" / "benchmark-queries.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k in ("api", "optimization_pre_aggregated_daily_mart")}, indent=1)[:3000])


if __name__ == "__main__":
    main()
