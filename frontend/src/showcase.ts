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
  if (filtered) return json({ detail: 'Filters query DuckDB live, so they need the local API. This public showcase replays unfiltered recordings.' }, 404);
  if (path === '/api/overview') return recorded('overview');
  if (path === '/api/lineage') return recorded(`lineage-${url.searchParams.get('metric') || 'net_recorded_gbp'}`);
  if (path === '/api/forecasts') return recorded('forecasts');
  if (path === '/api/trust') return recorded('trust');
  if (path === '/api/replay') return recorded(`replay-${stage}`);
  if (path === '/api/replay/correction') return recorded(`correction-${stage}`);
  return json({ detail: 'Not available in the public showcase.' }, 404);
}

if (SHOWCASE) {
  const original = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, window.location.origin);
    return url.pathname.startsWith('/api/') ? handle(url, init) : original(input, init);
  };
}
