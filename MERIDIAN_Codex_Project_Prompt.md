You are my lead data engineer, analytics consultant, and product engineer. Build MERIDIAN: a retail revenue and demand intelligence platform with an interactive data reliability demonstration.

I am applying for Avanade's Canada Data Engineering Internship. The role emphasizes exploratory analysis, SQL/data modeling, statistics, forecasting, visualization, client communication, and exposure to Spark, Kafka, Databricks, and Azure. Deliver a project I can run, demonstrate, explain, and defend technically. I already have substantial ML projects; make reliable data engineering and useful business decisions the center of this one.

Treat this as an implementation assignment. Inspect the workspace, summarize a practical plan, and begin building. Make routine design decisions independently. Continue through implementation and verification; maintain a clear handoff if a session ends. Use available sub-agents for independent engineering and review tasks if supported, with clear file ownership.
1. BUSINESS CASE AND FINISHED EXPERIENCE
The fictional client is a UK online retailer whose invoice exports, sales dashboard, and demand plans disagree. Use a real public dataset for the analysis; clearly identify the consulting engagement as a portfolio case study.

The finished product must answer:
- What were gross positive merchandise sales, cancellation/negative adjustments, and net recorded merchandise sales?
- Which products, countries, and customer cohorts drive the observed changes?
- Which numbers are trustworthy, incomplete, or awaiting reconciliation?
- What sales volume should the retailer plan for over the next 7 and 28 days?
- Which decisions does the evidence support, and what additional client data would be necessary?
Create a memorable demonstration: a reviewer selects a revenue KPI, drills into its contributing transactions and transformation lineage, injects a controlled data incident, sees the resulting discrepancy in an explicitly unsafe comparison calculation, then watches the trusted pipeline detect, contain, and recover from it.

The real pipeline must process the incident. Status badges, graphs, counters, and recovery results must come from persisted execution evidence.
2. CONSTRAINTS AND ARCHITECTURE
The software must run locally without paid services, API credits, credit cards, cloud trials, or a GPU. Support Windows through WSL2/Docker and Linux. Inspect available CPU, memory, and disk first. Provide a small demo profile and a full-data profile; measure their actual resource requirements.

Preferred stack:
- Python, PySpark, Spark SQL, and Delta Lake for ingestion, transformations, and versioned tables.
- Apache Kafka in a single-node local configuration for historical transaction replay.
- A small Python orchestration/CLI layer with explicit dependencies, retries, run manifests, and resumable stages. Add an orchestration framework only when it earns its operational cost.
- DuckDB over explicitly exported, versioned Gold snapshots for lightweight analytical serving.
- FastAPI and TypeScript/React for a polished product interface.
- A CPU-based forecasting model such as LightGBM or scikit-learn.
Resolve and pin compatible Spark, Delta, Java, Python, Kafka connector, and Scala artifact versions using current official documentation. Verify the combination with a tiny end-to-end job before building the full system.

Use real Spark and Kafka on the full path. If the environment cannot execute them, keep making independent progress and clearly mark those integrations unverified. A pandas fallback must not count as a passed Spark implementation.

Explain when a simpler DuckDB-only batch pipeline would be the better business choice at this dataset's size. Implement that small baseline for a fair comparison; justify the additional architecture by demonstrated replay/recovery requirements and measured scale, not by brand names.
3. REAL DATA AND PROVENANCE
Use UCI Online Retail II as the primary source:
https://archive.ics.uci.edu/dataset/502/online+retail+ii

UCI documents 1,067,371 records from December 2009 to December 2011 under CC BY 4.0. Verify the downloaded workbook, both sheets, actual counts, date ranges, field names, and checksum. Attribute the source.

Provide a reproducible downloader and bounded-memory workbook-to-Parquet conversion. Preserve raw inputs, source sheet/row coordinates, source checksum, ingestion time, and conversion version.

The data contains invoice lines, cancellation indicators, quantities, prices, customer IDs, and countries. It does not establish actual inventory, product costs, marketing spend, payment settlement, or a real client's realized savings. Do not fabricate those fields or infer profit from sales.

Preserve GBP amounts. Preserve original timestamps and document any timezone assumption. Keep unknown customer IDs visible in coverage statistics.

Keep three modes physically and visibly separate:
- Historical: original observed source records.
- Replay: those records emitted through Kafka on an accelerated clock.
- Synthetic stress/incidents: seeded generated or modified events for reliability and scale testing.
Never describe replay as a live retailer integration. Never describe synthetic records as additional observed transactions. A 10-million-event stress profile is a target to attempt within hardware limits, not a prewritten accomplishment. A smaller honestly measured run is acceptable.
4. LAKEHOUSE AND BUSINESS SEMANTICS
Build Bronze, Silver, and Gold layers with documented table grains.

Bronze:
- Preserve source records and received event envelopes, including malformed inputs when possible.
- Retain delivery metadata separately from the identity of the underlying source record.
- Record schema version, logical source identity, event time, ingestion/replay time, and run ID.
Silver:
- Normalize types, validate contracts, classify transaction types, and isolate questionable records.
- Use decimal arithmetic for money and define rounding.
- Preserve cancellation/negative adjustments and distinguish merchandise from non-merchandise codes through documented rules.
- Define a stable line identity from the pinned immutable source and row coordinates. Invoice plus product is not necessarily unique.
- Replayed copies of the same source row must deduplicate. Identical-looking rows at different original coordinates must not disappear without evidence.
- If introducing corrections, preserve the original logical identity and explicit revision; do not treat every re-export as new sales.
- Keep missing-customer transactions in valid sales totals where possible.
- Avoid inventing a link from every cancellation to a particular original invoice.
Gold:
- Implement an invoice-line fact table and useful date, product, customer, and geography dimensions.
- Define key cardinalities and prevent joins from multiplying sales.
- Build daily sales, product/country performance, customer cohorts, cancellation/negative-adjustment metrics, forecasting inputs, and quality/freshness marts.
- Use version-controlled SQL for the main business transformations.
- Define every KPI's formula, grain, filters, denominator, currency, and limitations in a metric dictionary.
- Publish related serving tables as a consistent versioned snapshot only after validation. Do not imply independent table commits are one cross-table transaction.
- Export through Delta-aware reads; never glob underlying Delta Parquet files as if they were the current table.
Reconciliation must account for every source row and every amount that can be parsed: accepted, quarantined, excluded by a named business rule, or repeated delivery. Original source rows and received deliveries have different denominators; report both.

Do not label recorded net invoice value as audited accounting revenue or cash collected.
5. STREAMING, FAILURES, AND RECOVERY
Implement accelerated historical replay with configurable speed, pause/resume, and deterministic event ordering. Preserve source time separately from replay time.

Demonstrate:
- Repeated deliveries within and across micro-batches.
- Out-of-order and late events.
- Missing required fields and incompatible schema changes.
- An explicitly supported additive schema change.
- A consumer interruption and restart during processing.
- A deliberately interrupted multi-stage publish.
- A correction and an affected-date backfill.
Set an event-time policy and document watermark behavior. Route arrivals beyond the supported window into a durable reconciliation/backfill path; do not silently lose financial adjustments.

Make writes and reprocessing idempotent. Prove this with fault injection around commit/checkpoint boundaries. If using foreachBatch, reason explicitly about its write semantics and retry behavior; never claim that Kafka plus Spark automatically provides end-to-end exactly-once business results.

Prove batch and replay produce the same accepted logical records and monetary totals after reconciliation. Demonstrate safe re-running of a backfill.

Retain the last validated serving snapshot when publication fails, clearly show its age and failure state, and prevent partial data from being displayed as current.

Expose real measurements: throughput, processing lag, late-event counts, rejected events, retry counts, freshness, and reconciliation status. Historical replay lag must not be confused with the age of the original dataset.
6. EDA, SQL, AND STATISTICS
Produce a reproducible analysis notebook and client-readable findings covering seasonality, concentration, repeat purchasing, cohort retention, cancellation/adjustment patterns, missingness, and extreme values.

Implement and document representative SQL questions using joins, CTEs, window functions, cohort calculations, and incremental updates.

For each substantive finding:
- State the business question and denominator.
- Link to the query, data snapshot, and supporting chart.
- Quantify the result and relevant uncertainty.
- Explain a plausible action and competing explanations.
- Distinguish association from causation.
Use appropriate intervals or resampling where they help assess a comparison. Account for temporal dependence when bootstrapping daily metrics. Avoid automatic significance claims across hundreds of products.

Show how missing customer identifiers change the scope of retention/RFM analysis. Do not treat an unidentified buyer as a new known customer. Do not claim customer lifetime value from a short observation window without a defensible model.
7. FORECASTING WITH HONEST EVALUATION
Forecast positive merchandise units sold at aggregate and selected product levels for 7- and 28-day horizons. These are observed sales forecasts, not measurements of unconstrained demand.

Compare:
- Weekly seasonal naive.
- A simple trailing-average baseline.
- One well-designed tabular forecasting model using lagged sales, trailing statistics, and calendar features.
Use at least four rolling chronological evaluation origins, with a final untouched test period. Define origins and intervals before tuning. Product selection, imputation, feature normalization, and interval calibration must use only information available at each origin.

Do not use random train/test splits, future customer aggregates, future prices, centered rolling averages, or actual future sales when generating multi-step forecasts.

Handle partial final periods, sparse histories, unknown observation gaps, cold starts, and zero denominators explicitly. Distinguish observed zero sales from unavailable data.

Report MAE, WAPE where defined, per-horizon and per-series results, aggregate results, and an 80% prediction interval's observed coverage and width. Calibrate intervals using past-only residuals. Include a meaningful leakage test.

Preserve predictions and actuals for reproducibility. If the simple baseline wins, promote it and explain the result. Improvement percentages must come from completed runs.

After the core is complete, add a clearly labeled inventory scenario simulator. Use explicit assumed starting stock, lead times, and holding/shortage costs; compare a fixed policy, seasonal-naive policy, and selected-model policy on identical held-out episodes and assumptions. Report sensitivity. Observed sales are only a demand proxy, and any savings are simulated, not client outcomes.
8. PRODUCT AND SIGNATURE DEMONSTRATION
Create four strong, functional views:

A. Business Overview
Revenue bridge, comparable-period trends, products/countries/cohorts, meaningful filters, GBP formatting, and concise evidence-backed observations. Clearly label the historical date range.

B. Forecast and Planning
Actuals, forecast ranges, baseline comparison, error breakdowns, and any explicitly simulated planning scenarios.

C. Data Trust and Recovery
Source-to-serving counts, quality failures, freshness, incident timeline, real job state, and a controlled local incident injector with a safe reset.

D. Metric Explorer
Trace a selected KPI through its SQL model, publication manifest, Delta version, and contributing source rows. Show reconciliation before and after a correction.

Use clean typography, restrained color, accessible charts, keyboard support, loading/error/empty states, and useful tables. Every visible control must work. Avoid filler sections and decorative infrastructure diagrams.

The signature two-minute sequence:
1. Open a KPI and its source evidence.
2. Replay duplicate and malformed events.
3. Show the deliberately unsafe calculation's discrepancy, clearly isolated from the trusted result.
4. Show duplicate handling, quarantine, and failed publication where appropriate.
5. Repair/reprocess the controlled incident.
6. Show reconciled results, new snapshot/version, recovery duration, and audit evidence.
Historical/replay/synthetic labels must persist across all relevant views. Operational actions should be bound to the local demo and must not expose unauthenticated infrastructure controls publicly.
9. MICROSOFT AND DATABRICKS CONNECTION
Keep the core free and runnable locally.

Deliver a substantive Azure migration design mapping the implemented components to ADLS Gen2, Azure Databricks, Event Hubs, orchestration, and Power BI or an appropriate SQL serving layer. Discuss identity, secrets, access control, private networking, incremental refresh, operational ownership, cost drivers, and migration risks. Include Synapse as a considered alternative where relevant, not as a fictitious implemented service.

Provide portable notebooks/jobs and a small contract-validation dataset for Databricks. If an authenticated Free Edition workspace is already available and the workflow is supported, run the batch path and record evidence. Otherwise mark it prepared but unexecuted. Verify current quotas/network/streaming restrictions; do not assume the local Kafka path works there.

Provide Gold exports, relationships, metric definitions, and DAX measures for a Power BI report. If native report authoring is available, build and open a genuine report and reconcile its headline measures against the SQL results. Otherwise provide precise setup instructions and label the native report unfinished; do not rename JSON into a PBIX file.

Never provision paid resources or activate a trial. Architecture documentation is not evidence of Azure deployment. Keep verified-local, verified-cloud, and planned capabilities distinct.
10. VERIFICATION, BENCHMARKS, AND DELIVERY
Use small hand-calculated fixtures as an independent oracle for monetary totals, repeated deliveries versus legitimate repeated lines, missing customers, adjustments, and join cardinality.

Verify the full integration with real Kafka and Spark: duplicate replay, interrupted writes, restart, late arrival/backfill, and consistent publication. Exercise actual product flows in a browser. Verify a clean checkout can follow the documented startup path.

Benchmark:
- Full real-dataset processing.
- At least one synthetic scaling run that fits available hardware.
- Analytical query latency.
- Replay throughput and ingestion-to-publication latency.
- Incident recovery time.
- A fair simple-batch baseline and any justified optimization.
Record commit SHA, dataset checksum, generated seed, hardware, software versions, exact commands, row counts, repetitions, and cold/warm cache conditions. Keep query-result equivalence checks with optimizations. Single-machine measurements must be described as single-machine measurements.

Prefer tests of business correctness and recovery over arbitrary test-count targets. Never invent metrics, screenshots, successful runs, or client savings.

Deliver:
- Working source, pinned dependencies, Docker configuration, and straightforward start/stop/demo commands.
- A README with a short demonstration path, real architecture, verified results, and honest limitations.
- Data contracts, table/metric dictionary, source attribution, and key design decisions.
- Reproducible EDA and forecasting outputs.
- Machine-readable benchmark and verification evidence.
- A concise client case study: problem, findings, recommended actions, uncertainty, proposed adoption steps, and expected cost drivers.
- A two-minute demo script and captured screenshots/video when the environment supports recording.
- An interview guide covering architecture tradeoffs, event identity, duplicates, time semantics, data modeling, forecasting leakage, and simpler alternatives.
- Three resume bullets using only verified results, each linked to its evidence.
- HANDOFF.md with commands, current state, completed gates, and precise remaining work.
11. EXECUTION ORDER AND COMPLETION GATES
Gate 1: A small genuine source slice flows through Spark/Delta to one reconciled KPI in the UI.
Gate 2: The full historical dataset, dimensional model, SQL analyses, and publication checks work.
Gate 3: Kafka replay, incidents, recovery, and batch/replay reconciliation pass.
Gate 4: Forecasting baselines, rolling evaluation, and the untouched test report are complete.
Gate 5: Product walkthrough, benchmark evidence, client case study, and interview materials are verified.
Gate 6: Add inventory scenarios and execute optional native Databricks/Power BI work only after the core is reliable.

Work incrementally and keep the demo functional. Explain important tradeoffs briefly, make sensible decisions, and proceed. If a requested tool is unavailable, complete the independent work and report exactly what remains unverified. Do not equate generated files with successfully executed capabilities.

Begin now: inspect the repository and environment, verify the source and dependency compatibility, choose a bounded implementation plan, and build Gate 1.