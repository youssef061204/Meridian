import { useCallback, useEffect, useState } from 'react';

const cache = new Map<string, unknown>();

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) {
    let detail = '';
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : ''; } catch { /* a proxy can return HTML */ }
    throw new Error(detail || `The data service returned ${response.status}. Check the local API and published snapshot.`);
  }
  return response.json() as Promise<T>;
}

/** Fetch with a session cache so moving between views is instant after the first visit. */
export function useApi<T>(path: string | null): { data: T | null; error: string; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(() => (path && cache.has(path) ? cache.get(path) as T : null));
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(!!path && !cache.has(path));
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => { if (path) cache.delete(path); setNonce(n => n + 1); }, [path]);
  useEffect(() => {
    if (!path) { setData(null); setLoading(false); return; }
    if (cache.has(path)) { setData(cache.get(path) as T); setLoading(false); setError(''); return; }
    const controller = new AbortController();
    setLoading(true); setError('');
    getJson<T>(path, controller.signal).then(value => { cache.set(path, value); setData(value); })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'The data request failed.'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [path, nonce]);
  return { data, error, loading, reload };
}
