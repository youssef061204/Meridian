import { useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as RPointerEvent, ReactNode } from 'react';
import { motion } from 'motion/react';
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from 'd3-force';
import type { SimulationLinkDatum, SimulationNodeDatum } from 'd3-force';
import { compact } from './ui';

const ease = [0.22, 1, 0.36, 1] as const;
const linear = (d0: number, d1: number, r0: number, r1: number) => (v: number) => r0 + (d1 === d0 ? .5 : (v - d0) / (d1 - d0)) * (r1 - r0);
function niceTicks(min: number, max: number, count = 4) {
  const span = max - min || 1, step0 = span / count, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0) ?? step0;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

function Tooltip({ x, y, children, width }: { x: number; y: number; children: ReactNode; width: number }) {
  const left = Math.min(Math.max(x, 90), width - 90);
  return <div className="chart-tooltip" style={{ left, top: y }}>{children}</div>;
}

// ---------------------------------------------------------------- time series
export interface TSPoint { t: number; v: number; expected?: number | null }
export interface Marker { t: number; v: number; kind: 'spike' | 'drop'; label: string }

/** Daily series with hover crosshair, drag-to-zoom brush and anomaly markers. Gaps > 1 day break the line. */
export function TimeSeries({ points, markers = [], height = 280, format = compact, label, showExpected = false }:
  { points: TSPoint[]; markers?: Marker[]; height?: number; format?: (v: number) => string; label: string; showExpected?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [hover, setHover] = useState<TSPoint | null>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, e.contentRect.width)));
    ro.observe(el); return () => ro.disconnect();
  }, []);
  const sorted = useMemo(() => [...points].sort((a, b) => a.t - b.t), [points]);
  const visible = zoom ? sorted.filter(p => p.t >= zoom[0] && p.t <= zoom[1]) : sorted;
  const pad = { l: 58, r: 14, t: 16, b: 30 };
  if (!visible.length) return <div className="empty compact">No observations in this selection.</div>;
  const t0 = visible[0].t, t1 = visible[visible.length - 1].t;
  const vals = visible.flatMap(p => [p.v, showExpected && p.expected != null ? p.expected : p.v]);
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(...vals));
  const vMin = Math.min(ticks[0], ...vals), vMax = Math.max(ticks[ticks.length - 1], ...vals);
  const x = linear(t0, t1, pad.l, width - pad.r), y = linear(vMin, vMax, height - pad.b, pad.t);
  const inv = (px: number) => t0 + (px - pad.l) / (width - pad.l - pad.r) * (t1 - t0);
  const DAY = 864e5;
  const segments: TSPoint[][] = [];
  for (const p of visible) { const s = segments[segments.length - 1]; if (!s || p.t - s[s.length - 1].t > DAY * 1.5) segments.push([p]); else s.push(p); }
  const path = (s: TSPoint[], key: 'v' | 'expected' = 'v') => s.filter(p => p[key] != null).map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p[key] as number).toFixed(1)}`).join('');
  const area = (s: TSPoint[]) => `${path(s)}L${x(s[s.length - 1].t).toFixed(1)},${y(Math.max(vMin, 0))}L${x(s[0].t).toFixed(1)},${y(Math.max(vMin, 0))}Z`;
  const nearest = (px: number) => { const t = inv(px); let best = visible[0]; for (const p of visible) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p; return best; };
  const local = (e: RPointerEvent) => e.clientX - (ref.current?.getBoundingClientRect().left ?? 0);
  const onDown = (e: RPointerEvent) => { const px = local(e); setDrag([px, px]); (e.target as Element).setPointerCapture?.(e.pointerId); };
  const onMove = (e: RPointerEvent) => { const px = local(e); setHover(nearest(px)); if (drag) setDrag([drag[0], px]); };
  const onUp = () => {
    if (drag && Math.abs(drag[1] - drag[0]) > 12) { const a = inv(Math.min(...drag)), b = inv(Math.max(...drag)); if (visible.filter(p => p.t >= a && p.t <= b).length > 2) setZoom([a, b]); }
    setDrag(null);
  };
  const yearTicks: number[] = [];
  const span = t1 - t0;
  const stepMonths = span > 400 * DAY ? 3 : span > 120 * DAY ? 1 : 0;
  if (stepMonths) { const d = new Date(t0); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + 1); for (; d.getTime() <= t1; d.setUTCMonth(d.getUTCMonth() + stepMonths)) yearTicks.push(d.getTime()); }
  else { for (let t = t0; t <= t1; t += Math.max(DAY, Math.round(span / 6 / DAY) * DAY)) yearTicks.push(t); }
  const fmtDate = (t: number) => new Date(t).toLocaleDateString('en-GB', stepMonths ? { month: 'short', year: '2-digit', timeZone: 'UTC' } : { day: 'numeric', month: 'short', timeZone: 'UTC' });
  const key = `${t0}-${t1}`;
  return <div className="chart" ref={ref}>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={() => { setHover(null); setDrag(null); }}
      onDoubleClick={() => setZoom(null)} style={{ touchAction: 'pan-y', cursor: 'crosshair' }}>
      <defs><linearGradient id="ts-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="var(--signal)" stopOpacity=".32" /><stop offset="100%" stopColor="var(--signal)" stopOpacity="0" /></linearGradient></defs>
      {ticks.map(t => <g key={t}><line className="gridline" x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} /><text x={pad.l - 10} y={y(t) + 4} textAnchor="end">{format(t)}</text></g>)}
      {yearTicks.map(t => <text key={t} x={x(t)} y={height - 8} textAnchor="middle">{fmtDate(t)}</text>)}
      <g key={key}>
        {segments.map((s, i) => <g key={i}>
          <motion.path d={area(s)} fill="url(#ts-fill)" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1.2, delay: .3 }} />
          {showExpected ? <path d={path(s, 'expected')} fill="none" stroke="var(--muted)" strokeWidth="1.2" strokeDasharray="3 4" opacity=".7" /> : null}
          <motion.path d={path(s)} fill="none" stroke="var(--signal)" strokeWidth="1.8" strokeLinejoin="round" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.6, ease }} />
          {s.length === 1 ? <circle cx={x(s[0].t)} cy={y(s[0].v)} r="3" fill="var(--signal)" /> : null}
        </g>)}
      </g>
      {markers.filter(m => m.t >= t0 && m.t <= t1).map((m, i) => <motion.g key={m.t} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 1.2 + i * .04, type: 'spring' }} style={{ transformOrigin: `${x(m.t)}px ${y(m.v)}px` }}>
        <circle cx={x(m.t)} cy={y(m.v)} r="9" fill={m.kind === 'spike' ? 'var(--lime)' : 'var(--rose)'} opacity=".18" />
        <circle cx={x(m.t)} cy={y(m.v)} r="4" fill="var(--bg)" stroke={m.kind === 'spike' ? 'var(--lime)' : 'var(--rose)'} strokeWidth="2" />
      </motion.g>)}
      {drag ? <rect x={Math.min(...drag)} y={pad.t} width={Math.abs(drag[1] - drag[0])} height={height - pad.t - pad.b} fill="var(--signal)" opacity=".1" stroke="var(--signal)" strokeDasharray="3 3" /> : null}
      {hover && !drag ? <g pointerEvents="none"><line x1={x(hover.t)} x2={x(hover.t)} y1={pad.t} y2={height - pad.b} stroke="var(--text)" strokeOpacity=".35" />
        <circle cx={x(hover.t)} cy={y(hover.v)} r="5" fill="var(--signal)" stroke="var(--bg)" strokeWidth="2" /></g> : null}
    </svg>
    {hover && !drag ? <Tooltip x={x(hover.t) / width * (ref.current?.clientWidth ?? width)} y={y(hover.v) / height * (ref.current?.clientHeight ?? height)} width={ref.current?.clientWidth ?? width}>
      <div className="t-title">{new Date(hover.t).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })}</div>
      <div className="t-row"><span><i style={{ background: 'var(--signal)' }} />Net</span><b>{format(hover.v)}</b></div>
      {showExpected && hover.expected != null ? <div className="t-row"><span><i style={{ background: 'var(--muted)' }} />Expected</span><b>{format(hover.expected)}</b></div> : null}
      {markers.find(m => m.t === hover.t) ? <div className="t-row" style={{ color: 'var(--lime)', marginTop: 4 }}>{markers.find(m => m.t === hover.t)!.label}</div> : null}
    </Tooltip> : null}
    <div className="brush-hint"><span>Drag across the chart to zoom</span>{zoom ? <button className="button-text" onClick={() => setZoom(null)}>Reset zoom</button> : <span>· double-click resets</span>}</div>
  </div>;
}

// ---------------------------------------------------------------- generic XY lines
export interface Series { name: string; color: string; points: { x: number; y: number }[]; dashed?: boolean; area?: boolean }
export function LineChart({ series, height = 260, xLabel, yLabel, xFmt = (v: number) => v.toFixed(1), yFmt = (v: number) => v.toFixed(2), diagonal = false, domain, label }:
  { series: Series[]; height?: number; xLabel?: string; yLabel?: string; xFmt?: (v: number) => string; yFmt?: (v: number) => string; diagonal?: boolean; domain?: { x?: [number, number]; y?: [number, number] }; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => { const el = ref.current; if (!el) return; const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, e.contentRect.width))); ro.observe(el); return () => ro.disconnect(); }, []);
  const all = series.flatMap(s => s.points);
  const [x0, x1] = domain?.x ?? [Math.min(...all.map(p => p.x)), Math.max(...all.map(p => p.x))];
  const [y0, y1] = domain?.y ?? [Math.min(0, ...all.map(p => p.y)), Math.max(...all.map(p => p.y))];
  const pad = { l: 48, r: 14, t: 12, b: 38 };
  const x = linear(x0, x1, pad.l, width - pad.r), y = linear(y0, y1, height - pad.b, pad.t);
  const d = (pts: { x: number; y: number }[]) => pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join('');
  const onMove = (e: RPointerEvent) => { const r = ref.current!.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * width; setHover(x0 + (px - pad.l) / (width - pad.l - pad.r) * (x1 - x0)); };
  const near = (s: Series) => hover == null ? null : s.points.reduce((a, b) => Math.abs(b.x - hover) < Math.abs(a.x - hover) ? b : a);
  return <div className="chart" ref={ref}>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      {niceTicks(y0, y1).map(t => <g key={t}><line className="gridline" x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} /><text x={pad.l - 8} y={y(t) + 4} textAnchor="end">{yFmt(t)}</text></g>)}
      {niceTicks(x0, x1, 5).map(t => <text key={t} x={x(t)} y={height - pad.b + 16} textAnchor="middle">{xFmt(t)}</text>)}
      {xLabel ? <text x={width / 2} y={height - 4} textAnchor="middle">{xLabel}</text> : null}
      {yLabel ? <text x={12} y={pad.t + 4} textAnchor="start" transform="">{yLabel}</text> : null}
      {diagonal ? <line x1={x(x0)} y1={y(y0)} x2={x(x1)} y2={y(y1)} stroke="var(--faint)" strokeDasharray="4 5" /> : null}
      {series.map((s, i) => <g key={s.name}>
        {s.area ? <motion.path d={`${d(s.points)}L${x(s.points[s.points.length - 1].x)},${y(y0)}L${x(s.points[0].x)},${y(y0)}Z`} fill={s.color} initial={{ opacity: 0 }} whileInView={{ opacity: .12 }} viewport={{ once: true }} transition={{ duration: 1 }} /> : null}
        <motion.path d={d(s.points)} fill="none" stroke={s.color} strokeWidth={s.dashed ? 1.4 : 2.2} strokeDasharray={s.dashed ? '5 5' : undefined}
          initial={{ pathLength: 0 }} whileInView={{ pathLength: 1 }} viewport={{ once: true }} transition={{ duration: 1.4, delay: i * .15, ease }} />
        {hover != null && near(s) ? <circle cx={x(near(s)!.x)} cy={y(near(s)!.y)} r="4" fill={s.color} stroke="var(--bg)" strokeWidth="2" /> : null}
      </g>)}
    </svg>
    {hover != null ? <Tooltip x={x(near(series[0])!.x) / width * (ref.current?.clientWidth ?? width)} y={pad.t + 10} width={ref.current?.clientWidth ?? width}>
      <div className="t-title">{xLabel ?? 'x'} {xFmt(near(series[0])!.x)}</div>
      {series.map(s => <div className="t-row" key={s.name}><span><i style={{ background: s.color }} />{s.name}</span><b>{yFmt(near(s)!.y)}</b></div>)}
    </Tooltip> : null}
    <div className="chart-legend" style={{ marginTop: 8 }}>{series.map(s => <span key={s.name}><i style={{ background: s.color }} />{s.name}</span>)}</div>
  </div>;
}

// ---------------------------------------------------------------- scatter (log axes)
export interface Dot { x: number; y: number; group: string }
export function Scatter({ dots, colors, height = 360, xLabel, yLabel, active, label }:
  { dots: Dot[]; colors: Record<string, string>; height?: number; xLabel: string; yLabel: string; active?: string | null; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(700);
  const [hover, setHover] = useState<Dot | null>(null);
  useEffect(() => { const el = ref.current; if (!el) return; const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, e.contentRect.width))); ro.observe(el); return () => ro.disconnect(); }, []);
  const pad = { l: 52, r: 14, t: 12, b: 38 };
  const lx = (v: number) => Math.log10(Math.max(1, v)), ly = (v: number) => Math.log10(Math.max(1, v));
  const xs = dots.map(d => lx(d.x)), ys = dots.map(d => ly(d.y));
  const x = linear(0, Math.max(...xs), pad.l, width - pad.r), y = linear(0, Math.max(...ys), height - pad.b, pad.t);
  const decades = (max: number) => Array.from({ length: Math.floor(max) + 1 }, (_, i) => i);
  return <div className="chart" ref={ref}>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} onPointerLeave={() => setHover(null)}>
      {decades(Math.max(...ys)).map(t => <g key={t}><line className="gridline" x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} /><text x={pad.l - 8} y={y(t) + 4} textAnchor="end">{compact(10 ** t)}</text></g>)}
      {decades(Math.max(...xs)).map(t => <text key={t} x={x(t)} y={height - pad.b + 16} textAnchor="middle">{10 ** t}d</text>)}
      <text x={width / 2} y={height - 4} textAnchor="middle">{xLabel}</text>
      <text x={pad.l} y={pad.t - 2}>{yLabel}</text>
      {dots.map((d, i) => <motion.circle key={i} cx={x(lx(d.x))} cy={y(ly(d.y))} r={hover === d ? 6 : 3.2} fill={colors[d.group]}
        opacity={active && active !== d.group ? .08 : .72} initial={{ opacity: 0, scale: 0 }} whileInView={{ opacity: active && active !== d.group ? .08 : .72, scale: 1 }}
        viewport={{ once: true }} transition={{ duration: .5, delay: (i % 120) * .004 }} onPointerEnter={() => setHover(d)} style={{ transition: 'opacity .3s' }} />)}
    </svg>
    {hover ? <Tooltip x={x(lx(hover.x)) / width * (ref.current?.clientWidth ?? width)} y={y(ly(hover.y)) / height * (ref.current?.clientHeight ?? height)} width={ref.current?.clientWidth ?? width}>
      <div className="t-title">{hover.group}</div>
      <div className="t-row"><span>Recency</span><b>{hover.x} days</b></div>
      <div className="t-row"><span>Value</span><b>{compact(hover.y)}</b></div>
    </Tooltip> : null}
  </div>;
}

// ---------------------------------------------------------------- heatmap
export function Heatmap({ rows, cols, value, color = 'var(--signal)', format, rowLabel, colLabel, label, tip }:
  { rows: string[]; cols: string[]; value: (r: number, c: number) => number | null; color?: string; format: (v: number) => string; rowLabel?: string; colLabel?: string; label: string; tip?: (r: number, c: number) => string }) {
  const vals: number[] = [];
  rows.forEach((_, r) => cols.forEach((__, c) => { const v = value(r, c); if (v != null) vals.push(v); }));
  const max = Math.max(...vals, 1e-9);
  const [hover, setHover] = useState<[number, number] | null>(null);
  return <div className="chart" role="img" aria-label={label}>
    <div className="heatmap" style={{ gridTemplateColumns: `minmax(44px, auto) repeat(${cols.length}, minmax(0, 1fr))` }}>
      <span className="heat-label">{rowLabel}</span>
      {cols.map(c => <span key={c} className="heat-label" style={{ justifyContent: 'center' }}>{c}</span>)}
      {rows.map((rLabel, r) => [<span key={`l${r}`} className="heat-label">{rLabel}</span>,
        ...cols.map((_, c) => { const v = value(r, c); return <motion.div key={`${r}-${c}`} className="heat-cell" onPointerEnter={() => setHover([r, c])} onPointerLeave={() => setHover(null)}
          title={v == null ? 'No observation' : tip ? tip(r, c) : format(v)}
          style={{ background: v == null ? 'transparent' : `color-mix(in srgb, ${color} ${Math.round(8 + 88 * Math.sqrt(Math.max(0, v) / max))}%, var(--surface-2))`, border: v == null ? '1px dashed var(--line)' : 'none',
            outline: hover && hover[0] === r && hover[1] === c ? '2px solid var(--text)' : 'none' }}
          initial={{ opacity: 0, scale: .6 }} whileInView={{ opacity: 1, scale: 1 }} viewport={{ once: true }} transition={{ duration: .4, delay: (r + c) * .012 }} />; })])}
    </div>
    <div className="brush-hint" style={{ justifyContent: 'space-between' }}><span>{colLabel}</span><span>{hover && value(hover[0], hover[1]) != null ? `${rows[hover[0]]} · ${cols[hover[1]]}: ${tip ? tip(hover[0], hover[1]) : format(value(hover[0], hover[1])!)}` : 'Hover a cell'}</span></div>
  </div>;
}

// ---------------------------------------------------------------- force network
interface NNode extends SimulationNodeDatum { id: string; name: string; support: number; degree: number }
interface NEdge extends SimulationLinkDatum<NNode> { lift: number; support: number }
export function Network({ nodes, edges, onSelect, selected }: { nodes: { id: string; name: string; support: number; degree: number }[]; edges: { source: string; target: string; lift: number; support: number }[]; onSelect?: (id: string | null) => void; selected?: string | null }) {
  const W = 900, H = 560;
  const [, tick] = useState(0);
  const [hover, setHover] = useState<string | null>(null);
  const sim = useMemo(() => {
    const ns: NNode[] = nodes.map(n => ({ ...n }));
    const es: NEdge[] = edges.map(e => ({ ...e }));
    const s = forceSimulation(ns).force('link', forceLink<NNode, NEdge>(es).id(d => d.id).distance(e => 40 + 220 / Math.sqrt(e.lift)).strength(.5))
      .force('charge', forceManyBody().strength(-110)).force('center', forceCenter(W / 2, H / 2)).force('collide', forceCollide<NNode>().radius(d => 10 + Math.sqrt(d.support) * 70))
      // Product families are disconnected components: per-node gravity keeps them all on canvas.
      .force('x', forceX(W / 2).strength(.07)).force('y', forceY(H / 2).strength(.1));
    const clamp = () => ns.forEach(n => { n.x = Math.max(24, Math.min(W - 140, n.x ?? 0)); n.y = Math.max(18, Math.min(H - 18, n.y ?? 0)); });
    s.on('tick.clamp', clamp);
    s.stop(); for (let i = 0; i < 240; i++) s.tick(); clamp(); // settle before first paint, then let it breathe
    s.alpha(.08).restart();
    return { s, ns, es };
  }, [nodes, edges]);
  useEffect(() => { sim.s.on('tick.render', () => tick(t => t + 1)); return () => { sim.s.stop(); }; }, [sim]);
  const focus = hover ?? selected ?? null;
  // Label only the best-supported products until something is focused, so names stay legible.
  const labelled = useMemo(() => new Set([...nodes].sort((a, b) => b.support - a.support).slice(0, 9).map(n => n.id)), [nodes]);
  const linked = new Set<string>();
  if (focus) sim.es.forEach(e => { const a = (e.source as NNode).id, b = (e.target as NNode).id; if (a === focus || b === focus) { linked.add(a); linked.add(b); } });
  const dragging = useRef<NNode | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const toLocal = (e: RPointerEvent) => { const r = svgRef.current!.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * H]; };
  return <svg ref={svgRef} className="network" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Product association network: nodes are frequently bought products, links join pairs with high lift"
    onPointerMove={e => { if (dragging.current) { const [px, py] = toLocal(e); dragging.current.fx = px; dragging.current.fy = py; sim.s.alphaTarget(.3).restart(); } }}
    onPointerUp={() => { if (dragging.current) { dragging.current.fx = null; dragging.current.fy = null; dragging.current = null; sim.s.alphaTarget(0); } }}
    onClick={e => { if (e.target === svgRef.current) onSelect?.(null); }}>
    {sim.es.map((e, i) => { const s = e.source as NNode, t = e.target as NNode; const hot = focus && (s.id === focus || t.id === focus);
      return <line key={i} className={`edge ${hot ? 'hot' : ''} ${focus && !hot ? 'dim' : ''}`} x1={s.x} y1={s.y} x2={t.x} y2={t.y} strokeWidth={Math.min(5, .6 + Math.log2(e.lift) * .55)} />; })}
    {sim.ns.map(n => { const r = 5 + Math.sqrt(n.support) * 70; const dim = focus && !linked.has(n.id) && n.id !== focus;
      return <motion.g key={n.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: .6, delay: .15 }} className={`node ${dim ? 'dim' : ''}`} transform={`translate(${n.x ?? 0},${n.y ?? 0})`} style={{ cursor: 'pointer' }}
        onPointerEnter={() => setHover(n.id)} onPointerLeave={() => setHover(null)} onPointerDown={e => { e.stopPropagation(); dragging.current = n; }}
        onClick={e => { e.stopPropagation(); onSelect?.(n.id === selected ? null : n.id); }}>
        <circle r={r} fill={n.id === focus ? 'var(--signal)' : 'var(--sky)'} opacity={n.id === focus ? 1 : .8} />
        {(focus ? n.id === focus || linked.has(n.id) : labelled.has(n.id)) ? <text x={r + 4} y={4}>{(n.name || n.id).slice(0, 26).toLowerCase()}</text> : null}
      </motion.g>; })}
  </svg>;
}
