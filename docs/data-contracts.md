# Data contracts

Three modes stay physically separate in topics, lake paths, publication pointers and UI labels:

| Mode | What it is | Where it lives |
|---|---|---|
| **historical** | Original UCI workbook rows, as converted | `data/converted/`, `data/lake/historical/`, pointer `data/serving/current.json` |
| **replay** | The same rows re-emitted through local Kafka on an accelerated clock. Not a live retailer integration. | topic `meridian.replay.<generation>`, `data/lake/replay/<generation>/`, pointer `data/serving/replay.json` |
| **synthetic** | Seeded incident events, the hand-calculated fixture and the scale-test profile. Never observed sales. | topic `meridian.synthetic.<generation>`, `data/lake/synthetic/`, `data/synthetic/` |

## 1. Source row contract (historical)

Produced by `python -m meridian.cli convert` from the pinned workbook (`SOURCE.lock.json`). Every value is preserved as text; typing happens in Silver.

| Field | Type | Rule |
|---|---|---|
| invoice, stock_code, description, quantity, event_time, unit_price, customer_id, country | string or null | Original cell text. Datetimes are rendered as `YYYY-MM-DD HH:MM:SS` civil time with no offset. |
| source_sha256 | string | SHA-256 of the pinned workbook |
| source_sheet | string | `Year 2009-2010` or `Year 2010-2011` |
| source_row | int64 | Excel row number (header = row 1) |
| ingested_at | string | UTC conversion time |
| conversion_version | string | `xlsx-stream-v1` |

**Logical identity:** `line_id = sha256(source_sha256 | source_sheet | source_row)`. Invoice + product is *not* unique. Identical-looking rows at different coordinates are different records.

## 2. Replay envelope contract (Kafka value, JSON)

Key: `source_identity` (= `line_id`). The replay topic has **one partition**, so the total event-time order of the deterministic replay is preserved. The incident topic has three partitions.

```json
{
  "schema_version": 1,
  "mode": "replay | synthetic",
  "revision": 1,
  "source_identity": "<sha256 line_id>",
  "source": {"sha256": "...", "sheet": "Year 2010-2011", "row": 12345},
  "event_time": "2010-12-01 08:26:00",
  "replay_time": "2026-11-16T20:48:31.120Z",
  "delivery_id": "<uuid per send>",
  "producer_run_id": "producer-1a2b3c4d",
  "incident": null,
  "payload": {"invoice": "...", "stock_code": "...", "description": "...", "quantity": "6",
              "unit_price": "2.55", "customer_id": "17850", "country": "United Kingdom"}
}
```

- `event_time` is source civil time. `replay_time` is the UTC emit time, so processing lag is measured against it, never against the 2009–2011 dataset age.
- `delivery_id` identifies a delivery. `source_identity` + `revision` identifies the business record. Delivery metadata (Kafka topic, partition and offset, delivery ID, batch ID) is kept in Bronze, separate from source identity.

### Schema versions

| Version | Status | Handling |
|---|---|---|
| 1 | supported | baseline contract |
| 2 | supported, **additive** | v1 plus optional `payload.channel`. Accepted; `channel` is retained in Silver. It does not change the payload hash, so a v2 re-delivery of a v1 row is still a duplicate. |
| 3+ | incompatible | quarantined as `incompatible_schema` (e.g. the demo's renamed/retyped `qty` field) |

### Envelope validation (applied before business rules)

| Order | Reason | Condition |
|---|---|---|
| 1 | `malformed_json` | value does not parse as an envelope |
| 2 | `incompatible_schema` | `schema_version` not in {1, 2} |
| 3 | `missing_required_field` | any of source identity, source sha/sheet/row, event_time, revision, invoice, stock_code, quantity, unit_price is null |
| 4 | `identity_mismatch` | `source_identity` ≠ sha256 of the declared coordinates |
| 5 | `identity_payload_conflict` | same identity + revision with a different payload hash than the stored or co-delivered payload. Never resolved by picking one. |

Valid envelopes then pass through the **same** `sql/silver.sql` as batch. Business quarantine (invalid quantity, negative price, etc.) and named exclusions apply identically.

### Delivery classification

| Class | Meaning | Effect |
|---|---|---|
| new | first time this identity + revision is seen | applied (or routed to late path) |
| within_batch_duplicate | repeat in the same micro-batch | counted, not applied |
| cross_batch_duplicate | identity + revision already in the revision ledger with the same payload hash | counted, not applied |
| identity_payload_conflict | see above | quarantined |

### Revisions and corrections

A correction keeps the original identity and carries `revision` > 1. The revision ledger keeps every revision. Silver keeps the highest revision (`MERGE … WHEN MATCHED AND s.revision > t.revision`). A lower revision arriving later never overwrites a higher one. A re-export with the same revision is a duplicate, not a new sale.

## 3. Event-time policy and late arrivals

- **Watermark** = max valid event time committed by *earlier* micro-batches − `allowed_lateness_hours` (168 h).
- Rows older than the watermark are **late_beyond_window**. They go to the durable `late_arrivals` Delta table with status `pending`, are never dropped, and are not applied by the stream.
- Rows older than the previous max but inside the window are `out_of_order_within_window`. They are applied normally and counted.
- **Publication is blocked** while any late financial row is pending. The last validated snapshot stays current.
- `replay-backfill` applies pending rows with the same revision-aware MERGE, recomputes only the affected business dates (`sql/replay/daily_sales_incremental.sql`), and marks rows `applied`. Re-running with `--force` re-applies everything and must change nothing. The repair job asserts that.

Spark's built-in `withWatermark` is not used for this decision. With stateful aggregates it silently drops data older than the watermark, which is unacceptable for financial adjustments.

## 4. Replay lake tables (`data/lake/replay/<generation>/`)

| Table | Grain | Write |
|---|---|---|
| bronze | one received Kafka record (topic, partition, offset) | idempotent append (`txnAppId`/`txnVersion` = batch id) |
| quarantine | one rejected envelope or conflicting delivery | idempotent append |
| late_arrivals | one late identity + revision | idempotent append; status updated by backfill MERGE |
| revisions | one identity + revision | insert-only MERGE |
| silver_current | one source identity at its highest revision | revision-aware MERGE |
| daily_sales | one observed business date | incremental MERGE for affected dates |
| batch_metrics | one micro-batch | idempotent append |

## 5. Serving snapshot contract

A snapshot directory under `data/serving/snapshots/<id>/` contains explicitly exported Parquet tables read from pinned Delta versions, plus a `manifest.json`. The manifest records source checksum, mode, profile, Delta versions, file checksums, SQL checksums, metrics and reconciliation. A pointer file (`current.json` for historical, `replay.json` for replay) is replaced atomically only after validation. Readers never glob Delta table directories.
