-- Grain: one stock code. Description changes never create another dimension key.
-- Latest nonempty description wins, then source coordinates provide stable ties.
WITH ranked AS (
 SELECT stock_code, nullif(trim(description), '') AS description, date,
        row_number() OVER (
          PARTITION BY stock_code
          ORDER BY CASE WHEN nullif(trim(description), '') IS NOT NULL THEN 0 ELSE 1 END,
                   event_time DESC, source_sheet DESC, source_row DESC, line_id DESC
        ) AS representative_rank
 FROM gold_fact_source
), history AS (
 SELECT stock_code, min(date) AS first_observed_date, max(date) AS last_observed_date,
        count(DISTINCT nullif(trim(description), '')) AS observed_description_variants
 FROM gold_fact_source GROUP BY stock_code
)
SELECT h.stock_code, coalesce(r.description, 'Description unavailable') AS description,
       r.date AS representative_description_date,
       h.first_observed_date, h.last_observed_date, h.observed_description_variants
FROM history h JOIN ranked r ON h.stock_code = r.stock_code AND r.representative_rank = 1
