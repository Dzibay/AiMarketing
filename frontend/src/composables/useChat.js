import { computed, inject, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { aiApi, errorText } from '../api/web'
import { refreshPending } from './usePending'
import { pushToast } from './useToasts'
import { loadWorkspaces, useWorkspaces } from './useWorkspaces'

const CHAT_KEY = Symbol('chat')
const POLL_MS = 1500
// «Позже»: карточка сворачивается только у вас в браузере, на сервере действие так и ждёт решения.
const LATER_KEY = 'aimarketing-later'

export const STATUS_LABELS = {
  pending: 'ждёт решения',
  approved: 'выполняется',
  done: 'выполнено',
  rejected: 'отклонено',
  error: 'ошибка',
}
const STATUS_TONES = { pending: 'tone-warn', done: 'tone-ok', error: 'tone-bad' }

function readLater() {
  try {
    const ids = JSON.parse(localStorage.getItem(LATER_KEY) || '[]')
    return Array.isArray(ids) ? ids : []
  } catch {
    return []
  }
}

function parseToolResult(content) {
  try {
    const value = JSON.parse(content)
    // Выжимка сигналов — готовый текст: показываем ровно то, что прочитал ИИ.
    if (typeof value?.digest === 'string') return { text: value.digest, failed: false }
    return { text: JSON.stringify(value, null, 2), failed: value && value.ok === false }
  } catch {
    return { text: content, failed: false }
  }
}

function toolError(content) {
  try {
    const value = JSON.parse(content)
    return String(value?.error || content)
  } catch {
    return content
  }
}

/** Строки с галочками карточки действия: «вся группа» — галочка раздела, остальное — строки. */
function pickRows(items) {
  if (!items?.length) return null
  const groups = {}
  const rows = []
  for (const p of items) {
    if (p.kind === 'group') groups[p.section] = p.id
    else rows.push({ id: p.id, section: p.section, kind: p.kind, cells: [p.text, p.note].slice(0, items.some((i) => i.note) ? 2 : 1) })
  }
  return { groups, rows, columns: items.some((i) => i.note) ? ['Строка', 'Что меняется'] : ['Строка'] }
}

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
// @Название в начале строки или после пробела: «me@site.ru» не упоминание.
const hasMention = (message, name) => new RegExp(`(^|\\s)@${escapeRe(name)}`).test(message)

/** Текст сообщения кусками: @Проект — ссылкой-чипом на проект, остальное — как есть. */
export function mentionParts(item) {
  const refs = item.refs || []
  if (!refs.length) return [{ text: item.content }]
  const names = [...new Set(refs.map((r) => r.name))].sort((a, b) => b.length - a.length)
  const re = new RegExp(`(@(?:${names.map(escapeRe).join('|')}))`)
  return item.content
    .split(re)
    .filter(Boolean)
    .map((text) => {
      const ref = text.startsWith('@') ? refs.find((r) => `@${r.name}` === text) : null
      return ref ? { text, ref } : { text }
    })
}

/**
 * Разговор с ИИ на странице чата: загрузка, опрос сервера раз в 1,5 с, отправка и решения по карточкам.
 * Страница (WsChat.vue) создаёт его через provideChat(), её части — ChatList, ChatThread, ChatComposer —
 * берут то же самое через useChat().
 *
 * general — общий чат по всем проектам; ws — ref проекта (у общего чата — заглушка); basePath — адрес раздела
 * без номера чата; reloadWs — перечитать проект после «Больше не спрашивать».
 */
export function provideChat({ general, ws, basePath, reloadWs }) {
  const route = useRoute()
  const router = useRouter()
  const { workspaces } = useWorkspaces()

  const chats = ref([])
  const chatsError = ref('')
  const data = ref(null)
  const loadError = ref('')
  const aiConfigured = ref(true)
  const aiModels = ref(null)
  const draft = ref('')
  const sending = ref(false)
  const reviewing = ref(false)
  const deciding = ref(false)
  const remember = reactive({})
  // {id действия: [id снятых строк]} — что маркетолог убрал из карточки перед «Выполнить».
  const excluded = reactive({})
  // Элементы ленты и поля ввода: их заполняют ChatThread и ChatComposer, здесь — прокрутка к низу и фокус.
  const scroller = ref(null)
  const input = ref(null)
  let pollTimer = null
  let stickToBottom = true

  const chatId = computed(() => Number(route.params.chatId) || null)
  const chat = computed(() => (data.value && data.value.chat.id === chatId.value ? data.value.chat : null))
  const status = computed(() => chat.value?.status || 'idle')
  // Ошибка хода уже стоит в ленте последней строкой (с размером запроса) — плашку с тем же текстом не дублируем.
  const errorInFeed = computed(() => {
    const last = chat.value ? data.value.messages[data.value.messages.length - 1] : null
    return last?.role === 'error' && !!chat.value.error && last.content.startsWith(chat.value.error)
  })
  const lastMessageKey = computed(() => {
    const messages = chat.value ? data.value.messages : []
    return messages.length ? `m${messages[messages.length - 1].id}` : ''
  })
  const retrying = ref(false)
  // «Повторить» — тот же ход заново: ИИ продолжит с места сбоя, без нового сообщения.
  const canRetry = computed(
    () => status.value === 'error' && !retrying.value && !sending.value && !deciding.value && aiConfigured.value,
  )
  const actions = computed(() => (chat.value ? data.value.actions : []))
  const pendingActions = computed(() => actions.value.filter((a) => a.status === 'pending'))
  // Вопросы, план и таблицы закрываются ответом в своей карточке, а не кнопками «Выполнить / Отклонить все».
  const pendingWrites = computed(() => pendingActions.value.filter((a) => a.kind !== 'ask'))

  const later = ref(new Set(readLater()))
  const activeWrites = computed(() => pendingWrites.value.filter((a) => !later.value.has(a.id)))
  const laterCount = computed(() => pendingWrites.value.length - activeWrites.value.length)
  const isLater = (a) => a.status === 'pending' && !!a.id && later.value.has(a.id)
  function setLater(a, on) {
    const next = new Set(later.value)
    if (on) next.add(a.id)
    else next.delete(a.id)
    later.value = next
    try {
      localStorage.setItem(LATER_KEY, JSON.stringify([...next].slice(-200)))
    } catch {
      // хранилище недоступно — свёрнуто до перезагрузки
    }
  }
  const pendingAsks = computed(() => pendingActions.value.filter((a) => a.kind === 'ask'))
  const plans = computed(() => actions.value.filter((a) => a.tool === 'propose_plan'))
  // Сборка новой кампании — доской на пол-экрана рядом с чатом; таблицы этапов решаются на ней.
  const build = computed(() => (chat.value ? data.value.build || null : null))
  // Что подключено у маркетолога: варианты ответа, которым это нужно, без него не выбрать.
  const caps = computed(() => ({ image_gen: !!data.value?.image_gen?.configured }))
  const onBoard = computed(() => new Set(build.value?.action_ids || []))
  const boardWaiting = computed(() => pendingActions.value.filter((a) => onBoard.value.has(a.id)).length)

  // @-упоминания других проектов: ИИ получает их на чтение. Подставленные из списка помним по id,
  // набранные руками находим по названию в тексте при отправке.
  const mentions = ref([])
  const otherProjects = computed(() => workspaces.value.filter((w) => w.id !== ws.value.id && !w.archived && !w.locked))
  loadWorkspaces().catch(() => null)

  // Класс модели: у открытого чата — сохранённый, у нового — выбранный здесь до первого сообщения.
  const pickedClass = ref('')
  const modelBusy = ref(false)
  const modelItems = computed(() => aiModels.value?.items || [])
  const selectedClass = computed(() => {
    const m = aiModels.value
    if (!m) return ''
    if (chat.value) return m.selected || m.default
    const has = (id) => !!id && modelItems.value.some((c) => c.id === id)
    if (has(pickedClass.value)) return pickedClass.value
    // Новый чат проекта сервер создаст на модели из настроек «Помощника» — показываем её же.
    const projectClass = general ? '' : ws.value?.ai_style?.model_class
    return has(projectClass) ? projectClass : m.default
  })

  function fillModels(res) {
    aiModels.value = res || null
    aiConfigured.value = !!res?.configured
  }

  async function chooseModel(id) {
    pickedClass.value = id
    if (!chat.value) return
    modelBusy.value = true
    try {
      const res = await aiApi.setChatModel(chat.value.id, id)
      if (data.value?.chat?.id === res.chat.id) data.value = { ...data.value, chat: { ...data.value.chat, ...res.chat }, ai: res.ai }
      fillModels(res.ai)
    } catch (err) {
      pushToast(errorText(err), 'error')
    } finally {
      modelBusy.value = false
    }
  }

  const canReview = computed(
    () => !reviewing.value && !sending.value && status.value !== 'running' && aiConfigured.value,
  )
  const canSendStarter = computed(() => !sending.value && status.value !== 'running' && aiConfigured.value)

  /** Проект карточки действия в общем чате — чтобы не перепутать, где ИИ что-то меняет. */
  function projectName(action) {
    if (!general) return ''
    const id = action.args?.project_id
    return id == null ? '' : data.value?.projects?.[String(id)] || `проект ${id}`
  }

  const thinkingText = computed(() => {
    const messages = chat.value ? data.value.messages : []
    const last = messages[messages.length - 1]
    if (last?.role === 'assistant' && last.tool_calls?.length) return `${last.tool_calls[last.tool_calls.length - 1].label}…`
    if (actions.value.some((a) => a.status === 'approved')) return 'Выполняю действия…'
    return 'ИИ думает…'
  })

  /** Ход за ходом: реплики пользователя, ответы ИИ с шагами чтения, вопросами, планом и карточками действий. */
  const items = computed(() => {
    if (!chat.value) return []
    const results = new Map()
    for (const m of data.value.messages) if (m.role === 'tool') results.set(m.tool_call_id, m.content)
    const byCall = new Map(data.value.actions.map((a) => [a.tool_call_id, a]))
    // Правки на доске — строкой после сообщения, на котором их сделали.
    const edits = new Map()
    for (const a of data.value.actions) {
      if (a.tool === 'board_edit') edits.set(a.message_id, [...(edits.get(a.message_id) || []), a])
    }
    const out = []
    for (const m of data.value.messages) {
      if (m.role === 'user') {
        out.push({ key: `m${m.id}`, role: 'user', content: m.content, refs: m.refs || [], created: m.created_at || '' })
      }
      if (m.role === 'error') out.push({ key: `m${m.id}`, role: 'error', content: m.content })
      if (m.role === 'assistant') {
        const item = assistantItem(m, results, byCall)
        if (item) out.push(item)
      }
      for (const e of edits.get(m.id) || []) out.push({ key: `e${e.id}`, role: 'note', content: e.summary })
    }
    return out
  })

  /** Ответ ИИ: текст, шаги чтения, вопросы, план, таблицы и карточки действий (решаемое на доске — плашкой). */
  function assistantItem(m, results, byCall) {
    const reads = []
    const asks = []
    const writes = []
    for (const call of m.tool_calls || []) {
      const raw = results.get(call.id)
      // Вопросы без карточки (на всё уже ответили или ошибка в аргументах) — обычный шаг с ответом.
      if (call.kind === 'ask' && byCall.has(call.id)) {
        const action = byCall.get(call.id)
        asks.push({ ...action, key: `a${action.id}`, plaque: plaqueText(action) })
      } else if (call.write) {
        const action = byCall.get(call.id)
        if (action && onBoard.value.has(action.id)) {
          writes.push({ ...action, key: `a${action.id}`, plaque: plaqueText(action) })
        } else if (action) {
          const { reason: _reason, plan_step: _step, ...argsShown } = action.args || {}
          writes.push({ ...action, key: `a${action.id}`, argsShown, picks: pickRows(action.items) })
        } else {
          // Подготовка не прошла (ошибка в аргументах) — ответ инструмента вместо карточки.
          writes.push({
            key: `c${call.id}`,
            label: call.label,
            status: raw != null ? 'error' : 'approved',
            summary: 'Действие не создано',
            error: raw != null ? toolError(raw) : '',
            argsShown: call.args,
          })
        }
      } else {
        const parsed = raw != null ? parseToolResult(raw) : null
        reads.push({ id: call.id, label: call.label, args: call.args, result: parsed?.text ?? null, failed: parsed?.failed })
      }
    }
    if (!m.content && !reads.length && !asks.length && !writes.length) return null
    return {
      key: `m${m.id}`,
      role: 'assistant',
      content: m.content,
      model: m.model || '',
      created: m.created_at || '',
      reads,
      asks,
      writes,
    }
  }

  /** Плашка вместо таблицы этапа или карточки создания, которые решаются на доске; '' — карточка в чате. */
  function plaqueText(action) {
    if (!onBoard.value.has(action.id)) return ''
    if (action.tool === 'create_campaign') {
      const name = action.args?.spec?.name ? ` «${action.args.spec.name}»` : ''
      const texts = {
        pending: `Черновик${name} на доске ждёт решения`,
        approved: `Черновик${name} создаётся…`,
        done: `Черновик${name} создан в Директе`,
        error: `Черновик${name} не создан: ${action.error || 'ошибка'}`,
        rejected: `Создание черновика${name} отклонено`,
      }
      return texts[action.status] || action.status
    }
    const stage = action.args?.stage
    const label = build.value.stages.find((s) => s.key === stage)?.label || stage
    if (action.status === 'pending') return `Этап «${label}» на доске ждёт решения`
    if (action.status === 'done') {
      const chosen = (action.result?.approved_rows || []).length
      return `Этап «${label}» согласован на доске: ${chosen} из ${(action.args?.rows || []).length}`
    }
    return `Этап «${label}» отклонён`
  }

  function offRows(action) {
    const off = new Set(excluded[action.id] || [])
    if (!action.picks || !off.size) return []
    return action.picks.rows.filter((r) => off.has(r.id) || (action.picks.groups[r.section] != null && off.has(action.picks.groups[r.section])))
  }

  function offCount(action) {
    return offRows(action).length
  }

  function allOff(action) {
    return !!action.picks && offCount(action) === action.picks.rows.length
  }

  /** id снятых строк ждущих действий — для «Выполнить» и «Выполнить все». */
  function excludeFor(action) {
    return action.picks ? (excluded[action.id] || []) : []
  }

  /** Действия ИИ по шагам плана: с номером шага, после этого плана и до следующего. */
  function planProgress(plan) {
    const next = plans.value.find((p) => p.id > plan.id)
    const out = {}
    for (const a of actions.value) {
      if (a.kind !== 'write' || a.plan_step == null || a.id < plan.id || (next && a.id > next.id)) continue
      ;(out[a.plan_step] ||= []).push({
        id: a.id,
        label: a.label,
        status: STATUS_LABELS[a.status] || a.status,
        tone: STATUS_TONES[a.status] || '',
      })
    }
    return out
  }

  /** Правки на доске: сервер возвращает чат целиком; false — правка не прошла (тост с причиной). */
  async function editBoard(ops) {
    const id = chatId.value
    try {
      const res = await aiApi.board(id, ops)
      if (chatId.value === id) data.value = res
      return true
    } catch (err) {
      pushToast(errorText(err), 'error')
      if (chatId.value === id) loadChat(id, true)
      return false
    }
  }

  // --- Загрузка и опрос -------------------------------------------------------------------------

  function stopPolling() {
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = null
  }

  function schedulePoll() {
    stopPolling()
    const needs = status.value === 'running' || actions.value.some((a) => a.status === 'approved')
    if (needs && chatId.value) pollTimer = setTimeout(() => loadChat(chatId.value, true), POLL_MS)
  }

  async function loadChats() {
    chatsError.value = ''
    try {
      const res = general ? await aiApi.generalChats() : await aiApi.chats(ws.value.id)
      chats.value = res.items || []
    } catch (err) {
      chatsError.value = errorText(err)
    }
  }

  /** Что прокручивается: лента сама — рядом с доской, иначе вся страница (поле ввода прилипает к низу окна). */
  function scrollBox() {
    const el = scroller.value
    if (el && getComputedStyle(el).overflowY !== 'visible') return el
    return document.scrollingElement
  }

  function onScroll() {
    const box = scrollBox()
    if (box) stickToBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120
  }

  async function scrollDown(force = false) {
    await nextTick()
    const box = scrollBox()
    if (box && (force || stickToBottom)) box.scrollTop = box.scrollHeight
  }

  async function loadChat(id, quiet = false) {
    if (!quiet) loadError.value = ''
    const prevStatus = chat.value?.status
    const prevCount = chat.value ? data.value.messages.length + data.value.actions.length : 0
    try {
      const res = await aiApi.chat(id)
      if (chatId.value !== id) return
      data.value = res
      fillModels(res.ai)
      const count = res.messages.length + res.actions.length
      if (!quiet || count !== prevCount) scrollDown(!quiet)
      if (prevStatus && prevStatus !== res.chat.status) loadChats()
      else if (!quiet) {
        const inList = chats.value.find((c) => c.id === id)
        if (inList) Object.assign(inList, res.chat)
      }
    } catch (err) {
      if (chatId.value !== id) return
      if (!quiet) {
        data.value = null
        loadError.value = errorText(err)
      }
    } finally {
      if (chatId.value === id) schedulePoll()
    }
  }

  // --- Действия пользователя --------------------------------------------------------------------

  /** Проекты, на которые ссылается текст сообщения. */
  function usedMentions(message) {
    const found = new Map()
    for (const m of mentions.value) if (hasMention(message, m.name)) found.set(m.id, m)
    const covered = new Set([...found.values()].map((m) => m.name))
    for (const w of otherProjects.value) {
      if (!covered.has(w.name) && hasMention(message, w.name)) found.set(w.id, { id: w.id, name: w.name })
    }
    return [...found.values()]
  }

  /** Без text — отправить черновик из поля ввода; с text — готовый запрос (подсказка, вопрос из другого раздела). */
  async function send(text) {
    const message = (text ?? draft.value).trim()
    if (!message || sending.value || status.value === 'running') return
    sending.value = true
    try {
      let id = chatId.value
      if (!id) {
        const created = general
          ? await aiApi.createGeneralChat('', pickedClass.value)
          : await aiApi.createChat(ws.value.id, '', pickedClass.value)
        id = created.id
        chats.value = [created, ...chats.value]
        await router.replace({ path: `${basePath.value}/${id}` })
      }
      // Подсказки-стартеры — готовый текст без упоминаний.
      await aiApi.send(id, message, text == null ? usedMentions(message).map((m) => m.id) : [])
      if (text == null || text === draft.value) {
        draft.value = ''
        mentions.value = []
      }
      stickToBottom = true
      await loadChat(id)
      loadChats()
    } catch (err) {
      if (err?.code === 'ai-not-configured') aiConfigured.value = false
      pushToast(errorText(err), 'error')
    } finally {
      sending.value = false
      nextTick(() => input.value?.focus())
    }
  }

  async function retryTurn() {
    const id = chatId.value
    if (!id || !canRetry.value) return
    retrying.value = true
    try {
      await aiApi.retry(id)
      stickToBottom = true
      await loadChat(id)
      loadChats()
    } catch (err) {
      if (err?.code === 'ai-not-configured') aiConfigured.value = false
      pushToast(errorText(err), 'error')
      loadChat(id, true)
    } finally {
      retrying.value = false
    }
  }

  /**
   * «Разбор»: в пустом чате — здесь же, иначе в новом. card — карточка сигнала (её запрос и название)
   * или эксперимента, по которому пора подвести итоги.
   */
  async function startReview(card = null) {
    if (!canReview.value) return
    reviewing.value = true
    try {
      const payload = card && !card.experiment_id ? { prompt: card.prompt, title: card.title } : {}
      if (chat.value && !items.value.length) payload.chat_id = chat.value.id
      const res = card?.experiment_id
        ? await aiApi.reviewExperiment(ws.value.id, card.experiment_id, payload)
        : await aiApi.review(ws.value.id, payload)
      const id = res.chat.id
      chats.value = [res.chat, ...chats.value.filter((c) => c.id !== id)]
      stickToBottom = true
      if (id !== chatId.value) {
        const go = chatId.value ? router.push : router.replace
        await go({ path: `${basePath.value}/${id}` })
      }
      await loadChat(id)
    } catch (err) {
      if (err?.code === 'ai-not-configured') aiConfigured.value = false
      pushToast(errorText(err), 'error')
    } finally {
      reviewing.value = false
    }
  }

  async function decide(action, approve) {
    deciding.value = true
    const rememberIt = approve && !!remember[action.id]
    try {
      await aiApi.decide(action.id, approve, rememberIt, approve ? excludeFor(action) : [])
      if (rememberIt) reloadWs()
      await loadChat(chatId.value)
    } catch (err) {
      pushToast(errorText(err), 'error')
      loadChat(chatId.value, true)
    } finally {
      deciding.value = false
    }
  }

  /** Ответ в карточке вопросов или плана; когда решать больше нечего, ИИ продолжает. */
  async function respond(request) {
    deciding.value = true
    try {
      await request()
      stickToBottom = true
      await loadChat(chatId.value)
    } catch (err) {
      pushToast(errorText(err), 'error')
      loadChat(chatId.value, true)
    } finally {
      deciding.value = false
    }
  }

  async function decideAll(approve) {
    if (!chatId.value) return
    deciding.value = true
    try {
      const exclude = {}
      if (approve) {
        for (const item of items.value) {
          for (const a of item.writes || []) if (a.status === 'pending' && excludeFor(a).length) exclude[a.id] = excludeFor(a)
        }
      }
      // Отложенные «Позже» не трогаем: решаем по одному только те, что на виду.
      if (laterCount.value) {
        for (const a of activeWrites.value) await aiApi.decide(a.id, approve, false, approve ? exclude[a.id] || [] : [])
      } else {
        await aiApi.decideAll(chatId.value, approve, exclude)
      }
      await loadChat(chatId.value)
    } catch (err) {
      pushToast(errorText(err), 'error')
    } finally {
      deciding.value = false
    }
  }

  /** Новый чат: пустой экран раздела, сам чат появится на сервере с первым сообщением. */
  function newChat() {
    stopPolling()
    data.value = null
    draft.value = ''
    router.push(basePath.value)
    nextTick(() => input.value?.focus())
  }

  /** Новое название открытого чата — в нём самом и в списке. Ошибку показывает тот, кто вызвал. */
  async function renameChat(title) {
    const updated = await aiApi.renameChat(chat.value.id, title)
    data.value = { ...data.value, chat: updated }
    chats.value = chats.value.map((c) => (c.id === updated.id ? updated : c))
  }

  /** Удалить открытый чат на сервере и из списка. Ошибку показывает тот, кто вызвал. */
  async function deleteChat() {
    const id = chat.value.id
    await aiApi.deleteChat(id)
    chats.value = chats.value.filter((c) => c.id !== id)
  }

  // Пустой открытый чат или новый: вместо ленты — приветствие и подсказки.
  const showIntro = computed(() => !loadError.value && !items.value.length && (!chatId.value || !!chat.value))

  // Счётчик «Ждёт решения» в шапке: в чате появились карточки без решения или решение принято.
  watch(
    () => (data.value?.actions || []).filter((a) => a.status === 'pending').length,
    () => refreshPending(),
  )

  watch(chatId, (id) => {
    stopPolling()
    loadError.value = ''
    if (id) {
      stickToBottom = true
      loadChat(id)
    }
  })

  onMounted(() => window.addEventListener('scroll', onScroll, { passive: true }))
  onBeforeUnmount(() => {
    stopPolling()
    window.removeEventListener('scroll', onScroll)
  })

  const ctx = {
    general,
    ws,
    basePath,
    // Список чатов и открытый чат
    chats,
    chatsError,
    chatId,
    chat,
    status,
    loadError,
    items,
    showIntro,
    thinkingText,
    errorInFeed,
    lastMessageKey,
    // Предложения ИИ и решения по ним
    actions,
    pendingActions,
    pendingAsks,
    pendingWrites,
    activeWrites,
    laterCount,
    build,
    caps,
    onBoard,
    boardWaiting,
    remember,
    excluded,
    deciding,
    isLater,
    setLater,
    projectName,
    offCount,
    allOff,
    planProgress,
    decide,
    decideAll,
    respond,
    editBoard,
    // Сообщение и модель ответа
    draft,
    mentions,
    otherProjects,
    aiConfigured,
    modelItems,
    selectedClass,
    modelBusy,
    sending,
    reviewing,
    canReview,
    canSendStarter,
    canRetry,
    fillModels,
    chooseModel,
    send,
    retryTurn,
    startReview,
    // Загрузка, прокрутка ленты и действия с чатом
    scroller,
    input,
    onScroll,
    loadChats,
    loadChat,
    newChat,
    renameChat,
    deleteChat,
  }
  provide(CHAT_KEY, ctx)
  return ctx
}

export function useChat() {
  const ctx = inject(CHAT_KEY, null)
  if (!ctx) throw new Error('useChat() вне страницы чата')
  return ctx
}
