import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  envDir: '..', // single .env at the repo root (only VITE_* values reach the browser)
  server: {
    port: 5173,
    // dev: the browser talks to /api on the same origin; Vite forwards to FastAPI
    proxy: { '/api': { target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true } },
  },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'], pool: 'threads' },
})
