-- Grain: one immutable source workbook/sheet/row. Deliveries deduplicate upstream.
WITH parsed AS (
 SELECT *,
   trim(invoice) AS invoice_clean,
   upper(trim(stock_code)) AS stock_code_clean,
   try_cast(quantity AS DECIMAL(18,4)) AS quantity_decimal,
   try_cast(unit_price AS DECIMAL(18,4)) AS price_decimal,
   try_cast(event_time AS TIMESTAMP) AS timestamp_parsed,
   cast(round(try_cast(quantity AS DECIMAL(18,4)) * try_cast(unit_price AS DECIMAL(18,4)), 2) AS DECIMAL(20,2)) AS parsed_amount
 FROM source_lines
), classified AS (
 SELECT *, CASE
   WHEN invoice_clean IS NULL OR invoice_clean = '' THEN 'missing_invoice'
   WHEN stock_code_clean IS NULL OR stock_code_clean = '' THEN 'missing_stock_code'
   WHEN timestamp_parsed IS NULL THEN 'invalid_timestamp'
   WHEN quantity_decimal IS NULL OR quantity_decimal != floor(quantity_decimal) OR quantity_decimal = 0
     OR try_cast(quantity AS DECIMAL(28,10)) != quantity_decimal THEN 'invalid_quantity'
   WHEN price_decimal IS NULL OR price_decimal < 0 THEN 'invalid_price'
   WHEN try_cast(unit_price AS DECIMAL(28,10)) != price_decimal THEN 'price_precision_exceeded'
   WHEN parsed_amount IS NULL THEN 'amount_overflow'
   WHEN upper(invoice_clean) LIKE 'C%' AND quantity_decimal > 0 THEN 'cancellation_sign_conflict'
   WHEN stock_code_clean IN ('POST','DOT','M','M2','C2','D','B','ADJUST','ADJUST2','BANK CHARGES','AMAZONFEE','CRUK','TEST001','TEST002') THEN 'non_merchandise_code'
   WHEN quantity_decimal < 0 THEN 'negative_adjustment'
   ELSE 'positive_merchandise'
 END AS classification
 FROM parsed
)
SELECT line_id, invoice_clean AS invoice, stock_code_clean AS stock_code, description,
 cast(quantity_decimal AS BIGINT) AS quantity, price_decimal AS unit_price,
 parsed_amount AS line_amount, timestamp_parsed AS event_time,
 cast(timestamp_parsed AS DATE) AS date,
 nullif(trim(customer_id), '') AS customer_id,
 coalesce(nullif(trim(country), ''), 'Unknown') AS country,
 source_sha256, source_sheet, source_row, ingested_at, conversion_version,
 classification,
 CASE WHEN classification IN ('positive_merchandise','negative_adjustment') THEN 'accepted'
      WHEN classification = 'non_merchandise_code' THEN 'excluded'
      ELSE 'quarantined' END AS disposition,
 parsed_amount
FROM classified
