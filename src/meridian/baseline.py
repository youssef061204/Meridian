"""The simpler alternative: DuckDB batch over the exact same immutable inputs."""
from __future__ import annotations

import time
from pathlib import Path

import duckdb

from .common import DATA, ROOT, atomic_json, read_json, run_record, sha256
from .pipeline import metrics_query


def baseline(profile="full"):
    path = DATA / "converted" / ("demo.parquet" if profile == "demo" else "historical.parquet")
    provenance = read_json(DATA / "converted" / "manifest.json")
    key = "demo_parquet_sha256" if profile == "demo" else "full_parquet_sha256"
    if sha256(path) != provenance[key]:
        raise ValueError("Input checksum mismatch")
    with run_record("duckdb_batch_baseline", profile=profile, cache_condition="OS cache uncontrolled; new DuckDB connection; same converted Parquet and business SQL; no workbook parsing included") as run:
        con = duckdb.connect()
        con.execute("SET threads=2")
        con.execute("SET memory_limit='768MB'")
        output = DATA / "baseline" / profile / run["run_id"]
        output.mkdir(parents=True)
        start = time.perf_counter()
        try:
            con.execute(f"CREATE TABLE source_lines AS SELECT *,sha256(concat_ws('|',source_sha256,source_sheet,cast(source_row AS VARCHAR))) AS line_id FROM read_parquet('{path.as_posix()}')")
            # DuckDB TIMESTAMP is a civil timestamp without timezone, matching Spark NTZ.
            con.execute("CREATE TABLE silver AS " + (ROOT/"sql"/"silver.sql").read_text().replace("TIMESTAMP_NTZ", "TIMESTAMP"))
            con.execute("CREATE VIEW fact_invoice_line AS SELECT * FROM silver WHERE disposition='accepted'")
            con.execute("CREATE TABLE daily_sales AS " + (ROOT/"sql"/"daily_sales.sql").read_text())
            for table in ["fact_invoice_line","daily_sales"]:
                con.execute(f"COPY (SELECT * FROM {table}) TO '{(output/(table+'.parquet')).as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            result = con.execute(metrics_query())
            metrics = dict(zip([d[0] for d in result.description],result.fetchone()))
            counts = {d:n for d,n in con.execute("SELECT disposition,count(*) FROM silver GROUP BY disposition").fetchall()}
            fingerprint = con.execute("SELECT count(*),bit_xor(hash(line_id)),sum(line_amount) FROM fact_invoice_line").fetchone()
            run.update(transform_export_seconds=round(time.perf_counter()-start,6), metrics=metrics, counts=counts, accepted_identity_fingerprint=list(fingerprint), input_sha256=provenance[key], workbook_sha256=provenance["source"]["workbook_sha256"])
            pointer = DATA / "serving" / f"{profile}.json"
            if pointer.exists():
                spark_folder = DATA / "serving" / "snapshots" / read_json(pointer)["snapshot_id"]
                con.execute(f"CREATE VIEW spark_fact AS SELECT * FROM read_parquet('{(spark_folder/'fact_invoice_line').as_posix()}/*.parquet')")
                # EXCEPT ALL checks both identities and all monetary values, not just a sum.
                difference = con.execute("SELECT count(*) FROM ((SELECT line_id,line_amount,quantity FROM fact_invoice_line EXCEPT ALL SELECT line_id,line_amount,quantity FROM spark_fact) UNION ALL (SELECT line_id,line_amount,quantity FROM spark_fact EXCEPT ALL SELECT line_id,line_amount,quantity FROM fact_invoice_line))").fetchone()[0]
                if difference:
                    raise AssertionError(f"Spark/baseline logical record difference: {difference}")
                run.update(spark_snapshot_id=spark_folder.name, equivalence="all accepted identities, quantities and exact line amounts match (EXCEPT ALL both directions)")
            atomic_json(output/"manifest.json", {**run,"state":"passed"})
            atomic_json(ROOT/"evidence"/f"{profile}-baseline.json", {**run,"state":"passed"})
            return run
        finally:
            con.close()
