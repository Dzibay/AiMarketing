import { defineConfig } from 'astro/config'
import vue from '@astrojs/vue'
import sitemap from '@astrojs/sitemap'
import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

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

// IndexNow: ключ подтверждается файлом {ключ}.txt в корне сайта; отправка адресов — npm run indexnow.
const indexNowKey = (process.env.INDEXNOW_KEY || '').trim()
if (indexNowKey && !/^[A-Za-z0-9-]{8,128}$/.test(indexNowKey)) throw new Error('INDEXNOW_KEY: 8–128 символов, латиница, цифры, дефис')

function indexNowKeyFile() {
  return {
    name: 'tolk:indexnow-key',
    hooks: {
      'astro:build:done': ({ dir }) => {
        if (indexNowKey) writeFileSync(fileURLToPath(new URL(`${indexNowKey}.txt`, dir)), indexNowKey)
      },
    },
  }
}

// Карты сайта: блог и словарь — отдельными файлами (sitemap-blog-0.xml, sitemap-slovar-0.xml), остальное —
// sitemap-pages-0.xml; общий индекс — sitemap-index.xml (он и в robots.txt).
const chunk = (prefix) => (item) => (new URL(item.url).pathname.startsWith(prefix) ? item : undefined)

export default defineConfig({
  site: 'https://tolk-ads.ru',
  trailingSlash: 'always',
  build: { format: 'directory' },
  integrations: [
    vue(),
    sitemap({
      filter: (page) => !page.includes('/404'),
      chunks: { blog: chunk('/blog/'), slovar: chunk('/slovar/') },
    }),
    indexNowKeyFile(),
  ],
  server: { port: 4321, host: true },
  vite: {
    plugins: [apiProxyFirst()],
    server: {
      proxy: { '/api': { target: apiTarget, changeOrigin: true } },
      fs: { allow: ['..'] },
    },
  },
})
