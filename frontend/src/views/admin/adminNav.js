import {
  Activity,
  Banknote,
  CreditCard,
  Filter,
  FolderKanban,
  Globe,
  LayoutDashboard,
  LifeBuoy,
  Link2,
  Send,
  Settings,
  ThumbsUp,
  Users,
  Wallet,
} from '@lucide/vue'

/**
 * Меню админки. Рабочие страницы — группами, всё настраиваемое — в «Настройках» (свои пункты ниже, SETTINGS).
 * badge(summary) — число у пункта (0 — не показываем); warn(summary) — кольцо «что-то не настроено».
 * План развития разделов — docs/admin-plan.md.
 */
export const ADMIN_BASE = '/admin-panel'

export const NAV = [
  {
    title: '',
    items: [{ key: 'dashboard', label: 'Дашборд', icon: LayoutDashboard, to: ADMIN_BASE, exact: true }],
  },
  {
    title: 'Работа',
    items: [
      { key: 'users', label: 'Пользователи', icon: Users, to: `${ADMIN_BASE}/users` },
      {
        key: 'payments',
        label: 'Платежи',
        icon: CreditCard,
        to: `${ADMIN_BASE}/payments`,
        badge: (s) => s?.payments?.no_receipt || 0,
        badgeTip: 'Оплаты без чека в «Мой налог»',
      },
      {
        key: 'support',
        label: 'Обращения',
        icon: LifeBuoy,
        to: `${ADMIN_BASE}/support`,
        badge: (s) => s?.support_waiting || 0,
        badgeTip: 'Переписки, где ждут ответа',
      },
      { key: 'outreach', label: 'Кому написать', icon: Send, to: `${ADMIN_BASE}/outreach` },
    ],
  },
  {
    title: 'Аналитика',
    items: [
      { key: 'site', label: 'Сайт', icon: Globe, to: `${ADMIN_BASE}/site` },
      { key: 'funnel', label: 'Воронка', icon: Filter, to: `${ADMIN_BASE}/funnel` },
      { key: 'refs', label: 'Реф-ссылки', icon: Link2, to: `${ADMIN_BASE}/refs` },
      { key: 'finance', label: 'Финансы', icon: Banknote, to: `${ADMIN_BASE}/finance` },
    ],
  },
  {
    title: 'ИИ',
    items: [
      { key: 'ai-costs', label: 'Расходы на ИИ', icon: Wallet, to: `${ADMIN_BASE}/ai-costs` },
      { key: 'ai-quality', label: 'Качество ИИ', icon: ThumbsUp, to: `${ADMIN_BASE}/ai-quality` },
    ],
  },
  {
    title: 'Сервис',
    items: [
      { key: 'projects', label: 'Проекты и Директ', icon: FolderKanban, to: `${ADMIN_BASE}/projects` },
      { key: 'system', label: 'Система', icon: Activity, to: `${ADMIN_BASE}/system` },
    ],
  },
]

/** «Настройки» — внизу меню, отдельно от рабочих страниц. */
export const SETTINGS_ITEM = {
  key: 'settings',
  label: 'Настройки',
  icon: Settings,
  to: `${ADMIN_BASE}/settings`,
  warn: (s) => missingSettings(s).length > 0,
}

const S = `${ADMIN_BASE}/settings`

/**
 * Пункты «Настроек». status(summary): true — работает, false — не настроено, undefined — состояния нет.
 * Путь (path) — часть адреса после /settings/.
 */
export const SETTINGS = [
  {
    title: 'Продажи',
    items: [
      { path: 'plans', label: 'Тарифы', hint: 'Цены, сроки, лимиты и скидки за переход на другой тариф' },
      { path: 'promos', label: 'Промокоды', hint: 'Скидки и бесплатные дни по коду' },
      { path: 'payments', label: 'Оплата', hint: 'ЮKassa: приём оплаты картой и СБП, уведомления', status: (s) => s?.payments_ready },
      { path: 'referral', label: 'Бонус за приглашение', hint: 'Дни к подписке обоим, когда приглашённый друг оплатит' },
    ],
  },
  {
    title: 'ИИ',
    items: [
      { path: 'ai', label: 'ИИ и модели', hint: 'Ключ, классы моделей, запасные модели, фоновые задачи', status: (s) => s?.ai_configured },
      { path: 'bridge', label: 'Мост Cloudflare', hint: 'Доступ к зарубежным моделям через свой Worker или AI Gateway' },
      { path: 'wordstat', label: 'Вордстат', hint: 'Частоты запросов — ИИ подбирает фразы по ним', status: (s) => s?.wordstat_configured },
      { path: 'images', label: 'Генерация картинок', hint: 'Картинки для объявлений', status: (s) => s?.images_configured },
      { path: 'site-check', label: 'Проверка сайта', hint: 'Бесплатная проверка сайта на лендинге: модель и лимиты' },
    ],
  },
  {
    title: 'Сайт',
    items: [{ path: 'site', label: 'Контакты и защита', hint: 'Контакты в подвале, SmartCaptcha, ИИ-генераторы лендинга' }],
  },
  {
    title: 'Владельцу',
    items: [{ path: 'alerts', label: 'Уведомления', hint: 'Оплаты и сбои — в Telegram-группу поддержки, темы «Оплаты» и «Сбои»' }],
  },
].map((group) => ({ ...group, items: group.items.map((item) => ({ ...item, to: `${S}/${item.path}` })) }))

/** Что задаётся на сервере (переменные окружения): в админке только состояние. */
export const SERVER_SETTINGS = [
  { key: 'yandex_oauth', label: 'Вход через Яндекс', hint: 'YANDEX_CLIENT_ID и YANDEX_CLIENT_SECRET', status: (s) => s?.yandex_oauth },
  {
    key: 'smtp',
    label: 'Почта (SMTP)',
    hint: 'Без неё письма не уходят и подтверждение почты выключено',
    status: (s) => s?.smtp_configured,
  },
]

/** Названия того, что не настроено: для сводки наверху «Настроек» и кольца у пункта меню. */
export function missingSettings(summary) {
  if (!summary) return []
  const own = SETTINGS.flatMap((g) => g.items).filter((i) => i.status && !i.status(summary))
  const server = SERVER_SETTINGS.filter((i) => !i.status(summary))
  return [...own, ...server].map((i) => i.label)
}

/** Старые адреса /admin-panel?tab=… — куда они переехали. */
export const OLD_TABS = {
  users: `${ADMIN_BASE}/users`,
  plans: `${S}/plans`,
  payments: `${ADMIN_BASE}/payments`,
  promos: `${S}/promos`,
  refs: `${ADMIN_BASE}/refs`,
  services: `${S}/ai`,
  site: `${S}/site`,
  usage: `${ADMIN_BASE}/ai-costs`,
  feedback: `${ADMIN_BASE}/ai-quality?tab=feedback`,
  waitlist: `${ADMIN_BASE}/site?tab=waitlist`,
}

export function navActive(item, path) {
  if (item.exact) return path === item.to || path === `${item.to}/`
  return path === item.to || path.startsWith(`${item.to}/`)
}
