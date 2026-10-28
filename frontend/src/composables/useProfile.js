import { ref } from 'vue'
import { webApi } from '../api/web'

// Пользователь для шапки: грузим раз за сессию, оболочка пересоздаётся на каждой странице.
const user = ref(null)
let pending = null

export function loadProfile() {
  if (user.value) return Promise.resolve(user.value)
  if (!pending) {
    pending = webApi
      .me()
      .then((data) => {
        user.value = data.user || null
        return user.value
      })
      .finally(() => {
        pending = null
      })
  }
  return pending
}

export function resetProfile() {
  user.value = null
  pending = null
}

export function useProfile() {
  return { user }
}
