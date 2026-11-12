<template>
  <HomeAsk :ws-id="ws?.id" />

  <div v-if="loading && !data" class="panel"><p class="empty">Собираем сводку…</p></div>
  <div v-else-if="!data" class="panel">
    <p class="empty">{{ loadError || 'Сводка недоступна.' }}</p>
    <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
  </div>

  <!-- Простой режим: решения, сигналы проекта, неделя в цифрах и главные кампании. -->
  <template v-else-if="!expert">
    <PendingDecisions :ws-id="ws.id" />
    <SignalCards :problems="data.problems" :ws-id="ws.id" />

    <h2 class="section-h">
      За неделю
      <small class="home-link"><RouterLink :to="`/w/${ws.id}/analytics`">Все отчёты</RouterLink></small>
    </h2>
    <div class="bigstats">
      <div v-for="t in weekTiles" :key="t.label" class="bigstat">
        <span>{{ t.label }}</span>
        <b>{{ t.value }}</b>
        <small :class="t.tone">
          <RouterLink v-if="t.settings" :to="`/w/${ws.id}/settings`">{{ t.note }}</RouterLink>
          <template v-else>{{ t.note }}</template>
        </small>
      </div>
    </div>

    <h2 class="section-h">
      Кампании проекта
      <small class="home-link"><RouterLink :to="`/w/${ws.id}/campaigns`">Все кампании</RouterLink></small>
    </h2>
    <div v-if="topCampaigns.length" class="ccard">
      <div v-for="c in topCampaigns" :key="c.campaign_row_id" class="crow">
        <div class="crow__name">
          <RouterLink :to="`/w/${ws.id}/campaigns/${c.campaign_row_id}`">{{ c.name }}</RouterLink>
          <small>{{ CAMPAIGN_STATES[c.state] || c.state }}</small>
        </div>
        <div class="crow__health">{{ c.health.text }}</div>
        <div class="crow__num">
          <b>{{ rub(c.cost_kopecks) }}</b>
          <small>{{ num(c.conversions) }} {{ convWord(c.conversions) }}</small>
        </div>
        <span class="st" :class="`st--${c.health.id}`">
          <component :is="HEALTH_BADGE[c.health.id].icon" :size="14" aria-hidden="true" />
          {{ HEALTH_BADGE[c.health.id].text }}
        </span>
      </div>
    </div>
    <div v-else class="ccard ccard--note">За неделю в проекте не было расхода.</div>
    <ExpertHint>Расход за сегодня, бюджет месяца, график недели к неделе и журнал действий</ExpertHint>
  </template>

  <template v-else>
    <PageHeader>
      <template #actions>
        <button class="btn btn--ghost" type="button" :disabled="refreshing" @click="refresh">
          <RefreshCw aria-hidden="true" />
          {{ refreshing ? 'Обновляем…' : 'Обновить расход и балансы' }}
        </button>
      </template>
    </PageHeader>
    <p v-if="refreshing" class="sync-status overview-updated">{{ refreshText }}</p>

    <div class="ktiles ktiles--six">
      <KpiTile label="Сегодня" :value="rub(row.spend_today)" :note="asOfNote(data.today_updated_at)" />
      <KpiTile
        label="Расход 7 дней"
        :value="rub(week.totals.cost_kopecks)"
        :change="week.compare.cost_kopecks"
        versus="к прошлым 7 дням"
        note="к прошлым 7 дням"
        :points="week.by_day"
        :prev-points="week.prev_by_day"
        value-key="cost_kopecks"
      />
      <KpiTile
        label="Конверсии 7 дней"
        :value="num(week.totals.conversions || 0)"
        :change="week.compare.conversions"
        good="up"
        versus="к прошлым 7 дням"
        :note="conversionsSource"
        :points="week.by_day"
        :prev-points="week.prev_by_day"
        value-key="conversions"
      />
      <KpiTile
        :label="`${isDrr ? 'ДРР' : 'CPA'} 7 дней`"
        :value="week.kpi?.type ? kpiValue(week.kpi) : rub(week.totals.cpa_kopecks)"
        :change="week.compare[isDrr ? 'drr' : 'cpa_kopecks']"
        good="down"
        versus="к прошлым 7 дням"
        :note="week.kpi?.type ? kpiGoal(week.kpi) : ''"
        :note-tone="KPI_TONES[week.kpi?.status] || ''"
        :points="isDrr ? [] : week.by_day"
        :prev-points="week.prev_by_day"
        value-key="cpa_kopecks"
      >
        <template v-if="!week.kpi?.type" #note>
          <RouterLink :to="`/w/${ws.id}/settings`">задать цель</RouterLink>
        </template>
      </KpiTile>
      <KpiTile
        label="Бюджет месяца"
        :value="row.budget ? rub(row.budget.spent_kopecks) : '—'"
        :note="budgetNote"
        :note-tone="row.budget ? BUDGET_TONES[row.budget.status] || '' : ''"
      >
        <template v-if="!row.budget" #note>
          <RouterLink :to="`/w/${ws.id}/settings`">задать бюджет</RouterLink>
        </template>
      </KpiTile>
      <KpiTile
        label="Баланс кабинетов"
        :value="row.balance_kopecks != null ? rub(row.balance_kopecks) : '—'"
        :note="row.days_on_balance != null ? `хватит ≈ на ${daysShort(row.days_on_balance)}` : 'нет данных о расходе'"
        :note-tone="balanceTone(row.days_on_balance)"
      />
    </div>

    <section class="panel">
      <div class="panel__head">
        <div>
          <h2>Неделя к неделе</h2>
          <p class="hint">{{ formatDate(week.date_from) }} — {{ formatDate(week.date_to) }} против предыдущих 7 дней.</p>
        </div>
        <div class="filters filters--inline">
          <UiSelect
            v-model="chartKey"
            size="sm"
            aria-label="Метрика графика"
            :options="CHART_METRICS.map((key) => ({ value: key, label: METRIC_BY_KEY[key].label }))"
          />
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
          <span class="metric-grid__cmp">
            <DeltaChip
              v-if="week.compare[m.key] != null"
              :value="week.compare[m.key]"
              :good="m.good || ''"
              versus="к прошлым 7 дням"
            />
            <small v-if="week.previous && week.previous[m.key] != null">было {{ m.fmt(week.previous[m.key]) }}</small>
          </span>
        </button>
      </div>
    </section>

    <section class="panel">
      <div class="panel__head">
        <h2>Что требует внимания <span class="cgroup__n">{{ data.problems.length }}</span></h2>
        <RouterLink
          v-if="data.problems.length"
          class="btn btn--ghost btn--sm"
          :to="{ path: `/w/${ws.id}/chat`, query: { ask: 'problems' } }"
        >
          <Sparkles aria-hidden="true" />
          Разобрать с ИИ
        </RouterLink>
      </div>
      <p v-if="!data.problems.length" class="empty">Ничего не горит: токены живы, бюджет и KPI в норме.</p>
      <ProblemList v-else :problems="data.problems" />
    </section>

    <div class="split">
      <section class="panel">
        <div class="panel__head">
          <h2>Топ кампаний за неделю</h2>
          <RouterLink class="btn btn--ghost" :to="`/w/${ws.id}/campaigns`">Все кампании</RouterLink>
        </div>
        <p v-if="!data.top_campaigns.length" class="empty">
          Нет расхода за неделю. Загрузите статистику в разделе
          <RouterLink :to="`/w/${ws.id}/analytics`">«Отчёты»</RouterLink>.
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
          <RouterLink class="btn btn--ghost" :to="`/w/${ws.id}/history`">История</RouterLink>
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
import {
  CircleCheck,
  CirclePause,
  CircleDashed,
  OctagonAlert,
  RefreshCw,
  Sparkles,
  TriangleAlert,
} from '@lucide/vue'
import DeltaChip from '../../components/DeltaChip.vue'
import ExpertHint from '../../components/ExpertHint.vue'
import HomeAsk from '../../components/HomeAsk.vue'
import KpiTile from '../../components/KpiTile.vue'
import PendingDecisions from '../../components/PendingDecisions.vue'
import ProblemList from '../../components/ProblemList.vue'
import SignalCards from '../../components/SignalCards.vue'
import TrendChart from '../../components/TrendChart.vue'
import PageHeader from '../../components/ui/PageHeader.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import { useExpert } from '../../composables/useExpert'
import { campaignHealth, convWord } from '../../utils/campaignHealth'
import { errorText, jobProgressText, webApi, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import {
  ACTION_SOURCES,
  ACTION_STATUS_TONES,
  BUDGET_TONES,
  CAMPAIGN_STATES,
  KPI_TONES,
  asOfNote,
  balanceTone,
  daysLong,
  daysShort,
  formatDate,
  formatDateTime,
  kpiGoal,
  kpiTarget,
  kpiValue,
  num,
  pct,
  rub,
} from '../../utils/format'
import { CHART_METRICS, METRICS, METRIC_BY_KEY } from './metrics'
import { useWs } from './context'

const STATUS_LABELS = { ok: 'выполнено', partial: 'частично', error: 'ошибка', failed: 'ошибка' }

const { ws } = useWs()
const { expert } = useExpert()
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
const isDrr = computed(() => week.value.kpi?.type === 'drr')

/** Прогноз округляем: точность до рубля у прогноза ложная. */
function approxRub(kopecks) {
  const step = kopecks >= 1_000_000 ? 100_000 : 10_000
  return `~${rub(Math.round(kopecks / step) * step)}`
}

const budgetNote = computed(() => {
  const b = row.value.budget
  if (!b) return ''
  const of = `из ${rub(b.budget_kopecks)}`
  if (b.status === 'exhausted') return `${of} · исчерпан`
  if ((b.status === 'over' || b.status === 'under') && b.forecast_kopecks != null) {
    return `${of} · к концу месяца ${approxRub(b.forecast_kopecks)}`
  }
  return `${of} · до ${rub(b.daily_allowed_kopecks)}/день`
})

const HEALTH_BADGE = {
  bad: { icon: OctagonAlert, text: 'Расход без заявок' },
  warn: { icon: TriangleAlert, text: 'Дороже цели' },
  ok: { icon: CircleCheck, text: 'Хорошо' },
  idle: { icon: CircleDashed, text: 'Без расхода' },
  off: { icon: CirclePause, text: 'Остановлена' },
}

/** «на 12% больше, чем неделей раньше»; inverse — рост это плохо (расход, цена). */
function versus(value, more, less, inverse = false) {
  if (value == null) return { note: 'неделей раньше не с чем сравнить', tone: '' }
  const v = Math.round(value)
  if (!v) return { note: 'как неделей раньше', tone: '' }
  const bad = inverse ? v > 0 : v < 0
  return { note: `на ${pct(Math.abs(v), 0)} ${v > 0 ? more : less}, чем неделей раньше`, tone: Math.abs(v) < 5 ? '' : bad ? 'is-warn' : 'is-ok' }
}

const weekTiles = computed(() => {
  const w = week.value
  const t = w.totals || {}
  const c = w.compare || {}
  const kpi = w.kpi?.type ? w.kpi : null
  const third =
    kpi?.type === 'drr'
      ? { label: 'Доля расходов на рекламу', value: t.drr != null ? pct(t.drr) : kpiValue(kpi), ...versus(c.drr, 'больше', 'меньше', true) }
      : { label: 'Цена конверсии', value: rub(t.cpa_kopecks), ...versus(c.cpa_kopecks, 'дороже', 'дешевле', true) }
  if (kpi) {
    third.note = kpiTarget(kpi)
    third.tone = kpi.status === 'ok' ? 'is-ok' : kpi.status === 'none' ? '' : 'is-warn'
  } else {
    third.note = 'цель не задана — задать'
    third.settings = true
  }
  const r = row.value
  const days = r.days_on_balance
  return [
    { label: 'Потрачено', value: rub(t.cost_kopecks), ...versus(c.cost_kopecks, 'больше', 'меньше', true) },
    { label: 'Конверсии', value: num(t.conversions || 0), ...versus(c.conversions, 'больше', 'меньше') },
    third,
    {
      label: 'Деньги в Директе',
      value: r.balance_kopecks != null ? rub(r.balance_kopecks) : '—',
      note: days != null ? `хватит примерно на ${daysLong(days)}` : 'нет данных о расходе',
      tone: days != null && days < 5 ? 'is-warn' : '',
    },
  ]
})

/** Три кампании с наибольшим расходом — со статусом против цели проекта. */
const topCampaigns = computed(() => {
  // ДРР по кампаниям сводка не отдаёт, поэтому против цели сверяем только CPA.
  const kpi = week.value.kpi?.type === 'cpa' ? week.value.kpi : null
  return (data.value?.top_campaigns || []).slice(0, 3).map((c) => ({ ...c, health: campaignHealth(c, kpi) }))
})

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
