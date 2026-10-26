<template>
  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Период</h2>
        <p class="hint">
          Сравнение с предыдущим периодом той же длины. Конверсии —
          {{ data?.conversions_source === 'goal' ? `по цели Метрики «${data.goal_name}»` : 'конверсии Директа' }}<template
            v-if="data?.conversions_source !== 'goal'"
          >
            (цель Метрики можно выбрать в <RouterLink :to="`/w/${ws.id}/settings`">настройках</RouterLink>)</template
          >.
        </p>
      </div>
      <button class="btn btn--ghost" type="button" :disabled="syncing" @click="syncStats">
        {{ syncing ? 'Загружаем…' : `Загрузить статистику за ${syncDays} дн.` }}
      </button>
    </div>
    <div class="filters filters--inline">
      <div class="seg" role="group" aria-label="Быстрый период">
        <button
          v-for="n in [7, 14, 30]"
          :key="n"
          type="button"
          :class="{ active: preset === n }"
          @click="setPreset(n)"
        >
          {{ n }} дн.
        </button>
      </div>
      <div class="field">
        <label>С</label>
        <input v-model="range.date_from" type="date" :max="range.date_to" @change="customRange" />
      </div>
      <div class="field">
        <label>По</label>
        <input v-model="range.date_to" type="date" :min="range.date_from" @change="customRange" />
      </div>
    </div>
    <p class="sync-status">
      <template v-if="syncing">{{ syncText }}</template>
      <template v-else-if="data">
        Директ обновлён {{ data.direct_updated_at ? formatDateTime(data.direct_updated_at) : 'ещё не загружался' }},
        Метрика — {{ data.metrika_updated_at ? formatDateTime(data.metrika_updated_at) : 'ещё не загружалась' }}.
      </template>
    </p>
  </section>

  <div v-if="loading && !data" class="panel"><p class="empty">Считаем…</p></div>
  <div v-else-if="loadError" class="panel"><p class="empty">{{ loadError }}</p></div>

  <template v-else-if="data">
    <section class="panel" :class="{ 'is-loading': loading }">
      <div class="panel__head">
        <h2>Итоги</h2>
        <span class="hint">
          {{ formatDate(data.date_from) }} — {{ formatDate(data.date_to) }} против {{ formatDate(data.prev_from) }} —
          {{ formatDate(data.prev_to) }}
        </span>
      </div>
      <div class="metric-grid">
        <button
          v-for="m in METRICS"
          :key="m.key"
          type="button"
          class="metric-grid__item"
          :class="{ active: chartKey === m.key, 'is-static': !CHART_METRICS.includes(m.key) }"
          :disabled="!CHART_METRICS.includes(m.key)"
          @click="chartKey = m.key"
        >
          <small>{{ m.label }}</small>
          <strong>{{ m.fmt(data.totals[m.key]) }}</strong>
          <span :class="deltaTone(data.compare[m.key], m.inverse)">
            {{ delta(data.compare[m.key]) }}
            <small v-if="data.previous && data.previous[m.key] != null">было {{ m.fmt(data.previous[m.key]) }}</small>
          </span>
        </button>
      </div>
      <p v-if="data.kpi?.type" class="hint">
        KPI: <strong :class="KPI_TONES[data.kpi.status]">{{ kpiValue(data.kpi) }}</strong> · {{ kpiTarget(data.kpi) }}
        <template v-if="data.totals.bounce_rate != null"> · отказы {{ pct(data.totals.bounce_rate) }}</template>
      </p>
      <TrendChart
        :points="data.by_day"
        :value-key="chartKey"
        :label="METRIC_BY_KEY[chartKey].label"
        :format-value="METRIC_BY_KEY[chartKey].fmt"
      />
    </section>

    <section class="panel">
      <div class="panel__head">
        <h2>Кампании</h2>
        <input v-model.trim="campaignSearch" type="search" class="search-input" placeholder="Найти кампанию" />
      </div>
      <p v-if="!data.by_campaign.length" class="empty">
        В пространстве нет кампаний —
        <RouterLink :to="`/w/${ws.id}/settings`">добавьте их в настройках</RouterLink>.
      </p>
      <div v-else class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>
                <button class="th-sort" type="button" @click="sortBy('name')">Кампания{{ sortMark('name') }}</button>
              </th>
              <th v-for="col in CAMPAIGN_COLUMNS" :key="col.key" class="num">
                <button class="th-sort" type="button" @click="sortBy(col.key)">
                  {{ col.label }}{{ sortMark(col.key) }}
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in campaigns" :key="c.campaign_row_id" :class="{ 'is-off': c.state !== 'ON' }">
              <td>
                <RouterLink :to="`/w/${ws.id}/campaigns/${c.campaign_row_id}`">{{ c.name }}</RouterLink>
                <small class="cell-note">
                  {{ c.account_login }} · {{ CAMPAIGN_STATES[c.state] || c.state }}
                  <template v-if="c.daily_budget_rub"> · {{ rubles(c.daily_budget_rub) }}/день</template>
                </small>
              </td>
              <td v-for="col in CAMPAIGN_COLUMNS" :key="col.key" class="num">{{ col.fmt(c[col.key]) }}</td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <th>Итого</th>
              <th v-for="col in CAMPAIGN_COLUMNS" :key="col.key" class="num">{{ col.fmt(data.totals[col.key]) }}</th>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>

    <section v-if="data.by_account.length > 1" class="panel">
      <h2>По кабинетам Директа</h2>
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>Кабинет</th>
              <th class="num">Кампаний</th>
              <th v-for="col in ACCOUNT_COLUMNS" :key="col.key" class="num">{{ col.label }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in data.by_account" :key="a.account_id">
              <td>
                {{ a.name || a.login }}
                <small v-if="a.name && a.name !== a.login" class="cell-note">{{ a.login }}</small>
              </td>
              <td class="num">{{ a.campaigns }}</td>
              <td v-for="col in ACCOUNT_COLUMNS" :key="col.key" class="num">{{ col.fmt(a[col.key]) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel__head">
        <h2>Что тратит деньги</h2>
        <div class="seg" role="tablist">
          <button
            v-for="t in TOP_TABS"
            :key="t.key"
            type="button"
            role="tab"
            :class="{ active: topTab === t.key }"
            @click="topTab = t.key"
          >
            {{ t.label }}
          </button>
        </div>
      </div>
      <p class="hint">
        Топ по расходу за период.
        <template v-if="data.tops?.note">{{ data.tops.note }}</template>
      </p>
      <p v-if="!topRows.length" class="empty">
        Нет данных. Статистика по {{ topTab === 'queries' ? 'запросам' : 'структуре' }} подгружается при синке кампании —
        откройте кампанию и обновите её.
      </p>
      <div v-else class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>{{ TOP_TABS.find((t) => t.key === topTab).label }}</th>
              <th class="num">Клики</th>
              <th class="num">Расход</th>
              <th class="num">Конверсии</th>
              <th class="num">CPA</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in topRows" :key="i">
              <td>
                {{ r.name }}
                <small class="cell-note">{{ [r.campaign_name, r.adgroup_name].filter(Boolean).join(' · ') }}</small>
              </td>
              <td class="num">{{ num(r.clicks) }}</td>
              <td class="num">{{ rub(r.cost_kopecks) }}</td>
              <td class="num" :class="{ 'tone-bad': !r.conversions && r.cost_kopecks > 0 }">{{ num(r.conversions) }}</td>
              <td class="num">{{ r.conversions ? rub(Math.round(r.cost_kopecks / r.conversions)) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="row-actions">
        <RouterLink
          class="btn btn--ghost"
          :to="{ path: `/w/${ws.id}/chat`, query: { ask: 'analytics', from: range.date_from, to: range.date_to } }"
        >
          Попросить ИИ проанализировать период
        </RouterLink>
      </div>
    </section>
  </template>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import TrendChart from '../../components/TrendChart.vue'
import { errorText, jobProgressText, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import {
  CAMPAIGN_STATES,
  KPI_TONES,
  delta,
  deltaTone,
  formatDate,
  formatDateTime,
  kpiTarget,
  kpiValue,
  lastDays,
  num,
  pct,
  rub,
  rubles,
} from '../../utils/format'
import { CHART_METRICS, METRICS, METRIC_BY_KEY } from './metrics'
import { useWs } from './context'

const TOP_TABS = [
  { key: 'keywords', label: 'Ключевые фразы' },
  { key: 'queries', label: 'Поисковые запросы' },
  { key: 'adgroups', label: 'Группы' },
]
const CAMPAIGN_COLUMNS = ['cost_kopecks', 'impressions', 'clicks', 'ctr', 'cpc_kopecks', 'conversions', 'cpa_kopecks', 'revenue_kopecks', 'drr', 'visits'].map(
  (key) => METRIC_BY_KEY[key],
)
const ACCOUNT_COLUMNS = ['cost_kopecks', 'clicks', 'conversions', 'cpa_kopecks', 'revenue_kopecks'].map((key) => METRIC_BY_KEY[key])

const { ws } = useWs()
const data = ref(null)
const loading = ref(false)
const loadError = ref('')
const preset = ref(7)
const range = reactive(lastDays(7))
const chartKey = ref('cost_kopecks')
const topTab = ref('keywords')
const campaignSearch = ref('')
const sort = reactive({ key: 'cost_kopecks', desc: true })
const syncing = ref(false)
const syncText = ref('')

const syncDays = computed(() => {
  const from = new Date(`${range.date_from}T00:00:00`)
  const days = Math.round((Date.now() - from.getTime()) / 86400000)
  return Math.min(30, Math.max(1, days))
})

const campaigns = computed(() => {
  const q = campaignSearch.value.toLowerCase()
  const rows = (data.value?.by_campaign || []).filter((c) => !q || c.name.toLowerCase().includes(q))
  const { key, desc } = sort
  return [...rows].sort((a, b) => {
    const av = a[key]
    const bv = b[key]
    if (key === 'name') return desc ? bv.localeCompare(av, 'ru') : av.localeCompare(bv, 'ru')
    if (av == null && bv == null) return 0
    if (av == null) return 1
    if (bv == null) return -1
    return desc ? bv - av : av - bv
  })
})

const topRows = computed(() => data.value?.tops?.[topTab.value] || [])

function sortBy(key) {
  if (sort.key === key) sort.desc = !sort.desc
  else {
    sort.key = key
    sort.desc = key !== 'name'
  }
}

function sortMark(key) {
  if (sort.key !== key) return ''
  return sort.desc ? ' ↓' : ' ↑'
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await wsApi.analytics(ws.value.id, { date_from: range.date_from, date_to: range.date_to })
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

function setPreset(n) {
  preset.value = n
  Object.assign(range, lastDays(n))
  load()
}

function customRange() {
  if (!range.date_from || !range.date_to) return
  preset.value = null
  load()
}

async function syncStats() {
  syncing.value = true
  syncText.value = 'Ставим загрузку в очередь…'
  try {
    await wsApi.syncStats(ws.value.id, syncDays.value, (job) => {
      syncText.value = `Загружаем статистику Директа и Метрики: ${jobProgressText(job)}`
    })
    pushToast('Статистика загружена')
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncing.value = false
  }
}

onMounted(load)
</script>
