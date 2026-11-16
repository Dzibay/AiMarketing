import { ref } from 'vue'
import { aiApi, notifyApi } from '../api/web'

// «Ждёт решения»: неподтверждённые предложения ИИ по всем проектам — счётчик в шапке и общая страница.
// Отложенные «Позже» хранятся на сервере (ai_actions.snoozed_at) и в счётчик не входят.
// Тем же запросом (/me/badges) приходит число непрочитанных уведомлений для колокольчика.
const pendingCount = ref(0)
const notifCount = ref(0)
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
      .then(() => notifyApi.badges())
      .then((data) => {
        pendingCount.value = Number(data?.pending || 0)
        notifCount.value = Number(data?.notifications || 0)
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
  notifCount.value = 0
}

/** Колокольчик сам знает точное число после прочтения — без лишнего запроса. */
export function setNotifCount(value) {
  notifCount.value = Math.max(0, Number(value) || 0)
}

/**
 * Открыта страница (чат) — уведомления, ведущие на неё, прочитаны: ответ ИИ в открытом чате
 * в колокольчике не висит.
 */
export async function readNotificationsAt(link, force = false) {
  // force — ответ только что пришёл в открытый чат: счётчик в шапке о нём ещё не знает.
  if (!link) return
  // Счётчик ещё грузится (страницу открыли сразу по адресу чата) — дождёмся его.
  if (!force && pending) await pending
  if (!force && !notifCount.value) return
  try {
    const data = await notifyApi.read({ link })
    if (data?.marked) setNotifCount(data.unread)
  } catch {
    // Не страшно: запись останется непрочитанной до следующего раза.
  }
}

export function usePending() {
  return { pendingCount, notifCount }
}
