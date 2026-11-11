import { defineConfig } from 'astro/config'
import vue from '@astrojs/vue'
import sitemap from '@astrojs/sitemap'

// Сайт tolk-ads.ru: статичный HTML + острова Vue. Кабинет — отдельное SPA (frontend/), nginx делит адреса.
const apiTarget = process.env.API_PROXY || 'http://127.0.0.1:8000'

// В dev Astro ставит свои проверки (в том числе trailingSlash: 'always') в самое начало цепочки middleware —
// раньше прокси Vite, и /api/site/check без слэша на конце получал 404 от Astro. Возвращаем прокси первым:
// хук после сервера (return () => …) с enforce: 'post' выполняется уже после хука Astro.
function apiProxyFirst() {
  return {
    name: 'tolk:api-proxy-first',
    enforce: 'post',
    configureServer(server) {
      return () => {
        const stack = server.middlewares.stack
        const i = stack.findIndex((layer) => layer.handle?.name === 'viteProxyMiddleware')
        if (i > 0) stack.unshift(...stack.splice(i, 1))
      }
    },
  }
}

export default defineConfig({
  site: 'https://tolk-ads.ru',
  trailingSlash: 'always',
  build: { format: 'directory' },
  integrations: [vue(), sitemap({ filter: (page) => !page.includes('/404') })],
  server: { port: 4321, host: true },
  vite: {
    plugins: [apiProxyFirst()],
    server: {
      proxy: { '/api': { target: apiTarget, changeOrigin: true } },
      fs: { allow: ['..'] },
    },
  },
})
