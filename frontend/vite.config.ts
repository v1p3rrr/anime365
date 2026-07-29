import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    // Собранная статика уезжает внутрь backend-пакета и раздаётся FastAPI.
    outDir: '../backend/app/static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // В разработке фронт живёт отдельно и ходит в backend через прокси.
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
