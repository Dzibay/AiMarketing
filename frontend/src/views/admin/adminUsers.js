/**
 * Подписи списка пользователей админки и его панели: метка активности, тариф, источник, «оплачено всего».
 * Дни — московские, как у сервера (app.clock).
 */
import { plural, rubles } from '../../utils/format'

const MSK_OFFSET = 3 * 3600 * 1000
const DAY = 864e5

/** Номер московского дня — для разницы «сколько дней назад». */
function mskDayNo(time) {
  return Math.floor((new Date(time).getTime() + MSK_OFFSET) / DAY)
}

/** Сколько московских дней назад; null — ни разу. */
export function daysAgo(iso, now = Date.now()) {
  if (!iso) return null
  return mskDayNo(now) - mskDayNo(iso)
}

/** Время по Москве «14:20». */
export function mskTime(iso) {
  return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' })
}

/** Ступень метки: 0 — сегодня … 5 — больше двух недель, 'never' — ни разу. */
export function activityLevel(iso, now = Date.now()) {
  const d = daysAgo(iso, now)
  if (d === null) return 'never'
  if (d <= 0) return 0
  if (d === 1) return 1
  if (d <= 3) return 2
  if (d <= 7) return 3
  if (d <= 14) return 4
  return 5
}

/** «был сегодня в 14:20», «был вчера в 9:05», «был 5 дней назад», «ни разу не заходил». */
export function activityText(iso, now = Date.now()) {
  const d = daysAgo(iso, now)
  if (d === null) return 'ни разу не заходил'
  if (d <= 0) return `был сегодня в ${mskTime(iso)}`
  if (d === 1) return `был вчера в ${mskTime(iso)}`
  return `был ${d} ${plural(d, 'день', 'дня', 'дней')} назад`
}

/** Цвет группы ссылок: из номера группы, стабильный (своего цвета у группы в базе нет). */
const GROUP_HUES = [150, 210, 30, 280, 340, 95, 190, 250, 10, 60]
export function groupColor(groupId) {
  if (!groupId) return 'var(--faint)'
  return `hsl(${GROUP_HUES[groupId % GROUP_HUES.length]} 55% 45%)`
}

/** Источник одной строкой: { text, note, color }; null — до учёта визитов, неизвестно. */
export function sourceView(src) {
  if (!src) return null
  if (src.kind === 'ref') {
    if (src.owner_email) return { text: `от ${src.owner_email}`, note: `личная ссылка · ${src.code}`, color: 'var(--faint)' }
    return { text: src.name || src.code, note: [src.code, src.group_name].filter(Boolean).join(' · '), color: groupColor(src.group_id) }
  }
  if (src.kind === 'utm') {
    const u = src.utm || {}
    return { text: u.utm_source, note: [u.utm_medium, u.utm_campaign].filter(Boolean).join(' · '), color: '' }
  }
  if (src.kind === 'referrer') return { text: src.referrer, note: 'переход с сайта', color: '' }
  return { text: 'напрямую', note: '', color: '' }
}

function dm(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
}

function dmy(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU')
}

/** «3 мес.», «1 год», «14 дн.» — срок покупки по дням. */
export function termText(days) {
  if (!days) return '0 дн.'
  if (days >= 360 && days % 360 < 30) {
    const y = Math.round(days / 365) || 1
    return `${y} ${plural(y, 'год', 'года', 'лет')}`
  }
  if (days >= 28) return `${Math.round(days / 30)} мес.`
  return `${days} дн.`
}

/** Сколько дней осталось до конца подписки (последний день — включительно). */
function daysLeft(lastDay, now = Date.now()) {
  return mskDayNo(new Date(`${lastDay}T12:00:00+03:00`)) - mskDayNo(now) + 1
}

/**
 * Колонка «Тариф»: { title, note, tone }. Купленный тариф — «Про · 3 мес. · 2 970 ₽», пробный — «Пробный · до 20.11»,
 * без платежа — «Про · выдан вручную · до …», закончился — красное «закончился 3 дня назад».
 */
export function tariffView(u, now = Date.now()) {
  const sub = u.subscription || {}
  const plan = sub.plan
  if (!plan) return { title: 'Без тарифа', note: '', tone: '' }
  const pay = u.last_payment
  let title
  if (pay && pay.plan_id === plan.id) title = `${plan.name} · ${termText(pay.days)} · ${rubles(pay.amount_rub)}`
  else if (plan.is_trial) title = sub.last_day ? `${plan.name} · до ${dm(sub.last_day)}` : plan.name
  else title = `${plan.name} · выдан вручную${sub.last_day ? ` · до ${dmy(sub.last_day)}` : ''}`
  if (!sub.last_day) return { title, note: 'бессрочно', tone: '' }
  if (sub.expired) {
    const ago = -daysLeft(sub.last_day, now)
    const when = ago <= 0 ? 'сегодня' : `${ago} ${plural(ago, 'день', 'дня', 'дней')} назад`
    return { title, note: `закончился ${when}`, tone: 'tone-bad' }
  }
  const left = daysLeft(sub.last_day, now)
  return { title, note: `оплачено по ${dmy(sub.last_day)} · осталось ${left} дн.`, tone: left <= 3 ? 'tone-warn' : '' }
}

/** «5 мес. · 3 платежа». */
export function paidTotalText(paid) {
  if (!paid?.count) return '—'
  return `${termText(paid.days)} · ${paid.count} ${plural(paid.count, 'платёж', 'платежа', 'платежей')}`
}

/** Израсходовано за период, % (null — без лимита). */
export function usedPct(usage) {
  const limit = usage?.limit
  if (limit === null || limit === undefined) return null
  if (limit <= 0) return 100
  return Math.round(((usage.spent || 0) / limit) * 100)
}

export function hasOverrides(sub) {
  const o = sub?.overrides || {}
  return 'units_limit' in o || 'projects_limit' in o || Object.keys(o.features || {}).length > 0
}
