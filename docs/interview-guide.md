# Interview guide

Short, defensible answers with pointers to code and evidence. Every number here comes from a recorded run.

## 1. Architecture and trade-offs

**What is the architecture?**
1. UCI workbook, streamed in a bounded way to Parquet ([source.py](../src/meridian/source.py)).
2. PySpark + Delta Bronze/Silver/Gold ([pipeline.py](../src/meridian/pipeline.py), [sql/](../sql)).
3. Export of pinned Delta versions to an immutable Parquet snapshot plus a validated manifest.
4. An atomic pointer swap.
5. DuckDB → FastAPI → React for serving.

The replay path sends the same rows through single-node Kafka, into Spark Structured Streaming `foreachBatch`, then into replay Delta tables ([stream.py](../src/meridian/stream.py)).

**Why not just DuckDB?** At this size you mostly should. The fair baseline in [baseline.py](../src/meridian/baseline.py) uses the same SQL and produced the identical 1,061,664 accepted lines (EXCEPT ALL both ways) in 17.3 s, versus 125 s for the 14-table Spark/Delta run on one machine. The scopes differ, so I don't present that as a speedup. Spark, Kafka and Delta are justified by what I demonstrate:
- replay with recovery;
- idempotent incremental writes;
- table versioning and time travel (used for reset and reproducible exports);
- a path to scale out.

If the client only needs a nightly dashboard, I'd recommend DuckDB or a single SQL warehouse.

**Why a manifest + pointer instead of a database transaction?** Delta commits are per table. Publication pins every table's version, exports them, validates the set with DuckDB, writes a manifest, and then atomically replaces one pointer file. Readers resolve the pointer once per request, so they never see a mix of versions. I don't claim a cross-table transaction, and I assume a single writer, enforced by a lock file.

## 2. Event identity and duplicates

**What identifies a record?** `sha256(workbook checksum | sheet | row)`. Invoice + stock code is not unique: the data has legitimate identical lines at different coordinates, and those must both count.

**Delivery vs record?** Bronze keeps every Kafka record (topic/partition/offset, delivery_id). Silver keeps one row per identity at its highest revision. The denominators are reported separately. In the demo incident, 76 repeated deliveries were detected: 4 within a batch and 72 across batches.

**What about the same identity with different content?** Same revision with a different payload hash is an `identity_payload_conflict`. It is quarantined, never silently resolved. A deliberate correction carries `revision` 2, and the ledger keeps both.

**Exactly-once?** Kafka + Spark do not give end-to-end exactly-once business results by themselves. `foreachBatch` is at-least-once: after a crash, Spark re-runs the same batch ID over the same offsets. I make the effects idempotent:
- Delta appends use `txnAppId`/`txnVersion = batch_id`, so a replayed append is skipped.
- Silver uses MERGE on identity with `s.revision > t.revision`.
- The revision ledger is an insert-only MERGE.
- A ledger row written by a failed attempt of the same batch is recognised and not counted as a prior delivery.

The drill kills the process (`os._exit`) after the Bronze write, and again after all writes but before the checkpoint commit. Bronze ends with exactly one row per offset (3,988 rows, 3,988 offsets) and each batch is recorded once ([evidence](../evidence/replay-drill.json)). The Kafka producer is idempotent too, but that only removes broker-retry duplicates, not business re-exports.

## 3. Time semantics

**Three clocks:**
- source event time: 2009–2011 UK civil time, stored as `TIMESTAMP_NTZ`;
- replay emit time (UTC);
- processing and publication time (UTC).

Processing lag = processed − replay emit, not processed − 2011. Snapshot freshness = now − publication time.

**Why NTZ?** A timezone-aware type made DuckDB shift summer invoice times by an hour. The independent check found 0 mismatches across 563,573 April–October rows after the fix.

**Late data:**
- The watermark is the max committed event time minus 168 h.
- Older rows go to a durable `late_arrivals` table. They are not dropped, which Spark's built-in watermark would do for stateful aggregates.
- Publication is blocked while late rows are pending.
- Backfill recomputes only affected dates with an incremental MERGE ([sql](../sql/replay/daily_sales_incremental.sql)), and re-running it changes nothing (net change £0.00).

**Ordering:** the replay topic has one partition, so replay order is total and deterministic. With many partitions a global watermark can mislabel slow partitions as late. I'd use per-partition watermarking or a larger lateness window, and I'd mention it as the scaling trade-off.

## 4. Data modelling

- **Grains:**
  - fact = one accepted source row;
  - dimensions = one key each (date, product, customer + `__UNKNOWN__` member, geography);
  - daily_sales = one observed date.
- **Join safety:** joining the fact to all four dimensions keeps 1,061,664 rows and the exact £19,379,245.82. Product descriptions vary per stock code, so the dimension picks one deterministically rather than multiplying rows.
- **Money:** decimal(18,4) prices; each line rounded half-up to 2 dp once, then summed. No float aggregation.
- **Unavailable vs zero:** 135 calendar days have no source rows. They stay *unavailable*, never zero sales, in the marts and the forecast.
- **Cancellations:** a negative quantity is an adjustment. I never invent a link to an original invoice.

## 5. Forecasting and leakage

- **Target:** positive merchandise units (observed sales, not demand).
- **Models:** weekly seasonal naive, trailing mean, and HistGradientBoosting with lag, trailing and calendar features.
- **Protocol:** origins, horizons and the interval method were written and hashed before fitting. 4 warm-up and 6 validation origins, then one untouched test month. The promotion decision used validation only.
- **Leakage test:**
  - Perturb all data after an origin: ×50 the aggregate, flip availability, inject a 10⁶-unit product. Product selection, features and every prediction must stay bit-identical.
  - A control that perturbs the 14 days before the origin must change forecasts.
- **Result:** test WAPE improved over seasonal naive by 3.1% (7-day) and 14.8% (28-day) on the aggregate. Intervals under-covered in validation (≈0.70–0.78 for nominal 0.80), and I say so.
- **Inventory simulation:** the selected model is *not* always cheapest. It loses when shortages cost ≥100× holding. I report that, and that the base-case cost ratio was revised after a first run.

## 6. Simpler alternatives I'd consider

- Nightly DuckDB / SQL warehouse batch with the same SQL and validation: cheapest and adequate at this volume.
- Change data capture from the source system instead of file exports, which would remove the overlap problem at its root.
- Databricks Auto Loader + Lakeflow Jobs instead of self-managed Kafka if the source is files ([azure-migration.md](azure-migration.md)).

## 7. What is not verified

- No Azure, Databricks or Power BI execution. Notebooks, bundle and DAX are prepared but unexecuted. The Databricks contract was checked locally only.
- Single-machine benchmarks only.
- The December 2010 overlap is unresolved without client keys.
