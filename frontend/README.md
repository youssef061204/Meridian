# MERIDIAN local interface

The React client reads the FastAPI service's persisted snapshot endpoints. It does not contain sample sales values, simulated run statuses, or a browser-side sales calculation. Amounts are formatted as GBP; authoritative decimal calculations remain in the pipeline.

From this directory, with Node.js 24.13.0 (used for verification) and npm available:

```powershell
npm.cmd ci --no-fund --no-audit
npm.cmd run build
```

On Linux, use `npm` in place of `npm.cmd`. The build writes `dist/`, intended to be served by the local FastAPI application. Development mode is `npm.cmd run dev`; Vite proxies `/api` to `http://127.0.0.1:8010`.

The four views are Business overview, Forecast & planning, Data trust & recovery, and Metric explorer. Forecasting and incident controls remain explicitly pending until their implementation gates pass. Country and inclusive date filters apply to the overview and metric explorer. The data trust view reports the complete snapshot.

Required endpoints:

- `GET /api/overview?country=&start=&end=`
- `GET /api/lineage?metric=net_recorded_gbp&limit=50&country=&start=&end=`
- `GET /api/trust`

The overview should return `metrics.accepted_rows` for its filtered customer coverage denominator. API types are in `src/types.ts`. No third-party assets, tracking, credentials, paid service, or cloud deployment are required.

Verified during implementation: dependency installation and a production build (TypeScript checking and Vite bundling). Browser flows require the real API and are verified in the project-level evidence rather than asserted here.
