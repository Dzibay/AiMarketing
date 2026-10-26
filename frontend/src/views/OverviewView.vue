<template>
  <div class="site">
    <SiteHeader solid />
    <main class="cabinet">
      <div class="wrap">
        <div class="cabinet__head">
          <div>
            <h1>Сводка</h1>
            <p>Все клиенты на одном экране: расход, KPI, бюджет, баланс — и что требует внимания.</p>
          </div>
          <div class="row-actions">
            <button class="btn btn--accent" type="button" :disabled="refreshing || loading" @click="refresh">
              {{ refreshing ? 'Обновляем…' : 'Обновить расход за сегодня' }}
            </button>
          </div>
        </div>
        <CabinetNav />

        <div v-if="loading" class="panel"><p class="empty">Собираем сводку…</p></div>

        <section v-else-if="!hasAccounts" class="panel">
          <h2>Пока нечего сводить</h2>
          <p class="hint">
            Подключите Яндекс и включите кабинеты Директа на главной, затем привяжите их к клиентам.
          </p>
          <RouterLink class="btn btn--accent" to="/cabinet">К подключениям</RouterLink>
        </section>

        <template v-else>
          <p class="sync-status overview-updated">
            <template v-if="refreshing">{{ refreshText }}</template>
            <template v-else-if="data.today_updated_at">
              Расход за сегодня — на {{ formatTime(data.today_updated_at) }}, Директ отдаёт его с задержкой.
            </template>
            <template v-else>Расход за сегодня ещё не загружался — нажмите «Обновить».</template>
          </p>

          <div class="stats-grid">
            <div class="stat-card">
              <small>Сегодня</small>
              <strong>{{ rub(totals.spend_today) }}</strong>
            </div>
            <div class="stat-card">
              <small>Вчера</small>
              <strong>{{ rub(totals.spend_yesterday) }}</strong>
            </div>
            <div class="stat-card">
              <small>Конверсии вчера</small>
              <strong>{{ totals.conversions_yesterday || 0 }}</strong>
            </div>
            <div class="stat-card">
              <small>7 дней</small>
              <strong>{{ rub(totals.spend_7d) }}</strong>
            </div>
            <div class="stat-card">
              <small>CPA 7 дней</small>
              <strong>{{ totals.conversions_7d ? rub(totals.spend_7d / totals.conversions_7d) : '—' }}</strong>
            </div>
            <div class="stat-card">
              <small>Горит</small>
              <strong :class="fireCount ? 'tone-bad' : 'tone-ok'">{{ fireCount }}</strong>
            </div>
          </div>

          <section ref="problemsPanel" class="panel overview-problems">
            <div class="panel__head">
              <h2>Где горит</h2>
            </div>
            <p class="hint">
              Токены и синки, баланс и бюджет, KPI, модерация, остановленные кампании и резкие изменения
              вчерашнего дня против двух недель до него.
            </p>
            <div class="filters">
              <div class="field">
                <label>Важность</label>
                <select v-model="severityFilter">
                  <option value="all">Все</option>
                  <option value="fire">Критично и внимание</option>
                  <option value="critical">Только критично</option>
                </select>
              </div>
              <div class="field">
                <label>Клиент</label>
                <select v-model="clientFilter">
                  <option value="">Все клиенты</option>
                  <option v-for="c in data.clients" :key="clientKey(c.id)" :value="clientKey(c.id)">
                    {{ c.name }}
                  </option>
                </select>
              </div>
            </div>
            <p v-if="!filteredProblems.length" class="empty">
              {{ data.problems.length ? 'По выбранным фильтрам ничего.' : 'Ничего не горит.' }}
            </p>
            <div v-else class="list">
              <div
                v-for="(p, i) in visibleProblems"
                :key="`${p.kind}-${p.client_id}-${p.account_id}-${p.campaign_row_id}-${i}`"
                class="list-row problem"
                :class="`is-${p.severity}`"
              >
                <div>
                  <span class="chip" :class="`chip--${p.severity}`">{{ SEVERITY_LABELS[p.severity] }}</span>
                  <strong>{{ p.title }}</strong>
                  <small>{{ problemWhere(p) }}<template v-if="p.detail"> · {{ ERRORS[p.detail] || p.detail }}</template></small>
                </div>
                <RouterLink
                  v-if="p.campaign_row_id"
                  class="btn btn--ghost"
                  :to="`/cabinet/campaigns/${p.campaign_row_id}`"
                >
                  К кампании
                </RouterLink>
                <RouterLink v-else-if="CONNECTION_KINDS.has(p.kind)" class="btn btn--ghost" to="/cabinet">
                  К подключениям
                </RouterLink>
                <RouterLink v-else-if="CLIENT_KINDS.has(p.kind)" class="btn btn--ghost" to="/cabinet/clients">
                  К клиенту
                </RouterLink>
              </div>
            </div>
            <div v-if="filteredProblems.length > visibleProblems.length" class="pager">
              <span class="sync-status">Показано {{ visibleProblems.length }} из {{ filteredProblems.length }}</span>
              <button class="btn btn--ghost" type="button" @click="showAllProblems = true">Показать все</button>
            </div>
          </section>

          <section class="panel">
            <div class="panel__head">
              <h2>Клиенты</h2>
            </div>
            <p class="hint">
              KPI — за 7 дней против цели клиента, темп — прогноз расхода месяца против бюджета, баланс —
              общий счёт Директа и на сколько дней его хватит при расходе последней недели.
            </p>
            <div class="filters">
              <div class="field">
                <label>Поиск</label>
                <input v-model="search" type="search" placeholder="Название клиента" />
              </div>
              <label class="check-row">
                <input v-model="view.onlyProblems" type="checkbox" />
                Только с проблемами
              </label>
            </div>
            <p v-if="!sortedClients.length" class="empty">Нет клиентов под фильтр.</p>
            <div v-else class="table-scroll">
              <table class="table overview-table">
                <thead>
                  <tr>
                    <th v-for="col in COLUMNS" :key="col.key" :class="{ num: col.num }">
                      <button class="th-sort" type="button" @click="sortBy(col.key)">
                        {{ col.label }}<span v-if="view.sort === col.key">{{ view.dir === 'asc' ? ' ↑' : ' ↓' }}</span>
                      </button>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="c in sortedClients" :key="clientKey(c.id)" :class="c.severity && `is-${c.severity}`">
                    <td>
                      <strong>{{ c.name }}</strong>
                      <small class="cell-note">
                        {{ c.accounts ? `кабинетов: ${c.accounts}` : 'нет кабинетов' }}
                        <template v-if="c.accounts && !c.stats_synced_at"> · статистика не загружена</template>
                      </small>
                    </td>
                    <td class="num">{{ rub(c.spend_today) }}</td>
                    <td class="num">
                      {{ rub(c.spend_yesterday) }}
                      <small class="cell-note">{{ c.conversions_yesterday }} конв.</small>
                    </td>
                    <td class="num">
                      {{ rub(c.spend_7d) }}
                      <small class="cell-note">{{ c.conversions_7d }} конв. · {{ c.clicks_7d }} кл.</small>
                    </td>
                    <td class="num">
                      <template v-if="c.kpi.type">
                        <span :class="KPI_TONES[c.kpi.status]">{{ kpiValue(c.kpi) }}</span>
                        <small class="cell-note">{{ kpiTarget(c.kpi) }}</small>
                      </template>
                      <template v-else>
                        {{ c.cpa_7d != null ? rub(c.cpa_7d) : '—' }}
                        <small class="cell-note">{{ c.cpa_7d != null ? 'CPA, цель не задана' : 'KPI не задан' }}</small>
                      </template>
                    </td>
                    <td class="num">
                      <template v-if="c.budget">
                        <span :class="BUDGET_TONES[c.budget.status]">
                          {{ c.budget.pace_pct != null ? `${c.budget.pace_pct}%` : '—' }}
                        </span>
                        <small class="cell-note">
                          {{ rub(c.budget.spent_kopecks) }} из {{ rub(c.budget.budget_kopecks) }}
                        </small>
                      </template>
                      <template v-else>—</template>
                    </td>
                    <td class="num">
                      {{ c.balance_kopecks != null ? rub(c.balance_kopecks) : '—' }}
                      <small v-if="c.days_on_balance != null" class="cell-note" :class="balanceTone(c.days_on_balance)">
                        ≈ {{ c.days_on_balance }} дн.
                      </small>
                    </td>
                    <td class="num">
                      <button
                        v-if="c.severity"
                        class="th-sort problem-counts"
                        type="button"
                        title="Показать проблемы клиента"
                        @click="focusClient(c.id)"
                      >
                        <span v-if="c.problems.critical" class="tone-bad">🔴 {{ c.problems.critical }}</span>
                        <span v-if="c.problems.warning" class="tone-warn">🟠 {{ c.problems.warning }}</span>
                        <span v-if="c.problems.info" class="cell-note">ⓘ {{ c.problems.info }}</span>
                      </button>
                      <span v-else class="tone-ok">✓</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </template>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import CabinetNav from '../components/CabinetNav.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { ERRORS, errorText, jobProgressText, webApi } from '../api/web'
import { getSession } from '../composables/useSession'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Сводка — AiMarketing', true)

const VIEW_KEY = 'aim.overview.view'
const PROBLEMS_LIMIT = 30
const SEVERITY_LABELS = { critical: 'Критично', warning: 'Внимание', info: 'К сведению' }
const SEVERITY_RANK = { critical: 0, warning: 1, info: 2, '': 3 }
const KPI_TONES = { ok: 'tone-ok', warn: 'tone-warn', bad: 'tone-bad', none: '' }
const BUDGET_TONES = { ok: 'tone-ok', under: 'tone-warn', over: 'tone-bad', exhausted: 'tone-bad' }
const CONNECTION_KINDS = new Set(['token', 'connection_sync', 'account_sync', 'stats_error', 'stats_stale'])
const CLIENT_KINDS = new Set(['budget', 'kpi'])
const COLUMNS = [
  { key: 'name', label: 'Клиент' },
  { key: 'spend_today', label: 'Сегодня', num: true },
  { key: 'spend_yesterday', label: 'Вчера', num: true },
  { key: 'spend_7d', label: '7 дней', num: true },
  { key: 'kpi', label: 'KPI 7 дн.', num: true },
  { key: 'budget', label: 'Темп бюджета', num: true },
  { key: 'balance', label: 'Баланс', num: true },
  { key: 'problems', label: 'Проблемы', num: true },
]
// Чем «хуже» клиент, тем больше значение — сортировка по убыванию поднимает проблемных наверх.
const SORT_VALUES = {
  name: (c) => c.name.toLowerCase(),
  spend_today: (c) => c.spend_today,
  spend_yesterday: (c) => c.spend_yesterday,
  spend_7d: (c) => c.spend_7d,
  kpi: (c) => c.kpi.ratio ?? -1,
  budget: (c) => c.budget?.pace_pct ?? -1,
  balance: (c) => (c.days_on_balance == null ? -Infinity : -c.days_on_balance),
  problems: (c) => -SEVERITY_RANK[c.severity] * 1000 + c.problems.critical * 100 + c.problems.warning * 10 + c.problems.info,
}

const router = useRouter()
const loading = ref(true)
const refreshing = ref(false)
const refreshText = ref('')
const data = ref({ clients: [], problems: [], totals: {} })
const search = ref('')
const severityFilter = ref('all')
const clientFilter = ref('')
const showAllProblems = ref(false)
const problemsPanel = ref(null)
const view = reactive(loadView())

function loadView() {
  const fallback = { sort: 'problems', dir: 'desc', onlyProblems: false }
  try {
    return { ...fallback, ...JSON.parse(localStorage.getItem(VIEW_KEY) || '{}') }
  } catch {
    return fallback
  }
}

watch(view, () => localStorage.setItem(VIEW_KEY, JSON.stringify(view)))
watch([severityFilter, clientFilter], () => {
  showAllProblems.value = false
})

const totals = computed(() => data.value.totals || {})
const hasAccounts = computed(() => data.value.clients.some((c) => c.accounts))
const fireCount = computed(() => data.value.problems.filter((p) => p.severity !== 'info').length)

const filteredProblems = computed(() =>
  data.value.problems.filter((p) => {
    if (severityFilter.value === 'fire' && p.severity === 'info') return false
    if (severityFilter.value === 'critical' && p.severity !== 'critical') return false
    if (clientFilter.value && !p.client_ids.some((id) => clientKey(id) === clientFilter.value)) return false
    return true
  }),
)
const visibleProblems = computed(() =>
  showAllProblems.value ? filteredProblems.value : filteredProblems.value.slice(0, PROBLEMS_LIMIT),
)

const sortedClients = computed(() => {
  const needle = search.value.trim().toLowerCase()
  const rows = data.value.clients.filter(
    (c) => (!needle || c.name.toLowerCase().includes(needle)) && (!view.onlyProblems || c.severity),
  )
  const value = SORT_VALUES[view.sort] || SORT_VALUES.problems
  const sign = view.dir === 'asc' ? 1 : -1
  return rows.sort((a, b) => {
    const va = value(a)
    const vb = value(b)
    if (va < vb) return -sign
    if (va > vb) return sign
    return a.name.localeCompare(b.name, 'ru')
  })
})

function clientKey(id) {
  return id == null ? 'none' : String(id)
}

function sortBy(key) {
  if (view.sort === key) {
    view.dir = view.dir === 'asc' ? 'desc' : 'asc'
  } else {
    view.sort = key
    view.dir = key === 'name' ? 'asc' : 'desc'
  }
}

function focusClient(id) {
  clientFilter.value = clientKey(id)
  severityFilter.value = 'all'
  problemsPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function rub(kopecks) {
  if (kopecks == null) return '—'
  return `${Math.round(Number(kopecks) / 100).toLocaleString('ru-RU')} ₽`
}

function kpiValue(kpi) {
  if (kpi.value == null) return '—'
  return kpi.type === 'cpa' ? rub(kpi.value) : `${kpi.value}%`
}

function kpiTarget(kpi) {
  const name = kpi.type === 'cpa' ? 'CPA' : 'ДРР'
  if (kpi.target == null) return `${name}, цель не задана`
  return kpi.type === 'cpa' ? `${name}, цель ${rub(kpi.target * 100)}` : `${name}, цель ${kpi.target}%`
}

function balanceTone(days) {
  if (days < 2) return 'tone-bad'
  if (days < 5) return 'tone-warn'
  return ''
}

function problemWhere(p) {
  return [p.client_name, p.object_name].filter(Boolean).join(' · ')
}

function formatTime(iso) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

async function load() {
  try {
    data.value = await webApi.overview()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  refreshText.value = 'Ставим задачу…'
  try {
    const result = await webApi.overviewRefresh((job) => {
      refreshText.value = `Обновляем: ${jobProgressText(job)}`
    })
    await load()
    const errors = (result.direct?.errors || 0) + (result.balances?.errors || 0)
    pushToast(errors ? `Обновлено, но с ошибками: ${errors} — см. «Где горит»` : 'Расход за сегодня обновлён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    refreshing.value = false
  }
}

onMounted(() => {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  load()
})
</script>
