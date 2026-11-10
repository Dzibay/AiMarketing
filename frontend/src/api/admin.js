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
  /** Сводка: { users_total, yandex_oauth, ai_configured, wordstat_configured, images_configured }. */
  me: () => call('/me'),
  /** { items: [пользователь + usage { spent, limit, own_limit, cost_rub, calls }], total, default_units_limit }. */
  users: (limit = 200, offset = 0) => call(`/users?limit=${limit}&offset=${offset}`),
  blockUser: (id, reason = '') => send('POST', `/users/${id}/block`, { reason }),
  unblockUser: (id) => send('POST', `/users/${id}/unblock`),
  /** null — общий лимит сервиса, число — свой лимит единиц в месяц. */
  setLimit: (id, unitsLimit) => send('PUT', `/users/${id}/limit`, { units_limit: unitsLimit }),

  /** Общий ключ ИИ, три класса моделей, классы фоновых задач, общий лимит единиц. */
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

  /** Расход сервиса за месяц (ГГГГ-ММ, по умолчанию текущий): по видам и по пользователям. */
  usage: (month = '') => call(`/usage${month ? `?month=${encodeURIComponent(month)}` : ''}`),
}
