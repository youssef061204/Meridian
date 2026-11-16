-- Grain: source date, including one NULL bucket for unparseable timestamps.
-- Source rows and amounts are logical records, not delivery counts.
SELECT date,
       count(*) AS source_rows,
       sum(CASE WHEN disposition = 'accepted' THEN 1 ELSE 0 END) AS accepted_rows,
       sum(CASE WHEN disposition = 'quarantined' THEN 1 ELSE 0 END) AS quarantined_rows,
       sum(CASE WHEN disposition = 'excluded' THEN 1 ELSE 0 END) AS excluded_rows,
       sum(CASE WHEN customer_id IS NULL THEN 1 ELSE 0 END) AS missing_customer_source_rows,
       sum(CASE WHEN disposition = 'accepted' AND customer_id IS NULL THEN 1 ELSE 0 END) AS missing_customer_accepted_rows,
       sum(CASE WHEN parsed_amount IS NULL THEN 1 ELSE 0 END) AS unparseable_amount_rows,
       cast(sum(parsed_amount) AS DECIMAL(22,2)) AS source_parseable_gbp,
       cast(sum(CASE WHEN disposition = 'accepted' THEN parsed_amount ELSE 0 END) AS DECIMAL(22,2)) AS accepted_parseable_gbp,
       cast(sum(CASE WHEN disposition = 'quarantined' THEN parsed_amount ELSE 0 END) AS DECIMAL(22,2)) AS quarantined_parseable_gbp,
       cast(sum(CASE WHEN disposition = 'excluded' THEN parsed_amount ELSE 0 END) AS DECIMAL(22,2)) AS excluded_parseable_gbp,
       date IS NULL AS has_unparseable_date
FROM gold_silver_source GROUP BY date
