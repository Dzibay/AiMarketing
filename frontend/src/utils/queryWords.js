// Слова поискового запроса для минус-фраз: «таблетки» слов и общее слово у нескольких запросов.
// Чистые функции без Vue — их можно проверить отдельно (node).

/** Предлоги, союзы и частицы — как STOP_WORDS в backend/app/query_cleaning.py: общим словом их не предлагаем. */
export const STOP_WORDS = new Set(
  'в во на и или с со к ко по о об обо от до для из у за над под при без про через не ни а но же ли бы как что это то'.split(
    ' ',
  ),
)

// Окончания — как _ENDINGS в query_cleaning.py: «отзывы», «отзывов», «отзыв» сводятся к одной основе.
const ENDINGS = (
  'иями ями ами ого его ому ему ыми ими ться тся ешь ете ите ить ать ять еть уть ией иям ием иях ' +
  'ий ый ой ая яя ое ее ые ие ую юю ых их ом ем ам ям ах ях ов ев ей ию ия ья ье ью ут ют ат ят ит ет ла ло ли ' +
  'а я о е ы и у ю ь й'
)
  .split(' ')
  .sort((a, b) => b.length - a.length)

const CYRILLIC = /[а-я]/
// Знаки операторов Директа и пунктуация по краям слова — для сравнения слов они не важны.
const EDGE = /^[^0-9a-zа-я]+|[^0-9a-zа-я]+$/g
// Слово — как _WORD на сервере: буквы и цифры, а дефис, слеш и прочие знаки делят слова («интернет-магазин» — два).
const WORD = /[0-9a-zа-я]+/g

function lowerText(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
}

/** Слово для сравнения: строчные, ё → е, без операторов («!», «+», кавычки, скобки) и знаков по краям. */
export function normWord(word) {
  return lowerText(word).replace(EDGE, '')
}

/** Слова текста так же, как их видит сервер: строчные, ё → е, только буквы и цифры. */
export function textWords(text) {
  return lowerText(text).match(WORD) || []
}

/** Во фразе есть [ ] или кавычки — операторы на несколько слов: кликом по словам такую фразу не пересобрать. */
export function phraseLocked(phrase) {
  return /[[\]"]/.test(String(phrase || ''))
}

/** Основа слова — как stem() на сервере. */
export function stem(word) {
  const w = normWord(word)
  if (w.length <= 3 || !CYRILLIC.test(w)) return w
  for (const ending of ENDINGS) {
    if (w.endsWith(ending) && w.length - ending.length >= 3) return w.slice(0, -ending.length)
  }
  return w
}

/** Одна основа или близкие формы — как _same() на сервере. */
export function sameStem(a, b) {
  if (a === b) return true
  if (Math.min(a.length, b.length) < 4 || Math.abs(a.length - b.length) > 2) return false
  return a.startsWith(b) || b.startsWith(a)
}

/** Основы значимых слов, а если значимых нет — всех (как stems() на сервере). */
function stemsOf(text) {
  const words = textWords(text)
  const significant = words.filter((w) => !STOP_WORDS.has(w))
  return (significant.length ? significant : words).map(stem)
}

/** Отсекает ли минус-фраза запрос: каждое её слово есть в запросе в любой форме (как blocks() на сервере). */
export function phraseCovers(phrase, query) {
  const neg = stemsOf(phrase)
  const text = stemsOf(query)
  return neg.length > 0 && neg.every((n) => text.some((t) => sameStem(n, t)))
}

/**
 * Слова запроса по порядку: [{ text, norm }] — делим как сервер («б/у», «интернет-магазине» — по два слова).
 * text — как в запросе, norm — строчными и с е вместо ё.
 */
export function queryWords(query) {
  const raw = String(query || '')
  const low = lowerText(raw)
  // Длина после toLowerCase почти всегда та же — тогда слово показываем как в запросе.
  const same = low.length === raw.length
  return [...low.matchAll(WORD)].map((m) => ({ text: same ? raw.slice(m.index, m.index + m[0].length) : m[0], norm: m[0] }))
}

// Слова минус-фразы: слово с операторами («!купить», «+в») храним как есть, «интернет-магазине» — по частям.
function phraseTokens(phrase) {
  const out = []
  for (const raw of String(phrase || '').split(/\s+/)) {
    const parts = textWords(raw)
    if (parts.length === 1) out.push({ raw, norm: parts[0] })
    else for (const part of parts) out.push({ raw: part, norm: part })
  }
  return out
}

// Слова фразы, разложенные по словам запроса: matched[i] — слово фразы для i-го слова запроса (или null),
// extra — слова фразы, которых в запросе нет (вписаны руками, их не теряем).
function split(query, phrase) {
  const words = queryWords(query)
  const stems = words.map((w) => stem(w.norm))
  const tokens = phraseTokens(phrase)
  const used = new Set()
  const matched = words.map(() => null)
  const pass = (fits) =>
    words.forEach((w, i) => {
      if (matched[i]) return
      const t = tokens.find((x) => !used.has(x) && fits(x, w, i))
      if (!t) return
      used.add(t)
      matched[i] = t
    })
  // Сначала то же слово, затем другая форма: у фраз ИИ, памяти и брифа своя форма («бесплатно» к «бесплатные»).
  pass((t, w) => t.norm === w.norm)
  pass((t, w, i) => sameStem(stem(t.norm), stems[i]))
  // Слово повторяется в запросе («окна окна пвх») — это то же слово фразы, второй раз его не добавляем.
  words.forEach((w, i) => {
    if (!matched[i]) matched[i] = [...used].find((t) => sameStem(stem(t.norm), stems[i])) || null
  })
  return { words, matched, extra: tokens.filter((t) => !used.has(t)) }
}

/** Какие слова запроса уже есть в минус-фразе: [true, false, …] по порядку слов запроса. */
export function pickedWords(query, phrase) {
  return split(query, phrase).matched.map(Boolean)
}

/**
 * Клик по i-му слову запроса: слово входит во фразу или выходит из неё (в любой форме, в какой оно во фразе).
 * Слова запроса идут в его порядке, операторы у слова («!купить») сохраняются, вписанные руками слова — в конце.
 * Фразу со [ ] или кавычками не трогаем — её правят в поле.
 */
export function toggleWord(query, phrase, index) {
  const text = String(phrase || '').trim()
  const { words, matched, extra } = split(query, phrase)
  if (index < 0 || index >= words.length || phraseLocked(text)) return text
  const token = matched[index]
  if (token) {
    for (let i = 0; i < matched.length; i++) if (matched[i] === token) matched[i] = null
  } else {
    matched[index] = { raw: words[index].text.toLowerCase() }
  }
  return [...new Set(matched.filter(Boolean)), ...extra].map((t) => t.raw).join(' ')
}

/** Слово можно предлагать общим: не предлог, не короче трёх букв (кроме чисел). */
function meaningful(norm) {
  if (STOP_WORDS.has(norm)) return false
  return norm.length >= 3 || /^\d{2,}$/.test(norm)
}

/**
 * Общие слова у нескольких запросов — кандидаты в одну минус-фразу вместо запросов целиком.
 * texts — запросы; skip — тексты ключевых фраз (их слова не предлагаем: минус отключит ключ);
 * avoid — полезные запросы (слово из них отрезало бы и их); min — у скольких запросов слово должно быть.
 * → [{ word, count, indices }] — лучшие первыми: больше запросов, затем длиннее слово.
 */
export function commonWords(texts, { skip = [], avoid = [], min = 2 } = {}) {
  const banned = [...skip, ...avoid].flatMap((t) => queryWords(t).map((w) => stem(w.norm)))
  const byStem = new Map()
  texts.forEach((text, i) => {
    const seen = new Set()
    for (const w of queryWords(text)) {
      if (!meaningful(w.norm)) continue
      const s = stem(w.norm)
      if (seen.has(s)) continue
      seen.add(s)
      if (!byStem.has(s)) byStem.set(s, { indices: [], forms: new Map() })
      const entry = byStem.get(s)
      entry.indices.push(i)
      entry.forms.set(w.norm, (entry.forms.get(w.norm) || 0) + 1)
    }
  })
  const out = []
  for (const [s, entry] of byStem) {
    if (entry.indices.length < min || banned.some((b) => sameStem(s, b))) continue
    // Форма слова — самая частая у этих запросов (минус-слово в Директе отсекает все формы).
    let word = ''
    let best = 0
    for (const [form, n] of entry.forms) {
      if (n > best) {
        word = form
        best = n
      }
    }
    out.push({ word, count: entry.indices.length, indices: entry.indices })
  }
  return out.sort((a, b) => b.count - a.count || b.word.length - a.word.length || a.word.localeCompare(b.word))
}
