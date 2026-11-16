-- Incremental update of the replay daily_sales table for affected business dates only.
-- Inputs: temp view affected_dates(date), temp view fact_current (accepted current revisions).
-- Grain: one observed business date. A date whose accepted rows all disappear is deleted,
-- never written as a zero-sales day. Re-running for the same dates is idempotent because the
-- source rows are recomputed from the current Silver state, not added as deltas.
MERGE INTO delta.`{daily_path}` AS target
USING (
  WITH affected AS (SELECT DISTINCT date FROM affected_dates),
  recomputed AS (
    SELECT a.date,
      cast(coalesce(sum(CASE WHEN f.line_amount > 0 THEN f.line_amount ELSE 0 END), 0) AS DECIMAL(22,2)) AS gross_positive_gbp,
      cast(coalesce(sum(CASE WHEN f.line_amount < 0 THEN f.line_amount ELSE 0 END), 0) AS DECIMAL(22,2)) AS negative_adjustments_gbp,
      cast(coalesce(sum(f.line_amount), 0) AS DECIMAL(22,2)) AS net_recorded_gbp,
      coalesce(sum(CASE WHEN f.quantity > 0 THEN f.quantity ELSE 0 END), 0) AS positive_units,
      count(f.line_id) AS rows,
      coalesce(sum(CASE WHEN f.customer_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS known_customer_rows
    FROM affected a LEFT JOIN fact_current f ON f.date = a.date
    GROUP BY a.date
  )
  SELECT * FROM recomputed
) AS source
ON target.date = source.date
WHEN MATCHED AND source.rows = 0 THEN DELETE
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED AND source.rows > 0 THEN INSERT *
