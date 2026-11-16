/**
 * Посетитель сайта: случайный идентификатор браузера (localStorage) — общий с лендингом, домен один.
 * Открытые страницы кабинета (вход, регистрация) сообщают серверу о просмотре — для визитов и источника;
 * при регистрации — по почте или через Яндекс — идентификатор уходит на сервер вместе с реф-кодом.
 * Как это делает лендинг — site/src/layouts/Base.astro (тот же ключ и тот же запрос).
 */
const KEY = 'tolk-visitor'
const ID_RE = /^[A-Za-z0-9_-]{16,64}$/
const UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term']

function makeId() {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

export function visitorId() {
  try {
    let id = localStorage.getItem(KEY) || ''
    if (!ID_RE.test(id)) {
      id = makeId()
      localStorage.setItem(KEY, id)
    }
    return id
  } catch {
    return ''
  }
}

/** Просмотр страницы: path — путь, query — метки и реф-код из адреса (до того, как их уберут). */
export function trackVisit(path, query = {}) {
  const visitor = visitorId()
  if (!visitor) return
  const utm = {}
  for (const key of UTM_KEYS) if (query[key]) utm[key] = String(query[key]).slice(0, 200)
  fetch('/api/site/visit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      visitor,
      path,
      referrer: document.referrer || '',
      utm,
      ref: query.ref ? String(query.ref).slice(0, 40) : '',
    }),
    keepalive: true,
  }).catch(() => null)
}
