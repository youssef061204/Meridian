-- Denominator is positive merchandise GBP; signed adjustments are not linked refunds.
SELECT cast(date_trunc('month',date) AS DATE) AS month,
       sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END) AS gross_positive_gbp,
       sum(CASE WHEN line_amount<0 THEN line_amount ELSE 0 END) AS negative_adjustments_gbp,
       -sum(CASE WHEN line_amount<0 THEN line_amount ELSE 0 END)
        /nullif(sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END),0) AS adjustment_to_gross_ratio,
       count(*) FILTER (WHERE line_amount<0) AS negative_amount_rows,
       count(*) FILTER (WHERE quantity<0 AND upper(invoice) NOT LIKE 'C%') AS negative_quantity_without_c_prefix
FROM fact_invoice_line GROUP BY month ORDER BY month;
