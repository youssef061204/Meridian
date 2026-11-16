# Databricks notebook source
# MAGIC %md
# MAGIC # MERIDIAN contract validation: 03 Gold fact and daily sales
# MAGIC
# MAGIC **Status: prepared, unexecuted in Databricks.** Mode: synthetic.
# MAGIC
# MAGIC `fact_invoice_line` holds the accepted Silver rows: positive merchandise and negative adjustments. `daily_sales`
# MAGIC executes the bundled byte-identical copy of `sql/daily_sales.sql`. Unavailable dates are not zero-filled.

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

spark.sql(f"""CREATE OR REPLACE TABLE {catalog}.{schema}.fact_invoice_line
  TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')
  AS SELECT * FROM {catalog}.{schema}.silver_invoice_line WHERE disposition = 'accepted'""")
spark.table(f"{catalog}.{schema}.fact_invoice_line").createOrReplaceTempView("fact_invoice_line")
with open(f"{bundle_root}/sql/daily_sales.sql", encoding="utf-8") as handle:
    daily_sql = handle.read()
spark.sql(daily_sql).createOrReplaceTempView("daily_candidate")
spark.sql(f"CREATE OR REPLACE TABLE {catalog}.{schema}.daily_sales AS SELECT * FROM daily_candidate")
display(spark.table(f"{catalog}.{schema}.daily_sales").orderBy("date"))
