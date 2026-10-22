import { ref } from 'vue'

const toasts = ref([])
let seq = 0

export function pushToast(message, tone = 'info') {
  const id = ++seq
  toasts.value = [...toasts.value, { id, message, tone }]
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((item) => item.id !== id)
  }, 4200)
}

export function useToasts() {
  return { toasts, pushToast }
}
