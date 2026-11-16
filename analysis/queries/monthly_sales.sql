-- Accepted merchandise; compare calendar-bounded Jan-Nov only, December is excluded.
SELECT year(date) AS year, month(date) AS month,
       sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END) AS gross_positive_gbp,
       sum(CASE WHEN line_amount<0 THEN line_amount ELSE 0 END) AS negative_adjustments_gbp,
       sum(line_amount) AS net_recorded_gbp,
       sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS positive_units,
       count(*) AS accepted_rows, count(DISTINCT date) AS dates_with_accepted_records
FROM fact_invoice_line
WHERE date >= DATE '2010-01-01' AND date < DATE '2011-12-01' AND month(date)<=11
GROUP BY year(date),month(date) ORDER BY year,month;
