/**
 * Реф-ссылка ?ref=код на любой странице: код запоминается при первом заходе (90 дней, следующая ссылка
 * первую не перебивает), о переходе сайт сообщает один раз. При регистрации — по почте или через
 * Яндекс — код уходит на сервер и привязывается к аккаунту; после этого он больше не нужен.
 */
const KEY = 'aimarketing-ref'
const TTL_MS = 90 * 24 * 60 * 60 * 1000
const CODE_RE = /^[a-z0-9_-]{2,40}$/

function read() {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || 'null')
    if (value && CODE_RE.test(value.code) && Date.now() - value.at < TTL_MS) return value
  } catch {
    // повреждённое значение — как будто его нет
  }
  return null
}

/** Код из адреса: запомнить, если своего ещё нет, и засчитать переход. */
export function captureRef(raw) {
  const code = String(raw || '')
    .trim()
    .toLowerCase()
  if (!CODE_RE.test(code) || read()) return
  localStorage.setItem(KEY, JSON.stringify({ code, at: Date.now() }))
  fetch('/api/site/ref', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  })
    .then((response) => {
      // Такой ссылки нет или её выключили — не держим код, следующая настоящая ссылка его заменит.
      if (response.status === 404) clearRef()
    })
    .catch(() => null)
}

export function currentRef() {
  return read()?.code || ''
}

export function clearRef() {
  localStorage.removeItem(KEY)
}
