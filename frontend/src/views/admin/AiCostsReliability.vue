<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
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
        <div class="panel__head">
          <h2>Подмены и ошибки по {{ data.period.step === 'day' ? 'дням' : data.period.step === 'week' ? 'неделям' : 'месяцам' }}</h2>
        </div>
        <p v-if="!hasSeries" class="empty">За период вызовов моделей нет.</p>
        <AdminChart
          v-else
          title="Подмены и ошибки"
          :points="points"
          :series="SERIES"
          :step="data.period.step"
          :last-day="data.period.to"
          :format-value="(v) => pct(v)"
          :height="220"
        />
      </section>

      <section class="panel">
        <div class="panel__head"><h2>По моделям</h2></div>
        <p class="hint">Ошибки — неудачные попытки модели; «потеряно» — ошибка без повтора и без запасной, человек остался без ответа.</p>
        <p v-if="!data.models.length" class="empty">За период ни вызовов, ни ошибок.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr>
                <th>Модель</th>
                <th class="num">Вызовов</th>
                <th class="num">Ошибок</th>
                <th class="num">Доля ошибок</th>
                <th class="num">Потеряно</th>
                <th>Подменяла</th>
                <th>Её подменяли</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in data.models" :key="m.model">
                <td>{{ m.model || '—' }}</td>
                <td class="num">{{ num(m.calls) }}</td>
                <td class="num">{{ m.errors ? num(m.errors) : '—' }}</td>
                <td class="num" :class="shareTone(m.error_share)">{{ m.error_share != null ? pct(m.error_share) : '—' }}</td>
                <td class="num" :class="{ 'tone-bad': m.lost }">{{ m.lost ? num(m.lost) : '—' }}</td>
                <td class="adm-costs__wrap">{{ pairs(m.replaced) }}</td>
                <td class="adm-costs__wrap">{{ pairs(m.replaced_by) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head"><h2>Причины сбоев</h2></div>
        <p v-if="!data.reasons.length" class="empty">Сбоев за период не было.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr>
                <th>Код</th>
                <th class="num">Раз</th>
                <th>Пример</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in data.reasons" :key="r.code">
                <td><code>{{ r.code || '—' }}</code></td>
                <td class="num">{{ num(r.count) }}</td>
                <td class="adm-costs__wrap">{{ r.sample }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head"><h2>Последние сбои</h2></div>
        <p v-if="!data.recent.length" class="empty">Сбоев за период не было.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr>
                <th>Когда</th>
                <th>Задача</th>
                <th>Модель</th>
                <th>Код</th>
                <th>Чем кончилось</th>
                <th>Кто</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(e, i) in data.recent" :key="i">
                <td>{{ formatDateTime(e.created_at) }}</td>
                <td>{{ e.task }}<span v-if="e.source === 'landing'" class="chip adm-costs__chip">лендинг</span></td>
                <td>{{ e.model }}<small v-if="e.class_name" class="cell-note"> · {{ e.class_name }}</small></td>
                <td class="adm-costs__wrap" v-tip="e.message || null"><code>{{ e.code || '—' }}</code></td>
                <td :class="e.answered ? '' : 'tone-bad'">{{ outcome(e) }}</td>
                <td>
                  <RouterLink v-if="e.user_id" :to="{ path: `${ADMIN_BASE}/users`, query: { user: e.user_id } }">{{ e.email || `#${e.user_id}` }}</RouterLink>
                  <span v-else class="cell-note">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { Info } from '@lucide/vue'
import AdminChart from './AdminChart.vue'
import { ADMIN_BASE } from './adminNav'
import { useCostsTab } from './aiCosts'
import { formatDateTime, num, pct, rubAi } from '../../utils/format'

/** Надёжность: доля подмен и ошибок по дням, модели, причины, переплата из-за подмен, последние сбои. */
const props = defineProps({ params: { type: Object, required: true } })
const emit = defineEmits(['period'])

const SERIES = [
  { key: 'sub_share', label: 'Подмены', color: 'var(--c-amber)', type: 'line' },
  { key: 'error_share', label: 'Ошибки', color: 'var(--c-red, #d64545)', type: 'line' },
]

const { data, loading, error } = useCostsTab('reliability', computed(() => props.params), null, (p) => emit('period', p))

const hasSeries = computed(() => data.value.series.some((s) => s.calls || s.errors))
const points = computed(() => data.value.series.map((s) => ({ day: s.day, sub_share: s.sub_share, error_share: s.error_share })))

const shareTone = (v) => (v == null ? '' : v >= 10 ? 'tone-bad' : v >= 3 ? 'tone-warn' : '')
const pairs = (list) => (list?.length ? list.map((p) => `${p.model} × ${num(p.calls)}`).join(', ') : '—')

function outcome(e) {
  if (e.answered) return e.answered_by && e.answered_by !== e.model ? `ответила ${e.answered_by}` : 'ответ получен'
  if (e.retried) return 'повтор не помог'
  if (e.fallback) return 'запасная не помогла'
  return 'без ответа'
}

const cards = computed(() => {
  const n = data.value.numbers
  const o = data.value.overpay
  return [
    {
      key: 'subs',
      label: 'Доля подмен',
      value: n.sub_share != null ? pct(n.sub_share) : '—',
      tone: shareTone(n.sub_share),
      note: `${num(n.substitutions)} из ${num(n.calls)} вызовов`,
      hint: 'Сколько ответов дала запасная модель вместо назначенной задаче.',
    },
    {
      key: 'errors',
      label: 'Доля ошибок',
      value: n.error_share != null ? pct(n.error_share) : '—',
      tone: shareTone(n.error_share),
      note: `${num(n.errors)} ${n.errors === 1 ? 'сбой' : 'сбоев'} на ${num(n.calls + n.errors)} попыток`,
      hint: 'Неудачные попытки моделей к числу всех попыток — удачных вызовов и сбоев.',
    },
    {
      key: 'lost',
      label: 'Осталось без ответа',
      value: num(n.lost),
      tone: n.lost ? 'tone-bad' : '',
      note: 'ни повтора, ни запасной',
      hint: 'Сбои, после которых модель не ответила: не повторили и запасной не было.',
    },
    {
      key: 'overpay',
      label: 'Переплата из-за подмен',
      value: o.calls || !o.unpriced ? rubAi(o.overpay_rub) : '—',
      tone: o.overpay_rub > 0 ? 'tone-warn' : '',
      note: o.calls
        ? `${num(o.calls)} подмен: ${rubAi(o.actual_rub)} вместо ${rubAi(o.planned_rub)}`
        : o.unpriced
          ? `${num(o.unpriced)} подмен, но у класса нет цен — не оценить`
          : 'подмен не было',
      hint:
        'Оценка: те же токены по ценам назначенного класса против фактической цены запасной.' +
        (o.unpriced ? ` Без цены класса — ${num(o.unpriced)} вызовов, в оценку не вошли.` : ''),
    },
  ]
})
</script>
