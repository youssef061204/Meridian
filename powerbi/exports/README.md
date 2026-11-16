# Power BI exports (generated, git-ignored)

`scripts/export_powerbi.py` writes `*.csv` (and `*.parquet` with `--format parquet|both`) here from the
validated full snapshot named by `data/serving/full.json`. The files are large (the fact CSV is about
205 MB) and are not committed. To regenerate them:

```powershell
.venv\Scripts\python.exe scripts\export_powerbi.py --format both
```

Row counts, SHA-256 checksums, and the snapshot id of the latest export are recorded in
`evidence/powerbi-export.json`. If your local files do not match those checksums, they come from a
different export.
