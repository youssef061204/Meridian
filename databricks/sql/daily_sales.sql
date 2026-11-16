-- Grain: observed source date, no zero filling of unavailable dates.
SELECT date,
 cast(sum(CASE WHEN line_amount > 0 THEN line_amount ELSE 0 END) AS DECIMAL(22,2)) AS gross_positive_gbp,
 cast(sum(CASE WHEN line_amount < 0 THEN line_amount ELSE 0 END) AS DECIMAL(22,2)) AS negative_adjustments_gbp,
 cast(sum(line_amount) AS DECIMAL(22,2)) AS net_recorded_gbp,
 sum(CASE WHEN quantity > 0 THEN quantity ELSE 0 END) AS positive_units,
 count(*) AS rows,
 sum(CASE WHEN customer_id IS NOT NULL THEN 1 ELSE 0 END) AS known_customer_rows
FROM fact_invoice_line GROUP BY date
