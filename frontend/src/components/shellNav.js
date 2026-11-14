import { ChartLine, CreditCard, History, House, ListFilter, Megaphone, PlugZap, Settings, Sparkles, Workflow } from '@lucide/vue'

/**
 * Одно меню, которое не меняется от выбора проекта. Список проектов в меню — фильтр: тот же раздел открывается
 * по всем проектам (/cabinet/…) или по одному (/w/:id/…). Настройки проекта — шестерёнка у выбранного проекта.
 *
 * Страницы помечают раздел в meta маршрута: { section, scope }. scope: 'all' — страница по всем проектам,
 * 'project' — по одному, 'neutral' — от проекта не зависит (подключения, тариф): фильтр на ней не сбрасывается.
 * expert у раздела или вкладки — виден только в экспертном режиме (Настройки → Интерфейс).
 * feature у вкладки — функция тарифа: выключена — у вкладки замок, раздел открывается с подсказкой о тарифе.
 * hint — строка пояснения под названием в шторке «Ещё» на телефоне (BottomBar).
 */
export const SECTIONS = [
  { key: 'summary', label: 'Главная', icon: House, all: '/cabinet', project: (id) => `/w/${id}/summary` },
  {
    key: 'chat',
    label: 'ИИ-помощник',
    icon: Sparkles,
    all: '/cabinet/chat',
    project: (id) => `/w/${id}/chat`,
    hidden: true, // в меню — кнопкой «Новый чат»
  },
  {
    key: 'campaigns',
    label: 'Кампании',
    icon: Megaphone,
    all: '/cabinet/campaigns',
    project: (id) => `/w/${id}/campaigns`,
  },
  {
    key: 'queries',
    label: 'Запросы',
    icon: ListFilter,
    all: '/cabinet/queries',
    project: (id) => `/w/${id}/queries`,
    hint: 'Что искали люди и какие запросы лишние',
  },
  {
    key: 'analytics',
    label: 'Отчёты',
    icon: ChartLine,
    all: '/cabinet/analytics',
    project: (id) => `/w/${id}/analytics`,
    hint: 'Расход, заявки и цена заявки по дням',
  },
  {
    key: 'autopilot',
    label: 'Автопилот',
    icon: Workflow,
    all: '/cabinet/autopilot',
    project: (id) => `/w/${id}/autopilot`,
    expert: true,
    hint: 'Правила, которые работают без вас',
  },
  {
    key: 'history',
    label: 'История',
    icon: History,
    all: '/cabinet/history',
    project: (id) => `/w/${id}/history`,
    hint: 'Что менялось в кампаниях и кто это сделал',
  },
  {
    key: 'project-settings',
    label: 'Настройки проекта',
    icon: Settings,
    all: null,
    project: (id) => `/w/${id}/settings`,
    hidden: true, // шестерёнка у выбранного проекта
  },
]

/** Служебные разделы: в меню профиля и в «Ещё» на телефоне, от проекта не зависят. */
export const SERVICE = [
  { key: 'settings', label: 'Настройки', icon: Settings, to: '/cabinet/settings', hint: 'Профиль, уведомления, интерфейс' },
  { key: 'connections', label: 'Подключения', icon: PlugZap, to: '/cabinet/connections', hint: 'Сайты, Директ и Метрика' },
  { key: 'plan', label: 'Тариф и лимит', icon: CreditCard, to: '/cabinet/plan', hint: 'Срок тарифа и остаток лимита ИИ' },
]

/** Адрес раздела при выбранном проекте (id) или «Все проекты» (null); null — раздел без версии «по всем». */
export function sectionTo(section, projectId) {
  if (projectId) return section.project(projectId)
  return section.all
}

/** Вкладки внутри раздела — под верхней строкой. Вкладки с expert — только в экспертном режиме. */
export function sectionTabs(key, projectId) {
  const p = projectId ? `/w/${projectId}` : null
  switch (key) {
    case 'queries':
      return [
        { label: 'Разбор', to: p ? `${p}/queries` : '/cabinet/queries' },
        { label: 'Минус-фразы', to: '/cabinet/negative-lists', expert: true },
      ]
    case 'analytics':
      if (!p) return []
      return [
        { label: 'Динамика', to: `${p}/analytics` },
        { label: 'Площадки', to: `${p}/slices`, expert: true },
        { label: 'Объявления', to: `${p}/ad-stats`, expert: true },
      ]
    case 'autopilot':
      if (!p) return [{ label: 'Общие правила', to: '/cabinet/autopilot', feature: 'autopilot' }]
      return [
        { label: 'Правила', to: `${p}/autopilot`, feature: 'autopilot' },
        { label: 'Эксперименты', to: `${p}/experiments`, feature: 'experiments' },
      ]
    case 'settings':
      return [
        { label: 'Профиль', to: '/cabinet/settings', exact: true },
        { label: 'Уведомления', to: '/cabinet/settings/notifications' },
        { label: 'Интерфейс', to: '/cabinet/settings/interface' },
      ]
    default:
      return []
  }
}

export function pageMatches(page, path) {
  if (page.exact) return path === page.to
  return path === page.to || path.startsWith(`${page.to}/`)
}
