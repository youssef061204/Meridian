# Databricks notebook source
# MAGIC %md
# MAGIC # MERIDIAN contract validation: 01 Bronze ingest (batch)
# MAGIC
# MAGIC **Status: prepared, unexecuted in Databricks.** Mode: synthetic.
# MAGIC
# MAGIC Reads every delivery in the contract CSV as text, which is the same string contract as the local converted Parquet.
# MAGIC It derives the logical line identity `sha256(source_sha256|source_sheet|source_row)` and writes all deliveries,
# MAGIC repeats included, to a Bronze Delta table. Delivery metadata (`delivery_id`, `run_id`, `bronze_ingested_at_utc`) stays
# MAGIC separate from the source identity. The batch overwrite mirrors the local deterministic rebuild. It is not the Kafka
# MAGIC replay append log.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "meridian_contract")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
for name, value in {"catalog": catalog, "schema": schema}.items():
    assert value.replace("_", "").isalnum(), f"unsafe {name} identifier: {value!r}"
# Local Spark 3.5.7 runs with ANSI off; serverless defaults it on. Both properties are settable on serverless.
spark.conf.set("spark.sql.ansi.enabled", "false")
spark.conf.set("spark.sql.session.timeZone", "Europe/London")  # matches the local pipeline; NTZ values are unaffected

# COMMAND ----------

import uuid

from pyspark.sql import functions as F
from pyspark.sql.types import LongType, StringType, StructField, StructType

text_fields = ["delivery_id", "invoice", "stock_code", "description", "quantity", "event_time", "unit_price",
               "customer_id", "country", "source_sha256", "source_sheet", "source_row", "ingested_at", "conversion_version"]
csv_schema = StructType([StructField(f, StringType(), True) for f in text_fields])
run_id = "dbx-" + uuid.uuid4().hex[:12]

deliveries = (
    spark.read.format("csv").option("header", "true").option("quote", '"').option("escape", '"')
    .schema(csv_schema).load(f"/Volumes/{catalog}/{schema}/contract/lines.csv")
    .withColumn("source_row", F.col("source_row").cast(LongType()))
    .withColumn("line_id", F.sha2(F.concat_ws("|", "source_sha256", "source_sheet", F.col("source_row").cast("string")), 256))
    .withColumn("run_id", F.lit(run_id))
    .withColumn("bronze_ingested_at_utc", F.current_timestamp())
)
deliveries.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{catalog}.{schema}.bronze_deliveries")

stats = spark.table(f"{catalog}.{schema}.bronze_deliveries").agg(
    F.count("*").alias("received"), F.countDistinct("line_id").alias("distinct_rows")).first()
print({"run_id": run_id, "received_deliveries": stats["received"], "distinct_source_rows": stats["distinct_rows"]})
dbutils.jobs.taskValues.set(key="run_id", value=run_id)
