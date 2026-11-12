import { CAMPAIGN_STATES, pct, plural, rub, rubles } from './format'

/** Группы простого режима — в порядке важности. «Расход без заявок» — только расход без единой заявки. */
export const HEALTH_GROUPS = [
  { id: 'bad', label: 'Расход без заявок' },
  { id: 'warn', label: 'Дороже цели' },
  { id: 'ok', label: 'Работают хорошо' },
  { id: 'idle', label: 'Без расхода' },
  { id: 'off', label: 'Остановлены' },
]

const convWord = (n) => plural(n, 'конверсия', 'конверсии', 'конверсий')
const leadWord = (n) => plural(n, 'заявка', 'заявки', 'заявок')

/**
 * Что с кампанией за период — простым языком, против цели проекта (kpi: {type: 'cpa'|'drr', target}).
 * bad — расход есть, а заявок (или дохода при цели по ДРР) нет; warn — заявки есть, но дороже цели.
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
    if (c.drr <= target) return { id: 'ok', text: `Доля расходов на рекламу ${pct(c.drr)} — в цели ${pct(target)}` }
    return { id: 'warn', text: `Доля расходов на рекламу ${pct(c.drr)} — выше цели ${pct(target)}` }
  }

  if (!conv) {
    // С целью по цене заявки пустой расход меньше одной цены заявки ещё ничего не значит.
    if (type === 'cpa' && target && cost <= target * 100)
      return { id: 'ok', text: `${rub(cost)} за период — заявок пока нет, рано судить` }
    return { id: 'bad', text: `${rub(cost)} за период, заявок нет` }
  }
  const cpa = cost / conv
  if (type === 'cpa' && target) {
    const ratio = cpa / (target * 100)
    if (ratio <= 1) return { id: 'ok', text: `Цена заявки ${rub(cpa)} — в цели ${rubles(target)}` }
    const over = Math.round((ratio - 1) * 100)
    return { id: 'warn', text: `Цена заявки ${rub(cpa)} — на ${pct(over, 0)} дороже цели ${rubles(target)}` }
  }
  return { id: 'ok', text: `${conv} ${leadWord(conv)}, цена заявки ${rub(cpa)}` }
}

export { convWord, leadWord }
