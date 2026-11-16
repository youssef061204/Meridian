"""Compare an executed synthetic Spark/Delta fixture publication to the oracle.

Usage: python tests/check_spark_fixture.py
Optional: python tests/check_spark_fixture.py /path/to/snapshot/manifest.json

This reads only exported versioned serving files and manifest reconciliation.
The manifest must originate from an actual pipeline run; the checker itself
does not execute Spark and must not be described as such.
"""

from decimal import Decimal
import json
from pathlib import Path
import sys

import duckdb

from test_business_oracle import EXPECTED


def check(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["mode"] == "synthetic", manifest["mode"]
    assert manifest["profile"] == "fixture", manifest["profile"]
    assert manifest["validation"] == "passed"
    for key in ("source_rows", "accepted_rows", "quarantined_rows", "excluded_rows", "repeat_deliveries", "received_deliveries"):
        assert manifest[key] == EXPECTED[key], (key, manifest[key], EXPECTED[key])
    for key in ("gross_positive_gbp", "negative_adjustments_gbp", "net_recorded_gbp", "positive_units", "known_customer_rows"):
        actual = Decimal(str(manifest["metrics"][key]))
        assert actual == EXPECTED[key], (key, actual, EXPECTED[key])
    reconciliation = manifest["reconciliation"]
    amounts = {
        disposition: sum(
            (Decimal(str(row["parsed_amount_gbp"])) for row in reconciliation
             if row["disposition"] == disposition and row["parsed_amount_gbp"] is not None),
            Decimal(0),
        )
        for disposition in ("accepted", "excluded", "quarantined")
    }
    assert amounts["accepted"] == EXPECTED["net_recorded_gbp"]
    assert amounts["excluded"] == EXPECTED["excluded_parseable_gbp"]
    assert amounts["quarantined"] == EXPECTED["quarantined_parseable_gbp"]
    assert sum(amounts.values()) == EXPECTED["source_parseable_gbp"]
    with duckdb.connect() as con:
        parquet = (manifest_path.parent / "fact_invoice_line" / "*.parquet").as_posix()
        con.read_parquet(parquet).create_view("fact")
        actual_rows = [r[0] for r in con.execute("SELECT source_row FROM fact ORDER BY source_row").fetchall()]
        assert actual_rows == [2, 3, 4, 5, 6, 7, 14, 15], actual_rows
        assert con.execute("SELECT count(*), count(DISTINCT line_id) FROM fact").fetchone() == (8, 8)
        by_country = dict(con.execute("SELECT country,sum(line_amount) FROM fact GROUP BY country").fetchall())
        assert by_country == {"United Kingdom": Decimal("37.50"), "France": Decimal("-2.50")}, by_country
        by_date = {str(day): amount for day, amount in con.execute("SELECT date,sum(line_amount) FROM fact GROUP BY date").fetchall()}
        assert by_date == {"2009-12-01": Decimal("47.50"), "2009-12-02": Decimal("-12.50"), "2009-12-03": Decimal("0.00")}, by_date
    return {"snapshot_id": manifest["id"], "state": "passed", "comparison": "hand-calculated independent Decimal oracle"}


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        root = Path(__file__).resolve().parents[1]
        pointer = json.loads((root / "data" / "serving" / "fixture.json").read_text(encoding="utf-8"))
        target = root / "data" / "serving" / "snapshots" / pointer["snapshot_id"] / "manifest.json"
    print(json.dumps(check(target), indent=2))
