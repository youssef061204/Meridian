-- A date without a source row is UNAVAILABLE, not a measured zero-sales day.
SELECT cast(try_cast(event_time AS TIMESTAMP) AS DATE) AS date,
       count(*) AS original_rows,count(DISTINCT source_sheet) AS contributing_sheets
FROM raw_source GROUP BY date ORDER BY date;
