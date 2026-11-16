import { ref } from 'vue'
import { adminApi } from '../api/admin'
import { errorText } from '../api/web'
import { pushToast } from './useToasts'

// Сводка админки (/api/admin/me): что подключено, оплаты за месяц, платежи без чека. Одна на все страницы —
// из неё точки состояния и счётчики в меню. Страница, поменявшая настройку, просит перечитать сводку.
const summary = ref(null)
// Настройки моста Cloudflare: от них зависят подсказки в «ИИ и модели» и «Генерации картинок».
const bridge = ref(null)

export async function reloadAdminSummary() {
  try {
    summary.value = await adminApi.me()
  } catch (err) {
    // 401 уже сбросил токен — оболочка покажет вход.
    if (err.status !== 401) pushToast(errorText(err), 'error')
  }
}

export function resetAdminSummary() {
  summary.value = null
  bridge.value = null
}

export async function loadAdminBridge() {
  if (bridge.value) return bridge.value
  try {
    bridge.value = await adminApi.bridge()
  } catch {
    // Без моста подсказки просто не упоминают его.
  }
  return bridge.value
}

export function setAdminBridge(data) {
  bridge.value = data
}

export function useAdminSummary() {
  return { summary, bridge }
}
