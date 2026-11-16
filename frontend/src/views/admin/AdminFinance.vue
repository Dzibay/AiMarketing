<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <div class="adm-dash__bar">
      <nav class="seg seg--sm" aria-label="За сколько месяцев">
        <button v-for="r in RANGES" :key="r.value" type="button" :class="{ active: range === r.value }" @click="setRange(r.value)">
          {{ r.label }}
        </button>
      </nav>
      <span class="adm-dash__versus">По месяцам оплаты, по Москве, без тестовых. Возврат — в месяце своего платежа.</span>
    </div>

    <p v-if="error && !data" class="panel empty">{{ error }}</p>
    <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>
    <template v-else>
      <div class="adm-kpis">
        <article v-for="c in cards" :key="c.key" class="adm-kpi">
          <header class="adm-kpi__label">
            <span>{{ c.label }}</span>
            <span class="adm-kpi__hint" tabindex="0" role="img" :aria-label="c.hint" v-tip="c.hint"><Info aria-hidden="true" /></span>
          </header>
          <div class="adm-kpi__value"><strong :class="c.tone">{{ c.value }}</strong></div>
          <p class="adm-kpi__note">{{ c.note }}</p>
        </article>
      </div>

      <section class="panel adm-charts__item">
        <div class="panel__head"><h2>Выручка и расход на ИИ</h2></div>
        <AdminChart
          title="Выручка и расход на ИИ"
          :points="points"
          :series="SERIES"
          step="month"
          :last-day="today"
          :format-value="(v) => rubles(v)"
          :height="240"
        />
      </section>

      <section class="panel">
        <div class="panel__head"><h2>По месяцам</h2></div>
        <div v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr>
                <th>Месяц</th>
                <th class="num">Выручка</th>
                <th class="num">Возвраты</th>
                <th class="num">Платежей</th>
                <th class="num" v-tip="'Первые оплаты / повторные'">Новые · повт.</th>
                <th class="num">Платящих</th>
                <th class="num">Средний чек</th>
                <th class="num" v-tip="'Вся себестоимость ИИ, в скобках — лендинг'">Расход на ИИ</th>
                <th class="num">Маржа</th>
                <th class="num">Маржа, %</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in rows" :key="m.month" :class="{ 'is-current': m.month === data.current_month }">
                <td>{{ monthName(m.month) }}<small v-if="m.month === data.current_month" class="cell-note"> · идёт</small></td>
                <td class="num">{{ m.revenue_rub ? rubles(m.revenue_rub) : '—' }}</td>
                <td class="num" :class="{ 'tone-warn': m.refunded_rub }">{{ m.refunded_rub ? rubles(m.refunded_rub) : '—' }}</td>
                <td class="num">{{ m.payments ? num(m.payments) : '—' }}</td>
                <td class="num">{{ m.payments ? `${num(m.new_payments)} · ${num(m.repeat_payments)}` : '—' }}</td>
                <td class="num">{{ m.payers ? num(m.payers) : '—' }}</td>
                <td class="num">{{ m.avg_rub != null ? rubles(m.avg_rub) : '—' }}</td>
                <td class="num">
                  {{ m.ai_rub ? rubAi(m.ai_rub) : '—' }}<small v-if="m.ai_landing_rub" class="cell-note"> ({{ rubAi(m.ai_landing_rub) }})</small>
                </td>
                <td class="num" :class="{ 'tone-bad': m.margin_rub < 0 }">{{ rubles(m.margin_rub) }}</td>
                <td class="num" :class="marginTone(m.margin_pct)">{{ m.margin_pct != null ? pct(m.margin_pct) : '—' }}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr class="adm-costs__sep">
                <td><b>Итого</b></td>
                <td class="num"><b>{{ rubles(t.revenue_rub) }}</b></td>
                <td class="num">{{ t.refunded_rub ? rubles(t.refunded_rub) : '—' }}</td>
                <td class="num">{{ num(t.payments) }}</td>
                <td class="num">{{ num(t.new_payments) }} · {{ num(t.repeat_payments) }}</td>
                <td class="num"></td>
                <td class="num">{{ t.avg_rub != null ? rubles(t.avg_rub) : '—' }}</td>
                <td class="num">{{ rubAi(t.ai_rub) }}</td>
                <td class="num" :class="{ 'tone-bad': t.margin_rub < 0 }"><b>{{ rubles(t.margin_rub) }}</b></td>
                <td class="num" :class="marginTone(t.margin_pct)">{{ t.margin_pct != null ? pct(t.margin_pct) : '—' }}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </section>

      <div class="adm-charts">
        <section class="panel adm-charts__item">
          <div class="panel__head"><h2>Выручка по тарифам</h2></div>
          <p v-if="!data.by_plan.length" class="empty">Оплат за эти месяцы нет.</p>
          <table v-else class="table adm-costs__table">
            <tbody>
              <tr v-for="p in data.by_plan" :key="p.plan">
                <td>{{ p.plan }}</td>
                <td class="num">{{ n(p.payments, 'оплата', 'оплаты', 'оплат') }}</td>
                <td class="num">{{ rubles(p.rub) }}</td>
                <td class="num">
                  <span class="adm-costs__share"><i :style="{ width: `${p.share || 0}%` }"></i></span>{{ p.share != null ? pct(p.share) : '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </section>
        <section class="panel adm-charts__item">
          <div class="panel__head"><h2>Выручка по сроку оплаты</h2></div>
          <p v-if="!data.by_term.length" class="empty">Оплат за эти месяцы нет.</p>
          <table v-else class="table adm-costs__table">
            <tbody>
              <tr v-for="r in data.by_term" :key="r.months">
                <td>{{ TERMS[r.months] || `${r.months} мес.` }}</td>
                <td class="num">{{ n(r.payments, 'оплата', 'оплаты', 'оплат') }}</td>
                <td class="num">{{ rubles(r.rub) }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Info } from '@lucide/vue'
import AdminChart from './AdminChart.vue'
import { queryOne, useAdminLoad } from './useAdminLoad'
import { adminApi } from '../../api/admin'
import { todayIso } from '../../components/ui/calendar'
import { num, pct, plural, rubAi, rubles } from '../../utils/format'

/** Финансы (docs/admin-plan.md, этап 7): месяцы, выручка против расхода на ИИ, тарифы и сроки. */
const RANGES = [
  { value: '6', label: 'Полгода' },
  { value: '12', label: 'Год' },
  { value: '24', label: '2 года' },
  { value: 'all', label: 'Всё время' },
]
const TERMS = { 1: '1 месяц', 3: '3 месяца', 12: '12 месяцев' }
const SERIES = [
  { key: 'net', label: 'Выручка за вычетом возвратов', color: 'var(--c-green)', type: 'bar' },
  { key: 'ai', label: 'Расход на ИИ', color: 'var(--c-amber)', type: 'line' },
]
const MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']

const route = useRoute()
const router = useRouter()
const today = todayIso()
const range = computed(() => (RANGES.some((r) => r.value === queryOne(route, 'range')) ? queryOne(route, 'range') : '12'))
const { data, loading, error } = useAdminLoad(adminApi.finance, range)
const setRange = (v) => router.push({ query: { ...route.query, range: v === '12' ? undefined : v } })

const t = computed(() => data.value.total)
const rows = computed(() => [...data.value.months].reverse())
const points = computed(() => data.value.months.map((m) => ({ day: m.month, net: m.net_rub, ai: m.ai_rub })))
const n = (v, one, few, many) => `${num(v)} ${plural(v, one, few, many)}`
const monthName = (iso) => {
  const [y, m] = iso.split('-')
  return `${MONTHS[Number(m) - 1]} ${y}`
}
const marginTone = (v) => (v == null ? '' : v < 0 ? 'tone-bad' : v < 50 ? 'tone-warn' : 'tone-ok')

const cards = computed(() => {
  const x = t.value
  const cur = data.value.months.at(-1) || {}
  return [
    {
      key: 'net',
      label: 'Выручка',
      value: rubles(x.net_rub),
      note: `за вычетом возвратов ${rubles(x.refunded_rub)} · этот месяц: ${rubles(cur.net_rub || 0)}`,
      hint: 'Успешные оплаты за выбранные месяцы минус возвраты по ним.',
    },
    {
      key: 'ai',
      label: 'Расход на ИИ',
      value: rubAi(x.ai_rub),
      note: `этот месяц: ${rubAi(cur.ai_rub || 0)}`,
      hint: 'Вся себестоимость вызовов моделей, Вордстата и картинок — с лендингом.',
    },
    {
      key: 'margin',
      label: 'Маржа',
      value: rubles(x.margin_rub),
      tone: x.margin_rub < 0 ? 'tone-bad' : '',
      note: x.margin_pct != null ? `${pct(x.margin_pct)} выручки` : 'выручки нет',
      hint: 'Выручка за вычетом возвратов минус расход на ИИ. Налог и прочие расходы не учтены.',
    },
    {
      key: 'payments',
      label: 'Платежи',
      value: num(x.payments),
      note: `новых ${num(x.new_payments)} · повторных ${num(x.repeat_payments)} · чек ${x.avg_rub != null ? rubles(x.avg_rub) : '—'}`,
      hint: 'Новый — первая успешная оплата человека, остальные — повторные.',
    },
  ]
})
</script>

<style scoped>
.adm-costs__table tr.is-current td {
  background: color-mix(in srgb, var(--accent) 8%, transparent);
}
</style>
