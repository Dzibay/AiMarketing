import { ref } from 'vue'

// Какие вопросы «Сделать так по умолчанию?» уже задавали в этом браузере: каждый вид настройки — один раз.
const KEY = 'tolk-defaults-asked'

function readAsked() {
  try {
    return new Set(JSON.parse(localStorage.getItem(KEY) || '[]'))
  } catch {
    return new Set()
  }
}

// { wsId, kind } — вопрос, который сейчас показан под сохранённой настройкой.
const pending = ref(null)

export function markAsked(kind) {
  const asked = readAsked()
  asked.add(kind)
  try {
    localStorage.setItem(KEY, JSON.stringify([...asked]))
  } catch {
    // Без localStorage вопрос может повториться после перезагрузки.
  }
  if (pending.value?.kind === kind) pending.value = null
}

/**
 * После первого сохранения настройки в проекте: предложить ставить её всем новым проектам.
 * kind — url_template | model_class | ai_access | kpi | budget_protection.
 */
export function offerDefaults(wsId, kind) {
  if (!wsId || readAsked().has(kind)) return
  pending.value = { wsId, kind }
}

export function useDefaultsAsk() {
  return { pending }
}
