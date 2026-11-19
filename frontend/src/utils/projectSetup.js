/**
 * «Подготовим проект»: пять необязательных шагов настройки нового проекта. Готовность считаем
 * по полям проекта — отдельного флага «настроен» нет, поэтому карточка гаснет сама, как только
 * цель и шаблон ссылки заданы (здесь или в настройках).
 */

/** Метки по умолчанию, если в других проектах шаблона ещё нет. */
export const DEFAULT_TEMPLATE_QUERY = 'utm_source=yandex&utm_medium={source_type}&utm_campaign={campaign_id}'

export const SETUP_STEPS = [
  { key: 'client', label: 'клиент', done: (w) => w.client_id != null },
  { key: 'goal', label: 'цель Метрики', done: (w) => w.goal_id != null },
  { key: 'kpi', label: 'цена заявки или бюджет', done: (w) => w.kpi_target != null || w.monthly_budget_rub != null },
  { key: 'template', label: 'шаблон ссылки', done: (w) => !!w.url_template },
  { key: 'brief', label: 'описание бизнеса', done: (w) => !!w.brief?.product },
]

/**
 * Карточка «Проект готов на N из 5» на сводке: только пока нет цели или шаблона ссылки —
 * без них не посчитать заявки и не проверить ссылки. null — показывать нечего.
 */
export function setupProgress(ws) {
  if (!ws || ws.archived || ws.locked) return null
  if (ws.goal_id != null && ws.url_template) return null
  const missing = SETUP_STEPS.filter((s) => !s.done(ws))
  return { done: SETUP_STEPS.length - missing.length, total: SETUP_STEPS.length, missing: missing.map((s) => s.label) }
}

/** Адрес сайта без протокола и хвостового «/»: «site.ru» или «site.ru/catalog». */
function siteBase(site) {
  return (site || '')
    .trim()
    .replace(/^https?:\/\//i, '')
    .replace(/[?#].*$/, '')
    .replace(/\/+$/, '')
}

/**
 * Шаблон ссылки для нового проекта: адрес — этого сайта, метки — из шаблона последнего проекта
 * пользователя, где он задан (обычно метки у маркетолога одни на всех клиентов).
 */
export function suggestTemplate(ws, others = []) {
  if (ws?.url_template) return ws.url_template
  const base = siteBase(ws?.site)
  if (!base) return ''
  const prev = others
    .filter((o) => o.id !== ws.id && o.url_template)
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')) || b.id - a.id)[0]
  const query = prev ? prev.url_template.split('?').slice(1).join('?') : DEFAULT_TEMPLATE_QUERY
  return `https://${base}${base.includes('/') ? '' : '/'}${query ? `?${query}` : ''}`
}
