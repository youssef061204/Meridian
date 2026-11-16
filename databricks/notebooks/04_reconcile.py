# Databricks notebook source
# MAGIC %md
# MAGIC # MERIDIAN contract validation: 04 reconciliation assertions
# MAGIC
# MAGIC **Status: prepared, unexecuted in Databricks.** Mode: synthetic.
# MAGIC
# MAGIC Compares the Databricks tables with the hand-calculated `contract/expected.json`. These are the same expectations
# MAGIC that `scripts/check_databricks_contract.py` checks locally with DuckDB. The notebook writes every check to the
# MAGIC `contract_reconciliation` table and to `/Volumes/<catalog>/<schema>/contract/evidence/<run_id>.json`. It raises if
# MAGIC any check fails, so the job run is marked failed. Download that JSON if you want it as Databricks evidence. Until a
# MAGIC real run produces it, the repository records no Databricks evidence.

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
fq = f"{catalog}.{schema}"

# COMMAND ----------

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

with open(f"{bundle_root}/contract/expected.json", encoding="utf-8") as handle:
    expected = json.load(handle)
checks = []


def check(name, actual, wanted):
    checks.append({"check": name, "expected": wanted, "actual": actual, "passed": actual == wanted})


for name in ["silver.sql", "daily_sales.sql"]:
    with open(f"{bundle_root}/sql/{name}", "rb") as handle:
        checks.append({"check": f"bundled sql/{name} sha256 (compare with local evidence)", "expected": None,
                       "actual": hashlib.sha256(handle.read()).hexdigest(), "passed": True})

# COMMAND ----------

d = spark.sql(f"SELECT count(*) AS received, count(DISTINCT line_id) AS distinct_rows FROM {fq}.bronze_deliveries").first()
check("received deliveries", d["received"], expected["deliveries"]["received_deliveries"])
check("distinct source rows", d["distinct_rows"], expected["deliveries"]["distinct_source_rows"])
check("repeated deliveries", d["received"] - d["distinct_rows"], expected["deliveries"]["repeated_deliveries"])
repeated = [r[0] for r in spark.sql(f"SELECT source_row FROM {fq}.bronze_deliveries GROUP BY source_row HAVING count(*) > 1 ORDER BY 1").collect()]
check("repeated coordinates", repeated, expected["deliveries"]["repeated_coordinates"])

silver = f"{fq}.silver_invoice_line"
dispositions = {r[0]: r[1] for r in spark.sql(f"SELECT disposition, count(*) FROM {silver} GROUP BY 1").collect()}
check("dispositions", {k: dispositions.get(k, 0) for k in expected["dispositions"]}, expected["dispositions"])
check("disposition rows sum to distinct source rows", sum(dispositions.values()), d["distinct_rows"])
classes = {r[0]: r[1] for r in spark.sql(f"SELECT classification, count(*) FROM {silver} GROUP BY 1").collect()}
check("classifications", classes, expected["classifications"])

k = spark.sql(f"""SELECT
  coalesce(sum(CASE WHEN line_amount > 0 THEN line_amount ELSE 0 END), 0) AS gross,
  coalesce(sum(CASE WHEN line_amount < 0 THEN line_amount ELSE 0 END), 0) AS adjustments,
  coalesce(sum(line_amount), 0) AS net,
  coalesce(sum(CASE WHEN quantity > 0 THEN quantity ELSE 0 END), 0) AS units,
  count(*) AS accepted_rows, count(customer_id) AS known FROM {fq}.fact_invoice_line""").first()
kpis = {"gross_positive_gbp": str(k["gross"]), "negative_adjustments_gbp": str(k["adjustments"]), "net_recorded_gbp": str(k["net"]),
        "positive_units": int(k["units"]), "accepted_rows": k["accepted_rows"], "known_customer_rows": k["known"],
        "known_customer_coverage": f"{k['known']}/{k['accepted_rows']}"}
for key, wanted in expected["kpis"].items():
    check(f"kpi {key}", kpis[key], wanted)
check("gross + adjustments = net", Decimal(kpis["gross_positive_gbp"]) + Decimal(kpis["negative_adjustments_gbp"]), Decimal(kpis["net_recorded_gbp"]))

b = spark.sql(f"""SELECT
  coalesce(sum(CASE WHEN disposition='accepted' THEN parsed_amount END), 0) AS accepted,
  coalesce(sum(CASE WHEN disposition='excluded' THEN parsed_amount END), 0) AS excluded,
  coalesce(sum(CASE WHEN disposition='quarantined' THEN parsed_amount END), 0) AS quarantined,
  coalesce(sum(parsed_amount), 0) AS source, count(*) - count(parsed_amount) AS unparseable FROM {silver}""").first()
bridge = {"accepted_parsed_amount_gbp": str(b["accepted"]), "excluded_parsed_amount_gbp": str(b["excluded"]),
          "quarantined_parsed_amount_gbp": str(b["quarantined"]), "source_parsed_amount_gbp": str(b["source"]),
          "unparseable_amount_rows": b["unparseable"]}
for key, wanted in expected["amount_bridge"].items():
    check(f"bridge {key}", bridge[key], wanted)
check("accepted + excluded + quarantined parsed = source parsed", b["accepted"] + b["excluded"] + b["quarantined"], b["source"])

unsafe = spark.sql(f"""SELECT sum(s.line_amount) FROM {fq}.bronze_deliveries d JOIN {silver} s ON d.line_id = s.line_id
  WHERE s.disposition = 'accepted'""").first()[0]
check("unsafe accepted-delivery net (comparison only)", str(unsafe), expected["unsafe_comparison"]["accepted_delivery_net_gbp"])
check("unsafe overstatement", str(unsafe - b["accepted"]), expected["unsafe_comparison"]["overstatement_gbp"])

legit = spark.sql(f"SELECT source_row, CAST(line_amount AS STRING) AS amount FROM {fq}.fact_invoice_line WHERE source_row IN (2, 3) ORDER BY source_row").collect()
check("legitimate identical rows both accepted", [r["source_row"] for r in legit], expected["legitimate_identical_rows"]["source_rows"])
check("legitimate identical row amount", sorted({r["amount"] for r in legit}), [expected["legitimate_identical_rows"]["each_line_amount_gbp"]])


def row(source_row, column):
    return spark.sql(f"SELECT CAST({column} AS STRING) FROM {silver} WHERE source_row = {int(source_row)}").first()[0]


rl = expected["row_level"]
check("half-up positive row 11", row(11, "line_amount"), rl["rounding_positive_row_11_gbp"])
check("half-up negative row 22", row(22, "line_amount"), rl["rounding_negative_row_22_gbp"])
check("trimmed upper stock code row 18", row(18, "stock_code"), rl["trimmed_stock_code_row_18"])
check("blank country row 16", row(16, "country"), rl["blank_country_row_16"])
check("summer civil time row 17 (no offset applied)", row(17, "event_time"), rl["summer_civil_time_row_17"])

daily = [{"date": str(r["date"]), "gross_positive_gbp": str(r["gross_positive_gbp"]), "negative_adjustments_gbp": str(r["negative_adjustments_gbp"]),
          "net_recorded_gbp": str(r["net_recorded_gbp"]), "positive_units": int(r["positive_units"]), "rows": r["rows"],
          "known_customer_rows": int(r["known_customer_rows"])}
         for r in spark.table(f"{fq}.daily_sales").orderBy("date").collect()]
check("daily_sales rows", daily, expected["daily_sales"])
check("daily net sums to fact net", str(spark.sql(f"SELECT sum(net_recorded_gbp) FROM {fq}.daily_sales").first()[0]), kpis["net_recorded_gbp"])
country = {r[0]: str(r[1]) for r in spark.sql(f"SELECT country, sum(line_amount) FROM {fq}.fact_invoice_line GROUP BY 1").collect()}
check("country net", country, expected["country_net_gbp"])

# COMMAND ----------

run_id = spark.sql(f"SELECT max(run_id) FROM {fq}.bronze_deliveries").first()[0]
passed = all(c["passed"] for c in checks)
evidence = {
    "label": "databricks-contract-run",
    "scope": "Databricks execution of the synthetic contract-validation batch path (not observed data, not the Kafka path).",
    "mode": "synthetic", "state": "passed" if passed else "failed", "run_id": run_id,
    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    "spark_version": spark.version,
    "spark_conf": {key: spark.conf.get(key) for key in ["spark.sql.ansi.enabled", "spark.sql.session.timeZone"]},
    "catalog": catalog, "schema": schema, "kpis": kpis, "amount_bridge": bridge,
    "checks_passed": sum(c["passed"] for c in checks), "checks_total": len(checks), "checks": checks,
}
evidence_dir = f"/Volumes/{catalog}/{schema}/contract/evidence"
os.makedirs(evidence_dir, exist_ok=True)
with open(f"{evidence_dir}/{run_id}.json", "w", encoding="utf-8") as handle:
    json.dump(evidence, handle, indent=2, default=str)
(spark.createDataFrame([(run_id, c["check"], json.dumps(c["expected"], default=str), json.dumps(c["actual"], default=str), c["passed"]) for c in checks],
                       "run_id STRING, check STRING, expected STRING, actual STRING, passed BOOLEAN")
 .write.mode("append").saveAsTable(f"{fq}.contract_reconciliation"))
print(f"{evidence['state']}: {evidence['checks_passed']}/{evidence['checks_total']} -> {evidence_dir}/{run_id}.json")
if not passed:
    failed = [c for c in checks if not c["passed"]]
    raise AssertionError(f"{len(failed)} contract checks failed: {json.dumps(failed, default=str)[:2000]}")
