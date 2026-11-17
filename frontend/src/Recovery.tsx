import { useCallback, useEffect, useState } from 'react';
import type { CorrectionData, ReplayState, Job } from './types';
import { SHOWCASE, showcaseAllows } from './showcase';

const gbp = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', maximumFractionDigits: 2 });
const integer = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
const money = (v: unknown) => v == null ? '—' : gbp.format(Number(v));
const num = (v: unknown) => v == null ? '—' : integer.format(Number(v));
const time = (v?: string | null) => v ? new Date(v).toLocaleTimeString('en-GB') : '—';
const age = (s: number | null) => s == null ? 'unknown' : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;

const ACTIONS: { kind: string; label: string; help: string }[] = [
  { kind: 'incident', label: 'Inject controlled incident', help: 'Seeded synthetic events: repeat deliveries, malformed and schema-breaking envelopes, an identity conflict, 12 withheld real rows arriving late, and a revision-2 correction. The real consumer processes them and attempts publication.' },
  { kind: 'repair', label: 'Repair and reprocess', help: 'Apply late arrivals through the durable backfill, re-run the backfill to prove it is idempotent, then publish a validated snapshot.' },
  { kind: 'interrupted-publish', label: 'Interrupt a publish', help: 'Start a multi-stage publication and kill it after two table exports. The serving pointer must not move.' },
  { kind: 'reset', label: 'Reset to baseline', help: 'Delta RESTORE every replay table to the recorded baseline version and point serving back. History and evidence are kept.' },
];

async function post(kind: string) {
  const response = await fetch('/api/demo/jobs', { method: 'POST', headers: { 'content-type': 'application/json', 'X-Meridian-Demo': 'local' }, body: JSON.stringify({ kind }) });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || `Request failed (${response.status})`); }
  return response.json() as Promise<Job>;
}

function JobProgress({ job }: { job: Job }) {
  return <div className="job-progress" aria-live="polite"><div className="job-title"><strong>{job.kind}</strong><span className={`status ${job.state === 'passed' ? 'status-good' : job.state === 'failed' ? 'status-alert' : 'status-busy'}`}>{job.state}</span><span className="small-muted mono">{job.job_id}</span></div>
    <ol className="stage-list">{job.stages.map((s, i) => <li key={i} className={`stage stage-${s.state}`}><span className="stage-dot" aria-hidden="true" /><span>{s.stage.replace(/_/g, ' ')}</span><span className="small-muted">{s.state}{s.seconds != null ? ` · ${s.seconds.toFixed(1)} s` : ''}{s.attempts > 1 ? ` · ${s.attempts} attempts` : ''}</span></li>)}
      {job.state === 'queued' ? <li className="stage"><span className="stage-dot" />waiting for the local worker</li> : null}</ol>
    {job.error ? <p className="job-error">{job.error}</p> : null}</div>;
}

export function RecoveryPanel() {
  const [data, setData] = useState<ReplayState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { const r = await fetch('/api/replay'); if (!r.ok) throw new Error(`Replay state unavailable (${r.status})`); setData(await r.json()); setError(''); }
    catch (e) { setError(e instanceof Error ? e.message : 'Replay state unavailable'); }
  }, []);
  const active = data?.jobs?.find(j => j.state === 'queued' || j.state === 'running');
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (!active) return; const t = window.setInterval(load, 2000); return () => window.clearInterval(t); }, [active, load]);
  if (error) return <section className="error-state" role="alert"><h2>Replay evidence unavailable</h2><p>{error}</p><button className="button-primary" onClick={load}>Try again</button></section>;
  if (!data) return <div className="loading-state"><span className="spinner" /><h2>Reading replay evidence…</h2></div>;
  if (data.state !== 'ready') return <section className="callout"><span className="eyebrow">REPLAY NOT INITIALISED</span><h2>No replay generation yet</h2><p>{data.message}</p></section>;
  const s = data.snapshot, status = data.status, rec = s?.reconciliation;
  const latestJob = data.jobs?.[0];
  const workerReady = data.worker?.state === 'ready' || data.worker?.state === 'busy';
  const run = async (kind: string) => { setBusy(true); try { await post(kind); await load(); } catch (e) { setError(e instanceof Error ? e.message : 'Job request failed'); } finally { setBusy(false); } };
  const retained = data.serving_state.startsWith('last validated');
  const t = data.timeline;
  return <>
    <section className="panel replay-head"><div className="panel-heading"><div><span className="eyebrow">KAFKA REPLAY · SPARK STRUCTURED STREAMING · DELTA</span><h2>Replay, incidents and recovery</h2></div><div className="badge-row"><span className="mode mode-replay"><span className="mode-dot" />replay</span><span className="mode mode-synthetic"><span className="mode-dot" />synthetic incidents</span></div></div>
      <p className="panel-footnote">The {data.config.profile} slice of historical rows is re-emitted through a local single-node Kafka topic on an accelerated clock. This is not a live retailer integration. Incident events are seeded synthetic events on a separate topic, never additional observed sales.</p>
      <div className="replay-status">
        <div><span>Serving state</span><strong className={retained ? 'text-alert' : 'text-good'}>{data.serving_state}</strong></div>
        <div><span>Replay snapshot</span><strong className="mono">{s?.id ?? 'none'}</strong><small>published {age(data.snapshot_age_seconds)} ago (publication age, not data age)</small></div>
        <div><span>Batch reconciliation</span><strong className={rec?.state === 'reconciled' ? 'text-good' : 'text-alert'}>{rec?.state ?? 'unknown'}</strong><small>{rec?.missing_vs_batch_rows ? `${num(rec.missing_vs_batch_rows)} source rows (${money(rec.missing_vs_batch_gbp)}) not yet received` : rec ? 'every accepted batch row present' : ''}</small></div>
        <div><span>Local worker</span><strong>{data.worker?.state ?? 'not running'}</strong><small>{workerReady ? 'one warm Spark session' : 'start: docker compose --profile replay up -d worker'}</small></div>
      </div></section>

    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">CONTROLLED LOCAL INCIDENT · DEMO ONLY</span><h2>Inject, contain, recover</h2></div><span className="small-muted">{data.controls_enabled ? 'Allow-listed jobs · loopback API only' : 'Controls disabled'}</span></div>
      <div className="action-grid">{ACTIONS.map(a => <div key={a.kind} className="action"><button className={a.kind === 'reset' ? 'button-secondary' : 'button-primary'} disabled={!!active || busy || !workerReady || !data.controls_enabled || (SHOWCASE && !showcaseAllows(a.kind))} onClick={() => run(a.kind)}>{a.label}</button><p>{a.help}</p></div>)}</div>
      {SHOWCASE ? <p className="panel-footnote">Showcase: these buttons replay the recorded responses of a real incident and repair run on the local stack. Interrupt a publish is available only locally.</p> : !workerReady ? <p className="panel-footnote">The worker is not running, so controls are disabled.</p> : null}
      {latestJob ? <JobProgress job={latestJob} /> : null}</section>

    {status ? <div className="two-column compare-cards">
      <section className="panel trusted-card"><div className="panel-heading"><div><span className="eyebrow">TRUSTED · SERVED</span><h2>Net recorded merchandise</h2></div><span className="status status-good">validated pipeline</span></div><strong className="big-number">{money(status.trusted_net_recorded_gbp)}</strong><p className="panel-footnote">Accepted current revisions in Silver: one row per source identity, envelope and business rules applied, late rows only after backfill.</p></section>
      <section className="panel unsafe-card" aria-label="Unsafe comparison calculation, never served"><div className="panel-heading"><div><span className="eyebrow">UNSAFE · DEMONSTRATION ONLY · NEVER SERVED</span><h2>Naive sum of the raw feed</h2></div><span className="status status-alert">do not use</span></div><strong className="big-number">{money(status.unsafe_comparison.unsafe_net_gbp)}</strong><p className="unsafe-delta">Discrepancy vs trusted: <strong>{money(status.unsafe_comparison.discrepancy_gbp)}</strong></p><p className="panel-footnote">{status.unsafe_comparison.label}.</p></section>
    </div> : null}

    {status?.bronze ? <section className="panel"><div className="panel-heading"><div><span className="eyebrow">DIFFERENT DENOMINATORS</span><h2>Deliveries are not source rows</h2></div></div><div className="trust-counts">{([['Deliveries received', status.bronze.deliveries], ['Distinct Kafka offsets', status.bronze.distinct_offsets], ['Distinct source identities', status.bronze.distinct_identities], ['Synthetic events', status.bronze.synthetic], ['Rejected envelopes', status.bronze.rejected]] as const).map(([l, v]) => <div key={l}><span>{l}</span><strong>{num(v)}</strong></div>)}</div>
      <div className="two-column inner-tables"><div className="table-scroll"><table><thead><tr><th scope="col">Quarantine reason</th><th scope="col" className="numeric">Envelopes</th></tr></thead><tbody>{Object.entries(status.quarantine_by_reason || {}).length ? Object.entries(status.quarantine_by_reason || {}).map(([k, v]) => <tr key={k}><td>{k.replace(/_/g, ' ')}</td><td className="numeric">{num(v)}</td></tr>) : <tr><td colSpan={2}>No quarantined envelopes</td></tr>}</tbody></table></div>
        <div className="table-scroll"><table><thead><tr><th scope="col">Late-arrival path</th><th scope="col" className="numeric">Rows</th></tr></thead><tbody><tr><td>Pending backfill</td><td className="numeric">{num(status.late_arrivals?.pending)}</td></tr><tr><td>Applied by backfill</td><td className="numeric">{num(status.late_arrivals?.applied)}</td></tr><tr><td>Allowed lateness</td><td className="numeric">{data.config.allowed_lateness_hours} h</td></tr></tbody></table></div></div>
      <p className="panel-footnote">Watermark = latest event time committed by earlier micro-batches minus the allowed lateness. Older rows are written to a durable late_arrivals table and block publication until backfilled; nothing financial is dropped.</p></section> : null}

    {t ? <section className="panel"><div className="panel-heading"><div><span className="eyebrow">INCIDENT TIMELINE · PERSISTED</span><h2>Detect, contain, recover</h2></div>{t.recovery_seconds != null ? <span className="status status-good">recovered in {t.recovery_seconds.toFixed(0)} s</span> : <span className="status status-alert">awaiting repair</span>}</div>
      <ol className="timeline"><li><strong>{time(t.injected_at)}</strong> Synthetic incident injected <span className="mono small-muted">{t.incident_run_id}</span></li>
        <li><strong>{time(t.detected_at)}</strong> Detected by the consumer: {Object.entries(t.detected || {}).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(' · ')}</li>
        <li><strong>{time(t.publication_blocked_at)}</strong> Publication {t.publication}: {t.blocked_reason || 'no block recorded'}</li>
        {t.recovered_at ? <li><strong>{time(t.recovered_at)}</strong> Validated snapshot <span className="mono">{t.recovery_snapshot}</span> published after backfill of {num(t.backfill?.applied_rows)} rows on {t.backfill?.affected_dates?.join(', ')}</li> : null}</ol>
      {t.recovery_scope ? <p className="panel-footnote">Recovery time scope: {t.recovery_scope}.</p> : null}</section> : null}

    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">MICRO-BATCH EVIDENCE</span><h2>What each batch saw</h2></div><span className="small-muted">Lag = processing time minus replay emit time, not the dataset’s age</span></div>
      <div className="table-scroll"><table className="batch-table"><thead><tr><th scope="col">Batch</th><th scope="col" className="numeric">Deliveries</th><th scope="col" className="numeric">New</th><th scope="col" className="numeric">Repeat in batch</th><th scope="col" className="numeric">Repeat across</th><th scope="col" className="numeric">Rejected</th><th scope="col" className="numeric">Late → backfill</th><th scope="col" className="numeric">Out of order</th><th scope="col" className="numeric">Lag s</th><th scope="col" className="numeric">Events/s</th><th scope="col">Watermark</th></tr></thead>
        <tbody>{data.batches.map(b => <tr key={b.batch_id}><td className="mono">{b.batch_id}</td><td className="numeric">{num(b.deliveries)}</td><td className="numeric">{num(b.new_revisions)}</td><td className="numeric">{num(b.within_batch_duplicates)}</td><td className="numeric">{num(b.cross_batch_duplicates)}</td><td className="numeric">{num(b.rejected_envelopes)}</td><td className="numeric">{num(b.late_routed_to_backfill)}</td><td className="numeric">{num(b.out_of_order_within_window)}</td><td className="numeric">{b.processing_lag_seconds?.toFixed(1) ?? '—'}</td><td className="numeric">{num(b.deliveries_per_second)}</td><td className="nowrap">{b.watermark?.slice(0, 16) ?? 'none yet'}</td></tr>)}</tbody></table></div></section>

    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">PUBLICATION ATTEMPTS</span><h2>Only validated snapshots become current</h2></div></div>
      <div className="table-scroll"><table><thead><tr><th scope="col">Attempted</th><th scope="col">State</th><th scope="col">Snapshot or reason</th><th scope="col" className="numeric">Seconds</th></tr></thead><tbody>{[...data.publications].reverse().map((p, i) => <tr key={i}><td className="nowrap">{time(p.attempted_at)}</td><td><span className={`status ${p.state === 'passed' ? 'status-good' : 'status-alert'}`}>{p.state}</span></td><td className={p.snapshot_id ? 'mono' : ''}>{p.snapshot_id || p.reason}</td><td className="numeric">{p.seconds?.toFixed(1)}</td></tr>)}</tbody></table></div></section>
  </>;
}

export function CorrectionPanel() {
  const [data, setData] = useState<CorrectionData | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { fetch('/api/replay/correction').then(r => r.ok ? r.json() : Promise.reject(new Error(`No replay snapshot (${r.status})`))).then(setData).catch(e => setError(e.message)); }, []);
  if (error) return <section className="panel"><div className="panel-heading"><div><span className="eyebrow">CORRECTION LINEAGE</span><h2>Replay snapshot unavailable</h2></div></div><p className="panel-footnote">{error}</p></section>;
  if (!data) return null;
  const rec = data.reconciliation;
  return <section className="panel"><div className="panel-heading"><div><span className="eyebrow">RECONCILIATION BEFORE AND AFTER A CORRECTION</span><h2>Revision history of corrected lines</h2></div><span className="mode mode-replay"><span className="mode-dot" />replay snapshot {data.snapshot_id}</span></div>
    <div className="period-grid"><div><span className="eyebrow">BATCH (HISTORICAL) NET</span><strong>{money(data.batch_net_recorded_gbp)}</strong><span className="mono">{data.batch_reference_snapshot}</span></div><span className="period-connector" aria-hidden="true">→</span><div><span className="eyebrow">REPLAY NET (CURRENT REVISIONS)</span><strong>{money(data.metrics?.net_recorded_gbp)}</strong><span>{rec?.state}</span></div><div className="period-change"><span className="eyebrow">SYNTHETIC CORRECTION DELTA</span><strong>{money(rec?.correction_delta_gbp)}</strong><span>{rec?.bridge}{rec?.missing_vs_batch_rows ? ` · ${num(rec.missing_vs_batch_rows)} rows (${money(rec.missing_vs_batch_gbp)}) not yet received` : ''}</span></div></div>
    {data.lines.length ? <div className="table-scroll"><table className="lineage-table"><thead><tr><th scope="col">Source coordinate</th><th scope="col">Revision</th><th scope="col">Origin</th><th scope="col" className="numeric">Quantity</th><th scope="col" className="numeric">Unit GBP</th><th scope="col" className="numeric">Line GBP</th><th scope="col">Applied by</th><th scope="col">Serving</th></tr></thead>
      <tbody>{data.lines.map(l => <tr key={`${l.line_id}-${l.revision}`}><td><span className="mono">{l.source_sheet}:{l.source_row}</span><span className="table-muted mono">{l.line_id.slice(0, 12)}…</span></td><td>v{l.revision}</td><td><span className={`mode mode-${l.mode}`}><span className="mode-dot" />{l.mode}</span>{l.incident ? <span className="table-muted">{l.incident}</span> : null}</td><td className="numeric">{l.quantity}</td><td className="numeric">{money(l.unit_price)}</td><td className="numeric amount">{money(l.line_amount)}</td><td className="mono">{l.first_batch_id}</td><td>{l.is_current ? <span className="status status-good">current</span> : <span className="small-muted">superseded, retained</span>}</td></tr>)}</tbody></table></div>
      : <p className="panel-footnote">The current replay snapshot contains no corrections. Inject an incident and repair it in Data trust & recovery to apply the seeded revision-2 correction.</p>}
    <p className="panel-footnote">A correction keeps the original logical identity and adds an explicit revision; the earlier revision stays in the ledger. Revision 1 is checked against the batch value of the same source row.</p></section>;
}
