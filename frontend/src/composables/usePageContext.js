import { computed, onBeforeUnmount, ref, shallowRef } from 'vue'
import { niceQuotes, plural } from '../utils/format'

/**
 * Что открыто в кабинете — для панели ИИ (components/AiPanel.vue): страница сообщает вид, объект, период, фильтр
 * и выбранные строки, панель показывает это чипом над полем ввода и отправляет вместе с сообщением (поле context,
 * сервер кладёт его в ai_messages.meta и пишет модели строкой перед вопросом).
 *
 * Страница зовёт usePageContext(() => ({ view, object, period, filter, selected })) в setup: пока она на экране,
 * её контекст — текущий. view — campaign | campaigns | queries | analytics | history | pending; object и
 * selected — { type: 'campaign', id, name }; period — { from, to } (YYYY-MM-DD).
 */

// Зарегистрированные страницы: последняя живая — текущая (новая страница монтируется раньше, чем уходит старая).
const sources = shallowRef([])
let seq = 0
// Чип убрали крестиком — контекст не уходит, пока страница не сообщит другой.
const hiddenKey = ref('')

const VIEW_LABELS = {
  campaign: 'кампания',
  campaigns: 'кампании',
  queries: 'поисковые запросы',
  analytics: 'аналитика',
  history: 'история изменений',
  pending: 'ждущие решения',
}

const day = (iso) => new Date(`${iso}T00:00:00`)

/** «7 дней», если период кончается вчера; иначе «9–15 ноября». */
export function periodLabel(period) {
  if (!period?.from || !period?.to) return ''
  const yesterday = new Date()
  yesterday.setHours(0, 0, 0, 0)
  yesterday.setDate(yesterday.getDate() - 1)
  const from = day(period.from)
  const to = day(period.to)
  const n = Math.round((to - from) / 86400000) + 1
  if (to.getTime() === yesterday.getTime() && n > 0) return `${n} ${plural(n, 'день', 'дня', 'дней')}`
  const month = (d) => d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
  if (from.getMonth() === to.getMonth() && from.getFullYear() === to.getFullYear()) {
    return `${from.getDate()}–${month(to)}`
  }
  return `${month(from)} — ${month(to)}`
}

/** Подпись чипа «Про: …»: вид и объект, выбранное, период, фильтр. */
function labelOf(ctx) {
  const parts = []
  const n = ctx.selected?.length || 0
  if (ctx.object?.name) parts.push(`${VIEW_LABELS[ctx.view]} «${niceQuotes(ctx.object.name)}»`)
  else if (n === 1) parts.push(`кампания «${niceQuotes(ctx.selected[0].name)}»`)
  else if (n > 1) parts.push(`${n} ${plural(n, 'выбранная кампания', 'выбранные кампании', 'выбранных кампаний')}`)
  else parts.push(VIEW_LABELS[ctx.view])
  if (ctx.period) parts.push(periodLabel(ctx.period))
  if (ctx.filter) parts.push(ctx.filter)
  return parts.filter(Boolean).join(' · ')
}

function clean(raw) {
  if (!raw || !VIEW_LABELS[raw.view]) return null
  const item = (x) => (x && x.id != null ? { type: x.type || 'campaign', id: Number(x.id), name: String(x.name || '') } : null)
  const ctx = { view: raw.view }
  if (item(raw.object)) ctx.object = item(raw.object)
  if (raw.period?.from && raw.period?.to) ctx.period = { from: raw.period.from, to: raw.period.to }
  if (raw.filter) ctx.filter = String(raw.filter)
  const selected = (raw.selected || []).map(item).filter(Boolean).slice(0, 30)
  if (selected.length) ctx.selected = selected
  ctx.label = labelOf(ctx)
  return ctx
}

const current = computed(() => {
  const list = sources.value
  return list.length ? clean(list[list.length - 1].get()) : null
})
const currentKey = computed(() => (current.value ? JSON.stringify(current.value) : ''))

/** Контекст для панели: null — страница ничего не сообщила или чип убрали. */
export const pageContext = computed(() => (current.value && currentKey.value !== hiddenKey.value ? current.value : null))

/** Убрать чип: следующее сообщение уйдёт без контекста, пока на странице ничего не поменяется. */
export function hidePageContext() {
  hiddenKey.value = currentKey.value
}

/** Вызов из setup страницы: get — функция, которая возвращает контекст (реактивно). */
export function usePageContext(get) {
  const entry = { id: ++seq, get }
  sources.value = [...sources.value, entry]
  onBeforeUnmount(() => {
    sources.value = sources.value.filter((e) => e.id !== entry.id)
  })
}
