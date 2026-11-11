import { CAMPAIGN_STATES, pct, plural, rub, rubles } from './format'

/** Во сколько раз хуже цели ещё «чуть дороже»; дальше — «тратит впустую». Как KPI_WARN_RATIO на бэкенде. */
const WARN_RATIO = 1.3

/** Группы простого режима — в порядке важности. */
export const HEALTH_GROUPS = [
  { id: 'bad', label: 'Тратят впустую' },
  { id: 'warn', label: 'Чуть дороже цели' },
  { id: 'ok', label: 'Работают хорошо' },
  { id: 'idle', label: 'Без расхода' },
  { id: 'off', label: 'Остановлены' },
]

const convWord = (n) => plural(n, 'конверсия', 'конверсии', 'конверсий')

/**
 * Что с кампанией за период — простым языком, против цели проекта (kpi: {type: 'cpa'|'drr', target}).
 * Без цели «плохо» только одно: расход есть, а конверсий нет.
 */
export function campaignHealth(c, kpi) {
  if (c.state !== 'ON') {
    const label = CAMPAIGN_STATES[c.state] || 'Остановлена'
    return { id: 'off', text: c.status === 'DRAFT' ? 'Черновик — ещё не запускалась' : label }
  }
  const cost = c.cost_kopecks || 0
  const conv = c.conversions || 0
  if (!cost) return { id: 'idle', text: 'За период не было расхода' }
  const type = kpi?.type
  const target = kpi?.target > 0 ? Number(kpi.target) : null

  if (type === 'drr' && target) {
    if (c.drr == null) return { id: 'bad', text: `${rub(cost)} за период, дохода нет` }
    const ratio = c.drr / target
    if (ratio <= 1) return { id: 'ok', text: `ДРР ${pct(c.drr)} — в цели ${pct(target)}` }
    return { id: ratio <= WARN_RATIO ? 'warn' : 'bad', text: `ДРР ${pct(c.drr)} — выше цели ${pct(target)}` }
  }

  if (!conv) {
    // С целью CPA пустой расход меньше одной цены конверсии ещё ничего не значит.
    if (type === 'cpa' && target && cost <= target * 100)
      return { id: 'ok', text: `${rub(cost)} за период — конверсий пока нет, рано судить` }
    return { id: 'bad', text: `${rub(cost)} за период, конверсий нет` }
  }
  const cpa = cost / conv
  if (type === 'cpa' && target) {
    const ratio = cpa / (target * 100)
    if (ratio <= 1) return { id: 'ok', text: `Конверсия ${rub(cpa)} — в цели ${rubles(target)}` }
    const over = Math.round((ratio - 1) * 100)
    return {
      id: ratio <= WARN_RATIO ? 'warn' : 'bad',
      text: `Конверсия ${rub(cpa)} — на ${over}% дороже цели ${rubles(target)}`,
    }
  }
  return { id: 'ok', text: `${conv} ${convWord(conv)} по ${rub(cpa)}` }
}

export { convWord }
