<template>
  <div v-if="loading && !data" class="panel"><p class="empty">Собираем сводку…</p></div>
  <div v-else-if="!data" class="panel">
    <p class="empty">{{ loadError || 'Сводка недоступна.' }}</p>
    <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
  </div>

  <template v-else>
    <div class="ws-toolbar">
      <p class="sync-status">
        <template v-if="refreshing">{{ refreshText }}</template>
        <template v-else-if="data.today_updated_at">
          Расход за сегодня — на {{ formatDateTime(data.today_updated_at) }}. Неделя: {{ formatDate(week.date_from) }} —
          {{ formatDate(week.date_to) }}
        </template>
        <template v-else>Расход за сегодня ещё не загружался.</template>
      </p>
      <button class="btn btn--ghost" type="button" :disabled="refreshing" @click="refresh">
        {{ refreshing ? 'Обновляем…' : 'Обновить расход и балансы' }}
      </button>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-card__head">
          <span class="stat-card__icon"><CalendarClock aria-hidden="true" /></span>
          <small>Сегодня</small>
        </div>
        <strong>{{ rub(row.spend_today) }}</strong>
        <span class="cell-note">вчера {{ rub(row.spend_yesterday) }}</span>
      </div>
      <div class="stat-card">
        <div class="stat-card__head">
          <span class="stat-card__icon is-blue"><Wallet aria-hidden="true" /></span>
          <small>Расход 7 дней</small>
        </div>
        <div class="stat-card__value">
          <strong>{{ rub(week.totals.cost_kopecks) }}</strong>
          <span v-if="week.compare.cost_kopecks != null" class="delta" :class="deltaTone(week.compare.cost_kopecks, true)">
            {{ delta(week.compare.cost_kopecks) }}
          </span>
        </div>
        <span class="cell-note">к прошлой неделе</span>
        <Sparkline :points="week.by_day" :prev-points="week.prev_by_day" value-key="cost_kopecks" color="var(--c-blue)" />
      </div>
      <div class="stat-card">
        <div class="stat-card__head">
          <span class="stat-card__icon"><Target aria-hidden="true" /></span>
          <small>Конверсии 7 дней</small>
        </div>
        <div class="stat-card__value">
          <strong>{{ num(week.totals.conversions) }}</strong>
          <span v-if="week.compare.conversions != null" class="delta" :class="deltaTone(week.compare.conversions)">
            {{ delta(week.compare.conversions) }}
          </span>
        </div>
        <span class="cell-note">{{ conversionsSource }}</span>
        <Sparkline :points="week.by_day" :prev-points="week.prev_by_day" value-key="conversions" />
      </div>
      <div class="stat-card">
        <div class="stat-card__head">
          <span class="stat-card__icon is-amber">
            <BadgePercent v-if="week.kpi?.type === 'drr'" aria-hidden="true" />
            <Crosshair v-else aria-hidden="true" />
          </span>
          <small>{{ week.kpi?.type === 'drr' ? 'ДРР' : 'CPA' }} 7 дней</small>
        </div>
        <strong :class="KPI_TONES[week.kpi?.status]">
          {{ week.kpi?.type ? kpiValue(week.kpi) : rub(week.totals.cpa_kopecks) }}
        </strong>
        <span class="cell-note">
          <RouterLink v-if="!week.kpi?.type" :to="`/w/${ws.id}/settings`">задать KPI</RouterLink>
          <template v-else>{{ kpiTarget(week.kpi) }}</template>
        </span>
        <Sparkline
          v-if="week.kpi?.type !== 'drr'"
          :points="week.by_day"
          :prev-points="week.prev_by_day"
          value-key="cpa_kopecks"
          color="var(--c-amber)"
        />
      </div>
      <div class="stat-card">
        <div class="stat-card__head">
          <span class="stat-card__icon is-teal"><PiggyBank aria-hidden="true" /></span>
          <small>Бюджет месяца</small>
        </div>
        <template v-if="row.budget">
          <strong :class="BUDGET_TONES[row.budget.status]">
            {{ row.budget.pace_pct != null ? `${row.budget.pace_pct}%` : rub(row.budget.spent_kopecks) }}
          </strong>
          <span class="cell-note">
            {{ rub(row.budget.spent_kopecks) }} из {{ rub(row.budget.budget_kopecks) }} · до
            {{ rub(row.budget.daily_allowed_kopecks) }}/день
          </span>
        </template>
        <template v-else>
          <strong>—</strong>
          <span class="cell-note"><RouterLink :to="`/w/${ws.id}/settings`">задать бюджет</RouterLink></span>
        </template>
      </div>
      <div class="stat-card">
        <div class="stat-card__head">
          <span class="stat-card__icon is-blue"><Landmark aria-hidden="true" /></span>
          <small>Баланс кабинетов</small>
        </div>
        <strong>{{ row.balance_kopecks != null ? rub(row.balance_kopecks) : '—' }}</strong>
        <span class="cell-note" :class="balanceTone(row.days_on_balance)">
          {{ row.days_on_balance != null ? `хватит ≈ на ${row.days_on_balance} дн.` : 'нет данных о расходе' }}
        </span>
      </div>
    </div>

    <section class="panel">
      <div class="panel__head">
        <div>
          <h2>Неделя к неделе</h2>
          <p class="hint">{{ formatDate(week.date_from) }} — {{ formatDate(week.date_to) }} против предыдущих 7 дней.</p>
        </div>
        <div class="filters filters--inline">
          <select v-model="chartKey" aria-label="Метрика графика">
            <option v-for="key in CHART_METRICS" :key="key" :value="key">{{ METRIC_BY_KEY[key].label }}</option>
          </select>
        </div>
      </div>
      <TrendChart
        :points="week.by_day"
        :prev-points="week.prev_by_day"
        :value-key="chartKey"
        :label="METRIC_BY_KEY[chartKey].label"
        :format-value="METRIC_BY_KEY[chartKey].fmt"
      />
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
          <strong>{{ m.fmt(week.totals[m.key]) }}</strong>
          <span :class="deltaTone(week.compare[m.key], m.inverse)">
            {{ delta(week.compare[m.key]) }}
            <template v-if="week.previous && week.previous[m.key] != null">
              <small>было {{ m.fmt(week.previous[m.key]) }}</small>
            </template>
          </span>
        </button>
      </div>
    </section>

    <section class="panel">
      <div class="panel__head">
        <h2>Что требует внимания</h2>
        <span class="chip" :class="data.problems.length ? 'chip--warning' : 'chip--ok'">
          {{ data.problems.length ? `${data.problems.length}` : 'всё спокойно' }}
        </span>
      </div>
      <p v-if="!data.problems.length" class="empty">Ничего не горит: токены живы, бюджет и KPI в норме.</p>
      <div v-else class="list">
        <div
          v-for="(p, i) in data.problems"
          :key="`${p.kind}-${p.account_id}-${p.campaign_row_id}-${i}`"
          class="list-row problem"
          :class="`is-${p.severity}`"
        >
          <div>
            <span class="chip" :class="`chip--${p.severity}`">{{ SEVERITY_LABELS[p.severity] }}</span>
            <strong>{{ p.title }}</strong>
            <small>
              {{ p.object_name }}
              <template v-if="p.detail">{{ p.object_name ? ' · ' : '' }}{{ errorDetail(p.detail) }}</template>
            </small>
          </div>
          <RouterLink v-if="problemLink(p)" class="btn btn--ghost" :to="problemLink(p).to">
            {{ problemLink(p).label }}
          </RouterLink>
        </div>
      </div>
      <div class="row-actions">
        <RouterLink class="btn btn--ghost" :to="{ path: `/w/${ws.id}/chat`, query: { ask: 'problems' } }">
          Разобрать с ИИ
        </RouterLink>
      </div>
    </section>

    <div class="split">
      <section class="panel">
        <div class="panel__head">
          <h2>Топ кампаний за неделю</h2>
          <RouterLink class="btn btn--ghost" :to="`/w/${ws.id}/campaigns`">Все кампании</RouterLink>
        </div>
        <p v-if="!data.top_campaigns.length" class="empty">
          Нет расхода за неделю. Загрузите статистику на вкладке
          <RouterLink :to="`/w/${ws.id}/analytics`">«Аналитика»</RouterLink>.
        </p>
        <div v-else class="list">
          <RouterLink
            v-for="c in data.top_campaigns"
            :key="c.campaign_row_id"
            class="list-row is-link"
            :to="`/w/${ws.id}/campaigns/${c.campaign_row_id}`"
          >
            <div>
              <strong>{{ c.name }}</strong>
              <small>{{ c.account_login }} · {{ CAMPAIGN_STATES[c.state] || c.state }}</small>
            </div>
            <div class="list-row__nums">
              <strong>{{ rub(c.cost_kopecks) }}</strong>
              <small>{{ num(c.conversions) }} конв. · CPA {{ rub(c.cpa_kopecks) }}</small>
            </div>
          </RouterLink>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head">
          <h2>Последние действия</h2>
          <RouterLink class="btn btn--ghost" :to="`/w/${ws.id}/actions`">Журнал</RouterLink>
        </div>
        <p v-if="!data.recent_actions.length" class="empty">Пока ничего не меняли.</p>
        <div v-else class="list">
          <div v-for="a in data.recent_actions" :key="a.id" class="list-row">
            <div>
              <strong>{{ a.title || a.action }}</strong>
              <small>
                <span class="chip" :class="`chip--src-${a.source}`">{{ ACTION_SOURCES[a.source] || a.source }}</span>
                {{ formatDateTime(a.created_at) }}
              </small>
            </div>
            <span :class="ACTION_STATUS_TONES[a.status]">{{ STATUS_LABELS[a.status] || a.status }}</span>
          </div>
        </div>
      </section>
    </div>
  </template>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { BadgePercent, CalendarClock, Crosshair, Landmark, PiggyBank, Target, Wallet } from '@lucide/vue'
import Sparkline from '../../components/Sparkline.vue'
import TrendChart from '../../components/TrendChart.vue'
import { errorDetail, errorText, jobProgressText, webApi, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import {
  ACTION_SOURCES,
  ACTION_STATUS_TONES,
  BUDGET_TONES,
  CAMPAIGN_STATES,
  KPI_TONES,
  SEVERITY_LABELS,
  balanceTone,
  delta,
  deltaTone,
  formatDate,
  formatDateTime,
  kpiTarget,
  kpiValue,
  num,
  problemLink,
  rub,
} from '../../utils/format'
import { CHART_METRICS, METRICS, METRIC_BY_KEY } from './metrics'
import { useWs } from './context'

const STATUS_LABELS = { ok: 'выполнено', partial: 'частично', error: 'ошибка', failed: 'ошибка' }

const { ws } = useWs()
const data = ref(null)
const loading = ref(false)
const loadError = ref('')
const refreshing = ref(false)
const refreshText = ref('')
const chartKey = ref('cost_kopecks')

const row = computed(() => data.value?.row || {})
const week = computed(() => data.value?.week || {})
const conversionsSource = computed(() =>
  week.value.conversions_source === 'goal' ? `цель «${week.value.goal_name}»` : 'конверсии Директа',
)

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await wsApi.summary(ws.value.id)
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  refreshText.value = 'Запрашиваем расход за сегодня и балансы…'
  try {
    const result = await webApi.overviewRefresh((job) => {
      refreshText.value = `Обновляем: ${jobProgressText(job)}`
    })
    await load()
    const errors = (result.direct?.errors || 0) + (result.balances?.errors || 0)
    pushToast(errors ? `Обновлено, но с ошибками: ${errors}` : 'Расход и балансы обновлены', errors ? 'error' : 'ok')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    refreshing.value = false
  }
}

onMounted(load)
</script>
