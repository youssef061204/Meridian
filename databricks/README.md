# MERIDIAN on Databricks: portable batch contract job

| Capability | Status | Evidence |
|---|---|---|
| Contract dataset produces the hand-calculated totals under the repository's `sql/silver.sql` and `sql/daily_sales.sql` | **verified-local** (DuckDB on the host, not Databricks) | `evidence/databricks-contract-local.json` (36/36 checks) |
| `databricks/sql/*.sql` byte-identical to `sql/*.sql` | **verified-local** (checked on every run of the local script) | same file |
| Notebooks and bundle in Databricks Free Edition or Azure Databricks | **prepared, unexecuted** | none. No authenticated workspace or Databricks CLI profile exists on the build machine (`~/.databrickscfg` absent, no `databricks` CLI on PATH, checked 2026-11-16) |
| Kafka replay path on Databricks | **not prepared, by design** | Free Edition cannot reach a local broker (see limitations) |

No result in this folder is Databricks evidence until a real run produces
`/Volumes/<catalog>/<schema>/contract/evidence/<run_id>.json` and that file is copied into `evidence/`.

## What it runs

The batch path, using exactly the local business rules:

```text
contract/lines.csv  (41 deliveries, 37 source coordinates; synthetic)
  00_setup         CREATE SCHEMA / VOLUME, copy CSV from workspace files to the UC volume
  01_bronze_ingest all deliveries as text + line_id = sha256(source_sha256|source_sheet|source_row) -> bronze_deliveries
  02_silver        conflicting-repeat guard, one delivery per line_id -> source_lines temp view,
                   run sql/silver.sql unchanged -> silver_invoice_line
  03_gold_daily    accepted rows -> fact_invoice_line; run sql/daily_sales.sql unchanged -> daily_sales
  04_reconcile     compare with contract/expected.json, write contract_reconciliation + evidence JSON, fail on mismatch
```

Adaptations from the local Spark 3.5.7 path. Business logic is unchanged.

- **Locations.** Delta paths under `data/lake/` become Unity Catalog managed tables
  `<catalog>.<schema>.*`. Input files go in a UC volume, because serverless restricts DBFS.
- **`TIMESTAMP_NTZ`** is native in Databricks, so `silver.sql` runs verbatim. Tables are created with
  `TBLPROPERTIES ('delta.feature.timestampNtz' = 'supported')`, as the local writer does for NTZ.
  Running this on a Databricks runtime is unverified.
- **ANSI mode.** Serverless defaults `spark.sql.ansi.enabled` to `true`. Local Spark 3.5.7 defaults to
  `false`, so each notebook sets `false` to keep the `try_cast` and overflow behaviour identical.
  `spark.sql.session.timeZone` is set to `Europe/London`, as in `pipeline.py`. Both properties are on
  the serverless supported list. NTZ values are unaffected by the session timezone.
- **SQL source.** The notebooks read `${workspace.file_path}/sql/*.sql`, which are the bundled copies. If
  a model changes, recopy it (`cp sql/silver.sql sql/daily_sales.sql databricks/sql/`). The local
  check fails until the copies match again.
- Only session temp views are used. Serverless does not support global temp views.

## Run it (when you have a workspace; nothing here was run)

Prerequisites: a Databricks Free Edition account (sign-in by email OTP, Google
or Microsoft) or an existing Azure Databricks workspace that Unity Catalog and serverless jobs are
enabled on. Do not create a paid workspace or start a trial for this project.

### A. Declarative Automation Bundle (CLI)

```bash
# A current Databricks CLI release with bundle support
databricks auth login --host https://<your-workspace-host>    # creates a profile; do not commit it
cd databricks
databricks bundle validate -t free
databricks bundle deploy   -t free                             # syncs notebooks/, sql/, contract/ and creates the job
databricks bundle run      -t free meridian_contract_batch     # runs all five tasks on serverless compute
```

Override the location with `--var catalog=<cat> --var schema=<schema>`. On Free Edition the catalog is
`workspace`. The `free` target uses `mode: development`, so the job name gets a user prefix and
schedules stay paused.

### B. Workspace UI only (no CLI)

1. **Workspace > Create > Git folder**, or import the `databricks/` folder. A notebook with the
   `# Databricks notebook source` header imports as a notebook.
2. Open `notebooks/00_setup` and attach it to **Serverless**. Run each notebook in order (00 to 04).
   Leave the `bundle_root` widget empty, so that each notebook derives it from its own path.
3. Alternatively, create a job (**Jobs & Pipelines > Create > Job**) with five notebook tasks in that
   order, each depending on the previous one, on serverless compute. Add job parameters `catalog`,
   `schema` and `bundle_root=/Workspace/<path to the databricks folder>`.

### Recording evidence after a real run

Download `/Volumes/<catalog>/<schema>/contract/evidence/<run_id>.json` (Catalog Explorer > volume >
file > Download). Save it as `evidence/databricks-contract-run.json` together with the job run URL and
the workspace type (Free Edition or Azure). Only then change the status above to **verified-cloud**.
The KPI values must equal the local evidence: net £191.14, gross £222.35, adjustments −£31.21, 146
positive units.

## Contract dataset: hand calculation

Synthetic test data (`mode: synthetic`), never observed transactions. The source checksum is the
placeholder `c` × 64 and the sheet is `Contract`. Row numbers are source coordinates, so they double as
line identity inputs. `delivery_id` is delivery metadata only.

**Accepted positive merchandise (17 rows)**: quantity × price, rounded half-up per line.

| Row | Case | Calc | £ |
|---|---|---|---:|
| 2 | widget, customer 13001 | 6 × 2.55 | 15.30 |
| 3 | **identical-looking copy at a different coordinate**, kept | 6 × 2.55 | 15.30 |
| 4 | mug | 12 × 0.85 | 10.20 |
| 5 | **missing customer** | 1 × 12.75 | 12.75 |
| 6 | missing customer | 24 × 0.42 | 10.08 |
| 7 | France | 3 × 2.55 | 7.65 |
| 8 | France | 2 × 4.95 | 9.90 |
| 9 | Germany | 5 × 1.25 | 6.25 |
| 10 | **zero price**, 4 units, no money | 4 × 0.00 | 0.00 |
| 11 | **half-up rounding** | 7 × 0.665 = 4.655 | 4.66 |
| 12 | | 10 × 1.65 | 16.50 |
| 13 | Netherlands | 48 × 0.72 | 34.56 |
| 14 | Netherlands | 2 × 8.50 | 17.00 |
| 15 | missing customer, EIRE | 2 × 12.75 | 25.50 |
| 16 | **blank country**, becomes `Unknown` | 3 × 3.25 | 9.75 |
| 17 | **summer civil time** 2010-07-15 14:30, stored unshifted | 10 × 2.10 | 21.00 |
| 18 | stock code `" p111 "`, trimmed and upper-cased to `P111` | 1 × 5.95 | 5.95 |
| | **Gross positive** | | **222.35** |

Positive units: 6+6+12+1+24+3+2+5+4+7+10+48+2+2+3+10+1 = **146**.

**Accepted negative adjustments (5 rows)**

| Row | Case | Calc | £ |
|---|---|---|---:|
| 19 | C-invoice cancellation | −2 × 2.55 | −5.10 |
| 20 | C-invoice cancellation | −1 × 4.95 | −4.95 |
| 21 | **negative quantity without C prefix** | −3 × 1.25 | −3.75 |
| 22 | **half-up away from zero** | −7 × 0.665 = −4.655 | −4.66 |
| 23 | anonymous cancellation | −1 × 12.75 | −12.75 |
| | **Negative adjustments** | | **−31.21** |

Net = 222.35 − 31.21 = **£191.14**. Accepted 22 rows, of which 18 have a known customer (rows 5, 6, 15
and 23 have none).

**Excluded by the named non-merchandise rule (5 rows, £62.60 parseable)**: row 24 POST 18.00, row 25
POST −18.00 (on a C invoice, quantity −1), row 26 M 2.50, row 27 `bank charges` (upper-cased to `BANK
CHARGES`) 15.00, and row 28 DOT 45.10.

**Quarantined (10 rows, £20.22 parseable, 1 unparseable)**

| Row | Reason | Parsed £ |
|---|---|---:|
| 29 | fractional quantity 2.5 → `invalid_quantity` | 2.50 |
| 30 | quantity `abc` → `invalid_quantity` | unparseable |
| 31 | zero quantity → `invalid_quantity` | 0.00 |
| 32 | **missing invoice** → `missing_invoice` | 8.00 |
| 33 | missing stock code | 2.00 |
| 34 | negative price → `invalid_price` | −1.50 |
| 35 | positive quantity on a C invoice → `cancellation_sign_conflict` | 5.10 |
| 36 | impossible timestamp `2010-13-45` → `invalid_timestamp` | 1.00 |
| 37 | price `0.12345` → `price_precision_exceeded` (cast to 4 dp = 0.1235) | 0.12 |
| 38 | missing timestamp → `invalid_timestamp` | 3.00 |

Amount bridge: 191.14 + 62.60 + 20.22 = **£273.96** of parseable source amount, plus 1 unparseable row.
Row bridge: 22 + 5 + 10 = 37 source rows.

**Repeated deliveries (4)**: d038 repeats row 2, d039 row 5, d040 row 19, and d041 row 29 (a quarantined
row). The payloads are identical, so they collapse to one logical row each: 41 received deliveries
against 37 source rows. The deliberately unsafe sum over accepted *deliveries* is 191.14 + 15.30 +
12.75 − 5.10 = **£214.09**, an overstatement of £22.95. It is a comparison only, never a KPI.

Per-day and per-country expectations are in `contract/expected.json`.

## Databricks Free Edition limitations that shape this design

Sources accessed 2026-11-16:

- [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations) (page updated 2026-10-18):
  - Serverless compute only; custom compute configurations are not supported.
  - Outbound internet access is limited to trusted domains.
  - One SQL warehouse, at most 2X-Small.
  - At most 5 concurrent job tasks per account. This job runs its tasks sequentially, so one at a time.
  - One active Lakeflow pipeline per pipeline type.
  - Daily quota. Exceeding it shuts compute down for the rest of the day, or in extreme cases the rest
    of the month.
  - No R or Scala, no custom workspace storage locations, and no legacy features.
  - One workspace and one metastore. No account console or account APIs, no private networking, and
    no SSO or SCIM.
  - Non-commercial use only, with no SLA.
- [Serverless compute limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations) (updated 2026-11-05):
  - Only the `Trigger.AvailableNow()` and deprecated `Trigger.Once()` streaming triggers are supported.
    `ProcessingTime` and `Continuous` are not, and the default trigger is rejected.
  - Spark Connect APIs only, with no RDD API.
  - No Maven coordinates or compute-scoped libraries in notebooks.
  - No Spark UI.
  - Limited DBFS, so use UC volumes.
  - No global temp views.
- [Serverless Spark configuration](https://docs.databricks.com/aws/en/spark/conf): the settable
  properties include `spark.sql.ansi.enabled` (default `true`) and `spark.sql.session.timeZone`
  (default `Etc/UTC`).
- [Free Edition tables live in `workspace.default` by default](https://docs.databricks.com/aws/en/getting-started/ce-migration).
- [Declarative Automation Bundles, formerly Databricks Asset Bundles](https://docs.databricks.com/aws/en/dev-tools/bundles/)
  (updated 2026-11-05). No official page lists which bundle resource types Free Edition supports. That
  `bundle deploy` works on Free Edition is an untested assumption. Path B (UI) is the fallback.

Consequences:

- The local Kafka broker (`localhost` / Docker network) cannot be reached from a Databricks-hosted
  workspace, and outbound access is limited anyway.
- A cloud replay would need a broker reachable from Databricks, for example Azure Event Hubs, which is a
  paid resource and outside this project's free scope. The stream would also have to run with
  `availableNow` micro-batches, not a continuous trigger.
- MERIDIAN's local `foreachBatch` consumer depends on the `spark-sql-kafka` Maven package, which
  serverless notebooks cannot attach. Serverless supports Kafka as a built-in source.
- The replay path was therefore not ported. See `docs/azure-migration.md` for the planned design.
