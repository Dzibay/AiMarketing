/**
 * Проверка сайта с лендинга: её id приходит в ссылке ?check=… (или лендинг кладёт его в тот же ключ, если
 * сайт и кабинет на одном домене). Когда пользователь вошёл и кабинет ему открыт, проверка один раз
 * переезжает в чат ИИ-помощника с полным результатом — и id больше не нужен.
 */
import { aiApi } from '../api/web'

const KEY = 'aimarketing-check'
const TTL_MS = 7 * 24 * 60 * 60 * 1000
const ID_RE = /^[A-Za-z0-9_-]{8,40}$/

function read() {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || 'null')
    if (value && ID_RE.test(value.id) && Date.now() - value.at < TTL_MS) return value
  } catch {
    // повреждённое значение — как будто его нет
  }
  return null
}

/** id из адреса: свежая проверка заменяет прежнюю. */
export function captureCheck(raw) {
  const id = String(raw || '').trim()
  if (ID_RE.test(id)) localStorage.setItem(KEY, JSON.stringify({ id, at: Date.now() }))
}

export function clearCheck() {
  localStorage.removeItem(KEY)
}

/**
 * Забрать проверку в кабинет → адрес чата с результатом или null.
 * Проверка ещё идёт — id остаётся до следующего захода; не нашлась или не удалась — забываем.
 */
export async function claimCheck() {
  const pending = read()
  if (!pending) return null
  try {
    const data = await aiApi.claimSiteCheck(pending.id)
    if (data.status !== 'done') return null
    clearCheck()
    return data.workspace_id ? `/w/${data.workspace_id}/chat/${data.chat_id}` : `/cabinet/chat/${data.chat_id}`
  } catch (err) {
    if (err?.status !== 401 && err?.status !== 402 && err?.status !== 403) clearCheck()
    return null
  }
}

export function hasCheck() {
  return read() !== null
}
