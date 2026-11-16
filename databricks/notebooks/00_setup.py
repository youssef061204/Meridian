# Databricks notebook source
# MAGIC %md
# MAGIC # MERIDIAN contract validation: 00 setup
# MAGIC
# MAGIC **Status: prepared, unexecuted in Databricks.** Synthetic contract-validation data only; never observed transactions.
# MAGIC
# MAGIC Creates the Unity Catalog schema and volume, then copies the bundled contract CSV from workspace files into the volume.
# MAGIC The default target is Databricks Free Edition, whose tables live in the `workspace` catalog. Nothing here reaches
# MAGIC outside the workspace. Free Edition limits outbound internet access to trusted domains, so the local Kafka broker
# MAGIC cannot be reached from it.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "meridian_contract")
dbutils.widgets.text("bundle_root", "")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
bundle_root = dbutils.widgets.get("bundle_root").rstrip("/")
if not bundle_root:
    # Interactive fallback: this notebook lives at <bundle_root>/notebooks/00_setup.
    ctx_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    bundle_root = "/Workspace" + ctx_path.rsplit("/notebooks/", 1)[0]
for name, value in {"catalog": catalog, "schema": schema}.items():
    assert value.replace("_", "").isalnum(), f"unsafe {name} identifier: {value!r}"
volume_dir = f"/Volumes/{catalog}/{schema}/contract"
print({"catalog": catalog, "schema": schema, "bundle_root": bundle_root, "volume_dir": volume_dir})

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema} COMMENT 'MERIDIAN synthetic contract validation (not observed data)'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.contract COMMENT 'MERIDIAN contract CSV and run evidence'")

# COMMAND ----------

import hashlib
import shutil

source = f"{bundle_root}/contract/lines.csv"
target = f"{volume_dir}/lines.csv"
shutil.copyfile(source, target)
with open(target, "rb") as handle:
    digest = hashlib.sha256(handle.read()).hexdigest()
print({"copied": target, "sha256": digest})
dbutils.jobs.taskValues.set(key="dataset_sha256", value=digest)
