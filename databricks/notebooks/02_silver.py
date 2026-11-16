# Databricks notebook source
# MAGIC %md
# MAGIC # MERIDIAN contract validation: 02 Silver
# MAGIC
# MAGIC **Status: prepared, unexecuted in Databricks.** Mode: synthetic.
# MAGIC
# MAGIC 1. Stops if a repeated delivery of a line identity carries a different payload. That would need an explicit
# MAGIC    revision, not an arbitrary pick.
# MAGIC 2. Keeps one delivery per `line_id` and registers it as the `source_lines` session temp view. Serverless does not
# MAGIC    support global temp views.
# MAGIC 3. Executes the bundled `sql/silver.sql`, a byte-identical copy of the repository model. The local
# MAGIC    `scripts/check_databricks_contract.py` checks that the copy matches. No business logic is restated here.
# MAGIC    Databricks supports `TIMESTAMP_NTZ` natively, so the SQL runs unchanged. Only the table location changes, to a
# MAGIC    Unity Catalog managed table.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "meridian_contract")
dbutils.widgets.text("bundle_root", "")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
bundle_root = dbutils.widgets.get("bundle_root").rstrip("/")
if not bundle_root:
    ctx_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    bundle_root = "/Workspace" + ctx_path.rsplit("/notebooks/", 1)[0]
for name, value in {"catalog": catalog, "schema": schema}.items():
    assert value.replace("_", "").isalnum(), f"unsafe {name} identifier: {value!r}"
spark.conf.set("spark.sql.ansi.enabled", "false")
spark.conf.set("spark.sql.session.timeZone", "Europe/London")

# COMMAND ----------

from pyspark.sql import Window
from pyspark.sql import functions as F

bronze = spark.table(f"{catalog}.{schema}.bronze_deliveries")
payload = ["invoice", "stock_code", "description", "quantity", "event_time", "unit_price", "customer_id", "country"]
conflicts = bronze.select("line_id", *payload).distinct().groupBy("line_id").count().filter("count > 1").count()
if conflicts:
    raise AssertionError(f"{conflicts} line identities have conflicting repeated payloads; explicit revision required")

first_delivery = Window.partitionBy("line_id").orderBy("delivery_id")
source_lines = (
    bronze.withColumn("_rn", F.row_number().over(first_delivery)).filter("_rn = 1")
    .drop("_rn", "delivery_id", "run_id", "bronze_ingested_at_utc")
)
source_lines.createOrReplaceTempView("source_lines")

# COMMAND ----------

with open(f"{bundle_root}/sql/silver.sql", encoding="utf-8") as handle:
    silver_sql = handle.read()
spark.sql(silver_sql).createOrReplaceTempView("silver_candidate")
spark.sql(f"""CREATE OR REPLACE TABLE {catalog}.{schema}.silver_invoice_line
  TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')
  AS SELECT * FROM silver_candidate""")
display(spark.table(f"{catalog}.{schema}.silver_invoice_line")
        .groupBy("disposition", "classification").count().orderBy("disposition", "classification"))
