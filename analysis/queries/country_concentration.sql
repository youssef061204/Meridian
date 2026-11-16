-- Denominator: all accepted positive merchandise GBP over the complete workbook.
WITH countries AS (
 SELECT country, sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END) AS gross_positive_gbp,
        sum(line_amount) AS net_recorded_gbp, count(*) AS accepted_rows
 FROM fact_invoice_line GROUP BY country
)
SELECT *, gross_positive_gbp/nullif(sum(gross_positive_gbp) OVER (),0) AS gross_share,
       row_number() OVER (ORDER BY gross_positive_gbp DESC,country) AS rank
FROM countries ORDER BY rank;
