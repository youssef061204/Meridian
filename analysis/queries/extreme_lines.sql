-- Preserve source coordinates for investigation; extreme values are not auto-deleted.
SELECT invoice,stock_code,description,quantity,unit_price,line_amount,date,country,
       customer_id,source_sheet,source_row,line_id
FROM fact_invoice_line ORDER BY abs(line_amount) DESC,line_id LIMIT 20;
