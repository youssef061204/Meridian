"""Small source-only diagnostic; the full analysis repeats and records this query."""
from pathlib import Path
import json
import duckdb

root = Path(__file__).resolve().parents[1]
con = duckdb.connect()
con.execute("SET threads=2")
con.execute("SET memory_limit='384MB'")
con.read_parquet(str(root/'data/converted/historical.parquet')).create_view('raw_source')
result = con.execute((root/'analysis/queries/source_overlap.sql').read_text()).fetchdf()
print(result.to_json(orient='records'))
con.close()
