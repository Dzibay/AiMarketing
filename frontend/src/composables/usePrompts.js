import { computed, ref } from 'vue'
import { aiApi } from '../api/web'
import { useRole } from './useRole'

/**
 * Готовые задачи ИИ — один источник (backend/app/ai_prompts.py) для стартового экрана чата,
 * «Задач» и «/» в поле ввода, быстрых вопросов на главной и в списке кампаний.
 * Грузятся один раз за вход; свои задачи меняются здесь же, без перезагрузки.
 *
 * Роль (useRole): встроенная задача видна, если роль есть в её roles, и стоит на главной, если роль есть в home.
 *
 * Места (place): general — общий чат; project — чат проекта с кампаниями; first — проект без кампаний;
 * campaigns / campaigns_picked — вопрос про кампании списка (все / отмеченные).
 */
const data = ref(null)
let loading = null

export function loadPrompts() {
  if (data.value) return Promise.resolve(data.value)
  if (!loading) {
    loading = aiApi
      .prompts()
      .then((res) => {
        data.value = { categories: res.categories || [], builtin: res.builtin || [], own: res.own || [] }
        return data.value
      })
      .finally(() => {
        loading = null
      })
  }
  return loading
}

export function resetPrompts() {
  data.value = null
}

/** Встроенная задача по ключу из мест places — для «?ask=…» из других разделов. */
export async function promptByKey(key, places) {
  const d = await loadPrompts()
  return d.builtin.find((p) => p.key === key && p.places.some((x) => places.includes(x))) || null
}

export async function saveOwnPrompt(title, text) {
  await loadPrompts().catch(() => null)
  const { prompt } = await aiApi.createPrompt(title, text)
  if (data.value && !data.value.own.some((p) => p.id === prompt.id)) data.value.own = [...data.value.own, prompt]
  return prompt
}

export async function deleteOwnPrompt(id) {
  await aiApi.deletePrompt(id)
  if (data.value) data.value.own = data.value.own.filter((p) => p.id !== id)
}

/**
 * Задачи места: builtin — встроенные этого места, groups — по категориям в порядке категорий
 * (пустые пропущены), own — свои (во всех местах чата). place — строка или computed.
 */
export function usePrompts(place) {
  loadPrompts().catch(() => {})
  const where = computed(() => (typeof place === 'string' ? place : place?.value))
  const { role } = useRole()
  const builtin = computed(() =>
    (data.value?.builtin || []).filter((p) => p.places.includes(where.value) && (p.roles || []).includes(role.value)),
  )
  const groups = computed(() =>
    (data.value?.categories || [])
      .map((c) => ({ ...c, items: builtin.value.filter((p) => p.category === c.key) }))
      .filter((g) => g.items.length),
  )
  const own = computed(() => data.value?.own || [])
  const home = computed(() => builtin.value.filter((p) => (p.home || []).includes(role.value)))
  const ready = computed(() => !!data.value)
  return { builtin, groups, own, home, ready }
}
