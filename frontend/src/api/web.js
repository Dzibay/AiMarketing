import { clearSession, getSession } from '../composables/useSession'

export class ApiError extends Error {
  constructor(code, status, message = '') {
    super(message || code)
    this.code = typeof code === 'string' ? code : 'error'
    this.status = status
    this.messageDetail = message || ''
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
  if (!response.ok) {
    const detail = body.detail
    if (detail && typeof detail === 'object') {
      throw new ApiError(detail.code || 'error', response.status, detail.message || '')
    }
    throw new ApiError(detail || 'error', response.status)
  }
  return body
}

const JOB_POLL_MS = 1500
const JOB_TIMEOUT_MS = 20 * 60 * 1000
const JOB_ACTIVE = new Set(['queued', 'running'])

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/** Ждёт фоновую задачу: возвращает job.result или бросает ApiError с её кодом ошибки. */
export async function waitJob(job, onProgress) {
  const started = Date.now()
  let current = job
  while (JOB_ACTIVE.has(current.status)) {
    if (onProgress) onProgress(current)
    if (Date.now() - started > JOB_TIMEOUT_MS) throw new ApiError('job-timeout', 0)
    await sleep(JOB_POLL_MS)
    current = await request(`/api/web/jobs/${current.id}`)
  }
  if (current.status === 'error') {
    const code = current.error_code || 'error'
    // Текст внутренних исключений пользователю не показываем.
    throw new ApiError(code, 400, code === 'internal' ? '' : current.error || '')
  }
  return current.result || {}
}

async function runJob(path, body, onProgress) {
  const options = { method: 'POST' }
  if (body !== undefined) options.body = JSON.stringify(body)
  const { job } = await request(path, options)
  return waitJob(job, onProgress)
}

export const webApi = {
  config: () => request('/api/web/config'),
  register: (payload) => request('/api/web/register', { method: 'POST', body: JSON.stringify(payload) }),
  login: (payload) => request('/api/web/login', { method: 'POST', body: JSON.stringify(payload) }),
  me: () => request('/api/web/me'),
  yandexStart: (purpose = 'login') => request(`/api/web/yandex/start?purpose=${encodeURIComponent(purpose)}`),
  syncConnection: (id, onProgress) => runJob(`/api/web/connections/${id}/sync`, undefined, onProgress),
  deleteConnection: (id) => request(`/api/web/connections/${id}`, { method: 'DELETE' }),
  patchMetrika: (id, enabled) =>
    request(`/api/web/metrika/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  patchDirect: (id, enabled) =>
    request(`/api/web/direct/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  syncAccount: (accountId, onProgress) =>
    runJob(`/api/web/direct/${accountId}/sync`, undefined, onProgress),
  clients: () => request('/api/web/clients'),
  createClient: (payload) => request('/api/web/clients', { method: 'POST', body: JSON.stringify(payload) }),
  patchClient: (id, payload) =>
    request(`/api/web/clients/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteClient: (id) => request(`/api/web/clients/${id}`, { method: 'DELETE' }),
  assignDirectClient: (accountId, clientId) =>
    request(`/api/web/direct/${accountId}/client`, {
      method: 'PUT',
      body: JSON.stringify({ client_id: clientId || null }),
    }),
  assignMetrikaClient: (counterRowId, clientId) =>
    request(`/api/web/metrika/${counterRowId}/client`, {
      method: 'PUT',
      body: JSON.stringify({ client_id: clientId || null }),
    }),
  jobs: (params = {}) => {
    const q = new URLSearchParams()
    if (params.active) q.set('active', 'true')
    if (params.limit != null) q.set('limit', params.limit)
    const qs = q.toString()
    return request(`/api/web/jobs${qs ? `?${qs}` : ''}`)
  },
  campaigns: (params = {}) => {
    const q = new URLSearchParams()
    if (params.account_id) q.set('account_id', params.account_id)
    if (params.state) q.set('state', params.state)
    if (params.search) q.set('search', params.search)
    if (params.limit != null) q.set('limit', params.limit)
    if (params.offset != null) q.set('offset', params.offset)
    const qs = q.toString()
    return request(`/api/web/direct/campaigns${qs ? `?${qs}` : ''}`)
  },
  changePassword: (payload) =>
    request('/api/web/change-password', { method: 'POST', body: JSON.stringify(payload) }),
  bulkAction: (payload) =>
    request('/api/web/direct/campaigns/actions', { method: 'POST', body: JSON.stringify(payload) }),
  createCampaign: (payload) =>
    request('/api/web/direct/campaigns', { method: 'POST', body: JSON.stringify(payload) }),
  updateCampaign: (id, payload) =>
    request(`/api/web/direct/campaigns/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  campaignDetail: (id) => request(`/api/web/direct/campaigns/${id}`),
  syncCampaignStructure: (id, onProgress) =>
    runJob(`/api/web/direct/campaigns/${id}/sync-structure`, undefined, onProgress),
  createAdGroup: (campaignRowId, payload) =>
    request(`/api/web/direct/campaigns/${campaignRowId}/adgroups`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  patchAdGroup: (id, payload) =>
    request(`/api/web/direct/adgroups/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAdGroups: (ids) =>
    request('/api/web/direct/adgroups/delete', {
      method: 'POST',
      body: JSON.stringify({ adgroup_ids: ids }),
    }),
  createAd: (payload) =>
    request('/api/web/direct/ads', { method: 'POST', body: JSON.stringify(payload) }),
  patchAd: (id, payload) =>
    request(`/api/web/direct/ads/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  adActions: (payload) =>
    request('/api/web/direct/ads/actions', { method: 'POST', body: JSON.stringify(payload) }),
  createKeywords: (payload) =>
    request('/api/web/direct/keywords', { method: 'POST', body: JSON.stringify(payload) }),
  patchKeyword: (id, payload) =>
    request(`/api/web/direct/keywords/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  keywordActions: (payload) =>
    request('/api/web/direct/keywords/actions', { method: 'POST', body: JSON.stringify(payload) }),
  searchQueries: (campaignId, params = {}) => {
    const q = new URLSearchParams()
    if (params.limit != null) q.set('limit', params.limit)
    if (params.offset != null) q.set('offset', params.offset)
    if (params.search) q.set('search', params.search)
    if (params.min_clicks != null && params.min_clicks !== '') q.set('min_clicks', params.min_clicks)
    if (params.min_cost_rub != null && params.min_cost_rub !== '') q.set('min_cost_rub', params.min_cost_rub)
    if (params.no_conversions) q.set('no_conversions', 'true')
    const qs = q.toString()
    return request(`/api/web/direct/campaigns/${campaignId}/search-queries${qs ? `?${qs}` : ''}`)
  },
  syncSearchQueries: (campaignId, payload = {}, onProgress) =>
    runJob(`/api/web/direct/campaigns/${campaignId}/search-queries/sync`, payload, onProgress),
  searchQueryAddKeyword: (id, payload = {}) =>
    request(`/api/web/direct/search-queries/${id}/add-keyword`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  searchQueryAddNegative: (id, payload = {}) =>
    request(`/api/web/direct/search-queries/${id}/add-negative`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  searchQueryActions: (payload) =>
    request('/api/web/direct/search-queries/actions', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  actionLogs: () => request('/api/web/actions'),
  analytics: (params = {}) => {
    const q = new URLSearchParams()
    if (params.date_from) q.set('date_from', params.date_from)
    if (params.date_to) q.set('date_to', params.date_to)
    if (params.account_id) q.set('account_id', params.account_id)
    if (params.goal_row_id) q.set('goal_row_id', params.goal_row_id)
    const qs = q.toString()
    return request(`/api/web/analytics${qs ? `?${qs}` : ''}`)
  },
  analyticsSync: (opts = {}, onProgress) => {
    const payload = typeof opts === 'string' ? { day: opts } : opts || {}
    return runJob('/api/web/analytics/sync', payload, onProgress)
  },
  oauthExchange: (code) =>
    request('/api/web/oauth/exchange', { method: 'POST', body: JSON.stringify({ code }) }),
  syncRuns: () => request('/api/web/sync-runs'),
  telegramStatus: () => request('/api/web/telegram'),
  telegramLink: () => request('/api/web/telegram/link', { method: 'POST' }),
  telegramUnlink: () => request('/api/web/telegram/unlink', { method: 'POST' }),
  telegramTest: () => request('/api/web/telegram/test', { method: 'POST' }),
  forgotPassword: (email) =>
    request('/api/web/forgot-password', { method: 'POST', body: JSON.stringify({ email }) }),
  resetPassword: (payload) =>
    request('/api/web/reset-password', { method: 'POST', body: JSON.stringify(payload) }),
  rules: () => request('/api/web/rules'),
  createRule: (payload) => request('/api/web/rules', { method: 'POST', body: JSON.stringify(payload) }),
  patchRule: (id, payload) =>
    request(`/api/web/rules/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteRule: (id) => request(`/api/web/rules/${id}`, { method: 'DELETE' }),
  runRule: (id, dryRun) => {
    const q = dryRun === undefined ? '' : `?dry_run=${dryRun ? 'true' : 'false'}`
    return request(`/api/web/rules/${id}/run${q}`, { method: 'POST' })
  },
  ruleRuns: () => request('/api/web/rules/runs'),
  goals: (counterRowId) =>
    request(`/api/web/metrika/goals${counterRowId ? `?counter_row_id=${counterRowId}` : ''}`),
  syncGoals: (counterRowId, onProgress) =>
    runJob(`/api/web/metrika/${counterRowId}/goals/sync`, undefined, onProgress),
  links: () => request('/api/web/links'),
  createLink: (payload) => request('/api/web/links', { method: 'POST', body: JSON.stringify(payload) }),
  deleteLink: (id) => request(`/api/web/links/${id}`, { method: 'DELETE' }),
  overview: () => request('/api/web/overview'),
  overviewRefresh: (onProgress) => runJob('/api/web/overview/refresh', undefined, onProgress),
  digestSettings: () => request('/api/web/digest'),
  updateDigest: (payload) => request('/api/web/digest', { method: 'PUT', body: JSON.stringify(payload) }),
  digestPreview: () => request('/api/web/digest/preview'),
  digestSend: () => request('/api/web/digest/send', { method: 'POST' }),
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
  state: 'Сессия или ссылка устарела. Попробуйте ещё раз.',
  code: 'Код Яндекса не получен.',
  'yandex-taken': 'Этот Яндекс уже привязан к другому аккаунту.',
  'not-found': 'Не найдено.',
  purpose: 'Некорректный режим подключения.',
  'token-expired': 'Токен Яндекса истёк. Подключите аккаунт заново.',
  metrika: 'Не удалось получить данные Метрики.',
  direct: 'Не удалось обратиться к API Директа.',
  'direct-denied': 'Нет доступа к API Директа. Проверьте заявку и права приложения.',
  disabled: 'Аккаунт выключен.',
  empty: 'Ничего не выбрано.',
  limit: 'Слишком много кампаний за раз (макс. 100).',
  action: 'Неизвестное действие.',
  budget: 'Укажите корректный дневной бюджет.',
  'rule-type': 'Неизвестный тип правила.',
  date: 'Некорректный период.',
  telegram: 'Telegram не подключён или бот не настроен.',
  'client-name': 'Укажите название клиента.',
  'kpi-type': 'KPI — CPA или ДРР.',
  'kpi-target': 'Целевое значение KPI — положительное число.',
  payload: 'Некорректные параметры задачи.',
  'unknown-kind': 'Неизвестный вид задачи.',
  internal: 'Внутренняя ошибка задачи. Попробуйте позже.',
  stale: 'Задача прервалась — воркер перестал отвечать.',
  'job-timeout': 'Задача выполняется слишком долго. Проверьте результат позже.',
  'digest-hour': 'Час отправки — от 0 до 23.',
}

export const JOB_LABELS = {
  sync_connection: 'Синк аккаунта Яндекса',
  sync_account_campaigns: 'Синк кампаний',
  sync_campaign_structure: 'Синк структуры кампании',
  sync_search_queries: 'Синк поисковых запросов',
  sync_goals: 'Синк целей Метрики',
  stats_day: 'Статистика за день',
  stats_range: 'Загрузка статистики',
  run_rules: 'Прогон правил',
  stats_today: 'Расход за сегодня и балансы',
  send_digest: 'Утренний дайджест',
}

export function jobProgressText(job) {
  if (!job) return ''
  if (job.status === 'queued') return job.error_code ? 'повтор после ошибки…' : 'в очереди…'
  const p = job.progress || {}
  if (p.total) return `${p.done}/${p.total}${p.text ? ` · ${p.text}` : ''}`
  return p.text || 'выполняется…'
}

export function errorText(err) {
  if (err instanceof ApiError) {
    if (err.messageDetail) return err.messageDetail
    return ERRORS[err.code] || 'Что-то пошло не так. Попробуйте позже.'
  }
  return 'Что-то пошло не так. Попробуйте позже.'
}

export function formatMoneyKopecks(kopecks) {
  if (kopecks === null || kopecks === undefined) return '—'
  return `${(Number(kopecks) / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`
}
