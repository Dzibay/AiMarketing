import { ref } from 'vue'
import { fetchSiteConfig } from '../api/site'

const config = ref(null)
const loading = ref(false)
let pending = null

const fallback = {
  app_name: 'AiMarketing',
  yandex_oauth: false,
  privacy_url: '/privacy',
  consent_url: '/consent',
  offer_url: '/offer',
  cookies_url: '/cookies',
}

export function useSiteConfig() {
  async function load() {
    if (config.value) return config.value
    if (pending) return pending
    loading.value = true
    pending = fetchSiteConfig()
      .then((data) => {
        config.value = { ...fallback, ...data }
        return config.value
      })
      .catch(() => {
        config.value = { ...fallback }
        return config.value
      })
      .finally(() => {
        loading.value = false
        pending = null
      })
    return pending
  }

  return { config, loading, load }
}
