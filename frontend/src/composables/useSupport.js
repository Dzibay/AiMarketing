import { ref } from 'vue'

// Виджет поддержки: открыт ли он и сколько ответов поддержки не прочитано. Счётчик приходит с /me/badges
// (usePending) и обновляется самим виджетом, когда он открыт.
const supportOpen = ref(false)
const supportUnread = ref(0)

export function openSupport() {
  supportOpen.value = true
}

export function closeSupport() {
  supportOpen.value = false
}

export function setSupportUnread(value) {
  supportUnread.value = Math.max(0, Number(value) || 0)
}

export function useSupport() {
  return { supportOpen, supportUnread }
}
