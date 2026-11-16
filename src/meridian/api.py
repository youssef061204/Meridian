from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from datetime import date, timedelta

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .common import DATA, ROOT, RUNTIME, now, read_json, sha256
from .pipeline import metrics_query

app = FastAPI(title="MERIDIAN", description="Local historical retail portfolio case study")


def snapshot():
    pointer = DATA / "serving" / "current.json"
    if not pointer.exists():
        raise HTTPException(503, "No validated snapshot. Run the documented demo pipeline.")
    folder = DATA / "serving" / "snapshots" / read_json(pointer)["snapshot_id"]
    return folder, read_json(folder / "manifest.json")


@contextmanager
def serving():
    folder, manifest = snapshot()
    con = duckdb.connect()
    try:
        con.execute("SET threads=2")
        for name in ["fact_invoice_line", "daily_sales", "product_performance", "country_performance"]:
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{(folder/name).as_posix()}/*.parquet')")
        yield con, manifest
    finally:
        con.close()


def rows(con, query, args=None):
    result = con.execute(query, args or [])
    columns = [d[0] for d in result.description]
    return [dict(zip(columns, record)) for record in result.fetchall()]


def filters(country=None, start=None, end=None):
    terms, args = [], []
    for col, op, val in [("country", "=", country), ("date", ">=", start), ("date", "<=", end)]:
        if val:
            terms.append(f"{col} {op} ?")
            args.append(val)
    return " AND ".join(terms) or "TRUE", args


@app.get("/api/health")
def health():
    return {"status": "ok", "has_snapshot": (DATA/"serving"/"current.json").exists(), "time":now()}


@app.get("/api/overview")
def overview(country: str | None = None, start: date | None = None, end: date | None = None):
    where, args = filters(country, start, end)
    with serving() as (con, manifest):
        con.execute("CREATE TEMP TABLE selected AS SELECT * FROM fact_invoice_line WHERE " + where, args)
        metrics = rows(con, metrics_query("selected"))[0]
        metrics["accepted_rows"] = con.execute("SELECT count(*) FROM selected").fetchone()[0]
        daily = rows(con, "SELECT date, sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END) AS gross_positive_gbp, sum(CASE WHEN line_amount<0 THEN line_amount ELSE 0 END) AS negative_adjustments_gbp, sum(line_amount) AS net_recorded_gbp, sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS positive_units FROM selected GROUP BY date ORDER BY date")
        products = rows(con, "SELECT stock_code,max(description) AS description,sum(line_amount) AS net_recorded_gbp,sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS positive_units FROM selected GROUP BY stock_code ORDER BY net_recorded_gbp DESC LIMIT 15")
        countries = rows(con, "SELECT country,sum(line_amount) AS net_recorded_gbp,count(*) AS rows FROM selected GROUP BY country ORDER BY net_recorded_gbp DESC")
        cohorts = []
        snapshot_folder = DATA / "serving" / "snapshots" / manifest["id"]
        if (snapshot_folder/"cohort_retention").exists() and not (country or start or end):
            con.execute(f"CREATE VIEW cohort_retention AS SELECT * FROM read_parquet('{(snapshot_folder/'cohort_retention').as_posix()}/*.parquet')")
            cohorts = rows(con, "SELECT *,cohort_size AS cohort_customers,is_period_within_source_bounds AS is_complete_month FROM cohort_retention ORDER BY cohort_month,month_index")
        comparison = None
        bound = min(date.fromisoformat(manifest["date_max"])-timedelta(days=1), end or date.max)
        previous_start = bound-timedelta(days=55)
        if manifest.get("profile") == "full" and previous_start >= date.fromisoformat(manifest["date_min"]) and (not start or previous_start >= start):
            current_start, previous_end = bound-timedelta(days=27),bound-timedelta(days=28)
            def period(a,b):
                predicate, values = filters(country,a,b)
                return con.execute("SELECT coalesce(sum(line_amount),0) FROM fact_invoice_line WHERE "+predicate,values).fetchone()[0]
            current_net, previous_net = period(current_start,bound),period(previous_start,previous_end)
            comparison = dict(current_start=str(current_start),current_end=str(bound),previous_start=str(previous_start),previous_end=str(previous_end),current_net_gbp=current_net,previous_net_gbp=previous_net,change_gbp=current_net-previous_net,change_pct=(current_net-previous_net)/abs(previous_net)*100 if previous_net else None,limitation="Equal 28-calendar-day windows; source's final day excluded as potentially partial. No-source days are unavailable, not proven zero sales. This is an association within recorded exports, not a causal estimate.")
        warning = "Original workbook coordinates are preserved. Identical transaction payloads occur across the overlapping December 2010 exports; unique real-world purchases require client reconciliation."
        return {"snapshot":manifest,"metrics":metrics,"daily":daily,"products":products,"countries":countries,"cohorts":cohorts,"comparison":comparison,"quality":{"known_customer_rows":metrics["known_customer_rows"],"accepted_rows":metrics["accepted_rows"],"source_overlap_warning":warning if manifest.get("profile")=="full" else ""},"filters":{"countries":[r[0] for r in con.execute("SELECT DISTINCT country FROM fact_invoice_line ORDER BY country").fetchall()]},"selected_rows":metrics["accepted_rows"]}


@app.get("/api/lineage")
def lineage(metric: str = "net_recorded_gbp", limit: int = Query(50, ge=1, le=200), country: str | None = None, start: date | None = None, end: date | None = None):
    if metric not in {"net_recorded_gbp","gross_positive_gbp","negative_adjustments_gbp","positive_units","known_customer_rows"}:
        raise HTTPException(400, "Unknown metric")
    where, args = filters(country, start, end)
    conditions = {"gross_positive_gbp":"line_amount > 0","negative_adjustments_gbp":"line_amount < 0","positive_units":"quantity > 0","known_customer_rows":"customer_id IS NOT NULL"}
    where += " AND " + conditions.get(metric, "TRUE")
    with serving() as (con, manifest):
        total = con.execute("SELECT count(*) FROM fact_invoice_line WHERE " + where, args).fetchone()[0]
        result = rows(con, "SELECT * FROM fact_invoice_line WHERE " + where + " ORDER BY source_sheet,source_row LIMIT ?", [*args,limit])
        folder = DATA / "serving" / "snapshots" / manifest["id"]
        models = []
        for name in ["silver.sql","daily_sales.sql"]:
            model = folder / "models" / name
            if not model.exists():
                model = ROOT / "sql" / name
            expected = manifest.get("sql_sha256",{}).get(name)
            if expected and sha256(model) != expected:
                raise HTTPException(409,"Snapshot SQL checksum mismatch; lineage unavailable")
            models.append(model.read_text(encoding="utf-8"))
        return {"snapshot":manifest,"sql":"\n\n".join(models),"rows":result,"total_rows":total,"metric":metric}


@app.get("/api/trust")
def trust():
    _, manifest = snapshot()
    runs = sorted((RUNTIME / "runs").glob("*.json"), reverse=True)[:30]
    run_data = [read_json(p) for p in runs]
    latest_publish = next((r for r in run_data if r["stage"] == "batch"), None)
    return {"snapshot":manifest,"runs":run_data,"source":read_json(DATA/"converted"/"manifest.json") if (DATA/"converted"/"manifest.json").exists() else {},"publication":{"state":latest_publish["state"] if latest_publish else "unknown", "last_validated_at":manifest["published_at"],"last_attempt":latest_publish}}


@app.get("/api/forecasts")
def forecasts():
    return {"state":"not_executed","message":"Forecasting is scheduled for Gate 4 after historical and recovery validation."}


dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist/"assets"), name="assets")


@app.get("/{path:path}")
def index(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    if not (dist/"index.html").exists():
        raise HTTPException(503,"Build frontend first: npm --prefix frontend run build")
    return FileResponse(dist/"index.html")
