<template>
  <AppShell>
    <div class="cabinet__head">
      <div>
        <h1>Обзор</h1>
        <p>Все сайты и клиенты на одном экране: расход, KPI, бюджет, баланс и что требует внимания.</p>
      </div>
      <div class="row-actions">
        <button class="btn btn--accent" type="button" :disabled="refreshing || loading" @click="refresh">
          {{ refreshing ? 'Обновляем…' : 'Обновить расход за сегодня' }}
        </button>
      </div>
    </div>

    <OnboardingChecklist @advanced="load" />

    <div v-if="loading" class="panel"><p class="empty">Собираем обзор…</p></div>

    <template v-else>
      <section v-if="!rows.length" class="panel">
        <p class="empty">
          Здесь появятся все сайты клиентов: расход, KPI, бюджет и что горит. Начните с подключения Метрики и Директа в
          <RouterLink to="/cabinet/accounts">аккаунтах</RouterLink>.
        </p>
      </section>

      <template v-else>
        <p class="sync-status overview-updated">
          <template v-if="refreshing">{{ refreshText }}</template>
          <template v-else-if="data.today_updated_at">
            Расход за сегодня — на {{ formatDateTime(data.today_updated_at) }}, Директ отдаёт его с задержкой.
          </template>
          <template v-else>Расход за сегодня ещё не загружался — нажмите «Обновить».</template>
        </p>

        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-card__head">
              <span class="stat-card__icon"><CalendarClock aria-hidden="true" /></span>
              <small>Сегодня</small>
            </div>
            <strong>{{ rub(totals.spend_today) }}</strong>
            <span v-if="weekAgo.today" class="cell-note">
              {{ WEEKDAY_LAST[weekdayOf(weekAgo.today.day)] }} — {{ rub(weekAgo.today.cost_kopecks) }} за день
            </span>
          </div>
          <div class="stat-card">
            <div class="stat-card__head">
              <span class="stat-card__icon is-teal"><Wallet aria-hidden="true" /></span>
              <small>Вчера</small>
            </div>
            <div class="stat-card__value">
              <strong>{{ rub(totals.spend_yesterday) }}</strong>
              <span v-if="yesterdayChange != null" class="delta" :class="deltaTone(yesterdayChange, true)">
                {{ delta(yesterdayChange) }}
              </span>
            </div>
            <span v-if="weekAgo.yesterday" class="cell-note">
              {{ WEEKDAY_TO_LAST[weekdayOf(weekAgo.yesterday.day)] }} — {{ rub(weekAgo.yesterday.cost_kopecks) }}
            </span>
          </div>
          <div class="stat-card">
            <div class="stat-card__head">
              <span class="stat-card__icon"><Target aria-hidden="true" /></span>
              <small>Конверсии вчера</small>
            </div>
            <strong>{{ num(totals.conversions_yesterday || 0) }}</strong>
            <span class="cell-note">по дням за неделю</span>
            <Sparkline :points="lastWeek" :prev-points="prevWeek" value-key="conversions" />
          </div>
          <div class="stat-card">
            <div class="stat-card__head">
              <span class="stat-card__icon is-blue"><Coins aria-hidden="true" /></span>
              <small>7 дней</small>
            </div>
            <div class="stat-card__value">
              <strong>{{ rub(totals.spend_7d) }}</strong>
              <span v-if="weekChange.cost != null" class="delta" :class="deltaTone(weekChange.cost, true)">
                {{ delta(weekChange.cost) }}
              </span>
            </div>
            <span class="cell-note">к прошлой неделе</span>
            <Sparkline :points="lastWeek" :prev-points="prevWeek" value-key="cost_kopecks" color="var(--c-blue)" />
          </div>
          <div class="stat-card">
            <div class="stat-card__head">
              <span class="stat-card__icon is-amber"><Crosshair aria-hidden="true" /></span>
              <small>CPA 7 дней</small>
            </div>
            <div class="stat-card__value">
              <strong>{{ totals.conversions_7d ? rub(totals.spend_7d / totals.conversions_7d) : '—' }}</strong>
              <span v-if="weekChange.cpa != null" class="delta" :class="deltaTone(weekChange.cpa, true)">
                {{ delta(weekChange.cpa) }}
              </span>
            </div>
            <span class="cell-note">к прошлой неделе</span>
            <Sparkline :points="lastWeek" :prev-points="prevWeek" value-key="cpa_kopecks" color="var(--c-amber)" />
          </div>
          <button
            class="stat-card stat-card--action"
            type="button"
            :disabled="!fireCount"
            :aria-pressed="onlyProblems"
            @click="toggleOnlyProblems"
          >
            <span class="stat-card__head">
              <span class="stat-card__icon" :class="fireCount ? 'is-danger' : ''"><TriangleAlert aria-hidden="true" /></span>
              <small>Горит</small>
            </span>
            <strong :class="fireCount ? 'tone-bad' : 'tone-ok'">{{ fireCount }}</strong>
            <span class="cell-note">
              {{ fireCount ? `${plural(fireCount, 'проблема требует', 'проблемы требуют', 'проблем требуют')} внимания` : 'всё спокойно' }}
            </span>
            <span v-if="fireCount" class="stat-card__action">
              {{ onlyProblems ? 'Показать все пространства' : 'Показать только их' }}
            </span>
          </button>
        </div>

        <section ref="spacesPanel" class="panel overview-spaces">
          <div class="panel__head">
            <h2>Пространства</h2>
            <div class="filters filters--inline">
              <input v-model="search" type="search" placeholder="Сайт или клиент" aria-label="Поиск по пространствам" />
              <label class="check-row">
                <input v-model="onlyProblems" type="checkbox" />
                Только с проблемами
              </label>
            </div>
          </div>
          <p class="hint">
            KPI — за 7 дней против цели, темп — прогноз расхода месяца против бюджета, баланс — на сколько дней
            хватит при расходе последней недели. Клик по строке открывает пространство.
          </p>
          <p v-if="!visibleFolders.length" class="empty">Нет пространств под фильтр.</p>
          <div v-else class="table-scroll">
            <table class="table overview-table">
              <thead>
                <tr>
                  <th>Сайт</th>
                  <th class="num">Сегодня</th>
                  <th class="num">Вчера</th>
                  <th class="num">7 дней</th>
                  <th class="num">KPI 7 дн.</th>
                  <th class="num">Темп бюджета</th>
                  <th class="num">Баланс</th>
                  <th class="num">Проблемы</th>
                </tr>
              </thead>
              <tbody v-for="folder in visibleFolders" :key="folder.key">
                <tr class="folder-row">
                  <td>
                    <strong>{{ folder.name }}</strong>
                    <small class="cell-note">{{ folder.rows.length }} {{ plural(folder.rows.length, 'сайт', 'сайта', 'сайтов') }}</small>
                  </td>
                  <td class="num">{{ rub(folder.spend_today) }}</td>
                  <td class="num">{{ rub(folder.spend_yesterday) }}</td>
                  <td class="num">{{ rub(folder.spend_7d) }}</td>
                  <td class="num"></td>
                  <td class="num">
                    <template v-if="folder.budget">
                      <span :class="BUDGET_TONES[folder.budget.status]">
                        {{ folder.budget.pace_pct != null ? `${folder.budget.pace_pct}%` : '—' }}
                      </span>
                      <small class="cell-note">общий бюджет {{ rub(folder.budget.budget_kopecks) }}</small>
                    </template>
                  </td>
                  <td class="num"></td>
                  <td class="num"></td>
                </tr>
                <tr
                  v-for="w in folder.rows"
                  :key="w.id ?? 'none'"
                  class="ws-row"
                  :class="[w.severity && `is-${w.severity}`, { 'is-link': w.id }]"
                  @click="w.id && openWs(w.id)"
                >
                  <td>
                    <RouterLink v-if="w.id" :to="`/w/${w.id}/summary`" @click.stop>
                      <strong>{{ w.name }}</strong>
                    </RouterLink>
                    <strong v-else>{{ w.name }}</strong>
                    <small class="cell-note">
                      <template v-if="w.id">
                        № {{ w.counter_id }} · {{ w.campaigns }} {{ plural(w.campaigns, 'кампания', 'кампании', 'кампаний') }}
                        <template v-if="!w.counter_ok"> · <span class="tone-warn">счётчик недоступен</span></template>
                        <template v-if="w.accounts && !w.stats_synced_at"> · статистика не загружена</template>
                      </template>
                      <template v-else>Кампании, которые не входят ни в одно пространство</template>
                    </small>
                  </td>
                  <td class="num">{{ rub(w.spend_today) }}</td>
                  <td class="num">
                    {{ rub(w.spend_yesterday) }}
                    <small class="cell-note">{{ num(w.conversions_yesterday) }} конв.</small>
                  </td>
                  <td class="num">
                    {{ rub(w.spend_7d) }}
                    <small class="cell-note">{{ num(w.conversions_7d) }} конв. · {{ num(w.clicks_7d) }} кл.</small>
                  </td>
                  <td class="num">
                    <template v-if="w.kpi?.type">
                      <span :class="KPI_TONES[w.kpi.status]">{{ kpiValue(w.kpi) }}</span>
                      <small class="cell-note">{{ kpiTarget(w.kpi) }}</small>
                    </template>
                    <template v-else>
                      {{ w.cpa_7d != null ? rub(w.cpa_7d) : '—' }}
                      <small class="cell-note">{{ w.cpa_7d != null ? 'CPA, KPI не задан' : 'KPI не задан' }}</small>
                    </template>
                  </td>
                  <td class="num">
                    <template v-if="w.budget">
                      <span :class="BUDGET_TONES[w.budget.status]">
                        {{ w.budget.pace_pct != null ? `${w.budget.pace_pct}%` : '—' }}
                      </span>
                      <small class="cell-note">{{ rub(w.budget.spent_kopecks) }} из {{ rub(w.budget.budget_kopecks) }}</small>
                    </template>
                    <template v-else>—</template>
                  </td>
                  <td class="num">
                    {{ w.balance_kopecks != null ? rub(w.balance_kopecks) : '—' }}
                    <small v-if="w.days_on_balance != null" class="cell-note" :class="balanceTone(w.days_on_balance)">
                      ≈ {{ w.days_on_balance }} дн.
                    </small>
                  </td>
                  <td class="num">
                    <button
                      v-if="w.severity"
                      class="th-sort problem-counts"
                      type="button"
                      title="Показать проблемы"
                      @click.stop="focusWorkspace(w.id)"
                    >
                      <span v-if="w.problems.critical" class="tone-bad">
                        {{ w.problems.critical }} {{ plural(w.problems.critical, 'критичная', 'критичные', 'критичных') }}
                      </span>
                      <span v-if="w.problems.warning" class="tone-warn">
                        {{ w.problems.warning }}
                        {{ plural(w.problems.warning, 'предупреждение', 'предупреждения', 'предупреждений') }}
                      </span>
                      <span v-if="w.problems.info" class="cell-note">{{ w.problems.info }} к сведению</span>
                    </button>
                    <span v-else class="tone-ok">✓</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section ref="problemsPanel" class="panel overview-problems">
          <div class="panel__head">
            <h2>Где горит</h2>
            <div class="filters filters--inline">
              <select v-model="severityFilter" aria-label="Серьёзность проблем">
                <option value="all">Все</option>
                <option value="fire">Критично и внимание</option>
                <option value="critical">Только критично</option>
              </select>
              <select v-model="wsFilter" aria-label="Пространство">
                <option value="">Все пространства</option>
                <option v-for="w in rows" :key="w.id ?? 'none'" :value="wsKey(w.id)">{{ w.name }}</option>
              </select>
            </div>
          </div>
          <p class="hint">
            Токены и синки, баланс и бюджет, KPI, модерация, остановленные кампании и резкие изменения вчерашнего дня.
          </p>
          <p v-if="!filteredProblems.length" class="empty">
            {{ data.problems.length ? 'По выбранным фильтрам ничего.' : 'Ничего не горит.' }}
          </p>
          <div v-else class="list">
            <div
              v-for="(p, i) in visibleProblems"
              :key="`${p.kind}-${p.workspace_id}-${p.account_id}-${p.campaign_row_id}-${i}`"
              class="list-row problem"
              :class="`is-${p.severity}`"
            >
              <div>
                <span class="chip" :class="`chip--${p.severity}`">{{ SEVERITY_LABELS[p.severity] }}</span>
                <strong>{{ p.title }}</strong>
                <small>
                  {{ [p.workspace_name, p.object_name].filter(Boolean).join(' · ') }}
                  <template v-if="p.detail"> · {{ errorDetail(p.detail) }}</template>
                </small>
              </div>
              <RouterLink v-if="problemLink(p)" class="btn btn--ghost" :to="problemLink(p).to">
                {{ problemLink(p).label }}
              </RouterLink>
            </div>
          </div>
          <div v-if="filteredProblems.length > visibleProblems.length" class="pager">
            <span class="sync-status">Показано {{ visibleProblems.length }} из {{ filteredProblems.length }}</span>
            <button class="btn btn--ghost" type="button" @click="showAllProblems = true">Показать все</button>
          </div>
        </section>
      </template>
    </template>
  </AppShell>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { CalendarClock, Coins, Crosshair, Target, TriangleAlert, Wallet } from '@lucide/vue'
import AppShell from '../components/AppShell.vue'
import OnboardingChecklist from '../components/OnboardingChecklist.vue'
import Sparkline from '../components/Sparkline.vue'
import { errorDetail, errorText, jobProgressText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { useWorkspaces } from '../composables/useWorkspaces'
import { useHead } from '../utils/useHead'
import {
  balanceTone,
  BUDGET_TONES,
  delta,
  deltaTone,
  formatDateTime,
  KPI_TONES,
  kpiTarget,
  kpiValue,
  problemLink,
  num,
  plural,
  rub,
  SEVERITY_LABELS,
} from '../utils/format'

useHead('Обзор — AiMarketing', true)

const PROBLEMS_LIMIT = 30
const SEVERITY_RANK = { critical: 0, warning: 1, info: 2, '': 3 }
// Сравнение дня с тем же днём недели неделю назад; индекс — getUTCDay().
const WEEKDAY_LAST = [
  'в прошлое воскресенье',
  'в прошлый понедельник',
  'в прошлый вторник',
  'в прошлую среду',
  'в прошлый четверг',
  'в прошлую пятницу',
  'в прошлую субботу',
]
const WEEKDAY_TO_LAST = [
  'к прошлому воскресенью',
  'к прошлому понедельнику',
  'к прошлому вторнику',
  'к прошлой среде',
  'к прошлому четвергу',
  'к прошлой пятнице',
  'к прошлой субботе',
]

const router = useRouter()
const { loadWorkspaces } = useWorkspaces()
const loading = ref(true)
const refreshing = ref(false)
const refreshText = ref('')
const data = ref({ workspaces: [], folders: [], problems: [], totals: {} })
const search = ref('')
const onlyProblems = ref(false)
const severityFilter = ref('all')
const wsFilter = ref('')
const showAllProblems = ref(false)
const problemsPanel = ref(null)
const spacesPanel = ref(null)

watch([severityFilter, wsFilter], () => {
  showAllProblems.value = false
})

const rows = computed(() => data.value.workspaces || [])
const totals = computed(() => data.value.totals || {})
const fireCount = computed(() => data.value.problems.filter((p) => p.severity !== 'info').length)

// by_day — две недели до вчера: последние 7 дней — эта неделя, первые 7 — прошлая (пунктир и изменение).
const byDay = computed(() =>
  (data.value.by_day || []).map((r) => ({ ...r, cpa_kopecks: r.conversions ? r.cost_kopecks / r.conversions : null })),
)
const lastWeek = computed(() => byDay.value.slice(-7))
const prevWeek = computed(() => byDay.value.slice(-14, -7))
const weekChange = computed(() => {
  const sum = (rows, key) => rows.reduce((acc, r) => acc + Number(r[key] || 0), 0)
  const change = (cur, prev) => (prev ? ((cur - prev) / prev) * 100 : null)
  const cost = sum(lastWeek.value, 'cost_kopecks')
  const prevCost = sum(prevWeek.value, 'cost_kopecks')
  const conv = sum(lastWeek.value, 'conversions')
  const prevConv = sum(prevWeek.value, 'conversions')
  return {
    cost: change(cost, prevCost),
    cpa: conv && prevConv ? change(cost / conv, prevCost / prevConv) : null,
  }
})

function shiftDay(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

function weekdayOf(iso) {
  return new Date(`${iso}T00:00:00Z`).getUTCDay()
}

// Тот же день недели неделю назад — для карточек «Сегодня» и «Вчера».
const weekAgo = computed(() => {
  const find = (iso) => (iso ? byDay.value.find((r) => r.day === shiftDay(iso, -7)) || null : null)
  return { today: find(data.value.today), yesterday: find(data.value.yesterday) }
})
const yesterdayChange = computed(() => {
  const prev = weekAgo.value.yesterday?.cost_kopecks
  return prev ? ((totals.value.spend_yesterday - prev) / prev) * 100 : null
})

function wsKey(id) {
  return id == null ? 'none' : String(id)
}

function matches(w) {
  const q = search.value.trim().toLowerCase()
  if (onlyProblems.value && !w.severity) return false
  if (!q) return true
  return [w.name, w.site, w.client_name, String(w.counter_id || '')].some((v) =>
    String(v || '').toLowerCase().includes(q),
  )
}

function bySeverity(a, b) {
  const s = SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]
  if (s) return s
  if (a.id == null) return 1
  if (b.id == null) return -1
  return a.name.localeCompare(b.name, 'ru')
}

const visibleFolders = computed(() => {
  const byId = new Map(rows.value.map((r) => [r.id, r]))
  const folders = (data.value.folders || []).map((f) => ({
    ...f,
    key: `f${f.id ?? 'none'}`,
    rows: f.workspace_ids.map((id) => byId.get(id)).filter(Boolean).filter(matches).sort(bySeverity),
  }))
  const unassigned = byId.get(null)
  if (unassigned && matches(unassigned) && (unassigned.spend_7d || unassigned.accounts)) {
    folders.push({
      key: 'none',
      id: null,
      name: 'Вне пространств',
      rows: [unassigned],
      spend_today: unassigned.spend_today,
      spend_yesterday: unassigned.spend_yesterday,
      spend_7d: unassigned.spend_7d,
      budget: null,
    })
  }
  return folders.filter((f) => f.rows.length)
})

const filteredProblems = computed(() =>
  data.value.problems.filter((p) => {
    if (severityFilter.value === 'fire' && p.severity === 'info') return false
    if (severityFilter.value === 'critical' && p.severity !== 'critical') return false
    if (wsFilter.value && !p.workspace_ids.some((id) => wsKey(id) === wsFilter.value)) return false
    return true
  }),
)
const visibleProblems = computed(() =>
  showAllProblems.value ? filteredProblems.value : filteredProblems.value.slice(0, PROBLEMS_LIMIT),
)


function openWs(id) {
  router.push(`/w/${id}/summary`)
}

// Карточка «Горит» включает фильтр таблицы и прокручивает к ней — на телефоне таблица ниже экрана.
function toggleOnlyProblems() {
  onlyProblems.value = !onlyProblems.value
  if (onlyProblems.value) spacesPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function focusWorkspace(id) {
  wsFilter.value = wsKey(id)
  severityFilter.value = 'all'
  problemsPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function load() {
  try {
    const [overview] = await Promise.all([webApi.overview(), loadWorkspaces(true)])
    data.value = overview
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

onMounted(load)
</script>
