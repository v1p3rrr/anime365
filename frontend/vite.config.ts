import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    // Собранная статика уезжает внутрь backend-пакета и раздаётся FastAPI.
    outDir: '../backend/app/static',
    emptyOutDir: true,
  },
  worker: {
    // Рендерер субтитров создаёт воркер. По умолчанию Vite собирает воркеры
    // в iife, а это несовместимо с разделением на чанки — а чанки нам нужны:
    // wasm рендерера весит 4 МБ и грузится только при открытии плеера.
    format: 'es',
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
