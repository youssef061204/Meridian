-- Eligible: identified buyers with positive paid merchandise, no unknown-ID customer.
-- Distinct invoice IDs avoid treating repeated lines or cross-sheet overlap as new visits.
WITH purchases AS (
 SELECT customer_id,count(DISTINCT invoice) AS invoices,min(date) AS first_observed_purchase,
        max(date) AS last_observed_purchase,sum(line_amount) AS positive_paid_gbp
 FROM fact_invoice_line WHERE customer_id IS NOT NULL AND quantity>0 AND line_amount>0
 GROUP BY customer_id
)
SELECT CASE WHEN invoices=1 THEN '1 invoice' WHEN invoices=2 THEN '2 invoices'
            WHEN invoices BETWEEN 3 AND 5 THEN '3-5 invoices' ELSE '6+ invoices' END AS purchase_band,
       count(*) AS known_customers,sum(invoices) AS distinct_customer_invoices,
       sum(positive_paid_gbp) AS positive_paid_gbp
FROM purchases GROUP BY purchase_band ORDER BY purchase_band;
