import { ref } from 'vue'
import { webApi } from '../api/web'
import { getSession } from './useSession'

// Пользователь и подписка для шапки и проверки доступа: грузим раз за сессию, оболочка пересоздаётся на каждой странице.
const user = ref(null)
const subscription = ref(null)
let owner = null
let pending = null

/** force — перечитать (после продления, подтверждения почты, ответа 402/403). */
export function loadProfile(force = false) {
  const session = getSession()
  if (user.value && !force && owner === session) return Promise.resolve(user.value)
  if (!pending) {
    pending = webApi
      .me()
      .then((data) => {
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
