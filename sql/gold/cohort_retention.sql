-- Grain: first-observed paid-purchase cohort month x observed activity month.
-- This is observed-window retention, not true acquisition or lifetime value.
WITH purchases AS (
 SELECT DISTINCT customer_id, cast(date_trunc('MONTH', date) AS DATE) AS purchase_month
 FROM gold_fact_source
 WHERE customer_id IS NOT NULL AND quantity > 0 AND line_amount > 0
), first_purchase AS (
 SELECT customer_id, min(purchase_month) AS cohort_month
 FROM purchases GROUP BY customer_id
), cohorts AS (
 SELECT cohort_month, count(*) AS cohort_size FROM first_purchase GROUP BY cohort_month
), monthly_activity AS (
 SELECT f.cohort_month, p.purchase_month AS activity_month,
        count(DISTINCT p.customer_id) AS active_customers
 FROM purchases p JOIN first_purchase f ON p.customer_id = f.customer_id
 GROUP BY f.cohort_month, p.purchase_month
), bounds AS (
 SELECT min(date) AS first_source_date, max(date) AS last_source_date FROM gold_dim_date
), observed_months AS (
 SELECT month_start AS activity_month,
        sum(CASE WHEN observed_source_day THEN 1 ELSE 0 END) AS observed_source_days
 FROM gold_dim_date GROUP BY month_start
), grid AS (
 SELECT c.cohort_month, m.activity_month, c.cohort_size, m.observed_source_days,
        dayofmonth(last_day(m.activity_month)) AS calendar_days,
        m.activity_month >= b.first_source_date AND last_day(m.activity_month) < b.last_source_date
          AS is_period_within_source_bounds,
        m.activity_month = cast(date_trunc('MONTH', b.last_source_date) AS DATE) AS is_final_partial_month
 FROM cohorts c CROSS JOIN bounds b
 JOIN observed_months m ON m.activity_month >= c.cohort_month
)
SELECT g.cohort_month, g.activity_month,
       cast(months_between(g.activity_month, g.cohort_month) AS INT) AS month_index,
       g.cohort_size,
       CASE WHEN g.observed_source_days > 0 THEN coalesce(a.active_customers, cast(0 AS BIGINT))
            ELSE cast(NULL AS BIGINT) END AS active_customers,
       CASE WHEN g.observed_source_days > 0 THEN
         cast(coalesce(a.active_customers, 0) AS DOUBLE) / g.cohort_size
         ELSE cast(NULL AS DOUBLE) END AS retention_rate,
       g.observed_source_days, g.calendar_days, g.is_period_within_source_bounds,
       g.observed_source_days < g.calendar_days AS has_unknown_source_days,
       g.is_period_within_source_bounds AND g.observed_source_days = g.calendar_days AS is_period_complete,
       g.is_final_partial_month
FROM grid g LEFT JOIN monthly_activity a
 ON g.cohort_month = a.cohort_month AND g.activity_month = a.activity_month
