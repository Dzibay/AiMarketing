import { isoDay } from '../../utils/format'

/**
 * Даты календаря — строки «ГГГГ-ММ-ДД» в местном времени: их можно сравнивать как строки и отдавать серверу как есть.
 * Месяц — «ГГГГ-ММ».
 */

export const MONTHS = [
  'Январь',
  'Февраль',
  'Март',
  'Апрель',
  'Май',
  'Июнь',
  'Июль',
  'Август',
  'Сентябрь',
  'Октябрь',
  'Ноябрь',
  'Декабрь',
]

export const WEEKDAYS = [
  ['Пн', 'понедельник'],
  ['Вт', 'вторник'],
  ['Ср', 'среда'],
  ['Чт', 'четверг'],
  ['Пт', 'пятница'],
  ['Сб', 'суббота'],
  ['Вс', 'воскресенье'],
]

export function toDate(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d || 1)
}

export const todayIso = () => isoDay(new Date())

export function addDays(iso, n) {
  const d = toDate(iso)
  d.setDate(d.getDate() + n)
  return isoDay(d)
}

/** Тот же день через n месяцев; 31 января + 1 месяц — последний день февраля. */
export function addMonths(iso, n) {
  const d = toDate(iso)
  const day = d.getDate()
  const target = new Date(d.getFullYear(), d.getMonth() + n, 1)
  const last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate()
  target.setDate(Math.min(day, last))
  return isoDay(target)
}

export const monthOf = (iso) => iso.slice(0, 7)

export function shiftMonth(month, n) {
  const [y, m] = month.split('-').map(Number)
  return isoDay(new Date(y, m - 1 + n, 1)).slice(0, 7)
}

/** День недели с понедельника: 0 — пн, 6 — вс. */
export const weekday = (iso) => (toDate(iso).getDay() + 6) % 7

export function clampIso(iso, min, max) {
  if (min && iso < min) return min
  if (max && iso > max) return max
  return iso
}

/** Первый и последний день прошлого месяца. */
export function previousMonth() {
  const now = new Date()
  return {
    date_from: isoDay(new Date(now.getFullYear(), now.getMonth() - 1, 1)),
    date_to: isoDay(new Date(now.getFullYear(), now.getMonth(), 0)),
  }
}

/**
 * «пятница, 13 ноября 2026» — полное название дня для экранного диктора.
 * weekday: false — «13 ноября 2026», для «с … по …».
 */
export function spokenDay(iso, { weekday = true } = {}) {
  return toDate(iso)
    .toLocaleDateString('ru-RU', { weekday: weekday ? 'long' : undefined, day: 'numeric', month: 'long', year: 'numeric' })
    .replace(/\s*г\.$/, '')
}
