# Power BI report setup (native report: unfinished)

These steps have **not** been executed. Power BI Desktop is not installed on the machine that produced
this repository. Follow them on a Windows machine with Power BI Desktop. Power BI Desktop is free and
needs no sign-in to build and save a local `.pbix`. Do not publish to the Power BI service and do not
start a Fabric or Pro trial for this portfolio project. A report is "reconciled" only when every value
in `reconciliation.json` has been matched in Power BI itself, and that comparison is recorded.

## 0. Produce the exports

```powershell
cd C:\Users\USER\Projects\Meridian
.venv\Scripts\python.exe scripts\export_powerbi.py --format both   # or --format csv
```

The script refuses to run unless `data/serving/full.json` points to a snapshot whose manifest is
validated, and every listed Parquet checksum matches. It writes `powerbi/exports/*.csv` (and `*.parquet`
with `--format both`), `powerbi/reconciliation.json`, and `evidence/powerbi-export.json`. The fact CSV is
about 205 MB and the fact Parquet about 60 MB. Both are git-ignored.

Check that `evidence/powerbi-export.json` has `"state": "passed"`. Note its `snapshot_id`, and put it on
the report's title page.

## 1. Desktop options (before loading anything)

1. **File > Options and settings > Options > Current File > Data Load**: clear **Detect column types and
   headers for unstructured sources**. Under **Relationships**, clear **Autodetect new relationships after
   data is loaded**.
2. **Current File > Regional Settings**: set Locale for import to **English (United Kingdom)**. The exports
   use ISO dates (`2010-03-01`) and `.` as the decimal separator.
3. Under **Global > Data Load**, also clear **Detect column types and headers for unstructured
   sources**, so new tables inherit the setting.

## 2. Load the nine tables

### Option A: CSV (Text/CSV connector)

For each file in `powerbi/exports/` (`fact_invoice_line`, `dim_date`, `dim_product`, `dim_customer`,
`dim_geography`, `daily_sales`, `quality_daily`, `cohort_retention`, `customer_summary`):

1. **Home > Get data > Text/CSV**, then select the file and choose **Open**.
2. In the preview, set **File origin** to `65001: Unicode (UTF-8)`, **Delimiter** to Comma, and **Data
   type detection** to **Do not detect data types**. Choose **Transform Data**, not Load.
3. In Power Query Editor, check that the first row became headers. If not, use **Home > Use First Row as
   Headers**. If a `Changed Type` step appears in **Applied Steps**, delete it.
4. Rename the query to the file name without the extension (for example `fact_invoice_line`).
5. Set the types explicitly. Use the column header type icon, then **Using Locale... > English (United
   Kingdom)** if your Windows locale uses a comma decimal separator:

| Table | Column(s) | Type |
|---|---|---|
| fact_invoice_line | line_amount, unit_price | **Fixed decimal number** |
| fact_invoice_line | quantity, source_row | Whole number |
| fact_invoice_line | date | Date |
| fact_invoice_line | event_time | Date/Time (not Date/Time/Timezone) |
| fact_invoice_line | line_id, invoice, stock_code, customer_key, customer_id, country, classification, source_sheet | Text |
| dim_date | date, month_start, week_start | Date |
| dim_date | year, month, day_of_month, iso_day_of_week, source_rows | Whole number |
| dim_date | is_weekend, observed_source_day, is_first_source_day, is_final_partial_source_day | True/False |
| dim_date | first_source_event_time, last_source_event_time | Date/Time |
| dim_product, dim_customer, dim_geography | key columns (stock_code, customer_id, country), description | Text; dates as Date; is_known_customer True/False |
| daily_sales | date | Date; *_gbp as **Fixed decimal number**; counts Whole number |
| quality_daily | date | Date (one row is blank: the invalid-timestamp bucket); *_gbp **Fixed decimal number** |
| cohort_retention | cohort_month, activity_month | Date; retention_rate Decimal number (a ratio, not money) |
| customer_summary | *_gbp | **Fixed decimal number**; dates Date; counts Whole number |

6. For `fact_invoice_line`, select `customer_id` and choose **Transform > Replace Values**, replacing
   an empty value with `null`. An empty CSV field arrives as an empty string. Coverage measures use
   `customer_key`, so they are correct either way.
7. Choose **Home > Close & Apply**. The fact load reads about 205 MB.

### Option B: Parquet (typed; smaller)

The built-in Power Query **Parquet** connector reads files from the local file system, Azure Blob
Storage or ADLS Gen2
([Microsoft Learn: Parquet connector](https://learn.microsoft.com/en-us/power-query/connectors/parquet),
accessed 2026-11-16).

1. **Home > Get data > More... > File > Parquet > Connect**. Enter the full local path, for example
   `C:\Users\USER\Projects\Meridian\powerbi\exports\fact_invoice_line.parquet`, then choose **OK** and
   then **Transform Data**.
2. Confirm the column types. Decimal columns may arrive as Decimal number. Change `line_amount`,
   `unit_price` and every `*_gbp` column to **Fixed decimal number**. Change `event_time` to Date/Time if
   it arrives with a timezone.
3. Repeat for the other eight tables. Rename the queries, then **Close & Apply**.

## 3. Model view

1. Open **Model view**. Delete any relationship that already exists.
2. Create each relationship in `model.md` (*Relationships*) by dragging the many-side column onto the
   one-side column. Then open the relationship (double-click) and set **Cardinality** to
   **Many to one (\*:1)**, set **Cross filter direction** to **Single**, and tick **Make this
   relationship active**. For `daily_sales[date]` to `dim_date[date]`, Power BI will suggest One to one.
   Change it to Many to one.
3. Select `dim_date`, then **Table tools > Mark as date table > Mark as date table**. Choose the
   `date` column, check that validation succeeds, and choose **OK**.
4. Hide the technical columns from report view (right-click, **Hide in report view**): all
   `fact_invoice_line` key columns (`customer_key`, `date`, `stock_code`, `country`) and `line_id`.
   Visuals should use the dimension columns.

## 4. Measures

1. **Home > Enter data**, name the table `Measures`, then **Load**.
2. For each measure in `powerbi/measures.dax`: select `Measures`, choose **Table tools > New measure**,
   paste the measure (name, `=` and expression), and press Enter. Then set **Measure tools > Format**
   as noted in the comments. Money uses `"£"#,0.00;-"£"#,0.00` and percentages use `0.00%`.
3. Delete the placeholder column of the `Measures` table so that it shows as a measure table.

## 5. Reconciliation page (do this before any design work)

Create a page named **Reconciliation** with no filters and no slicers:

1. Add a **Card** visual for each of: Net Recorded Merchandise, Gross Positive Merchandise, Negative
   Adjustments, Positive Units, Accepted Lines, Known-Customer Lines, Known-Customer Coverage % (set
   it to 4 decimals for the check), Adjustment Ratio % (4 decimals), Unknown-Customer Net and
   Revenue Bridge Difference.
2. Add a **Table** visual with `dim_date[year]`, `dim_date[month]`, Net Recorded Merchandise, Net SPLY,
   Observed Source Days and Observed Source Days SPLY. Find the row for 2011 / 11.
3. Add two cards, Net Last 28 Days and Net Prior 28 Days, with no date filter.
4. Compare every value with `powerbi/reconciliation.json` (`visual_checks`, `measures.periods`). Each
   must match to the cent, and Revenue Bridge Difference must show £0.00:

| Check | Expected |
|---|---:|
| Net Recorded Merchandise | £19,379,245.82 |
| Gross Positive Merchandise | £20,112,074.02 |
| Negative Adjustments | −£732,828.20 |
| Positive Units | 11,648,090 |
| Accepted Lines | 1,061,664 |
| Known-Customer Lines | 820,647 |
| Known-Customer Coverage % | 77.2982% |
| Adjustment Ratio % | 3.6437% |
| Unknown-Customer Net | £2,664,470.67 |
| Net, 2011-11 / Net SPLY | £1,432,651.11 / £1,400,610.30 |
| Net Last 28 Days / Net Prior 28 Days | £1,317,989.21 / £1,124,863.37 |
| Top country net (United Kingdom) | £16,587,674.39 |

5. Record the result as evidence: a screenshot of the page plus a small JSON file with the Power BI
   Desktop version, `snapshot_id`, each value observed and pass/fail. Only after that may the native
   report be described as built and reconciled. Save the report as
   `powerbi/MERIDIAN.pbix`, or use **File > Save as > Power BI project (.pbip)** for a diff-friendly
   text format. Consider a git-ignore entry, because a `.pbix` embeds about 1M rows.

## 6. Suggested report pages

- **Overview**: revenue bridge (cards for gross, adjustments and net, or a waterfall with categories
  Gross / Adjustments / Net), net by `dim_date[month_start]`, top products (`dim_product[description]`)
  and countries, and a slicer on `dim_geography[country]`. The subtitle states the historical range,
  2009-12-01 to 2011-12-09 (final day partial).
- **Comparable periods**: Net, Net SPLY, Net YoY % and Comparable Period Observed Days Match, by month.
- **Customers**: Known-Customer Coverage %, plus `cohort_retention` as a matrix (cohort_month ×
  month_index, value retention_rate). Filter `is_final_partial_month = FALSE`, or label the final month
  as immature. `customer_summary` values are descriptive, observed-window RFM only.
- **Data trust**: `quality_daily` (accepted / quarantined / excluded rows and parseable GBP by date,
  including the blank-date bucket), and `dim_date[source_observation_status]` counts.

## 7. Incremental refresh (only if the model is later published)

Not configured and not used locally: a local `.pbix` refreshes fully. If the model is later hosted
(see `docs/azure-migration.md`), define `RangeStart` / `RangeEnd` Date/Time parameters on
`fact_invoice_line[event_time]` against a source that supports query folding, such as a Databricks SQL
warehouse table. Folder-of-CSV sources do not fold. Incremental refresh is supported for Pro, PPU,
Premium and Embedded models
([Microsoft Learn: incremental refresh](https://learn.microsoft.com/en-us/power-bi/connect-data/incremental-refresh-overview),
accessed 2026-11-16).
