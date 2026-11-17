-- MERIDIAN on Azure Synapse serverless SQL. Status: PREPARED, NOT EXECUTED (no Azure subscription used).
-- Run against the workspace's serverless endpoint (output `serverlessSqlEndpoint` of infra/azure/main.bicep)
-- after scripts/azure_publish.sh has uploaded data/marts/*.parquet to the `gold` container.
-- Replace <storage-account> below. Serverless SQL bills per data processed; these files total ~25 MB.

CREATE DATABASE meridian COLLATE Latin1_General_100_BIN2_UTF8;  -- UTF-8 collation for Parquet strings
GO
USE meridian;
GO
CREATE MASTER KEY ENCRYPTION BY PASSWORD = '<a strong password>';
GO
-- The workspace managed identity was granted Storage Blob Data Reader by the Bicep template: no keys, no SAS.
CREATE DATABASE SCOPED CREDENTIAL workspace_identity WITH IDENTITY = 'Managed Identity';
GO
CREATE EXTERNAL DATA SOURCE gold WITH (LOCATION = 'https://<storage-account>.dfs.core.windows.net/gold', CREDENTIAL = workspace_identity);
GO

CREATE VIEW dbo.fact_invoice_line AS
SELECT * FROM OPENROWSET(BULK 'fact_invoice_line.parquet', DATA_SOURCE = 'gold', FORMAT = 'PARQUET') AS rows;
GO
CREATE VIEW dbo.products AS
SELECT * FROM OPENROWSET(BULK 'products.parquet', DATA_SOURCE = 'gold', FORMAT = 'PARQUET') AS rows;
GO
CREATE VIEW dbo.customer_segments AS
SELECT * FROM OPENROWSET(BULK 'customer_segments.parquet', DATA_SOURCE = 'gold', FORMAT = 'PARQUET') AS rows;
GO

-- Same grain and rules as sql/daily_sales.sql.
CREATE VIEW dbo.daily_sales AS
SELECT [date],
       CAST(SUM(CASE WHEN line_amount > 0 THEN line_amount ELSE 0 END) AS DECIMAL(22,2)) AS gross_positive_gbp,
       CAST(SUM(CASE WHEN line_amount < 0 THEN line_amount ELSE 0 END) AS DECIMAL(22,2)) AS negative_adjustments_gbp,
       CAST(SUM(line_amount) AS DECIMAL(22,2)) AS net_recorded_gbp,
       SUM(CASE WHEN quantity > 0 THEN quantity ELSE 0 END) AS positive_units,
       COUNT_BIG(*) AS [rows]
FROM dbo.fact_invoice_line GROUP BY [date];
GO
CREATE VIEW dbo.segment_value AS
SELECT segment, COUNT_BIG(*) AS customers, CAST(SUM(monetary_gbp) AS DECIMAL(22,2)) AS value_gbp,
       AVG(return_probability) AS mean_return_probability
FROM dbo.customer_segments GROUP BY segment;
GO

-- Reconciliation gate: must return net 19379245.82 over 1061664 accepted lines, or the upload is wrong.
SELECT COUNT_BIG(*) AS accepted_rows, CAST(SUM(line_amount) AS DECIMAL(22,2)) AS net_recorded_gbp FROM dbo.fact_invoice_line;
