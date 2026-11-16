# MERIDIAN Power BI semantic model

**Native report status: unfinished.** Power BI Desktop is not installed on the build machine (checked
2026-11-16). There was no `PBIDesktop.exe` under `Program Files` and no Power BI Appx package. No
`.pbix` or `.pbip` exists in this repository, and none will be created by renaming another file. This page
specifies the model that `SETUP.md` builds. `reconciliation.json` lists the values the finished report
must reproduce.

| Capability | Status | Evidence |
|---|---|---|
| Gold exports from the validated snapshot, with checksums, cardinality checks and CSV/Parquet round trip | verified-local | `evidence/powerbi-export.json` |
| Headline measures computed by SQL for reconciliation | verified-local | `powerbi/reconciliation.json` |
| DAX measures (`measures.dax`) | prepared, never run in Power BI | none |
| Native report and its reconciliation against SQL | unfinished | none |
| Power BI service, Fabric, gateway, incremental refresh | planned only (`docs/azure-migration.md`) | none |

## Source of the tables

`scripts/export_powerbi.py` resolves `data/serving/full.json`, which points to snapshot
`20261116T192553-472ac294`. It requires that snapshot's manifest to say `validation: passed`. It then
re-verifies the SHA-256 of every Parquet file that the manifest lists, and exports the tables through
DuckDB `read_parquet` over that explicit file list. It never reads Delta logs or globs Delta table
directories. Mode `historical`, profile `full`.

| Table (file) | Rows | Grain / key | Role |
|---|---:|---|---|
| `fact_invoice_line` | 1,061,664 | one accepted source row, `line_id` | fact |
| `dim_date` | 739 | one calendar date, `date` | date dimension (mark as date table) |
| `dim_product` | 5,118 | one `stock_code` | dimension |
| `dim_customer` | 5,877 | one `customer_id`, including reserved `__UNKNOWN__` | dimension |
| `dim_geography` | 43 | one invoice `country` label | dimension |
| `daily_sales` | 604 | one observed source date | reconciliation aggregate |
| `quality_daily` | 604 | one source date plus one null-date bucket | data-trust page |
| `cohort_retention` | 325 | `(cohort_month, activity_month)` | standalone analysis table |
| `customer_summary` | 5,876 | one known `customer_id` | standalone analysis table |

The export drops some fact columns: those that are constant for accepted rows (`disposition`, and
`parsed_amount`, which equals `line_amount`) and provenance or descriptive fields (`description`,
`source_sha256`, `ingested_at`, `conversion_version`). They remain in the snapshot. `description`
comes from `dim_product`, which picks one representative description per code, so descriptions cannot
multiply fact rows.

## Relationships (star schema)

Create exactly these relationships. Delete any that Power BI auto-detects.

| From (many side) | To (one side) | Cardinality | Cross-filter direction | Active |
|---|---|---|---|---|
| `fact_invoice_line[date]` | `dim_date[date]` | Many-to-one (*:1) | Single (dimension filters fact) | Yes |
| `fact_invoice_line[stock_code]` | `dim_product[stock_code]` | Many-to-one | Single | Yes |
| `fact_invoice_line[customer_key]` | `dim_customer[customer_id]` | Many-to-one | Single | Yes |
| `fact_invoice_line[country]` | `dim_geography[country]` | Many-to-one | Single | Yes |
| `daily_sales[date]` | `dim_date[date]` | Many-to-one (set it explicitly; do not accept 1:1) | Single | Yes |
| `quality_daily[date]` | `dim_date[date]` | Many-to-one | Single | Yes |

`cohort_retention` and `customer_summary` stay disconnected. Both carry their own observation-window
semantics (first observed paid purchase, known customers only). Joining them to the fact would invite
double counting or would treat unknown buyers as a customer. Visuals use their columns directly.

Bidirectional filters are not used. They would let a filter on the fact restrict a dimension, for
example to show only "dates that have sales", and that hides unavailable dates instead of labelling
them.

### The unknown-customer member

About 23% of accepted lines have no customer ID. They stay in every money total. In the fact, an empty
`customer_id` maps to `customer_key = coalesce(customer_id, '__UNKNOWN__')`. `dim_customer` contains
exactly one row `customer_id = '__UNKNOWN__'` with `is_known_customer = false`. The export checks
verified this, and also verified that no fact row lacks a dimension match. The relationship therefore
uses `customer_key`, never the nullable `customer_id`. Without this, the blank member that Power BI adds
for unmatched keys would hide £2,664,470.67 of anonymous net sales under "(Blank)". The unknown member
is a reporting bucket, not one real customer. Exclude it from any customer count, retention or RFM
visual: filter `dim_customer[is_known_customer] = TRUE`.

### Cardinality checks (executed by the export script)

For each dimension the export recorded 0 duplicate keys, 0 null keys and 0 unmatched fact rows. A join
through all four dimensions keeps 1,061,664 rows and £19,379,245.82. See `cardinality`, `star_join`
and `checks` in `evidence/powerbi-export.json`.

## Date table

- Mark `dim_date` as the date table on column `dim_date[date]` (data type Date).
- The table is contiguous from 2009-12-01 to 2011-12-09, with 739 dates and no gaps (`dim_date_contiguous`
  check). It does not cover full calendar years. Time-intelligence results for 2009 and 2011 are
  therefore partial periods, and comparisons outside the range return BLANK.
- 135 calendar dates have no source rows (`observed_source_day = false`,
  `source_observation_status = 'unavailable_no_source_records'`). Show them as unavailable, not as zero.
  `[Observed Source Days]` and `[Comparable Period Observed Days Match]` make period comparisons honest.
- 2011-12-09 is flagged `is_final_partial_source_day`, because the export ends during that day.
- Sort `month` by itself. To get month names, add a calculated column
  `Month Name = FORMAT(dim_date[date], "mmm")` sorted by `month`.

## Types

Money must stay exact. Set `line_amount` and `unit_price` to **Fixed decimal number**, the Currency type
(four decimal places, stored as a scaled integer). Do not use Decimal number, which is binary floating
point and can drift in the last cent over a million rows. `event_time` is a civil UK timestamp without a
timezone. Load it as Date/Time, never Date/Time/Timezone, and do not shift it. Load keys as Text. Some
stock codes look numeric, but codes such as `84559B` are not, and type detection would turn those into
errors.

## Metric definitions

The formulas, grain, filters, denominators and limitations are in `docs/metrics.md`. The DAX in
`measures.dax` implements the subset used by the report:

| Measure | Definition | Unfiltered expected |
|---|---|---:|
| Gross Positive Merchandise | Σ line_amount where line_amount > 0 | £20,112,074.02 |
| Negative Adjustments | Σ line_amount where line_amount < 0 (signed) | −£732,828.20 |
| Net Recorded Merchandise | Σ line_amount | £19,379,245.82 |
| Positive Units | Σ quantity where quantity > 0 | 11,648,090 |
| Known-Customer Coverage % | lines with customer_key ≠ `__UNKNOWN__` / accepted lines | 77.2982% |
| Adjustment Ratio % | −Negative Adjustments / Gross Positive Merchandise | 3.6437% |
| Net SPLY, Net YoY | SAMEPERIODLASTYEAR on the marked date table | Nov 2011 £1,432,651.11 vs Nov 2010 £1,400,610.30 |
| Net Last/Prior 28 Days | DATESINPERIOD anchored on the last fact date in context | £1,317,989.21 / £1,124,863.37 |

Visuals must label the data as a historical portfolio case study (UCI Online Retail II, CC BY 4.0). They
must also say that the values are recorded invoice values, not audited revenue.
