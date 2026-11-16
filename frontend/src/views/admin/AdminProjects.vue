<template>
  <div class="adm-dash" :class="{ 'is-loading': loading && data }">
    <div class="adm-dash__bar">
      <AdminPeriod :period="data?.period" :query="query" :disabled="loading && !data" @period="setPeriod" @step="onStep" />
      <span class="adm-dash__versus">Счётчики — на сейчас, изменения в Директе — за период.</span>
    </div>

    <p v-if="error && !data" class="panel empty">{{ error }}</p>
    <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>
    <template v-else>
      <div class="adm-kpis">
        <article class="adm-kpi">
          <header class="adm-kpi__label"><span>Проекты</span></header>
          <div class="adm-kpi__value"><strong>{{ num(c.projects) }}</strong></div>
          <p class="adm-kpi__note">у {{ n(c.project_users, 'человека', 'человек', 'человек') }} · новых за период {{ num(c.new_projects) }}</p>
        </article>
        <article class="adm-kpi">
          <header class="adm-kpi__label"><span>Подключения Яндекса</span></header>
          <div class="adm-kpi__value"><strong>{{ num(c.connections) }}</strong></div>
          <p class="adm-kpi__note">аккаунтов Директа {{ num(c.accounts) }}</p>
        </article>
        <article class="adm-kpi">
          <header class="adm-kpi__label">
            <span>Кампании под управлением</span>
            <span class="adm-kpi__hint" tabindex="0" role="img" :aria-label="MANAGED_HINT" v-tip="MANAGED_HINT"><Info aria-hidden="true" /></span>
          </header>
          <div class="adm-kpi__value"><strong>{{ num(c.managed) }}</strong></div>
          <p class="adm-kpi__note">всего загружено из Директа {{ num(c.campaigns) }}</p>
        </article>
        <article class="adm-kpi">
          <header class="adm-kpi__label"><span>Изменений в Директе</span></header>
          <div class="adm-kpi__value"><strong>{{ num(ch.total) }}</strong></div>
          <p class="adm-kpi__note">
            <span :class="{ 'tone-bad': ch.failed }">с ошибкой {{ num(ch.failed) }}</span>
            <template v-for="s in ch.sources" :key="s.key"> · {{ s.label.toLowerCase() }} {{ num(s.count) }}</template>
          </p>
        </article>
      </div>

      <div class="adm-charts">
        <section class="panel adm-charts__item">
          <div class="panel__head"><h2>Изменения по {{ stepWord(data.period.step) }}</h2></div>
          <p v-if="!ch.total" class="empty">За период в Директе ничего не меняли.</p>
          <AdminChart
            v-else
            title="Изменения в Директе"
            :points="ch.series"
            :series="kindSeries"
            :step="data.period.step"
            :last-day="data.period.to"
            :format-value="num"
            stacked
          />
        </section>
        <section class="panel adm-charts__item">
          <div class="panel__head"><h2>Что меняли</h2></div>
          <p v-if="!ch.actions.length" class="empty">Изменений нет.</p>
          <table v-else class="table adm-costs__table">
            <tbody>
              <tr v-for="x in ch.actions" :key="x.action">
                <td>{{ x.label }}</td>
                <td class="num">{{ num(x.count) }}</td>
                <td class="num" :class="{ 'tone-bad': x.failed }">{{ x.failed ? `ошибок ${num(x.failed)}` : '' }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <section class="panel">
        <div class="panel__head">
          <h2>Ошибка доступа к Директу</h2>
          <span class="adm-charts__total">{{ num(data.failing.length) }}</span>
        </div>
        <p v-if="!data.failing.length" class="empty">Все подключения и аккаунты синхронизируются.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr><th>Кто</th><th>Что</th><th>Ошибка</th><th>Последняя попытка</th></tr>
            </thead>
            <tbody>
              <tr v-for="f in data.failing" :key="`${f.kind}-${f.id}`">
                <td>
                  <RouterLink v-if="f.user_id" :to="{ path: `${ADMIN_BASE}/users`, query: { user: f.user_id } }">{{ f.email || `#${f.user_id}` }}</RouterLink>
                </td>
                <td>{{ f.kind === 'account' ? 'Аккаунт' : 'Подключение' }} {{ f.name }}</td>
                <td class="adm-costs__wrap tone-bad">{{ f.error || '—' }}</td>
                <td>{{ f.at ? formatDateTime(f.at) : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel__head">
          <h2>Проекты без кампаний</h2>
          <span class="adm-charts__total">{{ num(data.empty_projects.total) }}</span>
        </div>
        <p class="hint">Проект создан, а кампании к нему не привязаны — человек мог застрять на подключении.</p>
        <p v-if="!data.empty_projects.items.length" class="empty">Таких проектов нет.</p>
        <div v-else v-scroll-x class="table-scroll">
          <table class="table adm-costs__table">
            <thead>
              <tr><th>Проект</th><th>Сайт</th><th>Кто</th><th>Создан</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in data.empty_projects.items" :key="p.id">
                <td>{{ p.name || `#${p.id}` }}</td>
                <td class="adm-costs__wrap">{{ p.site || '—' }}</td>
                <td>
                  <RouterLink v-if="p.user_id" :to="{ path: `${ADMIN_BASE}/users`, query: { user: p.user_id } }">{{ p.email || `#${p.user_id}` }}</RouterLink>
                </td>
                <td>{{ formatDay(p.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="data.empty_projects.items.length < data.empty_projects.total" class="hint">
          Показаны последние {{ num(data.empty_projects.items.length) }}.
        </p>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { Info } from '@lucide/vue'
import AdminChart from './AdminChart.vue'
import AdminPeriod from './AdminPeriod.vue'
import { ADMIN_BASE } from './adminNav'
import { autoStep, stepWord, useAdminLoad } from './useAdminLoad'
import { useAdminPeriod } from './useAdminPeriod'
import { adminApi } from '../../api/admin'
import { formatDateTime, formatDay, num, plural } from '../../utils/format'

/** Проекты и Директ (docs/admin-plan.md, этап 7): счётчики, изменения в Директе, ошибки доступа, пустые проекты. */
const MANAGED_HINT = 'Кампании, привязанные к живым проектам. «Загружено» — все кампании подключённых аккаунтов.'
const COLORS = ['var(--c-green)', 'var(--c-blue)', 'var(--c-amber)', 'var(--muted)', 'var(--ink-2)', 'var(--line-2)']

const { query, setPeriod, setStep } = useAdminPeriod()
const { data, loading, error } = useAdminLoad(adminApi.projects, query)
const onStep = (step) => setStep(step, data.value ? autoStep(data.value.period.days) : '')

const c = computed(() => data.value.counts)
const ch = computed(() => data.value.changes)
const kindSeries = computed(() => ch.value.kinds.map((k, i) => ({ key: k.key, label: k.label, color: COLORS[i % COLORS.length], type: 'bar' })))
const n = (v, one, few, many) => `${num(v)} ${plural(v, one, few, many)}`
</script>
