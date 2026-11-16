import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

/**
 * Период дашборда: в адресе (?p=30d или ?from=…&to=…, плюс ?step=week, если шаг выбран руками)
 * и в памяти браузера — открыли дашборд без периода в адресе, подставляем последний выбранный.
 */
export const PERIOD_PRESETS = ['day', '7d', '30d', '90d', '180d', '365d', 'all']
export const PERIOD_LABELS = {
  day: 'День',
  '7d': 'Неделя',
  '30d': 'Месяц',
  '90d': 'Квартал',
  '180d': 'Полгода',
  '365d': 'Год',
  all: 'Всё время',
}
export const STEPS = [
  { value: 'day', label: 'Дни' },
  { value: 'week', label: 'Недели' },
  { value: 'month', label: 'Месяцы' },
]
const KEY = 'aimarketing-admin-period'
const ISO = /^\d{4}-\d{2}-\d{2}$/

function clean(q) {
  const out = {}
  if (ISO.test(q.from || '') && ISO.test(q.to || '')) {
    out.from = q.from
    out.to = q.to
  } else if (PERIOD_PRESETS.includes(q.p)) {
    out.p = q.p
  }
  if (STEPS.some((s) => s.value === q.step)) out.step = q.step
  return out
}

function remember(query) {
  try {
    localStorage.setItem(KEY, JSON.stringify(query))
  } catch {
    /* память браузера недоступна — живём только адресом */
  }
}

function recall() {
  try {
    return clean(JSON.parse(localStorage.getItem(KEY) || '{}'))
  } catch {
    return {}
  }
}

export function useAdminPeriod() {
  const route = useRoute()
  const router = useRouter()

  /** То, что уйдёт на сервер: { p } или { from, to }, и step, если выбран. Без периода — месяц. */
  const query = computed(() => {
    const q = clean(route.query)
    if (!q.p && !q.from) q.p = '30d'
    return q
  })

  const go = (next) => router.push({ query: clean(next) })

  // Заход без периода в адресе — подставить последний выбранный; период в адресе (и по ссылке) — запомнить.
  const inUrl = clean(route.query)
  const saved = recall()
  if (!inUrl.p && !inUrl.from && (saved.p || saved.from)) router.replace({ query: { ...route.query, ...saved } })
  watch(
    () => clean(route.query),
    (q) => {
      if (q.p || q.from) remember(q)
    },
    { immediate: true },
  )

  /** change от выбора периода: preset — быстрый период, null — свои даты. Шаг сбрасывается на автоматический. */
  function setPeriod({ preset, date_from, date_to }) {
    go(preset ? { p: preset } : { from: date_from, to: date_to })
  }

  /** Шаг графиков руками; совпал с автоматическим — не держим его в адресе. */
  function setStep(step, autoStep) {
    const { step: _, ...rest } = query.value
    go(step && step !== autoStep ? { ...rest, step } : rest)
  }

  return { query, setPeriod, setStep }
}
