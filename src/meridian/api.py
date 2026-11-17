from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import duckdb
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .common import DATA, ROOT, RUNTIME, now, read_json, sha256
from .pipeline import metrics_query

app = FastAPI(title="MERIDIAN", description="Local historical retail portfolio case study")


CACHE = Path(os.environ.get("MERIDIAN_SERVING_CACHE", Path(tempfile.gettempdir()) / "meridian-serving-cache"))
_verified: set[str] = set()


def local_snapshot(folder: Path) -> Path:
    """Serve immutable snapshots from container-local disk.

    Reading Parquet over the Windows/WSL2 bind mount made a lineage query ~24x slower
    (6.8 s vs 0.28 s). Snapshots never change after publication, so a checksum-verified
    local copy keyed by snapshot id is safe; the published folder stays the source of truth.
    """
    dest = CACHE / folder.name
    if folder.name in _verified and dest.exists():
        return dest
    if not dest.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        temp = CACHE / f"{folder.name}.partial-{uuid.uuid4().hex[:8]}"
        shutil.copytree(folder, temp)
        for relative, expected in read_json(temp / "manifest.json").get("checksums", {}).items():
            if sha256(temp / relative) != expected:
                shutil.rmtree(temp, ignore_errors=True)
                raise HTTPException(409, "Cached snapshot checksum mismatch")
        try:
            os.replace(temp, dest)
        except OSError:            # another request won the race
            shutil.rmtree(temp, ignore_errors=True)
        for stale in sorted((p for p in CACHE.iterdir() if p.is_dir() and ".partial-" not in p.name), key=lambda p: p.stat().st_mtime)[:-4]:
            shutil.rmtree(stale, ignore_errors=True)
            _verified.discard(stale.name)
    _verified.add(folder.name)
    return dest


def snapshot():
    pointer = DATA / "serving" / "current.json"
    if not pointer.exists():
        raise HTTPException(503, "No validated snapshot. Run the documented demo pipeline.")
    folder = DATA / "serving" / "snapshots" / read_json(pointer)["snapshot_id"]
    return folder, read_json(folder / "manifest.json")


@contextmanager
def serving():
    folder, manifest = snapshot()
    folder = local_snapshot(folder)
    con = duckdb.connect()
    try:
        con.execute("SET threads=2")
        # Explicit cap: inside a 400 MiB container DuckDB would otherwise size itself to the Docker VM.
        con.execute(f"SET memory_limit='{os.environ.get('MERIDIAN_API_DUCKDB_MEMORY', '256MB')}'")
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
        if args:   # filtered: parameters cannot be bound inside a view, so materialise the (smaller) selection
            con.execute("CREATE TEMP TABLE selected AS SELECT * FROM fact_invoice_line WHERE " + where, args)
        else:      # unfiltered: a view avoids copying ~1M rows per request
            con.execute("CREATE TEMP VIEW selected AS SELECT * FROM fact_invoice_line")
        metrics = rows(con, metrics_query("selected"))[0]
        metrics["accepted_rows"] = con.execute("SELECT count(*) FROM selected").fetchone()[0]
        daily = rows(con, "SELECT date, sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END) AS gross_positive_gbp, sum(CASE WHEN line_amount<0 THEN line_amount ELSE 0 END) AS negative_adjustments_gbp, sum(line_amount) AS net_recorded_gbp, sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS positive_units FROM selected GROUP BY date ORDER BY date")
        products = rows(con, "SELECT stock_code,coalesce(mode(CASE WHEN quantity>0 THEN description END),max(description)) AS description,sum(line_amount) AS net_recorded_gbp,sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS positive_units FROM selected GROUP BY stock_code ORDER BY net_recorded_gbp DESC LIMIT 15")
        countries = rows(con, "SELECT country,sum(line_amount) AS net_recorded_gbp,count(*) AS rows FROM selected GROUP BY country ORDER BY net_recorded_gbp DESC")
        cohorts = []
        snapshot_folder = local_snapshot(DATA / "serving" / "snapshots" / manifest["id"])
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
    for run in run_data:
        # A process killed on purpose (fault drills) never writes its completion record.
        if run.get("state") == "running" and run.get("started_at", "") < (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat():
            run["state"] = "interrupted: no completion recorded"
    latest_publish = next((r for r in run_data if r["stage"] == "batch"), None)
    return {"snapshot":manifest,"runs":run_data,"source":read_json(DATA/"converted"/"manifest.json") if (DATA/"converted"/"manifest.json").exists() else {},"publication":{"state":latest_publish["state"] if latest_publish else "unknown", "last_validated_at":manifest["published_at"],"last_attempt":latest_publish}}


@app.get("/api/forecasts")
def forecasts():
    path = ROOT / "analysis" / "forecast" / "serving.json"
    if not path.exists():
        return {"state": "not_executed", "message": "Run python -m meridian.cli forecast to produce evaluated forecasts."}
    result = read_json(path)
    inventory = ROOT / "analysis" / "forecast" / "inventory.json"
    if inventory.exists():
        data = read_json(inventory)
        result["inventory"] = {k: data.get(k) for k in ["label", "selected_model", "assumptions", "base_scenario", "lowest_cost_policy_counts", "sensitivity", "limitations", "episodes"]}
    return result


ASK_SPEC = ROOT / "frontend" / "shared" / "ask.json"
ASK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answerable", "sql", "explanation", "chart"],
              "properties": {"answerable": {"type": "boolean"}, "sql": {"type": "string"}, "explanation": {"type": "string"},
                             "chart": {"type": "string", "enum": ["line", "bar", "table"]}}}
_FORBIDDEN_SQL = re.compile(r"\b(copy|attach|detach|install|load|pragma|set|reset|create|insert|update|delete|drop|alter|export|import|call|checkpoint|vacuum|read_csv\w*|read_json\w*|read_parquet|read_text|read_blob|glob|httpfs)\b", re.I)


def check_sql(sql: str) -> str | None:
    """Python twin of frontend/shared/sqlguard.ts: one read-only statement."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        return "Empty query."
    if len(text) > 4000:
        return "Query is too long."
    if ";" in text:
        return "Only one statement is allowed."
    if not re.match(r"^(select|with|from)\b", text, re.I):
        return "Only SELECT queries are allowed."
    bad = _FORBIDDEN_SQL.search(re.sub(r"'(?:[^']|'')*'", "''", text))
    return f'"{bad.group(0)}" is not allowed in the SQL Lab.' if bad else None


@app.post("/api/ask")
def ask(body: dict):
    """Natural-language question -> one read-only SELECT. Returns SQL only; the browser executes it."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(501, "AI translation is not configured (set ANTHROPIC_API_KEY). Curated queries still work.")
    question = str(body.get("question", "")).strip()
    if not question or len(question) > 300:
        raise HTTPException(400, "Ask a question of 1-300 characters.")
    import anthropic
    spec = read_json(ASK_SPEC)
    system = f"{spec['system']}\n\n{spec['schema']}\n\nExamples:\n" + "\n\n".join(f"Q: {e['q']}\nSQL: {e['sql']}\nchart: {e['chart']}" for e in spec["examples"])
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=os.environ.get("MERIDIAN_ASK_MODEL", "claude-opus-5-5"), max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": ASK_SCHEMA}},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": question}])
    except anthropic.RateLimitError:
        raise HTTPException(429, "The AI service is rate limited. Try again shortly.")
    except anthropic.AuthenticationError:
        raise HTTPException(501, "The AI service key is invalid.")
    except anthropic.APIStatusError as error:
        raise HTTPException(502, f"AI service error ({error.status_code}).")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "AI service unreachable.")
    if response.stop_reason == "refusal":
        raise HTTPException(422, "The model declined this question.")
    if response.stop_reason == "max_tokens":
        raise HTTPException(502, "The model ran out of room before finishing. Try a simpler question.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise HTTPException(502, "No answer returned.")
    out = json.loads(text)
    if out["answerable"] and (problem := check_sql(out["sql"])):
        raise HTTPException(422, f"Generated SQL was rejected: {problem}")
    return {**out, "model": response.model, "source": "claude"}


@app.get("/api/insights")
def insights():
    path = ROOT / "analysis" / "insights" / "insights.json"
    if not path.exists():
        return {"state": "not_executed", "message": "Run python -m meridian.cli insights to produce customer, basket and statistical insights."}
    return read_json(path)


def _jsonl(path, limit=None):
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return records[-limit:] if limit else records


@app.get("/api/replay")
def replay_state():
    config_path = RUNTIME / "replay" / "config.json"
    if not config_path.exists():
        return {"state": "not_initialised", "message": "Run python -m meridian.cli replay-init --profile demo"}
    cfg = read_json(config_path)
    folder = RUNTIME / "replay" / cfg["generation"]
    pointer = DATA / "serving" / "replay.json"
    manifest = None
    if pointer.exists():
        manifest = read_json(DATA / "serving" / "snapshots" / read_json(pointer)["snapshot_id"] / "manifest.json")
        manifest.pop("checksums", None)
    batches = {}
    for record in _jsonl(folder / "batches.jsonl"):
        batches[record["batch_id"]] = record          # a retried batch keeps its last persisted record
    publications = _jsonl(folder / "publications.jsonl", 12)
    last_attempt = publications[-1] if publications else None
    age = None
    if manifest:
        from datetime import datetime
        age = (datetime.now(datetime.fromisoformat(manifest["published_at"]).tzinfo) - datetime.fromisoformat(manifest["published_at"])).total_seconds()
    resets = _jsonl(folder / "resets.jsonl", 3)
    serving_state = "current"
    if last_attempt and last_attempt["state"] != "passed" and not (resets and resets[-1]["reset_at"] > last_attempt["attempted_at"]):
        serving_state = f"last validated snapshot retained: latest attempt {last_attempt['state']}"
    elif resets and (not last_attempt or resets[-1]["reset_at"] > last_attempt["attempted_at"]):
        serving_state = "current: restored to recorded baseline"
    job_records = _job_records()[:8]
    worker = RUNTIME / "jobs" / "worker.json"
    timeline = folder / "incident_timeline.json"
    return {"state": "ready", "config": cfg, "snapshot": manifest, "snapshot_age_seconds": age,
            "serving_state": serving_state,
            "publications": publications, "batches": [batches[k] for k in sorted(batches)][-30:],
            "status": _live_json(folder / "status.json"),
            "backfills": _jsonl(folder / "backfills.jsonl", 6), "resets": _jsonl(folder / "resets.jsonl", 3),
            "timeline": _live_json(timeline),
            "worker": _live_json(worker, {"state": "not_running"}),
            "jobs": job_records, "controls_enabled": _controls_enabled()}


@app.get("/api/replay/correction")
def correction():
    pointer = DATA / "serving" / "replay.json"
    if not pointer.exists():
        raise HTTPException(404, "No replay snapshot")
    folder = local_snapshot(DATA / "serving" / "snapshots" / read_json(pointer)["snapshot_id"])
    manifest = read_json(folder / "manifest.json")
    con = duckdb.connect()
    try:
        con.execute(f"CREATE VIEW revisions AS SELECT * FROM read_parquet('{(folder/'revisions').as_posix()}/*.parquet')")
        con.execute(f"CREATE VIEW fact AS SELECT * FROM read_parquet('{(folder/'fact_invoice_line').as_posix()}/*.parquet')")
        lines = rows(con, """SELECT r.line_id, r.revision, r.mode, r.incident, r.source_sheet, r.source_row, r.invoice, r.stock_code, r.description,
              r.quantity, r.unit_price, round(try_cast(r.quantity AS DECIMAL(18,4)) * try_cast(r.unit_price AS DECIMAL(18,4)), 2) AS line_amount,
              r.event_time, r.first_batch_id, f.revision = r.revision AS is_current
            FROM revisions r LEFT JOIN fact f ON f.line_id = r.line_id
            WHERE r.line_id IN (SELECT line_id FROM revisions WHERE revision > 1) ORDER BY r.line_id, r.revision""")
    finally:
        con.close()
    return {"snapshot_id": manifest["id"], "reconciliation": manifest.get("reconciliation"), "metrics": manifest.get("metrics"),
            "batch_reference_snapshot": manifest.get("batch_reference_snapshot"), "batch_net_recorded_gbp": manifest.get("batch_net_recorded_gbp"),
            "lines": lines}


def _live_json(path, default=None):
    """Read a state file that another process replaces atomically. Over the Windows/WSL2 bind
    mount a rename can briefly make it invisible between exists() and open(), so retry."""
    for attempt in range(5):
        try:
            return read_json(path)
        except (FileNotFoundError, json.JSONDecodeError):
            if attempt == 4:
                return default
            time.sleep(0.05)


def _job_records():
    """Job records with stale 'running' states resolved: only the job the worker reports as busy is running.
    A process killed mid-job (e.g. an interrupted CLI replay-init) never writes its completion record."""
    folder = RUNTIME / "jobs"
    if not folder.exists():
        return []
    worker_path = folder / "worker.json"
    worker = _live_json(worker_path, {})
    records = []
    for path in folder.glob("*.json"):
        if path.name == "worker.json":
            continue
        record = _live_json(path)
        if record is None:
            continue
        if record.get("state") == "running" and not (worker.get("state") == "busy" and worker.get("job_id") == record.get("job_id")):
            record["state"] = "interrupted"
            record.setdefault("error", "Process ended without a completion record")
        records.append(record)
    return sorted(records, key=lambda j: j.get("requested_at", ""), reverse=True)


def _controls_enabled():
    return os.environ.get("MERIDIAN_DEMO_CONTROLS", "1") == "1"


@app.post("/api/demo/jobs")
def create_job(body: dict, x_meridian_demo: str | None = Header(default=None)):
    # Custom header => browsers must preflight; no CORS is granted, so other origins cannot trigger jobs.
    if not _controls_enabled() or x_meridian_demo != "local":
        raise HTTPException(403, "Demo controls are local-only and require the X-Meridian-Demo header")
    from .worker import ALLOWED_JOBS, enqueue
    kind = body.get("kind")
    if kind not in ALLOWED_JOBS:
        raise HTTPException(400, f"Allowed jobs: {sorted(ALLOWED_JOBS)}")
    active = [j for j in _job_records() if j.get("state") in ("queued", "running")]
    if active:
        raise HTTPException(409, "Another demo job is queued or running")
    return enqueue(kind, requested_by="local-ui")


@app.get("/api/demo/jobs/{job_id}")
def job(job_id: str):
    if not job_id.replace("-", "").isalnum():
        raise HTTPException(400, "Invalid job id")
    path = RUNTIME / "jobs" / f"{job_id}.json"
    if not path.exists():
        raise HTTPException(404, "Unknown job")
    return _live_json(path)


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
