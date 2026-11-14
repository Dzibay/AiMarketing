import { ref } from 'vue'
import { aiApi } from '../api/web'

// «Ждёт решения»: неподтверждённые предложения ИИ по всем проектам — счётчик в шапке и общая страница.
// Отложенные «Позже» хранятся на сервере (ai_actions.snoozed_at) и в счётчик не входят.
const pendingCount = ref(0)
let pending = null

// Раньше «Позже» помнил только браузер — номера из него один раз переносим на сервер.
const OLD_LATER_KEY = 'aimarketing-later'

async function moveOldLater() {
  let ids
  try {
    ids = JSON.parse(localStorage.getItem(OLD_LATER_KEY) || 'null')
  } catch {
    ids = []
  }
  if (ids === null) return
  const numbers = Array.isArray(ids) ? ids.filter((id) => Number.isInteger(id)).slice(-200) : []
  try {
    if (numbers.length) await aiApi.snoozeMany(numbers)
    localStorage.removeItem(OLD_LATER_KEY)
  } catch {
    // Не вышло (нет сети) — попробуем при следующем подсчёте.
  }
}

export function loadPending() {
  if (!pending) {
    pending = moveOldLater()
      .then(() => aiApi.pendingCount())
      .then((data) => {
        pendingCount.value = Number(data?.count || 0)
        return pendingCount.value
      })
      .catch(() => pendingCount.value)
      .finally(() => {
        pending = null
      })
  }
  return pending
}

/** Решение по предложению принято в чате или на странице «Ждёт решения» — пересчитать. */
export function refreshPending() {
  return loadPending()
}

export function resetPending() {
  pendingCount.value = 0
}

export function usePending() {
  return { pendingCount }
}
