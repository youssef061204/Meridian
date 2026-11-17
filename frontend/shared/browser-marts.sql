-- Rebuilds the accepted-line columns in the browser. Derived columns use the same decimal
-- expression as sql/silver.sql; meridian.insights asserts this equals the accepted lines exactly.
CREATE TABLE sales AS SELECT invoice, stock_code, quantity, unit_price,
  CAST(round(CAST(quantity AS DECIMAL(18,4)) * CAST(unit_price AS DECIMAL(18,4)), 2) AS DOUBLE) AS line_amount,
  event_time, CAST(event_time AS DATE) AS date, customer_id, country,
  CASE WHEN quantity < 0 THEN 'negative_adjustment' ELSE 'positive_merchandise' END AS classification
FROM read_csv('sales.csv.gz', header = true, nullstr = '', columns = {'invoice': 'VARCHAR', 'stock_code': 'VARCHAR', 'quantity': 'INTEGER', 'unit_price': 'DECIMAL(18,4)', 'event_time': 'TIMESTAMP', 'customer_id': 'VARCHAR', 'country': 'VARCHAR'});
CREATE TABLE products AS SELECT * FROM read_csv('products.csv.gz', header = true, nullstr = '', columns = {'stock_code': 'VARCHAR', 'description': 'VARCHAR', 'net_gbp': 'DOUBLE', 'units': 'BIGINT'});
CREATE TABLE customers AS SELECT * FROM read_csv('customers.csv.gz', header = true, nullstr = '', columns = {'customer_id': 'VARCHAR', 'segment': 'VARCHAR', 'recency_days': 'INTEGER', 'frequency': 'INTEGER', 'monetary_gbp': 'DOUBLE', 'country': 'VARCHAR', 'return_probability': 'DOUBLE'});
