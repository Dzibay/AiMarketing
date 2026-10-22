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
