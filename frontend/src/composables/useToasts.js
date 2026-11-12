import { ref } from 'vue'

const toasts = ref([])
let seq = 0

/**
 * Тост внизу справа. action: { label, run } — кнопка в тосте (например, «Вернуть» после удаления);
 * такой тост висит дольше, 10 секунд.
 */
export function pushToast(message, tone = 'info', { action = null, timeout } = {}) {
  const id = ++seq
  toasts.value = [...toasts.value, { id, message, tone, action }]
  window.setTimeout(() => dismissToast(id), timeout ?? (action ? 10000 : 4200))
  return id
}

export function dismissToast(id) {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

export function useToasts() {
  return { toasts, pushToast, dismissToast }
}
