-- Grain: every calendar date between the first and last parseable source date.
-- A calendar gap is unavailable evidence, not an observed zero-sales day.
WITH source_days AS (
 SELECT date, count(*) AS source_rows,
        min(event_time) AS first_source_event_time,
        max(event_time) AS last_source_event_time
 FROM gold_silver_source WHERE date IS NOT NULL GROUP BY date
), bounds AS (
 SELECT min(date) AS first_source_date, max(date) AS last_source_date FROM source_days
), calendar AS (
 SELECT explode(sequence(first_source_date, last_source_date, INTERVAL 1 DAY)) AS date,
        first_source_date, last_source_date
 FROM bounds
)
SELECT c.date,
       year(c.date) AS year, month(c.date) AS month, dayofmonth(c.date) AS day_of_month,
       cast(date_trunc('MONTH', c.date) AS DATE) AS month_start,
       date_sub(c.date, pmod(dayofweek(c.date) + 5, 7)) AS week_start,
       pmod(dayofweek(c.date) + 5, 7) + 1 AS iso_day_of_week,
       pmod(dayofweek(c.date) + 5, 7) >= 5 AS is_weekend,
       coalesce(s.source_rows, cast(0 AS BIGINT)) AS source_rows,
       s.source_rows IS NOT NULL AS observed_source_day,
       c.date = c.first_source_date AS is_first_source_day,
       c.date = c.last_source_date AS is_final_partial_source_day,
       s.first_source_event_time, s.last_source_event_time,
       CASE WHEN s.source_rows IS NULL THEN 'unavailable_no_source_records'
            WHEN c.date = c.last_source_date THEN 'partial_final_source_day'
            ELSE 'observed_source_records' END AS source_observation_status
FROM calendar c LEFT JOIN source_days s ON c.date = s.date
