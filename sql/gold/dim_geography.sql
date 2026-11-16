-- Grain: one source country label after Silver's explicit Unknown normalization.
-- These are invoice country labels, not verified customer residence.
SELECT country, min(date) AS first_observed_date, max(date) AS last_observed_date
FROM gold_fact_source GROUP BY country
