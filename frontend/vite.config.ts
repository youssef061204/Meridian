import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// `--mode showcase` builds the static public showcase with recorded responses (see src/showcase.ts).
// Both modes serve showcase/ as the public dir: it holds the browser marts (data/*.parquet) the SQL Lab reads.
export default defineConfig({
  plugins: [react()],
  publicDir: 'showcase',
  server: { proxy: { '/api': 'http://127.0.0.1:8010' } },
  build: { outDir: 'dist', chunkSizeWarningLimit: 900 },
  optimizeDeps: { exclude: ['@duckdb/duckdb-wasm'] },
});
