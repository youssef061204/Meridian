# MERIDIAN handoff

Specification: `MERIDIAN_Codex_Project_Prompt.md` (user attachment copied unchanged).
Dedicated local repository: `C:\Users\USER\Projects\Meridian`.

## Current gate

Gate 1 verification in progress. Real source conversion, Spark/Delta smoke, 4,000-row demo batch, actual synthetic fixture Spark batch, and independent fixture comparison passed. Browser verification is running. Gates 2–6 not started; do not advance until browser evidence is checked.

## Environment inspected

2026-11-16: Windows, Docker Desktop Linux engine 29.3.1 / WSL2, 28 logical CPUs (i7-14700HX), 15.71 GiB physical RAM, 1.70 GiB initially free, 142.61 GiB free disk. Docker VM has 7.61 GiB RAM. Other unrelated containers are running and were left alone. Host Python 3.11.9 available; default Python 3.14 unsuitable. Host Java 21 outside selected Spark 3.5 support: isolate Java 17 in Docker.

## Execution sequence

1. Verify UCI workbook, both sheets and checksum; bounded conversion.
2. Real Spark/Delta smoke; source slice → Silver decimal semantics → Gold → validated export → API/UI.
3. Independent fixture and browser verification. Advance gate only after evidence is recorded.
4. Full history/model/analysis; replay/recovery; forecasts; delivery, in specification order.

## Commands executed

`git init -b main`

`uv venv --python 3.11 .venv`

Environment: `docker version`, `docker image ls`, `docker stats --no-stream`, `py -0p`, `java -version`, `wsl --list --verbose`, Windows CIM hardware inspection.

## Remaining

Gate 1: complete browser checks; repeat demo publication to include immutable SQL artifacts and explicit monetary balance fields added after the first demo run. Enforce locked Docker rebuild and test documented startup. Gates 2–6 remain.

## Executed results

- `python -m meridian.cli convert` (host `.venv`): downloaded UCI archive, pinned archive/workbook SHA256 in `SOURCE.lock.json`, streamed workbook in 10,000-row chunks. Both sheets match UCI's 1,067,371 source rows. 525,461/541,910 rows; date ranges 2009-12-01 07:45 to 2010-12-09 20:01 / 2010-12-01 08:26 to 2011-12-09 12:50. Unknown customer IDs:107,927/135,080. Conversion99.4512s; download4.0391s. Evidence `evidence/source-verification.json`. Initial run metadata wrote git_sha="HEAD" before the empty-repository handling was corrected; no commit existed at conversion time.
- `docker compose build tools`: passed. Initial image resolved transitives; Dockerfile now changed to enforce generated hash-locked `requirements.lock`, rebuild pending.
- `docker compose run --rm --no-deps --name meridian-job tools python -m meridian.cli smoke`: passed on second attempt. First attempt failed because git binary was absent in container; source ref reading now supports that environment. Both failed and passed logs retained in `evidence/execution/spark-smoke*`. Actual Spark3.5.7/Delta3.3.3/Scala2.12.18/Java17.0.21/Python3.11.11. Connector class loads, Kafka broker path not executed.
- Demo command: `docker compose run --rm --no-deps --name meridian-job tools python -m meridian.cli batch --profile demo`: 4,000 source rows, accepted3,986/excluded14/quarantined0; gross71183.35, adjustments−1428.92, net69754.43 GBP. Spark stage52.3203s / wrapper57.1155s. Sampled container memory up to1.214GiB, limit2.246GiB; warm JVM artifact cache, uncontrolled OS cache, one repetition. `evidence/execution/demo-batch.*`.
- Fixture command: same with `--profile fixture`: passed; 18 deliveries/16 source coordinates; accepted8/quarantined7/excluded1; gross48.51, adjustments−13.51, net35.00 GBP. Spark39.3044s / wrapper41.8332s. `python tests/check_spark_fixture.py` passed all independent source identity, exact monetary, country/date assertions against actual exported Spark snapshot.
- `python -m pytest -q`:32 tests passed after API additions. Tests distinguish isolated API snapshots from real Spark integration; fixture checker above supplies integration evidence.
- `npm.cmd --prefix frontend install --no-fund --no-audit`, `npm.cmd --prefix frontend run build`: passed. Calendar chart leaves unavailable days blank.
- `docker compose up -d api`: running at http://localhost:8010 bound to loopback. `GET /api/health` returned200. API restarted after immutable-lineage/date-validation code changes.

## Correctness decisions and outstanding scope

- Preserve every original sheet/row; only repeat deliveries of same source identity deduplicate. Workbook sheets overlap Dec1–9 2010; assess exact repeated content in Gate2 before broad business interpretation.
- Source-row reconciliation and delivery counts have different denominators. Decimal half-up per line; named exclusions and quarantine retain parseable amounts. No profit/payment/inventory claims.
- Last validated serving pointer changes only after all table exports pass validation. Concurrent publishers currently require a single writer. Conflicting payloads under the same source identity require Gate3 conflict handling.
- All cloud/native Databricks/PowerBI capabilities are unexecuted. No paid/cloud services authorized or used.
