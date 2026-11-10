import { ref } from 'vue'
import { webApi } from '../api/web'
import { setSubscription } from './useProfile'

// Тариф, расход лимита и предложения: для баннера 80% / 100%, окна «Лимит исчерпан» и страницы тарифа.
const plan = ref(null)
let loadedAt = 0
let pending = null
const FRESH_MS = 5 * 60 * 1000

export function loadPlan(force = false) {
  if (!force && plan.value && Date.now() - loadedAt < FRESH_MS) return Promise.resolve(plan.value)
  if (!pending) {
    pending = webApi
      .plan()
      .then((data) => {
        plan.value = data
        loadedAt = Date.now()
        setSubscription(data.subscription)
        return data
      })
      .finally(() => {
        pending = null
      })
  }
  return pending
}

export function resetPlan() {
  plan.value = null
  loadedAt = 0
  pending = null
}

export function usePlan() {
  return { plan }
}
