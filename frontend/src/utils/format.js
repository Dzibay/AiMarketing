/**
 * Форматирование чисел, денег и дат для кабинета — одна точка на всё приложение, вручную `toFixed` и `+ '%'`
 * не собираем. Деньги в базе — копейки, в настройках — рубли.
 *
 * Правила: разряды через пробел («32 942 ₽»), дробная часть через запятую, перед «%» и «₽» — неразрывный
 * пробел («4,1 %»), минус — настоящий «−», а не дефис.
 */

// Неразрывный пробел: «4,1 %» и «150 ₽» не разъезжаются по строкам.
const NBSP = '\u00a0'

export function rub(kopecks) {
  if (kopecks === null || kopecks === undefined || kopecks === '') return '—'
  return `${Math.round(Number(kopecks) / 100).toLocaleString('ru-RU')}${NBSP}₽`
}

export function rubExact(kopecks) {
  if (kopecks === null || kopecks === undefined || kopecks === '') return '—'
  return `${(Number(kopecks) / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${NBSP}₽`
}

/** Сумма уже в рублях (бюджеты кампаний, ставки стратегий). */
export function rubles(value, digits = 0) {
  if (value === null || value === undefined || value === '') return '—'
  return `${Number(value).toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })}${NBSP}₽`
}

/** Расход на ИИ: вызов стоит копейки, поэтому дробную часть не теряем. */
export function rubAi(value) {
  if (value === null || value === undefined || value === '') return '—'
  const v = Number(value)
  if (v > 0 && v < 0.01) return `< 0,01${NBSP}₽`
  return `${v.toLocaleString('ru-RU', { minimumFractionDigits: v < 1000 ? 2 : 0, maximumFractionDigits: 2 })}${NBSP}₽`
}

export function num(value, digits = 0) {
  if (value === null || value === undefined || value === '') return '—'
  return Number(value).toLocaleString('ru-RU', { maximumFractionDigits: digits })
}

/** Проценты: «4,1 %», целые — «12 %». digits — сколько знаков после запятой максимум. */
export function pct(value, digits = 1) {
  if (value === null || value === undefined || value === '') return '—'
  return `${Number(value).toLocaleString('ru-RU', { maximumFractionDigits: digits })}${NBSP}%`
}

/** Число со знаком: «+18», «−4», «0». */
export function signed(value, digits = 0) {
  if (value === null || value === undefined || value === '') return ''
  const v = Number(value)
  const text = Math.abs(v).toLocaleString('ru-RU', { maximumFractionDigits: digits })
  if (text === '0') return '0'
  return `${v > 0 ? '+' : '−'}${text}`
}

/** Дни коротко: «1,7 дн.», «12 дн.» — запас баланса, длина эксперимента. */
export function daysShort(value, digits = 1) {
  if (value === null || value === undefined || value === '') return '—'
  return `${Number(value).toLocaleString('ru-RU', { maximumFractionDigits: digits })}${NBSP}дн.`
}

/** Дни словом: «1 день», «12 дней», дробное — «1,7 дня». */
export function daysLong(value) {
  if (value === null || value === undefined || value === '') return '—'
  const v = Number(value)
  const text = v.toLocaleString('ru-RU', { maximumFractionDigits: 1 })
  if (text.includes(',')) return `${text}${NBSP}дня`
  return `${text}${NBSP}${plural(Math.round(v), 'день', 'дня', 'дней')}`
}

/** Доля месячного лимита ИИ: «0,4%», совсем мало — «< 0,1%». */
export function limitPct(value) {
  if (value === null || value === undefined || value === '') return '—'
  const v = Number(value)
  if (v > 0 && v < 0.1) return `< 0,1${NBSP}%`
  return pct(v, 1)
}

/** Итог операции с ИИ для подсказки: «ИИ: 3 вызова, ≈ 0,4% месячного лимита». Без лимита — только вызовы. */
export function aiUsageText(usage) {
  const calls = `ИИ: ${usage.calls} ${plural(usage.calls, 'вызов', 'вызова', 'вызовов')}`
  if (usage.percent === null || usage.percent === undefined) return calls
  return `${calls}, ${usage.estimated ? '≈ ' : ''}${limitPct(usage.percent)} месячного лимита`
}

/** Изменение к прошлому периоду: «+12 %», «−4 %», «0 %». */
export function delta(value) {
  if (value === null || value === undefined) return ''
  return `${signed(Math.round(Number(value)))}${NBSP}%`
}

/**
 * Тон изменения: для расхода и CPA рост — плохо, для конверсий и кликов — хорошо.
 * inverse=true — «больше — хуже».
 */
export function deltaTone(value, inverse = false) {
  if (value === null || value === undefined || Math.abs(value) < 5) return ''
  const good = inverse ? value < 0 : value > 0
  return good ? 'tone-ok' : 'tone-bad'
}

export function formatDateTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** «только что», «12 мин назад», «3 ч назад», дальше — дата и время. */
export function timeAgo(iso, now = Date.now()) {
  if (!iso) return ''
  const minutes = Math.round((now - new Date(iso).getTime()) / 60000)
  if (Number.isNaN(minutes)) return ''
  if (minutes < 1) return 'только что'
  if (minutes < 60) return `${minutes} мин назад`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} ч назад`
  return formatDateTime(iso)
}

export function formatDate(iso) {
  if (!iso) return '—'
  const d = iso.length === 10 ? new Date(`${iso}T00:00:00`) : new Date(iso)
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
}

/** «15 декабря 2026» — сроки подписки и тарифа. */
export function formatDay(iso) {
  if (!iso) return '—'
  const d = iso.length === 10 ? new Date(`${iso}T00:00:00`) : new Date(iso)
  // Без «г.»: дата часто стоит в конце фразы, и точка задваивалась бы.
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).replace(/\s*г\.$/, '')
}

export function plural(n, one, few, many) {
  const v = Math.abs(Number(n || 0))
  const mod10 = v % 10
  const mod100 = v % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few
  return many
}

export function isoDay(date) {
  const d = new Date(date)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Период «последние N дней по вчера» — как считает сервер по умолчанию. */
export function lastDays(n) {
  const to = new Date()
  to.setDate(to.getDate() - 1)
  const from = new Date(to)
  from.setDate(from.getDate() - (n - 1))
  return { date_from: isoDay(from), date_to: isoDay(to) }
}

/** Период «с начала месяца по вчера»; первого числа — весь прошлый месяц. */
export function monthToDate() {
  const to = new Date()
  to.setDate(to.getDate() - 1)
  const from = new Date(to.getFullYear(), to.getMonth(), 1)
  return { date_from: isoDay(from), date_to: isoDay(to) }
}

export const KPI_TONES = { ok: 'tone-ok', warn: 'tone-warn', bad: 'tone-bad', none: '' }
export const BUDGET_TONES = { ok: 'tone-ok', under: 'tone-warn', over: 'tone-bad', exhausted: 'tone-bad' }
export const SEVERITY_LABELS = { critical: 'Критично', warning: 'Внимание', info: 'К сведению' }

export function kpiValue(kpi) {
  if (!kpi || kpi.value == null) return '—'
  return kpi.type === 'cpa' ? rub(kpi.value) : pct(kpi.value)
}

export function kpiTarget(kpi) {
  if (!kpi?.type) return 'KPI не задан'
  const name = kpi.type === 'cpa' ? 'CPA' : 'ДРР'
  if (kpi.target == null) return `${name}, цель не задана`
  return kpi.type === 'cpa' ? `${name}, цель ${rubles(kpi.target)}` : `${name}, цель ${pct(kpi.target)}`
}

export function balanceTone(days) {
  if (days == null) return ''
  if (days < 2) return 'tone-bad'
  if (days < 5) return 'tone-warn'
  return ''
}

export const CAMPAIGN_STATES = {
  ON: 'Идут показы',
  OFF: 'Остановлена',
  SUSPENDED: 'Приостановлена',
  ENDED: 'Завершена',
  ARCHIVED: 'В архиве',
  CONVERTED: 'Конвертирована',
  UNKNOWN: '—',
}

export const CAMPAIGN_STATUSES = {
  ACCEPTED: 'Принята',
  DRAFT: 'Черновик',
  MODERATION: 'На модерации',
  REJECTED: 'Отклонена',
  UNKNOWN: '—',
}

/**
 * Один статус кампании для плашки UiStatus: {tone, label, hint}. В Директе их два — состояние показов (state)
 * и статус модерации (status); человеку нужен один, самый важный. Порядок: отклонена → архив и завершена →
 * остановлена вручную → черновик → модерация → не показывается → идут показы.
 */
export function campaignStatus(c) {
  const state = c?.state || ''
  const status = c?.status || ''
  if (status === 'REJECTED') {
    return { tone: 'bad', label: 'Отклонена', hint: 'Модерация Директа не пропустила объявления — их нужно исправить' }
  }
  if (state === 'ARCHIVED') return { tone: 'off', label: 'В архиве', hint: '' }
  if (state === 'ENDED') return { tone: 'off', label: 'Завершена', hint: 'Прошла дата окончания кампании' }
  if (state === 'SUSPENDED') return { tone: 'off', label: 'Остановлена', hint: 'Кампанию остановили вручную' }
  if (status === 'DRAFT') return { tone: 'off', label: 'Черновик', hint: 'Кампания ещё не отправлена на модерацию' }
  if (status === 'MODERATION') return { tone: 'wait', label: 'На модерации', hint: 'Директ проверяет объявления' }
  if (state === 'OFF') {
    return {
      tone: 'warn',
      label: 'Не показывается',
      hint: 'Директ не показывает кампанию: например, нет денег на счёте или не наступила дата начала',
    }
  }
  if (state === 'ON') return { tone: 'ok', label: 'Идут показы', hint: '' }
  return { tone: 'off', label: CAMPAIGN_STATES[state] || state || '—', hint: '' }
}

export const CAMPAIGN_TYPES = {
  TEXT_CAMPAIGN: 'Текстово-графическая',
  UNIFIED_CAMPAIGN: 'Единая перфоманс-кампания',
  DYNAMIC_TEXT_CAMPAIGN: 'Динамические',
  SMART_CAMPAIGN: 'Смарт-баннеры',
  MOBILE_APP_CAMPAIGN: 'Мобильные приложения',
  CPM_BANNER_CAMPAIGN: 'Медийная',
}

/** Ключевые цели для API: строки без цели пропускаем. Пустую ценность не подменяем:
 *  Директ требует от 0,3 ₽, и сервер назовёт цель, которую нужно заполнить. */
export function priorityGoalsForApi(goals) {
  return (goals || [])
    .filter((g) => g.goal_id)
    .map((g) => ({ goal_id: g.goal_id, value_rub: g.value_rub === '' || g.value_rub == null ? null : Number(g.value_rub) }))
}

const CONNECTION_PROBLEMS = new Set(['token', 'connection_sync', 'account_sync', 'stats_error', 'stats_stale', 'balance'])

/** Куда вести из строки «Где горит»: кампания, подключения, настройки проекта или его сводка. */
export function problemLink(p) {
  const ws = p.workspace_id
  if (p.campaign_row_id && ws) return { to: `/w/${ws}/campaigns/${p.campaign_row_id}`, label: 'К кампании' }
  if (CONNECTION_PROBLEMS.has(p.kind)) return { to: '/cabinet/connections', label: 'К подключениям' }
  if ((p.kind === 'budget' || p.kind === 'kpi') && ws) return { to: `/w/${ws}/settings`, label: 'Настройки' }
  if (ws) return { to: `/w/${ws}/summary`, label: 'Открыть' }
  return null
}

export const ACTION_SOURCES = { user: 'Вы', ai: 'ИИ', rule: 'Правило' }
export const ACTION_STATUS_TONES = { ok: 'tone-ok', partial: 'tone-warn', error: 'tone-bad', failed: 'tone-bad' }
