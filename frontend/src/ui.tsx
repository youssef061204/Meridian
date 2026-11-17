import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent, ReactNode } from 'react';
import { animate, motion, useInView, useReducedMotion } from 'motion/react';

// ---------- formatting ----------
const gbpFmt = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', maximumFractionDigits: 2 });
const gbp0 = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', maximumFractionDigits: 0 });
const compactFmt = new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', notation: 'compact', maximumFractionDigits: 1 });
const intFmt = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
export const money = (v: unknown) => v == null || v === '' ? '—' : gbpFmt.format(Number(v));
export const money0 = (v: unknown) => v == null ? '—' : gbp0.format(Number(v));
export const compact = (v: unknown) => v == null ? '—' : compactFmt.format(Number(v));
export const int = (v: unknown) => v == null ? '—' : intFmt.format(Number(v));
export const pct = (v: unknown, digits = 1) => v == null || Number.isNaN(Number(v)) ? '—' : `${(Number(v) * 100).toFixed(digits)}%`;
export const day = (v?: string) => v ? v.slice(0, 10) : '—';
export const stamp = (v?: string) => v ? new Date(v).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' }) : '—';
export const sci = (p: number) => p < 1e-4 ? p.toExponential(1).replace('e', '×10^') : p.toFixed(4);

// ---------- motion primitives ----------
/** Animated number. Formats every frame so currency/commas stay correct while counting. */
export function CountUp({ value, format = int, duration = 1.4 }: { value: number; format?: (v: number) => string; duration?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const reduce = useReducedMotion();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (reduce || !inView) { el.textContent = format(reduce ? value : 0); if (reduce) return; }
    if (!inView) return;
    const controls = animate(0, value, { duration, ease: [0.22, 1, 0.36, 1], onUpdate: v => { el.textContent = format(v); } });
    return () => controls.stop();
  }, [value, inView, reduce, duration, format]);
  return <span ref={ref} className="tabular">{format(value)}</span>;
}

export function Reveal({ children, delay = 0, className, style }: { children: ReactNode; delay?: number; className?: string; style?: CSSProperties }) {
  return <motion.div className={className} style={style} initial={{ opacity: 0, y: 22 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: '-40px' }}
    transition={{ duration: .7, delay, ease: [0.22, 1, 0.36, 1] }}>{children}</motion.div>;
}

/** Card with a cursor-following highlight (CSS vars --mx/--my drive a radial gradient). */
export function Card({ children, className = '', span, delay = 0, as = 'section', ...rest }: { children: ReactNode; className?: string; span?: number; delay?: number; as?: 'section' | 'div'; 'aria-label'?: string }) {
  const onMove = (e: PointerEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`);
    e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`);
  };
  const Tag = as === 'div' ? motion.div : motion.section;
  return <Tag {...rest} onPointerMove={onMove} className={`card ${span ? `span-${span}` : ''} ${className}`}
    initial={{ opacity: 0, y: 26 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: '-30px' }}
    transition={{ duration: .75, delay, ease: [0.22, 1, 0.36, 1] }}>{children}</Tag>;
}

export function CardHead({ eyebrow, title, children }: { eyebrow: ReactNode; title: ReactNode; children?: ReactNode }) {
  return <div className="card-head"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{children}</div>;
}

export function PageHead({ index, kicker, title, accent, lede, meta }: { index: string; kicker: string; title: string; accent?: string; lede: string; meta?: ReactNode }) {
  const words = title.split(' ');
  return <header className="page-head">
    <div>
      <motion.div className="kicker" initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: .5 }}><b>{index}</b>{kicker}</motion.div>
      <h1 className="display">
        {words.map((w, i) => <motion.span key={i} style={{ display: 'inline-block', marginRight: '.22em' }} initial={{ opacity: 0, y: '0.6em', filter: 'blur(8px)' }}
          animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }} transition={{ duration: .7, delay: .05 + i * .06, ease: [0.22, 1, 0.36, 1] }}>{w}</motion.span>)}
        {accent ? <motion.span className="serif" style={{ display: 'inline-block' }} initial={{ opacity: 0, y: '0.6em' }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: .8, delay: .1 + words.length * .06, ease: [0.22, 1, 0.36, 1] }}>{accent}</motion.span> : null}
      </h1>
      <motion.p className="lede" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: .35, duration: .6 }}>{lede}</motion.p>
    </div>
    {meta ? <motion.div className="head-meta" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: .45 }}>{meta}</motion.div> : null}
  </header>;
}

export function Bars({ rows, format = money0 }: { rows: { label: string; sub?: string; value: number }[]; format?: (v: number) => string }) {
  const max = Math.max(1, ...rows.map(r => Math.abs(r.value)));
  return <div className="bar-list">{rows.map((r, i) => <div className="bar-row" key={r.label + i}>
    <span className="bar-name" title={r.label}>{r.label}{r.sub ? <span className="table-muted mono">{r.sub}</span> : null}</span>
    <span className="bar-val">{format(r.value)}</span>
    <div className="bar-track"><motion.div className="bar-fill" initial={{ scaleX: 0 }} whileInView={{ scaleX: Math.max(0.01, Math.abs(r.value) / max) }} viewport={{ once: true }}
      transition={{ duration: 1, delay: i * .05, ease: [0.22, 1, 0.36, 1] }} /></div>
  </div>)}</div>;
}

export function Loading({ label = 'Reading the published evidence…' }: { label?: string }) {
  return <div className="loading-state" role="status"><span className="spinner" /><h2>{label}</h2><p>Retrieving the current validated snapshot.</p></div>;
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <section className="error-state" role="alert"><span className="eyebrow">Data service unavailable</span><h2>The evidence could not be loaded</h2><p>{message}</p>
    {retry ? <button className="button-primary" onClick={retry}>Try again</button> : null}</section>;
}

type IconName = 'search' | 'sun' | 'moon' | 'arrow' | 'github' | 'play' | 'spark' | 'download' | 'external';
const PATHS: Record<IconName, string> = {
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  github: 'M9 19c-4 1.5-4-2-6-2.5M15 21v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.3 4.3 0 0 0-.1-3.2s-1-.3-3.4 1.3a11.6 11.6 0 0 0-6 0C6.8 2.8 5.8 3.1 5.8 3.1a4.3 4.3 0 0 0-.1 3.2A4.6 4.6 0 0 0 4.4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21',
  play: 'M7 4l13 8-13 8z',
  spark: 'M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6',
  download: 'M12 3v12M7 10l5 5 5-5M4 21h16',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
};
export function Icon({ name }: { name: IconName }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={PATHS[name]} /></svg>;
}

export function useTheme(): ['dark' | 'light', () => void] {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try { const saved = localStorage.getItem('meridian-theme'); if (saved === 'light' || saved === 'dark') return saved; } catch { /* storage may be blocked */ }
    return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('meridian-theme', theme); } catch { /* ignore */ }
  }, [theme]);
  return [theme, () => setTheme(t => t === 'dark' ? 'light' : 'dark')];
}

/** Segment / series palette as CSS variables so both themes stay legible. */
export const SERIES = ['var(--signal)', 'var(--lime)', 'var(--sky)', 'var(--amber)', 'var(--violet)', 'var(--rose)', 'var(--teal)'];
