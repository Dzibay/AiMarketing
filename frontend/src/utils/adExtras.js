// Быстрые ссылки, уточнения и картинки объявления: пределы Директа и сборка тела запроса.
export const SITELINK_TITLE_LEN = 30
export const SITELINK_DESC_LEN = 60
export const SITELINK_GROUP_LEN = 66
export const MAX_SITELINKS = 8
export const CALLOUT_LEN = 25
export const MAX_CALLOUTS = 10
export const IMAGE_MAX_BYTES = 5 * 1024 * 1024

export function emptyExtras() {
  return { sitelinks: [], callouts: '', images: [] }
}

// Ответ GET /direct/ads/{id}/extras → состояние формы.
export function extrasFromApi(data) {
  return {
    sitelinks: (data?.sitelinks || []).map((l) => ({ title: l.title || '', href: l.href || '', description: l.description || '' })),
    callouts: (data?.callouts || []).join('\n'),
    images: (data?.images || []).map((i) => ({ hash: i.hash, preview_url: i.preview_url || '' })),
  }
}

function filledSitelinks(extras) {
  return (extras.sitelinks || [])
    .map((l) => ({ title: l.title.trim(), href: l.href.trim(), description: (l.description || '').trim() }))
    .filter((l) => l.title || l.href || l.description)
}

function calloutLines(extras) {
  return [...new Set(String(extras.callouts || '').split('\n').map((l) => l.trim()).filter(Boolean))]
}

// Тело для API: { sitelinks, callouts, image_hashes }.
export function extrasPayload(extras) {
  return {
    sitelinks: filledSitelinks(extras),
    callouts: calloutLines(extras),
    image_hashes: (extras.images || []).map((i) => i.hash),
  }
}

export function extrasEmpty(extras) {
  const p = extrasPayload(extras)
  return !p.sitelinks.length && !p.callouts.length && !p.image_hashes.length
}

// Только изменившиеся части: при правке не плодим в кабинете одинаковые наборы ссылок.
export function extrasDiff(before, after) {
  const a = extrasPayload(before)
  const b = extrasPayload(after)
  const out = {}
  for (const key of Object.keys(b)) if (JSON.stringify(a[key]) !== JSON.stringify(b[key])) out[key] = b[key]
  return Object.keys(out).length ? out : null
}

export function groupLengths(extras) {
  const links = extras.sitelinks || []
  const sum = (part) => part.reduce((n, l) => n + l.title.trim().length, 0)
  return [sum(links.slice(0, 4)), sum(links.slice(4, 8))]
}

// Та же проверка, что на сервере, — чтобы ошибка была видна до отправки.
export function extrasProblem(extras, maxImages = 1) {
  const links = filledSitelinks(extras)
  for (const l of links) {
    if (!l.title || !l.href) return 'У быстрой ссылки нужны и заголовок, и адрес'
    if (l.title.length > SITELINK_TITLE_LEN) return `Заголовок быстрой ссылки «${l.title}» длиннее ${SITELINK_TITLE_LEN} символов`
    if (l.description.length > SITELINK_DESC_LEN) return `Описание быстрой ссылки «${l.title}» длиннее ${SITELINK_DESC_LEN} символов`
  }
  const titles = links.map((l) => l.title.toLowerCase())
  if (new Set(titles).size !== titles.length) return 'Заголовки быстрых ссылок повторяются'
  if (links.length > MAX_SITELINKS) return `Быстрых ссылок не больше ${MAX_SITELINKS}`
  const [first, second] = groupLengths({ sitelinks: links })
  if (first > SITELINK_GROUP_LEN) return `Заголовки быстрых ссылок 1–4 вместе длиннее ${SITELINK_GROUP_LEN} символов`
  if (second > SITELINK_GROUP_LEN) return `Заголовки быстрых ссылок 5–8 вместе длиннее ${SITELINK_GROUP_LEN} символов`
  const callouts = calloutLines(extras)
  const long = callouts.find((c) => c.length > CALLOUT_LEN)
  if (long) return `Уточнение «${long}» длиннее ${CALLOUT_LEN} символов`
  if (callouts.length > MAX_CALLOUTS) return `Уточнений не больше ${MAX_CALLOUTS}`
  if ((extras.images || []).length > maxImages)
    return maxImages === 1 ? 'В текстовом объявлении одна картинка' : `Картинок не больше ${maxImages}`
  return ''
}

export function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}
