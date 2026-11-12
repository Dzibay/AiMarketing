<template>
  <div v-if="locked" class="panel project-locked">
    <h2><Lock aria-hidden="true" /> Проект на тарифе выше</h2>
    <p>
      Проект сверх лимита вашего тарифа, но его данные сохранены. Чтобы открыть его, перейдите на тариф с большим
      числом проектов или отправьте в архив другой проект. Если этот проект больше не нужен — отправьте в архив его.
    </p>
    <div class="row-actions">
      <RouterLink class="btn btn--accent" to="/cabinet/plan">Тарифы</RouterLink>
      <button class="btn btn--ghost" type="button" :disabled="archiving" @click="archiveLocked">В архив</button>
      <RouterLink class="btn btn--ghost" to="/cabinet">К сводке</RouterLink>
    </div>
  </div>

  <div v-else-if="error" class="panel">
    <h2>Проект недоступен</h2>
    <p class="empty">{{ error }}</p>
    <RouterLink class="btn btn--ghost" to="/cabinet">К сводке</RouterLink>
  </div>

  <div v-else-if="!ws" class="panel"><p class="empty">Открываем проект…</p></div>

  <template v-else>
    <!-- Название проекта — в шапке страницы; здесь на сводке — сайт, KPI и цель, номер счётчика на втором плане. -->
    <p v-if="route.name === 'ws-summary'" class="ws-meta">
      <span v-if="ws.client_name">{{ ws.client_name }}</span>
      <a v-if="siteUrl" :href="siteUrl" target="_blank" rel="noopener noreferrer">{{ ws.site }}</a>
      <span>{{ ws.accounts_count }} {{ plural(ws.accounts_count, 'кабинет', 'кабинета', 'кабинетов') }} Директа</span>
      <span>{{ ws.campaigns_count }} {{ plural(ws.campaigns_count, 'кампания', 'кампании', 'кампаний') }}</span>
      <span v-if="ws.kpi_type">
        KPI: {{ ws.kpi_type === 'cpa' ? 'CPA' : 'ДРР' }}
        <template v-if="ws.kpi_target != null">
          ≤ {{ ws.kpi_type === 'cpa' ? rubles(ws.kpi_target) : pct(ws.kpi_target) }}
        </template>
      </span>
      <span v-if="ws.goal_name">цель «{{ ws.goal_name }}»</span>
      <span class="ws-meta__minor">счётчик № {{ ws.counter_id }}</span>
      <span v-if="refreshing" class="ws-meta__sync">
        <span class="spinner" aria-hidden="true"></span>
        обновляем данные
      </span>
    </p>

    <p v-if="!ws.counter_ok" class="notice notice--warn">
      Счётчик № {{ ws.counter_id }} сейчас не виден ни одному подключённому логину Метрики или выключен — визиты и цели
      не обновляются. Проверьте доступ в <RouterLink to="/cabinet/connections">Подключениях</RouterLink>.
    </p>
    <p v-if="ws.archived" class="notice">
      Проект в архиве. Вернуть его можно в <RouterLink :to="`/w/${ws.id}/settings`">настройках проекта</RouterLink>.
    </p>

    <div v-if="pendingItems.length" class="notice ws-loading" :class="{ 'notice--warn': loadStalled }" role="status">
      <div class="ws-loading__head">
        <span v-if="pendingItems.some((i) => i.job)" class="spinner" aria-hidden="true"></span>
        <strong>{{ loadStalled ? 'Загрузка остановилась на ошибке' : 'Данные ещё загружаются' }}</strong>
        <span v-if="loadPercent != null" class="ws-loading__pct">{{ pct(loadPercent, 0) }}</span>
      </div>
      <div class="load-bar"><span :style="{ width: `${loadPercent || 0}%` }"></span></div>
      <p v-if="loadStalled">
        Часть данных не загрузилась, поэтому кампании и цифры неполные. Загрузку повторим автоматически в течение часа,
        запустить сразу можно кнопкой «Обновить» в <RouterLink to="/cabinet/connections">Подключениях</RouterLink>.
      </p>
      <p v-else>
        Пока загрузка не закончится, кампании и цифры неполные — разделы обновятся сами. Подробнее — в
        <RouterLink to="/cabinet/connections">Подключениях</RouterLink>.
      </p>
      <ul class="ws-loading__list">
        <li v-for="item in pendingItems" :key="item.key">
          <strong>{{ item.title }}</strong>
          <span>{{ loadLine(item) }}</span>
        </li>
      </ul>
    </div>

    <RouterView :key="`${ws.id}:${dataVersion}`" />
  </template>
</template>

<script setup>
import { computed, onBeforeUnmount, provide, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { Lock } from '@lucide/vue'
import { ApiError, errorText, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { jobPercent, jobText, stageErrorText, stageFailure, stageProblem } from '../../composables/useLoads'
import { loadWorkspaces, patchWorkspaceInList, rememberWorkspace } from '../../composables/useWorkspaces'
import { useHead } from '../../utils/useHead'
import { pct, plural, rubles } from '../../utils/format'
import { WS_KEY } from './context'

const route = useRoute()
const router = useRouter()
const ws = ref(null)
const error = ref('')
// Проект сверх лимита тарифа: открыть нельзя, но можно освободить место архивом.
const locked = ref(false)
const archiving = ref(false)

useHead('Проект — Толк', true)
watch(
  () => ws.value?.name,
  (name) => {
    if (name) document.title = `${name} — Толк`
  },
)

const siteUrl = computed(() => {
  const site = (ws.value?.site || '').trim()
  if (!site) return ''
  return /^https?:\/\//i.test(site) ? site : `https://${site}`
})

async function load(id) {
  error.value = ''
  locked.value = false
  try {
    const data = await wsApi.get(id)
    if (Number(route.params.wsId) !== id) return
    ws.value = data
    rememberWorkspace(id)
  } catch (err) {
    ws.value = null
    locked.value = err instanceof ApiError && err.code === 'project-locked'
    error.value = errorText(err)
  }
}

async function archiveLocked() {
  archiving.value = true
  try {
    await wsApi.update(Number(route.params.wsId), { archived: true })
    pushToast('Проект в архиве — вернуть его можно в «Подключениях»')
    await loadWorkspaces(true).catch(() => null)
    router.replace('/cabinet')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    archiving.value = false
  }
}

function reload() {
  return load(Number(route.params.wsId))
}

/** Вкладка сохранила изменения проекта — обновляем шапку и переключатель без запроса. */
function patch(updated) {
  if (!updated || !ws.value || updated.id !== ws.value.id) return
  ws.value = { ...ws.value, ...updated }
  patchWorkspaceInList(updated)
}

provide(WS_KEY, { ws, reload, patch })

// --- Загрузка данных кабинетов и счётчика проекта ---

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
