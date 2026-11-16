-- Exact payload groups present in both original sheets. No deduplication decision.
WITH annotated AS (
 SELECT *,min(source_sheet) OVER w != max(source_sheet) OVER w AS shared_payload
 FROM raw_source
 WINDOW w AS (PARTITION BY invoice,stock_code,description,quantity,event_time,unit_price,customer_id,country)
)
SELECT r.source_sheet,count(*) AS shared_original_rows,
       min(r.event_time) AS first_shared_time,max(r.event_time) AS last_shared_time,
       count(f.line_id) AS shared_accepted_rows,sum(f.line_amount) AS shared_accepted_net_gbp
FROM annotated r LEFT JOIN fact_invoice_line f
 ON r.source_sheet=f.source_sheet AND r.source_row=f.source_row
WHERE shared_payload GROUP BY r.source_sheet ORDER BY r.source_sheet;
