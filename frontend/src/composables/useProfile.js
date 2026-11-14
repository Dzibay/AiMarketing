import { ref } from 'vue'
import { webApi } from '../api/web'
import { getSession } from './useSession'

// Пользователь и подписка для шапки и проверки доступа: грузим раз за сессию, оболочка пересоздаётся на каждой странице.
const user = ref(null)
const subscription = ref(null)
let owner = null
let pending = null
// Настройки интерфейса (user.ui): номер последнего изменения, очередь запросов и сколько из них ещё в пути.
let uiRev = 0
let uiQueue = Promise.resolve()
let uiBusy = 0

/** force — перечитать (после продления, подтверждения почты, ответа 402/403). */
export function loadProfile(force = false) {
  const session = getSession()
  if (user.value && !force && owner === session) return Promise.resolve(user.value)
  if (!pending) {
    const rev = uiRev
    pending = webApi
      .me()
      .then((data) => {
        // Настройки интерфейса меняли, пока шёл запрос (или их ещё сохраняют): ответ о них мог устареть —
        // оставляем то, что на экране, свежие придут с ответом на сохранение.
        if (data.user && user.value && (rev !== uiRev || uiBusy)) data.user.ui = user.value.ui
        user.value = data.user || null
        subscription.value = data.subscription || null
        owner = session
        return user.value
      })
      .finally(() => {
        pending = null
      })
  }
  return pending
}

/** Свежая подписка из другого ответа (страница тарифа) — без лишнего запроса /me. */
export function setSubscription(sub) {
  if (sub) subscription.value = sub
}

/** Свежий профиль из ответа PATCH /me — шапка сразу показывает новое имя. */
export function setUser(next) {
  if (next) user.value = next
}

/** Как сервер сольёт настройки: null сбрасывает ключ, подсказки дополняют список (users.set_ui). */
function mergeUi(ui, patch) {
  const next = { ...(ui || {}) }
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete next[key]
    else if (key === 'hints_seen') next[key] = [...new Set([...(next[key] || []), ...value])]
    else next[key] = value
  }
  return next
}

/**
 * Настройки интерфейса в аккаунте — роль, экспертный режим, просмотренные подсказки (PATCH /me/ui).
 * patch — только меняемые ключи, null сбрасывает ключ. В профиле на экране они меняются сразу, не дожидаясь
 * ответа. Запросы идут по очереди: два быстрых переключения не придут на сервер в обратном порядке.
 * Сервер отказал — промис отклонён, а при следующей загрузке профиля вернётся то, что лежит в аккаунте.
 */
export function saveUi(patch) {
  const rev = ++uiRev
  uiBusy += 1
  if (user.value) user.value = { ...user.value, ui: mergeUi(user.value.ui, patch) }
  const run = () =>
    webApi
      .updateUi(patch)
      .then((data) => {
        if (rev === uiRev && user.value) setUser(data.user)
        return data.user
      })
      .finally(() => {
        uiBusy -= 1
      })
  uiQueue = uiQueue.then(run, run)
  return uiQueue
}

export function resetProfile() {
  user.value = null
  subscription.value = null
  owner = null
  pending = null
}

/**
 * Куда вести при закрытом доступе: почта не подтверждена — на экран подтверждения,
 * подписка закончилась — на страницу тарифа («Продлить»). null — доступ открыт или уже там.
 */
export function accessRedirect(sub, path) {
  if (sub?.state === 'unverified') return path === '/verify-email' ? null : '/verify-email'
  if (sub?.state === 'expired') return path === '/cabinet/plan' ? null : '/cabinet/plan'
  return null
}

/** Функция тарифа выключена: правила (autopilot), эксперименты, Вордстат, картинки, дайджест. */
export function featureLocked(sub, key) {
  return !!key && sub?.features?.[key] === false
}

export function useProfile() {
  return { user, subscription }
}
