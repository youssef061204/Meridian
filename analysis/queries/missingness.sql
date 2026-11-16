-- Denominator: original workbook coordinates before business rules, by source sheet.
SELECT source_sheet,count(*) AS original_rows,
       count(*) FILTER (WHERE customer_id IS NULL OR trim(customer_id)='') AS missing_customer_rows,
       count(*) FILTER (WHERE description IS NULL OR trim(description)='') AS missing_description_rows,
       count(*) FILTER (WHERE invoice IS NULL OR trim(invoice)='') AS missing_invoice_rows,
       count(*) FILTER (WHERE country IS NULL OR trim(country)='') AS missing_country_rows,
       count(*) FILTER (WHERE try_cast(quantity AS DECIMAL(18,4)) IS NULL) AS unparseable_quantity_rows,
       count(*) FILTER (WHERE try_cast(unit_price AS DECIMAL(18,4)) IS NULL) AS unparseable_price_rows,
       min(try_cast(event_time AS TIMESTAMP)) AS first_observed_time,
       max(try_cast(event_time AS TIMESTAMP)) AS last_observed_time
FROM raw_source GROUP BY source_sheet ORDER BY source_sheet;
