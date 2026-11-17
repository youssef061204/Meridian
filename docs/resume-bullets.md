# Resume bullets

Each bullet uses only executed, recorded results. The evidence is linked under it. All timings come from one laptop (i7-14700HX, 16 GB RAM, Docker Desktop on WSL2, Spark `local[2]`); they are not cluster benchmarks.

## Primary bullets

1. **Built a local Spark/Delta Lake lakehouse that reconciles all 1,067,371 rows of the UCI Online Retail II dataset to a £19.38M net-sales figure.** It uses version-controlled SQL, a stable source-row identity and decimal money rules. The result was independently re-verified with DuckDB: identical line identities and exact GBP totals, and 0 time-zone shifts across 563,573 summer rows.
   *Evidence:* [full-batch.json](../evidence/full-batch.json), [full-snapshot-check.json](../evidence/full-snapshot-check.json), [full-baseline.json](../evidence/full-baseline.json)

2. **Engineered Kafka → Spark Structured Streaming recovery with idempotent Delta writes, revision-aware MERGE and a durable late-arrival backfill.** The system passed 21/21 fault-injection checks: process kills at commit boundaries, malformed and schema-breaking events, conflicting re-exports and a correction. It caught a dashboard-style overstatement of about £3.1K on a 4,000-row slice and published a snapshot that reconciled exactly with batch. It also replayed the full 1.07M-row history through Kafka with an exact batch match.
   *Evidence:* [replay-drill.json](../evidence/replay-drill.json), [browser-demo.json](../evidence/browser-demo.json), [benchmarks.json](../evidence/benchmarks.json)

3. **Delivered leakage-tested 7- and 28-day sales forecasts with rolling-origin evaluation and an untouched test month.** The model was promoted on validation only and cut 28-day aggregate WAPE by 14.8% versus a seasonal-naive baseline. I reported that its 80% intervals under-covered (70–78%) and that a simulated inventory policy favoured it only when shortages were cheap.
   *Evidence:* [forecast-run.json](../evidence/forecast-run.json), [forecasting.md](forecasting.md), [inventory-scenarios.md](inventory-scenarios.md)

## Alternative bullets (pick by role)

- **Data engineering / performance:** Cut API latency from 5–10 s to a ~0.3 s warm p50 by diagnosing slow WSL2 bind-mount reads and adding a checksum-verified local snapshot cache. The served JSON was proven identical before and after. A pre-aggregated daily mart answered trend queries 23.4× faster (p50) than a fact-table scan, with identical results. *Evidence:* [api-serving-optimization.json](../evidence/api-serving-optimization.json), [benchmark-queries.json](../evidence/benchmark-queries.json)
- **Streaming throughput:** Raised the Kafka replay producer from ~535 to 7,842 events/s (about 14.7×) by removing a per-message file `stat` on a Windows bind mount and dropping gzip. The full 1,067,371-event history then went through Kafka → Spark → Delta at 3,342 deliveries/s, reconciled to the penny. *Evidence:* [benchmarks.json](../evidence/benchmarks.json) → `replay_full_history`, `execution/replay-full-attempt*`
- **Scale and memory:** Ran a 10.7M-row synthetic stress profile within a 2.3 GiB container: Spark 355.6 s, DuckDB 123.3 s, exact equivalence on 10,616,640 lines. I fixed two out-of-memory failures by replacing an `EXCEPT ALL` with a 16-way partitioned equivalence proof. *Evidence:* [benchmarks.json](../evidence/benchmarks.json) → `synthetic_scale`
- **Reliability / operability:** Built a one-click break-and-repair demo, UI → loopback API → allow-listed worker → Kafka/Spark/Delta. It injects a controlled incident, blocks publication and recovers to a validated snapshot in 151.7 s. It passed 11/11 automated browser steps with zero console errors. *Evidence:* [browser-demo.json](../evidence/browser-demo.json)
- **Analytics engineering:** Modelled a Gold star schema (product, customer, geography and date dimensions, plus cohort retention and quality marts), published through pinned Delta versions → Parquet → DuckDB validation → an atomic pointer swap. A failed validation leaves the last good snapshot serving. *Evidence:* [data-model.md](data-model.md), [architecture.md](architecture.md)

## Metrics bank

| Metric | Value | Evidence |
|---|---|---|
| Source rows reconciled | 1,067,371 → 1,061,664 accepted / 5,701 named exclusions / 6 quarantined (100% accounted for) | `full-batch.json` |
| Net recorded sales | £19,379,245.82 (gross £20,112,074.02, adjustments −£732,828.20) | `full-batch.json` |
| Full Spark + Delta pipeline | 125.0 s Spark stage, 14 tables, peak 1.86 GiB | `full-batch.json` |
| DuckDB-only baseline | 17.3 s transform/export, identical lines and GBP | `full-baseline.json` |
| Full Kafka replay | 1,067,371 events; producer 7,842 events/s; consumer 3,342 deliveries/s; 0 missing, 0 extra, £0.00 difference vs batch | `benchmarks.json` |
| Fault-injection drill | 21/21 checks | `replay-drill.json` |
| Browser demo | 11/11 steps, recovery 151.7 s, 0 console errors | `browser-demo.json` |
| Unsafe-sum overstatement caught | £3,066.89 on the 4,000-row demo slice | `browser-demo.json` |
| Synthetic scale | 10,673,710 rows, exact Spark/DuckDB equivalence | `benchmarks.json` |
| API latency | warm p50: overview 315 ms, lineage 301 ms, trust 79 ms (was 5–10 s) | `benchmark-queries.json` |
| Daily mart speedup | 23.4× (p50) vs fact scan, identical results | `benchmark-queries.json` |
| Forecast | 28-day WAPE −14.8%, 7-day −3.1% vs seasonal naive (untouched test month) | `forecast-run.json` |
| Tests | 46 pytest tests plus a Spark fixture oracle; clean-clone README run passed | `clean-checkout.json` |

## Do not claim

- Production, cloud, Databricks or Power BI execution: none ran. The bundle and DAX are prepared only.
- Real revenue, cash or profit: these are recorded invoice values.
- Inventory savings: the scenarios are simulated, and the model's policy cost 22.5% more than a fixed policy in the base scenario.
- Calibrated forecast intervals: they under-cover.
