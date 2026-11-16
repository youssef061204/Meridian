-- Grain: known customer identifier, including known adjustment-only customers.
-- R/F/M are descriptive observed-window features; no lifetime-value claim.
WITH bounds AS (
 SELECT min(date) AS observation_window_start, max(date) AS observation_window_end
 FROM gold_dim_date
), customer_totals AS (
 SELECT customer_id, min(date) AS first_observed_date, max(date) AS last_observed_date,
        min(CASE WHEN quantity > 0 AND line_amount > 0 THEN date END) AS first_positive_purchase_date,
        max(CASE WHEN quantity > 0 AND line_amount > 0 THEN date END) AS last_positive_purchase_date,
        count(*) AS accepted_lines,
        count(DISTINCT CASE WHEN quantity > 0 AND line_amount > 0 THEN invoice END) AS rfm_frequency_invoices,
        count(DISTINCT CASE WHEN quantity > 0 AND line_amount > 0 THEN date END) AS positive_purchase_days,
        cast(sum(CASE WHEN line_amount > 0 THEN line_amount ELSE 0 END) AS DECIMAL(22,2)) AS rfm_monetary_gross_positive_gbp,
        cast(sum(CASE WHEN line_amount < 0 THEN line_amount ELSE 0 END) AS DECIMAL(22,2)) AS negative_adjustments_gbp,
        cast(sum(line_amount) AS DECIMAL(22,2)) AS net_recorded_gbp,
        sum(CASE WHEN quantity > 0 THEN quantity ELSE 0 END) AS positive_units
 FROM gold_fact_source WHERE customer_id IS NOT NULL GROUP BY customer_id
)
SELECT t.*, datediff(b.observation_window_end, t.last_positive_purchase_date) AS rfm_recency_days,
       t.rfm_frequency_invoices >= 2 AS is_observed_repeat_purchaser,
       b.observation_window_start, b.observation_window_end,
       'known_customers_observed_window_only' AS analysis_scope
FROM customer_totals t CROSS JOIN bounds b
