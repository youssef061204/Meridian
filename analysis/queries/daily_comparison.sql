-- Observed trading-date sequence only. Missing calendar dates remain unavailable.
SELECT date,year(date) AS year,sum(line_amount) AS net_recorded_gbp,
       sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS positive_units
FROM fact_invoice_line
WHERE date>=DATE '2010-01-01' AND date<DATE '2011-12-01' AND month(date)<=11
GROUP BY date ORDER BY date;
