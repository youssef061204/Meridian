# MERIDIAN handoff

Specification: `MERIDIAN_Codex_Project_Prompt.md` (user attachment copied unchanged).
Repository: `C:\Users\USER\Projects\Meridian`. Machine-readable gate state: `evidence/gates.json`.

## Gate status

| Gate | State | Evidence |
|---|---|---|
| 1. Source slice → Spark/Delta → reconciled KPI in UI | **passed** | `evidence/gates.json` → `gate_1` |
| 2. Full history, dimensional model, SQL analyses, publication checks | **passed** | `evidence/gates.json` → `gate_2` |
| 3. Kafka replay, incidents, recovery, batch/replay reconciliation | not started | — |
| 4. Forecasting baselines, rolling evaluation, untouched test | not started | — |
| 5. Walkthrough, benchmarks, case study, interview materials | not started | — |
| 6. Inventory scenarios, optional Databricks/Power BI | not started | — |

All cloud, Databricks, and Power BI capabilities are unexecuted. No paid or cloud services have been used.

## Environment (inspected 2026-11-16)

Windows 11, Docker Desktop 29.3.1 on WSL2, 28 logical CPUs (i7-14700HX), 15.71 GiB RAM (often only 1.4–1.7 GiB free because unrelated containers run; leave them alone), Docker VM 7.61 GiB, ~142 GiB free disk. Host Python 3.11.9 in `.venv` (default 3.14 is unsuitable). Host Java 21 is outside Spark 3.5 support, so Spark runs only in the Java 17 container.

Pinned runtime (verified): Spark 3.5.7, Delta 3.3.3, Scala 2.12.18, Java 17.0.21, Python 3.11.11 (container), DuckDB 1.2.2, Kafka `apache/kafka:4.1.2@sha256:5cc2a2fd93fa2687b44015eee04fb2c3edd9e526bd64bf8bec5ff1e268772e0e` (executed in the Kafka smoke below).

## Commands

```powershell
# Source download + verified bounded conversion (host)
.venv\Scripts\python.exe -m meridian.cli convert
# Build locked image
docker compose build tools
# Spark/Delta batch: profiles demo | fixture | full
docker compose run --rm --no-deps --name meridian-job tools python -m meridian.cli batch --profile full
# DuckDB baseline (run in the container; host run crashed under low free RAM)
docker compose run --rm --no-deps --name meridian-baseline tools python -m meridian.cli baseline --profile full
# Independent checks
.venv\Scripts\python.exe tests\check_spark_fixture.py
.venv\Scripts\python.exe tests\check_full_snapshot.py
.venv\Scripts\python.exe -m pytest -q
# EDA + executed notebook (host; PYTHONPATH=src)
.venv\Scripts\python.exe -m meridian.analysis --snapshot 20261116T192553-472ac294 --notebook
# API + UI
npm.cmd --prefix frontend run build
docker compose up -d api        # http://localhost:8010 (loopback only)
.venv\Scripts\python.exe scripts\verify_browser.py
.venv\Scripts\python.exe scripts\verify_browser_gate2.py
```

Wrap long runs with `scripts/run_evidence.py <label> --container <name> -- <command>` to retain log, exit code, duration, and sampled container memory under `evidence/execution/`.

## Verified results

**Source.** UCI workbook SHA-256 `bcbe73b3…2e980` pinned in `SOURCE.lock.json`. Both sheets: 525,461 + 541,910 = 1,067,371 rows, matching UCI. Date ranges 2009-12-01 07:45 to 2010-12-09 20:01 and 2010-12-01 08:26 to 2011-12-09 12:50. Conversion streamed 10,000-row chunks in 99.5 s. `evidence/source-verification.json`.

**Gate 1.** 4,000-row demo slice: 3,986 accepted / 14 excluded, net £69,754.43. Fixture: 18 deliveries / 16 source coordinates, net £35.00, matched by an independent oracle. 12 browser checks passed. Details in `evidence/gates.json`.

**Full Spark/Delta run** (commit 2939649, snapshot `20261116T192553-472ac294`): 1,067,371 source rows → 1,061,664 accepted, 6 quarantined, 5,701 excluded. Gross £20,112,074.02, negative adjustments −£732,828.20, net £19,379,245.82. 11,648,090 positive units. 14 Delta tables at v0. Spark stage 125.0 s, wrapper 128.4 s, container memory limit 2.3 GiB. `evidence/execution/full-batch.*`.

**Independent full-snapshot check** (DuckDB, 6.9 s): file and SQL checksums; one-to-one source-coordinate join; zero civil-timestamp mismatches over 563,573 April–October rows (TIMESTAMP_NTZ, no BST shift); four dimension joins preserve rows and exact GBP; unknown-customer member valid; cohort retention recomputed independently; 135 unobserved calendar days kept as unavailable (not zero); quality amount bridge balances. `evidence/full-snapshot-check.json`.

**DuckDB baseline** (same container): 17.3 s transform/export, 29.2 s wrapper. Identical 1,061,664 accepted identities, quantities, and exact line amounts versus Spark (EXCEPT ALL in both directions). It builds only fact + daily tables without Delta history, so no like-for-like speedup is claimed. The host attempt crashed (0xC0000005) and is retained as `evidence/execution/full-baseline-host-attempt1.*`.

**EDA.** 13 versioned queries, 8 charts, executed notebook (`notebooks/eda.ipynb`, 9 cells, 0 errors), client findings `docs/findings.md`. All charts were visually inspected. A bridge label collision and missing spaces in chart/finding text were fixed, then everything was re-executed.

**Gate 2 browser checks:** 9 steps passed in 130 s. Covered: full KPI with overlap caveat, comparable 28-day windows vs API, cohort selection and denominators, partial-month label, country filter, trust reconciliation vs manifest, mobile overflow, zero console errors. `evidence/browser-gate2.json`.

**Kafka compatibility smoke** (Gate 3 prerequisite, passed, commit 5b617f3 + `kafka_smoke.py`): `docker compose --profile replay up -d kafka`, then `docker compose run --rm --no-deps --name meridian-kafka-smoke tools python -m meridian.cli kafka-smoke`. 24 synthetic envelopes (20 source identities plus 4 repeat deliveries) went through an idempotent kafka-python producer, a 2-partition topic, and the Spark `spark-sql-kafka-0-10_2.12:3.5.7` `availableNow` stream into Delta. Bronze kept all 24 deliveries (24 distinct offsets and delivery IDs). Dedup by source identity gave quantity 210 = expected. A checkpoint rerun ingested 0 new rows. Duration 19.2 s. `evidence/kafka-smoke.json`. Scope: compatibility only, not the replay pipeline.

## Correctness decisions

- Line identity = sha256(source checksum | sheet | row). Only repeat deliveries of the same source identity deduplicate.
- Workbook overlap: 34,335 extra identical-looking coordinates, and 22,202 payload groups shared across sheets (22,523 pairs). All coordinates are retained and flagged for client reconciliation. No deletion rule was invented.
- Decimal half-up rounding per line. Named exclusions and quarantine retain parseable amounts. Source-row and delivery denominators are reported separately. No profit, payment, or inventory claims.
- Source times are UK civil clock in TIMESTAMP_NTZ. No timezone conversion is applied.
- The serving pointer moves only after every table export validates. A single writer is assumed for now.

## Remaining work (next: Gate 3)

1. **Gate 3.** The broker and connector are verified (see the Kafka smoke above). Build:
   - A deterministic accelerated replay producer with speed, pause/resume, and source vs replay time.
   - A Spark Structured Streaming consumer into the Bronze envelope.
   - An idempotent Silver MERGE keyed on line identity, with a revision for corrections.
   - A watermark policy, with late events routed to a durable backfill table.
   - Incident injector: duplicates within and across batches, malformed rows, incompatible and additive schema changes.
   - Fault injection: kill around the commit/checkpoint boundary, then restart, plus an interrupted multi-stage publish that leaves the last good snapshot current.
   - A correction plus an affected-date backfill, re-run safely. This also supplies the carried-forward incremental-update SQL example.
   - Batch vs replay reconciliation of identities and exact GBP.
   - An unsafe comparison calculation, isolated in the UI.
   - Persisted metrics for the Trust view.
2. **Gate 4.** Seasonal naive, trailing-average, and LightGBM/sklearn models over at least 4 rolling origins plus an untouched test period, with past-only interval calibration and a leakage test.
3. **Gate 5.** Benchmarks (synthetic scaling, query latency, replay throughput, recovery time), README, case study, demo script, interview guide, resume bullets, and clean-checkout verification. Rebuild the image so the `analysis` extras are included.
4. **Gate 6.** Inventory simulator, Databricks bundle, Power BI exports and DAX (native report unfinished unless authoring is available).
