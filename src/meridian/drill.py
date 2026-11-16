"""End-to-end recovery drill against real Kafka, Spark and Delta (demo profile).

Every fault is a real process death (os._exit) inside foreachBatch, before Spark commits
the micro-batch to its checkpoint. Assertions are written to evidence/replay-drill.json.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from decimal import ROUND_HALF_UP, Decimal

from .common import DATA, ROOT, atomic_json, now, read_json, run_record

CHECKS = []


def check(name, condition, **detail):
    CHECKS.append({"check": name, "passed": bool(condition), **detail})
    print(("PASS " if condition else "FAIL ") + name, detail, flush=True)
    if not condition:
        raise AssertionError(f"{name}: {detail}")


def consumer_process(crash=None):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    if crash:
        env["MERIDIAN_CRASH"] = crash
    started = time.perf_counter()
    result = subprocess.run([sys.executable, "-m", "meridian.cli", "replay-consume"], cwd=ROOT, env=env, capture_output=True, text=True)
    tail = (result.stdout + result.stderr)[-1500:]
    return {"crash": crash, "exit_code": result.returncode, "seconds": round(time.perf_counter() - started, 2), "fault_line": [l for l in tail.splitlines() if "FAULT INJECTION" in l]}


def drill():
    from pyspark.sql import functions as F
    from .pipeline import spark_session
    from .replay import inject_incident, new_generation, replay
    from .stream import PublicationBlocked, backfill, lake, publish, write_status
    with run_record("replay_recovery_drill", profile="demo", data_modes=["replay", "synthetic"]) as run:
        evidence = {"run_id": run["run_id"], "started_at": now(), "steps": []}
        try:
            cfg = new_generation("demo")
            evidence["generation"] = cfg["generation"]
            produced = replay("demo")
            evidence["steps"].append({"step": "produce_baseline_replay", **produced})
            # Fault 1: die after the Bronze append, before Silver/metrics, in batch 1.
            crash1 = consumer_process("after-bronze:1")
            check("consumer_killed_after_bronze_commit", crash1["exit_code"] == 86, **crash1)
            # Fault 2: die after every write of a batch but before the checkpoint commit.
            crash2 = consumer_process("after-silver:first")
            check("consumer_killed_before_checkpoint_commit", crash2["exit_code"] == 86, **crash2)
            resumed = consumer_process()
            check("consumer_restart_completes", resumed["exit_code"] == 0, **resumed)

            spark = spark_session()
            try:
                bronze = spark.read.format("delta").load(str(lake(cfg, "bronze")))
                b = bronze.agg(F.count("*").alias("rows"), F.countDistinct("kafka_topic", "kafka_partition", "kafka_offset").alias("offsets")).first()
                check("bronze_has_no_duplicate_writes_after_retries", b["rows"] == b["offsets"] == produced["sent"], rows=b["rows"], offsets=b["offsets"], produced=produced["sent"])
                metrics = [r["batch_id"] for r in spark.read.format("delta").load(str(lake(cfg, "batch_metrics"))).collect()]
                check("each_batch_recorded_exactly_once", len(metrics) == len(set(metrics)), batches=sorted(metrics))
                first = publish(spark, cfg)
                rec = first["reconciliation"]
                batch_snapshot = read_json(DATA / "serving" / "demo.json")["snapshot_id"]
                withheld_accepted = rec["missing_vs_batch_rows"]
                check("replay_matches_batch_except_withheld_rows", rec["extra_vs_batch_rows"] == 0 and rec["uncorrected_value_differences"] == 0
                      and 0 < withheld_accepted <= cfg["withheld_rows"], **rec, batch_snapshot=batch_snapshot)
                pointer_before = read_json(DATA / "serving" / "replay.json")
                try:
                    publish(spark, cfg, fail_at="after-export")
                    interrupted = False
                except RuntimeError:
                    interrupted = True
                check("interrupted_publish_keeps_last_validated_pointer", interrupted and read_json(DATA / "serving" / "replay.json") == pointer_before,
                      pointer=pointer_before)

                manifest = inject_incident("demo")
                evidence["steps"].append({"step": "inject_synthetic_incident", **manifest})
            finally:
                spark.stop()
            crash3 = consumer_process("after-silver:first")
            check("consumer_killed_during_incident_batch", crash3["exit_code"] == 86, **crash3)
            resumed2 = consumer_process()
            check("consumer_restart_after_incident_completes", resumed2["exit_code"] == 0, **resumed2)

            spark = spark_session()
            try:
                status = write_status(spark, cfg)
                q = status.get("quarantine_by_reason", {})
                counts = manifest["counts"]
                check("malformed_json_quarantined", q.get("malformed_json") == counts["malformed_json"], quarantine=q)
                check("missing_required_fields_quarantined", q.get("missing_required_field") == counts["missing_required_field"], quarantine=q)
                check("incompatible_schema_quarantined", q.get("incompatible_schema") == counts["incompatible_schema_v3"], quarantine=q)
                check("identity_payload_conflict_quarantined", q.get("identity_payload_conflict") == counts["identity_payload_conflict"], quarantine=q)
                check("late_rows_routed_to_durable_backfill", status["late_arrivals"]["pending"] == counts["late_arrival_withheld_source_row"] + counts["correction_revision_2"],
                      late=status["late_arrivals"])
                unsafe = status["unsafe_comparison"]
                check("unsafe_calculation_shows_discrepancy", Decimal(unsafe["discrepancy_gbp"]) != 0, **unsafe, trusted=status["trusted_net_recorded_gbp"])
                try:
                    publish(spark, cfg)
                    blocked = False
                except PublicationBlocked:
                    blocked = True
                check("publication_blocked_while_backfill_pending", blocked and read_json(DATA / "serving" / "replay.json") == pointer_before)
                filled = backfill(spark, cfg)
                check("backfill_applies_late_rows_for_affected_dates", filled["applied_rows"] > 0 and filled["affected_dates"], **filled)
                again = backfill(spark, cfg, force=True)
                check("backfill_rerun_is_idempotent", Decimal(again["net_change"]) == 0 and again["selected_late_rows"] == filled["selected_late_rows"], **again)
                final = publish(spark, cfg)
                rec = final["reconciliation"]
                c = manifest["correction"]
                quantity = Decimal(c["quantity"])
                expected_delta = ((quantity * Decimal(c["corrected_unit_price"])).quantize(Decimal("0.01"), ROUND_HALF_UP)
                                  - (quantity * Decimal(c["original_unit_price"])).quantize(Decimal("0.01"), ROUND_HALF_UP))
                check("replay_reconciled_with_batch_after_backfill", rec["state"] == "reconciled" and rec["missing_vs_batch_rows"] == 0, **rec)
                check("correction_delta_matches_independent_calculation", Decimal(rec["correction_delta_gbp"]) == expected_delta,
                      expected=str(expected_delta), actual=rec["correction_delta_gbp"])
                check("new_snapshot_published", final["id"] != pointer_before["snapshot_id"] and read_json(DATA / "serving" / "replay.json")["snapshot_id"] == final["id"],
                      snapshot=final["id"], delta_versions=final["delta_versions"])
                evidence["final_manifest"] = {k: final[k] for k in ["id", "metrics", "reconciliation", "delta_versions", "batch_reference_snapshot", "batch_net_recorded_gbp"]}
            finally:
                spark.stop()
            evidence["state"] = "passed"
        except Exception as exc:
            evidence.update(state="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            evidence.update(checks=CHECKS, finished_at=now())
            atomic_json(ROOT / "evidence" / "replay-drill.json", evidence)
