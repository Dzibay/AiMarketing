import { clearAdminSession, getAdminSession } from '../composables/useAdminSession'

export class AdminApiError extends Error {
  constructor(code, status) {
    super(code)
    this.code = code
    this.status = status
  }
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (!headers['Content-Type'] && options.body) headers['Content-Type'] = 'application/json'
  const session = getAdminSession()
  if (session) headers.Authorization = `Bearer ${session}`
  const response = await fetch(path, { cache: 'no-store', ...options, headers })
  const body = await response.json().catch(() => ({}))
  if (response.status === 401 && session && !path.endsWith('/login')) clearAdminSession()
  if (!response.ok) throw new AdminApiError(body.detail || 'error', response.status)
  return body
}

export const adminApi = {
  login: (password) => request('/api/admin/login', { method: 'POST', body: JSON.stringify({ password }) }),
  me: () => request('/api/admin/me'),
  users: () => request('/api/admin/users'),
}
