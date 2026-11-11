// Кнопка «Скопировать»: буфер обмена, а без него (http, старый браузер) — выделение текста в поле.
import { ref } from 'vue'

export function useCopy() {
  const copied = ref(false)
  let timer
  async function copy(text, el) {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      if (el) {
        el.focus()
        el.select()
        document.execCommand?.('copy')
      }
    }
    copied.value = true
    clearTimeout(timer)
    timer = setTimeout(() => (copied.value = false), 1600)
  }
  return { copied, copy }
}
