import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Built assets are served by the FastAPI app itself (app/main.py mounts web/dist as
// static files) — one deployable process, not two. The dev server proxies API calls
// to the FastAPI process running separately on :8000, so `npm run dev` works against
// a real backend without CORS configuration on either side.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
  },
  server: {
    proxy: {
      '/documents': 'http://127.0.0.1:8000',
      '/extract': 'http://127.0.0.1:8000',
      '/query': 'http://127.0.0.1:8000',
      '/reset': 'http://127.0.0.1:8000',
      '/settings': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
