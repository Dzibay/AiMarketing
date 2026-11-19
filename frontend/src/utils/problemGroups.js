import { niceQuotes, plural } from './format'

/**
 * Утренний разбор «Где горит»: одинаковые проблемы разных клиентов — одной строкой
 * «Деньги заканчиваются — 4 клиента», раскрыть — список. Одинаковые = тот же тип и та же серьёзность
 * (критичное не прячется в одной строке с предупреждениями). Одиночная проблема — обычная строка.
 */

// Общий заголовок группы, когда заголовки проблем внутри разные (в них цифры).
const GROUP_TITLES = {
  balance: 'Деньги заканчиваются',
  budget: 'Бюджет месяца идёт не по плану',
  // У KPI вид цели в p.subkind: цена заявки и доля расходов на рекламу — разные пачки.
  kpi_cpa: 'Заявки дороже цели',
  kpi_drr: 'Доля расходов на рекламу выше цели',
  kpi: 'Цель проекта не выполняется',
  ads_rejected: 'Отклонены объявления',
  spend_spike: 'Расход вырос',
  cpa_spike: 'Цена заявки выросла',
  token: 'Доступ к Яндексу',
  setup: 'Проект не настроен',
}

function placeOf(p) {
  return p.client_name || p.workspace_name || p.object_name || ''
}

/** «4 клиента», «5 кампаний», «2 кабинета» — что именно повторяется. */
export function groupCount(items) {
  const n = items.length
  // По одной у разных клиентов — «4 клиента»; несколько у одного — считаем сами объекты.
  const clients = new Set(items.map((p) => p.client_name).filter(Boolean))
  if (clients.size === n) return `${n} ${plural(n, 'клиент', 'клиента', 'клиентов')}`
  if (items.every((p) => p.campaign_row_id)) return `${n} ${plural(n, 'кампания', 'кампании', 'кампаний')}`
  if (items.every((p) => p.account_id)) return `${n} ${plural(n, 'кабинет', 'кабинета', 'кабинетов')}`
  return `${n} ${plural(n, 'проект', 'проекта', 'проектов')}`
}

function groupTitle(items) {
  const titles = new Set(items.map((p) => p.title))
  const base =
    titles.size === 1 ? items[0].title : GROUP_TITLES[items[0].subkind] || GROUP_TITLES[items[0].kind] || items[0].title
  return `${base} — ${groupCount(items)}`
}

function groupWhere(items) {
  const places = [...new Set(items.map(placeOf).filter(Boolean))].map(niceQuotes)
  return places.slice(0, 3).join(', ') + (places.length > 3 ? ` и ещё ${places.length - 3}` : '')
}

/** Одинаковые = тот же тип (у KPI — и вид цели, p.subkind) и та же серьёзность. */
function groupKey(p) {
  return `${p.subkind || p.kind}:${p.severity}`
}

/**
 * Список проблем (уже отсортирован сервером) → строки: проблема или группа
 * {group: true, key, kind, severity, title, where, items, is_new}. Группа стоит на месте первой своей проблемы.
 */
export function groupProblems(problems) {
  const byKey = new Map()
  for (const p of problems) {
    const key = groupKey(p)
    if (!byKey.has(key)) byKey.set(key, [])
    byKey.get(key).push(p)
  }
  const out = []
  const placed = new Set()
  for (const p of problems) {
    const key = groupKey(p)
    const items = byKey.get(key)
    if (items.length < 2) {
      out.push(p)
      continue
    }
    if (placed.has(key)) continue
    placed.add(key)
    out.push({
      group: true,
      key: `g:${key}`,
      kind: p.kind,
      severity: p.severity,
      title: groupTitle(items),
      where: groupWhere(items),
      items,
      is_new: items.some((x) => x.is_new),
    })
  }
  return out
}

/** Сколько проблем в строках (группа — все её проблемы). */
export function problemCount(rows) {
  return rows.reduce((n, r) => n + (r.group ? r.items.length : 1), 0)
}

/** «Со вчера: 3 новых, 2 решились» — по сводке review {since, new, resolved}; снимка нет — пусто. */
export function reviewText(review, yesterday) {
  if (!review) return ''
  let since = 'Со вчера'
  if (review.since !== yesterday) {
    const [, m, d] = String(review.since).split('-')
    since = `С ${d}.${m}`
  }
  const parts = []
  if (review.new) parts.push(`${review.new} ${plural(review.new, 'новая', 'новых', 'новых')}`)
  if (review.resolved) parts.push(`${review.resolved} ${plural(review.resolved, 'решилась', 'решились', 'решились')}`)
  return parts.length ? `${since}: ${parts.join(', ')}` : `${since} ничего не изменилось`
}
