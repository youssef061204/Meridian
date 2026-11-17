import { motion } from 'motion/react';
import type { Lineage, MetricKey, Snapshot, Trust } from '../types';
import { CorrectionPanel, RecoveryPanel } from '../Recovery';
import { Card, CardHead, PageHead, day, int, money, stamp } from '../ui';

export const METRIC_TITLES: Record<MetricKey, string> = { gross_positive_gbp: 'Gross positive merchandise', negative_adjustments_gbp: 'Negative adjustments', net_recorded_gbp: 'Net recorded merchandise' };

function SnapshotDetails({ snapshot }: { snapshot: Snapshot }) {
  return <dl className="details">
    <div><dt>Snapshot</dt><dd className="mono">{snapshot.id}</dd></div>
    <div><dt>Published</dt><dd>{stamp(snapshot.published_at)}</dd></div>
    <div><dt>Source period</dt><dd>{day(snapshot.date_min)} → {day(snapshot.date_max)}</dd></div>
    <div><dt>Source SHA-256</dt><dd className="mono">{snapshot.source_sha256}</dd></div>
    <div style={{ gridColumn: '1 / -1' }}><dt>Delta table versions</dt><dd>{Object.entries(snapshot.delta_versions || {}).map(([n, v]) => <span className="version" key={n}>{n} v{v}</span>)}</dd></div>
  </dl>;
}

function Pipeline({ s }: { s: Snapshot }) {
  const stages = [
    { k: 'XLSX', name: 'Source', sub: 'UCI workbook, SHA-256 pinned', count: s.source_rows },
    { k: 'BRZ', name: 'Bronze', sub: 'immutable rows + identity', count: s.received_deliveries as number ?? s.source_rows },
    { k: 'SLV', name: 'Silver', sub: 'typed, classified, quarantined', count: s.accepted_rows, extra: `${int(s.quarantined_rows)} quarantined` },
    { k: 'GLD', name: 'Gold', sub: `${Object.keys(s.delta_versions || {}).length - 2} marts & dimensions`, count: s.accepted_rows },
    { k: 'API', name: 'Serving', sub: 'validated snapshot only', count: null, extra: 'publication gate' },
  ];
  return <div className="pipeline">
    <div className="pipe-flow" aria-hidden="true"><svg viewBox="0 0 100 2" preserveAspectRatio="none"><line className="flow-line" x1="0" x2="100" y1="1" y2="1" vectorEffect="non-scaling-stroke" /><line className="flow-dash" x1="0" x2="100" y1="1" y2="1" vectorEffect="non-scaling-stroke" /></svg></div>
    {stages.map((st, i) => <motion.div className="pipe-stage" key={st.k} initial={{ opacity: 0, y: 18 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: i * .12, duration: .6 }}>
      <motion.div className="pipe-node" whileHover={{ scale: 1.08, rotate: -3 }} style={{ borderColor: i === 4 ? 'var(--lime)' : undefined, color: i === 4 ? 'var(--lime)' : undefined }}>{st.k}</motion.div>
      <strong>{st.name}</strong><span>{st.sub}</span>{st.count != null ? <div className="count">{int(st.count)}</div> : null}{st.extra ? <span>{st.extra}</span> : null}
    </motion.div>)}
  </div>;
}

export function ReliabilityView({ data }: { data: Trust }) {
  const s = data.snapshot;
  const accounted = Number(s.accepted_rows) + Number(s.quarantined_rows) + Number(s.excluded_rows);
  const reconciled = accounted === Number(s.source_rows);
  return <>
    <PageHead index="§06" kicker="Reliability · lakehouse, streaming and recovery" title="Break it. Then" accent="prove it's fixed." lede="Spark + Delta medallion layers, a Kafka replay with seeded incidents, and a publication gate that refuses to serve numbers it can't reconcile."
      meta={<span className={`chip ${reconciled ? 'good' : 'alert'}`}><span className="dot" />{reconciled ? 'counts reconciled' : 'count discrepancy'}</span>} />
    <Card><CardHead eyebrow={<><b>01</b> · medallion lineage</>} title="Every original row has a destination" /><Pipeline s={s} />
      <p className="note">{int(s.source_rows)} source rows = {int(s.accepted_rows)} accepted + {int(s.excluded_rows)} excluded by named rules + {int(s.quarantined_rows)} quarantined. Repeated deliveries ({int(s.repeat_deliveries)}) are counted separately and never add to sales.</p></Card>
    <h2 className="section-title">Streaming incident and recovery</h2>
    <RecoveryPanel />
    <h2 className="section-title">Historical batch publication</h2>
    {s.reconciliation_totals ? <Card><CardHead eyebrow={<><b>02</b> · signed monetary reconciliation</>} title="Parsed value, across every disposition" />
      <div className="table-scroll"><table><thead><tr><th>Disposition</th><th className="numeric">Signed parsed amount</th></tr></thead><tbody>
        {([['All original rows with a parseable amount', s.reconciliation_totals.source_parsed_amount_gbp], ['Accepted merchandise lines', s.reconciliation_totals.accepted_parsed_amount_gbp], ['Quarantined records', s.reconciliation_totals.quarantined_parsed_amount_gbp], ['Excluded by named business rules', s.reconciliation_totals.excluded_parsed_amount_gbp]] as const).map(([l, v]) => <tr key={l}><td>{l}</td><td className="numeric amount">{money(v)}</td></tr>)}
      </tbody></table></div>
      <div className="unparseable-amounts"><span>Original rows whose amount could not be parsed</span><strong className="mono">{int(s.reconciliation_totals.unparseable_amount_rows)}</strong></div></Card> : null}
    <div className="two-column">
      <Card><CardHead eyebrow={<><b>03</b> · validated publication</>} title="Serving snapshot"><span className="mode mode-historical"><span className="mode-dot" />{s.mode}</span></CardHead><SnapshotDetails snapshot={s} /></Card>
      <Card><CardHead eyebrow={<><b>04</b> · persisted evidence</>} title="Publication state" /><pre className="json-evidence">{JSON.stringify(data.publication, null, 2)}</pre></Card>
    </div>
    <Card><CardHead eyebrow={<><b>05</b> · execution history</>} title="Recorded pipeline runs" />
      {data.runs.length ? <div className="run-list">{data.runs.slice(0, 12).map((run, i) => <details key={String(run.run_id || run.id || i)}><summary><span className="mono">{String(run.run_id || run.id || `Run ${i + 1}`)}</span><span className="run-state">{String(run.status || run.state || 'evidence')}</span></summary><pre>{JSON.stringify(run, null, 2)}</pre></details>)}</div> : <div className="empty compact">No run manifests have been recorded.</div>}</Card>
  </>;
}

export function LineageView({ data, metric, setMetric }: { data: Lineage; metric: MetricKey; setMetric: (m: MetricKey) => void }) {
  return <>
    <PageHead index="§07" kicker="Lineage · from KPI to invoice line" title="Trace any number to its" accent="source." lede="The SQL that defines each headline metric, and the exact workbook coordinates of the rows that contribute to it."
      meta={<label className="field">Metric<select value={metric} onChange={e => setMetric(e.target.value as MetricKey)}>{Object.entries(METRIC_TITLES).map(([k, n]) => <option key={k} value={k}>{n}</option>)}</select></label>} />
    <div className="bento">
      <Card span={5}><CardHead eyebrow={<><b>01</b> · definition</>} title={METRIC_TITLES[metric]} /><pre className="sql-code"><code>{data.sql}</code></pre>
        <p className="note">GBP, decimal arithmetic, half-up per line, named merchandise rules. No settlement or cost data exists in the source.</p></Card>
      <Card span={7} delay={.06}><CardHead eyebrow={<><b>02</b> · lineage anchor</>} title="Published evidence" /><SnapshotDetails snapshot={data.snapshot} /></Card>
    </div>
    <Card><CardHead eyebrow={<><b>03</b> · contributing source rows</>} title="From the KPI to the invoice line"><span className="small-muted">Showing {int(data.rows.length)} of {int(data.total_rows)} rows</span></CardHead>
      <div className="table-scroll" style={{ maxHeight: 560 }}><table><thead><tr><th>Source coordinate</th><th>Invoice / product</th><th>Source time</th><th>Customer</th><th className="numeric">Qty</th><th className="numeric">Unit</th><th className="numeric">Line</th><th>Class</th></tr></thead>
        <tbody>{data.rows.map(r => <tr key={r.line_id}><td><span className="mono">{r.source_sheet}:{r.source_row}</span><span className="table-muted mono" title={r.line_id}>{r.line_id.slice(0, 12)}…</span></td>
          <td><span className="mono">{r.invoice}</span><span className="table-muted">{r.stock_code} · {r.description}</span></td><td className="nowrap mono">{r.event_time?.replace('T', ' ').slice(0, 16)}</td>
          <td>{r.customer_id || <span className="unknown">unknown</span>}</td><td className="numeric">{int(r.quantity)}</td><td className="numeric">{money(r.unit_price)}</td><td className="numeric amount">{money(r.line_amount)}</td>
          <td><span className="classification">{r.classification.replace('_', ' ')}</span><span className="table-muted">{r.country}</span></td></tr>)}</tbody></table></div></Card>
    <CorrectionPanel />
  </>;
}
