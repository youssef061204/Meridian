# Two-minute demo script

**Before the demo** (about 3 minutes, once):

```powershell
docker compose up -d api
docker compose --profile replay up -d kafka worker
docker compose run --rm --no-deps tools python -m meridian.cli replay-init --profile demo   # only if no demo replay generation exists
```

Open http://localhost:8010. If a previous incident is still shown, press **Reset to baseline** on the Data trust page first.

Each job takes about 30–60 s on the reference laptop. Talk over the wait; the job stages update live.

| Time | Action | Say |
|---|---|---|
| 0:00 | **Business overview.** Point to the period ribbon and the three KPI cards. | "Two years of real UCI invoice lines. Gross, adjustments and net add up exactly. This is recorded invoice value, not audited revenue." |
| 0:10 | Click **Net recorded merchandise**. | "Every KPI traces to its SQL, the published snapshot, pinned Delta versions and the original workbook sheet and row." |
| 0:25 | Open **Data trust & recovery**. | "This replays the historical slice through local Kafka and Spark. Replay and synthetic incidents are labelled throughout. Batch reconciliation says *incomplete*: 12 real rows haven't arrived yet." |
| 0:35 | Press **Inject controlled incident**. | "Seeded synthetic events: repeated deliveries, malformed JSON, missing fields, an incompatible schema, an additive one, a conflicting re-export, the 12 late rows and a price correction. The real consumer is processing them now." |
| 1:05 | Point to the **Unsafe** card vs the **Trusted** card. | "A naive sum of the raw feed is about £3,000 too high. That's the client's disagreeing dashboard. It's computed, but never served." |
| 1:15 | Point to the quarantine reasons, the late-arrival count and the timeline. | "Rejects are contained by reason. Late rows went to a durable backfill table, not the bin. Publication is blocked, and serving keeps the last validated snapshot." |
| 1:25 | Press **Repair and reprocess**. | "Backfill applies the late rows for the affected date only, runs a second time to prove it's idempotent, then publishes." |
| 1:50 | Point to *reconciled*, the new snapshot ID and *recovered in N s*. | "Replay now equals batch exactly; the only difference is the correction." |
| 1:55 | **Metric explorer**, scroll to the revision history. | "Revision 1 and revision 2 of the corrected line. The old one is kept, not overwritten." |

**Optional extras:**
- **Interrupt a publish** shows the pointer not moving.
- **Forecast & planning** shows 7- and 28-day planning volumes against the untouched test.

Recorded browser run of this sequence: [evidence/browser-demo.json](../evidence/browser-demo.json); screenshots in [evidence/screenshots/](../evidence/screenshots).
