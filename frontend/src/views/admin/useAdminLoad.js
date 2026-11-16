import { ref, watch } from 'vue'
import { errorText } from '../../api/web'

/**
 * Загрузка страницы админки по параметрам: при смене params — перезапрос, устаревший ответ отбрасывается.
 * fetcher(params) → Promise. Возвращает { data, loading, error, reload }.
 */
export function useAdminLoad(fetcher, params) {
  const data = ref(null)
  const loading = ref(false)
  const error = ref('')
  let seq = 0

  async function reload() {
    const my = ++seq
    loading.value = true
    error.value = ''
    try {
      const out = await fetcher(params ? params.value : undefined)
      if (my === seq) data.value = out
    } catch (err) {
      if (my === seq) error.value = errorText(err)
    } finally {
      if (my === seq) loading.value = false
    }
  }

  if (params) watch(params, reload, { immediate: true, deep: true })
  else reload()
  return { data, loading, error, reload }
}

/** Одно значение из адреса (?key=…), массив — первый элемент. */
export function queryOne(route, key) {
  const v = route.query[key]
  return (Array.isArray(v) ? v[0] : v) || ''
}

export const autoStep = (days) => (days <= 92 ? 'day' : days <= 400 ? 'week' : 'month')
export const stepWord = (step) => (step === 'week' ? 'неделям' : step === 'month' ? 'месяцам' : 'дням')
