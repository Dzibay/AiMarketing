/** Форматирование чисел, денег и дат для кабинета. Деньги в базе — копейки, в настройках — рубли. */

export function rub(kopecks) {
  if (kopecks === null || kopecks === undefined || kopecks === '') return '—'
  return `${Math.round(Number(kopecks) / 100).toLocaleString('ru-RU')} ₽`
}

export function rubExact(kopecks) {
  if (kopecks === null || kopecks === undefined || kopecks === '') return '—'
  return `${(Number(kopecks) / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`
}

/** Сумма уже в рублях (бюджеты кампаний, ставки стратегий). */
export function rubles(value, digits = 0) {
  if (value === null || value === undefined || value === '') return '—'
  return `${Number(value).toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })} ₽`
}

/** Расход на ИИ: вызов стоит копейки, поэтому дробную часть не теряем. */
export function rubAi(value) {
  if (value === null || value === undefined || value === '') return '—'
  const v = Number(value)
  if (v > 0 && v < 0.01) return '< 0,01 ₽'
  return `${v.toLocaleString('ru-RU', { minimumFractionDigits: v < 1000 ? 2 : 0, maximumFractionDigits: 2 })} ₽`
}

export function num(value, digits = 0) {
  if (value === null || value === undefined || value === '') return '—'
  return Number(value).toLocaleString('ru-RU', { maximumFractionDigits: digits })
}

export function pct(value, digits = 1) {
  if (value === null || value === undefined || value === '') return '—'
  return `${Number(value).toLocaleString('ru-RU', { maximumFractionDigits: digits })}%`
}

/** Изменение к прошлому периоду: «+12%», «−4%». */
export function delta(value) {
  if (value === null || value === undefined) return ''
  const v = Math.round(Number(value))
  if (v === 0) return '0%'
  return `${v > 0 ? '+' : '−'}${Math.abs(v)}%`
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

export const KPI_TONES = { ok: 'tone-ok', warn: 'tone-warn', bad: 'tone-bad', none: '' }
export const BUDGET_TONES = { ok: 'tone-ok', under: 'tone-warn', over: 'tone-bad', exhausted: 'tone-bad' }
export const SEVERITY_LABELS = { critical: 'Критично', warning: 'Внимание', info: 'К сведению' }

export function kpiValue(kpi) {
  if (!kpi || kpi.value == null) return '—'
  return kpi.type === 'cpa' ? rub(kpi.value) : `${kpi.value}%`
}

export function kpiTarget(kpi) {
  if (!kpi?.type) return 'KPI не задан'
  const name = kpi.type === 'cpa' ? 'CPA' : 'ДРР'
  if (kpi.target == null) return `${name}, цель не задана`
  return kpi.type === 'cpa' ? `${name}, цель ${rubles(kpi.target)}` : `${name}, цель ${kpi.target}%`
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

export const CAMPAIGN_TYPES = {
  TEXT_CAMPAIGN: 'Текстово-графическая',
  UNIFIED_CAMPAIGN: 'Единая перфоманс-кампания',
  DYNAMIC_TEXT_CAMPAIGN: 'Динамические',
  SMART_CAMPAIGN: 'Смарт-баннеры',
  MOBILE_APP_CAMPAIGN: 'Мобильные приложения',
  CPM_BANNER_CAMPAIGN: 'Медийная',
}

/** Ключевые цели для API: строки без цели пропускаем, пустая ценность — 0 (цель без ценности тоже ключевая). */
export function priorityGoalsForApi(goals) {
  return (goals || []).filter((g) => g.goal_id).map((g) => ({ goal_id: g.goal_id, value_rub: Number(g.value_rub) || 0 }))
}

const CONNECTION_PROBLEMS = new Set(['token', 'connection_sync', 'account_sync', 'stats_error', 'stats_stale', 'balance'])

/** Куда вести из строки «Где горит»: кампания, аккаунты, настройки пространства или папка клиента. */
export function problemLink(p) {
  const ws = p.workspace_id
  if (p.campaign_row_id && ws) return { to: `/w/${ws}/campaigns/${p.campaign_row_id}`, label: 'К кампании' }
  if (CONNECTION_PROBLEMS.has(p.kind)) return { to: '/cabinet/accounts', label: 'К аккаунтам' }
  if ((p.kind === 'budget' || p.kind === 'kpi') && ws) return { to: `/w/${ws}/settings`, label: 'Настройки' }
  if (p.kind === 'budget' && p.client_id) return { to: '/cabinet/clients', label: 'К клиенту' }
  if (ws) return { to: `/w/${ws}/summary`, label: 'Открыть' }
  return null
}

export const ACTION_SOURCES = { user: 'Вы', ai: 'ИИ', rule: 'Правило' }
export const ACTION_STATUS_TONES = { ok: 'tone-ok', partial: 'tone-warn', error: 'tone-bad', failed: 'tone-bad' }
