<template>
  <AppShell>
    <div v-if="error" class="panel">
      <h2>Пространство недоступно</h2>
      <p class="empty">{{ error }}</p>
      <RouterLink class="btn btn--ghost" to="/cabinet">К обзору</RouterLink>
    </div>

    <div v-else-if="!ws" class="panel"><p class="empty">Открываем пространство…</p></div>

    <template v-else>
      <div class="ws-head">
        <div>
          <p class="ws-head__crumbs">
            <RouterLink v-if="ws.client_id" to="/cabinet/clients">{{ ws.client_name }}</RouterLink>
            <span v-else>Без клиента</span>
            <span aria-hidden="true">/</span>
            <span>Счётчик № {{ ws.counter_id }}</span>
          </p>
          <h1>{{ ws.name }}</h1>
          <p class="ws-head__meta">
            <a v-if="siteUrl" :href="siteUrl" target="_blank" rel="noopener noreferrer">{{ ws.site }}</a>
            <span>{{ ws.accounts_count }} {{ plural(ws.accounts_count, 'кабинет', 'кабинета', 'кабинетов') }} Директа</span>
            <span>{{ ws.campaigns_count }} {{ plural(ws.campaigns_count, 'кампания', 'кампании', 'кампаний') }}</span>
            <span v-if="ws.kpi_type">
              KPI: {{ ws.kpi_type === 'cpa' ? 'CPA' : 'ДРР' }}
              <template v-if="ws.kpi_target != null">
                ≤ {{ ws.kpi_type === 'cpa' ? rubles(ws.kpi_target) : `${ws.kpi_target}%` }}
              </template>
            </span>
            <span v-if="ws.goal_name">цель «{{ ws.goal_name }}»</span>
          </p>
        </div>
        <div class="row-actions">
          <RouterLink class="btn btn--accent" :to="`/w/${ws.id}/chat`">Спросить ИИ</RouterLink>
        </div>
      </div>

      <p v-if="!ws.counter_ok" class="notice notice--warn">
        Счётчик № {{ ws.counter_id }} сейчас не виден ни одному подключённому логину Метрики или выключен — визиты и цели
        не обновляются. Проверьте доступ в <RouterLink to="/cabinet/accounts">Аккаунтах</RouterLink>.
      </p>
      <p v-if="ws.archived" class="notice">
        Пространство в архиве. Вернуть его можно в <RouterLink :to="`/w/${ws.id}/settings`">настройках</RouterLink>.
      </p>

      <nav class="ws-tabs" aria-label="Разделы пространства">
        <RouterLink
          v-for="tab in tabs"
          :key="tab.name"
          :to="`/w/${ws.id}/${tab.path}`"
          :class="{ active: tab.names.includes(route.name) }"
        >
          {{ tab.label }}
          <span v-if="tab.badge" class="ws-tabs__badge">{{ tab.badge }}</span>
        </RouterLink>
      </nav>

      <RouterView :key="ws.id" />
    </template>
  </AppShell>
</template>

<script setup>
import { computed, provide, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import AppShell from '../../components/AppShell.vue'
import { errorText, wsApi } from '../../api/web'
import { patchWorkspaceInList, rememberWorkspace } from '../../composables/useWorkspaces'
import { useHead } from '../../utils/useHead'
import { plural, rubles } from '../../utils/format'
import { WS_KEY } from './context'

const route = useRoute()
const ws = ref(null)
const error = ref('')

useHead('Пространство — AiMarketing', true)
watch(
  () => ws.value?.name,
  (name) => {
    if (name) document.title = `${name} — AiMarketing`
  },
)

const tabs = computed(() => [
  { name: 'summary', path: 'summary', label: 'Сводка', names: ['ws-summary'] },
  { name: 'analytics', path: 'analytics', label: 'Аналитика', names: ['ws-analytics'] },
  {
    name: 'campaigns',
    path: 'campaigns',
    label: 'Кампании',
    names: ['ws-campaigns', 'ws-campaign'],
    badge: ws.value?.campaigns_count || '',
  },
  { name: 'queries', path: 'queries', label: 'Поисковые запросы', names: ['ws-queries'] },
  { name: 'actions', path: 'actions', label: 'Действия и правила', names: ['ws-actions'] },
  { name: 'chat', path: 'chat', label: 'ИИ-чат', names: ['ws-chat'] },
  { name: 'settings', path: 'settings', label: 'Настройки', names: ['ws-settings'] },
])

const siteUrl = computed(() => {
  const site = (ws.value?.site || '').trim()
  if (!site) return ''
  return /^https?:\/\//i.test(site) ? site : `https://${site}`
})

async function load(id) {
  error.value = ''
  try {
    const data = await wsApi.get(id)
    if (Number(route.params.wsId) !== id) return
    ws.value = data
    rememberWorkspace(id)
  } catch (err) {
    ws.value = null
    error.value = errorText(err)
  }
}

function reload() {
  return load(Number(route.params.wsId))
}

/** Вкладка сохранила изменения пространства — обновляем шапку и переключатель без запроса. */
function patch(updated) {
  if (!updated || !ws.value || updated.id !== ws.value.id) return
  ws.value = { ...ws.value, ...updated }
  patchWorkspaceInList(updated)
}

provide(WS_KEY, { ws, reload, patch })

watch(
  () => Number(route.params.wsId),
  (id, prev) => {
    if (!id || id === prev) return
    ws.value = null
    load(id)
  },
  { immediate: true },
)
</script>
