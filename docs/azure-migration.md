# MERIDIAN Azure migration design

**This is a design, not a deployment.** No Azure subscription, Databricks workspace, Event Hubs
namespace, Power BI tenant or Fabric capacity has been used, provisioned or signed into for this
project. Nothing in this document is evidence that MERIDIAN runs on Azure. The cloud capabilities below
are **planned**. The only executed work related to the cloud is local:

- a DuckDB check of the Databricks contract dataset, and
- a local export of Power BI-ready Gold tables.

Product names were checked against current official documentation on 2026-11-16 (see *Sources*). Prices
are deliberately absent. The cost section names the billing meters and links the official pricing
pages.

## 1. Capability status

| Capability | Status | Evidence / location |
|---|---|---|
| Spark 3.5.7 / Delta 3.3.3 batch Bronze → Silver → Gold, full history (1,067,371 rows) | **verified-local** | `evidence/full-batch.json`, `evidence/full-snapshot-check.json` |
| Validated versioned snapshot + atomic pointer, DuckDB serving, FastAPI/React UI | **verified-local** | `evidence/gates.json` (Gates 1–2), `evidence/browser-gate2.json` |
| Kafka → Spark Structured Streaming → Delta compatibility smoke | **verified-local** | `evidence/kafka-smoke.json` |
| Kafka replay with idempotent Bronze appends, Silver MERGE with revisions, late-arrival backfill, versioned publication | **verified-local** (single-node Kafka, Spark `local[2]`): recovery drill 21/21 checks, browser-exercised incident/repair/reset | `evidence/replay-drill.json`, `evidence/browser-demo.json`; `src/meridian/replay.py`, `stream.py` | `src/meridian/replay.py`, `stream.py` |
| Databricks contract dataset reproduces hand-calculated totals under `sql/silver.sql` / `sql/daily_sales.sql` | **verified-local (DuckDB, not Databricks)** | `evidence/databricks-contract-local.json` |
| Databricks notebooks + Declarative Automation Bundle for the batch contract path | **prepared, unexecuted** (no authenticated workspace) | `databricks/` |
| Power BI Gold exports, relationships, DAX, SQL-computed reconciliation targets | exports and targets **verified-local**; DAX **prepared, never run** | `evidence/powerbi-export.json`, `powerbi/` |
| Native Power BI report | **unfinished** (Power BI Desktop not installed) | `powerbi/SETUP.md` |
| ADLS Gen2, Azure Databricks, Unity Catalog, Event Hubs, Key Vault, private networking, Power BI service / Fabric | **planned** (this document) | none |
| Low-cost deployable footprint: ADLS Gen2 + Synapse serverless SQL (no dedicated or Spark pools), optional Event Hubs Kafka endpoint (off by default), resource-group budget alert, Entra-ID-only storage access | **prepared, not compiled or deployed** | `infra/azure/main.bicep`, `scripts/azure_publish.sh` |
| Synapse serverless views over Gold Parquet, with the £19,379,245.82 reconciliation query | **prepared, never executed** | `synapse/serverless_views.sql` |
| Any verified-cloud capability | **none** | none |

## 2. Component mapping

| Implemented locally | Azure target (planned) | Notes |
|---|---|---|
| `data/raw/` pinned UCI workbook + `SOURCE.lock.json` | ADLS Gen2 container `landing` (hierarchical namespace), immutable blob policy on the source folder | Keep the SHA-256 pin. Ingestion refuses a checksum mismatch, exactly as locally. |
| `data/converted/*.parquet` (bounded streaming conversion) | Lakeflow Jobs Python task writing Parquet to a UC volume or external location | Converter is pure Python (openpyxl streaming), so it ports as-is. |
| `data/lake/historical/full/*` Delta tables | Unity Catalog catalog `meridian_prod`, schemas `bronze`, `silver`, `gold`. Managed tables in a catalog-level managed storage location on ADLS Gen2 | Paths become three-level names. Historical, replay and synthetic stay in **separate schemas or catalogs** (`historical`, `replay`, `synthetic`), preserving the physical separation required locally. |
| Spark `local[2]` in a Java 17 container | Azure Databricks Lakeflow Jobs on serverless jobs compute, or job clusters on a pinned Databricks Runtime LTS | Pin a runtime whose Spark/Delta lines match the verified local versions, then re-run the equivalence checks. Do not assume them. |
| `sql/*.sql` Spark SQL models | Same files, deployed by a Declarative Automation Bundle (`databricks/` shows the pattern) | `TIMESTAMP_NTZ` is native. Serverless defaults `spark.sql.ansi.enabled=true` while local Spark 3.5.7 runs with it off, so set it explicitly, or re-validate every `try_cast`/overflow path. |
| Kafka 4.1.2 single node (`apache/kafka` image) | Azure Event Hubs Kafka endpoint, Standard tier or higher | See §4. Semantics differ in ways that matter to the replay design. |
| `foreachBatch` consumer with Delta `txnAppId`/`txnVersion` idempotent appends, checkpoints on local disk | Same code on Databricks, `availableNow` trigger. Checkpoints in a UC volume (or an external location dedicated to checkpoints) | `availableNow` is already what the local consumer uses. It is also the only non-deprecated trigger on serverless. |
| `runtime/runs/*.json` run manifests, `writer_lock()` | Lakeflow Jobs run history + a `ops.run_manifest` Delta table, with `max_concurrent_runs: 1` replacing the local file lock | The local lock assumes one publisher. A job concurrency limit is the cloud equivalent. Cross-job publishers still need a compare-and-swap on the pointer (§6). |
| `data/serving/snapshots/<id>/` + `current.json` pointer | `gold_serving.snapshot_manifest` table (one row per validated publication, with the Delta version of every table) + `gold_serving.current_snapshot` single-row pointer updated with `MERGE ... WHEN MATCHED AND target.snapshot_id = :expected` | A pointer in a Delta table gives an auditable, conditional switch. Readers resolve it once, then use `VERSION AS OF`. |
| DuckDB over exported snapshot files | Databricks SQL serverless warehouse over UC Gold tables, **or** keep DuckDB reading exported snapshot Parquet from ADLS for the API | At this size (about 1.06M fact rows) DuckDB remains a credible, cheaper serving path. See §8. |
| FastAPI + React | Azure Container Apps or App Service, Entra ID authentication in front, private ingress | The incident injector stays local-only. It must never be exposed in a cloud environment. |
| `powerbi/` exports + DAX | Power BI semantic model on Databricks SQL (Azure Databricks connector) published to a Power BI or Fabric workspace | §8. |

## 3. Storage and Unity Catalog

- **ADLS Gen2** storage account with hierarchical namespace, in the same region as the workspace.
  - Containers: `landing` (raw source), `uc-managed` (the catalog managed location), and `checkpoints`.
  - Disable shared-key access, so that identities authenticate with Entra ID only.
  - Soft delete and versioning on `landing`.
- **Unity Catalog access**:
  - an *Access Connector for Azure Databricks* with a system-assigned managed identity,
  - the *Storage Blob Data Contributor* role on the containers, and
  - a UC *storage credential* plus *external locations* for `landing` and `checkpoints`.
  No storage account keys exist in notebooks.
- **Volumes** hold non-tabular files: the workbook, the converted Parquet and the contract CSV. On
  serverless, volumes replace DBFS.
- **Retention.** The publication manifest pins Delta versions, so `VACUUM` retention
  (`delta.deletedFileRetentionDuration`) must be at least the declared reproducibility horizon. Without
  that, a published snapshot can become unreadable. Locally this is a documented rule (see
  `docs/architecture.md`). In the cloud it becomes a table property set by the bundle.
- **Delta features.** `timestampNtz` and deletion vectors raise the reader protocol. Every consumer
  must be Delta-aware, including Power BI through the SQL warehouse and any DuckDB `delta` reader. No
  consumer may glob Parquet files under a Delta table, the same rule as locally.

## 4. Streaming: Event Hubs Kafka endpoint instead of local Kafka

Event Hubs exposes a Kafka protocol endpoint at `<namespace>.servicebus.windows.net:9093` with
`SASL_SSL`. The concept mapping is: namespace = cluster, event hub = topic, and partition, consumer
group and offset unchanged. The Kafka endpoint is available in the **Standard, Premium and Dedicated**
tiers, not Basic.

Changes the local replay code needs:

| Local behaviour | Event Hubs behaviour | Required change |
|---|---|---|
| Plaintext `kafka:9092`, no auth | TLS required; SASL `OAUTHBEARER` (Entra ID) recommended, or `PLAIN` with a SAS connection string | Producer and Spark source get SASL options. Use a service principal or managed identity with *Azure Event Hubs Data Sender* / *Data Receiver* roles. |
| Producer compression (`replay.py` now sends uncompressed; gzip was removed after it throttled local replay) | Kafka compression is supported **only in Premium and Dedicated** (gzip) | Uncompressed works on Standard. Re-enable compression only on Premium/Dedicated, and measure it. |
| Producer `enable_idempotence=True`, `acks="all"` | Idempotent producers are supported. Kafka **transactions** are public preview in Premium/Dedicated only | MERIDIAN does not rely on Kafka transactions. End-to-end correctness still comes from line identity + idempotent Delta writes + MERGE. Event Hubs documents **at-least-once** delivery, which is the assumption the local design already makes. |
| Topics created with `KafkaAdminClient` (`_admin_create`) | Event hubs (topics) are Azure resources with tier-specific partition, retention and size quotas | Create event hubs (`meridian-replay-<gen>`, `meridian-synthetic-<gen>`) in IaC (Bicep/Terraform) before the producer runs. Do not depend on admin-API topic creation. Check the current Event Hubs quotas before sizing partitions and retention. |
| Broker retention set by the local container | Retention is a per-tier quota | Bronze Delta remains the durable record. Optionally enable **Event Hubs Capture** to ADLS as an independent raw archive. |
| Replay of historical rows on an accelerated clock | Unchanged | It is still replay of historical rows, never a live retailer integration. The replay/synthetic labels stay on every event, table and view. |

The consumer reads with Spark's built-in Kafka source (`kafka.bootstrap.servers`, `subscribe`,
`kafka.security.protocol=SASL_SSL`, `kafka.sasl.mechanism`, `kafka.sasl.jaas.config` from a secret),
and uses `trigger(availableNow=True)` with `foreachBatch`. The idempotence argument carries over
unchanged:

- Each micro-batch append to Bronze uses `txnAppId=<generation>` and `txnVersion=<batch_id>`, so Delta
  skips a batch that was already committed when it is retried after a failure.
- The Silver MERGE on `line_id` with an explicit `revision` is idempotent by key.
- Neither Kafka nor Event Hubs provides exactly-once *business* results. The design claims only what
  the idempotent writes and the reconciliation prove.

**Databricks Free Edition cannot run this path against MERIDIAN's local broker.** It is serverless-only,
limits outbound internet access to trusted domains, and has no private networking. A cloud replay
therefore needs a reachable managed broker, and Event Hubs Standard or higher is a paid resource. That
is why only the batch contract path is prepared in `databricks/`.

**Auto Loader** (`cloudFiles`) is the file-based alternative. If the client delivers daily invoice
exports as files rather than events, Auto Loader with `availableNow` on the landing container replaces
Kafka entirely. That is cheaper and simpler. At this data volume it is likely the better business choice
unless sub-daily latency is required, which mirrors the local DuckDB-baseline argument.

## 5. Orchestration: Lakeflow Jobs vs Azure Data Factory

**Choice: Lakeflow Jobs** (formerly Databricks Jobs), deployed with Declarative Automation Bundles
(formerly Databricks Asset Bundles).

- Every MERIDIAN stage is Spark or Python on Databricks.
- Jobs give task dependencies, retries, repair-run, `max_concurrent_runs`, run-as service principals,
  and a single deployment artifact versioned with the code.
- The local orchestration (explicit stages, retries, run manifests, resumable stages) maps onto tasks
  one to one: convert → bronze → silver → gold → validate → publish.

**Azure Data Factory, or Data Factory in Fabric, is the better choice when**:

- the client already standardizes on it,
- many non-Databricks sources or sinks need managed connectors (on-premises SQL, SFTP, SaaS), or
- an integration-runtime pattern for private on-premises sources is needed.

In that case ADF triggers the Databricks job, and keeps no business logic of its own.

**Lakeflow Spark Declarative Pipelines** (formerly DLT) was considered for Silver/Gold. It was not
chosen for the first migration because MERIDIAN's publication protocol (validate every table → write
the manifest → switch the pointer) and its fault-injection evidence are built around explicit stages.
Rewriting them declaratively would change semantics that are already verified. Re-evaluate once the
cloud path is stable. Note that Free Edition allows one active pipeline per pipeline type.

## 6. Publication, incremental processing and backfill

- **Batch rebuild** (historical): the same deterministic overwrite as locally, then validation, then
  manifest, then pointer switch.
- **Incremental** (replay/corrections): Bronze append → Silver `MERGE` by `line_id` with an explicit
  revision. Late arrivals beyond the allowed lateness go to the durable backfill table, as in the local
  design. They are never dropped silently.
- **Affected-date backfill.** Recompute the daily, product and country marts for the affected dates.
  Cohort retention and customer summary must be **fully** republished: a late purchase can change a
  customer's first-observed cohort (`docs/data-model.md`).
- **Pointer switch.** A conditional `MERGE` on `gold_serving.current_snapshot` that succeeds only if
  the pointer still holds the snapshot the publisher started from (compare-and-swap). A failed
  validation leaves the previous pointer, and the UI shows its age and the failed run, as locally.
  Independent Delta commits are still **not** a cross-table transaction. The manifest is what defines
  consistency.

**Power BI incremental refresh** (if the model is hosted):

- Use `RangeStart`/`RangeEnd` Date/Time parameters on `fact_invoice_line[event_time]` against the
  Databricks SQL source. The filter must fold, and SQL warehouse sources fold where flat files do not.
- Supported for Pro, PPU, Premium and Embedded models. The real-time DirectQuery partition is
  Premium/PPU only.
- **Risk.** A late adjustment dated inside an already-archived historical partition is *not* picked up
  by the rolling refresh window. Either:
  - make the refresh window cover the supported lateness,
  - use *Detect data changes* on a `published_at` or revision timestamp column (not the partition
    column), or
  - refresh the affected partitions through the XMLA endpoint (Premium).
  The publication manifest's affected-date list drives that choice.

## 7. Identity, secrets and access control

**Identity (Microsoft Entra ID)**

- Humans sign in with Entra ID. Users and groups are provisioned to the Databricks account (SCIM or
  automatic identity management) and grouped as `meridian-engineers`, `meridian-analysts`,
  `meridian-operators` and `meridian-admins`.
- Production jobs run as an **Entra service principal** (`run_as` in the bundle), never as a person.
  CI/CD deploys with workload identity federation (OIDC from the CI system) rather than stored client
  secrets.
- **Managed identities** for Azure-to-Azure access:
  - the Access Connector identity for ADLS through UC,
  - the Container App identity for Key Vault and the SQL warehouse, and
  - Event Hubs RBAC roles for the producer.

**Secrets**

- First preference is no secret at all: managed identity, UC storage credentials, and UC service
  credentials where available.
- For anything that remains (for example an Event Hubs SAS string, if OAuth is not feasible), use an
  **Azure Key Vault-backed secret scope**. The scope is read-only from Databricks.
  - Anyone granted the scope can read every secret in that vault, so use one vault per trust boundary
    (for example `kv-meridian-ingest` separate from `kv-meridian-serving`).
  - Key Vault-backed scopes support only the vault access-policy permission model, not Azure RBAC.
  - Creating one requires an Azure-managed (Entra) identity, not a Databricks PAT.
- Secrets are referenced as `{{secrets/<scope>/<key>}}` or `dbutils.secrets.get`. They never appear in
  bundle files, notebooks or git.

**Access control**

- **UC grants**:
  - engineers have `MODIFY` on `bronze`/`silver`/`gold` in dev and only `SELECT` in prod;
  - the job service principal is the owner of prod schemas;
  - analysts have `USE CATALOG` + `USE SCHEMA` + `SELECT` on `gold_serving` only;
  - nobody browses Bronze raw payloads except operators.
- **Column masks and row filters.** A mask on `customer_id` for analysts who do not need identified
  buyers. Optionally a row filter by country for regional users, driven by a mapping table and
  `is_account_group_member()`.
- **Power BI RLS.** Roles on `dim_geography[country]` mirror the UC row filter when the report uses
  Import mode. RLS restricts viewers. It does not apply to workspace members with edit rights, so keep
  editors few.
- **The unknown-customer member stays visible in coverage measures.** Masking a customer must not
  reclassify a line as unknown, because that would change coverage KPIs.

## 8. Serving layer

Options, in order of preference for this workload:

1. **Databricks SQL serverless warehouse + Power BI** through the Azure Databricks connector.
   - Import mode with incremental refresh for the fact.
   - Dimensions in Import mode. DirectQuery is only worth it if sub-refresh latency matters.
   - Authenticate with Entra OAuth SSO, or a service principal for scheduled refresh.
   - The star schema, `__UNKNOWN__` member and DAX in `powerbi/` carry over unchanged. The `.csv`
     sources become warehouse tables.
2. **Microsoft Fabric**, if the client is standardizing on it. Power BI is part of Fabric, and semantic models can
   be hosted on Fabric capacity (F SKUs). Fabric options for reading UC-governed Delta data exist. Validate the
   current integration options (for example catalog mirroring or OneLake shortcuts) and their
   governance implications before committing. They were not verified for this document.
3. **Keep DuckDB** for the FastAPI product, reading the validated snapshot exported to ADLS. At about
   1M rows, with a nightly publication, this avoids an always-available warehouse. It keeps the same
   rule as locally: read only manifest-listed files of a validated snapshot.

**Azure Synapse Analytics was considered, not chosen, and not implemented.**

- *Synapse Spark pools* would duplicate Databricks capability with a weaker UC/Delta governance story
  for this design.
- *Dedicated SQL pools* add an always-provisioned MPP warehouse that a 1M-row star schema does not
  need.
- *Serverless SQL pools* could query Parquet/Delta in ADLS cheaply, and are the most relevant Synapse
  option. But they add a second query engine and permission model alongside UC.
- Microsoft currently states that Synapse remains supported, while Fabric is the preferred path
  forward, and it publishes migration guidance from Synapse dedicated SQL pools to Fabric Warehouse.
  A new build on Synapse would therefore start with a likely second migration.

Synapse would become reasonable only if the client already operates it, with established security and
operations.

## 9. Private networking

- **Azure Databricks classic compute**:
  - VNet injection into a customer VNet with dedicated host and container subnets,
  - secure cluster connectivity (no public IPs on cluster nodes), and
  - Private Link for front-end (workspace UI/API) and back-end (compute plane to control plane)
    connectivity.
- **Serverless compute** (jobs and SQL warehouse) reaches customer resources through Databricks
  network connectivity configurations with private endpoints to the storage account. Verify current
  serverless networking features for the chosen region before design sign-off.
- **Private endpoints** for:
  - ADLS Gen2 (`dfs` and `blob` sub-resources),
  - Key Vault,
  - Event Hubs (check private endpoint availability for the chosen tier in current docs), and
  - the Container App environment (internal ingress).
- Matching private DNS zones are required for each.
- Storage, Key Vault and Event Hubs firewalls set to deny public network access once private endpoints
  are in place.
- **Power BI service to a private Databricks workspace** requires a VNet data gateway or an on-premises
  data gateway, plus the matching licence and capacity requirements. Otherwise the warehouse must
  allow the Power BI service network path.
- **Egress control.** UDR and Azure Firewall or a NAT gateway for classic compute egress, with only
  the required destinations allowed.
- Databricks Free Edition offers no private networking. It is suitable only for the public synthetic
  contract check, never for client data.

## 10. Operational ownership (RACI)

R = responsible, A = accountable, C = consulted, I = informed.

| Activity | Data engineering | Platform / cloud ops | Analytics / BI | Client data owner | Security |
|---|---|---|---|---|---|
| Source delivery, checksum pin, schema changes | R | I | I | **A** | I |
| Bronze/Silver/Gold SQL and contracts | **A/R** | I | C | C | I |
| Lakeflow Jobs, bundles, CI/CD | **A/R** | C | I | I | C |
| Workspace, UC metastore, networking, Key Vault | C | **A/R** | I | I | C |
| UC grants, masks, row filters | R | C | C | **A** (data access policy) | C |
| Publication validation failures, reruns, backfills | **A/R** | I | I | I (incident notice) | I |
| Power BI model, DAX, RLS roles, refresh schedule | C | I | **A/R** | C | C |
| Reconciliation sign-off (report vs SQL) | R | I | R | **A** | I |
| Cost monitoring and budgets | C | **A/R** | I | I (budget owner) | I |
| Secret rotation, access reviews | I | R | I | I | **A** |

## 11. Cost drivers (no prices quoted)

| Meter | Driven by | Control |
|---|---|---|
| Azure Databricks DBUs per SKU: Jobs Compute, Jobs Serverless, SQL (serverless), All-Purpose | job duration × compute size; warehouse uptime; interactive notebook time | Job compute rather than all-purpose; `availableNow` batches rather than always-on streams; warehouse auto-stop; small warehouse size |
| Azure VMs + managed disks (classic compute only) | cluster nodes × hours | job clusters terminate on completion; spot for non-critical stages |
| ADLS Gen2 capacity + transactions + redundancy option | Delta history retained for the reproducibility horizon; small-file writes; checkpoints | `OPTIMIZE`; a retention horizon that is no longer than needed; lifecycle tiering of `landing` |
| Event Hubs throughput units (Standard) / processing units (Premium) / capacity units (Dedicated); ingress events; Capture | replay rate, partitions, retention, Capture enabled | Premium only if Kafka compression or transactions are required; consider Auto Loader file ingestion instead |
| Private endpoints (hourly + data processed), NAT gateway / Azure Firewall | number of endpoints; egress volume | consolidate per environment |
| Key Vault operations | secret reads per job | prefer managed identity |
| Log Analytics / Azure Monitor ingestion | diagnostic settings volume | route only the needed categories |
| Power BI Pro / PPU licences, or Fabric capacity (F SKU) | number of authors and viewers; refresh needs | Import + incremental refresh; avoid capacity sized for DirectQuery real-time if not needed |
| Container Apps (API) | vCPU-seconds, memory, requests | scale to zero outside demo hours |

Official pricing pages:

- [Azure Databricks](https://azure.microsoft.com/en-us/pricing/details/databricks/)
- [Storage](https://azure.microsoft.com/en-us/pricing/details/storage/data-lake/)
- [Event Hubs](https://azure.microsoft.com/en-us/pricing/details/event-hubs/)
- [Key Vault](https://azure.microsoft.com/en-us/pricing/details/key-vault/)
- [Private Link](https://azure.microsoft.com/en-us/pricing/details/private-link/)
- [Power BI](https://www.microsoft.com/en-us/power-platform/products/power-bi/pricing)
- [Fabric](https://azure.microsoft.com/en-us/pricing/details/microsoft-fabric/)
- [Azure pricing calculator](https://azure.microsoft.com/en-us/pricing/calculator/)

Estimate with the calculator from *measured* local job durations (`evidence/execution/`). The full
Spark batch took 125 s on `local[2]`, which is not a cloud measurement. Re-measure in the target
environment before quoting.

## 12. Migration risks

| Risk | Impact | Mitigation |
|---|---|---|
| ANSI mode default differs (serverless `true`, local Spark 3.5.7 `false`) | `try_cast`/overflow and cast behaviour changes, so rows could be classified differently or the job could fail | Set explicitly (as the `databricks/` notebooks do), then re-run the contract and full-history equivalence (EXCEPT ALL on identities and exact amounts) |
| Runtime version drift (Spark/Delta/Python) | Different decimal, NTZ or SQL behaviour | Pin a runtime LTS; gate promotion on the local oracle + contract dataset + full equivalence |
| Event Hubs semantics (at-least-once, no compression on Standard, transactions preview, quotas) | Producer errors; duplicate deliveries; retention shorter than replay needs | Producer config per tier; idempotent writes already assumed; Bronze is the durable log; Capture archive |
| Timezone handling | Civil UK timestamps shifted to UTC, corrupting dates around BST | Keep `TIMESTAMP_NTZ`; re-run the April–October civil-time check from `tests/check_full_snapshot.py` |
| `VACUUM` deleting versions pinned by a published manifest | Published snapshot not reproducible | Retention property ≥ reproducibility horizon; vacuum only by a job that reads manifests |
| Concurrent publishers | Pointer races; mixed-version serving | `max_concurrent_runs: 1` plus a compare-and-swap pointer MERGE |
| Power BI incremental refresh missing late adjustments in archived partitions | Report disagrees with the SQL reconciliation | Refresh window ≥ supported lateness; detect data changes on a publication timestamp; reconcile report vs `reconciliation.json`-style SQL after every publication |
| Free Edition used for anything beyond the synthetic contract check | Non-commercial terms; no private networking; no SLA | Use Free Edition for synthetic contract validation only. Client data requires a governed Azure Databricks workspace. |
| Over-architecture at 1M rows | Cost and operational burden without benefit | Keep the DuckDB baseline comparison. Adopt Event Hubs only for a demonstrated sub-daily or replay requirement. |
| Treating this document as deployment proof | Misleading claims | The capability table above; only executed run evidence may move a row to verified-cloud |

## 13. Phased migration

0. **Local readiness (now).** Gates 1–4 pass locally, including Gate 3 replay and recovery (see `evidence/gates.json`). The contract dataset passes under DuckDB
   (`scripts/check_databricks_contract.py`). The Power BI exports reconcile (`scripts/export_powerbi.py`).
1. **Free contract check (optional, free).** In an existing Databricks Free Edition workspace, deploy
   `databricks/` and run the batch contract job. Then save the reconciliation JSON as
   `evidence/databricks-contract-run.json`. This is the first and only step that could create
   verified-cloud evidence without paid resources. Only synthetic data is used.
2. **Landing zone (client-funded).** Resource groups per environment (dev/test/prod), ADLS Gen2, Key
   Vault, Access Connector, and an Azure Databricks workspace with UC, VNet injection and private
   endpoints, all in IaC. Entra groups and SCIM. Budgets and cost alerts set before any compute runs.
3. **Batch parity.** Lakeflow Jobs bundle for convert → bronze → silver → gold → validate → publish.
   Gate on:
   - the hand-calculated oracle,
   - the contract dataset,
   - full-history equivalence with the local snapshot (identical 1,061,664 identities and £19,379,245.82
     net), and
   - the civil-time check.
4. **Serving.** Databricks SQL warehouse and the Power BI semantic model from `powerbi/` (Import +
   incremental refresh), with UC grants, masks and RLS. Reconcile the report against SQL after each
   publication. Decide whether the API stays on DuckDB-over-snapshot or moves to the warehouse.
5. **Streaming (only if justified).** Event Hubs namespace and event hubs in IaC. Producer and consumer
   SASL/OAuth configuration. Replay incident drills (duplicates, late arrivals, interrupted publish,
   restart) re-executed in the cloud, with batch/replay reconciliation. If daily files suffice, use Auto
   Loader instead and skip Event Hubs.
6. **Operations.** Alerts on job failure, publication age, and reconciliation status. RACI handover,
   access reviews, secret rotation, and cost review against the measured meters.

## Sources

Fetched and read for this document (accessed 2026-11-16):

- [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations): page updated 2026-10-18.
- [Serverless compute limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations): updated 2026-11-05.
- [Serverless Spark configuration properties](https://docs.databricks.com/aws/en/spark/conf).
- [Declarative Automation Bundles (formerly Databricks Asset Bundles)](https://docs.databricks.com/aws/en/dev-tools/bundles/): updated 2026-11-05.
- [Lakeflow Spark Declarative Pipelines: "Where is DLT?"](https://docs.databricks.com/aws/en/ldp/where-is-dlt) and [June 2025 release notes (Jobs renamed Lakeflow Jobs)](https://docs.databricks.com/gcp/release-notes/product/2025/june).
- [Apache Kafka protocol support in Azure Event Hubs](https://learn.microsoft.com/en-us/azure/event-hubs/azure-event-hubs-apache-kafka-overview): ms.date 2026-02-05.
- [Azure Databricks secret scopes](https://learn.microsoft.com/azure/databricks/security/secrets/secret-scopes).
- [Power BI incremental refresh and real-time data](https://learn.microsoft.com/en-us/power-bi/connect-data/incremental-refresh-overview): updated 2026-07-01.
- [Power Query Parquet connector](https://learn.microsoft.com/en-us/power-query/connectors/parquet).
- [Migration: Synapse dedicated SQL pools to Fabric Warehouse](https://learn.microsoft.com/fabric/data-warehouse/migration-synapse-dedicated-sql-pool-warehouse).
- [Fabric blog: why Fabric Data Warehouse is the modernization path for Synapse](https://community.fabric.microsoft.com/t5/Fabric-Updates-Blog/Why-Fabric-Data-Warehouse-is-the-Modernization-Path-for-Synapse/ba-p/5178874).

Further reading. These pages were not re-fetched for this document, so confirm current details before
design sign-off:

- [Unity Catalog on Azure Databricks](https://learn.microsoft.com/azure/databricks/data-governance/unity-catalog/)
- [VNet injection](https://learn.microsoft.com/azure/databricks/security/network/classic/vnet-inject)
- [Serverless compute networking](https://learn.microsoft.com/azure/databricks/security/network/serverless-network-security/)
- [Auto Loader](https://learn.microsoft.com/azure/databricks/ingestion/cloud-object-storage/auto-loader/)
- [Event Hubs quotas](https://learn.microsoft.com/azure/event-hubs/event-hubs-quotas)
- [Connect Power BI to Azure Databricks](https://learn.microsoft.com/azure/databricks/partners/bi/power-bi)
- [Unity Catalog row filters and column masks](https://learn.microsoft.com/azure/databricks/tables/row-and-column-filters)
