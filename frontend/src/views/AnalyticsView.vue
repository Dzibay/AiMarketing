<template>
  <template v-if="!expert">
    <p v-if="data && !data.projects.length" class="empty">
      Проектов пока нет. Подключите Директ и Метрику в
      <RouterLink to="/cabinet/connections">подключениях</RouterLink> — проекты появятся по счётчикам.
    </p>
    <SimpleReport
      v-else
      :data="data"
      :loading="loading"
      :error="loadError"
      :period="preset"
      :campaigns="simpleCampaigns"
      :chat-link="{ path: '/cabinet/chat', query: { ask: 'analytics' } }"
      hint="Сравнение с прошлым периодом, все метрики, проекты и кампании таблицами"
      @period="setPreset"
    />
  </template>

  <template v-else>
  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Период</h2>
        <p class="hint">
          Сумма всех проектов против предыдущего периода той же длины. Конверсии каждый проект считает по своей
          цели (или по Директу) — в сумме они складываются как есть. Площадки и объявления — внутри проекта.
        </p>
      </div>
    </div>
    <div class="filters filters--inline">
      <div class="seg" role="group" aria-label="Быстрый период">
        <button v-for="n in [7, 14, 30]" :key="n" type="button" :class="{ active: preset === n }" @click="setPreset(n)">
          {{ n }} дн.
        </button>
      </div>
      <div v-field class="field">
        <label>С</label>
        <input v-model="range.date_from" type="date" :max="range.date_to" @change="customRange" />
      </div>
      <div v-field class="field">
        <label>По</label>
        <input v-model="range.date_to" type="date" :min="range.date_from" @change="customRange" />
      </div>
    </div>
  </section>

  <div v-if="loading && !data" class="panel"><p class="empty">Считаем…</p></div>
  <div v-else-if="loadError" class="panel"><p class="empty">{{ loadError }}</p></div>
  <div v-else-if="data && !data.projects.length" class="panel">
    <p class="empty">
      Проектов пока нет. Подключите Директ и Метрику в
      <RouterLink to="/cabinet/connections">подключениях</RouterLink> — проекты появятся по счётчикам.
    </p>
  </div>

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
          :aria-pressed="chartKey === m.key"
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
      <TrendChart
        :points="data.by_day"
        :prev-points="data.prev_by_day"
        :value-key="chartKey"
        :label="METRIC_BY_KEY[chartKey].label"
        :format-value="METRIC_BY_KEY[chartKey].fmt"
      />
    </section>

    <section class="panel" :class="{ 'is-loading': loading }">
      <div class="panel__head">
        <h2>Проекты</h2>
        <span class="hint">Линия — расход по дням. Откройте проект — там площадки, объявления и запросы.</span>
      </div>
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>Проект</th>
              <th class="spark-col">Расход по дням</th>
              <th class="num">Расход</th>
              <th class="num">Клики</th>
              <th class="num">Конверсии</th>
              <th class="num">CPA<TermHint term="cpa" /></th>
              <th class="num">ДРР<TermHint term="drr" /></th>
              <th>KPI</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in data.projects" :key="p.id">
              <td>
                <RouterLink :to="`/w/${p.id}/analytics`">{{ p.name }}</RouterLink>
                <small class="cell-note">
                  {{ [p.client_name, p.site].filter(Boolean).join(' · ') }}<template v-if="p.client_name || p.site"> · </template
                  >{{ p.campaigns }} {{ plural(p.campaigns, 'кампания', 'кампании', 'кампаний') }} ·
                  {{ p.conversions_source === 'goal' ? `цель «${p.goal_name}»` : 'конверсии Директа' }}
                </small>
              </td>
              <td class="spark-col">
                <Sparkline :points="p.spark.map((v) => ({ cost_kopecks: v }))" value-key="cost_kopecks" color="var(--c-blue)" :height="32" />
              </td>
              <td class="num">
                {{ rub(p.totals.cost_kopecks) }}
                <small class="cell-note" :class="deltaTone(p.compare.cost_kopecks, true)">{{ delta(p.compare.cost_kopecks) }}</small>
              </td>
              <td class="num">{{ num(p.totals.clicks) }}</td>
              <td class="num" :class="{ 'tone-bad': !p.totals.conversions && p.totals.cost_kopecks > 0 }">
                {{ num(p.totals.conversions) }}
                <small class="cell-note" :class="deltaTone(p.compare.conversions)">{{ delta(p.compare.conversions) }}</small>
              </td>
              <td class="num">{{ p.totals.cpa_kopecks != null ? rub(p.totals.cpa_kopecks) : '—' }}</td>
              <td class="num">{{ p.totals.drr != null ? pct(p.totals.drr) : '—' }}</td>
              <td>
                <template v-if="p.kpi?.type">
                  <strong :class="KPI_TONES[p.kpi.status]">{{ kpiValue(p.kpi) }}</strong>
                  <small class="cell-note">{{ kpiTarget(p.kpi) }}</small>
                </template>
                <RouterLink v-else class="cell-note" :to="`/w/${p.id}/settings`">задать KPI</RouterLink>
              </td>
            </tr>
          </tbody>
          <tfoot v-if="data.projects.length > 1">
            <tr>
              <th>Итого</th>
              <th></th>
              <th class="num">{{ rub(data.totals.cost_kopecks) }}</th>
              <th class="num">{{ num(data.totals.clicks) }}</th>
              <th class="num">{{ num(data.totals.conversions) }}</th>
              <th class="num">{{ data.totals.cpa_kopecks != null ? rub(data.totals.cpa_kopecks) : '—' }}</th>
              <th class="num">{{ data.totals.drr != null ? pct(data.totals.drr) : '—' }}</th>
              <th></th>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>

    <section v-if="topCampaigns.length" class="panel" :class="{ 'is-loading': loading }">
      <div class="panel__head">
        <h2>Что тратит деньги</h2>
        <RouterLink class="btn btn--ghost" to="/cabinet/campaigns">Все кампании</RouterLink>
      </div>
      <p class="hint">Десять кампаний с самым большим расходом за период по всем проектам.</p>
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>Кампания</th>
              <th>Проект</th>
              <th class="num">Расход</th>
              <th class="num">Клики</th>
              <th class="num">Конверсии</th>
              <th class="num">CPA<TermHint term="cpa" /></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in topCampaigns" :key="c.campaign_row_id" :class="{ 'is-off': c.state !== 'ON' }">
              <td>
                <RouterLink :to="`/w/${c.project.id}/campaigns/${c.campaign_row_id}`">{{ c.name }}</RouterLink>
                <small class="cell-note">{{ c.account_login }} · {{ CAMPAIGN_STATES[c.state] || c.state }}</small>
              </td>
              <td>{{ c.project.name }}</td>
              <td class="num">{{ rub(c.cost_kopecks) }}</td>
              <td class="num">{{ num(c.clicks) }}</td>
              <td class="num" :class="{ 'tone-bad': !c.conversions && c.cost_kopecks > 0 }">{{ num(c.conversions) }}</td>
              <td class="num">{{ c.cpa_kopecks != null ? rub(c.cpa_kopecks) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="row-actions">
        <RouterLink class="btn btn--ghost" :to="{ path: '/cabinet/chat', query: { ask: 'analytics' } }">
          Спросить ИИ о проектах
        </RouterLink>
      </div>
    </section>
  </template>
  </template>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import Sparkline from '../components/Sparkline.vue'
import TermHint from '../components/TermHint.vue'
import TrendChart from '../components/TrendChart.vue'
import SimpleReport from '../components/SimpleReport.vue'
import { useExpert } from '../composables/useExpert'
import { errorText, webApi } from '../api/web'
import {
  CAMPAIGN_STATES,
  KPI_TONES,
  delta,
  deltaTone,
  formatDate,
  kpiTarget,
  kpiValue,
  lastDays,
  monthToDate,
  num,
  pct,
  plural,
  rub,
} from '../utils/format'
import { useHead } from '../utils/useHead'
import { CHART_METRICS, METRICS, METRIC_BY_KEY } from './workspace/metrics'

useHead('Аналитика — Толк', true)

const data = ref(null)
const loading = ref(false)
const loadError = ref('')
const preset = ref(7)
const range = reactive(lastDays(7))
const chartKey = ref('cost_kopecks')
let seq = 0

const { expert } = useExpert()
const simpleCampaigns = computed(() =>
  (data.value?.campaigns || []).map((c) => ({
    ...c,
    link: `/w/${c.project.id}/campaigns/${c.campaign_row_id}`,
    where: c.project.name,
  })),
)

const topCampaigns = computed(() => (data.value?.campaigns || []).filter((c) => c.cost_kopecks > 0).slice(0, 10))

async function load() {
  const my = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const res = await webApi.portfolio({ date_from: range.date_from, date_to: range.date_to })
    if (my === seq) data.value = res
  } catch (err) {
    if (my === seq) loadError.value = errorText(err)
  } finally {
    if (my === seq) loading.value = false
  }
}

function setPreset(n) {
  preset.value = n
  Object.assign(range, n === 'month' ? monthToDate() : lastDays(n))
  load()
}

function customRange() {
  if (!range.date_from || !range.date_to) return
  preset.value = null
  load()
}

onMounted(load)
</script>

<style scoped>
.spark-col {
  width: 130px;
  min-width: 110px;
}
.spark-col :deep(.sparkline) {
  width: 120px;
  height: 32px;
  display: block;
}
</style>
