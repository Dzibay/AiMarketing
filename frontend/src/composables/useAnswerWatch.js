import { ref, watch } from 'vue'
import { aiApi } from '../api/web'
import { pushToast } from './useToasts'

/**
 * Ответ ИИ, который ждут вне его чата. Ушли со страницы, пока ИИ думал, — чат попадает в «ожидаемые»; раз в
 * несколько секунд сверяем статус по списку чатов раздела. Готов — всплывающее сообщение со ссылкой и число
 * в заголовке вкладки «(1) Толк», пока чат не откроют. Ответ, пришедший в скрытую вкладку на странице самого
 * чата, тоже считается непрочитанным — до возврата на вкладку.
 * Сервер к тому же пишет готовый ответ в колокольчик (уведомления); открытый чат отмечает свою запись прочитанной.
 */

const POLL_MS = 5000

// id чата → { id, workspace_id, title, link }
const awaited = new Map()
const unread = ref(new Map())
let timer = 0
let router = null
let openChatId = null

function setRouter(r) {
  router = r
}

/** Чат открыт на экране: его ответ не «непрочитанный», и ждать его в фоне не нужно. */
function chatOpened(id) {
  openChatId = id || null
  if (!id) return
  awaited.delete(id)
  if (unread.value.has(id) && !document.hidden) dropUnread(id)
}

/** Ушли из чата, пока ИИ думал. */
function awaitAnswer(chat, link) {
  if (!chat?.id) return
  awaited.set(chat.id, { id: chat.id, workspace_id: chat.workspace_id ?? null, title: chat.title, link })
  schedule()
}

/** Ответ пришёл, пока вкладка скрыта, — на странице самого чата. */
function answeredHidden(chat, link) {
  if (!document.hidden || !chat?.id) return
  addUnread({ id: chat.id, title: chat.title, link })
}

function addUnread(item) {
  const next = new Map(unread.value)
  next.set(item.id, item)
  unread.value = next
}

function dropUnread(id) {
  const next = new Map(unread.value)
  next.delete(id)
  unread.value = next
}

function schedule() {
  clearTimeout(timer)
  if (awaited.size) timer = setTimeout(poll, POLL_MS)
}

async function poll() {
  const scopes = new Set([...awaited.values()].map((c) => c.workspace_id))
  for (const ws of scopes) {
    let items
    try {
      items = (ws === null ? await aiApi.generalChats() : await aiApi.chats(ws)).items || []
    } catch {
      continue // сеть или сессия — попробуем в следующий раз
    }
    const byId = new Map(items.map((c) => [c.id, c]))
    for (const wait of [...awaited.values()].filter((c) => c.workspace_id === ws)) {
      const chat = byId.get(wait.id)
      if (!chat) {
        awaited.delete(wait.id) // чат удалили
        continue
      }
      if (chat.status === 'running') continue
      awaited.delete(wait.id)
      if (wait.id === openChatId) continue
      answered({ ...wait, title: chat.title }, chat.status)
    }
  }
  schedule()
}

function answered(item, status) {
  addUnread(item)
  const failed = status === 'error'
  pushToast(failed ? `Ответ в чате «${item.title}» прервался` : `ИИ ответил в чате «${item.title}»`, failed ? 'error' : 'info', {
    action: { label: 'Открыть', run: () => router?.push(item.link) },
  })
}

// --- Заголовок вкладки: «(n) …», пока есть непрочитанные ответы. Заголовок ставят разные страницы — следим за ним.
const PREFIX = /^\(\d+\) /

function applyTitle() {
  const n = unread.value.size
  const base = document.title.replace(PREFIX, '')
  const want = n ? `(${n}) ${base}` : base
  if (document.title !== want) document.title = want
}

if (typeof document !== 'undefined') {
  watch(unread, applyTitle)
  const observe = () => {
    const el = document.querySelector('title')
    if (el) new MutationObserver(applyTitle).observe(el, { childList: true, characterData: true, subtree: true })
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', observe)
  else observe()
  // Вернулись на вкладку с открытым чатом — его ответ прочитан.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && openChatId && unread.value.has(openChatId)) dropUnread(openChatId)
  })
}

export function useAnswerWatch() {
  return { setRouter, chatOpened, awaitAnswer, answeredHidden, unread }
}
