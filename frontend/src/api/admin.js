import { request } from './web'

/**
 * Админка владельца: та же сессия, что в кабинете; доступ — только почте из ADMIN_EMAILS (иначе 403 forbidden).
 * Ошибки — ApiError из web.js, текст для человека — errorText(err).
 */
const A = '/api/admin'

const send = (method, path, body) =>
  request(`${A}${path}`, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })

export const adminApi = {
  /** Сводка: { email, users_total, yandex_oauth, ai_configured, wordstat_configured, images_configured }. */
  me: () => request(`${A}/me`),
  /** { items: [пользователь + usage { spent, limit, own_limit, cost_rub, calls }], total, default_units_limit }. */
  users: (limit = 200, offset = 0) => request(`${A}/users?limit=${limit}&offset=${offset}`),
  blockUser: (id, reason = '') => send('POST', `/users/${id}/block`, { reason }),
  unblockUser: (id) => send('POST', `/users/${id}/unblock`),
  /** null — общий лимит сервиса, число — свой лимит единиц в месяц. */
  setLimit: (id, unitsLimit) => send('PUT', `/users/${id}/limit`, { units_limit: unitsLimit }),

  /** Общий ключ ИИ, три класса моделей, классы фоновых задач, общий лимит единиц. */
  ai: () => request(`${A}/ai`),
  saveAi: (payload) => send('PUT', '/ai', payload),
  /** Пробный вызов каждого включённого класса — без списания единиц. */
  testAi: () => send('POST', '/ai/test'),

  /** Мост Cloudflare: зарубежные модели с сервера в РФ. */
  bridge: () => request(`${A}/bridge`),
  saveBridge: (payload) => send('PUT', '/bridge', payload),
  /** Разворачивает Worker в аккаунте Cloudflare (API-токен не сохраняется). */
  deployBridge: (payload) => send('POST', '/bridge/deploy', payload),
  /** Откуда Worker выходит в интернет: { country, colo, blocked }. */
  testBridge: () => send('POST', '/bridge/test'),

  wordstat: () => request(`${A}/wordstat`),
  saveWordstat: (payload) => send('PUT', '/wordstat', payload),
  testWordstat: () => send('POST', '/wordstat/test'),

  images: () => request(`${A}/images`),
  saveImages: (payload) => send('PUT', '/images', payload),
  /** Одна настоящая картинка — платно для сервиса: { ok, model, image (data URL) }. */
  testImages: () => send('POST', '/images/test'),

  /** Расход сервиса за месяц (ГГГГ-ММ, по умолчанию текущий): по видам и по пользователям. */
  usage: (month = '') => request(`${A}/usage${month ? `?month=${encodeURIComponent(month)}` : ''}`),
}
