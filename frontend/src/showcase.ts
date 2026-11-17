// Public read-only showcase. Built only with `vite build --mode showcase`.
// Every response is a verbatim recording from the local stack (scripts/record_showcase.py).
// Spark, Kafka, DuckDB and the worker run only locally; nothing here recomputes data.

export const SHOWCASE = import.meta.env.MODE === 'showcase';
export const REPO_URL = import.meta.env.VITE_REPO_URL as string | undefined;

type Stage = 'baseline' | 'incident' | 'repaired';
let stage: Stage = 'baseline';
let jobs: Record<string, unknown> | null = null;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const recorded = (name: string) => fetch(`/recorded/${name}.json`);

/** Filtered overview computed by DuckDB-WASM over the published accepted-line mart (same accepted lines as the snapshot). */
async function browserOverview(url: URL) {
  const { query } = await import('./duck');
  const where: string[] = [], params: unknown[] = [];
  const country = url.searchParams.get('country'), start = url.searchParams.get('start'), end = url.searchParams.get('end');
  if (country) { where.push('country = ?'); params.push(country); }
  if (start) { where.push('date >= CAST(? AS DATE)'); params.push(start); }
  if (end) { where.push('date <= CAST(? AS DATE)'); params.push(end); }
  const w = where.length ? `WHERE ${where.join(' AND ')}` : '';
  const base = await (await recorded('overview')).json();
  const [m, daily, products, countries] = await Promise.all([
    query(`SELECT coalesce(sum(CASE WHEN line_amount>0 THEN line_amount END),0) AS gross_positive_gbp, coalesce(sum(CASE WHEN line_amount<0 THEN line_amount END),0) AS negative_adjustments_gbp,
      coalesce(sum(line_amount),0) AS net_recorded_gbp, coalesce(sum(CASE WHEN quantity>0 THEN quantity END),0) AS positive_units, count(customer_id) AS known_customer_rows, count(*) AS accepted_rows FROM sales ${w}`, params),
    query(`SELECT date, sum(line_amount) AS net_recorded_gbp, sum(CASE WHEN line_amount>0 THEN line_amount ELSE 0 END) AS gross_positive_gbp, sum(CASE WHEN line_amount<0 THEN line_amount ELSE 0 END) AS negative_adjustments_gbp, sum(CASE WHEN quantity>0 THEN quantity ELSE 0 END) AS positive_units FROM sales ${w} GROUP BY 1 ORDER BY 1`, params),
    query(`SELECT s.stock_code, any_value(p.description) AS description, sum(s.line_amount) AS net_recorded_gbp, sum(CASE WHEN s.quantity>0 THEN s.quantity ELSE 0 END) AS positive_units FROM sales s LEFT JOIN products p USING (stock_code) ${w.replace(/\b(country|date)\b/g, 's.$1')} GROUP BY 1 ORDER BY 3 DESC LIMIT 10`, params),
    query(`SELECT country, sum(line_amount) AS net_recorded_gbp, count(*) AS rows FROM sales ${w} GROUP BY 1 ORDER BY 2 DESC LIMIT 10`, params),
  ]);
  const round = (v: unknown) => Math.round(Number(v) * 100) / 100;
  const metrics = Object.fromEntries(Object.entries(m.rows[0]).map(([k, v]) => [k, k.endsWith('_gbp') ? round(v) : Number(v)]));
  return { ...base, metrics, daily: daily.rows, products: products.rows, countries: countries.rows, cohorts: [], comparison: null, computed_in_browser: true };
}

export function showcaseAllows(kind: string): boolean {
  if (kind === 'incident') return stage === 'baseline';
  if (kind === 'repair') return stage === 'incident';
  if (kind === 'reset') return stage !== 'baseline';
  return false; // interrupted-publish was not recorded for the public showcase
}

async function handle(url: URL, init?: RequestInit): Promise<Response> {
  const path = url.pathname;
  if (path === '/api/demo/jobs' && init?.method === 'POST') {
    const kind = JSON.parse(String(init.body || '{}')).kind as string;
    if (!showcaseAllows(kind)) return json({ detail: 'This step is not available in the recorded showcase. Run the project locally for every control.' }, 409);
    jobs ??= await (await recorded('jobs')).json();
    stage = kind === 'incident' ? 'incident' : kind === 'repair' ? 'repaired' : 'baseline';
    return json(jobs![kind]);
  }
  const filtered = ['country', 'start', 'end'].some(key => url.searchParams.get(key));
  if (filtered && path === '/api/overview') return browserOverview(url).then(body => json(body)).catch(e => json({ detail: `In-browser query failed: ${e instanceof Error ? e.message : e}` }, 500));
  if (filtered) return json({ detail: 'Filtered lineage needs the local API (it reads source workbook coordinates). This public showcase replays unfiltered recordings.' }, 404);
  if (path === '/api/overview') return recorded('overview');
  if (path === '/api/lineage') return recorded(`lineage-${url.searchParams.get('metric') || 'net_recorded_gbp'}`);
  if (path === '/api/forecasts') return recorded('forecasts');
  if (path === '/api/insights') return recorded('insights');
  if (path === '/api/trust') return recorded('trust');
  if (path === '/api/replay') return recorded(`replay-${stage}`);
  if (path === '/api/replay/correction') return recorded(`correction-${stage}`);
  return json({ detail: 'Not available in the public showcase.' }, 404);
}

if (SHOWCASE) {
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, window.location.origin);
    // /api/ask is a real serverless function on the public deployment; everything else is recorded.
    return url.pathname.startsWith('/api/') && url.pathname !== '/api/ask' ? handle(url, init) : original(input, init);
  };
}
