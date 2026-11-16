# MERIDIAN

A free, local retail revenue and demand intelligence portfolio case study. Original UCI invoice rows flow through **real PySpark and Delta Lake**, then a validated versioned Parquet snapshot serves DuckDB, FastAPI and React. Recorded merchandise invoice value is not audited revenue, cash collected, or profit.

## Run locally

Prerequisites: Docker Desktop with WSL2 on Windows (or Docker Engine/Compose on Linux), Node 24+, roughly 3 GiB available Docker memory for the demo. Initial dependency/data download needs internet. No accounts, cloud trials or paid services.

```powershell
docker compose build tools
docker compose run --rm --no-deps tools python -m meridian.cli convert
docker compose run --rm --no-deps tools python -m meridian.cli smoke
docker compose run --rm --no-deps tools python -m meridian.cli batch --profile fixture
docker compose run --rm --no-deps tools python tests/check_spark_fixture.py
docker compose run --rm --no-deps tools python -m meridian.cli batch --profile demo
npm --prefix frontend ci --no-fund --no-audit
npm --prefix frontend run build
docker compose up -d api
```

Open **http://localhost:8010**. Use `npm.cmd` instead of `npm` if PowerShell blocks the npm script shim. Stop this project's services with `docker compose down`. Raw data and snapshots persist in `data/`; stop does not delete them. Gate 1 does not require the Kafka service.

For local Python tooling: `uv venv --python 3.11 .venv`, then `uv pip install -e '.[dev,forecast]'`. Spark runs in Linux Docker with Java 17. `uv.lock` and the hash-checked `requirements.lock` lock transitive dependencies; `frontend/package-lock.json` locks the UI.

## Demonstrate the current slice

1. Business overview: inspect the GBP bridge and the explicitly historical source dates. The demo contains 2,000 original rows per worksheet, not a representative full-period sample.
2. Select net recorded merchandise. Metric explorer shows the contributing source coordinates, classifications, executed SQL and pinned Delta versions.
3. Apply a country/date filter and reset it. Unknown customer identifiers remain in valid sales totals.
4. Data trust: inspect original source rows versus received deliveries, named exclusions, quarantine, and real run manifests. Forecast/recovery controls remain visibly pending until their gates execute.

## Verified and pending

The downloaded workbook has **1,067,371 rows**: 525,461 in `Year 2009-2010` and 541,910 in `Year 2010-2011`. Both sheets were streamed and converted; workbook SHA-256 is `bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980`. Their date ranges overlap in December 2010; original coordinates are preserved, and potential repeated exports require analysis before full-period interpretation.

The executed Spark/Delta smoke passed with Spark 3.5.7, Delta 3.3.3, Scala 2.12.18, Java 17.0.21, Python 3.11.11. The first genuine 4,000-row demo batch reconciled 3,986 accepted lines and 14 named exclusions: gross **£71,183.35**, adjustments **−£1,428.92**, net **£69,754.43**. Evidence is in `evidence/` and persisted `runtime/runs/` manifests. The Kafka connector loaded; a loaded class is **not a verified broker integration**.

Gate status, exact executed commands, failures, test results and remaining work are maintained in [HANDOFF.md](HANDOFF.md). Full-history modeling, Kafka recovery, forecasting, benchmarks and cloud/native BI work require their own executed evidence. This README does not imply those integrations have passed.

## Source and design

[UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii), Daqing Chen (2012), [DOI 10.24432/C5CG6D](https://doi.org/10.24432/C5CG6D), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). This fictional consulting engagement is a portfolio case study.

See [metric definitions](docs/metrics.md), [architecture and tradeoffs](docs/architecture.md), [official compatibility sources](docs/dependency-compatibility.md), [source lock](SOURCE.lock.json) and the full [project specification](MERIDIAN_Codex_Project_Prompt.md). Raw UK civil timestamps are preserved without invented UTC offsets. There are no observed inventory, costs, marketing spend or payment settlement fields.
