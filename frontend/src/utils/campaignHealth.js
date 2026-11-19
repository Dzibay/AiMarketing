import { CAMPAIGN_STATES, isoDay, pct, plural, rub, rubles } from './format'

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

const adWord = (n) => plural(n, 'объявление', 'объявления', 'объявлений')

function dayMonth(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
}

/**
 * Почему кампания не показывается — словами — и один следующий шаг: {text, next: {kind, label}} или null.
 * kind: moderate — отправить черновик на проверку; fix — исправить отказ модерации с ИИ; topup — пополнить
 * счёт (c.money_url); resume — включить остановленную. Поля строки: state, status, status_clarification
 * (пояснение Директа), ads_rejected, balance_kopecks, start_date.
 */
export function campaignReason(c, today = isoDay(new Date())) {
  const rejected = c.ads_rejected || 0
  const fix = { kind: 'fix', label: 'Исправить с ИИ' }
  const topup = { kind: 'topup', label: 'Пополнить' }
  const broke = c.balance_kopecks != null && c.balance_kopecks <= 0
  if (c.state === 'ON') {
    if (broke) return { text: 'Деньги на счёте кончились — показы скоро остановятся', next: topup }
    if (rejected)
      return { text: `Отклонено ${rejected} ${adWord(rejected)} — остальные показываются`, next: fix }
    return null
  }
  if (c.state === 'ARCHIVED') return { text: 'В архиве', next: null }
  if (c.status === 'DRAFT') return { text: 'Черновик — ещё не отправлен на проверку', next: { kind: 'moderate', label: 'Отправить на проверку' } }
  if (c.status === 'MODERATION') return { text: 'На проверке в Директе — покажется, когда её примут', next: null }
  if (c.status === 'REJECTED') {
    const why = c.status_clarification ? `: ${c.status_clarification}` : ''
    return { text: `Отклонена модерацией${why}`, next: fix }
  }
  if (c.state === 'ENDED') return { text: 'Завершена — прошла дата окончания', next: null }
  if (c.state === 'SUSPENDED') return { text: 'Остановлена вами', next: { kind: 'resume', label: 'Включить' } }
  if (broke) return { text: 'Деньги на счёте кончились', next: topup }
  if (c.start_date && c.start_date > today) return { text: `Старт ${dayMonth(c.start_date)}`, next: null }
  if (rejected) return { text: `Отклонено ${rejected} ${adWord(rejected)}`, next: fix }
  // Остальное — словами Директа, если загрузились: «Ожидает оплаты», «Нет активных объявлений».
  if (c.status_clarification) return { text: c.status_clarification, next: null }
  return { text: CAMPAIGN_STATES[c.state] || 'Остановлена', next: null }
}

/**
 * Что с кампанией за период — простым языком, против цели проекта (kpi: {type: 'cpa'|'drr', target}).
 * bad — расход есть, а заявок (или дохода при цели по ДРР) нет; warn — заявки есть, но дороже цели.
 * Не показывается — причина словами (campaignReason).
 */
export function campaignHealth(c, kpi) {
  if (c.state !== 'ON') return { id: 'off', text: campaignReason(c)?.text || CAMPAIGN_STATES[c.state] || 'Остановлена' }
  const cost = c.cost_kopecks || 0
  const conv = c.conversions || 0
  if (!cost) return { id: 'idle', text: campaignReason(c)?.text || 'За период не было расхода' }
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
