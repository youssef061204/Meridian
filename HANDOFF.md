# MERIDIAN handoff

Specification: `MERIDIAN_Codex_Project_Prompt.md` (user attachment copied unchanged).
Dedicated local repository: `C:\Users\USER\Projects\Meridian`.

## Current gate

Gate 1 in progress. No integration has passed yet. Gates 2–6 not started.

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

All implementation gates require actual execution evidence. Dependency selection is researched in `docs/dependency-compatibility.md`, not yet runtime verified. No paid/cloud services authorized or used.
