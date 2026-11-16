# MERIDIAN

**Retail revenue and demand intelligence, with a data-reliability demonstration you can break and repair.**

A portfolio case study. The client, a UK online retailer whose invoice exports, dashboard and demand plans disagree, is fictional. The data is real: [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii), 1,067,371 invoice lines from December 2009 to December 2011 (Daqing Chen, [DOI 10.24432/C5CG6D](https://doi.org/10.24432/C5CG6D), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).

Everything runs locally and free: no cloud account, trial, API credit or GPU.

| | |
|---|---|
| Business answers | [Client case study](docs/case-study.md) · [EDA findings](docs/findings.md) · [Forecasting](docs/forecasting.md) |
| How it works | [Architecture](docs/architecture.md) · [Data contracts](docs/data-contracts.md) · [Metric dictionary](docs/metrics.md) · [Gold data model](docs/data-model.md) |
| Demo and interview | [Two-minute demo script](docs/demo-script.md) · [Interview guide](docs/interview-guide.md) · [Resume bullets](docs/resume-bullets.md) |
| Microsoft / Databricks | [Azure migration design](docs/azure-migration.md) · [Databricks bundle](databricks/README.md) · [Power BI setup](powerbi/SETUP.md) |
| Status and evidence | [HANDOFF.md](HANDOFF.md) · [evidence/gates.json](evidence/gates.json) · [evidence/benchmarks.json](evidence/benchmarks.json) |

## What it does

- **Real Spark + Delta lakehouse.** Bronze → Silver → Gold with version-controlled SQL, decimal money, a stable source-row identity, named exclusions and quarantine. Every one of the 1,067,371 rows is accounted for, and gross £20,112,074.02 + adjustments −£732,828.20 = **net recorded £19,379,245.82**.
- **Real Kafka replay and recovery.** Historical rows are re-emitted through local Kafka on an accelerated clock. A seeded incident adds repeated deliveries, malformed and schema-breaking events, a conflicting re-export, late rows and a correction. Spark Structured Streaming contains it: idempotent Delta writes, quarantine, a durable late-arrival backfill and a blocked publication. A repair then publishes a snapshot that reconciles exactly with batch.
- **A deliberately unsafe calculation,** shown beside the trusted number and never served, to explain why the client's dashboards disagree.
- **Honest forecasting.** 7- and 28-day observed-sales forecasts, evaluated on rolling origins with an untouched test month and a leakage test. Clearly labelled inventory scenarios are SIMULATED.
- **A fair DuckDB-only baseline** on the same SQL, and an explanation of when you should just use it.

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
python -m pytest -q                                                   # business, API, forecast tests
python tests/check_full_snapshot.py                                   # independent full-data check
docker compose run --rm --no-deps tools python -m meridian.cli replay-drill   # fault-injection drill
```

## Verified results

BENCHMARK_TABLE

All measurements were taken on one machine: an i7-14700HX laptop (28 logical CPUs, 16 GB RAM) running Docker Desktop on WSL2, with Spark `local[2]`. They are not cluster or cloud benchmarks.

## Honest limitations

- **Not real revenue.** Recorded invoice value is not audited revenue, cash or profit. The source has no costs, inventory, promotions or settlement data.
- **Possible double counting.** The two workbook sheets overlap. There are 22,202 identical-looking payload groups, kept and flagged until the client supplies line keys.
- **Not live.** Replay is not a live integration. Synthetic incident and scale-test events are never observed sales.
- **Forecast caveats.** Forecasts are of observed sales, not unconstrained demand. Their 80% intervals under-covered in validation.
- **Simulated savings only.** Inventory "savings" are simulated under stated assumptions; the winning policy changes with the cost ratio.
- **Nothing ran in Azure, Databricks or Power BI.** The notebooks, bundle and DAX are prepared; the native report is unfinished.
- **Local-only controls.** The demo controls and Kafka are loopback-only and must not be exposed.
