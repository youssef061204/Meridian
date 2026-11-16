# Case study: making a UK online retailer's sales numbers agree

*Portfolio case study. The client is fictional. The data is the real public [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii) dataset (Daqing Chen, CC BY 4.0): 1,067,371 invoice lines, December 2009 to December 2011. No real client outcomes or savings are claimed. Every figure below comes from executed runs linked in [evidence/](../evidence).*

## The problem

The retailer's invoice exports, sales dashboard and demand plans disagree. Leadership wants to know:
- which sales number to trust;
- what drove the recent changes;
- how much stock to plan for over the next weeks.

## What we found

**1. The headline is defensible, as a recorded invoice value.** Across all accepted merchandise lines, the full workbook shows:

| | GBP |
|---|---:|
| Gross positive merchandise | £20,112,074.02 |
| Negative adjustments (cancellations, returns) | −£732,828.20 |
| **Net recorded merchandise** | **£19,379,245.82** |

Every one of the 1,067,371 source rows is accounted for:
- 1,061,664 accepted;
- 5,701 excluded by a named rule (postage, fees, manual adjustments);
- 6 quarantined.

Accepted, excluded and quarantined amounts reconcile exactly to the parseable source value. The DuckDB baseline reproduced the Spark result line for line, so the numbers do not depend on one engine. This is recorded invoice value, not audited revenue, settled cash or profit. ([findings](findings.md), [full-snapshot check](../evidence/full-snapshot-check.json))

**2. The exports overlap, and that is the first thing to resolve.** The two workbook sheets share 22,202 groups of identical-looking lines, mostly in December 2010. We kept every row and flagged the overlap rather than deleting duplicates by guesswork, because legitimate repeated lines also exist. Until the client supplies an immutable line ID or confirms the export boundaries, December 2010 totals are **awaiting reconciliation**. This is exactly the sort of silent duplication that makes dashboards disagree.

**3. Growth was modest and concentrated.** Comparing January–November 2011 with the same months of 2010:
- Net recorded sales rose **£183,792 (2.2%)**.
- A moving-block bootstrap of daily means gives a 95% band of −£3,696 to +£5,365 per observed day. **The change is not distinguishable from noise.**
- Australia (+£106,676) and France were the largest positive contributors; EIRE (−£81k) was the largest negative.
- The UK is 85.7% of gross merchandise value.
- These are associations within the export, not causes.

**4. Customer analysis covers only about three-quarters of the business.**
- 77.3% of accepted lines carry a customer ID. Anonymous buyers stay in sales totals but cannot be in retention analysis.
- 72.4% of identified customers bought on two or more invoices.
- The pooled month-1 return rate is 23.3%.
- The window is too short to estimate lifetime value.

**5. Planning: a forecast that beats simple rules, with honest uncertainty.** We forecast observed units sold, which is not unconstrained demand because there is no stock-out data.
- A gradient-boosting model was chosen **on six validation origins only**.
- On the untouched final month it cut WAPE versus a weekly seasonal-naive rule by 14.8% at the 28-day aggregate horizon. At 7 days the gain was only 3.1%.
- Its 80% intervals were too narrow in validation (70–78% coverage). Plan ranges should be widened or recalibrated before relying on them.
- ([forecasting](forecasting.md))

**6. Pipeline reliability is demonstrable, not asserted.** In the replay demonstration:
- The pipeline absorbed repeated deliveries, malformed and schema-breaking events, a conflicting re-export, late-arriving rows and a correction.
- It blocked publication until late rows were backfilled, then published a snapshot that reconciles exactly with batch.
- Repeated real process kills at commit boundaries produced no duplicate writes (21/21 drill checks).
- A naive sum of the raw feed overstated net sales by about £3,000 on a 4,000-row slice. That is the dashboard disagreement the client described.
- ([replay drill](../evidence/replay-drill.json))

## Recommended actions

1. **Adopt one metric definition and one publication point.** Use the gross → adjustments → net bridge with named exclusions, and serve dashboards only from a validated snapshot.
2. **Fix the source, not the dashboard.** Request immutable line IDs and export-boundary documentation to resolve the December 2010 overlap.
3. **Improve customer-ID capture** before investing in retention targeting.
4. **Use the forecast for aggregate 28-day planning.** Widen intervals, and pilot product-level use only for high-volume SKUs, where errors are still large (WAPE of about 0.64 on test).
5. **Do not act on simulated inventory savings.** The inventory scenarios show the preferred policy depends on the shortage-to-holding cost ratio. The client must supply real costs, lead times and stock positions first.

## Uncertainty and data the client must provide

- **What is missing:** the source has no inventory, cost, margin, promotion, payment-settlement or channel data. Profit, true demand and realised savings cannot be computed.
- **Unconfirmed source time:** the source timezone is unconfirmed; times are treated as UK civil time.
- **Unexplained gaps:** 135 calendar days have no records (mostly Saturdays and holidays). We do not know whether these are closures or export gaps.
- **Needed from the client:**
  - immutable source line IDs;
  - export schedules;
  - stock-on-hand and stock-out history;
  - unit costs;
  - promotion calendars;
  - refund settlement data.

## Proposed adoption steps and cost drivers

| Phase | Scope | Main cost drivers |
|---|---|---|
| 1. Batch trust (2–4 weeks) | Scheduled batch, validated snapshot, metric dictionary, reconciliation report | Analyst and engineer time; a small SQL engine. **At this volume a single-node DuckDB batch is sufficient.** |
| 2. Source fixes | Line IDs, export contract, overlap resolution | Client IT time; vendor export changes |
| 3. Incremental / streaming | Kafka-compatible ingestion (e.g. Azure Event Hubs), idempotent lakehouse writes, backfill path | Event throughput units, Databricks compute hours, storage, on-call ownership |
| 4. Planning | Forecast service, interval recalibration, inventory policy with real costs | Model monitoring, data for costs and stock |

The Spark/Kafka architecture earns its cost only when the client needs:
- replay;
- incremental corrections with an audit trail;
- recovery guarantees;
- growth well beyond a single node.

At about 1M lines per two years, the measured DuckDB baseline did the core batch transformation in 17 s versus 125 s for the full Spark/Delta run. That comparison is not like for like: the DuckDB run built fewer tables and kept no Delta history. See the [Azure migration design](azure-migration.md) for the cloud mapping.
