<template>
  <div class="chat">
    <aside class="chat__side panel">
      <button class="btn btn--accent chat__new" type="button" :disabled="sending" @click="newChat">Новый чат</button>
      <p v-if="chatsError" class="empty">{{ chatsError }}</p>
      <p v-else-if="!chats.length" class="hint">Чатов пока нет.</p>
      <nav v-else class="chat__list" aria-label="Чаты">
        <RouterLink
          v-for="c in chats"
          :key="c.id"
          :to="`/w/${ws.id}/chat/${c.id}`"
          class="chat__item"
          :class="{ active: c.id === chatId }"
        >
          <span class="chat__item-title">{{ c.title }}</span>
          <small>
            <span v-if="c.status === 'running'" class="tone-warn">отвечает…</span>
            <span v-else-if="c.pending_actions" class="ws-tabs__badge">{{ c.pending_actions }}</span>
            <span v-else-if="c.status === 'error'" class="tone-bad">ошибка</span>
            {{ formatDateTime(c.updated_at) }}
          </small>
        </RouterLink>
      </nav>
    </aside>

    <section class="chat__main panel">
      <header class="chat__head">
        <div class="chat__title">
          <h2>{{ chat?.title || 'Новый чат' }}</h2>
          <p class="hint">{{ accessText }}</p>
        </div>
        <div class="row-actions">
          <button
            class="btn"
            type="button"
            :disabled="!canReview"
            title="Что работает, что затухает и почему, что стоит повторить"
            @click="startReview()"
          >
            {{ reviewing ? 'Запускаем…' : 'Разбор' }}
          </button>
          <label class="switch" :title="access.full_access ? 'Выключить полный доступ' : 'Включить полный доступ'">
            <input type="checkbox" :checked="access.full_access" :disabled="accessBusy" @change="toggleFullAccess($event)" />
            <span>Полный доступ</span>
          </label>
          <RouterLink class="btn btn--ghost" :to="{ path: `/w/${ws.id}/settings`, hash: '#ai' }">Права ИИ</RouterLink>
          <template v-if="chat">
            <button class="btn btn--ghost" type="button" @click="renameChat">Переименовать</button>
            <button class="btn btn--ghost btn--danger" type="button" @click="deleteChat">Удалить</button>
          </template>
        </div>
      </header>

      <p v-if="!aiConfigured" class="notice notice--warn">
        ИИ не подключён. Укажите провайдера и ключ в
        <RouterLink :to="{ path: '/cabinet/settings', hash: '#ai' }">настройках аккаунта</RouterLink> — подойдёт
        любой OpenAI-совместимый API или YandexGPT.
      </p>

      <div ref="scroller" class="chat__messages" @scroll="onScroll">
        <p v-if="loadError" class="empty">{{ loadError }}</p>
        <p v-else-if="chatId && !data" class="empty">Загружаем чат…</p>

        <div v-else-if="!items.length" class="chat__intro">
          <h3>Чем помочь с «{{ ws.name }}»?</h3>
          <p class="hint">
            ИИ видит статистику, кампании, ключевые фразы, объявления и поисковые запросы этого пространства. Любое
            изменение в Директе он сначала покажет вам на согласование.
          </p>
          <div class="chat__review">
            <button class="btn btn--accent" type="button" :disabled="!canReview" @click="startReview()">
              {{ reviewing ? 'Запускаем…' : 'Разбор пространства' }}
            </button>
            <span class="hint">
              Что работает, что затухает и почему, что стоит повторить. Сигналы за 30 дней считает код — ИИ получает
              короткую выжимку, а не сырые таблицы.
            </span>
          </div>

          <p v-if="signalsError" class="hint">Сигналы не посчитались: {{ signalsError }}</p>
          <template v-else-if="signals?.suggestions.length">
            <h4 class="chat__subhead">Заметил в данных</h4>
            <div class="chat__suggestions">
              <template v-for="s in signals.suggestions" :key="`${s.kind}${s.account_id || ''}`">
                <RouterLink v-if="!s.prompt" class="quick-action quick-action--warn" to="/cabinet/accounts">
                  <strong>{{ s.title }}</strong>
                  <small>{{ s.detail }}</small>
                </RouterLink>
                <button v-else type="button" class="quick-action" :disabled="!canReview" @click="startReview(s)">
                  <strong>{{ s.title }}</strong>
                  <small>{{ s.detail }}</small>
                  <small v-if="s.campaigns.length" class="quick-action__more">{{ campaignsText(s) }}</small>
                </button>
              </template>
            </div>
          </template>
          <details v-if="signals?.notes.length" class="chat__notes">
            <summary>Заметки о данных: {{ signals.notes.length }}</summary>
            <ul>
              <li v-for="n in signals.notes" :key="n">{{ n }}</li>
            </ul>
          </details>

          <h4 class="chat__subhead">Или спросите сами</h4>
          <div class="chat__suggestions">
            <button v-for="s in SUGGESTIONS" :key="s" type="button" class="quick-action" :disabled="!aiConfigured" @click="send(s)">
              {{ s }}
            </button>
          </div>
        </div>

        <template v-for="item in items" :key="item.key">
          <div v-if="item.role === 'user'" class="msg msg--user">
            <div class="msg__bubble">{{ item.content }}</div>
          </div>

          <div v-else class="msg msg--ai">
            <div v-if="item.content" class="msg__bubble md" v-html="renderMarkdown(item.content)" />

            <div v-if="item.reads.length" class="chat-steps">
              <details v-for="r in item.reads" :key="r.id" class="chat-step">
                <summary>
                  <span class="chat-step__label">{{ r.label }}</span>
                  <span v-if="r.result == null" class="hint">…</span>
                  <span v-else-if="r.failed" class="tone-bad">ошибка</span>
                </summary>
                <pre v-if="hasArgs(r.args)">{{ pretty(r.args) }}</pre>
                <pre v-if="r.result != null">{{ r.result }}</pre>
              </details>
            </div>

            <template v-for="a in item.asks" :key="a.key">
              <AiPlanCard
                v-if="a.tool === 'propose_plan'"
                :action="a"
                :progress="planProgress(a)"
                :disabled="deciding || status === 'running'"
                @submit="respond(() => aiApi.plan(a.id, $event))"
              />
              <AiQuestionsCard
                v-else
                :action="a"
                :disabled="deciding || status === 'running'"
                @submit="respond(() => aiApi.answer(a.id, $event))"
              />
            </template>

            <div v-for="a in item.writes" :key="a.key" class="action-card" :class="`is-${a.status}`">
              <div class="action-card__head">
                <strong>{{ a.label }}</strong>
                <span class="chip" :class="STATUS_CHIPS[a.status]">{{ STATUS_LABELS[a.status] || a.status }}</span>
                <span v-if="a.plan_step" class="chip">шаг {{ a.plan_step }} плана</span>
                <span v-if="a.auto" class="chip">без согласования</span>
              </div>
              <p class="action-card__summary">{{ a.summary }}</p>
              <AiChanges v-if="a.changes?.length" :changes="a.changes" />
              <p v-if="a.reason" class="hint">Зачем: {{ a.reason }}</p>
              <p v-if="a.categories?.length" class="action-card__cats">
                <span v-for="c in a.categories" :key="c.id" class="chip">{{ c.label }}</span>
              </p>
              <details v-if="hasArgs(a.argsShown)" class="action-card__details">
                <summary>Параметры</summary>
                <pre>{{ pretty(a.argsShown) }}</pre>
              </details>
              <p v-if="a.status === 'error' && a.error" class="tone-bad">{{ a.error }}</p>
              <details v-if="a.status === 'done' && a.result != null" class="action-card__details">
                <summary>Результат</summary>
                <pre>{{ pretty(a.result) }}</pre>
              </details>
              <p v-if="a.status === 'rejected' && a.error" class="hint">{{ a.error }}</p>
              <div v-if="a.status === 'pending' && a.id" class="action-card__buttons">
                <button class="btn btn--accent" type="button" :disabled="deciding" @click="decide(a, true)">Выполнить</button>
                <button class="btn btn--ghost" type="button" :disabled="deciding" @click="decide(a, false)">Отклонить</button>
                <label v-if="a.categories?.length && !a.categories.some((c) => c.id === 'destructive')" class="check-row">
                  <input v-model="remember[a.id]" type="checkbox" />
                  <span>Больше не спрашивать для «{{ a.categories.map((c) => c.label).join('», «') }}»</span>
                </label>
              </div>
            </div>
          </div>
        </template>

        <div v-if="status === 'running'" class="msg msg--ai">
          <div class="msg__bubble msg__bubble--thinking"><span class="spinner" /> {{ thinkingText }}</div>
        </div>
        <p v-if="status === 'error' && chat?.error" class="notice notice--warn">{{ chat.error }}</p>
      </div>

      <div v-if="pendingWrites.length > 1" class="chat__pending">
        <span>Ждут решения: {{ pendingWrites.length }}</span>
        <button class="btn btn--accent" type="button" :disabled="deciding" @click="decideAll(true)">Выполнить все</button>
        <button class="btn btn--ghost" type="button" :disabled="deciding" @click="decideAll(false)">Отклонить все</button>
      </div>

      <form class="chat__composer" @submit.prevent="send()">
        <textarea
          ref="input"
          v-model="draft"
          rows="2"
          maxlength="8000"
          :disabled="status === 'running' || !aiConfigured"
          :placeholder="placeholder"
          @keydown.enter.exact.prevent="send()"
        />
        <button class="btn btn--accent" type="submit" :disabled="!canSend">
          {{ sending ? 'Отправляем…' : 'Отправить' }}
        </button>
      </form>
    </section>
  </div>
</template>

<script setup>
import { askConfirm } from '../../composables/useConfirm'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { aiApi, errorText, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime } from '../../utils/format'
import { renderMarkdown } from '../../utils/markdown'
import AiChanges from './AiChanges.vue'
import AiPlanCard from './AiPlanCard.vue'
import AiQuestionsCard from './AiQuestionsCard.vue'
import { useWs } from './context'

const POLL_MS = 1500
const SUGGESTIONS = [
  'Проанализируй кампании за последние 7 дней и дай рекомендации',
  'Где сливается бюджет? Найди кампании и фразы с расходом без конверсий',
  'Почисти поисковые запросы: предложи минус-фразы',
  'Составь план оптимизации на неделю',
]
const STATUS_LABELS = {
  pending: 'ждёт решения',
  approved: 'выполняется',
  done: 'выполнено',
  rejected: 'отклонено',
  error: 'ошибка',
}
const STATUS_CHIPS = { pending: 'chip--warning', done: 'chip--ok', error: 'chip--bad' }
const STATUS_TONES = { pending: 'tone-warn', done: 'tone-ok', error: 'tone-bad' }

const route = useRoute()
const router = useRouter()
const { ws, patch, reload: reloadWs } = useWs()

const chats = ref([])
const chatsError = ref('')
const data = ref(null)
const loadError = ref('')
const aiConfigured = ref(true)
const draft = ref('')
const sending = ref(false)
const reviewing = ref(false)
const signals = ref(null)
const signalsError = ref('')
const deciding = ref(false)
const accessBusy = ref(false)
const remember = reactive({})
const scroller = ref(null)
const input = ref(null)
let pollTimer = null
let stickToBottom = true

const chatId = computed(() => Number(route.params.chatId) || null)
const chat = computed(() => (data.value && data.value.chat.id === chatId.value ? data.value.chat : null))
const status = computed(() => chat.value?.status || 'idle')
const access = computed(() => ws.value.ai_access || { full_access: false, categories: {}, max_change_pct: 30 })
const actions = computed(() => (chat.value ? data.value.actions : []))
const pendingActions = computed(() => actions.value.filter((a) => a.status === 'pending'))
// Вопросы и план закрываются ответом в своей карточке, а не кнопками «Выполнить / Отклонить все».
const pendingWrites = computed(() => pendingActions.value.filter((a) => a.kind !== 'ask'))
const pendingAsks = computed(() => pendingActions.value.filter((a) => a.kind === 'ask'))
const plans = computed(() => actions.value.filter((a) => a.tool === 'propose_plan'))

const accessText = computed(() => {
  const a = access.value
  if (a.full_access) return 'Полный доступ: ИИ выполняет действия без согласования.'
  const allowed = (ws.value.ai_categories || []).filter((c) => a.categories?.[c.key]).map((c) => c.label.toLowerCase())
  if (!allowed.length) return 'Каждое изменение в Директе — только после вашего подтверждения.'
  return `Без согласования (до ${a.max_change_pct}%): ${allowed.join('; ')}. Остальное — после подтверждения.`
})

const placeholder = computed(() => {
  if (!aiConfigured.value) return 'Сначала подключите ИИ в настройках аккаунта'
  if (status.value === 'running') return 'ИИ отвечает…'
  if (pendingAsks.value.length) {
    return pendingWrites.value.length
      ? 'Ответьте в карточке или напишите своими словами; действия, которые ждут решения, будут отклонены'
      : 'Ответьте в карточке или напишите своими словами'
  }
  if (pendingWrites.value.length) return 'Новое сообщение отклонит действия, которые ждут решения'
  return 'Задача для ИИ: проанализировать, найти проблемы, создать, поправить… (Enter — отправить, Shift+Enter — новая строка)'
})
const canSend = computed(
  () => !!draft.value.trim() && !sending.value && status.value !== 'running' && aiConfigured.value,
)
const canReview = computed(
  () => !reviewing.value && !sending.value && status.value !== 'running' && aiConfigured.value,
)

const thinkingText = computed(() => {
  const messages = chat.value ? data.value.messages : []
  const last = messages[messages.length - 1]
  if (last?.role === 'assistant' && last.tool_calls?.length) return `${last.tool_calls[last.tool_calls.length - 1].label}…`
  if (actions.value.some((a) => a.status === 'approved')) return 'Выполняю действия…'
  return 'ИИ думает…'
})

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

/** Ход за ходом: реплики пользователя, ответы ИИ с шагами чтения, вопросами, планом и карточками действий. */
const items = computed(() => {
  if (!chat.value) return []
  const results = new Map()
  for (const m of data.value.messages) if (m.role === 'tool') results.set(m.tool_call_id, m.content)
  const byCall = new Map(data.value.actions.map((a) => [a.tool_call_id, a]))
  const out = []
  for (const m of data.value.messages) {
    if (m.role === 'user') out.push({ key: `m${m.id}`, role: 'user', content: m.content })
    if (m.role !== 'assistant') continue
    const reads = []
    const asks = []
    const writes = []
    for (const call of m.tool_calls || []) {
      const raw = results.get(call.id)
      // Вопросы без карточки (на всё уже ответили или ошибка в аргументах) — обычный шаг с ответом.
      if (call.kind === 'ask' && byCall.has(call.id)) {
        const action = byCall.get(call.id)
        asks.push({ ...action, key: `a${action.id}` })
      } else if (call.write) {
        const action = byCall.get(call.id)
        if (action) {
          const { reason: _reason, plan_step: _step, ...argsShown } = action.args || {}
          writes.push({ ...action, key: `a${action.id}`, argsShown })
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
    if (m.content || reads.length || asks.length || writes.length) {
      out.push({ key: `m${m.id}`, role: 'assistant', content: m.content, reads, asks, writes })
    }
  }
  return out
})

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

function hasArgs(args) {
  return args && typeof args === 'object' && Object.keys(args).length > 0
}

function pretty(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function campaignsText(card) {
  const names = card.campaigns.map((c) => `«${c.name}»`).join(', ')
  return card.campaigns_total > card.campaigns.length ? `${names} и др.` : names
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

// Сигналы нужны только пустому чату: карточки «чем помочь» вместо догадок.
async function loadSignals() {
  signalsError.value = ''
  try {
    signals.value = await aiApi.signals(ws.value.id)
  } catch (err) {
    signalsError.value = errorText(err)
  }
}

async function loadChats() {
  chatsError.value = ''
  try {
    const res = await aiApi.chats(ws.value.id)
    chats.value = res.items || []
  } catch (err) {
    chatsError.value = errorText(err)
  }
}

function onScroll() {
  const el = scroller.value
  if (el) stickToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

async function scrollDown(force = false) {
  await nextTick()
  const el = scroller.value
  if (el && (force || stickToBottom)) el.scrollTop = el.scrollHeight
}

async function loadChat(id, quiet = false) {
  if (!quiet) loadError.value = ''
  const prevStatus = chat.value?.status
  const prevCount = chat.value ? data.value.messages.length + data.value.actions.length : 0
  try {
    const res = await aiApi.chat(id)
    if (chatId.value !== id) return
    data.value = res
    aiConfigured.value = !!res.ai?.configured
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

async function send(text) {
  const message = (text ?? draft.value).trim()
  if (!message || sending.value || status.value === 'running') return
  sending.value = true
  try {
    let id = chatId.value
    if (!id) {
      const created = await aiApi.createChat(ws.value.id)
      id = created.id
      chats.value = [created, ...chats.value]
      await router.replace({ path: `/w/${ws.value.id}/chat/${id}` })
    }
    await aiApi.send(id, message)
    if (text == null || text === draft.value) draft.value = ''
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

/** «Разбор»: в пустом чате — здесь же, иначе в новом. card — карточка сигнала: её запрос и название. */
async function startReview(card = null) {
  if (!canReview.value) return
  reviewing.value = true
  try {
    const payload = card ? { prompt: card.prompt, title: card.title } : {}
    if (chat.value && !items.value.length) payload.chat_id = chat.value.id
    const res = await aiApi.review(ws.value.id, payload)
    const id = res.chat.id
    chats.value = [res.chat, ...chats.value.filter((c) => c.id !== id)]
    stickToBottom = true
    if (id !== chatId.value) {
      const go = chatId.value ? router.push : router.replace
      await go({ path: `/w/${ws.value.id}/chat/${id}` })
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
    await aiApi.decide(action.id, approve, rememberIt)
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
    await aiApi.decideAll(chatId.value, approve)
    await loadChat(chatId.value)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    deciding.value = false
  }
}

async function toggleFullAccess(event) {
  const on = event.target.checked
  if (
    on &&
    !await askConfirm(
      'Включить полный доступ? ИИ сможет менять бюджеты и ставки, создавать, копировать и удалять кампании без подтверждения.',
    )
  ) {
    event.target.checked = false
    return
  }
  accessBusy.value = true
  try {
    const updated = await wsApi.update(ws.value.id, { ai_access: { full_access: on } })
    patch(updated)
    pushToast(on ? 'Полный доступ включён' : 'Полный доступ выключен', on ? 'warn' : 'ok')
  } catch (err) {
    event.target.checked = !on
    pushToast(errorText(err), 'error')
  } finally {
    accessBusy.value = false
  }
}

function newChat() {
  stopPolling()
  data.value = null
  draft.value = ''
  router.push(`/w/${ws.value.id}/chat`)
  nextTick(() => input.value?.focus())
}

async function renameChat() {
  const title = window.prompt('Название чата', chat.value.title)
  if (title == null) return
  try {
    const updated = await aiApi.renameChat(chat.value.id, title)
    data.value = { ...data.value, chat: updated }
    chats.value = chats.value.map((c) => (c.id === updated.id ? updated : c))
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function deleteChat() {
  if (!await askConfirm(`Удалить чат «${chat.value.title}»? Действия, уже выполненные в Директе, останутся в журнале.`)) return
  const id = chat.value.id
  try {
    await aiApi.deleteChat(id)
    chats.value = chats.value.filter((c) => c.id !== id)
    newChat()
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

// --- Запрос из других вкладок (?ask=…) --------------------------------------------------------

function askPrompt(q) {
  switch (q.ask) {
    case 'problems':
      return 'Разбери проблемы пространства из сводки: где сливается бюджет, что с KPI и темпом расхода. Дай конкретные действия по важности.'
    case 'analytics':
      return `Проанализируй аналитику за период ${q.from || ''} — ${q.to || ''}: динамика расхода, конверсий и CPA, что изменилось и почему, какие кампании тянут результат вниз. Дай рекомендации.`
    case 'campaign':
      return `Разбери кампанию${q.name ? ` «${q.name}»` : ''} (campaign_row_id ${q.cid}): настройки и стратегия, структура, ключевые фразы, объявления, поисковые запросы и статистика. Найди проблемы и предложи правки.`
    case 'audit':
      return 'Проведи аудит всех кампаний пространства: структура, стратегии и бюджеты, минус-фразы, объявления, поисковые запросы, расход без конверсий. Дай список проблем по важности и план правок.'
    default:
      return ''
  }
}

async function handleAsk() {
  const prompt = askPrompt(route.query)
  if (!prompt) return
  await router.replace({ path: `/w/${ws.value.id}/chat` })
  try {
    const settings = await aiApi.settings()
    aiConfigured.value = !!settings.configured
  } catch {
    // Узнаем при отправке.
  }
  if (aiConfigured.value) await send(prompt)
  else draft.value = prompt
}

// Пустой открытый чат или новый: тогда и считаем сигналы — один раз за визит.
const showIntro = computed(() => !loadError.value && !items.value.length && (!chatId.value || !!chat.value))
watch(
  showIntro,
  (show) => {
    if (show && !signals.value && !signalsError.value) loadSignals()
  },
  { immediate: true },
)

watch(chatId, (id) => {
  stopPolling()
  loadError.value = ''
  if (id) {
    stickToBottom = true
    loadChat(id)
  }
})

onMounted(async () => {
  loadChats()
  if (route.query.ask) {
    await handleAsk()
    return
  }
  if (chatId.value) loadChat(chatId.value)
  else
    aiApi
      .settings()
      .then((s) => {
        aiConfigured.value = !!s.configured
      })
      .catch(() => {})
})

onBeforeUnmount(stopPolling)
</script>
