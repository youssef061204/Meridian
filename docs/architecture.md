# Local architecture and execution boundaries

MERIDIAN is a UK retail portfolio case study built from UCI Online Retail II. It is not a retailer integration, audited accounting system, inventory system, or deployed cloud service. The implementation follows the ordered gates in `MERIDIAN_Codex_Project_Prompt.md`; `HANDOFF.md` and machine-readable execution evidence determine which gates have passed.

## Gate 1 vertical slice

The current implementation path is:

```text
Pinned UCI ZIP/workbook
    -> streaming XLSX reader -> bounded-row-group Parquet
    -> Spark Bronze Delta -> Spark SQL Silver Delta
    -> Spark Gold Delta tables
    -> reads pinned Delta versions -> immutable Parquet snapshot
    -> validation + publication manifest -> atomic active pointer
    -> DuckDB read-only analytical views -> FastAPI -> React/TypeScript UI
```

This describes implemented code, not proof of successful execution. A passing Spark smoke, actual source conversion, historical slice run, independent fixture reconciliation, API checks, and browser walkthrough are required before Gate 1 is complete. Resolving the Kafka connector class proves dependency loading only; it does not prove broker integration. Kafka processing, checkpoint recovery, late arrival handling, and correction/backfill are Gate 3 work and require separate executed evidence.

The demo profile selects the first 2,000 original rows from each workbook sheet. This gives a small genuine source slice from both sheets. It is intentionally not representative of the whole two-year history; its discontinuous date coverage must not support seasonality, forecasting, or comparable-period claims. Full-data conversion scans both sheets while holding only one 10,000-row chunk plus the small demo slice. The complete workbook and source coordinates remain available for lineage.

## Data paths and identities

| Path | Grain and purpose |
| --- | --- |
| `data/raw/` | Preserved downloaded archive/workbook; SHA-256 pins in `SOURCE.lock.json`. |
| `data/converted/` | Original historical rows converted to strings with source checksum, sheet, Excel row, ingestion UTC, and conversion version. |
| `data/lake/historical/demo/` | Delta tables for the genuine small historical profile. |
| `data/lake/historical/full/` | Delta tables for the full historical profile after Gate 2 execution. |
| `data/lake/synthetic/fixture/` | Synthetic hand-calculated oracle path, physically separate from observed data. |
| `data/serving/snapshots/<run_id>/` | Immutable explicit exports and manifest for one publication candidate. |
| `data/serving/current.json` | Active validated historical snapshot pointer. |
| `runtime/runs/<run_id>.json` | Persisted stage state, start/end, elapsed time, errors, and available git/version details. |
| `evidence/` | Small committed verification and resource artifacts; large source/lake/runtime files stay untracked. |

A source-line identity is SHA-256 of `workbook_sha256|sheet|source_row`. Two identical-looking original rows at different coordinates remain two records. Redelivering an original coordinate should contribute one accepted business record, while delivery counts remain separate. Immutable source checksums make historical row identity stable; corrections need explicit revisions rather than a new checksum masquerading as a new sale. The current batch overwrite path is suitable for deterministic historical rebuilds. It is not yet the durable received-envelope append log required for replay incidents.

Gate 3 must introduce physically separate replay and synthetic incident paths, including their own Bronze logs, checkpoints, topics/run identifiers, and publication pointers. Replay represents observed records delivered on an accelerated clock; synthetic events are generated or modified test evidence. Neither may become additional observed history. A supported route for conflicting same-identity payloads, additive schema versions, malformed envelopes, and late arrivals must be implemented before claiming replay safety.

All API responses carry the publication manifest's mode/profile; the UI must retain these labels. A fixture run must not replace `current.json`. Unpublished candidates must not be discoverable as the active business result.

## Table and money semantics

Bronze retains the converted source columns plus line identity and run. Silver has one logical source row with typed decimal amount, classification, and a disposition: accepted, quarantined, or excluded by an explicit non-merchandise rule. Missing customer IDs remain eligible for valid merchandise sales. No cancellation is automatically linked to an original invoice.

Gold currently contains the accepted invoice-line fact, daily sales, product performance, and country performance. Daily/product/country sums reconcile to the fact. Useful date/product/customer/geography dimensions, cohorts, and join-cardinality checks belong to Gate 2. See `docs/metrics.md` and version-controlled `sql/` for formula, denominator, precision, rounding, classification, and coverage definitions.

Source civil invoice timestamps have no UTC offsets. Preserve their original text, interpret business dates as UK local dates, and avoid claiming the source supplied timezone-aware timestamps. Ingestion, replay, publication, and recovery times are separate UTC observations. Historical dataset age, snapshot publication age, and replay processing lag answer different questions.

## Consistent publication

Delta commits each table independently. MERIDIAN does not claim a cross-table Delta transaction. A publication run records the selected version of every related Delta table, then exports each by a Delta-aware `versionAsOf` read. DuckDB reads only those exported snapshot files, never a glob over a Delta table's physical Parquet files.

The candidate must satisfy logical-row uniqueness, row reconciliation, the GBP revenue bridge, and daily/product/country agreement before receiving its manifest. The manifest records source and SQL checksums, Delta versions, row dispositions, mode/profile, date bounds, metrics, and export file checksums. Only after these checks does an atomic JSON replacement switch the active pointer. Readers resolve that pointer once per request and load all relevant views from the same immutable directory.

A failure after export or before pointer replacement leaves the previous serving snapshot intact. Its publication timestamp and latest failed run remain visible; a candidate's existence is not a current-data claim. Fault injection must actually execute to prove this behavior. This approach assumes one local publisher; concurrent publishers require a lock or compare-and-swap policy before supported use. Local file atomic replacement is not a distributed publication protocol.

Bronze/Silver/Gold overwrite commits can advance during a failed run without changing the last validated serving snapshot. The published manifest retains the earlier selected Delta versions; old files must not be vacuumed before the declared reproducibility horizon.

## Runtime and free execution

Docker isolates Python 3.11 and Java 17 from the Windows defaults and supplies a Linux execution environment for Spark. The tools service uses Spark `local[2]`, four shuffle partitions, a 768MiB requested driver heap, and a 2,300MiB container cap. API and single-node Kafka have their own caps. These are configured limits, not demonstrated peak requirements. No cloud account, API credit, trial, GPU, or paid resource is needed.

Run `python scripts/capture_environment.py` using the project's Python environment to write `evidence/environment.json`. It captures CPU, RAM availability, repository-disk space, Docker VM capacity, package/tool versions, command argv, and git state without dumping secret environment variables. Resource availability is point-in-time evidence. Full/demo peak RSS, Docker usage, durations, throughput, repetitions, and cache conditions must be measured during actual profile benchmarks.

Pin direct dependencies in `pyproject.toml` and resolve transitives in `uv.lock`. A reproducible Docker strategy is to export a hash-bearing `requirements.lock.txt` from the frozen uv lock outside Docker, install it with pip `--require-hashes`, then install the local project with `--no-deps`. This requires no uv executable in the image and prevents `pip install .[extras]` from silently selecting newer transitive dependencies. The root Dockerfile must actually adopt the strategy before it can be described as enforced. The exact Java base image/build packages and pulled image digests also belong in execution evidence.

The API binds loopback through Compose. Gate 1 exposes read-only historical views. Incident controls require local-only binding and explicit local run restrictions before introduction; do not expose unauthenticated Kafka or job controls publicly.

## Why also build a simpler baseline

At roughly one million invoice lines, a single DuckDB process reading the same bounded Parquet conversion is likely a practical choice for an ordinary scheduled retail dashboard. It avoids a JVM, broker, checkpoint operations, and extra failure modes. This is an architectural hypothesis until measured, not an asserted speed advantage.

Gate 2 must implement the DuckDB-only batch baseline with equivalent decimal classification, source identity, row dispositions, KPI filters, and output checks. Benchmark both paths on the same immutable input, hardware, cache conditions, and result-equivalence checks. Use Spark/Kafka where the demonstrated requirement is replay, distributed processing growth, or recovery semantics; names alone do not justify operational cost.

## Gate boundaries and cloud portability

Gate 1 establishes the genuine source slice and reconciled visible KPI. Gate 2 adds full history, dimensions, analysis, and baseline comparison. Gate 3 proves real Kafka processing and recovery. Gate 4 evaluates forecasting on defined past-only chronological origins. Gate 5 completes product/browser evidence, benchmarks, and client/interview artifacts. Only then does Gate 6 add clearly simulated inventory scenarios and optional native tools.

Azure/Databricks and Power BI are portability deliverables rather than local runtime dependencies. A later migration design should map storage, compute, streaming, orchestration, identity, cost, and operational ownership to real services. Prepared notebooks, job definitions, DAX, and report setup instructions do not prove a cloud run or a native PBIX report. No paid resources or trials may be activated.
