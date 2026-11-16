"""Version-controlled Gold models and executed cardinality checks.

This module builds real Spark DataFrames. It does not provide a pandas fallback
or publish a serving pointer. The pipeline owns Delta commits and publication.
"""
from __future__ import annotations

from decimal import Decimal

from .common import ROOT


MODEL_NAMES = (
    "dim_date", "dim_product", "dim_customer", "dim_geography",
    "cohort_retention", "customer_summary", "forecast_inputs", "quality_daily",
)
UNKNOWN_CUSTOMER = "__UNKNOWN__"


def build_gold(spark, fact, silver):
    """Return additional Gold DataFrames, registering dependencies in order."""
    fact.createOrReplaceTempView("gold_fact_source")
    silver.createOrReplaceTempView("gold_silver_source")
    tables = {}
    for name in MODEL_NAMES:
        frame = spark.sql((ROOT / "sql" / "gold" / f"{name}.sql").read_text(encoding="utf-8"))
        frame.createOrReplaceTempView(f"gold_{name}")
        tables[name] = frame
    return tables


def validate_gold(spark, tables, fact):
    """Execute primary-key, referential, and sum-preserving join assertions.

    Evidence is returned only after the underlying Spark actions complete.
    Dimension joins are broadcast because this source's dimensions are small.
    """
    from pyspark.sql import functions as F

    primary_keys = {
        "dim_date": ["date"], "dim_product": ["stock_code"],
        "dim_customer": ["customer_id"], "dim_geography": ["country"],
        "cohort_retention": ["cohort_month", "activity_month"],
        "customer_summary": ["customer_id"],
        "forecast_inputs": ["series_id", "date"], "quality_daily": ["date"],
    }
    evidence = {"state": "passed", "table_keys": {}}
    for name, keys in primary_keys.items():
        frame = tables[name]
        missing_key = F.lit(False)
        for key in keys:
            missing_key = missing_key | F.col(key).isNull()
        row = frame.agg(
            F.count("*").alias("rows"),
            F.countDistinct(F.struct(*[F.col(key) for key in keys])).alias("distinct_keys"),
            F.sum(F.when(missing_key, 1).otherwise(0)).alias("null_key_rows"),
        ).first().asDict()
        assert row["rows"] == row["distinct_keys"], f"{name} duplicates its declared grain: {row}"
        # An invalid timestamp remains visible as the one null-date quality bucket.
        if name != "quality_daily":
            assert (row["null_key_rows"] or 0) == 0, f"{name} has a null primary key: {row}"
        evidence["table_keys"][name] = {"columns": keys, **row}

    reserved = fact.filter(F.col("customer_id") == UNKNOWN_CUSTOMER).limit(1).count()
    assert reserved == 0, "Source customer identifier collides with reserved unknown-member key"
    unknown_members = tables["dim_customer"].filter(F.col("customer_id") == UNKNOWN_CUSTOMER).count()
    assert unknown_members == 1, "Customer dimension requires exactly one explicit unknown member"

    baseline = fact.agg(
        F.count("*").alias("rows"),
        F.countDistinct("line_id").alias("distinct_lines"),
        F.sum("line_amount").alias("net_recorded_gbp"),
    ).first().asDict()
    assert baseline["rows"] == baseline["distinct_lines"], "Fact line identity is not unique"
    joined = fact.select("line_id", "date", "stock_code", "customer_id", "country", "line_amount")
    joined = joined.withColumn("customer_key", F.coalesce(F.col("customer_id"), F.lit(UNKNOWN_CUSTOMER)))
    joins = (
        ("dim_date", "date", "date", "_joined_date"),
        ("dim_product", "stock_code", "stock_code", "_joined_product"),
        ("dim_customer", "customer_key", "customer_id", "_joined_customer"),
        ("dim_geography", "country", "country", "_joined_geography"),
    )
    for name, fact_key, dimension_key, marker in joins:
        dimension = tables[name].select(F.col(dimension_key).alias(marker))
        joined = joined.join(F.broadcast(dimension), F.col(fact_key) == F.col(marker), "left")
    join_result = joined.agg(
        F.count("*").alias("rows"), F.countDistinct("line_id").alias("distinct_lines"),
        F.sum("line_amount").alias("net_recorded_gbp"),
        *[F.sum(F.when(F.col(marker).isNull(), 1).otherwise(0)).alias(f"unmatched_{name}")
          for name, _, _, marker in joins],
    ).first().asDict()
    assert join_result["rows"] == baseline["rows"], f"Dimension joins multiply fact rows: {join_result}"
    assert join_result["distinct_lines"] == baseline["distinct_lines"]
    assert (join_result["net_recorded_gbp"] or Decimal(0)) == (baseline["net_recorded_gbp"] or Decimal(0)), "Dimension joins change the monetary total"
    for name, _, _, _ in joins:
        assert (join_result[f"unmatched_{name}"] or 0) == 0, f"Unmatched fact foreign key: {name}"
    evidence.update(fact_before_join=baseline, fact_after_join=join_result, unknown_customer_members=unknown_members)
    return evidence
