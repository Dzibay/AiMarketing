import { ref } from 'vue'
import { aiApi } from '../api/web'

// «Ждёт решения»: неподтверждённые предложения ИИ по всем проектам — счётчик в шапке и общая страница.
const pendingCount = ref(0)
let pending = null

export function loadPending() {
  if (!pending) {
    pending = aiApi
      .pendingCount()
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
