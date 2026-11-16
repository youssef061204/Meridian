"""Seeded synthetic scale profile: N relabelled copies of the converted workbook.

These rows are NOT observed transactions. Each copy gets a distinct synthetic source
identity (source_sha256 = synthetic-stress-v1-<seed>, sheet = '<sheet>#copy<k>'), so every
line identity is unique, while values stay realistic. Used only for scale benchmarks.
"""
from __future__ import annotations

import time

import duckdb

from .common import DATA, atomic_json, now, read_json, sha256

VERSION = "synthetic-stress-v1"


def generate(copies=10, seed=20261116):
    source = DATA / "converted" / "historical.parquet"
    if sha256(source) != read_json(DATA / "converted" / "manifest.json")["full_parquet_sha256"]:
        raise ValueError("Converted source checksum mismatch")
    out = DATA / "synthetic" / "stress.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    con = duckdb.connect()
    try:
        con.execute("SET threads=2"); con.execute("SET memory_limit='768MB'")
        con.execute(f"""COPY (
            SELECT invoice, stock_code, description, quantity, event_time, unit_price, customer_id, country,
                   '{VERSION}-{seed}' AS source_sha256, source_sheet || '#copy' || k AS source_sheet,
                   ingested_at, '{VERSION}' AS conversion_version, source_row
            FROM read_parquet('{source.as_posix()}') CROSS JOIN range({copies}) AS r(k)
            ORDER BY k, source_sheet, source_row) TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)""")
        rows = con.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
    finally:
        con.close()
    manifest = {"mode": "synthetic", "version": VERSION, "seed": seed, "copies": copies, "rows": rows, "generated_at": now(),
                "seconds": round(time.perf_counter() - started, 3), "derived_from_sha256": read_json(DATA / "converted" / "manifest.json")["full_parquet_sha256"],
                "parquet_sha256": sha256(out), "note": "Synthetic scale-test rows, never observed transactions and never served as current."}
    atomic_json(DATA / "synthetic" / "manifest.json", manifest)
    return manifest
