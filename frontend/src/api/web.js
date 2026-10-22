import { clearSession, getSession } from '../composables/useSession'

export class ApiError extends Error {
  constructor(code, status) {
    super(code)
    this.code = code
    this.status = status
  }
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (!headers['Content-Type'] && options.body) headers['Content-Type'] = 'application/json'
  const session = getSession()
  if (session && headers.Authorization !== '') headers.Authorization = `Bearer ${session}`
  if (headers.Authorization === '') delete headers.Authorization
  const response = await fetch(path, { cache: 'no-store', ...options, headers })
  const body = await response.json().catch(() => ({}))
  if (response.status === 401 && session && headers.Authorization && !path.endsWith('/login')) {
    clearSession()
  }
  if (!response.ok) throw new ApiError(body.detail || 'error', response.status)
  return body
}

export const webApi = {
  config: () => request('/api/web/config'),
  register: (payload) => request('/api/web/register', { method: 'POST', body: JSON.stringify(payload) }),
  login: (payload) => request('/api/web/login', { method: 'POST', body: JSON.stringify(payload) }),
  me: () => request('/api/web/me'),
  yandexStart: (purpose = 'login') => request(`/api/web/yandex/start?purpose=${encodeURIComponent(purpose)}`),
  syncConnection: (id) => request(`/api/web/connections/${id}/sync`, { method: 'POST' }),
  deleteConnection: (id) => request(`/api/web/connections/${id}`, { method: 'DELETE' }),
  patchMetrika: (id, enabled) =>
    request(`/api/web/metrika/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  patchDirect: (id, enabled) =>
    request(`/api/web/direct/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
}

export const ERRORS = {
  email: 'Введите корректную почту.',
  password: 'Пароль от 8 символов.',
  credentials: 'Неверная почта или пароль.',
  exists: 'Такой аккаунт уже есть. Войдите или войдите через Яндекс.',
  blocked: 'Доступ заблокирован.',
  unauthorized: 'Нужно войти заново.',
  'yandex-config': 'Подключение Яндекса ещё не настроено на сервере.',
  'base-url': 'Не задан адрес сайта для OAuth.',
  yandex: 'Яндекс не подтвердил доступ. Попробуйте снова.',
  state: 'Сессия подключения устарела. Нажмите кнопку ещё раз.',
  code: 'Код Яндекса не получен.',
  'yandex-taken': 'Этот Яндекс уже привязан к другому аккаунту.',
  'not-found': 'Не найдено.',
  purpose: 'Некорректный режим подключения.',
}

export function errorText(err) {
  if (err instanceof ApiError) {
    return ERRORS[err.code] || 'Что-то пошло не так. Попробуйте позже.'
  }
  return 'Что-то пошло не так. Попробуйте позже.'
}
