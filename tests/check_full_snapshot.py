"""Execute independent full-snapshot integrity and business-contract checks.

Run: python tests/check_full_snapshot.py
Reads source conversion and explicit serving exports with bounded DuckDB memory.
Never reads Delta's underlying Parquet files as a current table. Does not execute
Spark itself; its input is the already executed Spark publication manifest.
Evidence is written only after this checker actually executes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = ROOT / "evidence" / "full-snapshot-check.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition, message, detail=None):
    if not condition:
        raise AssertionError(f"{message}: {detail}" if detail is not None else message)


def row(con, sql):
    result = con.execute(sql)
    return dict(zip([description[0] for description in result.description], result.fetchone()))


def _safe_path(folder, relative):
    target = (folder / relative).resolve()
    require(target.is_relative_to(folder.resolve()), "Manifest path escapes its snapshot", relative)
    return target


def check(result):
    pointer = read_json(DATA / "serving" / "full.json")
    folder = DATA / "serving" / "snapshots" / pointer["snapshot_id"]
    manifest = read_json(folder / "manifest.json")
    converted = read_json(DATA / "converted" / "manifest.json")
    result.update(snapshot_id=manifest["id"], snapshot_manifest_sha256=checksum(folder / "manifest.json"),
                  workbook_sha256=manifest["source_sha256"], source_kind="observed historical source",
                  dataset_profile=manifest["profile"], checks={})
    checks = result["checks"]
    require(manifest["id"] == pointer["snapshot_id"], "Pointer and manifest disagree")
    require(manifest["profile"] == "full" and manifest["mode"] == "historical", "Wrong execution profile/mode")
    require(manifest["validation"] == "passed", "Snapshot was not validated")
    require(manifest["source_sha256"] == converted["source"]["workbook_sha256"], "Source workbook identity mismatch")

    source_path = DATA / "converted" / "historical.parquet"
    actual_source_hash = checksum(source_path)
    require(actual_source_hash == converted["full_parquet_sha256"], "Converted source checksum mismatch")
    actual_parquet_paths = {p.relative_to(folder).as_posix() for p in folder.rglob("*.parquet")}
    require(actual_parquet_paths == set(manifest["checksums"]), "Exported file set differs from manifest")
    for relative, expected in manifest["checksums"].items():
        require(checksum(_safe_path(folder, relative)) == expected, "Serving file checksum mismatch", relative)
    actual_sql_paths = {p.relative_to(folder / "models").as_posix() for p in (folder / "models").rglob("*.sql")}
    require(actual_sql_paths == set(manifest["sql_sha256"]), "Published SQL artifact set differs from manifest")
    for relative, expected in manifest["sql_sha256"].items():
        require(checksum(_safe_path(folder / "models", relative)) == expected, "Published SQL checksum mismatch", relative)
    checks["file_integrity"] = {"parquet_files": len(actual_parquet_paths), "sql_artifacts": len(actual_sql_paths),
                                "converted_source_sha256": actual_source_hash, "state": "passed"}

    run_path = ROOT / "runtime" / "runs" / f"{manifest['id']}.json"
    if run_path.exists():
        run = read_json(run_path)
        result["spark_run_git_sha"] = run.get("git_sha")
        result["spark_run_state"] = run.get("state")

    with duckdb.connect() as con:
        con.execute("SET threads=2")
        con.execute("SET memory_limit='384MB'")
        temp = ROOT / "runtime" / "full-snapshot-check-temp"
        temp.mkdir(parents=True, exist_ok=True)
        con.execute("SET temp_directory = ?", [temp.as_posix()])
        con.read_parquet(source_path.as_posix()).create_view("raw_source")
        required = {"fact_invoice_line", "dim_date", "dim_product", "dim_customer", "dim_geography",
                    "cohort_retention", "customer_summary", "forecast_inputs", "quality_daily"}
        require(required.issubset(manifest["tables"]), "Required Gold table missing", sorted(required - set(manifest["tables"])))
        for table in manifest["tables"]:
            con.read_parquet((folder / table / "*.parquet").as_posix()).create_view(table)

        raw = row(con, """SELECT count(*) AS rows,
            count(DISTINCT (source_sha256,source_sheet,source_row)) AS distinct_coordinates,
            count(DISTINCT source_sha256) AS source_checksums,
            min(source_sha256) AS source_sha256 FROM raw_source""")
        require(raw["rows"] == raw["distinct_coordinates"] == converted["total_rows"] == manifest["source_rows"], "Source coordinates do not reconcile", raw)
        require(raw["source_checksums"] == 1 and raw["source_sha256"] == manifest["source_sha256"], "Mixed source identity", raw)
        fact = row(con, "SELECT count(*) AS rows,count(DISTINCT line_id) AS distinct_lines,sum(line_amount) AS net_recorded_gbp FROM fact_invoice_line")
        require(fact["rows"] == fact["distinct_lines"] == manifest["accepted_rows"], "Fact identity/count mismatch", fact)
        require(fact["net_recorded_gbp"] == Decimal(str(manifest["metrics"]["net_recorded_gbp"])), "Fact amount/manifest mismatch", fact)
        source_join = row(con, """SELECT count(*) AS joined_rows,count(DISTINCT f.line_id) AS distinct_lines,
            sum(f.line_amount) AS net_recorded_gbp,
            count(*) FILTER (WHERE r.source_row IS NULL) AS unmatched_source_rows,
            count(*) FILTER (WHERE f.event_time IS DISTINCT FROM try_cast(r.event_time AS TIMESTAMP)) AS civil_timestamp_mismatches,
            count(*) FILTER (WHERE f.date IS DISTINCT FROM cast(try_cast(r.event_time AS TIMESTAMP) AS DATE)) AS business_date_mismatches,
            count(*) FILTER (WHERE f.line_id IS DISTINCT FROM sha256(concat_ws('|',r.source_sha256,r.source_sheet,cast(r.source_row AS VARCHAR)))) AS source_identity_mismatches,
            count(*) FILTER (WHERE month(try_cast(r.event_time AS TIMESTAMP)) BETWEEN 4 AND 10) AS summer_month_rows
            FROM fact_invoice_line f LEFT JOIN raw_source r
              ON f.source_sha256=r.source_sha256 AND f.source_sheet=r.source_sheet AND f.source_row=r.source_row""")
        require(source_join["joined_rows"] == source_join["distinct_lines"] == fact["rows"], "Source-coordinate join is not one-to-one", source_join)
        require(source_join["net_recorded_gbp"] == fact["net_recorded_gbp"], "Source join changes amounts", source_join)
        for key in ("unmatched_source_rows", "civil_timestamp_mismatches", "business_date_mismatches", "source_identity_mismatches"):
            require(source_join[key] == 0, key, source_join)
        require(source_join["summer_month_rows"] > 0, "No summer source rows exercised")
        checks["source_coordinate_and_civil_time"] = {"source": raw, "fact": fact, **source_join, "state": "passed"}

        dimensions = {}
        for table, key in (("dim_date", "date"), ("dim_product", "stock_code"), ("dim_customer", "customer_id"), ("dim_geography", "country")):
            counts = row(con, f"SELECT count(*) AS rows,count(DISTINCT {key}) AS distinct_keys,count(*) FILTER (WHERE {key} IS NULL) AS null_keys FROM {table}")
            require(counts["rows"] == counts["distinct_keys"] and counts["null_keys"] == 0, "Dimension key violation", {"table": table, **counts})
            dimensions[table] = counts
        unknown = row(con, """SELECT count(*) AS members,
            count(*) FILTER (WHERE is_known_customer OR first_observed_date IS NOT NULL OR last_observed_date IS NOT NULL) AS invalid_unknown_members
            FROM dim_customer WHERE customer_id='__UNKNOWN__'""")
        require(unknown["members"] == 1 and unknown["invalid_unknown_members"] == 0, "Invalid unknown customer member", unknown)
        require(con.execute("SELECT count(*) FROM fact_invoice_line WHERE customer_id='__UNKNOWN__'").fetchone()[0] == 0, "Real source ID collides with unknown sentinel")
        joined = row(con, """SELECT count(*) AS rows,count(DISTINCT f.line_id) AS distinct_lines,sum(f.line_amount) AS net_recorded_gbp,
            count(*) FILTER (WHERE d.date IS NULL) AS missing_date,
            count(*) FILTER (WHERE p.stock_code IS NULL) AS missing_product,
            count(*) FILTER (WHERE c.customer_id IS NULL) AS missing_customer,
            count(*) FILTER (WHERE g.country IS NULL) AS missing_geography,
            count(*) FILTER (WHERE f.customer_id IS NULL AND c.customer_id='__UNKNOWN__') AS unknown_customer_fact_rows
            FROM fact_invoice_line f
            LEFT JOIN dim_date d ON f.date=d.date
            LEFT JOIN dim_product p ON f.stock_code=p.stock_code
            LEFT JOIN dim_customer c ON coalesce(f.customer_id,'__UNKNOWN__')=c.customer_id
            LEFT JOIN dim_geography g ON f.country=g.country""")
        for key in ("rows", "distinct_lines", "net_recorded_gbp"):
            require(joined[key] == fact[key], "Dimension joins do not preserve fact", joined)
        require(all(joined[key] == 0 for key in ("missing_date", "missing_product", "missing_customer", "missing_geography")), "Unmatched dimension key", joined)
        checks["dimension_join_preservation"] = {"dimension_keys": dimensions, "unknown_member": unknown, "joined_fact": joined, "state": "passed"}

        # Recompute paid-purchase customer-month activity independently of Gold SQL.
        con.execute("""CREATE TEMP TABLE independent_paid_months AS
            SELECT DISTINCT customer_id,cast(date_trunc('month',date) AS DATE) AS activity_month
            FROM fact_invoice_line WHERE customer_id IS NOT NULL AND quantity>0 AND line_amount>0""")
        con.execute("""CREATE TEMP TABLE independent_first_purchase AS
            SELECT customer_id,min(activity_month) AS cohort_month FROM independent_paid_months GROUP BY customer_id""")
        con.execute("""CREATE TEMP TABLE independent_cohort_sizes AS
            SELECT cohort_month,count(*) AS cohort_size FROM independent_first_purchase GROUP BY cohort_month""")
        con.execute("""CREATE TEMP TABLE independent_cohort_activity AS
            SELECT f.cohort_month,p.activity_month,count(*) AS active_customers
            FROM independent_paid_months p JOIN independent_first_purchase f USING (customer_id)
            GROUP BY f.cohort_month,p.activity_month""")
        cohort = row(con, """SELECT count(*) AS rows,count(DISTINCT (c.cohort_month,c.activity_month)) AS distinct_keys,
            count(*) FILTER (WHERE c.cohort_size<=0 OR c.active_customers<0 OR c.active_customers>c.cohort_size
                OR c.month_index<0 OR c.month_index!=date_diff('month',c.cohort_month,c.activity_month)) AS invalid_counts,
            count(*) FILTER (WHERE c.retention_rate IS DISTINCT FROM cast(c.active_customers AS DOUBLE)/c.cohort_size) AS rate_mismatches,
            count(*) FILTER (WHERE c.cohort_size IS DISTINCT FROM s.cohort_size) AS independent_denominator_mismatches,
            count(*) FILTER (WHERE c.active_customers IS DISTINCT FROM CASE WHEN c.observed_source_days>0 THEN coalesce(a.active_customers,0) ELSE NULL END) AS independent_numerator_mismatches,
            count(*) FILTER (WHERE c.month_index=0) AS month_zero_rows,
            count(*) FILTER (WHERE c.month_index=0 AND (c.active_customers IS DISTINCT FROM c.cohort_size OR c.retention_rate IS DISTINCT FROM 1.0)) AS month_zero_mismatches,
            count(*) FILTER (WHERE c.activity_month=cast(date_trunc('month',b.final_date) AS DATE)) AS final_partial_month_rows,
            count(*) FILTER (WHERE c.is_final_partial_month IS DISTINCT FROM (c.activity_month=cast(date_trunc('month',b.final_date) AS DATE))) AS final_month_flag_mismatches,
            count(*) FILTER (WHERE c.is_final_partial_month AND (c.is_period_within_source_bounds OR c.is_period_complete)) AS final_month_marked_complete
            FROM cohort_retention c
            LEFT JOIN independent_cohort_sizes s ON c.cohort_month=s.cohort_month
            LEFT JOIN independent_cohort_activity a ON c.cohort_month=a.cohort_month AND c.activity_month=a.activity_month
            CROSS JOIN (SELECT max(date) AS final_date FROM dim_date) b""")
        require(cohort["rows"] == cohort["distinct_keys"] > 0, "Cohort grain invalid", cohort)
        for key in ("invalid_counts", "rate_mismatches", "independent_denominator_mismatches", "independent_numerator_mismatches", "month_zero_mismatches", "final_month_flag_mismatches", "final_month_marked_complete"):
            require(cohort[key] == 0, key, cohort)
        require(cohort["month_zero_rows"] == con.execute("SELECT count(*) FROM independent_cohort_sizes").fetchone()[0], "A cohort's month zero is missing", cohort)
        require(cohort["final_partial_month_rows"] > 0, "Final partial month was not exercised")
        checks["cohort_retention"] = {**cohort, "state": "passed"}

        calendar = row(con, """SELECT count(*) AS rows,min(date) AS first_date,max(date) AS final_date,
            count(*) FILTER (WHERE NOT observed_source_day) AS unknown_source_days,
            count(*) FILTER (WHERE is_final_partial_source_day) AS final_partial_days FROM dim_date""")
        require(calendar["rows"] == (calendar["final_date"]-calendar["first_date"]).days+1, "Calendar does not cover every date", calendar)
        require(calendar["final_partial_days"] == 1, "Final day flag is not unique", calendar)
        source_days = row(con, """WITH raw_days AS (
            SELECT cast(try_cast(event_time AS TIMESTAMP) AS DATE) AS date,count(*) AS source_rows
            FROM raw_source WHERE try_cast(event_time AS TIMESTAMP) IS NOT NULL GROUP BY 1)
            SELECT count(*) FILTER (WHERE d.source_rows!=coalesce(r.source_rows,0)
              OR d.observed_source_day IS DISTINCT FROM (r.source_rows IS NOT NULL)) AS source_day_mismatches
            FROM dim_date d LEFT JOIN raw_days r USING (date)""")
        require(source_days["source_day_mismatches"] == 0, "Calendar observation status differs from original source", source_days)
        forecast = row(con, """WITH expected_units AS (
            SELECT date,sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS positive_units FROM fact_invoice_line GROUP BY date)
            SELECT count(*) AS rows,count(DISTINCT (f.series_id,f.date)) AS distinct_keys,
            count(*) FILTER (WHERE f.series_id!='__ALL__') AS unexpected_series,
            count(*) FILTER (WHERE d.date IS NULL) AS missing_calendar_rows,
            count(*) FILTER (WHERE NOT d.observed_source_day) AS unavailable_days,
            count(*) FILTER (WHERE NOT d.observed_source_day AND (f.positive_units IS NOT NULL OR f.is_observation_available)) AS unavailable_days_fabricated_as_zero,
            count(*) FILTER (WHERE f.is_final_partial_source_day) AS final_partial_days,
            count(*) FILTER (WHERE f.is_final_partial_source_day AND f.is_observation_available) AS final_days_used_as_complete,
            count(*) FILTER (WHERE f.is_observation_available IS DISTINCT FROM (d.observed_source_day AND NOT d.is_final_partial_source_day AND f.quarantined_rows=0)) AS availability_rule_mismatches,
            count(*) FILTER (WHERE f.positive_units IS DISTINCT FROM CASE WHEN d.observed_source_day THEN coalesce(u.positive_units,0) ELSE NULL END) AS unit_sum_mismatches,
            count(*) FILTER (WHERE d.observed_source_day AND f.positive_units=0) AS observed_zero_unit_days
            FROM forecast_inputs f LEFT JOIN dim_date d ON f.date=d.date
            LEFT JOIN expected_units u ON f.date=u.date""")
        require(forecast["rows"] == forecast["distinct_keys"] == calendar["rows"], "Forecast/calendar grains differ", forecast)
        require(forecast["unavailable_days"] == calendar["unknown_source_days"], "Forecast availability lost calendar gaps", forecast)
        require(forecast["final_partial_days"] == 1, "Forecast final partial day missing", forecast)
        for key in ("unexpected_series", "missing_calendar_rows", "unavailable_days_fabricated_as_zero", "final_days_used_as_complete", "availability_rule_mismatches", "unit_sum_mismatches"):
            require(forecast[key] == 0, key, forecast)
        checks["calendar_and_forecast_availability"] = {"calendar": calendar, **source_days, "forecast": forecast, "state": "passed"}

        quality = row(con, """SELECT sum(source_rows) AS source_rows,sum(accepted_rows) AS accepted_rows,
            sum(quarantined_rows) AS quarantined_rows,sum(excluded_rows) AS excluded_rows,
            sum(unparseable_amount_rows) AS unparseable_amount_rows,
            sum(source_parseable_gbp) AS source_parseable_gbp,
            sum(accepted_parseable_gbp) AS accepted_parseable_gbp,
            sum(quarantined_parseable_gbp) AS quarantined_parseable_gbp,
            sum(excluded_parseable_gbp) AS excluded_parseable_gbp FROM quality_daily""")
        for key in ("source_rows", "accepted_rows", "quarantined_rows", "excluded_rows"):
            require(quality[key] == manifest[key], "Daily quality count mismatch", quality)
        require(quality["unparseable_amount_rows"] == manifest["reconciliation_totals"]["unparseable_amount_rows"], "Unparseable amount count mismatch", quality)
        require(quality["source_parseable_gbp"] == quality["accepted_parseable_gbp"]+quality["quarantined_parseable_gbp"]+quality["excluded_parseable_gbp"], "Daily quality amount bridge does not balance", quality)
        require(quality["accepted_parseable_gbp"] == fact["net_recorded_gbp"], "Accepted quality/fact amounts differ", quality)
        checks["quality_reconciliation"] = {**quality, "state": "passed"}


def main():
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    result = {"started_at": utc_now(), "command": [sys.executable, *sys.argv],
              "cwd": str(ROOT), "git_sha": revision.stdout.strip(), "working_tree_dirty": bool(dirty.stdout.strip()),
              "python": platform.python_version(), "duckdb": duckdb.__version__,
              "duckdb_threads": 2, "duckdb_memory_limit": "384MB", "cache_condition": "OS cache uncontrolled; new DuckDB connection",
              "scope": "Independent source/serving verification after the executed full Spark/Delta publication"}
    started = time.perf_counter()
    try:
        check(result)
        result["state"] = "passed"
    except Exception as exc:
        result.update(state="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    result.update(finished_at=utc_now(), duration_seconds=round(time.perf_counter()-started, 6))
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    temp = EVIDENCE.with_suffix(".tmp")
    temp.write_text(json.dumps(result, indent=2, default=str, allow_nan=False), encoding="utf-8")
    temp.replace(EVIDENCE)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["state"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
