import { TERMS } from './glossary'

/**
 * Справка кабинета: статьи — файлы docs/help/*.md (описание формата — docs/help/README.md). Vite кладёт их
 * в сборку как текст; шапка между `---` даёт заголовок, строку для списка, разделы для кнопки «?» и порядок.
 * changelog.md — «Что нового», записи `## ГГГГ-ММ-ДД · Заголовок`, свежая сверху.
 */
const FILES = import.meta.glob('../../../docs/help/*.md', { query: '?raw', import: 'default', eager: true })

function parse(path, raw) {
  const slug = path.split('/').pop().replace(/\.md$/, '')
  const text = String(raw).replace(/\r\n/g, '\n')
  const head = /^---\n([\s\S]*?)\n---\n?/.exec(text)
  const meta = {}
  for (const line of (head ? head[1] : '').split('\n')) {
    const m = /^(\w+):\s*(.*)$/.exec(line)
    if (m) meta[m[1]] = m[2].trim()
  }
  return {
    slug,
    title: meta.title || slug,
    summary: meta.summary || '',
    sections: (meta.sections || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean),
    order: Number(meta.order) || 50,
    body: head ? text.slice(head[0].length) : text,
  }
}

/** Термины словаря для статьи «Словарь»: подписи колонок главной («Сегодня», «KPI 7 дн.») — не термины. */
export const GLOSSARY = Object.entries(TERMS)
  .filter(([key]) => !['spend_today', 'kpi7'].includes(key))
  .map(([key, t]) => ({ key, ...t }))
  .sort((a, b) => a.title.localeCompare(b.title, 'ru'))

const all = Object.entries(FILES)
  .filter(([path]) => !/\/README\.md$/i.test(path))
  .map(([path, raw]) => parse(path, raw))

const changelogFile = all.find((a) => a.slug === 'changelog')

/** Статьи для списка — без «Что нового»: у неё своё место наверху справки. */
export const ARTICLES = all.filter((a) => a.slug !== 'changelog').sort((a, b) => a.order - b.order)

export function article(slug) {
  return all.find((a) => a.slug === slug) || null
}

/** Статья про раздел меню (route.meta.section) — для кнопки «?» в шапке страницы. */
export function sectionArticle(section) {
  return section ? ARTICLES.find((a) => a.sections.includes(section)) || null : null
}

/** Записи «Что нового»: [{ id: '2026-11-16', date, title, body }], свежая первой. */
export const CHANGELOG = (() => {
  if (!changelogFile) return []
  const out = []
  let cur = null
  for (const line of changelogFile.body.split('\n')) {
    const m = /^##\s+(\d{4}-\d{2}-\d{2})\s*[·—-]\s*(.+)$/.exec(line)
    if (m) {
      cur = { id: m[1], date: m[1], title: m[2].trim(), lines: [] }
      out.push(cur)
    } else if (cur) {
      cur.lines.push(line)
    }
  }
  return out
    .map(({ lines, ...e }) => ({ ...e, body: lines.join('\n').trim() }))
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0))
})()

// --- Поиск: статей мало, хватает поиска по словам ---

const norm = (s) => String(s || '').toLowerCase().replace(/ё/g, 'е')
const plain = (s) =>
  String(s || '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*`#>|]/g, '')
    .replace(/\s+/g, ' ')
    .trim()

const INDEX = [
  ...ARTICLES.map((a) => ({
    a,
    title: norm(a.title),
    text: norm(
      `${a.summary} ${plain(a.body)} ${a.slug === 'glossary' ? GLOSSARY.map((t) => `${t.title} ${t.text}`).join(' ') : ''}`,
    ),
    raw:
      plain(a.body) + (a.slug === 'glossary' ? ' ' + GLOSSARY.map((t) => `${t.title} — ${t.text}`).join(' ') : ''),
  })),
  ...(changelogFile
    ? [{ a: changelogFile, title: norm(changelogFile.title), text: norm(plain(changelogFile.body)), raw: plain(changelogFile.body) }]
    : []),
]

/** Кусок текста вокруг первого найденного слова — чтобы было видно, почему статья нашлась. */
function snippet(raw, word) {
  const at = norm(raw).indexOf(word)
  if (at < 0) return ''
  const from = Math.max(0, raw.lastIndexOf(' ', Math.max(0, at - 60)) + 1)
  const to = Math.min(raw.length, at + word.length + 90)
  return `${from > 0 ? '…' : ''}${raw.slice(from, to).trim()}${to < raw.length ? '…' : ''}`
}

/**
 * Статьи, где есть все слова запроса (по началу слова, без учёта регистра и ё). Совпадение в заголовке — выше.
 * Ответ: [{ article, snippet }].
 */
export function searchHelp(query) {
  // Короткие окончания отрезаем: «минус-фразы» найдёт и «минус-фразу».
  const words = norm(query)
    .split(/[^a-zа-я0-9%]+/i)
    .filter((w) => w.length > 1)
    .map((w) => (w.length > 4 ? w.slice(0, -1) : w))
  if (!words.length) return []
  return INDEX.map((e) => {
    if (!words.every((w) => e.title.includes(w) || e.text.includes(w))) return null
    const score = words.reduce((n, w) => n + (e.title.includes(w) ? 3 : 0) + (e.text.split(w).length - 1), 0)
    return { article: e.a, snippet: snippet(e.raw, words[0]), score }
  })
    .filter(Boolean)
    .sort((x, y) => y.score - x.score)
}
