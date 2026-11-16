<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <p v-if="error && !data" class="panel empty">{{ error }}</p>
    <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>

    <template v-else>
      <p class="adm-dash__versus">{{ versusNote }}</p>
      <div class="adm-kpis">
        <article v-for="c in cards" :key="c.key" class="adm-kpi">
          <header class="adm-kpi__label">
            <span>{{ c.label }}</span>
            <span class="adm-kpi__hint" tabindex="0" role="img" :aria-label="c.hint" v-tip="c.hint"><Info aria-hidden="true" /></span>
          </header>
          <div class="adm-kpi__value">
            <strong>{{ c.value }}</strong>
            <DeltaChip v-if="c.change != null" :value="c.change" :good="c.good || ''" versus="к прошлому периоду" />
          </div>
          <p v-for="(line, i) in c.notes" :key="i" class="adm-kpi__note">{{ line }}</p>
        </article>
      </div>

      <section class="panel adm-charts__item">
        <div class="panel__head adm-costs__chart-head">
          <h2>Расход по {{ data.period.step === 'day' ? 'дням' : data.period.step === 'week' ? 'неделям' : 'месяцам' }}</h2>
          <div class="seg seg--sm" role="group" aria-label="Разбивка">
            <button v-for="s in SPLITS" :key="s.value" type="button" :class="{ active: split === s.value }" :aria-pressed="split === s.value" @click="split = s.value">
              {{ s.label }}
            </button>
          </div>
          <div class="seg seg--sm" role="group" aria-label="Что показывать">
            <button v-for="m in METRICS" :key="m.value" type="button" :class="{ active: metric === m.value }" :aria-pressed="metric === m.value" @click="metric = m.value">
              {{ m.label }}
            </button>
          </div>
          <span class="adm-charts__total">{{ chartTotal }}</span>
        </div>
        <p v-if="!data.parts.length" class="empty">За период расходов нет.</p>
        <AdminChart
          v-else
          title="Расход по дням"
          :points="points"
          :series="series"
          :step="data.period.step"
          :last-day="data.period.to"
          :format-value="metricMeta.format"
          :height="240"
          stacked
        />
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Info } from '@lucide/vue'
import DeltaChip from '../../components/DeltaChip.vue'
import AdminChart from './AdminChart.vue'
import { partColor, tokens, useCostsTab } from './aiCosts'
import { num, pct, rubAi, rubles } from '../../utils/format'

/** Обзор: карточки за период с изменением к прошлому отрезку и расход по дням частями — по классам или видам. */
const props = defineProps({ params: { type: Object, required: true } })
const emit = defineEmits(['period'])

const SPLITS = [
  { value: 'class', label: 'По классам' },
  { value: 'kind', label: 'По видам' },
]
const METRICS = [
  { value: 'rub', label: 'Рубли', format: (v) => rubles(v, v && v < 10 ? 2 : 0) },
  { value: 'calls', label: 'Вызовы', format: (v) => num(v) },
  { value: 'tokens', label: 'Токены', format: tokens },
]
const split = ref('class')
const metric = ref('rub')
const metricMeta = computed(() => METRICS.find((m) => m.value === metric.value))

const { data, loading, error } = useCostsTab(
  'overview',
  computed(() => props.params),
  () => ({ split: split.value }),
  (p) => emit('period', p),
)

const versusNote = computed(() => {
  const prev = data.value.period.prev
  if (!prev) return 'За всё время — сравнивать не с чем'
  return `Сравнение — с ${prev.from.split('-').reverse().join('.')} по ${prev.to.split('-').reverse().join('.')}`
})

const cards = computed(() => {
  const n = data.value.numbers
  const ch = data.value.changes || {}
  return [
    {
      key: 'cost',
      label: 'Себестоимость',
      value: rubAi(n.cost_rub),
      change: ch.cost_rub,
      notes: [`в среднем за день: ${rubAi(n.per_day_rub)}`],
      hint: 'Сколько вызовы за период стоили сервису по ценам моделей, Вордстата и картинок из настроек.',
    },
    {
      key: 'calls',
      label: 'Вызовов',
      value: num(n.calls),
      change: ch.calls,
      notes: [n.calls ? `в среднем ${rubAi(n.cost_rub / n.calls)} за вызов` : 'вызовов не было'],
      hint: 'Платные вызовы моделей, Вордстата и картинок. Проверки подключения в админке не считаются.',
    },
    {
      key: 'question',
      label: 'Цена вопроса в чате',
      value: rubAi(n.question_rub),
      change: ch.question_rub,
      good: 'down',
      notes: [`вопросов: ${num(n.questions)}`],
      hint: 'Расход ИИ-чата за период, делённый на число заданных вопросов. Один вопрос — часто несколько вызовов модели.',
    },
    {
      key: 'cache',
      label: 'Вход из кэша',
      value: pct(n.cache_share),
      notes: [
        n.cache_saved_rub
          ? `сэкономил: ${rubAi(n.cache_saved_rub)}`
          : n.cache_share
            ? 'экономия: — (у классов нет цен входа и кэша)'
            : 'кэш не срабатывал',
      ],
      hint: 'Доля токенов входа, которые провайдер взял из кэша. Экономия — оценка: эти токены по полной цене входа минус по цене кэша, по ставкам класса.',
    },
    {
      key: 'landing',
      label: 'Расход лендинга',
      value: rubAi(n.landing_rub),
      change: ch.landing_rub,
      notes: [n.cost_rub ? `${pct((n.landing_rub * 100) / n.cost_rub)} от всего` : '—'],
      hint: 'Проверка сайта и ИИ-генераторы на лендинге — без пользователя, с единиц не списываются.',
    },
    {
      key: 'units',
      label: 'Списано единиц',
      value: num(n.units, 2),
      change: ch.units,
      notes: [n.markup == null ? 'наценка: —' : `наценка: ${num(n.markup, 2)} ед. за 1 ₽`],
      hint: 'Единицы, списанные с лимитов пользователей. Наценка — сколько единиц пришлось на рубль себестоимости.',
    },
  ]
})

const series = computed(() => data.value.parts.map((p, i) => ({ key: p.key, label: p.label, color: partColor(i), type: 'bar' })))
const points = computed(() =>
  data.value.series.map((s) => {
    const row = { day: s.day }
    for (const p of data.value.parts) row[p.key] = Number(s[metric.value][p.key] || 0)
    return row
  }),
)
const chartTotal = computed(() => {
  const sum = points.value.reduce((a, r) => a + data.value.parts.reduce((b, p) => b + r[p.key], 0), 0)
  return metric.value === 'rub' ? rubAi(sum) : metricMeta.value.format(sum)
})
</script>
