<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <div class="adm-dash__bar">
      <AdminPeriod :period="data?.period" :query="query" :disabled="loading && !data" @period="setPeriod" @step="onStep" />
      <span v-if="data" class="adm-dash__versus">{{ versusNote }} · данные на {{ mskTime(data.generated_at) }}</span>
    </div>

    <p v-if="error && !data" class="panel empty">{{ error }}</p>
    <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>

    <template v-else>
      <!-- Полоса состояния: точка на сервис, обращения без ответа и платежи без чека. -->
      <section class="adm-status" aria-label="Состояние сервиса">
        <component
          :is="s.to ? RouterLink : 'span'"
          v-for="s in statusItems"
          :key="s.key"
          class="adm-status__item"
          :to="s.to"
          v-tip="s.tip"
        >
          <span class="sdot" :class="s.dot"></span>{{ s.label }}
        </component>
        <span class="adm-status__gap"></span>
        <RouterLink class="adm-status__item" :class="{ 'is-warn': status.support_open }" :to="`${ADMIN_BASE}/support`">
          Без ответа в поддержке: <b>{{ num(status.support_open) }}</b>
        </RouterLink>
        <RouterLink class="adm-status__item" :class="{ 'is-warn': status.no_receipt }" :to="`${ADMIN_BASE}/payments?no_receipt=1`">
          Без чека: <b>{{ num(status.no_receipt) }}</b>
        </RouterLink>
      </section>

      <div class="adm-kpis">
        <article v-for="c in cards" :key="c.key" class="adm-kpi">
          <header class="adm-kpi__label">
            <component :is="c.to ? RouterLink : 'span'" :to="c.to">{{ c.label }}</component>
            <span class="adm-kpi__hint" tabindex="0" role="img" :aria-label="c.hint" v-tip="c.hint"><Info aria-hidden="true" /></span>
          </header>
          <div class="adm-kpi__value">
            <strong>{{ c.value }}</strong>
            <DeltaChip v-if="c.change != null" :value="c.change" :good="c.good ?? 'up'" :versus="versus" />
          </div>
          <p v-for="(line, i) in c.notes" :key="i" class="adm-kpi__note" :class="line.tone">
            <RouterLink v-if="line.to" :to="line.to">{{ line.text }}</RouterLink>
            <template v-else>{{ line.text }}</template>
          </p>
          <ul v-if="c.rows?.length" class="adm-kpi__rows">
            <li v-for="r in c.rows" :key="r.label">
              <RouterLink v-if="r.to" :to="r.to">{{ r.label }}</RouterLink>
              <span v-else>{{ r.label }}</span>
              <b>{{ r.value }}</b>
            </li>
          </ul>
        </article>
      </div>

      <div class="adm-charts">
        <section v-for="ch in charts" :key="ch.key" class="panel adm-charts__item" :class="{ 'is-wide': ch.wide }">
          <div class="panel__head">
            <h2>{{ ch.title }}</h2>
            <span class="adm-charts__total">{{ ch.total }}</span>
          </div>
          <AdminChart
            :title="ch.title"
            :points="data.series"
            :prev-points="data.prev_series"
            :series="ch.series"
            :step="period.step"
            :last-day="period.to"
            :format-value="ch.format || num"
          />
        </section>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { Info } from '@lucide/vue'
import DeltaChip from '../../components/DeltaChip.vue'
import AdminChart from './AdminChart.vue'
import AdminPeriod from './AdminPeriod.vue'
import { ADMIN_BASE } from './adminNav'
import { useAdminPeriod } from './useAdminPeriod'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { num, pct, plural, rubles } from '../../utils/format'
import { mskTime } from './adminUsers'

/**
 * Дашборд админки (docs/admin-plan.md, раздел 4): период в адресе, карточки с изменением к прошлому отрезку
 * той же длины, пять графиков и полоса состояния. Числа считает сервер (app/admin_dashboard.py).
 */
const { query, setPeriod, setStep } = useAdminPeriod()
const data = ref(null)
const loading = ref(false)
const error = ref('')
const S = `${ADMIN_BASE}/settings`
const U = `${ADMIN_BASE}/users`

let seq = 0
async function load() {
  const my = ++seq
  loading.value = true
  error.value = ''
  try {
    const out = await adminApi.dashboard(query.value)
    if (my === seq) data.value = out
  } catch (err) {
    if (my === seq) error.value = errorText(err)
  } finally {
    if (my === seq) loading.value = false
  }
}

watch(query, load, { immediate: true, deep: true })

const period = computed(() => data.value.period)
const cur = computed(() => data.value.current)
const ch = computed(() => data.value.changes || {})
const snap = computed(() => data.value.now)
const status = computed(() => data.value.status)

const autoStep = (days) => (days <= 92 ? 'day' : days <= 400 ? 'week' : 'month')
const onStep = (step) => setStep(step, data.value ? autoStep(period.value.days) : '')

const versus = computed(() => (period.value.preset === 'day' ? 'ко вчера к этому часу' : 'к прошлому периоду'))
const versusNote = computed(() => {
  const prev = period.value.prev
  if (!prev) return 'За всё время — сравнивать не с чем'
  if (period.value.preset === 'day') return 'Сравнение — со вчера до того же часа'
  return `Сравнение — с ${prev.from.split('-').reverse().join('.')} по ${prev.to.split('-').reverse().join('.')}`
})

const n = (v, one, few, many) => `${num(v)} ${plural(v, one, few, many)}`
const maybePct = (v) => (v == null ? '—' : pct(v))
const maybeNum = (v, d = 1) => (v == null ? '—' : num(v, d))

const cards = computed(() => {
  const c = cur.value
  const s = snap.value
  const subs = s.subscriptions
  const conv = c.conversion
  const ret = c.retention
  const w = data.value.conversion_window_days
  return [
    {
      key: 'users',
      label: 'Пользователи',
      to: U,
      value: num(s.users_total),
      change: ch.value.new_users,
      notes: [{ text: `новых за период: ${num(c.new_users)}` }],
      hint: 'Всего зарегистрированных сейчас. Изменение — по новым регистрациям за период против прошлого.',
    },
    {
      key: 'subs',
      label: 'С подпиской',
      to: `${U}?sub=paying`,
      value: num(subs.paying),
      notes: [{ text: `${pct(subs.paying_pct)} от всех пользователей` }],
      rows: [
        ...subs.by_plan.map((p) => ({ label: p.name, value: num(p.users), to: `${U}?plan=${p.plan_id}` })),
        { label: 'Пробный', value: num(subs.trial), to: `${U}?sub=trial` },
        { label: 'Без тарифа', value: num(subs.none), to: `${U}?sub=none` },
      ],
      hint: 'Сейчас, не за период: действующий платный тариф. Ниже — по тарифам, пробный и без тарифа.',
    },
    {
      key: 'ending',
      label: 'Истекают за 7 дней',
      to: `${U}?sub=ending`,
      value: num(s.ending.total),
      notes: [{ text: `из них платных: ${num(s.ending.paid)}`, to: `${U}?sub=ending_paid` }],
      hint: 'Сейчас: подписка (и пробная) заканчивается в ближайшие 7 дней. Ссылки открывают этих людей в списке.',
    },
    {
      key: 'revenue',
      label: 'Доход',
      to: `${ADMIN_BASE}/payments`,
      value: rubles(c.revenue),
      change: ch.value.revenue,
      notes: [{ text: `за всё время: ${rubles(s.all_time.revenue)}` }],
      hint: 'Успешные оплаты за период по дате оплаты, без тестовых и полностью возвращённых.',
    },
    {
      key: 'payments',
      label: 'Платежи',
      value: num(c.payments),
      change: ch.value.payments,
      notes: [{ text: `средний чек: ${c.payments ? rubles(c.avg_check) : '—'}` }],
      hint: 'Число успешных оплат за период и средняя сумма одной оплаты.',
    },
    {
      key: 'payers',
      label: 'Платящие',
      value: num(c.payers),
      change: ch.value.payers,
      notes: [{ text: `доход на платящего: ${c.payers ? rubles(c.per_payer) : '—'}` }],
      hint: 'Разные люди, оплатившие за период, и сколько в среднем принёс каждый.',
    },
    {
      key: 'conversion',
      label: 'Конверсия в оплату',
      value: pct(conv.rate),
      change: ch.value.conversion,
      notes: [
        {
          text: `${num(conv.paid)} из ${num(conv.registered)} за ${n(w, 'день', 'дня', 'дней')}${conv.growing ? ' · ещё растёт' : ''}`,
        },
        { text: `за всё время: ${pct(s.all_time.conversion.rate)}` },
      ],
      hint: `Из зарегистрированных за период — сколько оплатили в первые ${n(w, 'день', 'дня', 'дней')} (пробный срок + 7 дней). «Ещё растёт» — окно у части людей не закончилось.`,
    },
    {
      key: 'per_payer',
      label: 'Платежей на платящего',
      value: maybeNum(s.all_time.payments_per_payer),
      notes: [{ text: `за всё время: ${n(s.all_time.payments, 'оплата', 'оплаты', 'оплат')}, ${n(s.all_time.payers, 'человек', 'человека', 'человек')}` }],
      hint: 'Сколько раз в среднем платит один платящий — за всё время. Растёт, когда люди продлевают.',
    },
    {
      key: 'retention',
      label: 'Удержание',
      value: maybePct(ret.rate),
      change: ch.value.retention,
      notes: ret.endings
        ? [
            { text: `закончилось подписок: ${num(ret.endings)}` },
            { text: `вместе с вернувшимися позже: ${maybePct(ret.return_rate)}` },
          ]
        : [{ text: 'за период оплаченные подписки не заканчивались' }],
      rows: ret.endings
        ? [
            { label: 'Продлили досрочно', value: num(ret.early) },
            { label: 'В день окончания', value: num(ret.on_day) },
            { label: 'После перерыва', value: num(ret.returned) },
          ]
        : [],
      hint: 'Из оплаченных подписок, закончившихся за период, — доля продлённых не позже дня окончания. «Всего вернулись» — вместе с оплатившими после перерыва.',
    },
    {
      key: 'active',
      label: 'Активные',
      value: maybeNum(c.active_avg),
      change: ch.value.active_avg,
      notes: [
        { text: `сегодня заходили ${num(s.active.today_visited)}, действовали ${num(s.active.today_acted)}` },
        { text: `за 7 дней: ${num(s.active.week)} · за 30 дней: ${num(s.active.month)}` },
      ],
      hint: 'Среднее число людей, заходивших в кабинет за день периода. Ниже — сегодня и уникальные за 7 и 30 дней.',
    },
    {
      key: 'site',
      label: 'Сайт',
      to: `${ADMIN_BASE}/site`,
      value: num(c.visits),
      change: ch.value.visits,
      notes: [
        { text: `посетителей: ${num(c.visitors)} · регистраций: ${num(c.new_users)}` },
        { text: `конверсия в регистрацию: ${maybePct(c.site_conversion)}` },
      ],
      hint: 'Визиты на сайт за период, разные посетители и доля регистраций от посетителей.',
    },
    {
      key: 'questions',
      label: 'Вопросы ИИ',
      value: num(c.questions),
      change: ch.value.questions,
      notes: [{ text: `на активного: ${maybeNum(c.questions_per_active)}` }],
      hint: 'Вопросы, заданные ИИ в чате за период, и сколько в среднем на одного заходившего.',
    },
  ]
})

const sum = (key) => data.value.series.reduce((a, p) => a + Number(p[key] || 0), 0)
const charts = computed(() => [
  {
    key: 'users',
    title: 'Регистрации',
    total: num(cur.value.new_users),
    series: [{ key: 'users', label: 'Регистрации', color: 'var(--accent)', prev: true }],
  },
  {
    key: 'revenue',
    title: 'Доход',
    total: rubles(cur.value.revenue),
    format: (v) => rubles(v),
    series: [{ key: 'revenue', label: 'Доход', color: 'var(--c-green)', type: 'bar' }],
  },
  {
    key: 'active',
    title: 'Активность',
    total: `в среднем ${maybeNum(cur.value.active_avg)} в день`,
    series: [
      { key: 'visited', label: 'Заходили', color: 'var(--c-blue)' },
      { key: 'acted', label: 'Действовали', color: 'var(--c-amber)' },
    ],
  },
  {
    key: 'questions',
    title: 'Вопросы ИИ',
    total: num(sum('questions')),
    series: [{ key: 'questions', label: 'Вопросы', color: 'var(--c-teal)', type: 'bar' }],
  },
  {
    key: 'site',
    title: 'Сайт',
    total: `${num(cur.value.visits)} визитов`,
    wide: true,
    series: [
      { key: 'users', label: 'Регистрации', color: 'var(--accent)', type: 'bar' },
      { key: 'visits', label: 'Визиты', color: 'var(--c-blue)' },
      { key: 'visitors', label: 'Посетители', color: 'var(--c-amber)' },
    ],
  },
])

const SERVICES = {
  ai: { label: 'ИИ', to: `${S}/ai` },
  wordstat: { label: 'Вордстат', to: `${S}/wordstat` },
  images: { label: 'Картинки', to: `${S}/images` },
  oauth: { label: 'Вход через Яндекс', to: S },
  payments: { label: 'ЮKassa', to: `${S}/payments` },
  email: { label: 'Почта', to: S },
  bridge: { label: 'Мост', to: `${S}/bridge` },
  queue: { label: 'Очередь задач', to: `${ADMIN_BASE}/system` },
}

const statusItems = computed(() =>
  status.value.services.map((s) => {
    const meta = SERVICES[s.key] || { label: s.key }
    let tip = s.ok ? 'работает' : s.ok === null ? 'выключено' : 'не настроено'
    if (s.key === 'queue') {
      tip = s.ok
        ? `ждут: ${num(s.waiting)}`
        : `ждут: ${num(s.waiting)}, старейшая — ${n(s.lag_minutes, 'минуту', 'минуты', 'минут')}`
    }
    return {
      key: s.key,
      label: meta.label,
      to: meta.to,
      tip: `${meta.label}: ${tip}`,
      dot: s.ok ? 'sdot--ok' : s.ok === null ? '' : 'sdot--warn',
    }
  }),
)

</script>
