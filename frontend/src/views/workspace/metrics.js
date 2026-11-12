import { num, pct, rub, rubExact } from '../../utils/format'

/**
 * Метрики периода: подпись, формат и направление. inverse — «больше — хуже» (расход, цены). good — когда
 * направление однозначно и его можно красить: 'up' — рост хорош, 'down' — рост плох; без good — нейтрально
 * (больше расхода или показов само по себе ни хорошо, ни плохо).
 */
export const METRICS = [
  { key: 'cost_kopecks', label: 'Расход', fmt: rub, inverse: true },
  { key: 'impressions', label: 'Показы', fmt: (v) => num(v) },
  { key: 'clicks', label: 'Клики', fmt: (v) => num(v) },
  { key: 'ctr', label: 'CTR', fmt: (v) => pct(v, 2), good: 'up' },
  { key: 'cpc_kopecks', label: 'Цена клика', fmt: rubExact, inverse: true, good: 'down' },
  { key: 'conversions', label: 'Конверсии', fmt: (v) => num(v), good: 'up' },
  { key: 'cpa_kopecks', label: 'CPA', fmt: rub, inverse: true, good: 'down' },
  { key: 'revenue_kopecks', label: 'Доход', fmt: rub, good: 'up' },
  { key: 'drr', label: 'ДРР', fmt: (v) => pct(v), inverse: true, good: 'down' },
  { key: 'visits', label: 'Визиты', fmt: (v) => num(v) },
]

export const METRIC_BY_KEY = Object.fromEntries(METRICS.map((m) => [m.key, m]))

/** Что можно показать на графике по дням (есть в by_day). */
export const CHART_METRICS = ['cost_kopecks', 'clicks', 'impressions', 'conversions', 'cpa_kopecks', 'revenue_kopecks', 'visits']
