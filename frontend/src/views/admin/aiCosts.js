import { ref, watch } from 'vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { num } from '../../utils/format'

/** Общее для вкладок страницы «Расходы на ИИ» (docs/admin-plan.md, раздел 6). */

export const KIND_OPTIONS = [
  { value: '', label: 'Все виды' },
  { value: 'model', label: 'Модели' },
  { value: 'wordstat', label: 'Вордстат' },
  { value: 'image', label: 'Картинки' },
]

export const SOURCE_OPTIONS = [
  { value: '', label: 'Кабинет и лендинг' },
  { value: 'cabinet', label: 'Кабинет' },
  { value: 'landing', label: 'Лендинг' },
]

/** Цвета частей сложенных графиков — по порядку, от самой дорогой части. */
export const PART_COLORS = ['var(--accent)', 'var(--c-blue)', 'var(--c-amber)', 'var(--c-teal)', 'var(--c-green)', 'var(--muted)', 'var(--faint)']

export const partColor = (i) => PART_COLORS[i % PART_COLORS.length]

/** Токены коротко: 950, 12,4 тыс., 3,1 млн. */
export function tokens(value) {
  if (value === null || value === undefined) return '—'
  const v = Number(value)
  if (Math.abs(v) >= 1e6) return `${num(v / 1e6, 1)} млн`
  if (Math.abs(v) >= 1e4) return `${num(v / 1e3, 1)} тыс.`
  return num(v)
}

/**
 * Данные вкладки: грузит adminApi.aiCosts(tab, { ...params, ...extra }) при каждом изменении и сообщает период
 * наверх (onPeriod) — по нему кнопка периода показывает даты. Поздний ответ на старый запрос отбрасывается.
 */
export function useCostsTab(tab, params, extra, onPeriod) {
  const data = ref(null)
  const loading = ref(false)
  const error = ref('')
  let seq = 0

  async function load() {
    const my = ++seq
    loading.value = true
    error.value = ''
    try {
      const out = await adminApi.aiCosts(tab, { ...params.value, ...(extra ? extra() : {}) })
      if (my !== seq) return
      data.value = out
      onPeriod?.(out.period)
    } catch (err) {
      if (my === seq) error.value = errorText(err)
    } finally {
      if (my === seq) loading.value = false
    }
  }

  watch(() => [params.value, extra ? extra() : null], load, { immediate: true, deep: true })
  return { data, loading, error, load }
}

/** Чат пользователя на чтение — для AdminChatModal. */
export async function readChat(id) {
  return adminApi.chat(id)
}
