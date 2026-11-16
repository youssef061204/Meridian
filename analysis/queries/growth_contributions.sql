-- Arithmetic decomposition Jan-Nov2011 minus Jan-Nov2010, not causal attribution.
WITH segments AS (
 SELECT 'country' AS segment_type,country AS segment,year(date) AS year,sum(line_amount) AS net_gbp
 FROM fact_invoice_line WHERE year(date) IN (2010,2011) AND month(date)<=11 GROUP BY country,year(date)
 UNION ALL
 SELECT 'product',stock_code,year(date),sum(line_amount)
 FROM fact_invoice_line WHERE year(date) IN (2010,2011) AND month(date)<=11 GROUP BY stock_code,year(date)
), pivoted AS (
 SELECT segment_type,segment,sum(CASE WHEN year=2010 THEN net_gbp ELSE 0 END) AS net_2010_gbp,
        sum(CASE WHEN year=2011 THEN net_gbp ELSE 0 END) AS net_2011_gbp
 FROM segments GROUP BY segment_type,segment
)
SELECT *,net_2011_gbp-net_2010_gbp AS change_gbp,
       row_number() OVER (PARTITION BY segment_type ORDER BY abs(net_2011_gbp-net_2010_gbp) DESC,segment) AS magnitude_rank
FROM pivoted ORDER BY segment_type,magnitude_rank;
