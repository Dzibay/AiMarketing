<template>
  <div class="adm-dash">
    <nav class="seg adm-costs__tabs" aria-label="Разделы сайта">
      <button v-for="t in TABS" :key="t.key" type="button" :class="{ active: tab === t.key }" :aria-pressed="tab === t.key" @click="setTab(t.key)">
        {{ t.label }}
      </button>
    </nav>

    <AdminWaitlist v-if="tab === 'waitlist'" />

    <template v-else>
      <div class="adm-dash__bar" :class="{ 'is-loading': loading && data }">
        <AdminPeriod :period="data?.period" :query="query" :disabled="loading && !data" @period="setPeriod" @step="onStep" />
        <span v-if="data" class="adm-dash__versus">{{ versusNote }}</span>
      </div>

      <p v-if="error && !data" class="panel empty">{{ error }}</p>
      <div v-else-if="!ready" class="panel"><p class="empty">Загружаем…</p></div>

      <!-- Сайт: визиты, посетители, регистрации и откуда пришли. -->
      <template v-else-if="tab === 'site'">
        <div class="adm-kpis">
          <article v-for="c in siteCards" :key="c.key" class="adm-kpi">
            <header class="adm-kpi__label">
              <span>{{ c.label }}</span>
              <span class="adm-kpi__hint" tabindex="0" role="img" :aria-label="c.hint" v-tip="c.hint"><Info aria-hidden="true" /></span>
            </header>
            <div class="adm-kpi__value">
              <strong>{{ c.value }}</strong>
              <DeltaChip v-if="c.change != null" :value="c.change" good="up" :versus="versus" />
            </div>
            <p v-if="c.note" class="adm-kpi__note">{{ c.note }}</p>
          </article>
        </div>

        <section class="panel adm-charts__item">
          <div class="panel__head"><h2>Посетители и регистрации</h2></div>
          <AdminChart
            title="Посетители и регистрации"
            :points="data.series"
            :series="SITE_SERIES"
            :step="data.period.step"
            :last-day="data.period.to"
            :format-value="num"
          />
        </section>

        <div class="adm-charts">
          <section v-for="tbl in siteTables" :key="tbl.key" class="panel adm-charts__item">
            <div class="panel__head"><h2>{{ tbl.title }}</h2></div>
            <p v-if="!tbl.rows.length" class="empty">{{ tbl.empty }}</p>
            <div v-else v-scroll-x class="table-scroll">
              <table class="table adm-costs__table">
                <thead>
                  <tr>
                    <th>{{ tbl.head }}</th>
                    <th class="num">Визиты</th>
                    <th class="num">Посетители</th>
                    <th class="num">Регистрации</th>
                    <th class="num" v-tip="'Регистрации от посетителей'">Конверсия</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in tbl.rows" :key="r.key">
                    <td class="adm-costs__wrap">
                      {{ tbl.label ? tbl.label(r) : r.key }}
                      <small v-if="tbl.note && tbl.note(r)" class="cell-note"> · {{ tbl.note(r) }}</small>
                    </td>
                    <td class="num">{{ num(r.visits) }}</td>
                    <td class="num">{{ num(r.visitors) }}</td>
                    <td class="num">{{ r.registrations ? num(r.registrations) : '—' }}</td>
                    <td class="num">{{ r.conversion != null ? pct(r.conversion) : '—' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </template>

      <!-- Инструменты лендинга: проверки сайта, генераторы, переносы в кабинет. -->
      <template v-else>
        <div class="adm-kpis">
          <article class="adm-kpi">
            <header class="adm-kpi__label"><span>Проверок сайта</span></header>
            <div class="adm-kpi__value"><strong>{{ num(data.checks.total) }}</strong></div>
            <p class="adm-kpi__note">
              готово {{ num(data.checks.done) }}<span :class="{ 'tone-warn': data.checks.failed }"> · с ошибкой {{ num(data.checks.failed) }}</span>
            </p>
          </article>
          <article class="adm-kpi">
            <header class="adm-kpi__label">
              <span>Перенесли в кабинет</span>
              <span class="adm-kpi__hint" tabindex="0" role="img" aria-label="Готовые проверки, которые человек забрал в кабинет после регистрации" v-tip="'Готовые проверки, которые человек забрал в кабинет после регистрации'"><Info aria-hidden="true" /></span>
            </header>
            <div class="adm-kpi__value"><strong>{{ num(data.checks.claimed) }}</strong></div>
            <p class="adm-kpi__note">{{ data.checks.claim_share != null ? `${pct(data.checks.claim_share)} готовых проверок` : 'готовых проверок нет' }}</p>
          </article>
          <article v-for="t in otherTools" :key="t.key" class="adm-kpi">
            <header class="adm-kpi__label"><span>{{ t.label }}</span></header>
            <div class="adm-kpi__value"><strong>{{ num(t.total) }}</strong></div>
            <p class="adm-kpi__note">запусков</p>
          </article>
        </div>
        <section class="panel adm-charts__item">
          <div class="panel__head"><h2>Запуски по {{ stepWord(data.period.step) }}</h2></div>
          <AdminChart
            title="Запуски инструментов лендинга"
            :points="data.series"
            :series="toolSeries"
            :step="data.period.step"
            :last-day="data.period.to"
            :format-value="num"
          />
        </section>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Info } from '@lucide/vue'
import DeltaChip from '../../components/DeltaChip.vue'
import AdminChart from './AdminChart.vue'
import AdminPeriod from './AdminPeriod.vue'
import AdminWaitlist from './AdminWaitlist.vue'
import { autoStep, queryOne, stepWord, useAdminLoad } from './useAdminLoad'
import { useAdminPeriod } from './useAdminPeriod'
import { adminApi } from '../../api/admin'
import { num, pct } from '../../utils/format'

/** Сайт (docs/admin-plan.md, этап 7): статистика лендинга, инструменты лендинга, лист ожидания — вкладки ?tab=… */
const TABS = [
  { key: 'site', label: 'Сайт' },
  { key: 'tools', label: 'Инструменты лендинга' },
  { key: 'waitlist', label: 'Лист ожидания' },
]
const SITE_SERIES = [
  { key: 'visitors', label: 'Посетители', color: 'var(--c-blue)', type: 'line' },
  { key: 'registrations', label: 'Регистрации', color: 'var(--c-green)', type: 'bar' },
]
const TOOL_COLORS = ['var(--c-green)', 'var(--c-blue)', 'var(--c-amber)', 'var(--muted)']
const KINDS = { ref: 'Реф-ссылки', utm: 'UTM-метки', referrer: 'Другие сайты', direct: 'Прямые заходы' }

const route = useRoute()
const router = useRouter()
const tab = computed(() => (TABS.some((t) => t.key === queryOne(route, 'tab')) ? queryOne(route, 'tab') : 'site'))
const setTab = (k) => router.push({ query: { ...route.query, tab: k === 'site' ? undefined : k } })

const { query, setPeriod, setStep } = useAdminPeriod()
const params = computed(() => (tab.value === 'waitlist' ? null : { tab: tab.value, ...query.value }))
const { data, loading, error } = useAdminLoad(async (p) => {
  if (!p) return null
  const { tab: t, ...q } = p
  const out = await (t === 'tools' ? adminApi.siteTools(q) : adminApi.siteStats(q))
  return { ...out, tab: t }
}, params)

// Ответ прошлой вкладки не рисуем в новой.
const ready = computed(() => data.value && data.value.tab === tab.value)
const onStep = (step) => setStep(step, data.value ? autoStep(data.value.period.days) : '')
const versus = 'к прошлому периоду'
const versusNote = computed(() => {
  const prev = data.value.period.prev
  if (!prev) return 'За всё время — сравнивать не с чем'
  return `Сравнение — с ${prev.from.split('-').reverse().join('.')} по ${prev.to.split('-').reverse().join('.')}`
})

const siteCards = computed(() => {
  if (!ready.value || tab.value !== 'site') return []
  const x = data.value.numbers
  const c = data.value.changes || {}
  return [
    { key: 'visits', label: 'Визиты', value: num(x.visits), change: c.visits, hint: 'Заходы на лендинг. Новый визит — после 30 минут тишины.' },
    { key: 'visitors', label: 'Посетители', value: num(x.visitors), change: c.visitors, hint: 'Разные браузеры, заходившие на лендинг.' },
    {
      key: 'registrations',
      label: 'Регистрации',
      value: num(x.registrations),
      change: c.registrations,
      note: `с лендинга ${num(x.from_site)}`,
      hint: 'Все регистрации за период; «с лендинга» — у кого есть визит на сайт.',
    },
    {
      key: 'conversion',
      label: 'Конверсия',
      value: x.conversion != null ? pct(x.conversion) : '—',
      note: c.conversion != null ? `${c.conversion > 0 ? '+' : ''}${num(c.conversion, 1)} п. п. к прошлому периоду` : '',
      hint: 'Регистрации с лендинга от посетителей.',
    },
  ]
})

const siteTables = computed(() => {
  if (!ready.value || tab.value !== 'site') return []
  const s = data.value.sources
  return [
    { key: 'kinds', title: 'Откуда пришли', head: 'Источник', rows: s.kinds, empty: 'Визитов нет.', label: (r) => KINDS[r.key] || r.key },
    {
      key: 'refs',
      title: 'Реф-ссылки',
      head: 'Ссылка',
      rows: s.refs,
      empty: 'По реф-ссылкам не заходили.',
      note: (r) => [r.group_name, r.code && r.code !== r.key ? r.code : ''].filter(Boolean).join(' · '),
    },
    {
      key: 'utm',
      title: 'UTM-метки',
      head: 'utm_source',
      rows: s.utm,
      empty: 'С UTM-метками не заходили.',
      note: (r) => [r.medium, r.campaign].filter(Boolean).join(' / '),
    },
    { key: 'referrers', title: 'Другие сайты', head: 'Сайт', rows: s.referrers, empty: 'С других сайтов не заходили.' },
    { key: 'pages', title: 'Страницы входа', head: 'Страница', rows: data.value.pages, empty: 'Визитов нет.' },
    { key: 'devices', title: 'Устройства', head: 'Устройство', rows: data.value.devices, empty: 'Визитов нет.' },
  ]
})

const otherTools = computed(() => (ready.value && tab.value === 'tools' ? data.value.tools.filter((t) => !['check', 'claimed'].includes(t.key)) : []))
const toolSeries = computed(() =>
  (data.value?.tools || []).map((t, i) => ({ key: t.key, label: t.label, color: TOOL_COLORS[i % TOOL_COLORS.length], type: 'line' })),
)
</script>
