// IndexNow: сообщить Яндексу (и всем поисковикам протокола) о новых и изменённых страницах.
//   INDEXNOW_KEY=… npm run indexnow                      — все адреса из карт сайта в dist/ (после сборки)
//   INDEXNOW_KEY=… npm run indexnow -- /blog/x/ /slovar/   — только перечисленные адреса
// Ключ тот же, что при сборке: файл {ключ}.txt должен уже лежать на tolk-ads.ru, иначе Яндекс ответит 403.
import { readdirSync, readFileSync } from 'node:fs'

const SITE = 'https://tolk-ads.ru'
const ENDPOINT = 'https://yandex.com/indexnow'
const key = (process.env.INDEXNOW_KEY || '').trim()
if (!/^[A-Za-z0-9-]{8,128}$/.test(key)) {
  console.error('Задайте INDEXNOW_KEY (8–128 символов: латиница, цифры, дефис) — тот же, что при сборке сайта')
  process.exit(1)
}

function fromSitemaps() {
  const dir = new URL('../dist/', import.meta.url)
  let files
  try {
    files = readdirSync(dir).filter((f) => /^sitemap-.+-\d+\.xml$/.test(f))
  } catch {
    files = []
  }
  if (!files.length) {
    console.error('Нет карт сайта в site/dist — сначала npm run build')
    process.exit(1)
  }
  return files.flatMap((f) => [...readFileSync(new URL(f, dir), 'utf8').matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]))
}

const args = process.argv.slice(2)
const urls = [...new Set(args.length ? args.map((a) => new URL(a, SITE).href) : fromSitemaps())]
const host = new URL(SITE).host

// Протокол принимает до 10 000 адресов за запрос.
for (let i = 0; i < urls.length; i += 10000) {
  const urlList = urls.slice(i, i + 10000)
  const res = await fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ host, key, keyLocation: `${SITE}/${key}.txt`, urlList }),
  })
  const text = await res.text().catch(() => '')
  console.log(`${res.status} — ${urlList.length} адресов${text ? `: ${text.slice(0, 300)}` : ''}`)
  if (!res.ok && res.status !== 202) process.exitCode = 1
}
