"""Kafka replay consumer, late-arrival backfill, validated publication and safe reset.

Write semantics (foreachBatch is at-least-once):
  * Spark re-runs a micro-batch with the SAME batch_id and the same Kafka offsets when a
    run dies before the checkpoint commit. Every append below therefore carries the Delta
    idempotent-write options txnAppId=<generation app id>, txnVersion=<batch_id>; Delta
    skips a write whose (appId, version) it has already committed.
  * Silver and the revision ledger use MERGE keyed on source identity (+ revision). Applying
    the same rows twice is a no-op, so a retry after a partial batch converges.
  * Kafka + Spark do not by themselves give end-to-end exactly-once *business* results;
    the identity key, revision rule and idempotent writes do, and the recovery drill tests them.

Event-time policy: the watermark is the maximum valid event time committed by PREVIOUS
micro-batches minus `allowed_lateness_hours`. New rows older than the watermark are not
dropped (Spark's own watermark would silently drop them from stateful aggregates); they
are written to the durable late_arrivals table with status 'pending' and applied by an
explicit backfill. Publication is blocked while financial rows are pending.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from .common import DATA, ROOT, atomic_json, now, read_json, sha256
from .replay import STATE, config

ENVELOPE = ("schema_version INT, mode STRING, revision INT, source_identity STRING, "
            "source STRUCT<sha256: STRING, sheet: STRING, row: BIGINT>, event_time STRING, replay_time STRING, "
            "delivery_id STRING, producer_run_id STRING, incident STRING, "
            "payload STRUCT<invoice: STRING, stock_code: STRING, description: STRING, quantity: STRING, "
            "unit_price: STRING, customer_id: STRING, country: STRING, channel: STRING, qty: STRING>")
SUPPORTED_SCHEMAS = (1, 2)   # v2 = v1 + optional payload.channel (additive, explicitly supported)


def lake(cfg, name=""):
    return ROOT / cfg["lake"] / name


def gen_state(cfg):
    folder = STATE / cfg["generation"]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, default=str) + "\n")
        out.flush()
        os.fsync(out.fileno())


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _crash(point, batch_id, ordinal):
    """Fault injection: MERIDIAN_CRASH=after-bronze:2 kills the process inside batch 2;
    'after-silver:first' kills it in the first batch this process handles."""
    spec = os.environ.get("MERIDIAN_CRASH", "")
    if spec and spec in (f"{point}:{batch_id}", f"{point}:first" if ordinal == 1 else None):
        print(f"FAULT INJECTION: hard exit at {point} in batch {batch_id}", flush=True)
        os._exit(86)


def _delta_exists(spark, path):
    from delta.tables import DeltaTable
    return DeltaTable.isDeltaTable(spark, str(path))


def _txn_append(frame, path, app_id, version):
    (frame.write.format("delta").mode("append").option("txnAppId", app_id).option("txnVersion", version)
     .option("mergeSchema", "true").save(str(path)))


def _version(spark, path):
    from delta.tables import DeltaTable
    return DeltaTable.forPath(spark, str(path)).history(1).first()["version"]


# ---------------------------------------------------------------- envelope parsing

def parse_envelopes(frame):
    from pyspark.sql import functions as F
    e = F.from_json(F.col("raw_value"), ENVELOPE)
    flat = frame.withColumn("e", e).select(
        "kafka_topic", "kafka_partition", "kafka_offset", "kafka_timestamp", "kafka_key", "raw_value",
        F.col("e.schema_version").alias("schema_version"), F.col("e.mode").alias("mode"),
        F.col("e.revision").alias("revision"), F.col("e.source_identity").alias("source_identity"),
        F.col("e.source.sha256").alias("source_sha256"), F.col("e.source.sheet").alias("source_sheet"),
        F.col("e.source.row").alias("source_row"), F.col("e.event_time").alias("event_time"),
        F.col("e.replay_time").alias("replay_time"), F.col("e.delivery_id").alias("delivery_id"),
        F.col("e.producer_run_id").alias("producer_run_id"), F.col("e.incident").alias("incident"),
        *[F.col(f"e.payload.{name}").alias(name) for name in
          ["invoice", "stock_code", "description", "quantity", "unit_price", "customer_id", "country", "channel"]],
        F.col("e.payload.qty").alias("unsupported_qty_field"))
    required = ["source_identity", "source_sha256", "source_sheet", "source_row", "event_time", "revision",
                "invoice", "stock_code", "quantity", "unit_price"]
    expected_identity = F.sha2(F.concat_ws("|", "source_sha256", "source_sheet", F.col("source_row").cast("string")), 256)
    status = (F.when(F.col("schema_version").isNull() & F.col("source_identity").isNull(), "malformed_json")
              .when(~F.col("schema_version").isin(*SUPPORTED_SCHEMAS), "incompatible_schema")
              .when(F.greatest(*[F.col(c).isNull().cast("int") for c in required]) == 1, "missing_required_field")
              .when(F.col("source_identity") != expected_identity, "identity_mismatch")
              .otherwise("valid"))
    nul = F.lit("\u0000")
    payload_hash = F.sha2(F.concat_ws("|", *[F.coalesce(F.col(c).cast("string"), nul) for c in
                          ["invoice", "stock_code", "description", "quantity", "event_time", "unit_price",
                           "customer_id", "country", "source_sha256", "source_sheet", "source_row"]]), 256)
    return (flat.withColumn("envelope_status", status)
            .withColumn("payload_sha256", F.when(F.col("envelope_status") == "valid", payload_hash))
            .withColumn("event_ts", F.expr("try_cast(event_time AS TIMESTAMP_NTZ)"))  # civil time, no session-timezone shift
            .withColumn("unsafe_amount", F.expr("try_cast(quantity AS DOUBLE) * try_cast(unit_price AS DOUBLE)")))


def to_silver(spark, rows, ingested_at):
    """Apply the SAME version-controlled silver.sql used by the batch pipeline."""
    from pyspark.sql import functions as F
    source_lines = rows.select(
        "invoice", "stock_code", "description", "quantity", "event_time", "unit_price", "customer_id", "country",
        "source_sha256", "source_sheet", F.lit(ingested_at).alias("ingested_at"),
        F.concat(F.lit("replay-envelope-v"), F.col("schema_version").cast("string")).alias("conversion_version"),
        "source_row", F.col("source_identity").alias("line_id"))
    source_lines.createOrReplaceTempView("source_lines")
    silver = spark.sql((ROOT / "sql" / "silver.sql").read_text(encoding="utf-8"))
    extras = rows.select(F.col("source_identity").alias("line_id"), "revision", "mode", "schema_version", "channel",
                         "replay_time", "kafka_topic", "kafka_partition", "kafka_offset", "payload_sha256", "first_batch_id")
    return silver.join(extras, "line_id", "inner")


def apply_rows(spark, cfg, rows, batch_label, ingested_at):
    """Insert revisions, MERGE Silver to the highest revision, update affected daily totals."""
    from delta.tables import DeltaTable
    from pyspark.sql import Window
    from pyspark.sql import functions as F
    revisions_path, silver_path, daily_path = lake(cfg, "revisions"), lake(cfg, "silver_current"), lake(cfg, "daily_sales")
    rows = rows.withColumn("first_batch_id", F.lit(batch_label))
    ledger = rows.select(F.col("source_identity").alias("line_id"), "revision", "payload_sha256", "first_batch_id", "mode",
                         "incident", "schema_version", "event_time", "replay_time", "invoice", "stock_code", "description",
                         "quantity", "unit_price", "customer_id", "country", "channel", "source_sha256", "source_sheet",
                         "source_row", "kafka_topic", "kafka_partition", "kafka_offset")
    if not _delta_exists(spark, revisions_path):
        ledger.limit(0).write.format("delta").save(str(revisions_path))
    (DeltaTable.forPath(spark, str(revisions_path)).alias("t").merge(ledger.alias("s"), "t.line_id = s.line_id AND t.revision = s.revision")
     .whenNotMatchedInsertAll().execute())
    latest = rows.withColumn("_r", F.row_number().over(Window.partitionBy("source_identity").orderBy(F.desc("revision")))).filter("_r = 1").drop("_r")
    updates = to_silver(spark, latest, ingested_at).withColumn("applied_at", F.lit(ingested_at)).withColumn("applied_by", F.lit(batch_label))
    updates = updates.persist()
    if not _delta_exists(spark, silver_path):
        updates.limit(0).write.format("delta").save(str(silver_path))
    existing = spark.read.format("delta").load(str(silver_path))
    affected = (updates.select("date").union(existing.join(updates.select("line_id"), "line_id").select("date"))
                .where("date IS NOT NULL").distinct())
    affected_dates = [r["date"] for r in affected.collect()]
    (DeltaTable.forPath(spark, str(silver_path)).alias("t").merge(updates.alias("s"), "t.line_id = s.line_id")
     .whenMatchedUpdateAll(condition="s.revision > t.revision").whenNotMatchedInsertAll().execute())
    spark.read.format("delta").load(str(silver_path)).filter("disposition = 'accepted'").createOrReplaceTempView("fact_current")
    spark.createDataFrame([(d,) for d in affected_dates], "date DATE").createOrReplaceTempView("affected_dates")
    if not _delta_exists(spark, daily_path):
        spark.sql("SELECT * FROM fact_current").limit(0).createOrReplaceTempView("fact_invoice_line")
        spark.sql((ROOT / "sql" / "daily_sales.sql").read_text(encoding="utf-8")).write.format("delta").save(str(daily_path))
    spark.sql((ROOT / "sql" / "replay" / "daily_sales_incremental.sql").read_text(encoding="utf-8").format(daily_path=daily_path.as_posix()))
    counts = updates.groupBy().agg(F.count("*").alias("n"), F.sum(F.when(F.col("revision") > 1, 1).otherwise(0)).alias("corrections")).first()
    updates.unpersist()
    return {"applied_rows": counts["n"] or 0, "applied_corrections": counts["corrections"] or 0,
            "affected_dates": sorted(str(d) for d in affected_dates)}


# ---------------------------------------------------------------- micro-batch

class BatchProcessor:
    def __init__(self, spark, cfg, consumer_run_id):
        self.spark, self.cfg, self.consumer_run_id = spark, cfg, consumer_run_id
        self.batches = []
        self.ordinal = 0

    def __call__(self, frame, batch_id):
        from pyspark.sql import Window
        from pyspark.sql import functions as F
        # foreachBatch frames belong to a cloned session; temp views must be created and read there.
        spark, cfg = frame.sparkSession, self.cfg
        started, processed_at = time.perf_counter(), now()
        app_id = cfg["app_id"]
        raw = frame.selectExpr("topic AS kafka_topic", "partition AS kafka_partition", "offset AS kafka_offset",
                               "timestamp AS kafka_timestamp", "CAST(key AS STRING) AS kafka_key", "CAST(value AS STRING) AS raw_value")
        bronze = (parse_envelopes(raw).withColumn("batch_id", F.lit(batch_id)).withColumn("processed_at", F.lit(processed_at))
                  .withColumn("consumer_run_id", F.lit(self.consumer_run_id)).withColumn("generation", F.lit(cfg["generation"]))).persist()
        deliveries = bronze.count()
        if deliveries == 0:
            return
        # 1. Bronze: every received delivery, including malformed ones, with its Kafka coordinates.
        _txn_append(bronze.drop("event_ts"), lake(cfg, "bronze"), app_id, batch_id)
        self.ordinal += 1
        _crash("after-bronze", batch_id, self.ordinal)

        metrics_path = lake(cfg, "batch_metrics")
        prior_max = None
        if _delta_exists(spark, metrics_path):
            prior_max = (spark.read.format("delta").load(str(metrics_path)).filter(F.col("batch_id") < batch_id)
                         .agg(F.max(F.expr("try_cast(max_event_time AS TIMESTAMP_NTZ)"))).first()[0])
        lateness = timedelta(hours=cfg["allowed_lateness_hours"])
        watermark = (datetime.fromisoformat(str(prior_max)) - lateness) if prior_max else None

        valid = bronze.filter("envelope_status = 'valid'")
        stored = None
        if _delta_exists(spark, lake(cfg, "revisions")):
            stored = (spark.read.format("delta").load(str(lake(cfg, "revisions")))
                      .select(F.col("line_id").alias("source_identity"), "revision", F.col("payload_sha256").alias("stored_sha256"),
                              F.col("first_batch_id").alias("stored_batch")))
            # A ledger row written by a failed earlier attempt of THIS batch is not a prior delivery.
            stored = stored.filter(F.col("stored_batch") != f"stream-{batch_id}")
            valid = valid.join(stored, ["source_identity", "revision"], "left")
        else:
            valid = valid.withColumn("stored_sha256", F.lit(None).cast("string"))
        group = Window.partitionBy("source_identity", "revision")
        ordered = group.orderBy("kafka_topic", "kafka_partition", "kafka_offset")
        valid = (valid.withColumn("_rn", F.row_number().over(ordered))
                 .withColumn("_hashes", F.size(F.collect_set("payload_sha256").over(group))))
        delivery_class = (F.when(F.col("stored_sha256").isNotNull() & (F.col("stored_sha256") == F.col("payload_sha256")), "cross_batch_duplicate")
                          .when(F.col("stored_sha256").isNotNull(), "identity_payload_conflict")
                          .when(F.col("_hashes") > 1, "identity_payload_conflict")
                          .when(F.col("_rn") > 1, "within_batch_duplicate").otherwise("new"))
        valid = valid.withColumn("delivery_class", delivery_class)
        lateness_class = (F.when(F.col("event_ts") < F.expr(f"TIMESTAMP_NTZ '{watermark}'"), "late_beyond_window")
                          .when(F.col("event_ts") < F.expr(f"TIMESTAMP_NTZ '{prior_max}'"), "out_of_order_within_window")
                          .otherwise("in_order")) if prior_max else F.lit("in_order")
        valid = valid.withColumn("lateness_class", lateness_class).persist()

        # 2. Quarantine: rejected envelopes and identity conflicts (never silently resolved).
        rejected = (bronze.filter("envelope_status != 'valid'").withColumn("reason", F.col("envelope_status"))
                    .unionByName(valid.filter("delivery_class = 'identity_payload_conflict'").withColumn("reason", F.lit("identity_payload_conflict"))
                                 .select(*bronze.columns, "reason"))
                    .select("kafka_topic", "kafka_partition", "kafka_offset", "raw_value", "source_identity", "revision", "mode",
                            "incident", "event_time", "reason", F.col("unsafe_amount").alias("parsed_amount_hint"), "batch_id", "processed_at"))
        _txn_append(rejected, lake(cfg, "quarantine"), app_id, batch_id)

        new = valid.filter("delivery_class = 'new'")
        late = new.filter("lateness_class = 'late_beyond_window'")
        on_time = new.filter("lateness_class != 'late_beyond_window'")
        # 3. Late arrivals: durable, pending backfill. Nothing financial is dropped.
        late_rows = late.select(*[c for c in bronze.columns if c != "event_ts"]).withColumn(
            "watermark", F.lit(str(watermark))).withColumn("status", F.lit("pending")).withColumn("applied_run_id", F.lit(None).cast("string"))
        _txn_append(late_rows, lake(cfg, "late_arrivals"), app_id, batch_id)
        applied = apply_rows(spark, cfg, on_time, f"stream-{batch_id}", processed_at) if on_time.limit(1).count() else {"applied_rows": 0, "applied_corrections": 0, "affected_dates": []}

        tally = {r["k"]: r["n"] for r in valid.groupBy(F.col("delivery_class").alias("k")).count().withColumnRenamed("count", "n").collect()}
        lateness_tally = {r["k"]: r["n"] for r in new.groupBy(F.col("lateness_class").alias("k")).count().withColumnRenamed("count", "n").collect()}
        status_tally = {r["k"]: r["n"] for r in bronze.groupBy(F.col("envelope_status").alias("k")).count().withColumnRenamed("count", "n").collect()}
        mode_tally = {r["k"]: r["n"] for r in bronze.groupBy(F.coalesce("mode", F.lit("unknown")).alias("k")).count().withColumnRenamed("count", "n").collect()}
        extremes = valid.agg(F.max("event_ts").alias("max_et"), F.min("replay_time").alias("min_rt"), F.max("replay_time").alias("max_rt"),
                             F.sum(F.when(F.col("schema_version") == 2, 1).otherwise(0)).alias("v2")).first()
        max_event = max([t for t in [extremes["max_et"], prior_max] if t is not None], default=None)
        seconds = time.perf_counter() - started
        lag = None
        if extremes["min_rt"]:
            lag = (datetime.fromisoformat(processed_at) - datetime.fromisoformat(extremes["min_rt"])).total_seconds()
        record = {"generation": cfg["generation"], "batch_id": batch_id, "source": "stream", "consumer_run_id": self.consumer_run_id,
                  "processed_at": processed_at, "deliveries": deliveries, "by_mode": mode_tally, "envelope_status": status_tally,
                  "new_revisions": tally.get("new", 0), "within_batch_duplicates": tally.get("within_batch_duplicate", 0),
                  "cross_batch_duplicates": tally.get("cross_batch_duplicate", 0), "identity_conflicts": tally.get("identity_payload_conflict", 0),
                  "rejected_envelopes": deliveries - status_tally.get("valid", 0), "additive_schema_v2": extremes["v2"] or 0,
                  "late_routed_to_backfill": lateness_tally.get("late_beyond_window", 0),
                  "out_of_order_within_window": lateness_tally.get("out_of_order_within_window", 0),
                  "max_event_time": str(max_event) if max_event else None, "prior_max_event_time": str(prior_max) if prior_max else None,
                  "watermark": str(watermark) if watermark else None, "allowed_lateness_hours": cfg["allowed_lateness_hours"],
                  "min_replay_time": extremes["min_rt"], "max_replay_time": extremes["max_rt"],
                  "processing_lag_seconds": round(lag, 3) if lag is not None else None,
                  "processing_seconds": round(seconds, 3), "deliveries_per_second": round(deliveries / seconds, 1) if seconds else None,
                  **applied}
        metrics_row = spark.createDataFrame([(batch_id, str(max_event) if max_event else None, json.dumps(record, default=str))],
                                            "batch_id LONG, max_event_time STRING, record STRING")
        _txn_append(metrics_row, metrics_path, app_id, batch_id)
        append_jsonl(gen_state(cfg) / "batches.jsonl", record)
        self.batches.append(record)
        bronze.unpersist(); valid.unpersist()
        _crash("after-silver", batch_id, self.ordinal)


def consume(spark, cfg=None, timeout=None):
    """Drain everything currently in both topics (availableNow) through foreachBatch."""
    import uuid
    cfg = cfg or config()
    consumer_run_id = "consumer-" + uuid.uuid4().hex[:8]
    processor = BatchProcessor(spark, cfg, consumer_run_id)
    started = time.perf_counter()
    stream = (spark.readStream.format("kafka").option("kafka.bootstrap.servers", os.environ.get("MERIDIAN_KAFKA_BOOTSTRAP", "kafka:19092"))
              .option("subscribe", f"{cfg['replay_topic']},{cfg['incident_topic']}").option("startingOffsets", "earliest")
              .option("maxOffsetsPerTrigger", cfg["max_offsets_per_trigger"]).option("failOnDataLoss", "true").load())
    query = (stream.writeStream.foreachBatch(processor).option("checkpointLocation", str(ROOT / cfg["checkpoint"]))
             .trigger(availableNow=True).start())
    query.awaitTermination(timeout)
    if query.exception():
        raise RuntimeError(str(query.exception()))
    return {"consumer_run_id": consumer_run_id, "batches": processor.batches, "seconds": round(time.perf_counter() - started, 3)}


# ---------------------------------------------------------------- backfill

def backfill(spark, cfg=None, force=False):
    """Apply pending late arrivals (and, with force, re-apply already-applied ones: must be a no-op)."""
    import uuid
    from delta.tables import DeltaTable
    from pyspark.sql import functions as F
    cfg = cfg or config()
    run_id = "backfill-" + uuid.uuid4().hex[:8]
    path = lake(cfg, "late_arrivals")
    started = time.perf_counter()
    if not _delta_exists(spark, path):
        return {"backfill_run_id": run_id, "applied_rows": 0, "affected_dates": [], "note": "no late arrivals table"}
    late = spark.read.format("delta").load(str(path))
    selected = late.filter("status = 'pending'" if not force else "status IN ('pending','applied')")
    selected = selected.dropDuplicates(["source_identity", "revision"]).persist()
    count = selected.count()
    before = net_accepted(spark, cfg)
    result = {"applied_rows": 0, "applied_corrections": 0, "affected_dates": []}
    if count:
        result = apply_rows(spark, cfg, selected.drop("status", "watermark", "applied_run_id"), run_id, now())
        keys = selected.select("source_identity", "revision")
        (DeltaTable.forPath(spark, str(path)).alias("t").merge(keys.alias("s"), "t.source_identity = s.source_identity AND t.revision = s.revision AND t.status = 'pending'")
         .whenMatchedUpdate(set={"status": F.lit("applied"), "applied_run_id": F.lit(run_id)}).execute())
    selected.unpersist()
    after = net_accepted(spark, cfg)
    record = {"backfill_run_id": run_id, "force": force, "selected_late_rows": count, **result,
              "net_before": str(before), "net_after": str(after), "net_change": str(after - before),
              "seconds": round(time.perf_counter() - started, 3), "finished_at": now()}
    append_jsonl(gen_state(cfg) / "backfills.jsonl", record)
    return record


def net_accepted(spark, cfg):
    path = lake(cfg, "silver_current")
    if not _delta_exists(spark, path):
        return Decimal("0.00")
    value = spark.read.format("delta").load(str(path)).filter("disposition = 'accepted'").groupBy().sum("line_amount").first()[0]
    return value if value is not None else Decimal("0.00")


# ---------------------------------------------------------------- publication

class PublicationBlocked(RuntimeError):
    pass


def publish(spark, cfg=None, fail_at=None):
    """Multi-stage publication. The replay pointer moves only after every stage validates."""
    from pyspark.sql import functions as F
    cfg = cfg or config()
    attempt = {"attempted_at": now(), "generation": cfg["generation"], "fail_at": fail_at, "stages": []}
    started = time.perf_counter()
    snapshot_id = "replay-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3]
    folder = DATA / "serving" / "snapshots" / snapshot_id
    log = gen_state(cfg) / "publications.jsonl"

    def stage(name, **detail):
        attempt["stages"].append({"stage": name, "at": now(), **detail})

    try:
        late_path = lake(cfg, "late_arrivals")
        pending = {"rows": 0, "unsafe_amount_hint": 0.0}
        if _delta_exists(spark, late_path):
            row = spark.read.format("delta").load(str(late_path)).filter("status = 'pending'").agg(
                F.count("*").alias("rows"), F.sum("unsafe_amount").alias("amount")).first()
            pending = {"rows": row["rows"], "unsafe_amount_hint": float(row["amount"] or 0)}
        stage("precondition_pending_backfill", **pending)
        if pending["rows"]:
            raise PublicationBlocked(f"{pending['rows']} late financial rows await backfill; the last validated snapshot stays current")
        names = ["silver_current", "daily_sales", "quarantine", "revisions", "batch_metrics"]
        versions = {n: _version(spark, lake(cfg, n)) for n in names if _delta_exists(spark, lake(cfg, n))}
        stage("pinned_delta_versions", versions=versions)
        silver = spark.read.format("delta").option("versionAsOf", versions["silver_current"]).load(str(lake(cfg, "silver_current")))
        fact = silver.filter("disposition = 'accepted'").drop("applied_at", "applied_by")
        tables = {
            "fact_invoice_line": fact,
            "daily_sales": spark.read.format("delta").option("versionAsOf", versions["daily_sales"]).load(str(lake(cfg, "daily_sales"))),
            "product_performance": fact.groupBy("stock_code").agg(F.max("description").alias("description"), F.sum("line_amount").alias("net_recorded_gbp"),
                                                                  F.sum(F.when(F.col("quantity") > 0, F.col("quantity")).otherwise(0)).alias("positive_units")),
            "country_performance": fact.groupBy("country").agg(F.sum("line_amount").alias("net_recorded_gbp"), F.count("*").alias("rows")),
            "business_quarantine": silver.filter("disposition != 'accepted'").select("line_id", "classification", "disposition", "parsed_amount", "revision", "mode"),
            "envelope_quarantine": spark.read.format("delta").option("versionAsOf", versions["quarantine"]).load(str(lake(cfg, "quarantine"))) if "quarantine" in versions else None,
            "revisions": spark.read.format("delta").option("versionAsOf", versions["revisions"]).load(str(lake(cfg, "revisions"))),
        }
        folder.mkdir(parents=True, exist_ok=False)
        checksums = {}
        for index, (name, frame) in enumerate(tables.items()):
            if frame is None:
                continue
            frame.coalesce(1).write.mode("errorifexists").parquet(str(folder / name))
            for part in (folder / name).glob("*.parquet"):
                checksums[part.relative_to(folder).as_posix()] = sha256(part)
            if fail_at == "after-export" and index == 1:
                stage("export", tables_written=list(tables)[:2], interrupted=True)
                raise RuntimeError("Controlled interruption after exporting 2 tables; pointer untouched")
        stage("export", tables_written=[n for n, f in tables.items() if f is not None])
        validation = validate_replay_snapshot(folder, cfg)
        stage("validation", **{k: v for k, v in validation.items() if k != "equivalence_detail"})
        if fail_at == "before-pointer":
            raise RuntimeError("Controlled interruption before pointer swap; pointer untouched")
        manifest = {"id": snapshot_id, "published_at": now(), "mode": "replay", "profile": cfg["profile"],
                    "generation": cfg["generation"], "delta_versions": versions, "checksums": checksums,
                    "tables": [n for n, f in tables.items() if f is not None], "validation": "passed", **validation,
                    "sql_sha256": {"silver.sql": sha256(ROOT / "sql" / "silver.sql"), "daily_sales.sql": sha256(ROOT / "sql" / "daily_sales.sql"),
                                   "replay/daily_sales_incremental.sql": sha256(ROOT / "sql" / "replay" / "daily_sales_incremental.sql")},
                    "limitations": ["Replay re-emits historical source rows through local Kafka on an accelerated clock; it is not a live retailer integration.",
                                    "Revisions above 1 are seeded synthetic corrections for the reliability demonstration, not observed source changes.",
                                    "Recorded merchandise invoice value in GBP; not audited revenue or settled cash."]}
        atomic_json(folder / "manifest.json", manifest)
        atomic_json(DATA / "serving" / "replay.json", {"snapshot_id": snapshot_id, "generation": cfg["generation"]})
        attempt.update(state="passed", snapshot_id=snapshot_id, seconds=round(time.perf_counter() - started, 3))
        append_jsonl(log, attempt)
        return manifest
    except PublicationBlocked as exc:
        attempt.update(state="blocked", reason=str(exc), seconds=round(time.perf_counter() - started, 3))
        append_jsonl(log, attempt)
        raise
    except Exception as exc:
        attempt.update(state="failed", reason=f"{type(exc).__name__}: {exc}", partial_folder=str(folder.relative_to(ROOT)) if folder.exists() else None,
                       seconds=round(time.perf_counter() - started, 3))
        append_jsonl(log, attempt)
        raise


def validate_replay_snapshot(folder, cfg):
    """DuckDB checks over the exported files, plus exact equivalence against the batch snapshot."""
    batch_pointer = DATA / "serving" / f"{'demo' if cfg['profile'] == 'demo' else 'full'}.json"
    batch_folder = DATA / "serving" / "snapshots" / read_json(batch_pointer)["snapshot_id"]
    con = duckdb.connect()
    try:
        con.execute("SET threads=2"); con.execute("SET memory_limit='512MB'")
        for name in ["fact_invoice_line", "daily_sales", "revisions"]:
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{(folder / name).as_posix()}/*.parquet')")
        con.execute(f"CREATE VIEW batch_fact AS SELECT * FROM read_parquet('{(batch_folder / 'fact_invoice_line').as_posix()}/*.parquet')")
        one = lambda q: con.execute(q).fetchone()
        rows, distinct = one("SELECT count(*), count(DISTINCT line_id) FROM fact_invoice_line")
        assert rows == distinct, "Duplicate line identity in replay fact"
        full_daily = ((ROOT / "sql" / "daily_sales.sql").read_text(encoding="utf-8"))
        mismatch = one(f"""WITH full_daily AS ({full_daily})
            SELECT count(*) FROM ((SELECT * FROM full_daily EXCEPT ALL SELECT * FROM daily_sales)
                                  UNION ALL (SELECT * FROM daily_sales EXCEPT ALL SELECT * FROM full_daily))""")[0]
        assert mismatch == 0, f"Incrementally maintained daily_sales differs from full recompute on {mismatch} rows"
        gross, adj, net = one("SELECT sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END), sum(CASE WHEN line_amount<0 THEN line_amount ELSE 0 END), sum(line_amount) FROM fact_invoice_line")
        assert gross + adj == net and one("SELECT sum(net_recorded_gbp) FROM daily_sales")[0] == net
        corrected = [r[0] for r in con.execute("SELECT DISTINCT line_id FROM revisions WHERE revision > 1").fetchall()]
        con.execute("CREATE TEMP TABLE corrected AS SELECT DISTINCT line_id FROM revisions WHERE revision > 1")
        missing = one("SELECT count(*), coalesce(sum(line_amount),0) FROM batch_fact WHERE line_id NOT IN (SELECT line_id FROM fact_invoice_line)")
        extra = one("SELECT count(*), coalesce(sum(line_amount),0) FROM fact_invoice_line WHERE line_id NOT IN (SELECT line_id FROM batch_fact)")
        uncorrected_diff = one("""SELECT count(*) FROM (
            (SELECT line_id,quantity,line_amount FROM fact_invoice_line WHERE line_id NOT IN (SELECT line_id FROM corrected)
              EXCEPT ALL SELECT line_id,quantity,line_amount FROM batch_fact WHERE line_id NOT IN (SELECT line_id FROM corrected))
            UNION ALL
            (SELECT line_id,quantity,line_amount FROM batch_fact WHERE line_id NOT IN (SELECT line_id FROM corrected)
                AND line_id IN (SELECT line_id FROM fact_invoice_line)
              EXCEPT ALL SELECT line_id,quantity,line_amount FROM fact_invoice_line WHERE line_id NOT IN (SELECT line_id FROM corrected)))""")[0]
        # Revision-1 payloads of corrected rows must equal the batch values (the original source row).
        rev1_mismatch = one("""SELECT count(*) FROM revisions r JOIN batch_fact b ON r.line_id = b.line_id
            WHERE r.revision = 1 AND r.line_id IN (SELECT line_id FROM corrected)
              AND (try_cast(r.quantity AS BIGINT) IS DISTINCT FROM b.quantity
                   OR round(try_cast(r.quantity AS DECIMAL(18,4)) * try_cast(r.unit_price AS DECIMAL(18,4)), 2) IS DISTINCT FROM b.line_amount)""")[0]
        correction_delta = one("""SELECT coalesce(sum(f.line_amount - b.line_amount), 0) FROM fact_invoice_line f JOIN batch_fact b USING (line_id)
            WHERE line_id IN (SELECT line_id FROM corrected)""")[0]
        batch_net = one("SELECT sum(line_amount) FROM batch_fact")[0]
        reconciled = missing[0] == 0 and extra[0] == 0 and uncorrected_diff == 0 and rev1_mismatch == 0
        assert batch_net + correction_delta - missing[1] + extra[1] == net, "Replay/batch monetary bridge does not balance"
        return {"accepted_rows": rows, "metrics": {"gross_positive_gbp": str(gross), "negative_adjustments_gbp": str(adj), "net_recorded_gbp": str(net)},
                "incremental_daily_equals_full_recompute": True,
                "batch_reference_snapshot": batch_folder.name, "batch_net_recorded_gbp": str(batch_net),
                "reconciliation": {"state": "reconciled" if reconciled else ("incomplete" if missing[0] else "discrepancy"),
                                   "missing_vs_batch_rows": missing[0], "missing_vs_batch_gbp": str(missing[1]),
                                   "extra_vs_batch_rows": extra[0], "extra_vs_batch_gbp": str(extra[1]),
                                   "uncorrected_value_differences": uncorrected_diff, "revision1_vs_batch_mismatches": rev1_mismatch,
                                   "synthetic_corrections": len(corrected), "correction_delta_gbp": str(correction_delta),
                                   "bridge": "batch net + synthetic correction delta - not-yet-received + extra = replay net"}}
    finally:
        con.close()


# ---------------------------------------------------------------- status, unsafe comparison, reset

def write_status(spark, cfg=None):
    """Persist the evidence the UI reads: trusted vs deliberately unsafe totals, pending backfill."""
    from pyspark.sql import functions as F
    cfg = cfg or config()
    status = {"generation": cfg["generation"], "profile": cfg["profile"], "computed_at": now()}
    if _delta_exists(spark, lake(cfg, "bronze")):
        b = spark.read.format("delta").load(str(lake(cfg, "bronze"))).agg(
            F.count("*").alias("deliveries"), F.countDistinct("kafka_topic", "kafka_partition", "kafka_offset").alias("distinct_offsets"),
            F.countDistinct("source_identity").alias("distinct_identities"),
            F.sum("unsafe_amount").alias("unsafe_sum"),
            F.sum(F.when(F.col("mode") == "synthetic", 1).otherwise(0)).alias("synthetic"),
            F.sum(F.when(F.col("envelope_status") != "valid", 1).otherwise(0)).alias("rejected")).first().asDict()
        status["bronze"] = {k: (float(v) if k == "unsafe_sum" and v is not None else v) for k, v in b.items()}
    trusted = net_accepted(spark, cfg)
    unsafe = Decimal(str(round(status.get("bronze", {}).get("unsafe_sum") or 0, 2)))
    status["trusted_net_recorded_gbp"] = str(trusted)
    status["unsafe_comparison"] = {"label": "UNSAFE: naive sum of quantity x price over every received delivery (no identity dedup, no envelope or business validation, no merchandise rules, synthetic included)",
                                   "unsafe_net_gbp": str(unsafe), "discrepancy_gbp": str(unsafe - trusted)}
    if _delta_exists(spark, lake(cfg, "late_arrivals")):
        row = spark.read.format("delta").load(str(lake(cfg, "late_arrivals"))).groupBy().agg(
            F.sum(F.when(F.col("status") == "pending", 1).otherwise(0)).alias("pending"),
            F.sum(F.when(F.col("status") == "applied", 1).otherwise(0)).alias("applied"),
            F.sum(F.when(F.col("status") == "pending", F.col("unsafe_amount")).otherwise(0)).alias("pending_amount")).first()
        status["late_arrivals"] = {"pending": row["pending"] or 0, "applied": row["applied"] or 0, "pending_amount_hint_gbp": round(float(row["pending_amount"] or 0), 2)}
    if _delta_exists(spark, lake(cfg, "quarantine")):
        status["quarantine_by_reason"] = {r["reason"]: r["count"] for r in spark.read.format("delta").load(str(lake(cfg, "quarantine"))).groupBy("reason").count().collect()}
    if _delta_exists(spark, lake(cfg, "silver_current")):
        status["silver"] = {r["disposition"]: r["count"] for r in spark.read.format("delta").load(str(lake(cfg, "silver_current"))).groupBy("disposition").count().collect()}
        status["delta_versions"] = {n: _version(spark, lake(cfg, n)) for n in ["bronze", "quarantine", "late_arrivals", "revisions", "silver_current", "daily_sales", "batch_metrics"] if _delta_exists(spark, lake(cfg, n))}
    atomic_json(gen_state(cfg) / "status.json", status)
    return status


def record_baseline(spark, cfg=None):
    cfg = cfg or config()
    versions = {n: _version(spark, lake(cfg, n)) for n in ["bronze", "quarantine", "late_arrivals", "revisions", "silver_current", "daily_sales", "batch_metrics"] if _delta_exists(spark, lake(cfg, n))}
    baseline = {"recorded_at": now(), "delta_versions": versions, "snapshot_id": read_json(DATA / "serving" / "replay.json")["snapshot_id"]}
    atomic_json(gen_state(cfg) / "baseline.json", baseline)
    return baseline


def reset(spark, cfg=None):
    """Safe reset: Delta RESTORE every replay table to its recorded baseline version and point serving back.
    Earlier versions stay in Delta history; incident evidence stays in runtime/ and evidence/."""
    cfg = cfg or config()
    baseline = read_json(gen_state(cfg) / "baseline.json")
    restored = {}
    for name in ["bronze", "quarantine", "late_arrivals", "revisions", "silver_current", "daily_sales", "batch_metrics"]:
        path = lake(cfg, name)
        if not _delta_exists(spark, path):
            continue
        if name in baseline["delta_versions"]:
            if _version(spark, path) != baseline["delta_versions"][name]:
                spark.sql(f"RESTORE TABLE delta.`{path.as_posix()}` TO VERSION AS OF {baseline['delta_versions'][name]}")
            restored[name] = baseline["delta_versions"][name]
        else:   # table created after the baseline (e.g. first late arrival): empty it, keep its history
            spark.sql(f"DELETE FROM delta.`{path.as_posix()}`")
            restored[name] = "emptied"
    atomic_json(DATA / "serving" / "replay.json", {"snapshot_id": baseline["snapshot_id"], "generation": cfg["generation"]})
    record = {"reset_at": now(), "restored_versions": restored, "snapshot_id": baseline["snapshot_id"]}
    append_jsonl(gen_state(cfg) / "resets.jsonl", record)
    write_status(spark, cfg)
    return record
