import { computed, ref } from 'vue'

const SESSION_KEY = 'aimarketing-session'
const session = ref(localStorage.getItem(SESSION_KEY) || '')

export function getSession() {
  return session.value
}

export function setSession(value) {
  session.value = value || ''
  if (value) localStorage.setItem(SESSION_KEY, value)
  else localStorage.removeItem(SESSION_KEY)
}

export function clearSession() {
  setSession('')
}

export function useSession() {
  return {
    session,
    isLoggedIn: computed(() => Boolean(session.value)),
    setSession,
    clearSession,
  }
}

const OAUTH_RETURN_KEY = 'aimarketing-oauth-return'

/** Запоминает, куда вернуть пользователя после подключения Яндекса (только внутренние пути). */
export function setOAuthReturn(path) {
  try {
    if (path && path.startsWith('/') && !path.startsWith('//')) sessionStorage.setItem(OAUTH_RETURN_KEY, path)
    else sessionStorage.removeItem(OAUTH_RETURN_KEY)
  } catch {
    // Без sessionStorage вернёмся в обзор.
  }
}

export function takeOAuthReturn(fallback = '/cabinet') {
  try {
    const path = sessionStorage.getItem(OAUTH_RETURN_KEY)
    sessionStorage.removeItem(OAUTH_RETURN_KEY)
    if (path && path.startsWith('/') && !path.startsWith('//')) return path
  } catch {
    // ignore
  }
  return fallback
}
