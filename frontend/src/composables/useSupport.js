import { ref } from 'vue'

// Виджет поддержки: открыт ли он и сколько ответов поддержки не прочитано. Счётчик приходит с /me/badges
// (usePending) и обновляется самим виджетом, когда он открыт.
const supportOpen = ref(false)
const supportUnread = ref(0)
// Текст, который виджет впишет в поле при открытии, — вопрос, на который ИИ не нашёл ответа в справке.
const supportDraft = ref('')

/** Открыть поддержку; draft — готовый текст сообщения. Как обработчик клика получает событие — его не берём. */
export function openSupport(draft) {
  if (typeof draft === 'string' && draft.trim()) supportDraft.value = draft.trim()
  supportOpen.value = true
}

export function closeSupport() {
  supportOpen.value = false
}

export function setSupportUnread(value) {
  supportUnread.value = Math.max(0, Number(value) || 0)
}

export function useSupport() {
  return { supportOpen, supportUnread, supportDraft }
}
