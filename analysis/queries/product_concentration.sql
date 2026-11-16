-- Denominator: all accepted positive merchandise GBP; product code is the key.
WITH products AS (
 SELECT stock_code, max(description) AS description,
        sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END) AS gross_positive_gbp,
        sum(line_amount) AS net_recorded_gbp, count(*) AS accepted_rows
 FROM fact_invoice_line GROUP BY stock_code
)
SELECT *, gross_positive_gbp/nullif(sum(gross_positive_gbp) OVER (),0) AS gross_share,
       sum(gross_positive_gbp) OVER (ORDER BY gross_positive_gbp DESC,stock_code ROWS UNBOUNDED PRECEDING)
       /nullif(sum(gross_positive_gbp) OVER (),0) AS cumulative_gross_share,
       row_number() OVER (ORDER BY gross_positive_gbp DESC,stock_code) AS rank
FROM products ORDER BY rank;
