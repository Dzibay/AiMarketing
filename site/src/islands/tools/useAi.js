// ИИ-режим генераторов: POST /api/site/generate (лимиты и модель — в админке, вкладка «Сайт»).
// Кнопку показываем, только если ИИ-режим включён; капча — невидимая, если задан ключ.
import { onMounted, ref } from 'vue'
import { goal } from '../../lib/goal.js'
import { captchaToken, sitePublic } from '../../lib/public.js'

const FAILED = 'ИИ не ответил — попробуйте ещё раз чуть позже'

export function useAi(tool) {
  const available = ref(false)
  const busy = ref(false)
  const error = ref('')
  const result = ref(null)

  onMounted(async () => {
    available.value = !!(await sitePublic()).ai_tools
  })

  async function run(fields) {
    busy.value = true
    error.value = ''
    try {
      const captcha = await captchaToken()
      const res = await fetch('/api/site/generate', {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({ tool, fields, captcha }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        error.value = data?.detail?.message || FAILED
        return null
      }
      result.value = data
      goal('tool_ai', { tool })
      return data
    } catch {
      error.value = 'Нет связи с сервером — попробуйте ещё раз'
      return null
    } finally {
      busy.value = false
    }
  }

  return { available, busy, error, result, run }
}
