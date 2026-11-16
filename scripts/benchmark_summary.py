"""Aggregate executed benchmark evidence into evidence/benchmarks.json (no new measurements).

Every number is read from a persisted run record; missing inputs are reported as missing.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"


def load(path):
    path = ROOT / path
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def peak_mib(record):
    peak = None
    for sample in (record or {}).get("container_samples", []):
        match = re.match(r"([\d.]+)(KiB|MiB|GiB)", sample.get("MemUsage", ""))
        if match:
            value = float(match.group(1)) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[match.group(2)]
            peak = max(peak or 0, value)
    return round(peak, 1) if peak else None


def wrapper(label):
    record = load(f"evidence/execution/{label}.json")
    if not record:
        return {"state": "missing"}
    return {"command": " ".join(record["command"]), "exit_code": record["exit_code"], "wall_seconds": record["duration_seconds"],
            "peak_container_memory_mib": peak_mib(record), "memory_samples": len(record.get("container_samples", [])),
            "git_sha": record.get("git_sha"), "cache_condition": record.get("cache_condition")}


def replay_full():
    out = {"wrapper": wrapper("replay-full")}
    cfg_path = ROOT / "runtime/replay/full-benchmark-config.json"
    cfg = load("runtime/replay/full-benchmark-config.json") if cfg_path.exists() else None
    if not cfg:
        return out
    folder = ROOT / "runtime/replay" / cfg["generation"]
    batches = [json.loads(l) for l in (folder / "batches.jsonl").read_text().splitlines() if l.strip()]
    pubs = [json.loads(l) for l in (folder / "publications.jsonl").read_text().splitlines() if l.strip()]
    producer = max((json.loads(p.read_text()) for p in (ROOT / "runtime/replay/producers").glob("*.json")
                    if json.loads(p.read_text()).get("topic") == cfg["replay_topic"]), key=lambda r: r["finished_at"], default=None)
    passed = [p for p in pubs if p["state"] == "passed"][-1]
    manifest = load(f"data/serving/snapshots/{passed['snapshot_id']}/manifest.json")
    deliveries = sum(b["deliveries"] for b in batches)
    seconds = sum(b["processing_seconds"] for b in batches)
    last_emit = max(b["max_replay_time"] for b in batches if b["max_replay_time"])
    from datetime import datetime
    last = max(batches, key=lambda b: b["batch_id"])
    drained = datetime.fromisoformat(last["processed_at"]).timestamp() + last["processing_seconds"]
    failed_attempts = [{"attempted_at": p["attempted_at"], "state": p["state"], "reason": p.get("reason")} for p in pubs if p["state"] != "passed"]
    out.update(generation=cfg["generation"], partitions_replay_topic=1, max_offsets_per_trigger=cfg["max_offsets_per_trigger"],
               producer={k: producer[k] for k in ("sent", "seconds", "events_per_second")} if producer else None,
               consumer={"micro_batches": len(batches), "deliveries": deliveries, "foreach_batch_seconds": round(seconds, 1),
                         "deliveries_per_second": round(deliveries / seconds, 1),
                         "per_batch": [{k: b[k] for k in ("batch_id", "deliveries", "processing_seconds", "deliveries_per_second", "processing_lag_seconds")} for b in batches]},
               publication_seconds=passed["seconds"],
               last_event_emit_to_silver_seconds=round(drained - datetime.fromisoformat(last_emit).timestamp(), 1),
               ingestion_to_publication_seconds=round(drained - datetime.fromisoformat(last_emit).timestamp() + passed["seconds"], 1),
               ingestion_to_publication_definition="last replay emit -> last micro-batch committed to Silver, plus the successful publication's duration; excludes the failed first publication attempt and the time spent fixing it",
               failed_publication_attempts=failed_attempts,
               reconciliation_vs_batch=manifest["reconciliation"], replay_net=manifest["metrics"]["net_recorded_gbp"],
               batch_net=manifest["batch_net_recorded_gbp"], batch_reference=manifest["batch_reference_snapshot"],
               measurement_note="Producer emitted the full replay unthrottled, then an availableNow consumer drained it; ingestion-to-publication latency is measured from the last emitted event.")
    return out


def main():
    env = load("evidence/environment.json") or {}
    source = load("evidence/source-verification.json") or {}
    full_baseline = load("evidence/full-baseline.json") or {}
    synthetic_baseline = load("evidence/synthetic-baseline.json") or {}
    synthetic_batch = load("evidence/synthetic-batch.json") or {}
    synthetic_manifest = load("data/synthetic/manifest.json")
    demo_timeline = load("evidence/browser-demo.json") or {}
    drill = load("evidence/replay-drill.json") or {}
    recovery_step = next((s for s in demo_timeline.get("steps", []) if s["name"] == "repair_reconciles_and_publishes_new_snapshot"), {})
    result = {
        "generated_by": "scripts/benchmark_summary.py (aggregates persisted evidence only)",
        "git_sha_at_summary": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "scope": "Single-machine measurements on one Windows 11 laptop via Docker Desktop/WSL2; not distributed or cloud benchmarks. One repetition per pipeline run unless stated; OS page cache uncontrolled.",
        "hardware": env.get("host"), "docker_resources": env.get("docker_resources"),
        "dataset": {"workbook_sha256": "bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980", "source_rows": 1067371},
        "seeds": {"replay_and_incidents": 20261116, "bootstrap": 20261116, "synthetic_scale": (synthetic_manifest or {}).get("seed")},
        "full_real_dataset": {
            "conversion_workbook_to_parquet": {"seconds": (load("runtime/runs/20261116T190904-6cd7dfae.json") or {}).get("duration_seconds"), "run_record": "runtime/runs/20261116T190904-6cd7dfae.json", "environment": "host Python 3.11, 10,000-row chunks"},
            "spark_delta_full_pipeline": {**wrapper("full-batch"), "tables": 14, "rows": 1067371},
            "duckdb_baseline_full": {**wrapper("full-baseline"), "transform_export_seconds": full_baseline.get("transform_export_seconds"),
                                     "equivalence": full_baseline.get("equivalence"), "tables": 2},
            "comparison_note": "Not like-for-like: DuckDB builds fact + daily only with no Delta history; Spark builds 14 Delta tables, dimensions, cohorts and validations.",
        },
        "profiles_resource_use": {"demo_4000_rows_spark": wrapper("demo-locked"), "full_spark": wrapper("full-batch"),
                                   "full_kafka_replay": wrapper("replay-full"), "synthetic_scale_spark": wrapper("synthetic-batch")},
        "synthetic_scale": {"manifest": synthetic_manifest, "spark_delta_batch": {**wrapper("synthetic-batch"), "spark_stage": {"seconds": (load(f"runtime/runs/{synthetic_batch.get('run_id')}.json") or {}).get("duration_seconds"), **{k: synthetic_batch.get(k) for k in ("source_rows", "counts")}}},
                            "duckdb_baseline": {**wrapper("synthetic-baseline"), "transform_export_seconds": synthetic_baseline.get("transform_export_seconds"),
                                                "equivalence": synthetic_baseline.get("equivalence")},
                            "label": "SYNTHETIC rows (relabelled copies), not observed transactions"},
        "replay_full_history": replay_full(),
        "analytical_query_latency": load("evidence/benchmark-queries.json"),
        "incident_recovery": {"drill": {"state": drill.get("state"), "checks_passed": sum(c["passed"] for c in drill.get("checks", [])), "checks": len(drill.get("checks", []))},
                              "browser_demo_recovery_seconds": recovery_step.get("recovery_seconds"),
                              "browser_signature_sequence_seconds": demo_timeline.get("signature_sequence_seconds"),
                              "note": "Recovery seconds span injection to validated publication and include queueing/operator wait; job stage durations are in runtime/jobs."},
    }
    (EV / "benchmarks.json").write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("full_real_dataset", "synthetic_scale", "incident_recovery")}, indent=1, default=str)[:4000])


if __name__ == "__main__":
    main()
