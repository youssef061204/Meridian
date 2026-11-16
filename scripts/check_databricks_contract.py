"""Run the Databricks contract-validation dataset through MERIDIAN's Silver/daily SQL locally.

This is LOCAL contract evidence produced with DuckDB on the host. It is not a
Databricks run and proves nothing about a Databricks workspace. It verifies that:

* the dataset in databricks/contract/lines.csv produces the hand-calculated
  totals in databricks/contract/expected.json under the repository's own
  sql/silver.sql and sql/daily_sales.sql semantics (TIMESTAMP_NTZ is replaced by
  DuckDB's civil TIMESTAMP exactly as src/meridian/baseline.py does), and
* the SQL copies shipped in databricks/sql/ are byte-identical to sql/, so the
  Databricks notebooks cannot silently drift from the local business rules.

Usage (host):  .venv/Scripts/python.exe scripts/check_databricks_contract.py
Writes:        evidence/databricks-contract-local.json
Exit code 0 only when every check passes.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "databricks" / "contract" / "lines.csv"
EXPECTED = ROOT / "databricks" / "contract" / "expected.json"
EVIDENCE = ROOT / "evidence" / "databricks-contract-local.json"
SQL_FILES = ["silver.sql", "daily_sales.sql"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def git_dirty(paths: list[str]) -> bool | None:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return bool(out.strip())
    except Exception:
        return None


def main() -> int:
    started = time.perf_counter()
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    checks: list[dict] = []

    def check(name: str, actual, wanted) -> None:
        checks.append({"check": name, "expected": wanted, "actual": actual, "passed": actual == wanted})

    # 1. Drift guard: Databricks SQL copies must equal the repository models byte for byte.
    for name in SQL_FILES:
        check(f"databricks/sql/{name} identical to sql/{name}", sha256(ROOT / "databricks" / "sql" / name), sha256(ROOT / "sql" / name))

    con = duckdb.connect()
    try:
        con.execute("SET memory_limit='512MB'")
        con.execute("SET threads=2")
        # Every source field is read as text, mirroring the converted Parquet contract (string columns,
        # integer source_row). Empty CSV fields become NULL; Silver trims/nullifs either way.
        con.execute(
            "CREATE TABLE deliveries AS SELECT * REPLACE (CAST(source_row AS BIGINT) AS source_row), "
            "sha256(concat_ws('|', source_sha256, source_sheet, CAST(source_row AS VARCHAR))) AS line_id "
            f"FROM read_csv('{DATASET.as_posix()}', header=true, all_varchar=true, quote='\"', escape='\"')"
        )
        received, distinct_rows = con.execute("SELECT count(*), count(DISTINCT line_id) FROM deliveries").fetchone()
        check("received deliveries", received, expected["deliveries"]["received_deliveries"])
        check("distinct source rows", distinct_rows, expected["deliveries"]["distinct_source_rows"])
        check("repeated deliveries", received - distinct_rows, expected["deliveries"]["repeated_deliveries"])
        repeated = [r[0] for r in con.execute("SELECT source_row FROM deliveries GROUP BY source_row HAVING count(*) > 1 ORDER BY 1").fetchall()]
        check("repeated coordinates", repeated, expected["deliveries"]["repeated_coordinates"])
        # Repeats must carry identical payloads; a conflict would need an explicit revision, not an arbitrary pick.
        payload = "invoice, stock_code, description, quantity, event_time, unit_price, customer_id, country"
        conflicts = con.execute(f"SELECT count(*) FROM (SELECT line_id FROM (SELECT DISTINCT line_id, {payload} FROM deliveries) GROUP BY line_id HAVING count(*) > 1)").fetchone()[0]
        check("conflicting repeat payloads", conflicts, expected["deliveries"]["conflicting_repeat_payloads"])

        # 2. Deduplicate deliveries by logical identity (first delivery wins; payloads proven identical above).
        con.execute("CREATE TABLE source_lines AS SELECT * EXCLUDE (delivery_id, rn) FROM (SELECT *, row_number() OVER (PARTITION BY line_id ORDER BY delivery_id) AS rn FROM deliveries) WHERE rn = 1")
        silver_sql = (ROOT / "sql" / "silver.sql").read_text(encoding="utf-8").replace("TIMESTAMP_NTZ", "TIMESTAMP")
        con.execute("CREATE TABLE silver AS " + silver_sql)
        con.execute("CREATE VIEW fact_invoice_line AS SELECT * FROM silver WHERE disposition = 'accepted'")
        con.execute("CREATE TABLE daily_sales AS " + (ROOT / "sql" / "daily_sales.sql").read_text(encoding="utf-8"))

        dispositions = dict(con.execute("SELECT disposition, count(*) FROM silver GROUP BY 1").fetchall())
        check("dispositions", {k: dispositions.get(k, 0) for k in expected["dispositions"]}, expected["dispositions"])
        check("disposition rows sum to distinct source rows", sum(dispositions.values()), distinct_rows)
        classes = dict(con.execute("SELECT classification, count(*) FROM silver GROUP BY 1").fetchall())
        check("classifications", classes, expected["classifications"])

        k = con.execute("""SELECT
            coalesce(sum(CASE WHEN line_amount > 0 THEN line_amount ELSE 0 END), 0),
            coalesce(sum(CASE WHEN line_amount < 0 THEN line_amount ELSE 0 END), 0),
            coalesce(sum(line_amount), 0),
            coalesce(sum(CASE WHEN quantity > 0 THEN quantity ELSE 0 END), 0),
            count(*), count(customer_id) FROM fact_invoice_line""").fetchone()
        kpis = {"gross_positive_gbp": str(k[0]), "negative_adjustments_gbp": str(k[1]), "net_recorded_gbp": str(k[2]),
                "positive_units": int(k[3]), "accepted_rows": k[4], "known_customer_rows": k[5], "known_customer_coverage": f"{k[5]}/{k[4]}"}
        for key, wanted in expected["kpis"].items():
            check(f"kpi {key}", kpis[key], wanted)
        check("gross + adjustments = net", Decimal(kpis["gross_positive_gbp"]) + Decimal(kpis["negative_adjustments_gbp"]), Decimal(kpis["net_recorded_gbp"]))

        b = con.execute("""SELECT
            coalesce(sum(CASE WHEN disposition='accepted' THEN parsed_amount END), 0),
            coalesce(sum(CASE WHEN disposition='excluded' THEN parsed_amount END), 0),
            coalesce(sum(CASE WHEN disposition='quarantined' THEN parsed_amount END), 0),
            coalesce(sum(parsed_amount), 0), count(*) - count(parsed_amount) FROM silver""").fetchone()
        bridge = {"accepted_parsed_amount_gbp": str(b[0]), "excluded_parsed_amount_gbp": str(b[1]),
                  "quarantined_parsed_amount_gbp": str(b[2]), "source_parsed_amount_gbp": str(b[3]), "unparseable_amount_rows": b[4]}
        for key, wanted in expected["amount_bridge"].items():
            check(f"bridge {key}", bridge[key], wanted)
        check("accepted + excluded + quarantined parsed = source parsed", b[0] + b[1] + b[2], b[3])

        # Deliberately unsafe comparison: accepted deliveries without identity dedup.
        unsafe = con.execute("SELECT sum(s.line_amount) FROM deliveries d JOIN silver s USING (line_id) WHERE s.disposition='accepted'").fetchone()[0]
        check("unsafe accepted-delivery net (comparison only)", str(unsafe), expected["unsafe_comparison"]["accepted_delivery_net_gbp"])
        check("unsafe overstatement", str(unsafe - b[0]), expected["unsafe_comparison"]["overstatement_gbp"])

        legit = con.execute("SELECT list(source_row ORDER BY source_row), list(DISTINCT CAST(line_amount AS VARCHAR)) FROM fact_invoice_line WHERE source_row IN (2,3)").fetchone()
        check("legitimate identical rows both accepted", legit[0], expected["legitimate_identical_rows"]["source_rows"])
        check("legitimate identical row amount", legit[1], [expected["legitimate_identical_rows"]["each_line_amount_gbp"]])

        row = lambda r, col: con.execute(f"SELECT CAST({col} AS VARCHAR) FROM silver WHERE source_row = {r}").fetchone()[0]
        rl = expected["row_level"]
        check("half-up positive row 11", row(11, "line_amount"), rl["rounding_positive_row_11_gbp"])
        check("half-up negative row 22", row(22, "line_amount"), rl["rounding_negative_row_22_gbp"])
        check("trimmed upper stock code row 18", row(18, "stock_code"), rl["trimmed_stock_code_row_18"])
        check("blank country row 16", row(16, "country"), rl["blank_country_row_16"])
        check("summer civil time row 17 (no offset applied)", row(17, "event_time"), rl["summer_civil_time_row_17"])

        daily = [
            {"date": str(r[0]), "gross_positive_gbp": str(r[1]), "negative_adjustments_gbp": str(r[2]), "net_recorded_gbp": str(r[3]),
             "positive_units": int(r[4]), "rows": r[5], "known_customer_rows": int(r[6])}
            for r in con.execute("SELECT date, gross_positive_gbp, negative_adjustments_gbp, net_recorded_gbp, positive_units, rows, known_customer_rows FROM daily_sales ORDER BY date").fetchall()
        ]
        check("daily_sales rows", daily, expected["daily_sales"])
        check("daily net sums to fact net", str(con.execute("SELECT sum(net_recorded_gbp) FROM daily_sales").fetchone()[0]), kpis["net_recorded_gbp"])

        country = {c: str(v) for c, v in con.execute("SELECT country, sum(line_amount) FROM fact_invoice_line GROUP BY 1").fetchall()}
        check("country net", country, expected["country_net_gbp"])
        duck_version = duckdb.__version__
    finally:
        con.close()

    passed = all(c["passed"] for c in checks)
    evidence = {
        "label": "databricks-contract-local",
        "scope": "LOCAL contract evidence only (DuckDB on the host). This is NOT a Databricks execution; the Databricks job in databricks/ is prepared but unexecuted.",
        "mode": "synthetic",
        "state": "passed" if passed else "failed",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_sha(),
        "uncommitted_changes_in_checked_files": git_dirty(["databricks", "sql", "scripts/check_databricks_contract.py"]),
        "command": ".venv/Scripts/python.exe scripts/check_databricks_contract.py",
        "engine": {"duckdb": duck_version, "python": platform.python_version(), "platform": platform.platform(), "memory_limit": "512MB", "threads": 2},
        "sql_adaptation": "sql/silver.sql with TIMESTAMP_NTZ replaced by DuckDB TIMESTAMP (civil, no offset), as in src/meridian/baseline.py; sql/daily_sales.sql unchanged",
        "inputs": {
            "dataset": {"path": "databricks/contract/lines.csv", "sha256": sha256(DATASET)},
            "expected": {"path": "databricks/contract/expected.json", "sha256": sha256(EXPECTED)},
            "sql_sha256": {f"sql/{n}": sha256(ROOT / "sql" / n) for n in SQL_FILES},
        },
        "results": {"kpis": kpis, "amount_bridge": bridge, "dispositions": dispositions, "unsafe_accepted_delivery_net_gbp": str(unsafe)},
        "checks_passed": sum(c["passed"] for c in checks),
        "checks_total": len(checks),
        "checks": checks,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    for c in checks:
        if not c["passed"]:
            print(f"FAIL {c['check']}: expected {c['expected']!r}, actual {c['actual']!r}")
    print(f"{evidence['state']}: {evidence['checks_passed']}/{evidence['checks_total']} checks -> {EVIDENCE.relative_to(ROOT).as_posix()}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
