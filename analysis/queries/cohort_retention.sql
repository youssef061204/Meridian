-- First OBSERVED positive PAID purchase month, not acquisition; known customers only.
-- Ages0-6, bounded through November2011. No rows are fabricated as customer identities.
WITH purchases AS (
 SELECT DISTINCT customer_id,cast(date_trunc('month',date) AS DATE) AS activity_month
 FROM fact_invoice_line WHERE customer_id IS NOT NULL AND quantity>0 AND line_amount>0
), first_seen AS (
 SELECT customer_id,min(activity_month) AS cohort_month FROM purchases GROUP BY customer_id
), cohort_sizes AS (
 SELECT cohort_month,count(*) AS cohort_size FROM first_seen
 WHERE cohort_month<=DATE '2011-11-01' GROUP BY cohort_month
), grid AS (
 SELECT c.*,cast(g.age AS INTEGER) AS month_index,
        cast(c.cohort_month+g.age*INTERVAL '1 month' AS DATE) AS activity_month
 FROM cohort_sizes c CROSS JOIN generate_series(0,6) g(age)
), active AS (
 SELECT f.cohort_month,p.activity_month,count(*) AS active_customers
 FROM purchases p JOIN first_seen f USING(customer_id) GROUP BY f.cohort_month,p.activity_month
)
SELECT g.*,CASE WHEN g.activity_month<=DATE '2011-11-01' THEN coalesce(a.active_customers,0) END AS active_customers,
       CASE WHEN g.activity_month<=DATE '2011-11-01' THEN coalesce(a.active_customers,0)*1.0/g.cohort_size END AS retention_rate,
       g.activity_month<=DATE '2011-11-01' AS within_calendar_bounds
FROM grid g LEFT JOIN active a USING(cohort_month,activity_month)
ORDER BY cohort_month,month_index;
