<template>
  <div class="site">
    <SiteHeader solid />
    <main class="auth-shell">
      <div class="wrap">
        <div class="auth-card">
          <h1>{{ title }}</h1>
          <p class="sub">{{ text }}</p>
          <RouterLink v-if="failed" class="btn btn--wide btn--accent" :to="back.to">{{ back.label }}</RouterLink>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import SiteHeader from '../components/SiteHeader.vue'
import { ERRORS, webApi } from '../api/web'
import { setSession, takeOAuthReturn } from '../composables/useSession'
import { useHead } from '../utils/useHead'

useHead('Подключение Яндекса — AiMarketing', true)

const route = useRoute()
const router = useRouter()
const failed = ref(false)
const errorCode = ref('')
const pending = ref(true)

const title = computed(() => {
  if (failed.value) return 'Не удалось подключить'
  if (pending.value) return 'Подключаем…'
  return 'Готово'
})
const text = computed(() => {
  if (!failed.value) return 'Открываем кабинет…'
  return ERRORS[errorCode.value] || 'Попробуйте подключить Яндекс ещё раз.'
})
// Аудитории подключают уже вошедшему — назад к логинам, а не ко входу.
const back = computed(() =>
  errorCode.value.startsWith('audience')
    ? { to: '/cabinet/accounts', label: 'К аккаунтам' }
    : { to: '/login', label: 'Ко входу' },
)

onMounted(async () => {
  const code = String(route.query.code || '')
  const error = String(route.query.error || '')
  // Совместимость со старым редиректом ?token=
  const legacyToken = String(route.query.token || '')

  if (error) {
    failed.value = true
    pending.value = false
    errorCode.value = error || 'yandex'
    return
  }

  if (legacyToken) {
    setSession(legacyToken)
    pending.value = false
    router.replace(takeOAuthReturn())
    return
  }

  if (!code) {
    failed.value = true
    pending.value = false
    errorCode.value = 'yandex'
    return
  }

  try {
    const data = await webApi.oauthExchange(code)
    setSession(data.token)
    pending.value = false
    router.replace(takeOAuthReturn())
  } catch (err) {
    failed.value = true
    pending.value = false
    errorCode.value = err?.code || 'state'
  }
})
</script>
