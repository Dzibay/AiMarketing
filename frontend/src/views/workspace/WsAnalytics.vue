<template>
  <SimpleReport
    v-if="!expert"
    :data="data"
    :loading="loading"
    :error="loadError"
    :period="preset"
    :campaigns="simpleCampaigns"
    :chat-link="{ path: `/w/${ws.id}/chat`, query: { ask: 'analytics', from: range.date_from, to: range.date_to } }"
    hint="Сравнение с прошлым периодом, все метрики, ключевые фразы, запросы и загрузка статистики"
    @period="setPreset"
  />

  <template v-else>
  <div class="ws-toolbar report-bar">
    <UiDateRange :from="range.date_from" :to="range.date_to" :preset="preset" @change="setRange">
      <TermHint title="Период" :text="periodHint" />
    </UiDateRange>
    <button class="btn btn--ghost btn--sm" type="button" :disabled="syncing" @click="syncStats">
      <RefreshCw aria-hidden="true" />
      {{ syncing ? 'Загружаем…' : `Загрузить статистику за ${syncDays} дн.` }}
    </button>
  </div>
  <p class="sync-status report-sync">
    <template v-if="syncing">{{ syncText }}</template>
    <template v-else-if="data">
      Директ обновлён {{ data.direct_updated_at ? formatDateTime(data.direct_updated_at) : 'ещё не загружался' }},
      Метрика — {{ data.metrika_updated_at ? formatDateTime(data.metrika_updated_at) : 'ещё не загружалась' }}.
    </template>
  </p>

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
      <MetricGrid
        v-model="chartKey"
        :totals="data.totals"
        :previous="data.previous"
        :compare="data.compare"
        :metrika="!!(data.counter_ok && data.metrika_updated_at)"
      >
        <template #no-metrika>
          <span v-if="!data.counter_ok">
            <RouterLink to="/cabinet/connections">Подключите Метрику</RouterLink>, чтобы видеть визиты
          </span>
          <span v-else>Визиты появятся после первой загрузки Метрики</span>
        </template>
      </MetricGrid>
      <p v-if="data.kpi?.type" class="hint">
        KPI: <strong :class="KPI_TONES[data.kpi.status]">{{ kpiValue(data.kpi) }}</strong> · {{ kpiTarget(data.kpi) }}
      </p>
      <TrendChart
        :points="data.by_day"
        :prev-points="data.prev_by_day"
        :value-key="chartKey"
        :label="METRIC_BY_KEY[chartKey].label"
        :format-value="METRIC_BY_KEY[chartKey].fmt"
      />
    </section>

    <section class="panel">
      <div class="panel__head">
        <h2>Кампании</h2>
        <input v-model.trim="campaignSearch" type="search" class="search-input" placeholder="Найти кампанию" aria-label="Поиск кампании" />
      </div>
      <p v-if="!data.by_campaign.length" class="empty">
        В проекте нет кампаний —
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
              <th class="num">CPA<TermHint term="cpa" /></th>
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
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { RefreshCw } from '@lucide/vue'
import MetricGrid from '../../components/MetricGrid.vue'
import TrendChart from '../../components/TrendChart.vue'
import TermHint from '../../components/TermHint.vue'
import SimpleReport from '../../components/SimpleReport.vue'
import UiDateRange from '../../components/ui/UiDateRange.vue'
import { useExpert } from '../../composables/useExpert'
import { errorText, jobProgressText, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import {
  CAMPAIGN_STATES,
  KPI_TONES,
  formatDate,
  formatDateTime,
  kpiTarget,
  kpiValue,
  lastDays,
  monthToDate,
  num,
  rub,
  rubles,
} from '../../utils/format'
import { METRIC_BY_KEY } from './metrics'
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

const { expert } = useExpert()
const simpleCampaigns = computed(() =>
  (data.value?.by_campaign || []).map((c) => ({ ...c, link: `/w/${ws.value.id}/campaigns/${c.campaign_row_id}` })),
)

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
  Object.assign(range, n === 'month' ? monthToDate() : lastDays(n))
  load()
}

function setRange({ date_from, date_to, preset: p }) {
  preset.value = p
  Object.assign(range, { date_from, date_to })
  load()
}

const periodHint = computed(() => {
  const conv =
    data.value?.conversions_source === 'goal'
      ? `по цели Метрики «${data.value.goal_name}»`
      : 'из Директа; цель Метрики можно выбрать в настройках проекта'
  return `Цифры сравниваются с предыдущим периодом той же длины. Конверсии — ${conv}. «Месяц» — с первого числа по вчера.`
})

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

<style scoped>
.report-bar {
  margin-bottom: 0.35rem;
}


.report-sync {
  margin: 0 0 1rem;
}
</style>
