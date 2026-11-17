// One read-only statement over the published marts. Queries run in the viewer's own browser
// (DuckDB-WASM over public Parquet), so this guards against confusing output, not data access.
const FORBIDDEN = /\b(copy|attach|detach|install|load|pragma|set|reset|create|insert|update|delete|drop|alter|export|import|call|checkpoint|vacuum|read_csv\w*|read_json\w*|read_parquet|read_text|read_blob|glob|httpfs)\b/i;

export function checkSql(sql: string): string | null {
  const text = sql.trim().replace(/;\s*$/, '');
  if (!text) return 'Empty query.';
  if (text.length > 4000) return 'Query is too long.';
  if (text.includes(';')) return 'Only one statement is allowed.';
  if (!/^(select|with|from)\b/i.test(text)) return 'Only SELECT queries are allowed.';
  const stripped = text.replace(/'(?:[^']|'')*'/g, "''");
  const bad = stripped.match(FORBIDDEN);
  return bad ? `"${bad[0]}" is not allowed in the SQL Lab.` : null;
}
