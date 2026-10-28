<template>
  <div v-if="ready && items.length" ref="root" class="loads-ind" :class="[`is-${tone}`, { 'is-open': open }]">
    <button class="loads-ind__toggle" type="button" :aria-expanded="open" title="Загрузка данных из Яндекса" @click="open = !open">
      <span v-if="busy" class="spinner" aria-hidden="true"></span>
      <span v-else class="loads-ind__dot" aria-hidden="true"></span>
      <span class="loads-ind__label">{{ label }}</span>
    </button>

    <div v-if="open" class="loads-ind__menu">
      <div class="loads-ind__head">
        <strong>Загрузка данных</strong>
        <small>{{ summary }}</small>
      </div>
      <div class="loads-ind__list">
        <div v-for="item in shown" :key="item.key" class="loads-ind__item">
          <div class="loads-ind__row">
            <strong>{{ item.title }}</strong>
            <span class="loads-ind__kind">{{ item.type === 'account' ? 'Директ' : 'Метрика' }}</span>
          </div>
          <template v-if="item.job">
            <div class="load-bar" :class="{ 'is-queued': item.job.status !== 'running' }">
              <span :style="{ width: `${jobPercent(item.job)}%` }"></span>
            </div>
            <small>{{ jobLine(item) }}</small>
          </template>
          <small v-else :class="{ 'is-warn': stageProblem(item) || !item.loaded_at }">{{ statusLine(item) }}</small>
        </div>
        <p v-if="hidden" class="loads-ind__more">и ещё {{ hidden }} — все в «Аккаунтах»</p>
      </div>
      <div class="loads-ind__footer">
        <RouterLink to="/cabinet/accounts" @click="open = false">Кабинеты и счётчики</RouterLink>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { errorDetail } from '../api/web'
import { jobPercent, jobText, stageErrorText, stageProblem, useLoads } from '../composables/useLoads'
import { plural, timeAgo } from '../utils/format'

const MAX_ITEMS = 8

const { ready, items, active, busy, waiting, problems, percent, overview, start, stop } = useLoads()

const root = ref(null)
const open = ref(false)

const unitsWait = computed(() =>
  [...problems.value, ...waiting.value].some((i) =>
    (i.stages || []).some((s) => s.error === 'units' || s.skipped === 'units'),
  ),
)

/** Цвет и подпись кнопки: грузится → ошибки → ждёт баллов → всё загружено. */
const tone = computed(() => {
  if (busy.value) return 'busy'
  if (problems.value.some((i) => stageProblem(i)?.status === 'error' && stageProblem(i)?.error !== 'units')) return 'error'
  if (problems.value.length || waiting.value.length) return 'warn'
  return 'ok'
})

const label = computed(() => {
  if (busy.value) return percent.value == null ? 'Синхронизация' : `Загрузка ${percent.value}%`
  if (tone.value === 'error') return 'Ошибка загрузки'
  if (tone.value === 'warn') return unitsWait.value ? 'Ждём баллы' : 'Ждёт загрузки'
  return 'Данные загружены'
})

const summary = computed(() => {
  const n = active.value.length
  const parts = []
  if (n) parts.push(`грузится ${n} ${plural(n, 'источник', 'источника', 'источников')}`)
  if (overview.value.connections_syncing) parts.push('обновляем список кабинетов')
  if (waiting.value.length) parts.push(`ждут: ${waiting.value.length}`)
  if (!parts.length) parts.push('кампании — каждый час, статистика — каждые 6 часов')
  return parts.join(' · ')
})

/** Сначала то, что грузится, потом проблемы и ожидание, потом остальное. */
const ordered = computed(() => {
  const rank = (i) => (i.job ? 0 : stageProblem(i) ? 1 : !i.loaded_at ? 2 : 3)
  return [...items.value].sort((a, b) => rank(a) - rank(b))
})
const shown = computed(() => ordered.value.slice(0, MAX_ITEMS))
const hidden = computed(() => Math.max(0, ordered.value.length - MAX_ITEMS))

function jobLine(item) {
  const job = item.job
  if (job.status !== 'running' && job.error_code) return `повтор после ошибки: ${errorDetail(job.error_code)}`
  return item.loaded_at && job.status === 'running' ? `актуализация · ${jobText(job)}` : jobText(job)
}

function lastAt(item) {
  return (item.stages || []).map((s) => s.at).filter(Boolean).sort().pop() || item.loaded_at
}

function statusLine(item) {
  const problem = stageProblem(item)
  if (problem) {
    if (problem.error === 'units' || problem.skipped === 'units') return `${problem.title}: ждём баллы API Директа`
    return `${problem.title}: ${stageErrorText(problem)}`
  }
  if (!item.loaded_at) return 'ещё не загружен — начнём в ближайший час'
  return `обновлено ${timeAgo(lastAt(item))}`
}

function onDocClick(event) {
  if (open.value && root.value && !root.value.contains(event.target)) open.value = false
}

onMounted(() => {
  start()
  document.addEventListener('click', onDocClick)
})
onBeforeUnmount(() => {
  stop()
  document.removeEventListener('click', onDocClick)
})
</script>
