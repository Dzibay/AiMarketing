import { defineConfig } from 'astro/config'
import vue from '@astrojs/vue'
import sitemap from '@astrojs/sitemap'

// Сайт tolk-ads.ru: статичный HTML + острова Vue. Кабинет — отдельное SPA (frontend/), nginx делит адреса.
const apiTarget = process.env.API_PROXY || 'http://127.0.0.1:8000'

export default defineConfig({
  site: 'https://tolk-ads.ru',
  trailingSlash: 'always',
  build: { format: 'directory' },
  integrations: [vue(), sitemap({ filter: (page) => !page.includes('/404') })],
  server: { port: 4321, host: true },
  vite: {
    server: {
      proxy: { '/api': { target: apiTarget, changeOrigin: true } },
      fs: { allow: ['..'] },
    },
  },
})
