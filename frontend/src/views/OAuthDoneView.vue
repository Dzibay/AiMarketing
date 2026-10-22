<template>
  <div class="site">
    <SiteHeader solid />
    <main class="auth-shell">
      <div class="wrap">
        <div class="auth-card">
          <h1>{{ title }}</h1>
          <p class="sub">{{ text }}</p>
          <RouterLink v-if="failed" class="btn btn--wide btn--accent" to="/login">Ко входу</RouterLink>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import SiteHeader from '../components/SiteHeader.vue'
import { ERRORS } from '../api/web'
import { setSession } from '../composables/useSession'
import { useHead } from '../utils/useHead'

useHead('Подключение Яндекса — AiMarketing', true)

const route = useRoute()
const router = useRouter()
const failed = ref(false)
const errorCode = ref('')

const title = computed(() => (failed.value ? 'Не удалось подключить' : 'Готово'))
const text = computed(() => {
  if (!failed.value) return 'Открываем кабинет…'
  return ERRORS[errorCode.value] || 'Попробуйте подключить Яндекс ещё раз.'
})

onMounted(() => {
  const token = String(route.query.token || '')
  const error = String(route.query.error || '')
  if (token) {
    setSession(token)
    router.replace('/cabinet')
    return
  }
  failed.value = true
  errorCode.value = error || 'yandex'
})
</script>
