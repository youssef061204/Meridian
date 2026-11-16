# MERIDIAN metric dictionary and independent oracle

This is a portfolio case study using historical invoice exports. Values are GBP
recorded merchandise invoice amounts, not audited accounting revenue, profit,
payment settlement, or cash collected. Historical, replay, and synthetic results
must be kept separate in storage, manifests, and the interface.

## Contract and grain

The underlying logical grain is one immutable workbook row. Its identifier is
SHA256 of the UTF-8 string `source_sha256|source_sheet|source_row`. Workbook row
coordinates count the header as row 1. Identical-looking business fields at
different original coordinates remain distinct. A new delivery of the same
logical row is a repeated delivery, not a new sale. Conflicting content for the
same identity requires an explicit revision and a recorded correction policy;
choosing an arbitrary payload is not a safe resolution.

Delivery metadata (run, Kafka partition/offset, received time) has a different
grain from the source row. Report received deliveries and distinct original
source rows with separate denominators. Missing customer IDs do not invalidate
otherwise valid merchandise lines or manufacture new known customers.

Preserve original naive invoice timestamps. Until the retailer confirms the
source clock, interpret their dates as UK local business dates without claiming
that these are timezone-aware source observations. Replay and ingestion times
must be recorded separately in UTC.

## Validation and classification rules

Required fields are nonempty invoice and stock code, a parseable event timestamp,
a nonzero integral quantity, and a finite nonnegative unit price. Negative unit
prices, fractional or missing quantities, missing required fields, and invalid
timestamps are quarantined. A cancellation invoice (case-insensitive prefix
`C`) with a positive quantity is quarantined for an ambiguous sign; do not
silently negate it. A valid negative quantity is a negative adjustment, including
when its invoice lacks a `C` prefix. Do not infer which original invoice it
adjusts. A valid zero-price item is accepted and contributes units and coverage,
but no money.

After validation, exact case-insensitive stock codes `POST`, `DOT`, `M`, `M2`,
`C2`, `D`, `B`, `ADJUST`, `ADJUST2`, `BANK CHARGES`, `AMAZONFEE`, `CRUK`,
`TEST001`, and `TEST002` are excluded by the named non-merchandise rule. This
explicit initial list is a portfolio modeling assumption to review with the
retailer. Do not apply fuzzy description matching or imply every other source
code is conclusively merchandise. Invalid records with these codes remain
quarantined so validation failures remain visible.

Unit prices use decimal arithmetic with four fractional digits; reject or
explicitly reconcile values outside that supported precision. Line amounts are
quantity times unit price rounded once per line to two decimals using half up
(`ROUND_HALF_UP`, ties away from zero). Sum those rounded line amounts. This
rounding policy is an explicit modeling decision rather than a claim about the
retailer's accounting policy. Avoid binary floating-point aggregation.

## KPI definitions

All money KPIs have invoice-line grain, GBP currency, and the published snapshot
plus active business-date/country filters. They include only accepted merchandise
logical records after revision handling and deduplication. Each serving snapshot
must record the corresponding source checksum, mode, SQL version, and Delta table
version. Related table commits are not one cross-table Delta transaction; the
validated publication manifest selects the consistent set.

| Metric | Formula and denominator | Limitations |
| --- | --- | --- |
| Gross positive merchandise sales | Sum rounded `line_amount` where amount > 0 | Observed invoice value, not order-level booked revenue or payment. Zero-price items add no amount. |
| Cancellation/negative adjustments | Sum rounded `line_amount` where amount < 0; signed negative GBP | Includes negative quantities without cancellation prefix. No automatic linkage to original invoice. |
| Net recorded merchandise sales | Gross positive merchandise sales + signed negative adjustments | Missing customers remain included. Does not establish refunds settled or recognized revenue. |
| Positive merchandise units | Sum `quantity` where valid merchandise quantity > 0 | Includes zero-price items. Observed sales, not unconstrained demand or inventory. |
| Known customer coverage | Accepted merchandise lines with a nonempty customer ID / all accepted merchandise lines | Line-weighted coverage. Retention and RFM use identified buyers only; unknown is not a customer cohort. |
| Repeated deliveries | Received deliveries minus distinct source identities, after checking conflicting payloads | This is a delivery denominator, not a deduplicated source-row count. |
| Quarantine share | Distinct quarantined source rows / all distinct source rows | Display reasons; amounts may still be parseable and must reconcile. |
| Source amount reconciliation | Accepted net amount + excluded parseable amount + quarantined parseable amount = all parseable distinct source amounts | Separately count unparseable amounts. Do not silently treat them as zero. |
| Row reconciliation | Accepted + excluded + quarantined = all distinct source rows | Received deliveries = distinct source rows + repeated deliveries. |
| Snapshot freshness | Current UTC time minus last validated publication UTC time | Distinct from dataset historical age and historical replay processing lag. |

Product and country aggregations must sum back to the fact total. Joins require
one dimension row per joined key, including a single visible unknown member.
Using a product description as an additional joined key can multiply fact rows;
select and document a single description rather than accidentally broadening
dimension cardinality.

## Hand-calculated synthetic fixture

`tests/fixtures/lines.json` is synthetic test data, never additional observed
transactions. It contains 16 distinct original rows and 18 deliveries. The two
legitimate identical-looking widget rows contribute £20 each. An anonymous
purchase contributes £7.50; negative adjustments contribute −£10 and −£2.50;
zero-price merchandise contributes four positive units. The rounding pair
`3 × £0.335` and `−3 × £0.335` contributes £1.01 and −£1.01.

| Expected outcome | Exact value |
| --- | ---: |
| Received deliveries | 18 |
| Distinct source rows | 16 |
| Repeated deliveries | 2 |
| Accepted merchandise rows | 8 |
| Quarantined rows | 7 |
| Excluded non-merchandise rows | 1 |
| Gross positive merchandise sales | £48.51 |
| Signed negative adjustments | −£13.51 |
| Net recorded merchandise sales | £35.00 |
| Positive merchandise units | 14 |
| Known customer line coverage | 6 / 8 = 75% |
| Quarantined parseable amount | £11.50 |
| Excluded parseable amount | £5.00 |
| All parseable distinct source amount | £51.50 |
| Unparseable distinct source amounts | 1 |
| Deliberately unsafe accepted delivery sum | £62.50 |

The unsafe total includes a repeated £20 row and a repeated £7.50 anonymous row.
It is a separate comparison calculation and must never become a trusted KPI.
Quarantine includes −£3 negative price, £2 missing invoice, £10 ambiguous
positive cancellation, £0 zero quantity, £1 invalid timestamp, £1.50 fractional
quantity, and one missing-quantity amount that cannot be parsed.

`tests/test_business_oracle.py` uses standard-library `Decimal`, no production
transformations, and explicit expected constants. Run it with:

```text
python -m unittest discover -s tests -p test_business_oracle.py -v
```

Passing this oracle test alone does not prove the Spark implementation. The
Spark/Delta path must execute the fixture and match every expected result.
