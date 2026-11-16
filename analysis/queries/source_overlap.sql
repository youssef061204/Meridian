-- Question: do identical payloads occur across the two original workbook sheets?
-- Grain: exact eight-field source payload, preserving nulls and string values.
-- Identity remains workbook checksum + sheet + original row; no rows are removed.
WITH payloads AS (
  SELECT invoice, stock_code, description, quantity, event_time, unit_price,
         customer_id, country,
         count(*) AS copies,
         count(*) FILTER (WHERE source_sheet='Year 2009-2010') AS first_sheet_copies,
         count(*) FILTER (WHERE source_sheet='Year 2010-2011') AS second_sheet_copies
  FROM raw_source
  GROUP BY invoice, stock_code, description, quantity, event_time, unit_price,
           customer_id, country
)
SELECT count(*) AS distinct_payloads,
       sum(copies) AS original_rows,
       sum(copies - 1) AS identical_looking_extra_rows,
       count(*) FILTER (WHERE first_sheet_copies>0 AND second_sheet_copies>0) AS cross_sheet_shared_payloads,
       sum(least(first_sheet_copies, second_sheet_copies)) AS cross_sheet_match_pairs,
       sum(copies) FILTER (WHERE first_sheet_copies>0 AND second_sheet_copies>0) AS rows_in_shared_payload_groups,
       sum(greatest(first_sheet_copies-1,0)+greatest(second_sheet_copies-1,0)) AS within_sheet_identical_looking_extra_rows
FROM payloads;
