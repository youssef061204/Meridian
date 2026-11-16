# MERIDIAN Gold data model

Gold models are version-controlled Spark SQL in `sql/gold/`. `build_gold` returns
real Spark DataFrames; the pipeline writes their Delta versions and exports those
explicit versions to a validated serving snapshot. A manifest selects the related
versions after validation. Independent Delta commits are not a cross-table
transaction. Historical, replay, and synthetic results retain separate lake roots
and explicit publication modes.

## Fact and dimension relationships

`fact_invoice_line` has one accepted merchandise source row per immutable
`line_id`. It retains invoice, stock code, customer identifier, country, source
business date/time, signed quantity, decimal unit price and line amount, original
sheet/row coordinates, checksum, and classification. Missing customer IDs remain
in fact totals. Repeated deliveries have a separate Bronze denominator.

Silver represents the naive source civil timestamp as Spark `TIMESTAMP_NTZ`.
This preserves the workbook's displayed UK business clock through Delta and the
exported Parquet/DuckDB serving path, including summer dates. It does not assert
an original UTC offset or infer daylight-saving transitions. Operational UTC
ingestion/replay timestamps remain separate. Delta tables containing these
fields require the timestamp-without-time-zone table feature; compatible
Delta-aware readers and the pinned Spark/Delta versions are required.

| Table | Declared grain/key | Join and interpretation |
| --- | --- | --- |
| `dim_date` | One calendar `date` from first through final parseable source date | Fact `date` joins to `date`. Includes dates with no records; their availability is unknown. |
| `dim_product` | One `stock_code` | Fact `stock_code` joins to `stock_code`. Latest nonempty observed description wins; event time, sheet, row, and line ID give deterministic ties. Description variants never multiply a key. |
| `dim_customer` | One `customer_id`, plus reserved `__UNKNOWN__` | Join with `coalesce(fact.customer_id, '__UNKNOWN__')`. The unknown member is a reporting bucket, not one real customer. Known adjustment-only customers remain represented. |
| `dim_geography` | One normalized `country` label | Exact label join. A country records the invoice export label, not verified residence. Silver's `Unknown` label stays visible. |

Customer identifiers equal to the reserved unknown-member key fail validation.
All dimension keys must be nonnull and unique. A broadcast join through all four
dimensions must preserve fact count, distinct line identities, and exact decimal
GBP sum, with no unmatched foreign keys. `validate_gold` executes those Spark
actions and returns the measurements for the run manifest. Generated SQL or
an unexecuted validator does not constitute a passing integration.

## Calendar and observation availability

`observed_source_day` means at least one original source row has that parseable
business date. It does not establish that all trading activity was exported.
Dates without source records are `unavailable_no_source_records`; do not silently
impute their sales to zero. The calendar records source-row counts and first/last
event times. It flags the final source date `is_final_partial_source_day`
conservatively because the historical export ends within that date. No inference
about opening hours or actual business closure is made.

`forecast_inputs` has one `('__ALL__', date)` row. Positive units include accepted
positive-quantity merchandise, including zero-price lines. They are observed
sales, not unconstrained demand. A date with observed source rows can have an
observed zero accepted-unit sum; a no-source date has a null unit value. All final
source-day values remain visible but `is_observation_available` is false for
that partial date, dates without source records, and dates with quarantined
records. `observation_status`, source counts, and quarantine counts explain the
restriction. This is a modeling input mart, not an executed forecast evaluation.

## Customer analysis scope

`cohort_retention` has key `(cohort_month, activity_month)`. A cohort is the month
of a known customer's **first observed paid positive merchandise purchase**:
quantity > 0 and line amount > 0. Activity uses the same purchase rule. Free items
alone do not establish paid-purchase acquisition. Returns alone do not count as
repeat purchasing. Source-window left censoring means these may be existing
customers making their first purchase visible to us.

`cohort_size` is the number of known customers in that first-observed month.
`active_customers` counts distinct cohort customers with qualifying activity in
the activity month, and `retention_rate = active_customers / cohort_size`. It is
zero when source days are observed but no cohort customers return, and null for a
wholly unavailable activity month. Unknown IDs never enter cohort denominators.

Month completeness has separate fields. `is_period_within_source_bounds` requires
the entire month to lie inside the observed date boundaries and excludes the
partial final source date. `observed_source_days < calendar_days` flags unknown
source days; this may reflect ordinary closed days, export gaps, or other causes
that these files cannot distinguish. `is_period_complete` requires both full
calendar-boundary coverage and no unknown days. The final observed month always
has `is_final_partial_month = true`; partial-month rates should not be compared
as fully matured retention. No assumption about unobserved days is smuggled into
the definition.

`customer_summary` has one row per known ID and no anonymous pseudo-customer.
It records all accepted lines, gross positive GBP, signed adjustments, net GBP,
positive units, and first/last observed dates. Descriptive RFM columns are:

- `rfm_recency_days`: days from the last paid positive purchase to the snapshot's
  final source date, null for an adjustment-only or free-only customer.
- `rfm_frequency_invoices`: distinct observed invoice identifiers containing a
  paid positive purchase. It is not a count of underlying people or sessions.
- `rfm_monetary_gross_positive_gbp`: gross positive accepted merchandise amount,
  alongside explicit negative adjustments and net amounts.

`is_observed_repeat_purchaser` means at least two such invoices. All features have
explicit observation-window bounds and are descriptive; there is no CLV claim or
unqualified lifetime inference. These retrospective customer aggregates must not
be used as future-aware inputs at a historical forecasting origin.

## Quality and reconciliation

`quality_daily` has one logical source business-date row plus one null-date
bucket for invalid timestamps. It reports accepted, quarantined, and excluded
counts; missing-customer counts use source and accepted denominators separately.
Parseable decimal amounts retain their original signs across dispositions.
Unparseable amounts are counted separately and never silently become zero.

Across this table, accepted + quarantined + excluded must equal source rows, and
accepted + quarantined + excluded parseable amounts must equal source parseable
amounts. This grain counts original source rows, not Kafka deliveries. The run
manifest separately reports repeated deliveries and source/delivery totals.

`validate_gold` also executes uniqueness checks for every mart key. The quality
mart explicitly permits its one null-date bucket; all other keys are nonnull.

## Representative SQL techniques and business questions

| Question | SQL model and techniques | Interpretation |
| --- | --- | --- |
| Which product description should a reviewer see without multiplying sales? | `dim_product.sql`: CTEs, `row_number` window, deterministic latest-description selection, one-to-one aggregate join | Latest observed descriptive label, not a claim about a product master record. |
| How often do identifiable buyers return after their first observed purchase? | `cohort_retention.sql`: distinct customer-month activity, minimum first month, cohort aggregation, month grid and left joins | Known paid purchasers only; show maturity and unknown-day flags. |
| Which known customers buy repeatedly and what do their observed R/F/M values show? | `customer_summary.sql`: conditional aggregates, distinct invoice frequency, observed-window cross join | Descriptive observed behavior, with adjustment amounts preserved. |
| Which days are fit to enter forecasting evaluation? | `forecast_inputs.sql`: calendar left joins with observed-unit and quality aggregates | Missing observation and partial final periods stay explicit. |
| Where do discrepancies enter source-to-serving totals? | `quality_daily.sql`: conditional counts and signed amount bridges | Include invalid-date and unparseable-amount buckets. |

Incremental corrections and affected-date update semantics are a later recovery
gate. This batch model rebuilds the deterministic Gold tables from a pinned
source; it does not claim that changing an earlier purchase leaves later cohort
assignments unaffected. A backfill can change a customer's first-observed cohort
and requires all dependent cohort/RFM outputs to be republished consistently.
