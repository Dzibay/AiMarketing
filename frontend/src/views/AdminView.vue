<template>
  <AppShell>
    <div class="cabinet__head">
      <div>
        <h1>Админка</h1>
        <p>Пользователи и их лимиты, общие ИИ, Вордстат и картинки, расход сервиса.</p>
      </div>
    </div>

    <div v-if="loading" class="panel"><p class="empty">Загружаем…</p></div>

    <section v-else-if="denied" class="panel">
      <h2>Нет доступа</h2>
      <p class="hint">
        Админка открыта только владельцу сервиса: почта аккаунта должна быть в ADMIN_EMAILS в настройках сервера.
      </p>
      <RouterLink class="btn btn--ghost" to="/cabinet">В кабинет</RouterLink>
    </section>

    <template v-else-if="summary">
      <div class="stats-grid">
        <div class="stat-card">
          <small>Пользователей</small>
          <strong>{{ num(summary.users_total) }}</strong>
        </div>
        <div v-for="s in services" :key="s.key" class="stat-card">
          <small>{{ s.label }}</small>
          <strong :class="s.ok ? 'tone-ok' : 'tone-warn'">{{ s.ok ? 'работает' : 'не подключено' }}</strong>
        </div>
      </div>

      <nav class="seg seg--tabs" aria-label="Разделы админки">
        <button v-for="t in TABS" :key="t.id" type="button" :class="{ active: tab === t.id }" @click="setTab(t.id)">
          {{ t.label }}
        </button>
      </nav>

      <AdminUsers v-if="tab === 'users'" />
      <template v-else-if="tab === 'services'">
        <AdminServices :bridge="bridge" @changed="loadSummary" />
        <AdminBridge @update="bridge = $event" />
      </template>
      <AdminUsage v-else-if="tab === 'usage'" />
    </template>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import AppShell from '../components/AppShell.vue'
import AdminBridge from './admin/AdminBridge.vue'
import AdminServices from './admin/AdminServices.vue'
import AdminUsage from './admin/AdminUsage.vue'
import AdminUsers from './admin/AdminUsers.vue'
import { adminApi } from '../api/admin'
import { errorText } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'
import { num } from '../utils/format'

useHead('Админка — AiMarketing', true)

const TABS = [
  { id: 'users', label: 'Пользователи' },
  { id: 'services', label: 'ИИ и сервисы' },
  { id: 'usage', label: 'Расход' },
]

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const denied = ref(false)
const summary = ref(null)
const bridge = ref(null)
const tab = ref(TABS.some((t) => t.id === route.query.tab) ? route.query.tab : 'users')

const services = computed(() => [
  { key: 'ai', label: 'ИИ', ok: summary.value?.ai_configured },
  { key: 'wordstat', label: 'Вордстат', ok: summary.value?.wordstat_configured },
  { key: 'images', label: 'Картинки', ok: summary.value?.images_configured },
  { key: 'oauth', label: 'Вход через Яндекс', ok: summary.value?.yandex_oauth },
])

function setTab(id) {
  tab.value = id
  router.replace({ query: id === 'users' ? {} : { tab: id } })
}

async function loadSummary() {
  try {
    summary.value = await adminApi.me()
  } catch (err) {
    if (err.status === 403) denied.value = true
    else pushToast(errorText(err), 'error')
  }
}

onMounted(async () => {
  await loadSummary()
  loading.value = false
})
</script>
