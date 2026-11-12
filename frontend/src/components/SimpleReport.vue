<template>
  <div class="seg period-seg" role="group" aria-label="Период отчёта">
    <button
      v-for="p in PERIODS"
      :key="p.key"
      type="button"
      :class="{ active: period === p.key }"
      :aria-pressed="period === p.key"
      @click="emit('period', p.key)"
    >
      {{ p.label }}
    </button>
  </div>

  <p v-if="loading && !data" class="empty">Считаем…</p>
  <p v-else-if="error" class="empty">{{ error }}</p>
  <template v-else-if="data">
    <p class="meta-note simple-report__dates">{{ formatDate(data.date_from) }} — {{ formatDate(data.date_to) }}</p>
    <div class="bigstats" :class="{ 'is-loading': loading }">
      <div v-for="t in tiles" :key="t.label" class="bigstat">
        <span>{{ t.label }}<TermHint v-if="t.term" :term="t.term" /></span>
        <b>{{ t.value }}</b>
        <small :class="t.tone">{{ t.note }}</small>
      </div>
    </div>

    <div class="ccard simple-report__chart">
      <h3 class="section-h">Расход по дням</h3>
      <TrendChart :points="data.by_day" value-key="cost_kopecks" label="Расход" :format-value="rub" :height="190" />
    </div>

    <template v-if="bars.length">
      <h2 class="section-h">На что ушли деньги <small class="cgroup__n">кампании по расходу</small></h2>
      <div class="ccard hbars">
        <div v-for="b in bars" :key="b.key" class="hbar">
          <div class="hbar__name">
            <RouterLink :to="b.link">{{ b.name }}</RouterLink>
            <small class="cell-note">{{ b.note }}</small>
          </div>
          <span class="hbar__track" aria-hidden="true"><i class="hbar__fill" :style="{ width: `${b.share}%` }"></i></span>
          <b class="hbar__val">{{ rub(b.cost) }}</b>
        </div>
      </div>
    </template>

    <div class="row-actions">
      <RouterLink class="btn btn--accent" :to="chatLink">
        <Sparkles :size="16" aria-hidden="true" />
        Попросить ИИ разобрать период
      </RouterLink>
    </div>
    <ExpertHint>{{ hint }}</ExpertHint>
  </template>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { Sparkles } from '@lucide/vue'
import TrendChart from './TrendChart.vue'
import ExpertHint from './ExpertHint.vue'
import TermHint from './TermHint.vue'
import { formatDate, kpiGoal, num, pct, rub } from '../utils/format'
import { leadWord } from '../utils/campaignHealth'

const PERIODS = [
  { key: 7, label: '7 дней' },
  { key: 30, label: '30 дней' },
  { key: 'month', label: 'Этот месяц' },
]

const props = defineProps({
  /** Ответ аналитики: totals, compare, by_day, date_from/date_to; kpi — если цель одна. */
  data: { type: Object, default: null },
  loading: Boolean,
  error: { type: String, default: '' },
  period: { type: [Number, String], default: 7 },
  /** Кампании: name, link, cost_kopecks, conversions, cpa_kopecks, where. */
  campaigns: { type: Array, default: () => [] },
  chatLink: { type: [String, Object], required: true },
  hint: { type: String, default: 'Все метрики, сравнение с прошлым периодом и подробные таблицы' },
})
const emit = defineEmits(['period'])

/** «на 12% больше, чем в прошлом периоде»; рост расхода и цены — жёлтым, падение заявок — тоже. */
function change(value, more, less, inverse = false) {
  if (value == null) return { note: 'прошлый период не с чем сравнить', tone: '' }
  const v = Math.round(value)
  if (!v) return { note: 'как в прошлом периоде', tone: '' }
  const bad = inverse ? v > 0 : v < 0
  return {
    note: `на ${pct(Math.abs(v), 0)} ${v > 0 ? more : less}, чем в прошлом периоде`,
    tone: Math.abs(v) < 5 ? '' : bad ? 'is-warn' : 'is-ok',
  }
}

const tiles = computed(() => {
  const d = props.data
  if (!d) return []
  const t = d.totals || {}
  const c = d.compare || {}
  const kpi = d.kpi?.type ? d.kpi : null
  const drr = kpi?.type === 'drr'
  const third = drr
    ? { label: 'Доля расходов на рекламу', term: 'drr', value: t.drr != null ? pct(t.drr) : '—', ...change(c.drr, 'больше', 'меньше', true) }
    : { label: 'Цена заявки', term: 'cpa', value: t.cpa_kopecks != null ? rub(t.cpa_kopecks) : '—', ...change(c.cpa_kopecks, 'дороже', 'дешевле', true) }
  if (kpi) {
    third.note = kpiGoal(kpi)
    third.tone = kpi.status === 'ok' ? 'is-ok' : kpi.status === 'none' ? '' : 'is-warn'
  }
  return [
    { label: 'Потрачено', value: rub(t.cost_kopecks), ...change(c.cost_kopecks, 'больше', 'меньше', true) },
    { label: 'Заявки', term: 'leads', value: num(t.conversions), ...change(c.conversions, 'больше', 'меньше') },
    third,
  ]
})

const bars = computed(() => {
  const rows = props.campaigns.filter((c) => c.cost_kopecks > 0).sort((a, b) => b.cost_kopecks - a.cost_kopecks).slice(0, 6)
  const max = Math.max(1, ...rows.map((c) => c.cost_kopecks))
  return rows.map((c) => ({
    key: c.link,
    name: c.name,
    link: c.link,
    cost: c.cost_kopecks,
    share: Math.max(1, (c.cost_kopecks / max) * 100).toFixed(1),
    note: [
      c.where,
      c.conversions ? `${num(c.conversions)} ${leadWord(c.conversions)} по ${rub(c.cpa_kopecks)}` : 'заявок нет',
    ]
      .filter(Boolean)
      .join(' · '),
  }))
})
</script>

<style scoped>
.simple-report__dates {
  margin: 0 2px 10px;
}
.simple-report__chart {
  margin-top: 12px;
  padding: 4px 18px 12px;
}
.simple-report__chart .section-h {
  margin: 12px 0 4px;
}
.hbar__name a {
  color: var(--ink);
  font-weight: 700;
  text-decoration: none;
}
.hbar__name a:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.hbar__name small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
}
.row-actions {
  margin-top: 18px;
}
</style>
