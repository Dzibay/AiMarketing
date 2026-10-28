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
            <span v-if="refreshing" class="ws-head__sync">
              <span class="spinner" aria-hidden="true"></span>
              обновляем данные
            </span>
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

      <div v-if="pendingItems.length" class="notice ws-loading" :class="{ 'notice--warn': loadStalled }" role="status">
        <div class="ws-loading__head">
          <span v-if="pendingItems.some((i) => i.job)" class="spinner" aria-hidden="true"></span>
          <strong>{{ loadStalled ? 'Загрузка остановилась на ошибке' : 'Данные ещё загружаются' }}</strong>
          <span v-if="loadPercent != null" class="ws-loading__pct">{{ loadPercent }}%</span>
        </div>
        <div class="load-bar"><span :style="{ width: `${loadPercent || 0}%` }"></span></div>
        <p v-if="loadStalled">
          Часть данных не загрузилась, поэтому кампании и цифры неполные. Загрузку повторим автоматически в течение часа,
          запустить сразу можно кнопкой «Обновить» в <RouterLink to="/cabinet/accounts">Аккаунтах</RouterLink>.
        </p>
        <p v-else>
          Пока загрузка не закончится, кампании и цифры неполные — разделы обновятся сами. Подробнее — в
          <RouterLink to="/cabinet/accounts">Аккаунтах</RouterLink>.
        </p>
        <ul class="ws-loading__list">
          <li v-for="item in pendingItems" :key="item.key">
            <strong>{{ item.title }}</strong>
            <span>{{ loadLine(item) }}</span>
          </li>
        </ul>
      </div>

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

      <RouterView :key="`${ws.id}:${dataVersion}`" />
    </template>
  </AppShell>
</template>

<script setup>
import { computed, onBeforeUnmount, provide, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import AppShell from '../../components/AppShell.vue'
import { errorText, wsApi } from '../../api/web'
import { jobPercent, jobText, stageErrorText, stageFailure, stageProblem } from '../../composables/useLoads'
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
  { name: 'experiments', path: 'experiments', label: 'Эксперименты', names: ['ws-experiments'] },
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

// --- Загрузка данных кабинетов и счётчика пространства ---

const LOAD_ACTIVE_MS = 3000
const LOAD_WAIT_MS = 30000
// Разделы с данными из Яндекса: их перерисовываем, когда догрузился очередной этап.
const DATA_ROUTES = ['ws-summary', 'ws-analytics', 'ws-campaigns', 'ws-campaign', 'ws-queries']

const wsLoad = ref(null)
const dataVersion = ref(0)
let loadTimer = null

const loadItems = computed(() => {
  const d = wsLoad.value
  if (!d) return []
  return [
    ...d.accounts.filter((a) => a.enabled).map((a) => ({ ...a, key: `a${a.id}`, title: a.name || a.client_login })),
    ...d.counters
      .filter((c) => c.enabled && c.canonical)
      .map((c) => ({ ...c, key: `c${c.id}`, title: `Метрика, счётчик № ${c.counter_id}` })),
  ]
})
/** Ещё ни разу не загружены целиком — вместо нулей показываем плашку. */
const pendingItems = computed(() => loadItems.value.filter((i) => !i.loaded_at))
/** Всё загружено, идёт плановая актуализация — только тихая отметка в шапке. */
const refreshing = computed(() => !pendingItems.value.length && loadItems.value.some((i) => i.job))
/** Ничего не грузится, а незагруженное упало с ошибкой — не «ещё загружаются», а ждёт повтора. */
const loadStalled = computed(
  () => !pendingItems.value.some((i) => i.job) && pendingItems.value.some((i) => stageFailure(i)),
)

function stagesDone(item) {
  return (item.stages || []).filter((s) => s.status === 'ok').length
}

/** Процент по незагруженным: у идущей задачи — её прогресс, у ждущей — доля готовых этапов. */
const loadPercent = computed(() => {
  const items = pendingItems.value
  if (!items.length) return null
  const sum = items.reduce((acc, i) => {
    if (i.job?.status === 'running') return acc + jobPercent(i.job)
    const total = (i.stages || []).length
    return acc + (total ? (100 * stagesDone(i)) / total : 0)
  }, 0)
  return Math.round(sum / items.length)
})

function loadLine(item) {
  if (item.job) return jobText(item.job)
  const problem = stageProblem(item)
  if (problem?.error === 'units' || problem?.skipped === 'units') {
    return 'закончились баллы API Директа — продолжим автоматически'
  }
  if (problem) return `${problem.title}: ${stageErrorText(problem)}`
  return 'в очереди на загрузку'
}

function progressMark(data) {
  // Сколько этапов готово у незагруженных и сколько незагруженных: изменилось — есть новые данные.
  const items = [...(data?.accounts || []), ...(data?.counters || [])]
  return items.map((i) => `${i.id}:${i.loaded_at ? 'L' : stagesDone(i)}`).join(',')
}

async function pollLoad(id) {
  clearTimeout(loadTimer)
  loadTimer = null
  let data = null
  try {
    data = await wsApi.load(id)
  } catch {
    // Сеть моргнула — попробуем на следующем тике.
  }
  if (Number(route.params.wsId) !== id) return
  if (data) {
    const before = wsLoad.value ? progressMark(wsLoad.value) : null
    wsLoad.value = data
    if (before !== null && before !== progressMark(data)) {
      reload()
      if (DATA_ROUTES.includes(route.name)) dataVersion.value += 1
    }
  }
  const anyJob = loadItems.value.some((i) => i.job)
  if (anyJob || pendingItems.value.length) {
    loadTimer = setTimeout(() => pollLoad(id), anyJob ? LOAD_ACTIVE_MS : LOAD_WAIT_MS)
  }
}

watch(
  () => Number(route.params.wsId),
  (id, prev) => {
    if (!id || id === prev) return
    ws.value = null
    wsLoad.value = null
    load(id)
    pollLoad(id)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  clearTimeout(loadTimer)
  loadTimer = null
})
</script>
