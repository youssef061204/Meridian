"""Tiny real Kafka -> Spark Structured Streaming -> Delta compatibility job.

Proves the broker, the kafka-python producer, the pinned spark-sql-kafka connector and Delta
work together before the replay pipeline is built on them. It deliberately sends repeat
deliveries of the same source identity so the smoke also checks that dedup keys on source
identity (not on Kafka offset/delivery), and that delivery metadata is kept separately.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid

from .common import DATA, ROOT, atomic_json, now, run_record

BOOTSTRAP = os.environ.get("MERIDIAN_KAFKA_BOOTSTRAP", "kafka:19092")
SOURCE_ROWS = 20
REPEATED = (3, 7, 7, 11)  # row 7 is delivered three times in total


def _produce(topic):
    from kafka import KafkaProducer
    from kafka.admin import KafkaAdminClient, NewTopic

    admin = KafkaAdminClient(bootstrap_servers=BOOTSTRAP, client_id="meridian-smoke")
    try:
        admin.create_topics([NewTopic(topic, num_partitions=2, replication_factor=1)])
        broker_metadata = admin.describe_cluster()
    finally:
        admin.close()
    producer = KafkaProducer(bootstrap_servers=BOOTSTRAP, acks="all", enable_idempotence=True,
                             key_serializer=str.encode, value_serializer=lambda v: json.dumps(v).encode())
    deliveries = [*range(1, SOURCE_ROWS + 1), *REPEATED]
    for sequence, row in enumerate(deliveries):
        identity = f"smoke|Sheet|{row}"
        producer.send(topic, key=identity, value={
            "schema_version": 1, "mode": "synthetic", "source_identity": identity,
            "delivery_id": uuid.uuid4().hex, "delivery_sequence": sequence,
            "quantity": row, "price": "1.25", "produced_at": now()})
    producer.flush()
    producer.close()
    return len(deliveries), broker_metadata


def kafka_smoke():
    topic = "meridian.smoke." + uuid.uuid4().hex[:8]
    target = DATA / "smoke" / "kafka_bronze"
    checkpoint = DATA / "smoke" / "kafka_checkpoint"
    for path in (target, checkpoint):
        shutil.rmtree(path, ignore_errors=True)
    with run_record("kafka_spark_delta_smoke", bootstrap=BOOTSTRAP, topic=topic,
                    data_mode="synthetic smoke envelopes; not observed transactions") as run:
        delivered, cluster = _produce(topic)
        from pyspark.sql import functions as F
        from .pipeline import spark_session
        spark = spark_session()
        try:
            schema = "schema_version INT, mode STRING, source_identity STRING, delivery_id STRING, delivery_sequence INT, quantity INT, price STRING, produced_at STRING"
            stream = (spark.readStream.format("kafka")
                      .option("kafka.bootstrap.servers", BOOTSTRAP)
                      .option("subscribe", topic)
                      .option("startingOffsets", "earliest")
                      .option("failOnDataLoss", "true")
                      .load()
                      .select(F.col("topic"), F.col("partition"), F.col("offset"),
                              F.col("timestamp").alias("kafka_timestamp"),
                              F.from_json(F.col("value").cast("string"), schema).alias("event"))
                      .select("topic", "partition", "offset", "kafka_timestamp", "event.*"))
            query = (stream.writeStream.format("delta")
                     .option("checkpointLocation", str(checkpoint))
                     .trigger(availableNow=True)
                     .start(str(target)))
            query.awaitTermination()
            bronze = spark.read.format("delta").load(str(target))
            stats = bronze.agg(
                F.count("*").alias("deliveries"),
                F.countDistinct("topic", "partition", "offset").alias("distinct_offsets"),
                F.countDistinct("source_identity").alias("distinct_source_identities"),
                F.countDistinct("delivery_id").alias("distinct_delivery_ids")).first().asDict()
            deduped = bronze.dropDuplicates(["source_identity"]).agg(F.sum("quantity").alias("q")).first()["q"]
            expected_quantity = sum(range(1, SOURCE_ROWS + 1))
            # Rerunning the same query against its checkpoint must read nothing new.
            rerun = (stream.writeStream.format("delta").option("checkpointLocation", str(checkpoint))
                     .trigger(availableNow=True).start(str(target)))
            rerun.awaitTermination()
            after_rerun = spark.read.format("delta").load(str(target)).count()
            assert stats["deliveries"] == delivered == stats["distinct_offsets"] == stats["distinct_delivery_ids"], stats
            assert stats["distinct_source_identities"] == SOURCE_ROWS, stats
            assert deduped == expected_quantity, (deduped, expected_quantity)
            assert after_rerun == delivered, ("checkpoint rerun re-ingested", after_rerun)
            run.update(
                spark_version=spark.version,
                java_version=spark.sparkContext._jvm.java.lang.System.getProperty("java.version"),
                kafka_connector="org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.7",
                broker_cluster=str(cluster), produced_deliveries=delivered, bronze=stats,
                deduplicated_by_source_identity_quantity=deduped, expected_quantity=expected_quantity,
                bronze_rows_after_checkpoint_rerun=after_rerun,
                scope="Compatibility smoke only: one broker, two partitions, availableNow trigger. Not the replay pipeline.")
            atomic_json(ROOT / "evidence" / "kafka-smoke.json", {**run, "state": "passed"})
        finally:
            spark.stop()
