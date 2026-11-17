// Vercel serverless function: natural-language question -> one read-only DuckDB SELECT.
// The SQL is returned, never executed here: the browser runs it in DuckDB-WASM over public marts.
// Without ANTHROPIC_API_KEY the endpoint answers 501 and the UI falls back to curated queries.
import Anthropic from '@anthropic-ai/sdk';
import type { VercelRequest, VercelResponse } from '@vercel/node';
import spec from '../shared/ask.json' with { type: 'json' };
import { checkSql } from '../shared/sqlguard.js';

const MODEL = process.env.MERIDIAN_ASK_MODEL || 'claude-opus-5-5';
const OUTPUT_SCHEMA = {
  type: 'object',
  properties: {
    answerable: { type: 'boolean' },
    sql: { type: 'string' },
    explanation: { type: 'string' },
    chart: { type: 'string', enum: ['line', 'bar', 'table'] },
  },
  required: ['answerable', 'sql', 'explanation', 'chart'],
  additionalProperties: false,
} as const;

// Best-effort per-instance limiter: protects the API bill from casual abuse, not a determined attacker.
const hits = new Map<string, number[]>();
function limited(ip: string, perMinute = Number(process.env.MERIDIAN_ASK_PER_MINUTE || 6)) {
  const now = Date.now(), recent = (hits.get(ip) || []).filter(t => now - t < 60_000);
  recent.push(now); hits.set(ip, recent);
  return recent.length > perMinute;
}

const client = process.env.ANTHROPIC_API_KEY ? new Anthropic() : null;
// Stable prefix (system + schema + examples) is cached across questions.
const SYSTEM = `${spec.system}\n\n${spec.schema}\n\nExamples:\n${spec.examples.map(e => `Q: ${e.q}\nSQL: ${e.sql}\nchart: ${e.chart}`).join('\n\n')}`;

export default async function handler(req: VercelRequest, res: VercelResponse) {
  if (req.method !== 'POST') return res.status(405).json({ detail: 'POST a JSON body {"question": "..."}' });
  if (!client) return res.status(501).json({ detail: 'AI translation is not configured on this deployment (no ANTHROPIC_API_KEY). Curated queries still work.' });
  const question = typeof req.body?.question === 'string' ? req.body.question.trim() : '';
  if (!question || question.length > 300) return res.status(400).json({ detail: 'Ask a question of 1–300 characters.' });
  const ip = String(req.headers['x-forwarded-for'] || 'unknown').split(',')[0];
  if (limited(ip)) return res.status(429).json({ detail: 'Too many questions in a minute. Try again shortly.' });

  try {
    const response = await client.beta.messages.create({
      model: MODEL,
      max_tokens: 4000,
      betas: ['server-side-fallback-2026-07-01'],
      fallbacks: 'default',
      output_config: { effort: 'low', format: { type: 'json_schema', schema: OUTPUT_SCHEMA } },
      system: [{ type: 'text', text: SYSTEM, cache_control: { type: 'ephemeral' } }],
      messages: [{ role: 'user', content: question }],
    });
    if (response.stop_reason === 'refusal') return res.status(422).json({ detail: 'The model declined this question.' });
    if (response.stop_reason === 'max_tokens') return res.status(502).json({ detail: 'The model ran out of room before finishing. Try a simpler question.' });
    const text = response.content.find(b => b.type === 'text');
    if (!text || text.type !== 'text') return res.status(502).json({ detail: 'No answer returned.' });
    const out = JSON.parse(text.text) as { answerable: boolean; sql: string; explanation: string; chart: string };
    if (out.answerable) {
      const problem = checkSql(out.sql);
      if (problem) return res.status(422).json({ detail: `Generated SQL was rejected: ${problem}`, sql: out.sql });
    }
    return res.status(200).json({ ...out, model: response.model, source: 'claude' });
  } catch (error) {
    if (error instanceof Anthropic.RateLimitError) return res.status(429).json({ detail: 'The AI service is rate limited. Try again shortly.' });
    if (error instanceof Anthropic.AuthenticationError) return res.status(501).json({ detail: 'The AI service key is invalid on this deployment.' });
    if (error instanceof Anthropic.APIError) return res.status(502).json({ detail: `AI service error (${error.status}).` });
    return res.status(502).json({ detail: 'AI service unavailable.' });
  }
}
