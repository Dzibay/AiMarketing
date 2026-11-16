import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiTarget = process.env.API_PROXY || 'http://127.0.0.1:8000'
const proxy = { target: apiTarget, changeOrigin: true, secure: true }

export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    // Статьи справки лежат в docs/help (их читает и бэкенд) — разрешаем dev-серверу отдавать их.
    fs: { allow: ['.', '../docs/help'] },
    proxy: {
      '/api': proxy,
    },
  },
})
