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

/** ?a=1&b=2 из объекта; пустые значения пропускаются. */
function query(params = {}) {
  const q = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '' || value === false) continue
    q.set(key, value === true ? 'true' : String(value))
  }
  const qs = q.toString()
  return qs ? `?${qs}` : ''
}

const post = (path, body) =>
  request(path, { method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
const patch = (path, body) => request(path, { method: 'PATCH', body: JSON.stringify(body) })
const put = (path, body) => request(path, { method: 'PUT', body: JSON.stringify(body) })
const del = (path) => request(path, { method: 'DELETE' })

const WS = (id) => `/api/web/workspaces/${id}`

/** Рабочие пространства: пространство = счётчик Метрики + кампании из любых кабинетов Директа. */
export const wsApi = {
  list: (archived = false) => request(`/api/web/workspaces${query({ archived })}`),
  refresh: () => post('/api/web/workspaces/refresh'),
  get: (id) => request(WS(id)),
  load: (id) => request(`${WS(id)}/load`),
  update: (id, payload) => patch(WS(id), payload),
  summary: (id) => request(`${WS(id)}/summary`),
  analytics: (id, params = {}) => request(`${WS(id)}/analytics${query(params)}`),
  syncStats: (id, days, onProgress) => runJob(`${WS(id)}/analytics/sync`, { days }, onProgress),
  accounts: (id) => request(`${WS(id)}/accounts`),
  accountCandidates: (id) => request(`${WS(id)}/accounts/candidates`),
  addAccount: (id, accountId, campaigns = 'matching') =>
    post(`${WS(id)}/accounts`, { account_id: accountId, campaigns }),
  removeAccount: (id, accountId) => del(`${WS(id)}/accounts/${accountId}`),
  campaignCandidates: (id) => request(`${WS(id)}/campaigns/candidates`),
  setCampaigns: (id, add = [], remove = []) => post(`${WS(id)}/campaigns/set`, { add, remove }),
  syncCampaigns: async (id, onProgress) => {
    const { jobs } = await post(`${WS(id)}/campaigns/sync`)
    const results = []
    for (const job of jobs || []) results.push(await waitJob(job, onProgress))
    return results
  },
  campaigns: (id, params = {}) => request(`${WS(id)}/campaigns${query(params)}`),
  campaignOptions: (id) => request(`${WS(id)}/campaign-options`),
  createCampaign: (id, accountId, spec) => post(`${WS(id)}/campaigns`, { account_id: accountId, spec }),
  bulk: (id, payload) => post(`${WS(id)}/campaigns/actions`, payload),
  campaign: (id, cid) => request(`${WS(id)}/campaigns/${cid}`),
  campaignSettings: (id, cid) => request(`${WS(id)}/campaigns/${cid}/settings`),
  updateCampaignSettings: (id, cid, changes) => patch(`${WS(id)}/campaigns/${cid}/settings`, { changes }),
  duplicate: (id, cid, payload, onProgress) => runJob(`${WS(id)}/campaigns/${cid}/duplicate`, payload, onProgress),
  moderate: (id, cid) => post(`${WS(id)}/campaigns/${cid}/moderate`),
  actions: (id, params = {}) => request(`${WS(id)}/actions${query(params)}`),
  rules: (id) => request(`${WS(id)}/rules`),
  // Чистка поисковых запросов: очередь, разметка ИИ, решения и память.
  queries: (id, params = {}) => request(`${WS(id)}/queries${query(params)}`),
  queriesSync: (id, days, onProgress) => runJob(`${WS(id)}/queries/sync`, { days }, onProgress),
  queriesClassify: (id, payload = {}, onProgress) => runJob(`${WS(id)}/queries/classify`, payload, onProgress),
  /** Без вызова ИИ: сколько закроют правила, сколько уйдёт в модель, токены, ₽ и остаток лимита. */
  queriesClassifyEstimate: (id, payload = {}) => post(`${WS(id)}/queries/classify/estimate`, payload),
  /** Только «целевые» без новых ключей сохраняются сразу, остальное — задачей (вызовы Директа). */
  queriesApply: async (id, payload, onProgress) => {
    const res = await post(`${WS(id)}/queries/apply`, payload)
    return res.job ? waitJob(res.job, onProgress) : res.result
  },
  queriesCheck: (id, items) => post(`${WS(id)}/queries/check`, { items }),
  queriesMemory: (id, params = {}) => request(`${WS(id)}/queries/memory${query(params)}`),
  queriesForget: (id, queryNorms) => post(`${WS(id)}/queries/forget`, { query_norms: queryNorms }),
  briefDraft: (id, site, onProgress) => runJob(`${WS(id)}/brief/draft`, { site }, onProgress),
}

/** Откат действия из журнала: предпросмотр шагов и запуск задачей. */
export const undoApi = {
  preview: (logId) => request(`/api/web/actions/${logId}/undo`),
  run: (logId, onProgress) => runJob(`/api/web/actions/${logId}/undo`, undefined, onProgress),
}

/** ИИ-ассистент: свой ключ пользователя (BYOK), чаты пространства, согласование действий. */
export const aiApi = {
  settings: () => request('/api/web/ai/settings'),
  saveSettings: (payload) => put('/api/web/ai/settings', payload),
  test: () => post('/api/web/ai/settings/test'),
  /** Расход за месяц (ГГГГ-ММ, по умолчанию текущий): итог, лимит, по задачам, пространствам и моделям. */
  usage: (month = '') => request(`/api/web/ai/usage${query({ month })}`),
  chats: (workspaceId) => request(`${WS(workspaceId)}/chats`),
  createChat: (workspaceId, title = '') => post(`${WS(workspaceId)}/chats`, { title }),
  chat: (id) => request(`/api/web/ai/chats/${id}`),
  renameChat: (id, title) => patch(`/api/web/ai/chats/${id}`, { title }),
  deleteChat: (id) => del(`/api/web/ai/chats/${id}`),
  send: (id, text) => post(`/api/web/ai/chats/${id}/messages`, { text }),
  decide: (actionId, approve, remember = false) =>
    post(`/api/web/ai/actions/${actionId}/decide`, { approve, remember }),
  decideAll: (chatId, approve) => post(`/api/web/ai/chats/${chatId}/decide-all`, { approve }),
}

export const webApi = {
  config: () => request('/api/web/config'),
  register: (payload) => request('/api/web/register', { method: 'POST', body: JSON.stringify(payload) }),
  login: (payload) => request('/api/web/login', { method: 'POST', body: JSON.stringify(payload) }),
  me: () => request('/api/web/me'),
  yandexStart: (purpose = 'login', mode = '') =>
    request(
      `/api/web/yandex/start?purpose=${encodeURIComponent(purpose)}${mode ? `&mode=${encodeURIComponent(mode)}` : ''}`,
    ),
  syncConnection: (id, onProgress) => runJob(`/api/web/connections/${id}/sync`, undefined, onProgress),
  patchConnection: (id, payload) =>
    request(`/api/web/connections/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteConnection: (id) => request(`/api/web/connections/${id}`, { method: 'DELETE' }),
  patchMetrika: (id, enabled) =>
    request(`/api/web/metrika/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  patchDirect: (id, enabled) =>
    request(`/api/web/direct/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
  // Загрузки кабинетов и счётчиков идут минутами: ставим задачу и не ждём — прогресс в шапке и на карточке.
  refreshAccount: (accountId) => post(`/api/web/direct/${accountId}/sync`),
  reloadAccount: (accountId) => post(`/api/web/direct/${accountId}/load`),
  loadCounter: (counterRowId) => post(`/api/web/metrika/${counterRowId}/load`),
  loads: () => request('/api/web/loads'),
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
  actionLogs: (params = {}) => request(`/api/web/actions${query(params)}`),
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
  overview: () => request('/api/web/overview'),
  overviewRefresh: (onProgress) => runJob('/api/web/overview/refresh', undefined, onProgress),
  digestSettings: () => request('/api/web/digest'),
  updateDigest: (payload) => request('/api/web/digest', { method: 'PUT', body: JSON.stringify(payload) }),
  digestPreview: () => request('/api/web/digest/preview'),
  digestSend: () => request('/api/web/digest/send', { method: 'POST' }),
  negativeLists: () => request('/api/web/negative-lists'),
  negativeList: (id) => request(`/api/web/negative-lists/${id}`),
  createNegativeList: (payload) =>
    request('/api/web/negative-lists', { method: 'POST', body: JSON.stringify(payload) }),
  updateNegativeList: (id, payload) =>
    request(`/api/web/negative-lists/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteNegativeList: (id) => request(`/api/web/negative-lists/${id}`, { method: 'DELETE' }),
  negativeTargets: () => request('/api/web/negative-lists/targets'),
  negativePreview: (id, campaignRowIds) =>
    request(`/api/web/negative-lists/${id}/preview`, {
      method: 'POST',
      body: JSON.stringify({ campaign_row_ids: campaignRowIds }),
    }),
  negativeApply: (id, campaignRowIds, onProgress) =>
    runJob(`/api/web/negative-lists/${id}/apply`, { campaign_row_ids: campaignRowIds }, onProgress),
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
  phrase: 'Проверьте минус-фразы: до 7 слов, слово до 35 символов.',
  'negatives-limit': 'Минус-фраз больше 20 000 символов — это лимит Директа на кампанию.',
  'in-use': 'Список применён к кампаниям. Сначала снимите его со всех кампаний.',
  busy: 'Идёт другое применение списка — задача повторится автоматически.',
  archived: 'Кампания в архиве.',
  unsupported: 'Медийные кампании не поддерживают минус-фразы.',
  mode: 'Некорректный режим подключения Яндекса.',
  scope: 'Это действие не входит в рабочее пространство.',
  decided: 'Решение по действию уже принято.',
  'ai-not-configured': 'Подключите ИИ: Настройки → ИИ-ассистент.',
  'ai-url': 'Адрес API ИИ должен быть публичным https-адресом.',
  'ai-provider': 'Неизвестный провайдер ИИ.',
  'ai-folder': 'Для YandexGPT укажите каталог Yandex Cloud.',
  'ai-auth': 'Провайдер ИИ отклонил ключ.',
  'ai-limit': 'Превышен лимит запросов к ИИ.',
  'ai-timeout': 'ИИ не ответил вовремя.',
  'ai-network': 'Не удалось связаться с ИИ.',
  'ai-error': 'Ошибка ИИ.',
  'ai-busy': 'ИИ ещё отвечает на прошлое сообщение.',
  'ai-json': 'ИИ ответил не по формату. Попробуйте ещё раз.',
  site: 'Не удалось прочитать сайт.',
  undo: 'Это действие нельзя откатить.',
  units: 'Закончились баллы API Директа — загрузка продолжится автоматически, когда они восстановятся.',
  stopped: 'Загрузка остановлена: кабинет выключен.',
}

export const JOB_LABELS = {
  sync_connection: 'Синк аккаунта Яндекса',
  sync_account_campaigns: 'Синк кампаний',
  load_account: 'Загрузка кабинета Директа',
  load_counter: 'Загрузка счётчика Метрики',
  sync_campaign_structure: 'Синк структуры кампании',
  sync_search_queries: 'Синк поисковых запросов',
  sync_goals: 'Синк целей Метрики',
  stats_day: 'Статистика за день',
  stats_range: 'Загрузка статистики',
  run_rules: 'Прогон правил',
  stats_today: 'Расход за сегодня и балансы',
  send_digest: 'Утренний дайджест',
  apply_negative_list: 'Применение списка минус-фраз',
  ai_chat: 'Ответ ИИ-ассистента',
  duplicate_campaign: 'Копирование кампании',
  sync_workspace_queries: 'Синк поисковых запросов пространства',
  classify_queries: 'Разметка запросов ИИ',
  apply_query_decisions: 'Применение решений по запросам',
  brief_draft: 'Черновик брифа по сайту',
  undo_action: 'Откат действия',
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

const ERROR_SOURCES = { metrika: 'Метрика', direct: 'Директ' }

/**
 * Код ошибки из БД или составная строка синка «metrika:код;direct:код|ответ Директа» → текст для человека.
 * Есть ответ Директа — показываем его: в нём причина точнее нашего общего текста.
 */
export function errorDetail(text) {
  if (!text) return ''
  if (ERRORS[text]) return ERRORS[text]
  return String(text)
    .split(';')
    .map((part) => {
      const piece = part.trim()
      const m = /^(metrika|direct):([^|]+)(?:\|(.+))?$/.exec(piece)
      if (!m) return ERRORS[piece] || piece
      return `${ERROR_SOURCES[m[1]]}: ${(m[3] || ERRORS[m[2]] || m[2]).replace(/\.$/, '')}`
    })
    .join('; ')
}

export function formatMoneyKopecks(kopecks) {
  if (kopecks === null || kopecks === undefined) return '—'
  return `${(Number(kopecks) / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`
}
