import { useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import type { Insights, Rule } from '../types';
import { Network } from '../charts';
import { Card, CardHead, CountUp, PageHead, int, pct } from '../ui';

type SortKey = 'lift' | 'confidence' | 'support';
const title = (s: string | null | undefined) => (s ?? '').toLowerCase().replace(/(^|\s)\S/g, c => c.toUpperCase()).trim();

export function BasketsView({ data }: { data: Insights }) {
  const b = data.basket;
  const [selected, setSelected] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<SortKey>('lift');
  const names = useMemo(() => Object.fromEntries(b.rules.flatMap(r => [[r.lhs, r.lhs_name], [r.rhs, r.rhs_name]])), [b]);
  const together = selected ? b.rules.filter(r => r.lhs === selected).sort((x, y) => y.confidence - x.confidence).slice(0, 6) : [];
  const rows = b.rules.filter(r => !q || `${r.lhs_name} ${r.rhs_name} ${r.lhs} ${r.rhs}`.toLowerCase().includes(q.toLowerCase())).sort((x, y) => y[sort] - x[sort]).slice(0, 40);
  const top: Rule | undefined = b.rules[0];
  return <>
    <PageHead index="§03" kicker="Baskets · market basket analysis" title="What sells" accent="together." lede={`Association rules mined from ${int(b.invoices)} positive-merchandise invoices. Lift tells you how much more often two products share a basket than chance would predict.`}
      meta={<><span className="chip signal"><span className="dot" />{int(b.rules_found)} rules</span><span className="meta-line"><strong>{int(b.frequent_items)}</strong> frequent items</span></>} />
    <div className="bento">
      <Card span={8}><CardHead eyebrow={<><b>01</b> · association network</>} title="Products linked by lift"><span className="small-muted">Drag nodes · click to focus</span></CardHead>
        <Network nodes={b.graph.nodes} edges={b.graph.edges} selected={selected} onSelect={setSelected} />
        <p className="note">Top {b.graph.edges.length} pairs by lift. Node size is basket support, line weight is lift. {b.method}.</p></Card>
      <Card span={4} delay={.08}>
        <AnimatePresence mode="wait">
          {selected ? <motion.div key={selected} initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={{ duration: .35 }}>
            <CardHead eyebrow={<><b>02</b> · frequently bought together</>} title={title(names[selected]) || selected} />
            <span className="chip">{selected}</span>
            <div className="bar-list" style={{ marginTop: 16 }}>{together.length ? together.map(r => <div className="bar-row" key={r.rhs}>
              <span className="bar-name">{title(r.rhs_name)}<span className="table-muted mono">lift {r.lift.toFixed(1)} · in {int(r.n_ab)} baskets</span></span>
              <span className="bar-val">{pct(r.confidence, 0)}</span>
              <div className="bar-track"><motion.div className="bar-fill" initial={{ scaleX: 0 }} animate={{ scaleX: r.confidence }} transition={{ duration: .8 }} /></div></div>)
              : <p className="note">No rules start from this product above the support threshold.</p>}</div>
            <p className="note">Read as: of baskets containing this product, the share that also contain the other one (confidence).</p>
          </motion.div> : <motion.div key="intro" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <CardHead eyebrow={<><b>02</b> · strongest rule</>} title="Pick a product in the network" />
            {top ? <div style={{ display: 'grid', gap: 16 }}>
              <div className="stat"><span className="stat-label">Highest lift pair</span><span style={{ fontSize: 17 }}>{title(top.lhs_name)} <span style={{ color: 'var(--signal)' }}>→</span> {title(top.rhs_name)}</span></div>
              <div className="stat-row">
                <div className="stat"><span className="stat-label">Lift</span><span className="stat-value" style={{ color: 'var(--signal)' }}><CountUp value={top.lift} format={v => `${v.toFixed(1)}×`} /></span></div>
                <div className="stat"><span className="stat-label">Confidence</span><span className="stat-value sm">{pct(top.confidence, 0)}</span></div>
                <div className="stat"><span className="stat-label">Support</span><span className="stat-value sm">{pct(top.support, 2)}</span></div>
              </div>
              <p className="note">Consultant read-out: high-lift sets are bundle and cross-sell candidates; high-support pairs are where a recommendation widget earns the most impressions.</p>
            </div> : null}
          </motion.div>}
        </AnimatePresence>
      </Card>
    </div>
    <Card><CardHead eyebrow={<><b>03</b> · rule explorer</>} title="Search every mined rule">
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}><input type="search" placeholder="Filter by product…" value={q} onChange={e => setQ(e.target.value)} aria-label="Filter rules by product" />
        <div className="segmented" role="group" aria-label="Sort rules">{(['lift', 'confidence', 'support'] as SortKey[]).map(k => <button key={k} aria-pressed={sort === k} onClick={() => setSort(k)}>{k}</button>)}</div></div></CardHead>
      <div className="table-scroll" style={{ maxHeight: 520 }}><table><thead><tr><th>If a basket has</th><th>…it often also has</th><th className="numeric">Baskets</th><th className="numeric">Support</th><th className="numeric">Confidence</th><th className="numeric">Lift</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.lhs + r.rhs} onClick={() => setSelected(r.lhs)} style={{ cursor: 'pointer' }}><td>{title(r.lhs_name)}<span className="table-muted mono">{r.lhs}</span></td><td>{title(r.rhs_name)}<span className="table-muted mono">{r.rhs}</span></td>
          <td className="numeric">{int(r.n_ab)}</td><td className="numeric">{pct(r.support, 2)}</td><td className="numeric">{pct(r.confidence, 0)}</td><td className="numeric amount" style={{ color: 'var(--signal)' }}>{r.lift.toFixed(1)}×</td></tr>)}</tbody></table></div>
      <p className="note">Showing {rows.length} of the top {b.rules.length} rules. Association is not causation: co-purchase can reflect catalogue layout or wholesale buyers restocking a range.</p></Card>
  </>;
}
