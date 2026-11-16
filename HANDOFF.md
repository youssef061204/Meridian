# MERIDIAN handoff

Specification: `MERIDIAN_Codex_Project_Prompt.md`. Repository: `C:\Users\USER\Projects\Meridian`. Machine-readable gate state: [evidence/gates.json](evidence/gates.json). Benchmarks: [evidence/benchmarks.json](evidence/benchmarks.json).

## Gate status

| Gate | State | Key evidence |
|---|---|---|
| 1. Source slice → Spark/Delta → reconciled KPI in UI | **passed** | `browser-gate1.json`, `execution/demo-locked.json`, `execution/fixture-oracle.json` |
| 2. Full history, dimensional model, SQL analyses, publication checks | **passed** | `full-batch.json`, `full-snapshot-check.json`, `full-baseline.json`, `browser-gate2.json`, `analysis/report.json` |
| 3. Kafka replay, incidents, recovery, batch/replay reconciliation | **passed** | `kafka-smoke.json`, `replay-drill.json` (21/21), `browser-demo.json` (11/11), full replay in `benchmarks.json` |
| 4. Forecast baselines, rolling evaluation, untouched test | **passed** | `forecast-run.json`, `analysis/forecast/metrics.json` |
| 5. Walkthrough, benchmarks, case study, interview materials | see `gates.json` | `browser-demo.json`, `benchmarks.json`, `clean-checkout.json`, `docs/` |
| 6. Inventory scenarios; Databricks / Power BI | inventory **passed (simulated)**; Databricks **prepared, unexecuted**; Power BI exports **verified-local**, native report **unfinished** | `inventory-run.json`, `databricks-contract-local.json`, `powerbi-export.json` |

Nothing has run in Azure, Databricks or Power BI. No paid or cloud resources were used.

## Environment

Windows 11, Docker Desktop 29.3.1 / WSL2, i7-14700HX (28 logical CPUs), 15.7 GiB RAM. Often only 1.4–1.7 GiB is free because unrelated containers (`atlas-*`, `forge-*`, `ledgermatch-*`) run alongside; leave them alone. Docker VM 7.6 GiB.
- **Host:** Python 3.11.9 `.venv` for analysis, forecasting, tests and browser checks. Host Java 21 is unsupported by Spark 3.5, so Spark runs only in the Java 17 container.
- **Pinned runtime:** Spark 3.5.7, Delta 3.3.3, Scala 2.12.18, Java 17.0.21, Python 3.11.11 (container), spark-sql-kafka 3.5.7, `apache/kafka:4.1.2@sha256:5cc2a2fd…`, DuckDB 1.2.2.

## Commands

See [README.md](README.md) for the startup path. Additional commands:

```powershell
# Evidence wrapper: retains log, exit code, duration, sampled container memory
.venv\Scripts\python.exe scripts\run_evidence.py <label> --container <name> -- <command>

# Replay operations (inside: docker compose run --rm --no-deps tools python -m meridian.cli ...)
replay-init --profile demo|full | replay-produce [--speed N] | replay-consume | replay-incident
replay-backfill [--force] | replay-publish [--fail-at after-export] | replay-reset | replay-status
replay-control pause|resume | replay-drill | job incident|repair|reset|interrupted-publish | worker

# Scale and benchmarks
docker compose run --rm --no-deps tools python -m meridian.cli synthetic --copies 10
docker compose run --rm --no-deps -e MERIDIAN_SPARK_DRIVER_MEMORY=1400m tools python -m meridian.cli batch --profile synthetic
docker compose run --rm --no-deps tools python -m meridian.cli baseline --profile synthetic
.venv\Scripts\python.exe scripts\benchmark_queries.py
.venv\Scripts\python.exe scripts\benchmark_summary.py

# Verification
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe tests\check_full_snapshot.py
.venv\Scripts\python.exe scripts\verify_browser.py ; scripts\verify_browser_gate2.py ; scripts\verify_browser_demo.py
.venv\Scripts\python.exe scripts\check_databricks_contract.py
.venv\Scripts\python.exe scripts\export_powerbi.py --format both
```

## Current state

- **Historical serving** (`data/serving/current.json`): full snapshot `20261116T192553-472ac294`, net £19,379,245.82.
- **Replay serving** (`data/serving/replay.json`): the demo generation recorded in `runtime/replay/config.json`. Its baseline is "incomplete: 12 withheld rows" by design. The UI's Inject / Repair / Interrupt / Reset buttons operate on it through the `worker` container.
- **Full-replay benchmark generation:** `runtime/replay/full-benchmark-config.json`.

## Notable failures, fixed and retained as evidence

| Failure | Cause and fix | Evidence |
|---|---|---|
| Host DuckDB baseline crashed (0xC0000005) | Ran under low free RAM; rerun in the Spark container | `execution/full-baseline-host-attempt1.*` |
| foreachBatch failed | Temp views were created in the cloned micro-batch session; now uses `frame.sparkSession` throughout | first drill attempts in `runtime/runs/` |
| Watermark type error | Watermark compared a string with a datetime; now parsed as `TIMESTAMP_NTZ` | first drill attempts in `runtime/runs/` |
| Replay ordering not guaranteed | Replay topic was created with 3 partitions; now 1 for total order | demo and drill late counts were still exactly as expected |
| Full replay producer stalled at ~535 events/s | Per-message `stat` of the pause file on the Windows bind mount, plus gzip; now polled every 0.25 s, uncompressed, streamed. Measured 7,168 events/s on 50k. | `execution/replay-full-attempt{1,2}-stopped.*` |

## Correctness decisions

- **Identity:** `sha256(workbook sha | sheet | row)`.
  - Repeat deliveries are deduplicated by identity + revision + payload hash.
  - The same identity + revision with different content is quarantined.
  - Corrections use explicit revisions.
- **Workbook overlap:** 22,202 shared payload groups are kept and flagged for client reconciliation.
- **Money:** decimal arithmetic, half-up per line. Source-row and delivery denominators are reported separately.
- **Time:** source civil time is `TIMESTAMP_NTZ`. The watermark uses only earlier batches. Late rows go to durable backfill and block publication.
- **Publication:** pinned Delta versions → explicit Parquet export → DuckDB validation → atomic pointer. Single writer (file lock).

## Remaining work and known limits

- Databricks: run the prepared bundle in a Free Edition workspace, if one becomes available, and record evidence (`databricks/README.md`).
- Power BI: build the native report from `powerbi/SETUP.md` in Power BI Desktop and reconcile it to `powerbi/reconciliation.json`.
- The December 2010 overlap needs client line keys.
- Forecast intervals under-cover and need recalibration or widening.
- Benchmarks are single-machine with one repetition per pipeline run.
