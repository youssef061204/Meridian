import { useState } from 'react';
import type { ForecastData, ForecastPoint, ForecastMetric } from './types';

const units = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
const fmt = (v: number | null | undefined) => v == null ? '—' : units.format(v);
const pct = (v: number | null | undefined) => v == null ? 'Undefined' : `${(v * 100).toFixed(1)}%`;
const modelNames: Record<string, string> = { gradient_boosting: 'Gradient boosting', seasonal_naive: 'Weekly seasonal naive', trailing_mean: 'Trailing average', fixed: 'Fixed order level' };
const nameOf = (m: string) => modelNames[m] || m;

function BandChart({ points, comparison, label }: { points: ForecastPoint[]; comparison?: ForecastPoint[]; label: string }) {
  if (!points.length) return <div className="empty compact">No forecast points for this series.</div>;
  const width = 860, height = 250, left = 70, right = 18, top = 14, bottom = 34;
  const values = points.flatMap(p => [p.upper ?? p.prediction, p.lower ?? p.prediction, p.actual ?? 0, p.prediction]).concat((comparison || []).map(p => p.prediction)).filter(v => v != null) as number[];
  const max = Math.max(1, ...values) * 1.05;
  const x = (i: number) => left + (points.length > 1 ? i / (points.length - 1) : .5) * (width - left - right);
  const y = (v: number) => top + (1 - v / max) * (height - top - bottom);
  const line = (rows: ForecastPoint[], key: 'prediction' | 'actual') => rows.map((p, i) => p[key] == null ? '' : `${i && rows[i - 1]?.[key] != null ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[key] as number).toFixed(1)}`).join(' ');
  const band = points.every(p => p.lower != null && p.upper != null)
    ? `M${points.map((p, i) => `${x(i).toFixed(1)},${y(p.upper as number).toFixed(1)}`).join(' L')} L${[...points].reverse().map((p, j) => `${x(points.length - 1 - j).toFixed(1)},${y(p.lower as number).toFixed(1)}`).join(' L')} Z` : '';
  const ticks = [0, max / 2, max];
  return <div className="chart-wrap"><svg className="sales-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
    {ticks.map((t, i) => <g key={i}><line x1={left} x2={width - right} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeDasharray="2 4" /><text x={left - 10} y={y(t) + 4} textAnchor="end">{fmt(t)}</text></g>)}
    {band ? <path d={band} fill="var(--signal)" opacity=".16" /> : null}
    {comparison ? <path d={line(comparison, 'prediction')} fill="none" stroke="var(--amber)" strokeWidth="1.6" strokeDasharray="5 4" /> : null}
    <path d={line(points, 'prediction')} fill="none" stroke="var(--signal)" strokeWidth="2.2" />
    {points.map((p, i) => p.actual != null && p.available !== false ? <circle key={p.date} cx={x(i)} cy={y(p.actual)} r="3" fill="var(--text)" /> : p.available === false ? <line key={p.date} x1={x(i)} x2={x(i)} y1={height - bottom - 6} y2={height - bottom} stroke="var(--amber)" strokeWidth="2" /> : null)}
    <text x={left} y={height - 8}>{points[0].date}</text><text x={width - right} y={height - 8} textAnchor="end">{points[points.length - 1].date}</text>
  </svg></div>;
}

function MetricsTable({ rows, title }: { rows: ForecastMetric[]; title: string }) {
  return <div className="table-scroll"><table aria-label={title}><thead><tr><th scope="col">Scope</th><th scope="col">Horizon</th><th scope="col">Model</th><th scope="col" className="numeric">MAE (units)</th><th scope="col" className="numeric">WAPE</th><th scope="col" className="numeric">80% coverage</th><th scope="col" className="numeric">80% width</th><th scope="col" className="numeric">Scored days</th></tr></thead>
    <tbody>{rows.map(r => <tr key={`${r.series_scope}-${r.horizon}-${r.model_key}`}><td>{r.series_scope}</td><td>{r.horizon} days</td><td>{nameOf(r.model)}</td><td className="numeric">{fmt(r.mae)}</td><td className="numeric amount" title={r.wape_null_reason || undefined}>{pct(r.wape)}</td><td className="numeric">{r.coverage80 == null ? 'No interval' : pct(r.coverage80)}</td><td className="numeric">{fmt(r.width80)}</td><td className="numeric">{fmt(r.n)}</td></tr>)}</tbody></table></div>;
}

export function ForecastView({ data }: { data: ForecastData }) {
  const [series, setSeries] = useState('__ALL__');
  const [split, setSplit] = useState<'test' | 'validation'>('test');
  if (data.state !== 'passed') return <section className="panel forecast-empty"><h2>Forecasts not executed</h2><p>{data.message}</p></section>;
  const label = data.series.find(s => s.series_id === series)?.label || series;
  const test = data.test_forecast[series] || [];
  const promoted = test.filter(p => p.model === data.promoted_model);
  const naive = test.filter(p => p.model === 'seasonal_naive');
  const latest = data.latest_forecast[series] || [];
  const sum = (n: number) => latest.slice(0, n).reduce((a, p) => a + (p.prediction || 0), 0);
  const metricRows = data.metrics[split].filter(r => r.series_scope !== 'per_series').sort((a, b) => a.series_scope.localeCompare(b.series_scope) || a.horizon - b.horizon || a.model.localeCompare(b.model));
  const inv = data.inventory;
  const policies = inv ? Object.entries(inv.base_scenario.policies).sort((a, b) => a[1].total_cost - b[1].total_cost) : [];
  return <>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">PLANNING VOLUME · OBSERVED SALES, NOT DEMAND</span><h2>What to plan for next</h2></div>
      <label className="inline-label">Series<select value={series} onChange={e => setSeries(e.target.value)}>{data.series.map(s => <option key={s.series_id} value={s.series_id}>{s.label}{!s.in_latest ? ' (test only)' : !s.in_test ? ' (latest only)' : ''}</option>)}</select></label></div>
      <div className="period-grid plan-grid"><div><span className="eyebrow">NEXT 7 DAYS · POINT FORECAST</span><strong>{latest.length ? `${fmt(sum(7))} units` : 'Not forecast'}</strong><span>From {data.config.latest_forecast_origin} · {nameOf(data.promoted_model)}</span></div><span className="period-connector" aria-hidden="true">→</span><div><span className="eyebrow">NEXT 28 DAYS · POINT FORECAST</span><strong>{latest.length ? `${fmt(sum(28))} units` : 'Not forecast'}</strong><span>Sum of daily point forecasts</span></div><div className="period-change"><span className="eyebrow">PROMOTED BY VALIDATION</span><strong className="model-name">{nameOf(data.promoted_model)}</strong><span>{data.promotion_reason}</span></div></div>
      <BandChart points={latest.map(p => ({ ...p, actual: null }))} label={`Daily forecast of positive merchandise units for ${label} with 80% interval, next 28 days`} />
      <p className="panel-footnote">Shaded band: daily 80% prediction interval calibrated from past-only residuals. Daily bounds do not add up to an interval for the 7- or 28-day total. Days flagged as expected non-trading (e.g. Saturdays) are forecast at 0. Holiday closures are not modelled.</p></section>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">UNTOUCHED TEST PERIOD · ORIGIN {data.config.test_forecast_origin}</span><h2>Forecast against what actually sold</h2></div><span className="legend forecast-legend"><i /> {nameOf(data.promoted_model)} <i className="legend-dashed" /> Seasonal naive <i className="legend-dot" /> Actual</span></div>
      <BandChart points={promoted} comparison={naive} label={`Test period: actual units versus ${nameOf(data.promoted_model)} and seasonal naive for ${label}`} />
      <p className="panel-footnote">Gold ticks mark unavailable days (no source records): they are forecast but never scored. The final partial day ({data.config.excluded_partial_day}) is never used as an actual.</p></section>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">ROLLING-ORIGIN EVALUATION</span><h2>Error by model and horizon</h2></div><div className="segmented" role="group" aria-label="Evaluation split">{(['test', 'validation'] as const).map(s => <button key={s} className={split === s ? 'active' : ''} aria-pressed={split === s} onClick={() => setSplit(s)}>{s === 'test' ? 'Untouched test' : `Validation (${data.config.origins.filter(o => o.role === 'validation').length} origins)`}</button>)}</div></div>
      <MetricsTable rows={metricRows} title={`${split} metrics`} />
      <p className="panel-footnote">WAPE = Σ|error| ÷ Σ actual units on scored days (undefined when actuals sum to zero). Coverage is the observed share of actuals inside the 80% interval; validation coverage below 80% means the intervals are too narrow. Improvement figures come only from these completed runs. Method and leakage test: docs/forecasting.md.</p></section>
    {inv ? <section className="panel simulated"><div className="panel-heading"><div><span className="eyebrow">{inv.label}</span><h2>Inventory scenarios on held-out weeks</h2></div><span className="mode mode-synthetic"><span className="mode-dot" />simulated</span></div>
      <p className="panel-footnote">Same {inv.episodes.length} product episodes, starting stock and costs for every policy. Base case: {inv.base_scenario.lead_time_days}-day lead time, shortage cost {inv.base_scenario.shortage_to_holding_ratio}× daily holding cost. Costs are abstract units, not GBP. Observed sales stand in for demand.</p>
      <div className="table-scroll"><table><thead><tr><th scope="col">Policy</th><th scope="col" className="numeric">Total cost</th><th scope="col" className="numeric">Holding</th><th scope="col" className="numeric">Shortage</th><th scope="col" className="numeric">Fill rate</th></tr></thead><tbody>{policies.map(([name, p]) => <tr key={name}><td>{nameOf(name)}{name === inv.base_scenario.lowest_total_cost_policy ? <span className="classification winner">lowest in base case</span> : null}</td><td className="numeric amount">{fmt(p.total_cost)}</td><td className="numeric">{fmt(p.holding_cost)}</td><td className="numeric">{fmt(p.shortage_cost)}</td><td className="numeric">{pct(p.fill_rate)}</td></tr>)}</tbody></table></div>
      <p className="panel-footnote">Sensitivity across {inv.sensitivity.length} scenarios (lead time × shortage/holding ratio × safety factor). Lowest-cost policy counts: {Object.entries(inv.lowest_cost_policy_counts).map(([k, v]) => `${nameOf(k)} ${v}`).join(' · ')}. The selected model wins when shortages are cheap relative to holding and loses when they are expensive. These are simulated scenarios under stated assumptions, not client savings; the base-case cost ratio was revised after a first run, and both regimes are reported in docs/inventory-scenarios.md.</p></section> : null}
    <section className="callout"><span className="eyebrow">LIMITATIONS</span><ul className="limits">{data.limitations.map(l => <li key={l}>{l}</li>)}</ul></section>
  </>;
}
