import { blobUrl, request } from './web'
import { clearAdminSession, getAdminSession } from '../composables/useAdminSession'

/**
 * Админка владельца: отдельный вход по паролю ADMIN_PASSWORD, свой токен (не сессия кабинета).
 * 401 — токен истёк или неверен: сбрасываем его, админка показывает вход.
 * Ошибки — ApiError из web.js, текст для человека — errorText(err).
 */
const A = '/api/admin'

async function call(path, options = {}) {
  try {
    return await request(`${A}${path}`, { ...options, token: getAdminSession() })
  } catch (err) {
    if (err.status === 401) clearAdminSession()
    throw err
  }
}

const send = (method, path, body) =>
  call(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })

export const adminApi = {
  /** { token } — 401 unauthorized при неверном пароле, 429 too-many-attempts при переборе. */
  login: (password) =>
    request(`${A}/login`, { method: 'POST', token: '', body: JSON.stringify({ password }) }),
  /**
   * Сводка: { users_total, yandex_oauth, ai_configured, wordstat_configured, images_configured,
   * email_check (подтверждение почты включено — есть SMTP), smtp_configured, trial_plan (название | null) }.
   */
  me: () => call('/me'),
  /**
   * Страница списка (по 50). params: q, plan, sub (paying|trial|expired|none), activity (today|week|lost|never),
   * source (link:ID | group:ID | personal | ref | none), paid (yes|no), blocked, unverified, sort
   * (created|seen|paid|ai|ends), dir (asc|desc), page.
   * { items: [пользователь + last_seen_at, source, subscription, last_payment, paid { rub, count, days },
   *   ai_cost_rub, margin_rub, usage { spent, limit }, projects { count, direct, campaigns }, support_unread],
   *   summary { found, paying, today }, page, pages, per_page, options { plans, groups, links } }.
   */
  users: (params = {}) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v !== '' && v !== null && v !== undefined && v !== false) q.set(k, v)
    return call(`/users?${q}`)
  },
  /**
   * Дашборд. params: p (day|7d|30d|90d|180d|365d|all) или from/to («ГГГГ-ММ-ДД»), step (day|week|month).
   * { period { preset, from, to, days, step, prev }, current, previous, changes (ключ → % или null), now (срез
   *   на сейчас), series / prev_series (по корзинам шага), status { services, support_open, no_receipt },
   *   conversion_window_days }. Сервер держит ответ минуту.
   */
  dashboard: (params = {}) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) if (v) q.set(k, v)
    return call(`/dashboard?${q}`)
  },
  /** Карточка для панели: { user (как строка списка), days, ai_days, ai_tasks, chats, project_list }. */
  userCard: (id) => call(`/users/${id}/card`),
  blockUser: (id, reason = '') => send('POST', `/users/${id}/block`, { reason }),
  unblockUser: (id) => send('POST', `/users/${id}/unblock`),
  /**
   * Правка подписки — только переданные поля: plan_id (null — без тарифа), until («ГГГГ-ММ-ДД» — последний
   * день, null — без срока), overrides ({ units_limit, projects_limit, features }: ключа нет — как в тарифе,
   * null — без лимита), renew (продлить на срок тарифа). Ответ — подписка, как в списке пользователей.
   */
  setSubscription: (id, payload) => send('PUT', `/users/${id}/subscription`, payload),
  verifyEmail: (id) => send('POST', `/users/${id}/verify-email`),
  /** Журнал пользователя: { items: [{ kind, label, meta, created_at }], kinds }. */
  events: (id, limit = 100) => call(`/users/${id}/events?limit=${limit}`),
  /** Обращения: { messages: [{ id, author, body, has_image, context, created_at }], topic }. */
  support: (id) => call(`/users/${id}/support`),
  supportReply: (id, text) => send('POST', `/users/${id}/support`, { text }),
  supportImage: (messageId) => blobUrl(`${A}/support/images/${messageId}`, { token: getAdminSession() }),

  /** { items: [тариф + users], upgrades: [{ from_plan_id, to_plan_id, discount_pct }], features: { ключ: название } }. */
  plans: () => call('/plans'),
  createPlan: (payload) => send('POST', '/plans', payload),
  updatePlan: (id, payload) => send('PUT', `/plans/${id}`, payload),
  /** 409 plan-in-use — на тарифе есть пользователи. */
  deletePlan: (id) => send('DELETE', `/plans/${id}`),
  /** Скидка за переход с тарифа на тариф, %; 0 или пусто — без скидки. */
  setUpgrade: (fromPlanId, toPlanId, discountPct) =>
    send('PUT', '/plan-upgrades', { from_plan_id: fromPlanId, to_plan_id: toPlanId, discount_pct: discountPct }),

  /** Общий ключ ИИ, три класса моделей, классы фоновых задач. */
  ai: () => call('/ai'),
  saveAi: (payload) => send('PUT', '/ai', payload),
  /** Пробный вызов каждого включённого класса — без списания единиц. */
  testAi: () => send('POST', '/ai/test'),

  /** Проверка сайта на лендинге: своя модель (тот же адрес API и ключ), лимиты, кэш, счётчики. */
  siteCheck: () => call('/site-check'),
  saveSiteCheck: (payload) => send('PUT', '/site-check', payload),
  /** Пробный вызов модели проверки — без списания единиц. */
  testSiteCheck: () => send('POST', '/site-check/test'),

  /** Сайт: контакты, ключи SmartCaptcha (ключ сервера не возвращается), лимиты ИИ-генераторов. */
  site: () => call('/site'),
  saveSite: (payload) => send('PUT', '/site', payload),
  /** Лист ожидания со страниц «скоро»: { channels: [{id,label,count}], items }. */
  waitlist: () => call('/site/waitlist'),
  deleteWaitlist: (id) => send('DELETE', `/site/waitlist/${id}`),

  /** Мост Cloudflare: зарубежные модели с сервера в РФ. */
  bridge: () => call('/bridge'),
  saveBridge: (payload) => send('PUT', '/bridge', payload),
  /** Разворачивает Worker в аккаунте Cloudflare (API-токен не сохраняется). */
  deployBridge: (payload) => send('POST', '/bridge/deploy', payload),
  /** Откуда Worker выходит в интернет: { country, colo, blocked }. */
  testBridge: () => send('POST', '/bridge/test'),

  wordstat: () => call('/wordstat'),
  saveWordstat: (payload) => send('PUT', '/wordstat', payload),
  testWordstat: () => send('POST', '/wordstat/test'),

  images: () => call('/images'),
  saveImages: (payload) => send('PUT', '/images', payload),
  /** Одна настоящая картинка — платно для сервиса: { ok, model, image (data URL) }. */
  testImages: () => send('POST', '/images/test'),

  /**
   * ЮKassa: { configured, enabled, ready, shop_id, has_key, key_hint, test_mode, checked_at, check_error, check_test,
   * webhook_url, webhook_events }. secret_key: null — оставить, '' — удалить.
   */
  paymentsSettings: () => call('/payments/settings'),
  savePaymentsSettings: (payload) => send('PUT', '/payments/settings', payload),
  /** GET /me в ЮKassa: { ok, account_id, test, status, warning }. */
  testPayments: () => send('POST', '/payments/settings/test'),
  /** { items, summary: { month_count, month_rub, total_count, total_rub, refunded_rub, no_receipt } }. */
  payments: ({ userId = null, status = '', limit = 300 } = {}) => {
    const q = new URLSearchParams({ limit: String(limit) })
    if (userId) q.set('user_id', String(userId))
    if (status) q.set('status', status)
    return call(`/payments?${q}`)
  },
  /** Перечитать платёж из ЮKassa (статус, возврат). */
  syncPayment: (id) => send('POST', `/payments/${id}/sync`),
  /** Отметка «чек самозанятого пробит в „Мой налог“». */
  setReceipt: (id, done) => send('PUT', `/payments/${id}/receipt`, { done }),

  /** { items: [промокод + uses, plan_name], kinds: { units | days | plan: название } }. */
  promos: () => call('/promos'),
  createPromo: (payload) => send('POST', '/promos', payload),
  updatePromo: (id, payload) => send('PUT', `/promos/${id}`, payload),
  deletePromo: (id) => send('DELETE', `/promos/${id}`),

  /** { items: [{ id, code, name, active, url, visits, registrations, verified, payers, payments, revenue_rub }] }. */
  /**
   * Таблица реф-ссылок за период ({ p } или { from, to }; personal: 1 — и личные ссылки):
   * { period, spark_from, groups: [{ id, name, color, position }], personal_count, items: [{ id, code, name, active,
   * created_at, group_id, personal, owner_id, owner_email, url, clicks, spark: [7 дней], registrations, payers,
   * payments, repeat_payments, revenue_rub, cascade, cascade_rub }] }.
   */
  refs: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null).map(([k, v]) => [k, String(v)]))
    return call(`/refs?${q}`)
  },
  /** Бонус за приглашение и сводка личных ссылок: { bonus, personal: { links, registrations, bonuses, bonus_days } }. */
  refBonus: () => call('/ref-bonus'),
  /** Дни бонуса за приглашение по личной ссылке: { referrer_days, invitee_days }. */
  saveRefBonus: (payload) => send('PUT', '/ref-bonus', payload),
  /** { code, name, active, group_id? } — без group_id группа при правке не меняется. */
  createRef: (payload) => send('POST', '/refs', payload),
  updateRef: (id, payload) => send('PUT', `/refs/${id}`, payload),
  /** Несколько ссылок: action — move (в group_id, null — «Без группы»), activate, deactivate, delete. → { done }. */
  bulkRefs: (ids, action, groupId = null) => send('POST', '/refs/bulk', { ids, action, group_id: groupId }),
  createRefGroup: (payload) => send('POST', '/ref-groups', payload),
  updateRefGroup: (id, payload) => send('PUT', `/ref-groups/${id}`, payload),
  deleteRefGroup: (id) => send('DELETE', `/ref-groups/${id}`),
  /** Порядок групп: ids по очереди → { groups }. */
  orderRefGroups: (ids) => send('PUT', '/ref-groups/order', { ids }),

  /** Расход сервиса за месяц (ГГГГ-ММ, по умолчанию текущий): по видам и по пользователям. */
  usage: (month = '') => call(`/usage${month ? `?month=${encodeURIComponent(month)}` : ''}`),

  /**
   * Оценки ответов ИИ, новые сверху: { rating (-1 | 1 | 0 — все), model, reason, limit, offset } →
   * { total, items: [{ rating, reasons, comment, model, user, chat_id, message_id, question, answer }], models, reasons }.
   */
  feedback: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null).map(([k, v]) => [k, String(v)]))
    return call(`/feedback?${q}`)
  },
  /** Чат пользователя на чтение: { chat, messages: [{ role, content, model, feedback }] }. */
  chat: (id) => call(`/chats/${id}`),
}
