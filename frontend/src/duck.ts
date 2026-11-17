// DuckDB-WASM, started on demand. Marts are gzip CSV next to the page: CSV and gzip are in
// DuckDB's core, so nothing is fetched from extensions.duckdb.org at runtime.
import type { AsyncDuckDB } from '@duckdb/duckdb-wasm';
import martsSql from '../shared/browser-marts.sql?raw';

let ready: Promise<AsyncDuckDB> | null = null;
export type Result = { columns: string[]; types: string[]; rows: Record<string, unknown>[]; ms: number };

async function start(): Promise<AsyncDuckDB> {
  const duckdb = await import('@duckdb/duckdb-wasm');
  const [{ default: mvpWasm }, { default: mvpWorker }, { default: ehWasm }, { default: ehWorker }] = await Promise.all([
    import('@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url'), import('@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url'),
    import('@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url'), import('@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url'),
  ]);
  const bundle = await duckdb.selectBundle({ mvp: { mainModule: mvpWasm, mainWorker: mvpWorker }, eh: { mainModule: ehWasm, mainWorker: ehWorker } });
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), new Worker(bundle.mainWorker!));
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  // Some hosts serve .gz with Content-Encoding: gzip, so the browser has already inflated it. Sniff the magic bytes.
  const registered = await Promise.all(['sales', 'products', 'customers'].map(async name => {
    const response = await fetch(`/data/${name}.csv.gz`);
    if (!response.ok) throw new Error(`Mart ${name} unavailable (${response.status})`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    const file = bytes[0] === 0x1f && bytes[1] === 0x8b ? `${name}.csv.gz` : `${name}.csv`;
    await db.registerFileBuffer(file, bytes);
    return [`'${name}.csv.gz'`, `'${file}'`] as const;
  }));
  const con = await db.connect();
  await con.query('SET autoinstall_known_extensions = false');
  await con.query('SET autoload_known_extensions = false');
  let sql = martsSql.split('\n').filter(l => !l.startsWith('--')).join('\n');
  for (const [from, to] of registered) sql = sql.replaceAll(from, to);
  for (const statement of sql.split(';').map(s => s.trim()).filter(Boolean)) await con.query(statement);
  for (const [, file] of registered) await db.dropFile(file.slice(1, -1));
  await con.close();
  return db;
}

export function engine() { ready ??= start().catch(e => { ready = null; throw e; }); return ready; }

const plain = (v: unknown): unknown => typeof v === 'bigint' ? Number(v) : v instanceof Date ? v.toISOString() : v;
export async function query(sql: string, params: unknown[] = []): Promise<Result> {
  const db = await engine();
  const con = await db.connect();
  const t = performance.now();
  try {
    let table;
    if (params.length) { const stmt = await con.prepare(sql); table = await stmt.query(...params); await stmt.close(); }
    else table = await con.query(sql);
    const columns = table.schema.fields.map(f => f.name), types = table.schema.fields.map(f => String(f.type));
    const rows = table.toArray().map(r => { const o = r.toJSON() as Record<string, unknown>; for (const k of columns) o[k] = plain(o[k]); return o; });
    // Arrow dates/timestamps arrive as epoch milliseconds; decimals as scaled values: normalise for display.
    columns.forEach((c, i) => {
      if (/^Date|^Timestamp/.test(types[i])) rows.forEach(r => { if (typeof r[c] === 'number') r[c] = new Date(r[c] as number).toISOString().slice(0, /^Date/.test(types[i]) ? 10 : 19).replace('T', ' '); });
      const scale = /^Decimal\[\d+e(\d+)\]/.exec(types[i]);
      if (scale) rows.forEach(r => { if (r[c] != null) r[c] = Number(r[c]) / 10 ** Number(scale[1]); });
    });
    return { columns, types, rows, ms: performance.now() - t };
  } finally { await con.close(); }
}
