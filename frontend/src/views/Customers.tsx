import { useState } from 'react';
import { motion } from 'motion/react';
import type { Insights } from '../types';
import { LineChart, Scatter } from '../charts';
import { Bars, Card, CardHead, CountUp, PageHead, SERIES, compact, int, money0, pct } from '../ui';

const MODEL_NAMES: Record<string, string> = { logistic_regression: 'Logistic regression', gradient_boosting: 'Gradient boosting', recency_rule: 'Recency-only rule' };
const FEATURE_NAMES: Record<string, string> = { recency_days: 'Days since last order', frequency: 'Distinct orders', monetary_gbp: 'Lifetime value', tenure_days: 'Tenure', avg_invoice_gbp: 'Avg order value',
  distinct_products: 'Distinct products', invoices_last_90d: 'Orders in last 90 days', active_months: 'Active months', adjustment_share: 'Return / adjustment share', is_uk: 'UK buyer' };

export function CustomersView({ data }: { data: Insights }) {
  const seg = data.segmentation, rm = data.return_model;
  const [active, setActive] = useState<string | null>(null);
  const colors = Object.fromEntries(seg.segments.map((s, i) => [s.segment, SERIES[i % SERIES.length]]));
  const best = rm.models[rm.selected];
  return <>
    <PageHead index="§02" kicker="Customers · segmentation and prediction" title="Know who comes" accent="back." lede={`${int(seg.customers)} identified buyers clustered on recency, frequency and value, plus a time-split model that ranks who will order again in the next 90 days.`}
      meta={<><span className="chip signal"><span className="dot" />k-means · k = {seg.k}</span><span className="meta-line">reference day <strong>{seg.reference_day}</strong></span></>} />

    <div className="segment-cards" style={{ marginBottom: 18 }}>
      {seg.segments.map((s, i) => <motion.button key={s.segment} className={`segment-card ${active === s.segment ? 'active' : ''}`} style={{ ['--seg' as string]: colors[s.segment] }}
        onClick={() => setActive(active === s.segment ? null : s.segment)} aria-pressed={active === s.segment}
        initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: .15 + i * .08, duration: .6, ease: [0.22, 1, 0.36, 1] }}>
        <span className="eyebrow">{pct(s.share_of_customers, 0)} of buyers · {pct(s.share_of_value, 0)} of value</span>
        <h3>{s.segment}</h3>
        <dl><div><dt>Customers</dt><dd>{int(s.customers)}</dd></div><div><dt>Median recency</dt><dd>{int(s.median_recency_days)} d</dd></div>
          <div><dt>Median orders</dt><dd>{int(s.median_frequency)}</dd></div><div><dt>Median value</dt><dd>{money0(s.median_monetary_gbp)}</dd></div>
          <div style={{ gridColumn: 'span 2' }}><dt>Mean predicted return (90 d)</dt><dd style={{ color: colors[s.segment] }}>{pct(s.mean_return_probability, 0)}</dd></div></dl>
        <div className="share-bar"><motion.span style={{ background: colors[s.segment], display: 'block' }} initial={{ width: 0 }} animate={{ width: `${s.share_of_value * 100}%` }} transition={{ delay: .6 + i * .1, duration: 1 }} /></div>
      </motion.button>)}
    </div>

    <div className="bento">
      <Card span={8}><CardHead eyebrow={<><b>01</b> · RFM space</>} title="Segments in recency × value space"><span className="chart-legend">{seg.segments.map(s => <span key={s.segment}><i style={{ background: colors[s.segment] }} />{s.segment}</span>)}</span></CardHead>
        <Scatter dots={seg.scatter.map(p => ({ x: p.r, y: Math.max(1, p.m), group: p.s }))} colors={colors} active={active} xLabel="Days since last order (log)" yLabel="Lifetime value, log GBP" label="Scatter of sampled customers by recency and value, coloured by segment" />
        <p className="note">Stratified sample of up to 260 buyers per segment. Click a segment card to isolate it. {seg.method}.</p></Card>
      <Card span={4} delay={.08}><CardHead eyebrow={<><b>02</b> · model selection</>} title="Choosing k honestly" />
        <LineChart label="Silhouette score by number of clusters" series={[{ name: 'Silhouette', color: 'var(--signal)', points: seg.k_trials.map(t => ({ x: t.k, y: t.silhouette })), area: true }]}
          xLabel="k" xFmt={v => String(Math.round(v))} yFmt={v => v.toFixed(2)} height={220} />
        <p className="note">Silhouette on a fixed 3,000-buyer sample. The chosen k is the largest within 0.02 of the best, so segments stay actionable without overfitting the geometry.</p></Card>
    </div>

    <h2 className="section-title">Predictive · repeat purchase in the next {rm.design.test_label_window ? 90 : ''} days</h2>
    <div className="bento">
      <Card span={4}><CardHead eyebrow={<><b>03</b> · headline</>} title={rm.question} />
        <div className="stat-row">
          <div className="stat"><span className="stat-label">Test AUC · {MODEL_NAMES[rm.selected]}</span><span className="stat-value" style={{ color: 'var(--lime)' }}><CountUp value={best.auc} format={v => v.toFixed(3)} /></span></div>
          <div className="stat"><span className="stat-label">Recency-only baseline</span><span className="stat-value sm" style={{ color: 'var(--muted)' }}>{rm.models.recency_rule.auc.toFixed(3)}</span></div>
          <div className="stat"><span className="stat-label">Top decile hit rate</span><span className="stat-value sm">{pct(rm.deciles[0].observed_rate, 0)}</span></div>
          <div className="stat"><span className="stat-label">Brier score</span><span className="stat-value sm">{best.brier?.toFixed(3)}</span></div>
        </div>
        <ol className="timeline" style={{ marginTop: 22 }}>
          <li><strong>{rm.design.train_cutoff}</strong>train features cut off · {int(rm.design.train_customers)} buyers, base rate {pct(rm.design.train_base_rate, 0)}</li>
          <li><strong>{rm.design.test_cutoff}</strong>test features cut off · {int(rm.design.test_customers)} buyers, base rate {pct(rm.design.test_base_rate, 0)}</li>
          <li><strong>{rm.design.test_label_window[1]}</strong>test labels end at the last complete day</li>
        </ol>
        <p className="note">{rm.design.leakage_guard}.</p></Card>
      <Card span={4} delay={.06}><CardHead eyebrow={<><b>04</b> · discrimination</>} title="ROC on the untouched window" />
        <LineChart label="ROC curves for the return model and the recency baseline" diagonal domain={{ x: [0, 1], y: [0, 1] }} height={260} xLabel="False positive rate" xFmt={v => v.toFixed(1)} yFmt={v => v.toFixed(1)}
          series={Object.entries(rm.models).map(([k, m], i) => ({ name: `${MODEL_NAMES[k]} ${m.auc.toFixed(3)}`, color: [ 'var(--signal)', 'var(--sky)', 'var(--faint)'][i], dashed: k === 'recency_rule', points: m.roc.map(p => ({ x: p.fpr, y: p.tpr })) }))} /></Card>
      <Card span={4} delay={.12}><CardHead eyebrow={<><b>05</b> · calibration</>} title="Predicted vs observed, by decile" />
        <LineChart label="Mean predicted probability and observed return rate by decile" height={260} xLabel="Decile (1 = highest score)" xFmt={v => String(Math.round(v))} yFmt={v => pct(v, 0)} domain={{ x: [1, 10], y: [0, 1] }}
          series={[{ name: 'Observed', color: 'var(--lime)', points: rm.deciles.map(d => ({ x: d.decile, y: d.observed_rate })) }, { name: 'Predicted', color: 'var(--signal)', dashed: true, points: rm.deciles.map(d => ({ x: d.decile, y: d.mean_predicted })) }]} /></Card>

      <Card span={6}><CardHead eyebrow={<><b>06</b> · what the model uses</>} title="Permutation importance (AUC drop)" />
        <Bars rows={rm.importance.slice(0, 8).map(f => ({ label: FEATURE_NAMES[f.feature] ?? f.feature, sub: `± ${f.std.toFixed(3)}`, value: f.auc_drop }))} format={v => v.toFixed(3)} /></Card>
      <Card span={6} delay={.08} className="unsafe-card" aria-label="Value at risk"><CardHead eyebrow={<><b>07</b> · action list</>} title="Revenue history at risk of going quiet" />
        <div className="stat-row">
          <div className="stat"><span className="stat-label">Buyers under 33% return probability</span><span className="stat-value"><CountUp value={rm.value_at_risk.customers} /></span></div>
          <div className="stat"><span className="stat-label">Their historical value</span><span className="stat-value" style={{ color: 'var(--rose)' }}><CountUp value={rm.value_at_risk.historical_value_gbp} format={v => compact(v)} /></span></div>
        </div>
        <Bars rows={Object.entries(rm.value_at_risk.by_segment).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: k, value: v }))} format={int} />
        <p className="note">Scored at the reference day with the model refit on the latest labelled window. Use it to prioritise a win-back campaign; it says nothing about whether outreach would change behaviour.</p></Card>
    </div>
    <section className="callout"><span className="eyebrow">Limitations</span><ul className="limits">{rm.limitations.map(l => <li key={l}>{l}</li>)}</ul></section>
  </>;
}
