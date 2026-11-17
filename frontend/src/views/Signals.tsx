import { useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import type { Insights } from '../types';
import { Heatmap, LineChart, TimeSeries } from '../charts';
import { Card, CardHead, PageHead, compact, int, money, money0, pct, sci } from '../ui';

const DOW = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function SignalsView({ data }: { data: Insights }) {
  const a = data.anomalies, st = data.statistics;
  const [pick, setPick] = useState(a.anomalies[0]?.date ?? '');
  const chosen = a.anomalies.find(x => x.date === pick);
  const points = useMemo(() => a.series.map(s => ({ t: Date.parse(`${s.date}T00:00:00Z`), v: s.net, expected: s.expected })), [a]);
  const markers = a.anomalies.map(x => ({ t: Date.parse(`${x.date}T00:00:00Z`), v: x.net, kind: x.direction, label: `z = ${x.z.toFixed(1)}` }));
  const hours = [...new Set(st.heatmap.map(h => h.hour))].sort((x, y) => x - y);
  const dows = [...new Set(st.heatmap.map(h => h.dow))].sort();
  const heat = new Map(st.heatmap.map(h => [`${h.dow}|${h.hour}`, h]));
  const iv = st.invoice_value, wd = st.weekday;
  return <>
    <PageHead index="§04" kicker="Signals · anomalies and statistical tests" title="Separate signal from" accent="noise." lede="Robust anomaly detection on daily sales, with the invoice lines that caused each one, and the hypothesis tests behind the patterns people quote in meetings."
      meta={<><span className="chip alert"><span className="dot" />{a.anomalies.length} anomalous days</span><span className="meta-line">threshold |z| &gt; <strong>{a.threshold}</strong></span></>} />
    <div className="bento">
      <Card span={12}><CardHead eyebrow={<><b>01</b> · anomaly detection</>} title="Daily net against its weekday-seasonal expectation"><span className="chart-legend"><span><i />Observed</span><span><i style={{ background: 'var(--muted)' }} />Expected (8-week same-weekday median)</span></span></CardHead>
        <TimeSeries points={points} markers={markers} showExpected height={300} label="Daily net with expected baseline and flagged anomalies" /></Card>
      <Card span={5}><CardHead eyebrow={<><b>02</b> · flagged days</>} title="Pick a day to explain it" />
        <div className="table-scroll" style={{ maxHeight: 420 }}><table><thead><tr><th>Day</th><th className="numeric">Net</th><th className="numeric">Expected</th><th className="numeric">z</th></tr></thead>
          <tbody>{a.anomalies.map(x => <tr key={x.date} onClick={() => setPick(x.date)} style={{ cursor: 'pointer', background: x.date === pick ? 'var(--surface-3)' : undefined }}>
            <td className="mono">{x.date}<span className="table-muted">{new Date(`${x.date}T00:00:00Z`).toLocaleDateString('en-GB', { weekday: 'long', timeZone: 'UTC' })}</span></td>
            <td className="numeric">{compact(x.net)}</td><td className="numeric" style={{ color: 'var(--muted)' }}>{compact(x.expected)}</td>
            <td className="numeric" style={{ color: x.direction === 'spike' ? 'var(--lime)' : 'var(--rose)' }}>{x.z > 0 ? '+' : ''}{x.z.toFixed(1)}</td></tr>)}</tbody></table></div></Card>
      <Card span={7} delay={.06}>
        <AnimatePresence mode="wait">{chosen ? <motion.div key={chosen.date} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} transition={{ duration: .3 }}>
          <CardHead eyebrow={<><b>03</b> · root cause</>} title={`${chosen.direction === 'spike' ? 'Spike' : 'Drop'} on ${chosen.date}`}><span className={`chip ${chosen.direction === 'spike' ? 'good' : 'alert'}`}>{chosen.direction} · z {chosen.z.toFixed(1)}</span></CardHead>
          <div className="stat-row" style={{ marginBottom: 18 }}>
            <div className="stat"><span className="stat-label">Observed net</span><span className="stat-value sm">{money0(chosen.net)}</span></div>
            <div className="stat"><span className="stat-label">Expected</span><span className="stat-value sm" style={{ color: 'var(--muted)' }}>{money0(chosen.expected)}</span></div>
            <div className="stat"><span className="stat-label">Invoices</span><span className="stat-value sm">{int(chosen.invoices)}</span></div>
            <div className="stat"><span className="stat-label">Largest buyer share</span><span className="stat-value sm">{pct(chosen.top_customer.share, 0)}</span></div>
          </div>
          <table><thead><tr><th>Largest lines that day</th><th>Invoice · customer</th><th className="numeric">Line GBP</th></tr></thead>
            <tbody>{chosen.drivers.map((d, i) => <tr key={i}><td>{d.description}<span className="table-muted mono">{d.stock_code} · {d.country}</span></td><td className="mono">{d.invoice}<span className="table-muted">{d.customer_id ?? 'unidentified'}</span></td>
              <td className="numeric amount" style={{ color: d.line_amount < 0 ? 'var(--rose)' : undefined }}>{money(d.line_amount)}</td></tr>)}</tbody></table>
          <p className="note">{chosen.top_customer.share > .25 ? `One buyer (${chosen.top_customer.customer_id}) accounts for ${pct(chosen.top_customer.share, 0)} of the day: a single wholesale order, not a demand shift.` : 'No single buyer dominates: the move is spread across many orders, which is more likely a genuine demand or calendar effect.'}</p>
        </motion.div> : <p className="note">No anomalies flagged.</p>}</AnimatePresence>
        <p className="note">{a.method}</p></Card>
    </div>

    <h2 className="section-title">Hypothesis tests</h2>
    <div className="bento">
      <Card span={4}><CardHead eyebrow={<><b>04</b> · weekday effect</>} title="Does the day of week matter?" />
        <div className="stat-row"><div className="stat"><span className="stat-label">Kruskal–Wallis H</span><span className="stat-value sm">{wd.H.toFixed(1)}</span></div>
          <div className="stat"><span className="stat-label">p-value</span><span className="stat-value sm" style={{ color: 'var(--lime)' }}>{sci(wd.p_value)}</span></div>
          <div className="stat"><span className="stat-label">Effect size ε²</span><span className="stat-value sm">{wd.epsilon_squared.toFixed(2)}</span></div></div>
        <div className="bar-list" style={{ marginTop: 16 }}>{wd.by_weekday.map(w => { const max = Math.max(...wd.by_weekday.map(x => x.median_net)); return <div className="bar-row" key={w.dow}>
          <span className="bar-name">{DOW[w.dow]} <span className="table-muted mono" style={{ display: 'inline' }}>· {w.days} days</span></span><span className="bar-val">{compact(w.median_net)}</span>
          <div className="bar-track"><motion.div className="bar-fill" initial={{ scaleX: 0 }} whileInView={{ scaleX: w.median_net / max }} viewport={{ once: true }} transition={{ duration: .9 }} /></div></div>; })}</div>
        <p className="note">Median daily net by weekday. {wd.note}</p></Card>
      <Card span={4} delay={.06}><CardHead eyebrow={<><b>05</b> · year on year</>} title="Did baskets get bigger?" />
        <div className="stat-row"><div className="stat"><span className="stat-label">Median order, yr 1</span><span className="stat-value sm" style={{ color: 'var(--muted)' }}>{money0(iv.median_y1)}</span></div>
          <div className="stat"><span className="stat-label">Median order, yr 2</span><span className="stat-value sm">{money0(iv.median_y2)}</span></div>
          <div className="stat"><span className="stat-label">Mann–Whitney p</span><span className="stat-value sm" style={{ color: 'var(--lime)' }}>{sci(iv.p_value)}</span></div></div>
        <div style={{ margin: '22px 0 6px' }}><span className="stat-label">Bootstrap 95% CI, change in mean order value</span>
          {(() => { const [lo, hi] = iv.mean_diff_ci95, d = iv.mean_y2 - iv.mean_y1, M = Math.max(Math.abs(lo), Math.abs(hi)) * 1.3, sx = (v: number) => 50 + v / M * 50;
            return <svg viewBox="0 0 100 26" style={{ width: '100%', height: 60 }} aria-label={`Mean difference ${money0(d)}, 95% CI ${money0(lo)} to ${money0(hi)}`}>
              <line x1="50" x2="50" y1="2" y2="24" stroke="var(--faint)" strokeDasharray="1 1.5" strokeWidth=".4" />
              <motion.rect y="11" height="4" rx="2" fill="var(--signal)" opacity=".35" initial={{ x: 50, width: 0 }} whileInView={{ x: sx(lo), width: sx(hi) - sx(lo) }} viewport={{ once: true }} transition={{ duration: 1 }} />
              <circle cx={sx(d)} cy="13" r="2.2" fill="var(--signal)" /><text x={sx(d)} y="7" textAnchor="middle" style={{ fontSize: 4 }} fill="var(--text)">+{money0(d)}</text>
              <text x="50" y="25.5" textAnchor="middle" style={{ fontSize: 3 }}>no change</text></svg>; })()}</div>
        <p className="note">{int(iv.invoices_y1)} vs {int(iv.invoices_y2)} invoices, Dec–Nov years. {iv.note}</p></Card>
      <Card span={4} delay={.12}><CardHead eyebrow={<><b>06</b> · concentration</>} title="How lopsided is value?" />
        <LineChart label="Lorenz-style cumulative share of value for products and customers" domain={{ x: [0, 1], y: [0, 1] }} diagonal height={220} xLabel="Share of items, ranked" xFmt={v => pct(v, 0)} yFmt={v => pct(v, 0)}
          series={[{ name: `Products · Gini ${st.pareto.products.gini.toFixed(2)}`, color: 'var(--signal)', points: [{ x: 0, y: 0 }, ...st.pareto.products.curve] }, { name: `Customers · Gini ${st.pareto.customers.gini.toFixed(2)}`, color: 'var(--sky)', points: [{ x: 0, y: 0 }, ...st.pareto.customers.curve] }]} />
        <p className="note">The top 20% of products earn {pct(st.pareto.products.top_20pct_share, 0)} of identified value; the top 20% of buyers spend {pct(st.pareto.customers.top_20pct_share, 0)}. A textbook Pareto business.</p></Card>
      <Card span={12}><CardHead eyebrow={<><b>07</b> · trading rhythm</>} title="When orders land: weekday × hour" />
        <Heatmap rows={dows.map(d => DOW[d])} cols={hours.map(h => `${h}`)} rowLabel="Day" colLabel="Hour of invoice (UK civil time) →" format={v => compact(v)}
          value={(r, c) => heat.get(`${dows[r]}|${hours[c]}`)?.net ?? null} tip={(r, c) => { const h = heat.get(`${dows[r]}|${hours[c]}`)!; return `${compact(h.net)} · ${int(h.invoices)} invoices`; }}
          label="Heatmap of net sales by weekday and hour" />
        <p className="note">Trading concentrates on weekday late mornings and early afternoons; Saturday is effectively closed in this source. Useful for staffing, warehouse cut-offs and when to send campaigns.</p></Card>
    </div>
  </>;
}
