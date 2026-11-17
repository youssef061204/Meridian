import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import spec from '../../shared/ask.json';
import { checkSql } from '../../shared/sqlguard';
import { engine, query } from '../duck';
import type { Result } from '../duck';
import { LineChart } from '../charts';
import { Bars, Card, CardHead, Icon, PageHead, compact, int } from '../ui';

type Chart = 'line' | 'bar' | 'table';
type Answer = { sql: string; explanation: string; chart: Chart; source: 'claude' | 'curated'; model?: string; note?: string };

/** No-key fallback: closest curated question by token overlap. Honest about being a match, not AI. */
function closest(question: string) {
  const tokens = (s: string) => new Set(s.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(/\s+/).filter(w => w.length > 2));
  const q = tokens(question);
  let best = spec.examples[0], score = -1;
  for (const e of spec.examples) { const t = tokens(e.q); const s = [...q].filter(w => t.has(w) || [...t].some(x => x.startsWith(w.slice(0, 5)))).length / Math.sqrt(t.size || 1); if (s > score) { best = e; score = s; } }
  return { example: best, score };
}

function ResultChart({ result, chart }: { result: Result; chart: Chart }) {
  const [label, ...rest] = result.columns;
  const numeric = rest.find(c => result.rows.every(r => r[c] == null || typeof r[c] === 'number'));
  if (chart === 'table' || !numeric || result.rows.length < 2) return null;
  if (chart === 'bar') return <Bars rows={result.rows.slice(0, 15).map(r => ({ label: String(r[label]), value: Number(r[numeric]) }))} format={v => compact(v).replace('£', /gbp|net|value/i.test(numeric) ? '£' : '')} />;
  const isDate = result.rows.every(r => typeof r[label] === 'string' && !Number.isNaN(Date.parse(String(r[label]))));
  const pts = result.rows.map((r, i) => ({ x: isDate ? Date.parse(String(r[label])) : typeof r[label] === 'number' ? Number(r[label]) : i, y: Number(r[numeric]) }));
  return <LineChart label={`${numeric} by ${label}`} series={[{ name: numeric, color: 'var(--signal)', points: pts, area: true }]} height={240}
    xFmt={v => isDate ? new Date(v).toLocaleDateString('en-GB', { month: 'short', year: '2-digit', timeZone: 'UTC' }) : String(Math.round(v))} yFmt={v => compact(v).replace('£', '')} />;
}

function toCsv(r: Result) {
  const esc = (v: unknown) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [r.columns.join(','), ...r.rows.map(row => r.columns.map(c => esc(row[c])).join(','))].join('\n');
}

export function SqlLabView() {
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [rowCount, setRowCount] = useState<number | null>(null);
  const [sql, setSql] = useState(spec.examples[0].sql);
  const [chart, setChart] = useState<Chart>(spec.examples[0].chart as Chart);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const [running, setRunning] = useState(false);
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const started = useRef(false);

  const run = async (text = sql, kind: Chart = chart) => {
    const problem = checkSql(text);
    if (problem) { setError(problem); setResult(null); return; }
    setRunning(true); setError('');
    try { const r = await query(text.trim().replace(/;\s*$/, '')); setResult(r); setChart(kind); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); setResult(null); }
    finally { setRunning(false); }
  };

  useEffect(() => {
    if (started.current) return; started.current = true;
    setStatus('loading');
    engine().then(async () => { const r = await query('SELECT count(*) AS n FROM sales'); setRowCount(Number(r.rows[0].n)); setStatus('ready'); run(); })
      .catch(e => { setStatus('error'); setError(`DuckDB could not start in this browser: ${e instanceof Error ? e.message : e}`); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const ask = async (e: FormEvent) => {
    e.preventDefault();
    const q = question.trim(); if (!q) return;
    setAsking(true); setAnswer(null);
    let a: Answer;
    try {
      const response = await fetch('/api/ask', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ question: q }) });
      const body = await response.json().catch(() => ({}));
      if (response.ok && body.answerable) a = { sql: body.sql, explanation: body.explanation, chart: body.chart, source: 'claude', model: body.model };
      else if (response.ok) a = { sql: '', explanation: body.explanation, chart: 'table', source: 'claude', model: body.model };
      else throw new Error(response.status === 501 || response.status === 429 ? body.detail : 'the AI endpoint is not reachable from this host');
    } catch (cause) {
      const { example } = closest(q);
      a = { sql: example.sql, explanation: `Closest curated query: “${example.q}”.`, chart: example.chart as Chart, source: 'curated', note: cause instanceof Error ? cause.message : 'AI unavailable' };
    }
    setAnswer(a); setAsking(false);
    if (a.sql) { setSql(a.sql); run(a.sql, a.chart); }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); run(); } };
  const download = () => { if (!result) return; const url = URL.createObjectURL(new Blob([toCsv(result)], { type: 'text/csv' })); const a = document.createElement('a'); a.href = url; a.download = 'meridian-query.csv'; a.click(); URL.revokeObjectURL(url); };
  const tables = useMemo(() => spec.schema.split('\n- ').slice(1).map(t => { const [head, ...note] = t.split(' -- '); const name = head.slice(0, head.indexOf('(')); return { name, cols: head.slice(head.indexOf('(') + 1, head.lastIndexOf(')')), note: note.join(' -- ') }; }), []);

  return <>
    <PageHead index="§08" kicker="SQL Lab · DuckDB in your browser" title="Ask the data" accent="anything." lede="A real analytical database running locally in this tab over 1.06M accepted invoice lines. Type a question in plain English, or write the SQL yourself."
      meta={<span className="engine-status"><span className="dot" style={{ color: status === 'ready' ? 'var(--lime)' : status === 'error' ? 'var(--rose)' : 'var(--amber)' }} />
        {status === 'ready' ? `DuckDB-WASM ready · ${int(rowCount)} lines` : status === 'loading' ? 'Starting DuckDB-WASM…' : status === 'error' ? 'Engine unavailable' : 'Idle'}</span>} />

    <Card><CardHead eyebrow={<><b>01</b> · natural language → SQL</>} title="What do you want to know?" />
      <form className="ask-box" onSubmit={ask}><span style={{ width: 18, marginLeft: 8, color: 'var(--signal)', display: 'inline-flex' }}><Icon name="spark" /></span>
        <input value={question} onChange={e => setQuestion(e.target.value)} maxLength={300} placeholder="e.g. Which products do German customers return most often?" aria-label="Ask a question about the data" />
        <button className="button-primary" type="submit" disabled={asking || status !== 'ready'}>{asking ? 'Thinking…' : 'Ask'}</button></form>
      <AnimatePresence>{answer ? <motion.div key={answer.sql + answer.explanation} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0 }} style={{ overflow: 'hidden' }}>
        <p className="note" style={{ marginTop: 14 }}><span className={`chip ${answer.source === 'claude' ? 'signal' : ''}`} style={{ marginRight: 10 }}>{answer.source === 'claude' ? `Claude · ${answer.model}` : 'curated match'}</span>
          {answer.explanation}{answer.note ? ` (AI translation unavailable here: ${answer.note})` : ''}</p></motion.div> : null}</AnimatePresence>
      <div className="example-list" style={{ marginTop: 16 }}>{spec.examples.map(e => <button key={e.q} onClick={() => { setQuestion(e.q); setAnswer(null); setSql(e.sql); run(e.sql, e.chart as Chart); }}>{e.q}</button>)}</div>
      <p className="note">Questions go to Claude only to draft SQL against the schema; no data leaves your browser. Every generated query is checked to be a single read-only SELECT before it runs.</p></Card>

    <div className="bento">
      <Card span={8}><CardHead eyebrow={<><b>02</b> · editor</>} title="SQL"><div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><kbd>⌘/Ctrl</kbd><kbd>Enter</kbd>
        <button className="button-primary" onClick={() => run()} disabled={running || status !== 'ready'}><span style={{ width: 14, display: 'inline-flex' }}><Icon name="play" /></span>{running ? 'Running…' : 'Run'}</button></div></CardHead>
        <textarea className="sql-editor" value={sql} onChange={e => setSql(e.target.value)} onKeyDown={onKey} spellCheck={false} aria-label="SQL query" />
        {error ? <p className="note" role="alert" style={{ color: 'var(--rose)' }}>{error}</p> : null}</Card>
      <Card span={4} delay={.06}><CardHead eyebrow={<><b>03</b> · schema</>} title="Tables" />
        <div style={{ display: 'grid', gap: 14 }}>{tables.map(t => <div key={t.name}><strong className="mono" style={{ color: 'var(--signal)' }}>{t.name}</strong>
          <div className="mono" style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 4, lineHeight: 1.7 }}>{t.cols}</div></div>)}</div>
        <p className="note">Marts exported by <span className="mono">meridian.insights</span> from the validated accepted lines (gzip CSV, ~6 MB), loaded once into in-memory tables. The export is checked to reproduce the accepted lines exactly.</p></Card>
    </div>

    {result ? <Card><CardHead eyebrow={<><b>04</b> · result</>} title={`${int(result.rows.length)} rows · ${result.ms.toFixed(0)} ms`}>
      <div style={{ display: 'flex', gap: 8 }}><div className="segmented" role="group" aria-label="Result view">{(['table', 'bar', 'line'] as Chart[]).map(c => <button key={c} aria-pressed={chart === c} onClick={() => setChart(c)}>{c}</button>)}</div>
        <button className="button-secondary" onClick={download}><span style={{ width: 14, display: 'inline-flex' }}><Icon name="download" /></span>CSV</button></div></CardHead>
      <ResultChart result={result} chart={chart} />
      <div className="table-scroll" style={{ maxHeight: 460, marginTop: chart === 'table' ? 0 : 18 }}><table><thead><tr>{result.columns.map(c => <th key={c} className={typeof result.rows[0]?.[c] === 'number' ? 'numeric' : ''}>{c}</th>)}</tr></thead>
        <tbody>{result.rows.slice(0, 500).map((r, i) => <tr key={i}>{result.columns.map(c => <td key={c} className={typeof r[c] === 'number' ? 'numeric' : ''}>{r[c] == null ? <span className="unknown">null</span> : typeof r[c] === 'number' ? (Number.isInteger(r[c]) ? int(r[c]) : Number(r[c]).toLocaleString('en-GB', { maximumFractionDigits: 4 })) : String(r[c])}</td>)}</tr>)}</tbody></table></div></Card> : null}
  </>;
}
