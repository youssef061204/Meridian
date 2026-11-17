# MERIDIAN

**Retail revenue and demand intelligence, with a data-reliability demonstration you can break and repair.**

A portfolio case study. The client, a UK online retailer whose invoice exports, dashboard and demand plans disagree, is fictional. The data is real: [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii), 1,067,371 invoice lines from December 2009 to December 2011 (Daqing Chen, [DOI 10.24432/C5CG6D](https://doi.org/10.24432/C5CG6D), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).

Everything runs locally and free: no cloud account, trial, API credit or GPU.

**Live showcase: [meridian-chi-ivory.vercel.app](https://meridian-chi-ivory.vercel.app).** This is the real UI, serving responses recorded verbatim from the local stack (`scripts/record_showcase.py`), including a real incident-and-repair run. On the public site:
- **Filters** run live in your browser with DuckDB-WASM over the validated accepted lines.
- **The SQL Lab** runs live in the browser the same way.
- **"Ask the data"** turns plain English into SQL through Claude when the deployment has an `ANTHROPIC_API_KEY`, and falls back to curated queries when it doesn't.
- **Spark, Kafka and the worker** still run only locally.

Build it with `npm --prefix frontend run build:showcase`.

| | |
|---|---|
| Business answers | [Client case study](docs/case-study.md) · [EDA findings](docs/findings.md) · [Customer, basket & statistical insights](docs/insights.md) · [Forecasting](docs/forecasting.md) |
| How it works | [Architecture](docs/architecture.md) · [Data contracts](docs/data-contracts.md) · [Metric dictionary](docs/metrics.md) · [Gold data model](docs/data-model.md) |
| Demo and interview | [Two-minute demo script](docs/demo-script.md) · [Interview guide](docs/interview-guide.md) · [Resume bullets](docs/resume-bullets.md) |
| Microsoft / Databricks | [Azure migration design](docs/azure-migration.md) · [Low-cost Azure Bicep](infra/azure/main.bicep) · [Synapse serverless views](synapse/serverless_views.sql) · [Databricks bundle](databricks/README.md) · [Power BI setup](powerbi/SETUP.md) |
| Status and evidence | [HANDOFF.md](HANDOFF.md) · [evidence/gates.json](evidence/gates.json) · [evidence/benchmarks.json](evidence/benchmarks.json) |

## What it does

- **Real Spark + Delta lakehouse.** Bronze → Silver → Gold with version-controlled SQL, decimal money, a stable source-row identity, named exclusions and quarantine. Every one of the 1,067,371 rows is accounted for, and gross £20,112,074.02 + adjustments −£732,828.20 = **net recorded £19,379,245.82**.
- **Real Kafka replay and recovery.** Historical rows are re-emitted through local Kafka on an accelerated clock. A seeded incident adds repeated deliveries, malformed and schema-breaking events, a conflicting re-export, late rows and a correction. Spark Structured Streaming contains it: idempotent Delta writes, quarantine, a durable late-arrival backfill and a blocked publication. A repair then publishes a snapshot that reconciles exactly with batch.
- **A deliberately unsafe calculation,** shown beside the trusted number and never served, to explain why the client's dashboards disagree.
- **Honest forecasting.** 7- and 28-day observed-sales forecasts, evaluated on rolling origins with an untouched test month and a leakage test. Clearly labelled inventory scenarios are SIMULATED.
- **A fair DuckDB-only baseline** on the same SQL, and an explanation of when you should just use it.
- **Customer analytics and data mining.**
  - RFM segmentation with k-means; k is chosen by silhouette.
  - A time-split repeat-purchase model: test AUC 0.793 against 0.762 for a recency rule, with a leakage test.
  - Market-basket association rules with an interactive product network.
- **Statistics.** Robust anomaly detection that explains each flagged day by its driving invoice lines, plus Kruskal–Wallis, Mann–Whitney with a bootstrap CI, and Gini/Pareto concentration.
- **SQL Lab in the browser.** DuckDB-WASM over 1.06M accepted lines, natural language → SQL through Claude, a read-only SQL guard, and CSV export.
- **A deployable low-cost Azure footprint (prepared).**
  - ADLS Gen2 and Synapse *serverless* SQL only, so idle cost is zero.
  - An optional Event Hubs Kafka endpoint, off by default.
  - A budget alert, plus views that re-check the £19,379,245.82 reconciliation.

## Run it

Prerequisites:
- Docker Desktop with WSL2 (Windows) or Docker Engine + Compose (Linux).
- Node 20+.
- About 4 GB of free Docker memory for the replay demo.

The first run downloads the UCI archive (~45 MB) and the Maven packages.

```powershell
# 1. Build the locked image and convert the verified source workbook
docker compose build tools
docker compose run --rm --no-deps tools python -m meridian.cli convert

# 2. Historical batch (demo profile: 4,000 real rows; full profile: all 1,067,371)
docker compose run --rm --no-deps tools python -m meridian.cli batch --profile demo
docker compose run --rm --no-deps tools python -m meridian.cli batch --profile full

# 3. UI + API on http://localhost:8010 (bound to 127.0.0.1)
npm --prefix frontend ci
npm --prefix frontend run build
docker compose up -d api

# 4. Kafka replay demo: broker, worker, then a baseline replay generation
docker compose --profile replay up -d kafka
docker compose run --rm --no-deps tools python -m meridian.cli replay-init --profile demo
docker compose --profile replay up -d worker
```

Open **http://localhost:8010** → **Data trust & recovery** → **Inject controlled incident** → **Repair and reprocess**. See the [demo script](docs/demo-script.md).

Stop everything with `docker compose --profile replay down`. Data in `data/` persists. On Windows PowerShell use `npm.cmd` if script execution is blocked. Set `MERIDIAN_PORT` or `MERIDIAN_KAFKA_PORT` to change the host ports.

**Analysis, forecasting and checks** use host Python 3.11 (`uv venv --python 3.11 .venv`, then `uv pip install -e ".[spark,forecast,analysis,dev]"`):

```powershell
python -m meridian.analysis --snapshot <full snapshot id> --notebook   # EDA notebook + findings
python -m meridian.cli forecast                                       # rolling-origin evaluation
python -m meridian.cli inventory                                      # SIMULATED scenarios
python -m meridian.cli insights                                       # segments, basket rules, return model, anomalies, tests, browser marts
python -m pytest -q                                                   # business, API, forecast tests
python tests/check_full_snapshot.py                                   # independent full-data check
docker compose run --rm --no-deps tools python -m meridian.cli replay-drill   # fault-injection drill
```

## Verified results

| Capability | Result | Evidence |
|---|---|---|
| Source verification | Both sheets, 525,461 + 541,910 = 1,067,371 rows, matching UCI; SHA-256 pinned; bounded conversion in 99.5 s | [source-verification.json](evidence/source-verification.json) |
| Full Spark + Delta pipeline | 1,067,371 rows → 1,061,664 accepted / 5,701 excluded / 6 quarantined; 14 tables; Spark stage 125.0 s; peak container memory 1.86 GiB | [full-batch.json](evidence/full-batch.json) |
| Independent full-data check | Source coordinates 1:1, 0 civil-time mismatches (563,573 summer rows), joins preserve exact GBP, cohorts recomputed | [full-snapshot-check.json](evidence/full-snapshot-check.json) |
| DuckDB-only baseline (same SQL) | Identical accepted lines and GBP; 17.3 s transform/export (fact + daily only, no Delta history, so not like-for-like) | [full-baseline.json](evidence/full-baseline.json) |
| **Full-history Kafka replay** | All 1,067,371 events through a 1-partition topic; producer 7,842 events/s; consumer 3,342 deliveries/s over 5 micro-batches; **reconciled exactly with batch** (£19,379,245.82). First publication attempt hit DuckDB's memory cap and was correctly refused; fixed and republished. | [benchmarks.json](evidence/benchmarks.json) → `replay_full_history` |
| Recovery drill | 21/21 checks: process kills after the Bronze commit and before the checkpoint commit, no duplicate writes, quarantine by reason, durable late path, blocked publication, idempotent backfill re-run, exact batch equivalence | [replay-drill.json](evidence/replay-drill.json) |
| Signature demo in a real browser | 11/11 steps through UI → API → worker → Kafka/Spark/Delta; unsafe sum £3,066.89 too high; recovered in 151.7 s, injection to validated publication including queueing; whole sequence 164 s | [browser-demo.json](evidence/browser-demo.json), [screenshots](evidence/screenshots) |
| Synthetic scale run (10,673,710 rows) | Spark 355.6 s at the 2.3 GiB container ceiling; DuckDB 123.3 s (1.4 GB cap + spill); exact equivalence on 10,616,640 lines. The first two DuckDB attempts ran out of memory and are retained. | [benchmarks.json](evidence/benchmarks.json) → `synthetic_scale` |
| Query latency (full snapshot) | API warm p50: overview 315 ms, lineage 301 ms, trust 79 ms. Pre-aggregated daily mart 23.4× faster (p50) than a fact scan, with identical results. | [benchmark-queries.json](evidence/benchmark-queries.json), [api-serving-optimization.json](evidence/api-serving-optimization.json) |
| Forecasting | Promoted on validation only; untouched-test WAPE vs seasonal naive −3.1% (7 d) / −14.8% (28 d) aggregate; 80% intervals under-cover in validation | [forecast-run.json](evidence/forecast-run.json) |
| Customer & statistical insights | k = 4 segments (Champions: 20% of buyers, 73% of value); repeat-purchase AUC 0.793 on an untouched window; 940 basket rules (top lift 48×); 17 anomalous days with drivers; full run 17 s. Computed from a two-mirror rebuild whose accepted lines and GBP totals match the pinned run exactly (see [docs/insights.md](docs/insights.md)) | [analysis/insights/insights.json](analysis/insights/insights.json), [mirror-reconstruction.json](evidence/mirror-reconstruction.json) |
| Browser marts | 5.9 MB gzip CSV; the export asserts the browser SQL reproduces 1,061,664 lines and £19,379,245.82 exactly. In-browser Germany filter £379,900.91 = recorded country total | `browser_marts` in insights.json |
| Tests | 65 pytest tests (business oracle, API, forecast leakage, inventory, insights oracles and leakage, SQL guard and ask contract) plus a Spark fixture oracle; CI runs pytest and the web build | `python -m pytest -q`, [fixture-batch.json](evidence/fixture-batch.json) |
| Clean checkout | Fresh clone followed the README: locked build, download and convert, fixture oracle, demo batch, UI build, API serving £69,754.43 (full batch and Kafka steps verified in the working copy) | [clean-checkout.json](evidence/clean-checkout.json) |

All measurements were taken on one machine: an i7-14700HX laptop (28 logical CPUs, 16 GB RAM) running Docker Desktop on WSL2, with Spark `local[2]`. They are not cluster or cloud benchmarks.

## Honest limitations

- **Not real revenue.** Recorded invoice value is not audited revenue, cash or profit. The source has no costs, inventory, promotions or settlement data.
- **Possible double counting.** The two workbook sheets overlap. There are 22,202 identical-looking payload groups, kept and flagged until the client supplies line keys.
- **Not live.** Replay is not a live integration. Synthetic incident and scale-test events are never observed sales.
- **Forecast caveats.** Forecasts are of observed sales, not unconstrained demand. Their 80% intervals under-covered in validation.
- **Simulated savings only.** Inventory "savings" are simulated under stated assumptions; the winning policy changes with the cost ratio.
- **Nothing ran in Azure, Databricks or Power BI.**
  - The notebooks, bundle, DAX, Bicep template and Synapse views are prepared.
  - The Bicep template has not been compiled here; run `az bicep build` first.
  - The native Power BI report is unfinished.
- **Insights came from a mirror rebuild.** The UCI host was unreachable when they were computed. The rebuild matches every accepted line and GBP total but has one fewer excluded row. Line identities differ from the pinned snapshot, so insights are not joined to snapshot lineage. Re-run `meridian.cli insights` against the pinned snapshot to replace them.
- **The AI endpoint was not exercised against a live key here.** The request shape was checked, and it reached the API and failed at authentication with a dummy key. On Vercel, set `ANTHROPIC_API_KEY`, and optionally `MERIDIAN_ASK_MODEL` (default `claude-opus-5-5`). The function only returns SQL; queries execute in the viewer's browser.
- **Local-only controls.** The demo controls and Kafka are loopback-only and must not be exposed.
