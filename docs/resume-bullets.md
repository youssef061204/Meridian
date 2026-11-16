# Resume bullets

Each bullet uses only executed, recorded results. The evidence is linked under it.

1. **Built a local Spark/Delta Lake lakehouse that reconciles all 1,067,371 rows of the UCI Online Retail II dataset to a £19.38M net-sales figure.** It uses version-controlled SQL, a stable source-row identity and decimal money rules. The result was independently re-verified with DuckDB: identical line identities and exact GBP totals, and 0 time-zone shifts across 563,573 summer rows.
   *Evidence:* [full-batch.json](../evidence/full-batch.json), [full-snapshot-check.json](../evidence/full-snapshot-check.json), [full-baseline.json](../evidence/full-baseline.json)

2. **Engineered Kafka → Spark Structured Streaming recovery with idempotent Delta writes, revision-aware MERGE and a durable late-arrival backfill.** The system passed 21/21 fault-injection checks: process kills at commit boundaries, malformed and schema-breaking events, conflicting re-exports and a correction. It caught a dashboard-style overstatement of about £3.1K on a 4,000-row slice and published a snapshot that reconciled exactly with batch. REPLAY_BULLET_SUFFIX
   *Evidence:* [replay-drill.json](../evidence/replay-drill.json), [browser-demo.json](../evidence/browser-demo.json), [benchmarks.json](../evidence/benchmarks.json)

3. **Delivered leakage-tested 7- and 28-day sales forecasts with rolling-origin evaluation and an untouched test month.** The model was promoted on validation only and cut 28-day aggregate WAPE by 14.8% versus a seasonal-naive baseline. I reported that its 80% intervals under-covered (70–78%) and that a simulated inventory policy favoured it only when shortages were cheap.
   *Evidence:* [forecast-run.json](../evidence/forecast-run.json), [forecasting.md](forecasting.md), [inventory-scenarios.md](inventory-scenarios.md)
