-- Grain: one known source customer identifier, plus one reserved unknown member.
-- Unknown source rows do not establish a single real customer.
SELECT customer_id, true AS is_known_customer,
       min(date) AS first_observed_date, max(date) AS last_observed_date
FROM gold_fact_source WHERE customer_id IS NOT NULL GROUP BY customer_id
UNION ALL
SELECT '__UNKNOWN__' AS customer_id, false AS is_known_customer,
       cast(NULL AS DATE) AS first_observed_date, cast(NULL AS DATE) AS last_observed_date
