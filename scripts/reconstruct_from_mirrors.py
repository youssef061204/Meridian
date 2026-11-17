"""Fallback source path when archive.ics.uci.edu is unreachable.

Online Retail II has two sheets. Each one is published separately elsewhere:
  * "Year 2009-2010": a CSV export of the sheet (PacktWorkshops/The-Data-Analysis-Workshop).
  * "Year 2010-2011": the older UCI "Online Retail" dataset (id 352), the same rows as the second sheet.

The rebuilt file is NOT the pinned workbook, so it is never written to data/converted/ and line
identities use a distinct source marker. It is accepted only if the project's own business SQL
reproduces the pinned accepted lines and every GBP total exactly (see `validate`). Results go to evidence/.

Usage: python scripts/reconstruct_from_mirrors.py <sheet1.csv> <sheet2.xlsx> <out.parquet>
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from meridian.pipeline import metrics_query  # noqa: E402
from meridian.source import FIELDS, SCHEMA, text_value  # noqa: E402

MARKER = "mirror-reconstruction-v1"
PINNED = {"source_rows": 1067371, "accepted_rows": 1061664, "excluded_rows": 5701, "quarantined_rows": 6,
          "gross_positive_gbp": "20112074.02", "negative_adjustments_gbp": "-732828.20", "net_recorded_gbp": "19379245.82"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def record(values, sheet, row):
    rec = dict(zip(FIELDS, values))
    rec.update(source_sha256=MARKER, source_sheet=sheet, source_row=row, ingested_at=datetime.now().isoformat(), conversion_version=MARKER)
    return rec


def csv_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for n, row in enumerate(reader, start=2):
            row = [v if v != "" else None for v in row]
            if row[4]:  # dd/mm/yyyy HH:MM -> ISO, same text form the xlsx converter emits
                row[4] = datetime.strptime(row[4], "%d/%m/%Y %H:%M").isoformat(sep=" ")
            if row[6] and row[6].endswith(".0"):
                row[6] = row[6][:-2]
            yield record(row, "Year 2009-2010", n)


def xlsx_rows(path: Path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = wb.worksheets[0].iter_rows(values_only=True)
    next(rows)
    for n, values in enumerate(rows, start=2):
        yield record(list(map(text_value, values)), "Year 2010-2011", n)
    wb.close()


def write(out: Path, *sources):
    with pq.ParquetWriter(out, SCHEMA, compression="zstd") as writer:
        chunk = []
        for source in sources:
            for rec in source:
                chunk.append(rec)
                if len(chunk) == 20000:
                    writer.write_table(pa.Table.from_pylist(chunk, schema=SCHEMA)); chunk.clear()
        if chunk:
            writer.write_table(pa.Table.from_pylist(chunk, schema=SCHEMA))


def validate(parquet: Path) -> dict:
    con = duckdb.connect()
    con.execute(f"CREATE TABLE source_lines AS SELECT *, sha256(concat_ws('|',source_sha256,source_sheet,cast(source_row AS VARCHAR))) AS line_id FROM read_parquet('{parquet.as_posix()}')")
    con.execute("CREATE TABLE silver AS " + (ROOT / "sql" / "silver.sql").read_text().replace("TIMESTAMP_NTZ", "TIMESTAMP"))
    con.execute("CREATE VIEW fact_invoice_line AS SELECT * FROM silver WHERE disposition='accepted'")
    cur = con.execute(metrics_query())
    metrics = {d[0]: str(v) for d, v in zip(cur.description, cur.fetchone())}
    counts = dict(con.execute("SELECT disposition, count(*) FROM silver GROUP BY 1").fetchall())
    per_sheet = dict(con.execute("SELECT source_sheet, count(*) FROM source_lines GROUP BY 1").fetchall())
    observed = {"source_rows": sum(per_sheet.values()), "accepted_rows": counts.get("accepted", 0), "excluded_rows": counts.get("excluded", 0),
                "quarantined_rows": counts.get("quarantined", 0), **{k: metrics[k] for k in ("gross_positive_gbp", "negative_adjustments_gbp", "net_recorded_gbp")}}
    diffs = {k: {"pinned": v, "observed": observed[k]} for k, v in PINNED.items() if str(observed[k]) != str(v)}
    accepted_keys = ("accepted_rows", "quarantined_rows", "gross_positive_gbp", "negative_adjustments_gbp", "net_recorded_gbp")
    excluded_extra = con.execute("SELECT stock_code, count(*) FROM silver WHERE disposition='excluded' GROUP BY 1 ORDER BY 1").fetchall()
    return {"rows_per_sheet": per_sheet, "observed": observed, "pinned": PINNED, "differences": diffs,
            "accepted_lines_exact": not any(k in diffs for k in accepted_keys),
            "excluded_by_code": dict(excluded_extra),
            "note": "Gate: accepted rows, quarantine and every GBP total must match the pinned Spark run exactly. Row-count differences confined to excluded non-merchandise codes are recorded, not hidden."}


def main(csv_path, xlsx_path, out):
    csv_path, xlsx_path, out = Path(csv_path), Path(xlsx_path), Path(out)
    write(out, csv_rows(csv_path), xlsx_rows(xlsx_path))
    result = {"method": __doc__.strip().splitlines()[0], "marker": MARKER,
              "inputs": {"sheet_2009_2010_csv_sha256": digest(csv_path), "sheet_2010_2011_xlsx_sha256": digest(xlsx_path)},
              "output_parquet_sha256": digest(out), **validate(out)}
    (ROOT / "evidence" / "mirror-reconstruction.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["accepted_lines_exact"] else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
