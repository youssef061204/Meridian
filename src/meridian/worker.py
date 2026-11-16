"""Explicit orchestration for replay jobs: named stages, dependencies, retries, persisted state.

The API never runs commands. It can only enqueue one of ALLOWED_JOBS as a JSON file;
this worker (a separate local container with one warm Spark session) executes them in
order and records every stage, retry and timing under runtime/jobs/.
"""
from __future__ import annotations

import time
import traceback
import uuid

from .common import RUNTIME, atomic_json, now, read_json, writer_lock
from .replay import STATE, config, inject_incident, new_generation, replay

JOBS = RUNTIME / "jobs"
ALLOWED_JOBS = {"incident", "repair", "reset", "interrupted-publish"}
TRANSIENT = ("NoBrokersAvailable", "KafkaTimeoutError", "TimeoutException", "ConcurrentAppendException", "ConcurrentModificationException")


def enqueue(kind, requested_by="cli"):
    if kind not in ALLOWED_JOBS:
        raise ValueError(f"Unsupported job {kind}")
    job_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job = {"job_id": job_id, "kind": kind, "state": "queued", "requested_at": now(), "requested_by": requested_by, "stages": []}
    atomic_json(JOBS / f"{job_id}.json", job)
    return job


def latest_incident_timeline(cfg):
    path = STATE / cfg["generation"] / "incident_timeline.json"
    return read_json(path) if path.exists() else {}


class Runner:
    def __init__(self, spark):
        self.spark = spark

    def stage(self, job, name, fn, retries=2, depends_on=None):
        if depends_on and not any(s["stage"] == depends_on and s["state"] == "passed" for s in job["stages"]):
            raise RuntimeError(f"Stage {name} requires {depends_on}")
        record = {"stage": name, "state": "running", "started_at": now(), "attempts": 0}
        job["stages"].append(record)
        self.save(job)
        while True:
            record["attempts"] += 1
            started = time.perf_counter()
            try:
                result = fn()
                record.update(state="passed", finished_at=now(), seconds=round(time.perf_counter() - started, 3), result=result)
                self.save(job)
                return result
            except Exception as exc:
                transient = any(marker in f"{type(exc).__name__} {exc}" for marker in TRANSIENT)
                record.setdefault("errors", []).append(f"{type(exc).__name__}: {str(exc)[:400]}")
                if transient and record["attempts"] <= retries:
                    record["retries"] = record.get("retries", 0) + 1
                    self.save(job)
                    time.sleep(2 * record["attempts"])
                    continue
                record.update(state="blocked" if type(exc).__name__ == "PublicationBlocked" else "failed",
                              finished_at=now(), seconds=round(time.perf_counter() - started, 3))
                self.save(job)
                raise

    @staticmethod
    def save(job):
        atomic_json(JOBS / f"{job['job_id']}.json", job)

    # ------------------------------------------------------------- jobs
    def init(self, profile="demo", speed=0.0):
        from .stream import consume, publish, record_baseline, write_status
        job = {"job_id": "init-" + uuid.uuid4().hex[:6], "kind": "init", "state": "running", "requested_at": now(), "stages": []}
        cfg = self.stage(job, "new_generation", lambda: new_generation(profile))
        if profile == "full":
            cfg["max_offsets_per_trigger"] = 250000
            atomic_json(STATE / "config.json", cfg)
        self.stage(job, "produce_replay", lambda: replay(profile, speed=speed))
        self.stage(job, "consume", lambda: consume(self.spark, cfg))
        self.stage(job, "publish", lambda: publish(self.spark, cfg)["id"])
        self.stage(job, "record_baseline", lambda: record_baseline(self.spark, cfg))
        self.stage(job, "status", lambda: write_status(self.spark, cfg))
        job["state"] = "passed"; job["finished_at"] = now(); self.save(job)
        return job

    def run(self, job):
        from .stream import PublicationBlocked, backfill, consume, publish, reset, write_status
        cfg = config()
        job.update(state="running", started_at=now(), generation=cfg["generation"])
        self.save(job)
        timeline_path = STATE / cfg["generation"] / "incident_timeline.json"
        try:
            with writer_lock():
                if job["kind"] == "incident":
                    timeline = {"incident_job": job["job_id"], "injected_at": now()}
                    manifest = self.stage(job, "inject_synthetic_incident", lambda: inject_incident(cfg["profile"]))
                    timeline["incident_run_id"] = manifest["incident_run_id"]
                    consumed = self.stage(job, "consume", lambda: consume(self.spark, cfg), depends_on="inject_synthetic_incident")
                    timeline["detected_at"] = now()
                    timeline["detected"] = {k: sum(b.get(k, 0) for b in consumed["batches"]) for k in
                                            ["rejected_envelopes", "within_batch_duplicates", "cross_batch_duplicates", "identity_conflicts", "late_routed_to_backfill", "additive_schema_v2"]}
                    self.stage(job, "status", lambda: write_status(self.spark, cfg))
                    try:
                        self.stage(job, "publish", lambda: publish(self.spark, cfg), retries=0)
                        timeline["publication"] = "passed"
                    except PublicationBlocked as exc:
                        timeline.update(publication="blocked", publication_blocked_at=now(), blocked_reason=str(exc))
                    atomic_json(timeline_path, timeline)
                elif job["kind"] == "repair":
                    timeline = read_json(timeline_path) if timeline_path.exists() else {}
                    self.stage(job, "consume_remaining", lambda: consume(self.spark, cfg))
                    first = self.stage(job, "backfill_late_arrivals", lambda: backfill(self.spark, cfg))
                    rerun = self.stage(job, "rerun_backfill_idempotency", lambda: backfill(self.spark, cfg, force=True), depends_on="backfill_late_arrivals")
                    if rerun["net_change"] not in ("0", "0.00"):
                        raise RuntimeError(f"Backfill re-run changed net by {rerun['net_change']}")
                    manifest = self.stage(job, "publish", lambda: publish(self.spark, cfg), retries=1, depends_on="rerun_backfill_idempotency")
                    self.stage(job, "status", lambda: write_status(self.spark, cfg))
                    if timeline.get("injected_at"):
                        from datetime import datetime
                        recovered = now()
                        timeline.update(recovered_at=recovered, recovery_snapshot=manifest["id"], backfill=first,
                                        recovery_seconds=round((datetime.fromisoformat(recovered) - datetime.fromisoformat(timeline["injected_at"])).total_seconds(), 1),
                                        recovery_scope="injected_at -> validated publication, including demo operator wait time between jobs")
                        atomic_json(timeline_path, timeline)
                elif job["kind"] == "reset":
                    self.stage(job, "restore_baseline", lambda: reset(self.spark, cfg))
                    if timeline_path.exists():
                        archive = STATE / cfg["generation"] / f"incident_timeline-{job['job_id']}.json"
                        timeline_path.replace(archive)
                elif job["kind"] == "interrupted-publish":
                    from .common import DATA
                    before = read_json(DATA / "serving" / "replay.json")
                    try:
                        self.stage(job, "publish_with_interruption", lambda: publish(self.spark, cfg, fail_at="after-export"), retries=0)
                    except RuntimeError:
                        pass
                    after = read_json(DATA / "serving" / "replay.json")
                    self.stage(job, "verify_pointer_unchanged", lambda: {"before": before, "after": after, "unchanged": before == after}
                               if before == after else (_ for _ in ()).throw(RuntimeError("pointer moved after interrupted publish")))
            job["state"] = "passed"
        except Exception as exc:
            job["state"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["traceback"] = traceback.format_exc()[-3000:]
        job["finished_at"] = now()
        self.save(job)
        return job


def worker(poll_seconds=1.0):
    from .pipeline import spark_session
    spark = spark_session()
    runner = Runner(spark)
    atomic_json(JOBS / "worker.json", {"state": "ready", "since": now()})
    print("MERIDIAN worker ready", flush=True)
    while True:
        queued = sorted(p for p in JOBS.glob("*.json") if p.name != "worker.json" and read_json(p).get("state") == "queued")
        for path in queued:
            job = read_json(path)
            atomic_json(JOBS / "worker.json", {"state": "busy", "job_id": job["job_id"], "since": now()})
            runner.run(job)
            atomic_json(JOBS / "worker.json", {"state": "ready", "since": now()})
        time.sleep(poll_seconds)
