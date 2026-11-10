import {
  ChartLine,
  CreditCard,
  History,
  LayoutDashboard,
  ListFilter,
  Megaphone,
  PlugZap,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Workflow,
} from '@lucide/vue'

/**
 * Одно меню, которое не меняется от выбора проекта. Переключатель проекта — фильтр: тот же раздел открывается
 * по всем проектам (/cabinet/…) или по одному (/w/:id/…). Раздел без версии «по всем» (Настройки проекта)
 * при «Все проекты» серый с подсказкой.
 *
 * Страницы помечают раздел в meta маршрута: { section, scope }. scope: 'all' — страница по всем проектам,
 * 'project' — по одному, 'neutral' — от проекта не зависит (подключения, тариф): фильтр на ней не сбрасывается.
 * feature у вкладки — функция тарифа: выключена — у вкладки замок, раздел открывается с подсказкой о тарифе.
 */
export const SECTIONS = [
  { key: 'summary', label: 'Сводка', icon: LayoutDashboard, all: '/cabinet', project: (id) => `/w/${id}/summary` },
  {
    key: 'chat',
    label: 'ИИ-помощник',
    short: 'ИИ',
    icon: Sparkles,
    all: '/cabinet/chat',
    project: (id) => `/w/${id}/chat`,
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
    label: 'Чистка запросов',
    short: 'Запросы',
    icon: ListFilter,
    all: '/cabinet/queries',
    project: (id) => `/w/${id}/queries`,
  },
  {
    key: 'analytics',
    label: 'Аналитика',
    icon: ChartLine,
    all: '/cabinet/analytics',
    project: (id) => `/w/${id}/analytics`,
  },
  {
    key: 'autopilot',
    label: 'Автопилот',
    icon: Workflow,
    all: '/cabinet/autopilot',
    project: (id) => `/w/${id}/autopilot`,
  },
  { key: 'history', label: 'История', icon: History, all: '/cabinet/history', project: (id) => `/w/${id}/history` },
  {
    key: 'project-settings',
    label: 'Настройки проекта',
    icon: SlidersHorizontal,
    all: null,
    project: (id) => `/w/${id}/settings`,
    hint: 'Выберите проект',
  },
]

/** Служебные разделы внизу меню: от проекта не зависят. */
export const SERVICE = [
  { key: 'connections', label: 'Подключения', icon: PlugZap, to: '/cabinet/connections' },
  { key: 'settings', label: 'Настройки', icon: Settings, to: '/cabinet/settings' },
  { key: 'plan', label: 'Тариф', icon: CreditCard, to: '/cabinet/plan' },
]

/** На нижней панели телефона — четыре главных раздела, остальные в «Ещё». */
export const TABBAR_KEYS = ['summary', 'chat', 'campaigns', 'queries']

/** Адрес раздела при выбранном проекте (id) или «Все проекты» (null); null — раздел без версии «по всем». */
export function sectionTo(section, projectId) {
  if (projectId) return section.project(projectId)
  return section.all
}

/** Вкладки внутри раздела — в шапке страницы. */
export function sectionTabs(key, projectId) {
  const p = projectId ? `/w/${projectId}` : null
  switch (key) {
    case 'queries':
      return [
        { label: 'Разбор запросов', to: p ? `${p}/queries` : '/cabinet/queries' },
        { label: 'Списки минус-фраз', to: '/cabinet/negative-lists' },
      ]
    case 'analytics':
      if (!p) return []
      return [
        { label: 'Динамика', to: `${p}/analytics` },
        { label: 'Площадки', to: `${p}/slices` },
        { label: 'Объявления', to: `${p}/ad-stats` },
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
      ]
    default:
      return []
  }
}

export function pageMatches(page, path) {
  if (page.exact) return path === page.to
  return path === page.to || path.startsWith(`${page.to}/`)
}
