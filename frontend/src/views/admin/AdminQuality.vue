<template>
  <div class="adm-dash">
    <nav class="seg adm-costs__tabs" aria-label="Разделы качества ИИ">
      <button v-for="t in TABS" :key="t.key" type="button" :class="{ active: tab === t.key }" :aria-pressed="tab === t.key" @click="setTab(t.key)">
        {{ t.label }}
      </button>
    </nav>

    <AdminFeedback v-if="tab === 'feedback'" />

    <template v-else>
      <div class="adm-dash__bar" :class="{ 'is-loading': loading && data }">
        <AdminPeriod :period="data?.period" :query="query" :disabled="loading && !data" @period="setPeriod" @step="onStep" />
      </div>

      <p v-if="error && !data" class="panel empty">{{ error }}</p>
      <div v-else-if="!data" class="panel"><p class="empty">Загружаем…</p></div>
      <template v-else>
        <div class="adm-kpis">
          <article class="adm-kpi">
            <header class="adm-kpi__label"><span>Оценок ответов</span></header>
            <div class="adm-kpi__value"><strong>{{ num(r.total) }}</strong></div>
            <p class="adm-kpi__note">«вниз» {{ num(r.down) }} · с комментарием {{ num(r.comments) }}</p>
          </article>
          <article class="adm-kpi">
            <header class="adm-kpi__label">
              <span>Доля «вниз»</span>
              <span class="adm-kpi__hint" tabindex="0" role="img" :aria-label="DOWN_HINT" v-tip="DOWN_HINT"><Info aria-hidden="true" /></span>
            </header>
            <div class="adm-kpi__value"><strong :class="downTone(r.down_share)">{{ r.down_share != null ? pct(r.down_share) : '—' }}</strong></div>
          </article>
          <article class="adm-kpi">
            <header class="adm-kpi__label"><span>Предложений ИИ</span></header>
            <div class="adm-kpi__value"><strong>{{ num(a.proposed) }}</strong></div>
            <p class="adm-kpi__note">одобрено {{ num(a.approved) }}</p>
          </article>
          <article class="adm-kpi">
            <header class="adm-kpi__label">
              <span>Одобряют</span>
              <span class="adm-kpi__hint" tabindex="0" role="img" :aria-label="APPROVE_HINT" v-tip="APPROVE_HINT"><Info aria-hidden="true" /></span>
            </header>
            <div class="adm-kpi__value"><strong>{{ a.approve_share != null ? pct(a.approve_share) : '—' }}</strong></div>
            <p class="adm-kpi__note">
              <template v-for="(s, i) in a.statuses" :key="s.key">{{ i ? ' · ' : '' }}{{ s.label.toLowerCase() }} {{ num(s.count) }}</template>
            </p>
          </article>
        </div>

        <div class="adm-charts">
          <section class="panel adm-charts__item">
            <div class="panel__head"><h2>Оценки по {{ stepWord(data.period.step) }}</h2></div>
            <AdminChart title="Оценки ответов" :points="r.series" :series="RATING_SERIES" :step="data.period.step" :last-day="data.period.to" :format-value="num" />
          </section>
          <section class="panel adm-charts__item">
            <div class="panel__head"><h2>Предложения по {{ stepWord(data.period.step) }}</h2></div>
            <AdminChart title="Судьба предложений" :points="a.series" :series="ACTION_SERIES" :step="data.period.step" :last-day="data.period.to" :format-value="num" stacked />
          </section>
        </div>

        <div class="adm-charts">
          <section class="panel adm-charts__item">
            <div class="panel__head"><h2>Почему «вниз»</h2></div>
            <p v-if="!r.reasons.length" class="empty">Причин за период нет.</p>
            <table v-else class="table adm-costs__table">
              <tbody>
                <tr v-for="x in r.reasons" :key="x.key">
                  <td>{{ x.label }}</td>
                  <td class="num">{{ num(x.count) }}</td>
                  <td class="num">
                    <span class="adm-costs__share"><i :style="{ width: `${x.share || 0}%` }"></i></span>{{ x.share != null ? pct(x.share) : '—' }}
                  </td>
                </tr>
              </tbody>
            </table>
            <p class="hint">Доля — от оценок «вниз»; у одной оценки может быть несколько причин.</p>
          </section>
          <section class="panel adm-charts__item">
            <div class="panel__head"><h2>«Вниз» по моделям и чатам</h2></div>
            <p v-if="!r.models.length" class="empty">Оценок за период нет.</p>
            <table v-else class="table adm-costs__table">
              <thead>
                <tr><th></th><th class="num">Оценок</th><th class="num">«Вниз»</th><th class="num">Доля</th></tr>
              </thead>
              <tbody>
                <tr v-for="x in [...r.models, ...r.kinds]" :key="`${x.key}-${x.label}`">
                  <td>{{ x.label }}</td>
                  <td class="num">{{ num(x.total) }}</td>
                  <td class="num">{{ num(x.down) }}</td>
                  <td class="num" :class="downTone(x.down_share)">{{ x.down_share != null ? pct(x.down_share) : '—' }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>

        <section class="panel">
          <div class="panel__head"><h2>Предложения по категориям</h2></div>
          <p v-if="!a.categories.length" class="empty">Предложений за период нет.</p>
          <div v-else v-scroll-x class="table-scroll">
            <table class="table adm-costs__table">
              <thead>
                <tr>
                  <th>Категория</th>
                  <th class="num">Всего</th>
                  <th class="num">Одобрено</th>
                  <th class="num">Выполнено</th>
                  <th class="num">Отклонено</th>
                  <th class="num">Ошибка</th>
                  <th class="num">Ждут</th>
                  <th class="num">Выполнено, %</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="c in a.categories" :key="c.key">
                  <td>{{ c.label }}</td>
                  <td class="num">{{ num(c.total) }}</td>
                  <td class="num">{{ num(c.approved) }}</td>
                  <td class="num">{{ num(c.done) }}</td>
                  <td class="num">{{ num(c.rejected) }}</td>
                  <td class="num" :class="{ 'tone-bad': c.error }">{{ num(c.error) }}</td>
                  <td class="num">{{ num(c.pending) }}</td>
                  <td class="num">{{ c.done_share != null ? pct(c.done_share) : '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head"><h2>Предложения с ошибкой</h2></div>
          <p v-if="!a.errors.length" class="empty">Ошибок за период нет.</p>
          <div v-else v-scroll-x class="table-scroll">
            <table class="table adm-costs__table">
              <thead>
                <tr><th>Когда</th><th>Кто</th><th>Что</th><th>Ошибка</th></tr>
              </thead>
              <tbody>
                <tr v-for="e in a.errors" :key="e.id">
                  <td>{{ formatDateTime(e.created_at) }}</td>
                  <td>
                    <RouterLink v-if="e.user_id" :to="{ path: `${ADMIN_BASE}/users`, query: { user: e.user_id } }">{{ e.email || `#${e.user_id}` }}</RouterLink>
                  </td>
                  <td class="adm-costs__wrap">{{ e.summary || e.tool }}</td>
                  <td class="adm-costs__wrap tone-bad">{{ e.error || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Info } from '@lucide/vue'
import AdminChart from './AdminChart.vue'
import AdminFeedback from './AdminFeedback.vue'
import AdminPeriod from './AdminPeriod.vue'
import { ADMIN_BASE } from './adminNav'
import { autoStep, queryOne, stepWord, useAdminLoad } from './useAdminLoad'
import { useAdminPeriod } from './useAdminPeriod'
import { adminApi } from '../../api/admin'
import { formatDateTime, num, pct } from '../../utils/format'

/** Качество ИИ (docs/admin-plan.md, этап 7): оценки и причины, судьба предложений; вкладка «Оценки» — список. */
const TABS = [
  { key: 'summary', label: 'Сводка' },
  { key: 'feedback', label: 'Оценки' },
]
const RATING_SERIES = [
  { key: 'total', label: 'Всего оценок', color: 'var(--c-blue)', type: 'line' },
  { key: 'down', label: '«Вниз»', color: 'var(--c-amber)', type: 'bar' },
]
const ACTION_SERIES = [
  { key: 'done', label: 'Выполнено', color: 'var(--c-green)', type: 'bar' },
  { key: 'rejected', label: 'Отклонено', color: 'var(--muted)', type: 'bar' },
  { key: 'error', label: 'Ошибка', color: 'var(--c-amber)', type: 'bar' },
]
const DOWN_HINT = 'Больше трети — повод разобраться, больше половины — плохо.'
const APPROVE_HINT = 'Одобренные от решённых: ждущие решения не считаются.'

const route = useRoute()
const router = useRouter()
const tab = computed(() => (queryOne(route, 'tab') === 'feedback' ? 'feedback' : 'summary'))
const setTab = (k) => router.push({ query: { ...route.query, tab: k === 'summary' ? undefined : k } })

const { query, setPeriod, setStep } = useAdminPeriod()
const params = computed(() => (tab.value === 'summary' ? { ...query.value } : null))
const { data, loading, error } = useAdminLoad((p) => (p ? adminApi.quality(p) : Promise.resolve(null)), params)
const onStep = (step) => setStep(step, data.value ? autoStep(data.value.period.days) : '')

const r = computed(() => data.value.ratings)
const a = computed(() => data.value.actions)
const downTone = (v) => (v == null ? '' : v > 50 ? 'tone-bad' : v > 33 ? 'tone-warn' : 'tone-ok')
</script>
