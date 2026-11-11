import { request } from './web'
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
   * { items: [пользователь + usage { spent, limit, cost_rub, calls, period_start, period_end }
   *   + subscription { plan, last_day, state, expired, units_limit, projects_limit, features, overrides }], total }.
   */
  users: (limit = 200, offset = 0) => call(`/users?limit=${limit}&offset=${offset}`),
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
  /** { items, bonus: { referrer_days, invitee_days }, personal: { links, registrations, bonuses, bonus_days } }. */
  refs: () => call('/refs'),
  /** Дни бонуса за приглашение по личной ссылке: { referrer_days, invitee_days }. */
  saveRefBonus: (payload) => send('PUT', '/ref-bonus', payload),
  createRef: (payload) => send('POST', '/refs', payload),
  updateRef: (id, payload) => send('PUT', `/refs/${id}`, payload),
  deleteRef: (id) => send('DELETE', `/refs/${id}`),

  /** Расход сервиса за месяц (ГГГГ-ММ, по умолчанию текущий): по видам и по пользователям. */
  usage: (month = '') => call(`/usage${month ? `?month=${encodeURIComponent(month)}` : ''}`),
}
