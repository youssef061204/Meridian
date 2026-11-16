-- Grain: aggregate series x calendar date. No source day is not a zero-sales day.
WITH units AS (
 SELECT date, sum(CASE WHEN quantity > 0 THEN quantity ELSE 0 END) AS positive_units,
        count(*) AS accepted_merchandise_rows
 FROM gold_fact_source GROUP BY date
), quality AS (
 SELECT date, sum(CASE WHEN disposition = 'quarantined' THEN 1 ELSE 0 END) AS quarantined_rows
 FROM gold_silver_source WHERE date IS NOT NULL GROUP BY date
)
SELECT '__ALL__' AS series_id, d.date,
       CASE WHEN d.observed_source_day THEN coalesce(u.positive_units, cast(0 AS BIGINT))
            ELSE cast(NULL AS BIGINT) END AS positive_units,
       coalesce(u.accepted_merchandise_rows, cast(0 AS BIGINT)) AS accepted_merchandise_rows,
       d.source_rows, coalesce(q.quarantined_rows, cast(0 AS BIGINT)) AS quarantined_rows,
       d.observed_source_day, d.is_final_partial_source_day,
       d.observed_source_day AND NOT d.is_final_partial_source_day
         AND coalesce(q.quarantined_rows, 0) = 0 AS is_observation_available,
       CASE WHEN NOT d.observed_source_day THEN 'unavailable_no_source_records'
            WHEN d.is_final_partial_source_day THEN 'partial_final_source_day'
            WHEN coalesce(q.quarantined_rows, 0) > 0 THEN 'quality_affected_day'
            ELSE 'observed' END AS observation_status,
       d.last_source_event_time, d.iso_day_of_week, d.month, d.is_weekend
FROM gold_dim_date d LEFT JOIN units u ON d.date = u.date
LEFT JOIN quality q ON d.date = q.date
