# Architecture

MERIDIAN is a local, free-to-run portfolio case study on the UCI Online Retail II workbook. It is not a live retailer integration, an audited accounting system or a deployed cloud service. Verified status per capability is in [HANDOFF.md](../HANDOFF.md) and [evidence/gates.json](../evidence/gates.json).

## Components (as implemented)

```text
            UCI workbook (SHA-256 pinned)
                     │  streamed in 10,000-row chunks
                     ▼
         data/converted/*.parquet  (sheet/row coordinates, checksum, ingest time)
          │                                   │
 HISTORICAL BATCH (Spark + Delta)      REPLAY (Kafka → Spark Structured Streaming → Delta)
  bronze → silver.sql → gold/*.sql       producer: deterministic order, speed, pause/resume
          │                              topic meridian.replay.<gen> (1 partition)
          │                              topic meridian.synthetic.<gen> (incidents)
          │                              foreachBatch: bronze, quarantine, late_arrivals,
          │                              revisions, silver_current, daily_sales (incremental)
          ▼                                   ▼
  pinned Delta versions → explicit Parquet snapshot → DuckDB validation → manifest
          │                                   │
   data/serving/current.json            data/serving/replay.json   (atomic pointer swap)
          └──────────────► FastAPI (DuckDB read-only) ──► React/TypeScript UI
                              ▲ allow-listed demo jobs
                     worker container (one warm Spark session; staged, retried jobs)
```

| Concern | Implementation |
|---|---|
| Source and provenance | [source.py](../src/meridian/source.py), [SOURCE.lock.json](../SOURCE.lock.json) |
| Business transformations | version-controlled SQL: [silver.sql](../sql/silver.sql), [daily_sales.sql](../sql/daily_sales.sql), [gold/](../sql/gold), [replay incremental MERGE](../sql/replay/daily_sales_incremental.sql) |
| Batch orchestration | [pipeline.py](../src/meridian/pipeline.py), [gold.py](../src/meridian/gold.py); run manifests in `runtime/runs/` |
| Replay, incidents | [replay.py](../src/meridian/replay.py) |
| Streaming consumer, backfill, publish, reset | [stream.py](../src/meridian/stream.py) |
| Job orchestration (stages, dependencies, retries) | [worker.py](../src/meridian/worker.py) |
| Fault-injection drill | [drill.py](../src/meridian/drill.py) |
| Fair simple baseline | [baseline.py](../src/meridian/baseline.py) (DuckDB, same SQL) |
| Analysis, forecasting, inventory simulation | [analysis.py](../src/meridian/analysis.py), [forecast.py](../src/meridian/forecast.py), [inventory.py](../src/meridian/inventory.py) |
| Serving | [api.py](../src/meridian/api.py), [frontend/src](../frontend/src) |

There is no orchestration framework (Airflow, Dagster). A few named stages with explicit dependencies, retries and JSON state are enough at this scale. A framework would add a scheduler, a metadata database and UI operations without solving a problem the project has.

## Modes and isolation

Historical, replay and synthetic data use separate lake roots, Kafka topics, checkpoints and pointers, and carry a `mode` field that the UI renders as a badge. Each replay *generation* is isolated: its own topics, `data/lake/replay/<generation>/`, checkpoint and Delta `txnAppId`. Synthetic snapshots (the fixture and the scale profile) never replace `current.json`. See [data-contracts.md](data-contracts.md).

## Publication

Delta commits each table separately, so MERIDIAN does not claim a cross-table transaction. Publication does the following:
1. Pin each table's version.
2. Export it with a Delta-aware `versionAsOf` read. Delta Parquet files are never globbed.
3. Validate the exported set with DuckDB (identity uniqueness, the count and GBP bridges, dimension join preservation, and, for replay, incremental-vs-full daily equality and exact batch equivalence).
4. Write a manifest.
5. Atomically replace one pointer file.

Readers resolve the pointer once per request. A failed, blocked or interrupted publication leaves the previous pointer, and the UI shows the snapshot's publication age and the failed attempt. A single writer is enforced with an OS file lock; concurrent publishers are out of scope.

## Streaming write semantics

`foreachBatch` is at-least-once:
- Appends use Delta idempotent writes (`txnAppId`, `txnVersion = batch_id`).
- Silver and the revision ledger use MERGE keyed on identity and revision.
- The watermark decision uses only metrics committed by *earlier* batches, so a retried batch is classified identically.

The recovery drill kills real processes at the Bronze and checkpoint boundaries and verifies the end state ([evidence/replay-drill.json](../evidence/replay-drill.json)). Kafka + Spark do not make business results exactly-once on their own. These idempotent effects do.

## When a simpler DuckDB-only batch is the better choice

At ~1M lines over two years, a scheduled single-process DuckDB job over the same converted Parquet and SQL is the cheaper, simpler design for a dashboard refresh. It has no JVM, broker, checkpoints or streaming failure modes. The measured comparison is in [evidence/benchmarks.json](../evidence/benchmarks.json).

The Spark/Kafka/Delta path is justified only by what this project demonstrates:
- deterministic replay;
- idempotent incremental processing with revisions;
- durable late-arrival backfill;
- versioned tables for reproducible exports and safe reset;
- a migration path to Databricks/Event Hubs if volumes grow.

The synthetic scale run shows where each engine stands on this hardware.

## Runtime

Docker isolates Python 3.11 and Java 17 (the host has Java 21, which Spark 3.5 does not support). The containers and their memory limits:

| Container | Configuration | Memory limit |
|---|---|---|
| tools / worker | Spark `local[2]`, 768 MiB driver (1,400 MiB for the full replay and scale benchmarks) | 2,300 MiB |
| Kafka | KRaft single node, 256 MiB heap | 600 MiB |
| API | — | 400 MiB |

Pinned versions: Spark 3.5.7, Delta 3.3.3, Scala 2.12, spark-sql-kafka 3.5.7, Kafka 4.1.2 (digest-pinned), DuckDB 1.2.2. Python dependencies are hash-locked (`requirements.lock`, `uv.lock`). See [dependency-compatibility.md](dependency-compatibility.md).

The API binds to `127.0.0.1` only. Demo controls can enqueue four allow-listed jobs and nothing else, and they need an `X-Meridian-Demo` header, which forces a CORS preflight that the API never grants. Kafka is also loopback-bound. Neither is safe to expose beyond a local machine.

## Cloud portability

[azure-migration.md](azure-migration.md) maps these components to ADLS Gen2, Azure Databricks (Unity Catalog, Lakeflow Jobs), Event Hubs and Power BI. Its status:
- The Databricks bundle and notebooks are **prepared but not executed** in Databricks.
- Their SQL contract was checked locally.
- Power BI exports and DAX are prepared. The native report is unfinished because Power BI Desktop is not available here.
