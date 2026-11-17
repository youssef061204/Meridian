import { useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { motion } from 'motion/react';
import type { Insights, MetricKey, Overview } from '../types';
import { useApi } from '../api';
import { Heatmap, TimeSeries } from '../charts';
import { Bars, Card, CardHead, CountUp, ErrorState, Icon, Loading, PageHead, compact, day, int, money, pct, stamp } from '../ui';
import { SHOWCASE } from '../showcase';

type Filters = { country: string; start: string; end: string };
const empty: Filters = { country: '', start: '', end: '' };
const TITLES: Record<MetricKey, string> = { gross_positive_gbp: 'Gross positive merchandise', negative_adjustments_gbp: 'Negative adjustments', net_recorded_gbp: 'Net recorded merchandise' };

function HeroNumber({ value }: { value: number }) {
  const fmt = (v: number) => new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 }).format(Math.floor(v));
  const pence = Math.round((Math.abs(value) % 1) * 100).toString().padStart(2, '0');
  return <div className="hero-number" aria-label={money(value)}><span className="currency">£</span><CountUp value={value} format={fmt} duration={2.2} /><span className="pence">.{pence}</span></div>;
}

export function OverviewView({ explore, countries, setCountries }: { explore: (m: MetricKey) => void; countries: string[]; setCountries: (c: string[]) => void }) {
  const [draft, setDraft] = useState<Filters>(empty);
  const [filters, setFilters] = useState<Filters>(empty);
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
  const { data, error, loading, reload } = useApi<Overview>(`/api/overview?${query}`);
  const insights = useApi<Insights>('/api/insights').data;
  const filtered = !!query;
  if (data?.filters?.countries?.length && data.filters.countries.length > countries.length) setTimeout(() => setCountries(data.filters.countries), 0);

  const points = useMemo(() => (data?.daily ?? []).map(d => ({ t: Date.parse(`${d.date.slice(0, 10)}T00:00:00Z`), v: Number(d.net_recorded_gbp) })), [data]);
  const markers = useMemo(() => filtered || !insights?.anomalies ? [] : insights.anomalies.anomalies.map(a => ({
    t: Date.parse(`${a.date}T00:00:00Z`), v: a.net, kind: a.direction, label: `${a.direction === 'spike' ? '▲' : '▼'} anomaly, z = ${a.z.toFixed(1)}` })), [insights, filtered]);
  const apply = (e: FormEvent) => { e.preventDefault(); setFilters({ ...draft }); };
  const net = Number(data?.metrics.net_recorded_gbp ?? 0);
  const s = data?.snapshot;

  return <>
    <PageHead index="§01" kicker="Pulse · UK online retail, Dec 2009 – Dec 2011" title="Every row," accent="accounted for." lede="1,067,371 real invoice lines, traced from the source workbook through a Spark + Delta lakehouse to the numbers on this page."
      meta={s ? <><span className="mode mode-historical"><span className="mode-dot" />{s.mode}</span><span className="meta-line">snapshot <strong>{s.id.slice(0, 24)}</strong></span><span className="meta-line">published {stamp(s.published_at)}</span></> : null} />

    <form className="filters" onSubmit={apply} aria-label="Filter the overview">
      <fieldset>
        <label className="field">Country<select value={draft.country} onChange={e => setDraft({ ...draft, country: e.target.value })}><option value="">All countries</option>{countries.map(c => <option key={c}>{c}</option>)}</select></label>
        <label className="field">From<input type="date" min="2009-12-01" max="2011-12-09" value={draft.start} onChange={e => setDraft({ ...draft, start: e.target.value })} /></label>
        <label className="field">Through<input type="date" min={draft.start || '2009-12-01'} max="2011-12-09" value={draft.end} onChange={e => setDraft({ ...draft, end: e.target.value })} /></label>
        <button className="button-primary" type="submit" disabled={loading}>Apply</button>
        <button className="button-ghost" type="button" onClick={() => { setDraft(empty); setFilters(empty); }}>Reset</button>
      </fieldset>
      <span className="filter-active">{filtered ? (SHOWCASE ? 'Computed live in your browser · DuckDB-WASM' : 'Filtered · live DuckDB query') : 'All published records'}</span>
    </form>

    {loading && !data ? <Loading label={filtered && SHOWCASE ? 'Starting DuckDB in your browser and scanning 1.06M lines…' : undefined} /> : error ? <ErrorState message={error} retry={reload} /> : data ? <>
      <motion.section className="hero" initial={{ opacity: 0, y: 30, scale: .985 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ duration: .9, ease: [0.22, 1, 0.36, 1] }}>
        <div className="hero-grid">
          <div>
            <span className="eyebrow"><b>●</b> Net recorded merchandise · {filtered ? 'filtered selection' : `${day(s?.date_min)} → ${day(s?.date_max)}`}</span>
            <HeroNumber value={net} key={net} />
            <p className="hero-sub">Recorded invoice value after returns and adjustments. Not audited revenue or cash collected: the source has no costs, settlement or refunds ledger.</p>
          </div>
          <div className="equation">
            {(['gross_positive_gbp', 'negative_adjustments_gbp', 'net_recorded_gbp'] as MetricKey[]).map((k, i) => <button key={k} className={`equation-row ${i === 2 ? 'total' : ''}`} onClick={() => explore(k)} title="Trace this number to its source rows">
              <span className="op">{i === 0 ? ' ' : i === 1 ? '+' : '='}</span><span className="label">{TITLES[k]}</span><span className="val">{money(data.metrics[k])}</span></button>)}
            <span className="small-muted" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>Click any line to trace it to invoice rows <span style={{ width: 14, display: 'inline-flex' }}><Icon name="arrow" /></span></span>
          </div>
        </div>
        <div className="ticker" aria-hidden="true"><div className="ticker-track">{[0, 1].map(k => <span key={k} style={{ display: 'inline-flex', gap: 48 }}>
          <span><b>{int(s?.source_rows)}</b>source rows, each with a destination</span>
          <span><b>{int(s?.accepted_rows)}</b>accepted · {int(s?.excluded_rows)} excluded · {int(s?.quarantined_rows)} quarantined</span>
          <span><b>{Object.keys(s?.delta_versions ?? {}).length}</b>Delta tables versioned</span>
          <span><b>£0.00</b>difference between Kafka replay and batch</span>
          <span><b>21/21</b>fault-injection drill checks</span>
          {insights?.return_model ? <span><b>AUC {insights.return_model.models[insights.return_model.selected].auc.toFixed(3)}</b>repeat-purchase model, time-split test</span> : null}
          {insights?.basket ? <span><b>{int(insights.basket.rules_found)}</b>association rules mined from {int(insights.basket.invoices)} baskets</span> : null}
          <span><b>23.4×</b>faster daily mart vs fact scan</span>
        </span>)}</div></div>
      </motion.section>

      <div className="bento">
        <Card span={8}>
          <CardHead eyebrow={<><b>01</b> · sales over time</>} title="Every trading day, in context"><span className="chart-legend"><span><i />Net recorded</span>{markers.length ? <span><i style={{ background: 'var(--lime)', width: 8, height: 8, borderRadius: 8 }} />Statistical anomaly</span> : null}</span></CardHead>
          <TimeSeries points={points} markers={markers} label="Daily net recorded merchandise in GBP. Gaps are days with no source records." />
          <p className="note">Calendar axis; days without source records are left blank, never imputed as zero. {markers.length ? 'Markers come from a robust weekday-seasonal anomaly model (see Signals).' : ''}</p>
        </Card>
        {data.comparison ? <Card span={4} delay={.08}>
          <CardHead eyebrow={<><b>02</b> · comparable windows</>} title="Last 28 days vs the 28 before" />
          {(() => { const c = data.comparison!; const up = Number(c.change_gbp) >= 0; return <div style={{ display: 'grid', gap: 22 }}>
            <div className="stat"><span className="stat-label">Current · {day(c.current_start)} → {day(c.current_end)}</span><span className="stat-value"><CountUp value={Number(c.current_net_gbp)} format={v => compact(v)} /></span></div>
            <div className="stat"><span className="stat-label">Previous · {day(c.previous_start)} → {day(c.previous_end)}</span><span className="stat-value sm" style={{ color: 'var(--muted)' }}>{compact(c.previous_net_gbp)}</span></div>
            <div className="stat"><span className="stat-label">Observed change</span><span className={`stat-value ${up ? 'delta-up' : 'delta-down'}`}>{up ? '+' : ''}{c.change_pct == null ? money(c.change_gbp) : `${Number(c.change_pct).toFixed(1)}%`}</span><span className="small-muted">{up ? '+' : ''}{money(c.change_gbp)}</span></div>
            <p className="note" style={{ margin: 0 }}>{c.limitation}</p>
          </div>; })()}
        </Card> : <Card span={4} delay={.08}><CardHead eyebrow={<><b>02</b> · selection</>} title="Scope of this view" /><div className="stat-row">
          <div className="stat"><span className="stat-label">Accepted lines</span><span className="stat-value sm">{int(data.metrics.accepted_rows ?? data.metrics.rows)}</span></div>
          <div className="stat"><span className="stat-label">Positive units</span><span className="stat-value sm">{int(data.metrics.positive_units)}</span></div></div>
          <p className="note">Period comparison is shown for the unfiltered snapshot.</p></Card>}

        <Card span={6}><CardHead eyebrow={<><b>03</b> · product contribution</>} title="What drives recorded sales" />
          <Bars rows={data.products.slice(0, 8).map(p => ({ label: insights?.product_names?.[p.stock_code] ?? (p.description || 'Description unavailable'), sub: p.stock_code, value: Number(p.net_recorded_gbp) }))} />
          {insights?.product_names ? <p className="note">Names are each product's most frequent description on sales lines; free-text damage notes recorded against a stock code are not used as names.</p> : null}</Card>
        <Card span={6} delay={.08}><CardHead eyebrow={<><b>04</b> · geography</>} title="Where sales are recorded" />
          <Bars rows={data.countries.slice(0, 8).map(c => ({ label: c.country || 'Unknown country', sub: `${int(c.rows)} lines`, value: Number(c.net_recorded_gbp) }))} /></Card>

        {(() => { const known = Number(data.metrics.known_customer_rows || 0), scoped = Number(data.metrics.accepted_rows ?? data.metrics.rows ?? s?.accepted_rows ?? 0); const share = scoped ? known / scoped : 0; const C = 2 * Math.PI * 52;
          return <Card span={4}><CardHead eyebrow={<><b>05</b> · identity coverage</>} title="Who we can analyse" />
            <div style={{ display: 'flex', alignItems: 'center', gap: 22 }}>
              <svg viewBox="0 0 120 120" width="130" height="130" aria-hidden="true"><circle cx="60" cy="60" r="52" fill="none" stroke="var(--surface-3)" strokeWidth="10" />
                <motion.circle cx="60" cy="60" r="52" fill="none" stroke="var(--lime)" strokeWidth="10" strokeLinecap="round" transform="rotate(-90 60 60)" strokeDasharray={C}
                  initial={{ strokeDashoffset: C }} whileInView={{ strokeDashoffset: C * (1 - share) }} viewport={{ once: true }} transition={{ duration: 1.6, ease: [0.22, 1, 0.36, 1] }} />
                <text x="60" y="66" textAnchor="middle" fill="var(--text)" fontFamily="var(--font-mono)" fontSize="20">{pct(share, 0)}</text></svg>
              <p className="note" style={{ margin: 0 }}>{int(known)} of {int(scoped)} accepted lines carry a customer identifier. The rest stay in sales totals; customer analytics covers identified buyers only.</p>
            </div>
            <div className="stat-row" style={{ marginTop: 26, gridTemplateColumns: '1fr 1fr' }}>
              <div className="stat"><span className="stat-label">Accepted lines</span><span className="stat-value sm"><CountUp value={scoped} /></span></div>
              <div className="stat"><span className="stat-label">Positive units</span><span className="stat-value sm"><CountUp value={Number(data.metrics.positive_units)} /></span></div>
              <div className="stat"><span className="stat-label">Countries</span><span className="stat-value sm">{int(data.countries.length)}</span></div>
              {insights?.population ? <div className="stat"><span className="stat-label">Identified buyers</span><span className="stat-value sm"><CountUp value={insights.population.identified_buyers} /></span></div> : null}
            </div>
            {insights?.segmentation && !filtered ? <div style={{ marginTop: 26 }}><span className="eyebrow">Buyer segments · value share</span>
              <div className="share-bar" style={{ height: 12, marginTop: 10 }}>{insights.segmentation.segments.map((sg, i) => <motion.span key={sg.segment} title={`${sg.segment}: ${pct(sg.share_of_value, 0)} of value`}
                style={{ display: 'block', background: ['var(--signal)', 'var(--lime)', 'var(--sky)', 'var(--amber)', 'var(--violet)'][i % 5] }} initial={{ width: 0 }} whileInView={{ width: `${sg.share_of_value * 100}%` }} viewport={{ once: true }} transition={{ duration: 1, delay: i * .1 }} />)}</div>
              <div className="chart-legend" style={{ marginTop: 10 }}>{insights.segmentation.segments.map((sg, i) => <span key={sg.segment}><i style={{ background: ['var(--signal)', 'var(--lime)', 'var(--sky)', 'var(--amber)', 'var(--violet)'][i % 5] }} />{sg.segment} {pct(sg.share_of_value, 0)}</span>)}</div>
              <a className="button-text" href="#/customers" style={{ display: 'inline-block', marginTop: 14 }}>Explore customer segments →</a></div> : null}
          </Card>; })()}

        {data.cohorts?.length ? (() => {
          const cohorts = [...new Set(data.cohorts.map(c => c.cohort_month.slice(0, 7)))].sort();
          const maxIdx = Math.min(12, Math.max(...data.cohorts.map(c => c.month_index)));
          const idx = Array.from({ length: maxIdx }, (_, i) => i + 1);
          const lookup = new Map(data.cohorts.map(c => [`${c.cohort_month.slice(0, 7)}|${c.month_index}`, c]));
          return <Card span={8} delay={.08}><CardHead eyebrow={<><b>06</b> · cohort retention</>} title="Which buyers come back, month by month" />
            <Heatmap rows={cohorts} cols={idx.map(String)} rowLabel="Cohort" colLabel="Months after first observed purchase →" color="var(--lime)" format={v => pct(v)}
              value={(r, c) => { const row = lookup.get(`${cohorts[r]}|${idx[c]}`); return row?.retention_rate == null ? null : Number(row.retention_rate); }}
              tip={(r, c) => { const row = lookup.get(`${cohorts[r]}|${idx[c]}`)!; return `${pct(row.retention_rate)} (${int(row.active_customers)}/${int(row.cohort_customers)})${row.is_complete_month ? '' : ' · partial month'}`; }}
              label="Cohort retention heatmap: share of each first-purchase cohort active in later months" />
            <p className="note">Positive merchandise purchases by identified buyers. A cohort starts at the first purchase observed in this source, which is not proof of acquisition. Blank cells are unobserved, not zero.</p></Card>;
        })() : null}
      </div>
    </> : null}
  </>;
}
