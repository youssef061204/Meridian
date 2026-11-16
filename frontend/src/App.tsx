import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import type { Daily, Lineage, MetricKey, Overview, Snapshot, Trust } from './types';

type View = 'overview' | 'forecast' | 'trust' | 'explorer';
type Filters = { country: string; start: string; end: string };
const emptyFilters: Filters = { country: '', start: '', end: '' };
const names: Record<View, string> = {
  overview: 'Business overview', forecast: 'Forecast & planning',
  trust: 'Data trust & recovery', explorer: 'Metric explorer',
};
const descriptions: Record<View, string> = {
  overview: 'Recorded sales, with the evidence to explain them.',
  forecast: 'Plan observed sales with honest, chronological evaluation.',
  trust: 'Follow every source row through the published snapshot.',
  explorer: 'Trace a number back to its definition and source rows.',
};
const metricTitles: Record<MetricKey, string> = {
  gross_positive_gbp: 'Gross positive merchandise',
  negative_adjustments_gbp: 'Negative adjustments',
  net_recorded_gbp: 'Net recorded merchandise',
};
const gbp = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', maximumFractionDigits: 2 });
const integer = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
const money = (value: unknown) => value == null ? '—' : gbp.format(Number(value));
const number = (value: unknown) => value == null ? '—' : integer.format(Number(value));
const date = (value: string | undefined) => value ? value.slice(0, 10) : 'Unknown';
const timestamp = (value: string | undefined) => value ? new Date(value).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' }) : 'Unknown';

function Icon({ name }: { name: View | 'arrow' | 'refresh' }) {
  const paths: Record<string, string> = {
    overview: 'M3 13h5v8H3z M10 3h5v18h-5z M17 8h5v13h-5z',
    forecast: 'M3 19l6-7 4 3 8-11 M15 4h6v6',
    trust: 'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6',
    explorer: 'M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14z M15 15l6 6',
    arrow: 'M5 12h14 M13 6l6 6-6 6',
    refresh: 'M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-2l2 7 M4 12l2 7a7 7 0 0 0 12-2',
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

function Mode({ mode }: { mode: string | undefined }) {
  const normalized = (mode || 'No snapshot').toLowerCase();
  return <span className={`mode mode-${normalized}`}><span className="mode-dot" />{normalized}</span>;
}

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) {
    let detail = '';
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : ''; } catch { /* A proxy can return an HTML error. */ }
    throw new Error(detail || `The data service returned ${response.status}. Check the local API and published snapshot.`);
  }
  return response.json() as Promise<T>;
}

function SalesChart({ daily }: { daily: Daily[] }) {
  if (!daily.length) return <div className="empty compact">No recorded sales in this selection.</div>;
  const width = 850, height = 230, left = 82, right = 22, top = 16, bottom = 36;
  const values = daily.map(row => Number(row.net_recorded_gbp));
  const max = Math.max(0, ...values), min = Math.min(0, ...values);
  const span = max - min || 1;
  const y = (v: number) => top + (max - v) / span * (height - top - bottom);
  const x = (i: number) => left + (daily.length === 1 ? .5 : i / (daily.length - 1)) * (width - left - right);
  const line = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
  const area = `${line} L${x(values.length - 1)},${y(0)} L${x(0)},${y(0)} Z`;
  const ticks = [min, min + span / 2, max];
  return <div className="chart-wrap"><svg className="sales-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Daily net recorded merchandise sales in GBP, ${date(daily[0].date)} to ${date(daily.at(-1)?.date)}. ${daily.length} observed days.`}>
    <defs><linearGradient id="sales-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#3d8981" stopOpacity=".22" /><stop offset="100%" stopColor="#3d8981" stopOpacity=".02" /></linearGradient></defs>
    {ticks.map((tick, i) => <g key={i}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} stroke="#e7ece9" /><text x={left - 12} y={y(tick) + 4} textAnchor="end">{new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', notation: 'compact', maximumFractionDigits: 1 }).format(tick)}</text></g>)}
    <path d={area} fill="url(#sales-fill)" /><path d={line} fill="none" stroke="#2e7770" strokeWidth="2.5" />
    {daily.length === 1 ? <circle cx={x(0)} cy={y(values[0])} r="4" fill="#2e7770" /> : null}
    <text x={left} y={height - 6}>{date(daily[0].date)}</text><text x={width - right} y={height - 6} textAnchor="end">{date(daily.at(-1)?.date)}</text>
  </svg><p className="chart-caption">Observed source dates · GBP · gaps are not imputed as zero sales</p></div>;
}

function SnapshotDetails({ snapshot }: { snapshot: Snapshot }) {
  return <dl className="details">
    <div><dt>Snapshot</dt><dd className="mono">{snapshot.id}</dd></div>
    <div><dt>Published</dt><dd>{timestamp(snapshot.published_at)}</dd></div>
    <div><dt>Source period</dt><dd>{date(snapshot.date_min)} → {date(snapshot.date_max)}</dd></div>
    <div><dt>Source SHA-256</dt><dd className="mono checksum">{snapshot.source_sha256}</dd></div>
    <div><dt>Delta table versions</dt><dd>{Object.entries(snapshot.delta_versions || {}).map(([name, version]) => <span className="version" key={name}>{name} <strong>v{version}</strong></span>)}</dd></div>
  </dl>;
}

function OverviewView({ data, explore }: { data: Overview; explore: (metric: MetricKey) => void }) {
  const known = Number(data.metrics.known_customer_rows || 0);
  const scopedRows = Number(data.metrics.accepted_rows ?? data.metrics.rows ?? data.snapshot.accepted_rows);
  return <>
    <div className="metric-grid">
      {(Object.keys(metricTitles) as MetricKey[]).map((key, i) => <button className={`metric-card ${key === 'net_recorded_gbp' ? 'metric-featured' : ''}`} key={key} onClick={() => explore(key)}>
        <span className="eyebrow">{i === 0 ? '01 / POSITIVE SALES' : i === 1 ? '02 / ADJUSTMENTS' : '03 / RECORDED NET'}</span>
        <span className="metric-label">{metricTitles[key]}</span><strong>{money(data.metrics[key])}</strong>
        <span className="metric-link">Explore contributing rows <Icon name="arrow" /></span>
      </button>)}
    </div>
    <p className="metric-note">Positive merchandise value + negative adjustments = net recorded merchandise value. These are recorded invoice values, not audited revenue or cash collected.</p>
    <section className="panel chart-panel"><div className="panel-heading"><div><span className="eyebrow">SALES OVER TIME</span><h2>Every day, in context</h2></div><span className="legend"><i /> Net recorded merchandise</span></div><SalesChart daily={data.daily} /></section>
    <div className="two-column">
      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">PRODUCT CONTRIBUTION</span><h2>What drives recorded sales</h2></div></div><div className="table-scroll"><table><thead><tr><th scope="col">Product</th><th scope="col" className="numeric">Positive units</th><th scope="col" className="numeric">Net GBP</th></tr></thead><tbody>{data.products.slice(0, 8).map(row => <tr key={row.stock_code}><td><span className="product-name">{row.description || 'Description unavailable'}</span><span className="table-muted mono">{row.stock_code}</span></td><td className="numeric">{number(row.positive_units)}</td><td className="numeric amount">{money(row.net_recorded_gbp)}</td></tr>)}</tbody></table>{!data.products.length ? <p className="empty compact">No products in this selection.</p> : null}</div></section>
      <section className="panel"><div className="panel-heading"><div><span className="eyebrow">GEOGRAPHY</span><h2>Where sales are recorded</h2></div></div><div className="table-scroll"><table><thead><tr><th scope="col">Country</th><th scope="col" className="numeric">Rows</th><th scope="col" className="numeric">Net GBP</th></tr></thead><tbody>{data.countries.slice(0, 8).map(row => <tr key={row.country}><td>{row.country || 'Unknown country'}</td><td className="numeric">{number(row.rows)}</td><td className="numeric amount">{money(row.net_recorded_gbp)}</td></tr>)}</tbody></table>{!data.countries.length ? <p className="empty compact">No countries in this selection.</p> : null}</div></section>
    </div>
    <div className="coverage-strip"><div className="coverage-symbol">◎</div><div><strong>Customer identity coverage</strong><p>{number(known)} of {number(scopedRows)} accepted lines have a known customer identifier. Missing identifiers remain in valid sales totals; customer analysis covers identified buyers only.</p></div><strong className="coverage-percent">{scopedRows ? `${(known / scopedRows * 100).toFixed(1)}%` : '—'}</strong></div>
  </>;
}

function TrustView({ data }: { data: Trust }) {
  const s = data.snapshot;
  const accounted = Number(s.accepted_rows) + Number(s.quarantined_rows) + Number(s.excluded_rows);
  const reconciled = accounted === Number(s.source_rows);
  return <>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">SOURCE → SERVING</span><h2>Every original row has a destination</h2></div><span className={`status ${reconciled ? 'status-good' : 'status-alert'}`}>{reconciled ? 'Counts reconciled' : 'Count discrepancy'}</span></div><div className="trust-counts">{[['Original source rows', s.source_rows], ['Accepted logical rows', s.accepted_rows], ['Quarantined', s.quarantined_rows], ['Named exclusions', s.excluded_rows], ['Repeated deliveries', s.repeat_deliveries]].map(([label, value]) => <div key={String(label)}><span>{label}</span><strong>{number(value)}</strong></div>)}</div><p className="panel-footnote">Source rows and received deliveries have different denominators. Repeated deliveries are reported separately and must not increase merchandise sales.</p></section>
    <div className="two-column"><section className="panel"><div className="panel-heading"><div><span className="eyebrow">VALIDATED PUBLICATION</span><h2>Serving snapshot</h2></div><Mode mode={s.mode} /></div><SnapshotDetails snapshot={s} /></section><section className="panel"><div className="panel-heading"><div><span className="eyebrow">PERSISTED EVIDENCE</span><h2>Publication state</h2></div></div><pre className="json-evidence">{JSON.stringify(data.publication, null, 2)}</pre><p className="panel-footnote">Publication evidence is read from the local pipeline. A snapshot timestamp describes the publication, not the age of the original observations.</p></section></div>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">EXECUTION HISTORY</span><h2>Recorded pipeline runs</h2></div></div>{data.runs.length ? <div className="run-list">{data.runs.slice(0, 12).map((run, index) => <details key={String(run.run_id || run.id || index)}><summary><span className="mono">{String(run.run_id || run.id || `Run ${index + 1}`)}</span><span className="run-state">{String(run.status || run.state || 'Evidence available')}</span></summary><pre>{JSON.stringify(run, null, 2)}</pre></details>)}</div> : <div className="empty compact">No run manifests have been recorded.</div>}</section>
    <section className="callout"><span className="eyebrow">RECOVERY DEMONSTRATION</span><h2>Incident controls arrive with verified replay</h2><p>The current view exposes persisted batch evidence. Duplicate replay, malformed events, interruption, and recovery will be enabled after the real Kafka path has passed its integration gate.</p></section>
  </>;
}

function ExplorerView({ data, metric, setMetric }: { data: Lineage; metric: MetricKey; setMetric: (value: MetricKey) => void }) {
  return <>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">METRIC DEFINITION</span><h2>{metricTitles[metric]}</h2></div><label className="inline-label">Metric<select value={metric} onChange={event => setMetric(event.target.value as MetricKey)}>{Object.entries(metricTitles).map(([key, name]) => <option value={key} key={key}>{name}</option>)}</select></label></div><pre className="sql-code"><code>{data.sql}</code></pre><p className="panel-footnote">Currency: GBP. Amounts use the pipeline’s decimal calculation and named merchandise rules. The source workbook has no payment settlement or product cost data.</p></section>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">CONTRIBUTING SOURCE EVIDENCE</span><h2>From the KPI to the invoice line</h2></div><span className="small-muted">Showing {number(data.rows.length)} of {number(data.total_rows)} rows</span></div><div className="table-scroll"><table className="lineage-table"><thead><tr><th scope="col">Source coordinate</th><th scope="col">Invoice / product</th><th scope="col">Source time</th><th scope="col">Customer</th><th scope="col" className="numeric">Quantity</th><th scope="col" className="numeric">Unit GBP</th><th scope="col" className="numeric">Line GBP</th><th scope="col">Classification</th></tr></thead><tbody>{data.rows.map(row => <tr key={row.line_id}><td><span className="mono">{row.source_sheet}:{row.source_row}</span><span className="table-muted mono" title={row.line_id}>{row.line_id.slice(0, 12)}…</span></td><td><span className="mono">{row.invoice}</span><span className="table-muted">{row.stock_code} · {row.description}</span></td><td className="nowrap">{row.event_time?.replace('T', ' ').slice(0, 19)}</td><td>{row.customer_id || <span className="unknown">Unknown</span>}</td><td className="numeric">{number(row.quantity)}</td><td className="numeric">{money(row.unit_price)}</td><td className="numeric amount">{money(row.line_amount)}</td><td><span className="classification">{row.classification}</span><span className="table-muted">{row.country}</span></td></tr>)}</tbody></table>{!data.rows.length ? <p className="empty compact">No contributing lines in this selection.</p> : null}</div></section>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">LINEAGE ANCHOR</span><h2>Published evidence</h2></div></div><SnapshotDetails snapshot={data.snapshot} /></section>
  </>;
}

function ForecastView() {
  return <section className="panel forecast-empty"><span className="forecast-icon"><Icon name="forecast" /></span><span className="eyebrow">IMPLEMENTATION GATE 4</span><h2>Forecasts will follow verified sales data</h2><p>This working slice establishes the source-to-KPI path. No forecast has been trained or evaluated yet, so no planning recommendation is published.</p><div className="forecast-plan"><div><span>01</span><strong>Establish the baseline</strong><p>Weekly seasonal naive and trailing average.</p></div><div><span>02</span><strong>Evaluate through time</strong><p>At least four rolling origins and an untouched final test.</p></div><div><span>03</span><strong>Publish honest uncertainty</strong><p>7- and 28-day observed-sales forecasts with measured interval coverage.</p></div></div><p className="small-muted">Observed sales are a demand proxy. Inventory, lost sales, and product costs are not present in this source.</p></section>;
}

export default function App() {
  const [view, setView] = useState<View>('overview');
  const [draft, setDraft] = useState<Filters>(emptyFilters);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [metric, setMetric] = useState<MetricKey>('net_recorded_gbp');
  const [overview, setOverview] = useState<Overview | null>(null);
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [trust, setTrust] = useState<Trust | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [countries, setCountries] = useState<string[]>([]);
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value)).toString();

  useEffect(() => {
    if (view === 'forecast') { setLoading(false); setError(''); return; }
    const controller = new AbortController();
    setLoading(true); setError('');
    const path = view === 'overview' ? `/api/overview?${query}` : view === 'explorer' ? `/api/lineage?metric=${metric}&limit=50&${query}` : '/api/trust';
    getJson<Overview | Lineage | Trust>(path, controller.signal).then(result => {
      if (view === 'overview') { const data = result as Overview; setOverview(data); if (data.filters?.countries?.length) setCountries(previous => [...new Set([...previous, ...data.filters.countries])].sort()); }
      if (view === 'explorer') setLineage(result as Lineage);
      if (view === 'trust') setTrust(result as Trust);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'The data request failed.');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [view, query, metric, refresh]);

  const snapshot = view === 'trust' ? trust?.snapshot : view === 'explorer' ? lineage?.snapshot : overview?.snapshot;
  const explore = (key: MetricKey) => { setMetric(key); setView('explorer'); };
  const apply = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setFilters({ ...draft }); };
  const activeFilter = !!(filters.country || filters.start || filters.end);
  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to main content</a>
    <aside className="sidebar"><a className="brand" href="#" onClick={event => { event.preventDefault(); setView('overview'); }} aria-label="Meridian business overview"><span className="brand-mark"><i /><i /><i /></span><span>MERIDIAN<small>RETAIL INTELLIGENCE</small></span></a><div className="workspace-label">THE RETAILER’S WORKSPACE</div><nav aria-label="Main navigation">{(Object.keys(names) as View[]).map(key => <button key={key} className={view === key ? 'nav-item active' : 'nav-item'} aria-current={view === key ? 'page' : undefined} onClick={() => setView(key)}><Icon name={key} /><span>{names[key]}</span>{view === key ? <span className="nav-active-dot" /> : null}</button>)}</nav><div className="sidebar-note"><span className="sidebar-note-icon">↳</span><strong>Evidence before decisions.</strong><p>From original invoice rows to reconciled business measures.</p></div><div className="sidebar-footer"><span className="local-dot" /> Local portfolio case study<small>UCI Online Retail II · CC BY 4.0</small></div></aside>
    <div className="workspace"><header className="topbar"><div className="breadcrumb">MERIDIAN <span>/</span> {names[view]}</div><div className="topbar-right"><span className="local-label">LOCAL WORKSPACE</span><span className="avatar" aria-hidden="true">M</span></div></header><main id="main-content"><div className="page-heading"><div><span className="eyebrow">UK ONLINE RETAIL · PORTFOLIO CASE STUDY</span><h1>{names[view]}</h1><p>{descriptions[view]}</p></div><div className="heading-meta"><Mode mode={snapshot?.mode} /><button className="refresh-button" onClick={() => setRefresh(value => value + 1)} aria-label="Refresh persisted data" disabled={loading}><Icon name="refresh" /><span>Refresh</span></button></div></div>
      {snapshot ? <div className="source-ribbon"><span className="source-label">OBSERVED SOURCE PERIOD</span><strong>{date(snapshot.date_min)} — {date(snapshot.date_max)}</strong><span className="ribbon-divider" /><span>Snapshot <span className="mono" title={snapshot.id}>{snapshot.id.length > 28 ? `${snapshot.id.slice(0, 28)}…` : snapshot.id}</span></span><span className="publication-time">Published {timestamp(snapshot.published_at)}</span></div> : null}
      {view === 'overview' || view === 'explorer' ? <form className="filters" onSubmit={apply}><label>Country<select value={draft.country} onChange={event => setDraft({ ...draft, country: event.target.value })}><option value="">All countries</option>{countries.map(country => <option key={country}>{country}</option>)}</select></label><label>From<input type="date" value={draft.start} onChange={event => setDraft({ ...draft, start: event.target.value })} /></label><label>Through<input type="date" value={draft.end} min={draft.start || undefined} onChange={event => setDraft({ ...draft, end: event.target.value })} /></label><button className="button-primary" type="submit" disabled={loading}>Apply filters</button><button className="button-text" type="button" onClick={() => { setDraft(emptyFilters); setFilters(emptyFilters); }}>Reset</button>{activeFilter ? <span className="filter-active">Filtered selection</span> : <span className="filter-active">All published records</span>}</form> : null}
      <div aria-live="polite" aria-busy={loading}>{loading ? <div className="loading-state"><span className="spinner" /><h2>Reading the published evidence…</h2><p>Retrieving the current validated snapshot.</p></div> : error ? <section className="error-state" role="alert"><span className="eyebrow">DATA SERVICE UNAVAILABLE</span><h2>The evidence could not be loaded</h2><p>{error}</p><button className="button-primary" onClick={() => setRefresh(value => value + 1)}>Try again</button></section> : view === 'overview' && overview ? <OverviewView data={overview} explore={explore} /> : view === 'trust' && trust ? <TrustView data={trust} /> : view === 'explorer' && lineage ? <ExplorerView data={lineage} metric={metric} setMetric={setMetric} /> : view === 'forecast' ? <ForecastView /> : <div className="empty">No published evidence is available.</div>}</div>
      <footer className="page-footer"><span>MERIDIAN <span className="footer-separator">/</span> Reliable data. Defensible decisions.</span><a href="https://archive.ics.uci.edu/dataset/502/online+retail+ii" target="_blank" rel="noreferrer">UCI Online Retail II source ↗</a></footer></main></div>
  </div>;
}
