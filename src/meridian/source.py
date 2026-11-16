"""Pinned raw download and row-group bounded XLSX conversion; no dataframe load."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

import openpyxl
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from .common import DATA, ROOT, atomic_json, now, read_json, run_record, sha256

URL = "https://archive.ics.uci.edu/static/public/502/online%2Bretail%2Bii.zip"
VERSION = "xlsx-stream-v1"
FIELDS = ["invoice", "stock_code", "description", "quantity", "event_time", "unit_price", "customer_id", "country"]
HEADERS = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]
SCHEMA = pa.schema([(name, pa.string()) for name in FIELDS + ["source_sha256", "source_sheet", "ingested_at", "conversion_version"]] + [("source_row", pa.int64())])


def download():
    raw = DATA / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    archive = raw / "online-retail-ii.zip"
    workbook = raw / "online_retail_II.xlsx"
    with run_record("download", url=URL) as run:
        if not archive.exists():
            temp = archive.with_suffix(".partial")
            with requests.get(URL, stream=True, timeout=(15, 120)) as response:
                response.raise_for_status()
                with temp.open("wb") as output:
                    for chunk in response.iter_content(1024 * 1024):
                        output.write(chunk)
            temp.replace(archive)
        with ZipFile(archive) as zf:
            names = [n for n in zf.namelist() if n.endswith(".xlsx")]
            if len(names) != 1:
                raise ValueError(f"Expected one XLSX, found {names}")
            if not workbook.exists():
                with zf.open(names[0]) as inp, workbook.open("wb") as out:
                    for block in iter(lambda: inp.read(1024 * 1024), b""):
                        out.write(block)
        measured = {"url": URL, "archive_sha256": sha256(archive), "workbook_sha256": sha256(workbook), "archive_bytes": archive.stat().st_size, "workbook_bytes": workbook.stat().st_size,
                    "license": "CC BY 4.0", "attribution": "Daqing Chen, Online Retail II, UCI Machine Learning Repository, DOI 10.24432/C5CG6D"}
        lock = ROOT / "SOURCE.lock.json"
        if lock.exists():
            previous = read_json(lock)
            for field in ("archive_sha256", "workbook_sha256"):
                if previous[field] != measured[field]:
                    raise ValueError(f"Pinned source checksum mismatch: {field}")
        else:
            atomic_json(lock, measured)
        run.update(measured)
    return workbook


def text_value(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def convert(demo_per_sheet=2000):
    workbook = download()
    pinned = read_json(ROOT / "SOURCE.lock.json")
    output = DATA / "converted"
    output.mkdir(parents=True, exist_ok=True)
    full_path, demo_path = output / "historical.parquet", output / "demo.parquet"
    with run_record("convert", workbook_sha256=pinned["workbook_sha256"], chunk_rows=10000, demo_per_sheet=demo_per_sheet) as run:
        wb = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
        sheets, demo_rows = [], []
        ingested = now()
        with pq.ParquetWriter(full_path.with_suffix(".partial"), SCHEMA, compression="zstd") as writer:
            for sheet in wb:
                rows = sheet.iter_rows(values_only=True)
                headers = list(next(rows))
                if headers != HEADERS:
                    raise ValueError(f"Unexpected columns {sheet.title}: {headers}")
                info = {"sheet": sheet.title, "headers": headers, "rows": 0, "date_min": None, "date_max": None, "missing_customer_rows": 0}
                chunk = []
                for row_number, values in enumerate(rows, start=2):
                    record = dict(zip(FIELDS, map(text_value, values)))
                    record.update(source_sha256=pinned["workbook_sha256"], source_sheet=sheet.title, source_row=row_number, ingested_at=ingested, conversion_version=VERSION)
                    chunk.append(record)
                    info["rows"] += 1
                    info["missing_customer_rows"] += record["customer_id"] is None
                    dt = record["event_time"]
                    if dt:
                        info["date_min"] = min(info["date_min"] or dt, dt)
                        info["date_max"] = max(info["date_max"] or dt, dt)
                    if row_number <= demo_per_sheet + 1:
                        demo_rows.append(record)
                    if len(chunk) == 10000:
                        writer.write_table(pa.Table.from_pylist(chunk, schema=SCHEMA))
                        chunk.clear()
                if chunk:
                    writer.write_table(pa.Table.from_pylist(chunk, schema=SCHEMA))
                sheets.append(info)
                print(f"Converted {sheet.title}: {info['rows']} source rows", flush=True)
        wb.close()
        full_path.with_suffix(".partial").replace(full_path)
        pq.write_table(pa.Table.from_pylist(demo_rows, schema=SCHEMA), demo_path, compression="zstd")
        summary = dict(source=pinned, conversion_version=VERSION, converted_at=ingested, sheets=sheets, total_rows=sum(s["rows"] for s in sheets), demo_rows=len(demo_rows), full_parquet_sha256=sha256(full_path), demo_parquet_sha256=sha256(demo_path), timezone_assumption="Source timestamps are naive UK civil time; original values preserved; no UTC conversion or DST inference.")
        if summary["total_rows"] != 1067371 or len(sheets) != 2:
            raise ValueError(f"UCI documented count/sheets differ: {summary}")
        atomic_json(output / "manifest.json", summary)
        atomic_json(ROOT / "evidence" / "source-verification.json", summary)
        run.update(total_rows=summary["total_rows"], demo_rows=len(demo_rows), sheets=sheets)
    return summary
