import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AnimatePresence, motion, MotionConfig, useScroll, useTransform } from 'motion/react';
import type { ForecastData, Insights, Lineage, MetricKey, Trust } from './types';
import { useApi } from './api';
import { ForecastView } from './Forecast';
import { OverviewView } from './views/Overview';
import { CustomersView } from './views/Customers';
import { BasketsView } from './views/Baskets';
import { SignalsView } from './views/Signals';
import { LineageView, ReliabilityView } from './views/Reliability';
import { ErrorState, Icon, Loading, PageHead, useTheme } from './ui';
import { REPO_URL, SHOWCASE } from './showcase';

const SqlLabView = lazy(() => import('./views/SqlLab').then(m => ({ default: m.SqlLabView })));

type Route = 'pulse' | 'customers' | 'baskets' | 'signals' | 'forecast' | 'reliability' | 'lineage' | 'sql';
const ROUTES: { id: Route; label: string; hint: string }[] = [
  { id: 'pulse', label: 'Pulse', hint: 'Headline sales, trends, cohorts' },
  { id: 'customers', label: 'Customers', hint: 'RFM segments and repeat-purchase model' },
  { id: 'baskets', label: 'Baskets', hint: 'Market basket analysis and product network' },
  { id: 'signals', label: 'Signals', hint: 'Anomalies, hypothesis tests, rhythm' },
  { id: 'forecast', label: 'Forecast', hint: 'Demand forecasts and inventory scenarios' },
  { id: 'reliability', label: 'Reliability', hint: 'Lakehouse, Kafka incident and recovery' },
  { id: 'lineage', label: 'Lineage', hint: 'Trace a KPI to its invoice rows' },
  { id: 'sql', label: 'SQL Lab', hint: 'Ask questions, DuckDB in the browser' },
];
const routeFromHash = (): Route => { const h = window.location.hash.replace(/^#\/?/, '') as Route; return ROUTES.some(r => r.id === h) ? h : 'pulse'; };

function Globe() {
  const { scrollY } = useScroll();
  const y = useTransform(scrollY, [0, 800], [0, 160]);
  const meridians = Array.from({ length: 9 }, (_, i) => i);
  return <motion.svg className="globe" viewBox="0 0 720 720" style={{ y }} aria-hidden="true">
    <circle cx="360" cy="360" r="320" />
    {[-240, -160, -80, 0, 80, 160, 240].map(dy => { const r = Math.sqrt(320 ** 2 - dy ** 2); return <ellipse key={dy} cx="360" cy={360 + dy} rx={r} ry={r * .12} />; })}
    <g className="globe-spin">{meridians.map(i => <ellipse key={i} cx="360" cy="360" rx={320 * Math.abs(Math.cos(i * Math.PI / 9))} ry="320" className={i === 2 ? 'globe-accent' : ''} />)}</g>
    <line x1="360" y1="20" x2="360" y2="700" className="globe-accent" />
  </motion.svg>;
}

function Palette({ open, close, go, toggleTheme }: { open: boolean; close: () => void; go: (r: Route) => void; toggleTheme: () => void }) {
  const [q, setQ] = useState('');
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const items = useMemo(() => [...ROUTES.map(r => ({ key: r.id, label: `Go to ${r.label}`, hint: r.hint, run: () => go(r.id) })),
    { key: 'theme', label: 'Toggle light / dark theme', hint: 'Appearance', run: toggleTheme },
    ...(REPO_URL ? [{ key: 'repo', label: 'Open source code', hint: 'GitHub', run: () => window.open(REPO_URL, '_blank', 'noopener') }] : [])]
    .filter(i => `${i.label} ${i.hint}`.toLowerCase().includes(q.toLowerCase())), [q, go, toggleTheme]);
  useEffect(() => { if (open) { setQ(''); setIndex(0); setTimeout(() => input.current?.focus(), 20); } }, [open]);
  return <AnimatePresence>{open ? <motion.div className="palette-backdrop" onClick={close} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
    <motion.div className="palette" role="dialog" aria-modal="true" aria-label="Command palette" onClick={e => e.stopPropagation()} initial={{ y: -20, scale: .97 }} animate={{ y: 0, scale: 1 }} exit={{ y: -10, opacity: 0 }}>
      <input ref={input} value={q} placeholder="Jump to a view or run a command…" onChange={e => { setQ(e.target.value); setIndex(0); }} aria-label="Search commands"
        onKeyDown={e => { if (e.key === 'ArrowDown') { e.preventDefault(); setIndex(i => Math.min(items.length - 1, i + 1)); } if (e.key === 'ArrowUp') { e.preventDefault(); setIndex(i => Math.max(0, i - 1)); }
          if (e.key === 'Enter' && items[index]) { items[index].run(); close(); } if (e.key === 'Escape') close(); }} />
      <ul role="listbox">{items.map((item, i) => <li key={item.key}><button role="option" aria-selected={i === index} onMouseEnter={() => setIndex(i)} onClick={() => { item.run(); close(); }}><span>{item.label}</span><small>{item.hint}</small></button></li>)}
        {!items.length ? <li style={{ padding: 14, color: 'var(--muted)' }}>No matches</li> : null}</ul>
    </motion.div></motion.div> : null}</AnimatePresence>;
}

function InsightsGate({ children }: { children: (d: Insights) => ReactNode }) {
  const { data, error, loading, reload } = useApi<Insights>('/api/insights');
  if (loading) return <Loading label="Loading customer, basket and statistical insights…" />;
  if (error) return <ErrorState message={error} retry={reload} />;
  if (!data || data.state !== 'passed') return <section className="callout"><span className="eyebrow">Not executed</span><h2>Insights have not been produced yet</h2><p>{data?.message}</p></section>;
  return <>{children(data)}</>;
}

function Gate<T>({ path, label, children }: { path: string; label?: string; children: (d: T) => ReactNode }) {
  const { data, error, loading, reload } = useApi<T>(path);
  if (loading && !data) return <Loading label={label} />;
  if (error) return <ErrorState message={error} retry={reload} />;
  return data ? <>{children(data)}</> : null;
}

export default function App() {
  const [route, setRoute] = useState<Route>(routeFromHash);
  const [metric, setMetric] = useState<MetricKey>('net_recorded_gbp');
  const [countries, setCountries] = useState<string[]>([]);
  const [palette, setPalette] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const go = useMemo(() => (r: Route) => { window.location.hash = `/${r}`; }, []);
  useEffect(() => {
    const onHash = () => { setRoute(routeFromHash()); window.scrollTo({ top: 0, behavior: 'smooth' }); };
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette(p => !p); } };
    window.addEventListener('hashchange', onHash); window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('hashchange', onHash); window.removeEventListener('keydown', onKey); };
  }, []);
  useEffect(() => { document.title = `${ROUTES.find(r => r.id === route)!.label} · MERIDIAN`; }, [route]);
  const explore = (m: MetricKey) => { setMetric(m); go('lineage'); };

  const view = (() => {
    switch (route) {
      case 'pulse': return <OverviewView explore={explore} countries={countries} setCountries={setCountries} />;
      case 'customers': return <InsightsGate>{d => <CustomersView data={d} />}</InsightsGate>;
      case 'baskets': return <InsightsGate>{d => <BasketsView data={d} />}</InsightsGate>;
      case 'signals': return <InsightsGate>{d => <SignalsView data={d} />}</InsightsGate>;
      case 'forecast': return <><PageHead index="§05" kicker="Forecast · rolling-origin evaluation" title="Plan for what will" accent="actually sell." lede="7- and 28-day forecasts of observed sales, promoted on validation only and scored once on an untouched test month, plus simulated inventory policies." />
        <Gate<ForecastData> path="/api/forecasts">{d => <ForecastView data={d} />}</Gate></>;
      case 'reliability': return <Gate<Trust> path="/api/trust">{d => <ReliabilityView data={d} />}</Gate>;
      case 'lineage': return <Gate<Lineage> path={`/api/lineage?metric=${metric}&limit=50`}>{d => <LineageView data={d} metric={metric} setMetric={setMetric} />}</Gate>;
      case 'sql': return <Suspense fallback={<Loading label="Loading the SQL Lab…" />}><SqlLabView /></Suspense>;
    }
  })();

  return <MotionConfig reducedMotion="user">
    <a className="skip-link" href="#main">Skip to content</a>
    <div className="atmosphere" aria-hidden="true"><div className="grid-lines" /><Globe /></div>
    <div className="grain" aria-hidden="true" />
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/pulse" aria-label="MERIDIAN home"><span className="brand-mark" /><span>MERIDIAN<small>retail intelligence</small></span></a>
        <nav className="dock" aria-label="Main">{ROUTES.map(r => <button key={r.id} aria-current={route === r.id ? 'page' : undefined} onClick={() => go(r.id)} title={r.hint}>
          {route === r.id ? <motion.span className="dock-pill" layoutId="dock-pill" transition={{ type: 'spring', stiffness: 420, damping: 34 }} /> : null}<span>{r.label}</span></button>)}</nav>
        <div className="top-actions">
          <button className="kbd-button" onClick={() => setPalette(true)} aria-label="Open command palette"><span style={{ width: 15, display: 'inline-flex' }}><Icon name="search" /></span><span className="label">Search</span><kbd>⌘K</kbd></button>
          <button className="icon-button" onClick={toggleTheme} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}><Icon name={theme === 'dark' ? 'sun' : 'moon'} /></button>
          {REPO_URL ? <a className="icon-button" href={REPO_URL} target="_blank" rel="noreferrer" aria-label="Source code on GitHub"><Icon name="github" /></a> : null}
        </div>
      </header>
      <nav className="mobile-nav" aria-label="Main (compact)">{ROUTES.map(r => <button key={r.id} aria-current={route === r.id ? 'page' : undefined} onClick={() => go(r.id)}>{r.label}</button>)}</nav>
      <main id="main">
        {SHOWCASE ? <div className="banner" role="note"><strong>Public showcase</strong><span>Responses recorded verbatim from the real local stack (Spark, Delta, Kafka, DuckDB). Filters and the SQL Lab run live in your browser with DuckDB-WASM; the recovery buttons replay a recorded incident and repair.</span></div> : null}
        <AnimatePresence mode="wait">
          <motion.div key={route + (route === 'lineage' ? metric : '')} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} transition={{ duration: .35, ease: [0.22, 1, 0.36, 1] }}>{view}</motion.div>
        </AnimatePresence>
      </main>
      <footer className="site-footer">
        <div><p className="footer-title">Reliable data.<br /><span className="serif" style={{ color: 'var(--signal)' }}>Defensible decisions.</span></p><p>A portfolio case study for a fictional UK online retailer, built on real public data.</p></div>
        <div><span className="eyebrow">Stack</span><ul><li>Spark 3.5 · Delta Lake 3.3 · Kafka 4.1</li><li>DuckDB · DuckDB-WASM · scikit-learn</li><li>FastAPI · React 19 · Motion</li><li>Databricks bundle · Power BI · Azure Bicep</li></ul></div>
        <div><span className="eyebrow">Source</span><ul><li><a href="https://archive.ics.uci.edu/dataset/502/online+retail+ii" target="_blank" rel="noreferrer">UCI Online Retail II</a> · CC BY 4.0</li><li>Daqing Chen, DOI 10.24432/C5CG6D</li>{REPO_URL ? <li><a href={REPO_URL} target="_blank" rel="noreferrer">Source and run instructions</a></li> : null}</ul></div>
      </footer>
    </div>
    <Palette open={palette} close={() => setPalette(false)} go={go} toggleTheme={toggleTheme} />
  </MotionConfig>;
}
