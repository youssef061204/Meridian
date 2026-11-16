"""Accelerated historical replay producer and seeded synthetic incident injector.

Three modes stay physically separate:
  * historical  - the converted workbook rows (never modified here)
  * replay      - those rows re-emitted on the replay topic with a replay clock
  * synthetic   - seeded incident events on a separate incident topic

Replay is not a live retailer integration. Synthetic events are never additional
observed transactions: every incident targets an existing source identity or is
deliberately invalid, and is labelled mode="synthetic" with an incident type.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
import uuid
from datetime import datetime

from .common import DATA, RUNTIME, atomic_json, now, read_json

BOOTSTRAP = os.environ.get("MERIDIAN_KAFKA_BOOTSTRAP", "kafka:19092")
STATE = RUNTIME / "replay"
CONTROL = STATE / "control.json"
SEED = 20261116
WITHHELD_ROWS = 12          # real source rows withheld from baseline replay; delivered later as late arrivals
PAYLOAD_FIELDS = ["invoice", "stock_code", "description", "quantity", "event_time", "unit_price", "customer_id", "country"]


def line_identity(source_sha256, sheet, row):
    return hashlib.sha256(f"{source_sha256}|{sheet}|{row}".encode()).hexdigest()


def config():
    path = STATE / "config.json"
    return read_json(path) if path.exists() else None


def new_generation(profile):
    """A generation is an isolated replay environment: topics, lake, checkpoint, txn app id."""
    generation = datetime.utcnow().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    cfg = {"generation": generation, "profile": profile,
           "replay_topic": f"meridian.replay.{generation}", "incident_topic": f"meridian.synthetic.{generation}",
           "app_id": f"meridian-replay-{generation}", "lake": f"data/lake/replay/{generation}",
           "checkpoint": f"data/lake/replay/{generation}/_checkpoint", "allowed_lateness_hours": 168,
           "max_offsets_per_trigger": 1500, "seed": SEED, "withheld_rows": WITHHELD_ROWS, "created_at": now()}
    atomic_json(STATE / "config.json", cfg)
    return cfg


def _admin_create(topics, partitions=3):
    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import TopicAlreadyExistsError
    admin = KafkaAdminClient(bootstrap_servers=BOOTSTRAP, client_id="meridian-admin")
    try:
        for topic in topics:
            try:
                admin.create_topics([NewTopic(topic, num_partitions=partitions, replication_factor=1)])
            except TopicAlreadyExistsError:
                pass
    finally:
        admin.close()


def _producer():
    from kafka import KafkaProducer
    # Idempotent producer removes broker-level duplicates from producer retries only.
    # Application-level repeats (re-exports, replays) are handled by source identity downstream.
    return KafkaProducer(bootstrap_servers=BOOTSTRAP, acks="all", enable_idempotence=True, linger_ms=20,
                         batch_size=256 * 1024, compression_type="gzip", key_serializer=str.encode,
                         value_serializer=lambda v: v if isinstance(v, bytes) else json.dumps(v, separators=(",", ":")).encode())


def source_rows(profile):
    import pyarrow.parquet as pq
    path = DATA / "converted" / ("demo.parquet" if profile == "demo" else "historical.parquet")
    table = pq.read_table(path, columns=[*PAYLOAD_FIELDS, "source_sha256", "source_sheet", "source_row"])
    rows = table.to_pylist()
    # Deterministic replay order: source event time, then original coordinates.
    rows.sort(key=lambda r: (r["event_time"] or "", r["source_sheet"], r["source_row"]))
    return rows


def withheld_identities(rows, cfg):
    """Seeded choice of early rows withheld from baseline replay (the late-arrival scenario)."""
    early = [r for r in rows if r["event_time"] and r["event_time"] < rows[-1]["event_time"][:4]]
    candidates = early or rows[: len(rows) // 2]
    chosen = random.Random(cfg["seed"]).sample(candidates, min(cfg["withheld_rows"], len(candidates)))
    return {line_identity(r["source_sha256"], r["source_sheet"], r["source_row"]) for r in chosen}


def envelope(row, *, run_id, mode="replay", revision=1, schema_version=1, incident=None, payload_override=None, extra=None):
    payload = {k: row[k] for k in PAYLOAD_FIELDS if k != "event_time"}
    payload.update(payload_override or {})
    value = {"schema_version": schema_version, "mode": mode, "revision": revision,
             "source_identity": line_identity(row["source_sha256"], row["source_sheet"], row["source_row"]),
             "source": {"sha256": row["source_sha256"], "sheet": row["source_sheet"], "row": row["source_row"]},
             "event_time": row["event_time"], "replay_time": now(), "delivery_id": uuid.uuid4().hex,
             "producer_run_id": run_id, "incident": incident, "payload": payload}
    if extra:
        value["payload"].update(extra)
    return value


def _wait_if_paused(stats):
    while CONTROL.exists() and read_json(CONTROL).get("paused"):
        stats["paused_polls"] = stats.get("paused_polls", 0) + 1
        time.sleep(0.2)


def set_paused(paused):
    atomic_json(CONTROL, {"paused": bool(paused), "changed_at": now()})


def replay(profile="demo", speed=0.0, max_sleep=1.0, limit=None, include_withheld=False):
    """Emit historical rows in deterministic order. speed = source seconds per wall second (0 = unthrottled)."""
    cfg = config()
    if not cfg or cfg["profile"] != profile:
        raise RuntimeError("Initialise a replay generation for this profile first (replay-init)")
    _admin_create([cfg["replay_topic"], cfg["incident_topic"]])
    rows = source_rows(profile)
    held = set() if include_withheld else withheld_identities(rows, cfg)
    run_id = "producer-" + uuid.uuid4().hex[:8]
    stats = {"producer_run_id": run_id, "mode": "replay", "topic": cfg["replay_topic"], "speed": speed,
             "started_at": now(), "sent": 0, "withheld": len(held)}
    producer = _producer()
    start, previous = time.perf_counter(), None
    try:
        for row in rows[:limit] if limit else rows:
            identity = line_identity(row["source_sha256"], row["source_sheet"], row["source_row"])
            if identity in held:
                continue
            _wait_if_paused(stats)
            if speed and previous and row["event_time"] and previous["event_time"]:
                gap = (datetime.fromisoformat(row["event_time"]) - datetime.fromisoformat(previous["event_time"])).total_seconds()
                if gap > 0:
                    time.sleep(min(gap / speed, max_sleep))
            producer.send(cfg["replay_topic"], key=identity, value=envelope(row, run_id=run_id))
            stats["sent"] += 1
            previous = row
        producer.flush()
    finally:
        producer.close()
    elapsed = time.perf_counter() - start
    stats.update(finished_at=now(), seconds=round(elapsed, 4), events_per_second=round(stats["sent"] / elapsed, 1) if elapsed else None)
    atomic_json(STATE / "producers" / f"{run_id}.json", stats)
    return stats


def inject_incident(profile="demo"):
    """Seeded synthetic incident on the separate incident topic. Returns the exact manifest of what was sent."""
    cfg = config()
    rows = source_rows(profile)
    held = withheld_identities(rows, cfg)
    by_id = {line_identity(r["source_sha256"], r["source_sheet"], r["source_row"]): r for r in rows}
    rng = random.Random(cfg["seed"] + 1)
    sent_rows = [r for i, r in by_id.items() if i not in held]
    # Duplicates of rows whose quantity/price parse, so the unsafe sum visibly double counts.
    candidates = [r for r in sent_rows if r["quantity"] and r["unit_price"] and not str(r["invoice"]).startswith("C")]
    duplicates = rng.sample(candidates, 40)
    run_id = "incident-" + uuid.uuid4().hex[:8]
    topic = cfg["incident_topic"]
    events, manifest = [], {"incident_run_id": run_id, "mode": "synthetic", "topic": topic, "seed": cfg["seed"] + 1, "sent_at": now(), "counts": {}}

    def add(kind, key, value):
        events.append((key, value))
        manifest["counts"][kind] = manifest["counts"].get(kind, 0) + 1

    for row in duplicates[:25]:          # same row delivered twice inside one micro-batch
        for _ in range(2):
            add("repeat_delivery", line_identity(row["source_sha256"], row["source_sheet"], row["source_row"]),
                envelope(row, run_id=run_id, mode="synthetic", incident="repeat_delivery"))
    for row in duplicates[25:37]:        # additive schema v2: new optional field, otherwise identical
        add("additive_schema_v2", line_identity(row["source_sha256"], row["source_sheet"], row["source_row"]),
            envelope(row, run_id=run_id, mode="synthetic", schema_version=2, incident="additive_schema_v2", extra={"channel": "web"}))
    for index in range(5):               # bytes that are not JSON at all
        add("malformed_json", f"malformed-{index}", b'{"schema_version": 1, "payload": {"invoice": "4893' + str(index).encode())
    for row in duplicates[37:40]:        # required fields removed
        value = envelope(row, run_id=run_id, mode="synthetic", incident="missing_required_field")
        value["payload"].pop("invoice"); value["payload"].pop("unit_price")
        add("missing_required_field", value["source_identity"], value)
    for row in duplicates[:2]:           # incompatible schema v3: renamed/retyped fields
        value = envelope(row, run_id=run_id, mode="synthetic", schema_version=3, incident="incompatible_schema_v3")
        value["payload"]["qty"] = {"value": value["payload"].pop("quantity")}
        add("incompatible_schema_v3", value["source_identity"], value)
    conflict = duplicates[2]              # same identity + revision, different payload: must not pick one silently
    add("identity_payload_conflict", line_identity(conflict["source_sha256"], conflict["source_sheet"], conflict["source_row"]),
        envelope(conflict, run_id=run_id, mode="synthetic", incident="identity_payload_conflict",
                 payload_override={"quantity": str(int(float(conflict["quantity"])) * 10)}))
    # Withheld real rows arrive now: beyond the allowed lateness, so they must route to backfill.
    for identity in sorted(held):
        add("late_arrival_withheld_source_row", identity, envelope(by_id[identity], run_id=run_id, mode="replay", incident="late_arrival"))
    for identity in sorted(held)[:4]:   # never-seen rows delivered twice in the same micro-batch
        add("repeat_delivery_within_batch", identity, envelope(by_id[identity], run_id=run_id, mode="replay", incident="repeat_delivery_within_batch"))
    # Explicit correction: revision 2 of one accepted source row with a corrected unit price.
    target = sorted(candidates, key=lambda r: (r["event_time"], r["source_sheet"], r["source_row"]))[0]
    corrected_price = f"{float(target['unit_price']) * 0.5:.2f}"
    add("correction_revision_2", line_identity(target["source_sha256"], target["source_sheet"], target["source_row"]),
        envelope(target, run_id=run_id, mode="synthetic", revision=2, incident="correction",
                 payload_override={"unit_price": corrected_price}))
    manifest["correction"] = {"source_identity": line_identity(target["source_sha256"], target["source_sheet"], target["source_row"]),
                              "source_sheet": target["source_sheet"], "source_row": target["source_row"],
                              "quantity": target["quantity"], "original_unit_price": target["unit_price"], "corrected_unit_price": corrected_price}
    producer = _producer()
    try:
        # Out-of-order delivery: shuffle the incident events deterministically.
        order = list(range(len(events)))
        rng.shuffle(order)
        for index in order:
            producer.send(topic, key=events[index][0], value=events[index][1])
        # Cross-batch repeats: the first 10 duplicate rows again, flushed separately so they land later.
        producer.flush()
        for row in duplicates[:10]:
            key = line_identity(row["source_sha256"], row["source_sheet"], row["source_row"])
            producer.send(topic, key=key, value=envelope(row, run_id=run_id, mode="synthetic", incident="repeat_delivery_later"))
            manifest["counts"]["repeat_delivery_later"] = manifest["counts"].get("repeat_delivery_later", 0) + 1
        producer.flush()
    finally:
        producer.close()
    manifest["total_events"] = sum(manifest["counts"].values())
    atomic_json(STATE / "incidents" / f"{run_id}.json", manifest)
    return manifest
