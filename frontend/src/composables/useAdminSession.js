import { computed, ref } from 'vue'

// Токен админки — отдельно от сессии кабинета: админ не пользователь кабинета.
const KEY = 'aimarketing-admin'
const session = ref(localStorage.getItem(KEY) || '')

export function getAdminSession() {
  return session.value
}

export function setAdminSession(value) {
  session.value = value || ''
  if (value) localStorage.setItem(KEY, value)
  else localStorage.removeItem(KEY)
}

export function clearAdminSession() {
  setAdminSession('')
}

export function useAdminSession() {
  return {
    session,
    isLoggedIn: computed(() => Boolean(session.value)),
  }
}
