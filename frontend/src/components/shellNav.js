import {
  Folders,
  LayoutDashboard,
  Megaphone,
  PlugZap,
  Settings,
  Sparkles,
  Workflow,
  Wrench,
} from '@lucide/vue'

/**
 * Навигация оболочки: группа — иконка в левой колонке, её страницы — вкладки в шапке.
 * В кабинете и в пространстве ровно четыре основные группы — столько же, сколько на нижней панели телефона.
 */
export const CABINET_NAV = [
  { key: 'overview', label: 'Обзор', icon: LayoutDashboard, pages: [{ label: 'Обзор', to: '/cabinet', exact: true }] },
  { key: 'clients', label: 'Клиенты', icon: Folders, pages: [{ label: 'Клиенты', to: '/cabinet/clients' }] },
  { key: 'accounts', label: 'Аккаунты', icon: PlugZap, pages: [{ label: 'Аккаунты', to: '/cabinet/accounts' }] },
  {
    key: 'tools',
    label: 'Инструменты',
    icon: Wrench,
    pages: [
      { label: 'Минус-фразы', to: '/cabinet/negative-lists' },
      { label: 'Правила', to: '/cabinet/rules' },
    ],
  },
]

export const CABINET_SETTINGS = {
  key: 'settings',
  label: 'Настройки',
  icon: Settings,
  pages: [{ label: 'Настройки', to: '/cabinet/settings' }],
}

export function workspaceNav(wsId, campaignsCount = 0) {
  const base = `/w/${wsId}`
  return [
    {
      key: 'overview',
      label: 'Обзор',
      icon: LayoutDashboard,
      pages: [
        { label: 'Сводка', to: `${base}/summary` },
        { label: 'Аналитика', to: `${base}/analytics` },
      ],
    },
    {
      key: 'campaigns',
      label: 'Кампании',
      icon: Megaphone,
      pages: [
        { label: 'Кампании', to: `${base}/campaigns`, count: campaignsCount || '' },
        { label: 'Поисковые запросы', to: `${base}/queries` },
        { label: 'Площадки и срезы', to: `${base}/slices` },
        { label: 'Объявления: CTR', to: `${base}/ad-stats` },
      ],
    },
    {
      key: 'automation',
      label: 'Автоматизация',
      short: 'Действия',
      icon: Workflow,
      pages: [
        { label: 'Действия и правила', to: `${base}/actions` },
        { label: 'Эксперименты', to: `${base}/experiments` },
      ],
    },
    { key: 'chat', label: 'ИИ-чат', icon: Sparkles, pages: [{ label: 'ИИ-чат', to: `${base}/chat` }] },
  ]
}

export function workspaceSettings(wsId) {
  return {
    key: 'settings',
    label: 'Настройки пространства',
    icon: Settings,
    pages: [{ label: 'Настройки', to: `/w/${wsId}/settings` }],
  }
}

export function pageMatches(page, path) {
  if (page.exact) return path === page.to
  return path === page.to || path.startsWith(`${page.to}/`)
}

export function groupMatches(group, path) {
  return group.pages.some((page) => pageMatches(page, path))
}
