import { computed, ref } from 'vue'
import { webApi } from '../api/web'

/**
 * Загрузка данных кабинетов Директа и счётчиков Метрики: один опрос /api/web/loads на всё приложение —
 * для индикатора в шапке и карточек в «Аккаунтах». Пока идут задачи — часто, в покое — редко.
 */

const ACTIVE_MS = 2500
const IDLE_MS = 20000
const HIDDEN_MS = 60000

const overview = ref({ accounts: [], counters: [], connections_syncing: 0 })
const ready = ref(false)
let timer = null
let users = 0
let inflight = null
const finishedHandlers = new Set()

/** Включённые кабинеты и счётчики, которые грузим (дубль счётчика из другого логина не грузится). */
const items = computed(() => [
  ...overview.value.accounts
    .filter((a) => a.enabled)
    .map((a) => ({ ...a, type: 'account', key: `a${a.id}`, title: a.name || a.client_login })),
  ...overview.value.counters
    .filter((c) => c.enabled && c.canonical)
    .map((c) => ({ ...c, type: 'counter', key: `c${c.id}`, title: c.name || `Счётчик ${c.counter_id}` })),
])

const active = computed(() => items.value.filter((i) => i.job))
const busy = computed(() => active.value.length > 0 || overview.value.connections_syncing > 0)

/** Ни разу не загружались и сейчас не грузятся — ждут баллов, следующего часа или упали с ошибкой. */
const waiting = computed(() => items.value.filter((i) => !i.job && !i.loaded_at))
const problems = computed(() => items.value.filter((i) => !i.job && stageProblem(i)))

/** Общий процент по идущим задачам: каждая весит одинаково, задача в очереди — 0%. */
const percent = computed(() => {
  if (!active.value.length) return null
  const sum = active.value.reduce((acc, i) => acc + jobPercent(i.job), 0)
  return Math.round(sum / active.value.length)
})

export function jobPercent(job) {
  if (!job || job.status !== 'running' || !job.total) return 0
  return Math.min(100, Math.round((100 * job.done) / job.total))
}

/** Текст прогресса без префикса «логин · » — имя и так написано рядом. */
export function jobText(job) {
  if (!job) return ''
  if (job.status !== 'running') return job.error_code ? 'повтор после ошибки…' : 'в очереди…'
  const parts = String(job.text || '').split(' · ')
  return (parts.length > 1 ? parts.slice(1).join(' · ') : parts[0]) || 'идёт…'
}

/** Первый этап с ошибкой или пропуском (кончились баллы) — для подсветки. */
export function stageProblem(item) {
  return (item.stages || []).find((s) => s.status === 'error' || s.skipped) || null
}

/** Ключи «кто грузится» — чтобы понять, что какая-то загрузка завершилась. */
function activeKeys(data) {
  const keys = new Set()
  for (const a of data.accounts || []) if (a.job) keys.add(`a${a.id}`)
  for (const c of data.counters || []) if (c.job) keys.add(`c${c.id}`)
  if (data.connections_syncing) keys.add('connections')
  return keys
}

async function poll() {
  clearTimeout(timer)
  timer = null
  if (!inflight) {
    inflight = (async () => {
      try {
        const before = activeKeys(overview.value)
        const data = await webApi.loads()
        overview.value = data
        ready.value = true
        const after = activeKeys(data)
        const finished = [...before].filter((k) => !after.has(k))
        if (finished.length) finishedHandlers.forEach((fn) => fn(finished))
      } catch {
        // Сеть моргнула или сессия кончилась — попробуем на следующем тике.
      } finally {
        inflight = null
      }
    })()
  }
  await inflight
  // Два одновременных вызова (тик и refreshLoads) ждут один запрос — следующий тик ставим один.
  clearTimeout(timer)
  timer = null
  if (users > 0) {
    const delay = document.hidden ? HIDDEN_MS : busy.value ? ACTIVE_MS : IDLE_MS
    timer = setTimeout(poll, delay)
  }
}

function onVisible() {
  if (!document.hidden && users > 0) poll()
}

/** Подписка на опрос: пока есть хоть один подписчик (шапка), опрос идёт. */
export function useLoads({ onFinished } = {}) {
  return {
    overview,
    ready,
    items,
    active,
    busy,
    waiting,
    problems,
    percent,
    start() {
      users += 1
      if (onFinished) finishedHandlers.add(onFinished)
      if (users === 1) {
        document.addEventListener('visibilitychange', onVisible)
        poll()
      }
    },
    stop() {
      users = Math.max(0, users - 1)
      if (onFinished) finishedHandlers.delete(onFinished)
      if (users === 0) {
        clearTimeout(timer)
        timer = null
        document.removeEventListener('visibilitychange', onVisible)
      }
    },
  }
}

/** Сразу опросить (после «Обновить» / «Загрузить заново»), не дожидаясь редкого тика. */
export function refreshLoads() {
  return poll()
}

export function resetLoads() {
  overview.value = { accounts: [], counters: [], connections_syncing: 0 }
  ready.value = false
}
