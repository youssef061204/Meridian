import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// `--mode showcase` builds the static public showcase with recorded responses (see src/showcase.ts).
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  publicDir: mode === 'showcase' ? 'showcase' : 'public',
  server: { proxy: { '/api': 'http://127.0.0.1:8010' } },
  build: { outDir: 'dist' },
}));
