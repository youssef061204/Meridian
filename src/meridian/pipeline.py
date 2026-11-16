from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb

from .common import DATA, ROOT, RUNTIME, atomic_json, now, read_json, run_record, sha256


def spark_session():
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession
    builder = (SparkSession.builder.master("local[2]").appName("MERIDIAN")
        .config("spark.driver.memory", "768m")
        .config("spark.driver.maxResultSize", "128m")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.default.parallelism", "2")
        .config("spark.sql.session.timeZone", "Europe/London")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.jars.repositories", "https://repo.maven.apache.org/maven2")
        .config("spark.databricks.delta.snapshotPartitions", "2"))
    spark = configure_spark_with_delta_pip(builder, extra_packages=["org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.7"]).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    return spark


def sql(spark, name):
    return spark.sql((ROOT / "sql" / name).read_text(encoding="utf-8"))


def smoke():
    with run_record("spark_delta_smoke") as run:
        spark = spark_session()
        try:
            target = DATA / "smoke" / "delta"
            spark.range(3).write.format("delta").mode("overwrite").save(str(target))
            result = spark.read.format("delta").load(str(target)).groupBy().sum("id").first()[0]
            assert result == 3
            run.update(spark_version=spark.version, delta_version="3.3.3", scala_version=spark.sparkContext._jvm.scala.util.Properties.versionNumberString(), java_version=spark.sparkContext._jvm.java.lang.System.getProperty("java.version"), rows=3, sum=result, kafka_connector_class_loaded=str(spark.sparkContext._jvm.java.lang.Class.forName("org.apache.spark.sql.kafka010.KafkaSourceProvider", True, spark.sparkContext._jvm.java.lang.Thread.currentThread().getContextClassLoader())))
            atomic_json(ROOT / "evidence" / "spark-smoke.json", {**run, "state":"passed"})
        finally:
            spark.stop()


def load_input(spark, profile):
    if profile == "fixture":
        import pyarrow as pa
        import pyarrow.parquet as pq
        from .source import SCHEMA, text_value
        data = read_json(ROOT / "tests" / "fixtures" / "lines.json")
        rows = data["deliveries"] if isinstance(data, dict) else data
        rows = [{**{k: (v if k == 'source_row' else text_value(v)) for k,v in r.items()}, "ingested_at": "2026-11-16T00:00:00Z", "conversion_version": "fixture-v1"} for r in rows]
        path = DATA / "fixtures" / "lines.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path)
    else:
        path = DATA / "converted" / ("demo.parquet" if profile == "demo" else "historical.parquet")
    return spark.read.parquet(str(path))


def metrics_query(table="fact_invoice_line"):
    return f"""SELECT coalesce(sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END),0) AS gross_positive_gbp,
      coalesce(sum(CASE WHEN line_amount<0 THEN line_amount ELSE 0 END),0) AS negative_adjustments_gbp,
      coalesce(sum(line_amount),0) AS net_recorded_gbp,
      coalesce(sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END),0) AS positive_units,
      coalesce(sum(CASE WHEN customer_id IS NOT NULL THEN 1 ELSE 0 END),0) AS known_customer_rows FROM {table}"""


def batch(profile="demo", fail_at=None):
    from pyspark.sql import functions as F
    from delta.tables import DeltaTable
    mode = "synthetic" if profile == "fixture" else "historical"
    with run_record("batch", profile=profile, mode=mode) as run:
        spark = spark_session()
        try:
            source = load_input(spark, profile)
            source = source.withColumn("line_id", F.sha2(F.concat_ws("|", "source_sha256", "source_sheet", F.col("source_row").cast("string")), 256))
            lake = DATA / "lake" / mode / profile
            bronze_path = lake / "bronze"
            source.withColumn("run_id", F.lit(run["run_id"])).write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(str(bronze_path))
            bronze = spark.read.format("delta").load(str(bronze_path))
            deliveries = bronze.count()
            bronze.dropDuplicates(["line_id"]).createOrReplaceTempView("source_lines")
            silver = sql(spark, "silver.sql")
            silver_path = lake / "silver"
            silver.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(str(silver_path))
            silver = spark.read.format("delta").load(str(silver_path))
            source_rows = silver.count()
            reconciliation = [r.asDict() for r in silver.groupBy("disposition", "classification").agg(F.count("*").alias("rows"), F.sum("parsed_amount").alias("parsed_amount_gbp")).collect()]
            counts = {d: sum(r["rows"] for r in reconciliation if r["disposition"] == d) for d in ["accepted", "quarantined", "excluded"]}
            assert sum(counts.values()) == source_rows
            fact = silver.filter("disposition = 'accepted'")
            fact.createOrReplaceTempView("fact_invoice_line")
            tables = {"fact_invoice_line": fact, "daily_sales": sql(spark, "daily_sales.sql"),
                "product_performance": spark.sql("SELECT stock_code, max(description) AS description, sum(line_amount) AS net_recorded_gbp, sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS positive_units FROM fact_invoice_line GROUP BY stock_code"),
                "country_performance": spark.sql("SELECT country, sum(line_amount) AS net_recorded_gbp, count(*) AS rows FROM fact_invoice_line GROUP BY country")}
            versions = {"bronze": DeltaTable.forPath(spark, str(bronze_path)).history(1).first()["version"], "silver": DeltaTable.forPath(spark, str(silver_path)).history(1).first()["version"]}
            for name, frame in tables.items():
                path = lake / name
                frame.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(str(path))
                versions[name] = DeltaTable.forPath(spark, str(path)).history(1).first()["version"]
            snapshot_dir = DATA / "serving" / "snapshots" / run["run_id"]
            snapshot_dir.mkdir(parents=True, exist_ok=False)
            checksums = {}
            # Each read explicitly pins a Delta version; unrelated commits are not a cross-table transaction.
            for name in tables:
                versioned = spark.read.format("delta").option("versionAsOf", versions[name]).load(str(lake / name))
                versioned.coalesce(1).write.mode("errorifexists").parquet(str(snapshot_dir / name))
                for path in (snapshot_dir / name).glob("*.parquet"):
                    checksums[str(path.relative_to(snapshot_dir)).replace('\\', '/')] = sha256(path)
            if fail_at == "after-export":
                raise RuntimeError("Controlled failure after export; active snapshot untouched")
            con = duckdb.connect()
            try:
                for name in tables:
                    con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{(snapshot_dir / name).as_posix()}/*.parquet')")
                metric_values = dict(zip([d[0] for d in con.execute(metrics_query()).description], con.execute(metrics_query()).fetchone()))
                accepted = con.execute("SELECT count(*),count(DISTINCT line_id),min(date),max(date) FROM fact_invoice_line").fetchone()
                assert accepted[0] == accepted[1] == counts["accepted"]
                daily_net = con.execute("SELECT sum(net_recorded_gbp) FROM daily_sales").fetchone()[0]
                assert daily_net == metric_values["net_recorded_gbp"]
                assert metric_values["gross_positive_gbp"] + metric_values["negative_adjustments_gbp"] == metric_values["net_recorded_gbp"]
                for table in ["product_performance", "country_performance"]:
                    assert con.execute(f"SELECT sum(net_recorded_gbp) FROM {table}").fetchone()[0] == daily_net
                if profile == "fixture":
                    assert str(metric_values["net_recorded_gbp"]) == "35.00", metric_values
                    assert counts == {"accepted": 8, "quarantined": 7, "excluded": 1}, counts
                manifest = dict(id=run["run_id"], published_at=now(), mode=mode, profile=profile,
                    source_sha256=source.select("source_sha256").first()[0], source_rows=source_rows,
                    accepted_rows=counts["accepted"], quarantined_rows=counts["quarantined"], excluded_rows=counts["excluded"], repeat_deliveries=deliveries-source_rows, received_deliveries=deliveries,
                    date_min=str(accepted[2]), date_max=str(accepted[3]), delta_versions=versions,
                    delta_root=str(lake.relative_to(ROOT)).replace('\\', '/'), metrics=metric_values,
                    reconciliation=reconciliation, checksums=checksums, validation="passed", sql_sha256={n:sha256(ROOT/"sql"/n) for n in ["silver.sql","daily_sales.sql"]},
                    limitations=["Recorded merchandise invoice value in GBP; not audited revenue or settled cash.", "Missing customer IDs remain in merchandise totals.", "Source civil timestamps have no recorded timezone offset."])
            finally:
                con.close()
            atomic_json(snapshot_dir / "manifest.json", manifest)
            if fail_at == "before-pointer":
                raise RuntimeError("Controlled failure before publication pointer; active snapshot untouched")
            atomic_json(DATA / "serving" / f"{profile}.json", {"snapshot_id": run["run_id"]})
            if profile != "fixture":
                atomic_json(DATA / "serving" / "current.json", {"snapshot_id": run["run_id"]})
            run.update(snapshot_id=run["run_id"], source_rows=source_rows, counts=counts, metrics=metric_values, delta_versions=versions)
            atomic_json(ROOT / "evidence" / f"{profile}-batch.json", {**run, "state":"passed", "manifest": manifest})
            return manifest
        finally:
            spark.stop()
