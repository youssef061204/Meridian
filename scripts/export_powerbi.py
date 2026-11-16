"""Export the validated full historical snapshot for a Power BI report.

Reads only the publication the serving pointer names (data/serving/full.json ->
data/serving/snapshots/<id>/). Every table is read through the explicit Parquet
files listed in that snapshot's manifest checksums, after their SHA-256 is
re-verified. No glob, no Delta table internals, no Delta _delta_log. Writes:

* powerbi/exports/<table>.csv (default) and/or .parquet  (git-ignored; large)
* powerbi/reconciliation.json  headline measures computed by SQL that a
  Power BI report built from these exports must reproduce exactly
* evidence/powerbi-export.json  row counts, checksums, snapshot id, round trip

This is export evidence. It does not show that a Power BI report exists: no
.pbix is produced, and native authoring was unavailable on the build machine.

Usage (host):  .venv/Scripts/python.exe scripts/export_powerbi.py [--format csv|parquet|both]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SERVING = ROOT / "data" / "serving"
OUT = ROOT / "powerbi" / "exports"
RECONCILIATION = ROOT / "powerbi" / "reconciliation.json"
EVIDENCE = ROOT / "evidence" / "powerbi-export.json"

# Export projection per table. The fact drops columns that are constant for accepted rows
# (disposition, parsed_amount == line_amount) or belong to provenance/dimensions (description,
# source checksum, ingestion metadata). Those remain in the snapshot and in the manifest.
EXPORTS = {
    "fact_invoice_line": """SELECT line_id, invoice, stock_code,
        coalesce(customer_id, '__UNKNOWN__') AS customer_key, customer_id, country, date, event_time,
        quantity, unit_price, line_amount, classification, source_sheet, source_row FROM {src}""",
    "dim_date": "SELECT * FROM {src}",
    "dim_product": "SELECT * FROM {src}",
    "dim_customer": "SELECT * FROM {src}",
    "dim_geography": "SELECT * FROM {src}",
    "daily_sales": "SELECT * FROM {src}",
    "quality_daily": "SELECT * FROM {src}",
    "cohort_retention": "SELECT * FROM {src}",
    "customer_summary": "SELECT * FROM {src}",
}
# Explicit CSV re-read types for the round-trip check (money must stay exact decimal).
FACT_CSV_TYPES = {"line_amount": "DECIMAL(20,2)", "unit_price": "DECIMAL(18,4)", "quantity": "BIGINT",
                  "date": "DATE", "customer_key": "VARCHAR", "customer_id": "VARCHAR", "country": "VARCHAR"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def q(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def measures(con, fact: str) -> dict:
    """Headline measures, each defined exactly as in docs/metrics.md and powerbi/measures.dax."""
    one = lambda sql: con.execute(sql).fetchone()
    base = one(f"""SELECT sum(CASE WHEN line_amount > 0 THEN line_amount ELSE 0 END),
        sum(CASE WHEN line_amount < 0 THEN line_amount ELSE 0 END), sum(line_amount),
        sum(CASE WHEN quantity > 0 THEN quantity ELSE 0 END), count(*),
        sum(CASE WHEN customer_key <> '__UNKNOWN__' THEN 1 ELSE 0 END), min(date), max(date) FROM {fact}""")
    gross, adjustments, net, units, rows, known, first, last = base
    window = lambda a, b: one(f"""SELECT coalesce(sum(line_amount), 0), coalesce(sum(CASE WHEN line_amount > 0 THEN line_amount ELSE 0 END), 0),
        count(DISTINCT date) FROM {fact} WHERE date BETWEEN DATE {q(a)} AND DATE {q(b)}""")
    result = {
        "gross_positive_merchandise_gbp": str(gross),
        "negative_adjustments_gbp": str(adjustments),
        "net_recorded_merchandise_gbp": str(net),
        "positive_units": int(units),
        "accepted_lines": rows,
        "known_customer_lines": int(known),
        "known_customer_coverage_pct": f"{Decimal(int(known)) / Decimal(rows) * 100:.4f}",
        "adjustment_ratio_pct": f"{-adjustments / gross * 100:.4f}",
        "fact_date_min": str(first),
        "fact_date_max": str(last),
    }
    periods = {}
    for label, a, b in [
        ("calendar_2010", "2010-01-01", "2010-12-31"),
        ("calendar_2011_partial_to_2011_12_09", "2011-01-01", "2011-12-31"),
        ("month_2011_11", "2011-11-01", "2011-11-30"),
        ("month_2010_11_same_period_last_year", "2010-11-01", "2010-11-30"),
        ("last_28_days_ending_2011_12_09", "2011-11-12", "2011-12-09"),
        ("prior_28_days_ending_2011_11_11", "2011-10-15", "2011-11-11"),
    ]:
        n, g, days = window(a, b)
        periods[label] = {"from": a, "to": b, "net_recorded_gbp": str(n), "gross_positive_gbp": str(g), "fact_dates_with_rows": days}
    result["periods"] = periods
    result["unknown_customer_member_net_gbp"] = str(one(f"SELECT sum(line_amount) FROM {fact} WHERE customer_key = '__UNKNOWN__'")[0])
    result["top_countries_net_gbp"] = {c: str(v) for c, v in con.execute(
        f"SELECT country, sum(line_amount) AS v FROM {fact} GROUP BY 1 ORDER BY v DESC, country LIMIT 5").fetchall()}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--format", choices=["csv", "parquet", "both"], default="csv")
    args = parser.parse_args()
    started = time.perf_counter()

    pointer = json.loads((SERVING / "full.json").read_text(encoding="utf-8"))
    snapshot_id = pointer["snapshot_id"]
    snapshot = SERVING / "snapshots" / snapshot_id
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("validation") != "passed" or manifest.get("id") != snapshot_id or manifest.get("profile") != "full":
        raise SystemExit(f"Refusing export: snapshot {snapshot_id} is not a validated full publication")

    files_by_table: dict[str, list[Path]] = {}
    verified = {}
    for relative, digest in manifest["checksums"].items():
        table = relative.split("/", 1)[0]
        if table not in EXPORTS:
            continue
        path = snapshot / relative
        actual = sha256(path)
        if actual != digest:
            raise SystemExit(f"Refusing export: checksum mismatch for {relative}")
        verified[relative] = actual
        files_by_table.setdefault(table, []).append(path)
    missing = [t for t in EXPORTS if t not in manifest["tables"] or t not in files_by_table]
    if missing:
        raise SystemExit(f"Refusing export: tables not listed in manifest: {missing}")

    OUT.mkdir(parents=True, exist_ok=True)
    formats = ["csv", "parquet"] if args.format == "both" else [args.format]
    con = duckdb.connect()
    tables_evidence = {}
    try:
        con.execute("SET memory_limit='512MB'")
        con.execute("SET threads=2")
        con.execute("SET preserve_insertion_order=false")
        for table, projection in EXPORTS.items():
            listing = "[" + ", ".join(q(p.as_posix()) for p in sorted(files_by_table[table])) + "]"
            con.execute(f"CREATE VIEW snap_{table} AS " + projection.format(src=f"read_parquet({listing})"))
            rows = con.execute(f"SELECT count(*) FROM snap_{table}").fetchone()[0]
            entry = {"rows": rows, "source_files": [p.relative_to(snapshot).as_posix() for p in sorted(files_by_table[table])], "outputs": {}}
            for fmt in formats:
                target = OUT / f"{table}.{fmt}"
                options = "FORMAT CSV, HEADER true, DELIMITER ','" if fmt == "csv" else "FORMAT PARQUET, COMPRESSION ZSTD"
                con.execute(f"COPY (SELECT * FROM snap_{table}) TO {q(target.as_posix())} ({options})")
                entry["outputs"][fmt] = {"path": target.relative_to(ROOT).as_posix(), "bytes": target.stat().st_size, "sha256": sha256(target)}
            tables_evidence[table] = entry

        # Cardinality checks that the Power BI model relies on.
        cardinality = {}
        for dim, key, fk in [("dim_date", "date", "date"), ("dim_product", "stock_code", "stock_code"),
                             ("dim_customer", "customer_id", "customer_key"), ("dim_geography", "country", "country")]:
            dup = con.execute(f"SELECT count(*) - count(DISTINCT {key}), count(*) - count({key}) FROM snap_{dim}").fetchone()
            orphans = con.execute(f"SELECT count(*) FROM snap_fact_invoice_line f ANTI JOIN snap_{dim} d ON f.{fk} = d.{key}").fetchone()[0]
            cardinality[f"{dim}.{key}"] = {"duplicate_keys": dup[0], "null_keys": dup[1], "fact_rows_without_match": orphans,
                                           "fact_column": f"fact_invoice_line.{fk}", "relationship": "many-to-one"}
        joined = con.execute("""SELECT count(*), sum(f.line_amount) FROM snap_fact_invoice_line f
            JOIN snap_dim_date d ON f.date = d.date JOIN snap_dim_product p ON f.stock_code = p.stock_code
            JOIN snap_dim_customer c ON f.customer_key = c.customer_id JOIN snap_dim_geography g ON f.country = g.country""").fetchone()
        reserved = con.execute("SELECT count(*) FROM snap_dim_customer WHERE customer_id = '__UNKNOWN__' AND NOT is_known_customer").fetchone()[0]
        date_gaps = con.execute("SELECT count(*) - (max(date) - min(date) + 1) FROM snap_dim_date").fetchone()[0]

        snapshot_measures = measures(con, "snap_fact_invoice_line")
        manifest_metrics = manifest["metrics"]
        checks = {
            "net_matches_manifest": snapshot_measures["net_recorded_merchandise_gbp"] == manifest_metrics["net_recorded_gbp"],
            "gross_matches_manifest": snapshot_measures["gross_positive_merchandise_gbp"] == manifest_metrics["gross_positive_gbp"],
            "adjustments_match_manifest": snapshot_measures["negative_adjustments_gbp"] == manifest_metrics["negative_adjustments_gbp"],
            "units_match_manifest": snapshot_measures["positive_units"] == int(manifest_metrics["positive_units"]),
            "known_lines_match_manifest": snapshot_measures["known_customer_lines"] == int(manifest_metrics["known_customer_rows"]),
            "fact_rows_match_manifest_accepted": tables_evidence["fact_invoice_line"]["rows"] == manifest["accepted_rows"],
            "dimension_keys_unique_nonnull": all(v["duplicate_keys"] == 0 and v["null_keys"] == 0 for v in cardinality.values()),
            "no_orphan_fact_rows": all(v["fact_rows_without_match"] == 0 for v in cardinality.values()),
            "star_join_preserves_rows_and_gbp": joined[0] == tables_evidence["fact_invoice_line"]["rows"] and str(joined[1]) == snapshot_measures["net_recorded_merchandise_gbp"],
            "unknown_customer_member_present_once": reserved == 1,
            "dim_date_contiguous": date_gaps == 0,
            "daily_sales_net_equals_fact_net": str(con.execute("SELECT sum(net_recorded_gbp) FROM snap_daily_sales").fetchone()[0]) == snapshot_measures["net_recorded_merchandise_gbp"],
        }

        # Round trip: re-read the exported fact file with explicit types; it must give identical measures.
        roundtrip = {}
        for fmt in formats:
            path = q((OUT / f"fact_invoice_line.{fmt}").as_posix())
            if fmt == "csv":
                types = "{" + ", ".join(f"{q(k)}: {q(v)}" for k, v in FACT_CSV_TYPES.items()) + "}"
                con.execute(f"CREATE OR REPLACE VIEW rt_fact AS SELECT * FROM read_csv({path}, header=true, types={types})")
            else:
                con.execute(f"CREATE OR REPLACE VIEW rt_fact AS SELECT * FROM read_parquet({path})")
            reread = measures(con, "rt_fact")
            roundtrip[fmt] = reread == snapshot_measures
            checks[f"{fmt}_roundtrip_measures_identical"] = roundtrip[fmt]
        duck_version = duckdb.__version__
    finally:
        con.close()

    passed = all(checks.values())
    reconciliation = {
        "purpose": "Headline values a Power BI report built from powerbi/exports must reproduce exactly (compare to the cent). Computed by DuckDB SQL from the validated snapshot, not by Power BI.",
        "native_report_status": "unfinished: Power BI Desktop is not installed on the build machine; no .pbix exists",
        "mode": manifest["mode"],
        "profile": manifest["profile"],
        "snapshot_id": snapshot_id,
        "manifest_sha256": sha256(manifest_path),
        "currency": "GBP recorded merchandise invoice value; not audited revenue or settled cash",
        "measure_definitions": "docs/metrics.md; DAX in powerbi/measures.dax",
        "measures": snapshot_measures,
        "visual_checks": [
            {"visual": "Card: Net Recorded Merchandise (no filters)", "must_equal": snapshot_measures["net_recorded_merchandise_gbp"]},
            {"visual": "Card: Gross Positive Merchandise (no filters)", "must_equal": snapshot_measures["gross_positive_merchandise_gbp"]},
            {"visual": "Card: Negative Adjustments (no filters)", "must_equal": snapshot_measures["negative_adjustments_gbp"]},
            {"visual": "Card: Positive Units (no filters)", "must_equal": snapshot_measures["positive_units"]},
            {"visual": "Card: Known-Customer Coverage % (no filters)", "must_equal_pct_4dp": snapshot_measures["known_customer_coverage_pct"]},
            {"visual": "Card: Adjustment Ratio % (no filters)", "must_equal_pct_4dp": snapshot_measures["adjustment_ratio_pct"]},
            {"visual": "Net Recorded Merchandise, dim_customer[customer_id] = __UNKNOWN__", "must_equal": snapshot_measures["unknown_customer_member_net_gbp"]},
            {"visual": "Net Recorded Merchandise, dim_date month 2011-11; Net SPLY", "must_equal": [snapshot_measures["periods"]["month_2011_11"]["net_recorded_gbp"], snapshot_measures["periods"]["month_2010_11_same_period_last_year"]["net_recorded_gbp"]]},
            {"visual": "Net Last 28 Days / Net Prior 28 Days with no date filter (anchor = last fact date 2011-12-09)", "must_equal": [snapshot_measures["periods"]["last_28_days_ending_2011_12_09"]["net_recorded_gbp"], snapshot_measures["periods"]["prior_28_days_ending_2011_11_11"]["net_recorded_gbp"]]},
        ],
    }
    RECONCILIATION.write_text(json.dumps(reconciliation, indent=2) + "\n", encoding="utf-8")
    evidence = {
        "label": "powerbi-export",
        "scope": "Local export of validated Gold snapshot tables for Power BI. Not evidence that a Power BI report was built.",
        "state": "passed" if passed else "failed",
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_sha(),
        "command": f".venv/Scripts/python.exe scripts/export_powerbi.py --format {args.format}",
        "engine": {"duckdb": duck_version, "python": platform.python_version(), "platform": platform.platform(), "memory_limit": "512MB", "threads": 2},
        "pointer": "data/serving/full.json",
        "snapshot_id": snapshot_id,
        "manifest_sha256": reconciliation["manifest_sha256"],
        "mode": manifest["mode"], "profile": manifest["profile"],
        "read_method": "DuckDB read_parquet over the explicit file list in the manifest checksums, each SHA-256 re-verified; no glob, no Delta internals",
        "verified_snapshot_files": verified,
        "tables": tables_evidence,
        "cardinality": cardinality,
        "star_join": {"rows": joined[0], "net_gbp": str(joined[1])},
        "checks": checks,
        "reconciliation_file": RECONCILIATION.relative_to(ROOT).as_posix(),
        "headline": {k: snapshot_measures[k] for k in ["gross_positive_merchandise_gbp", "negative_adjustments_gbp", "net_recorded_merchandise_gbp", "positive_units"]},
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    for name, ok in checks.items():
        if not ok:
            print(f"FAIL {name}")
    print(f"{evidence['state']}: snapshot {snapshot_id}; fact rows {tables_evidence['fact_invoice_line']['rows']}; "
          f"net {snapshot_measures['net_recorded_merchandise_gbp']}; {evidence['duration_seconds']} s")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
