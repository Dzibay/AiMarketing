<template>
  <HomeAsk />

  <PageHeader v-if="expert && rows.length">
    <template #actions>
      <button class="btn btn--ghost" type="button" :disabled="refreshing || loading" @click="refresh">
        <RefreshCw aria-hidden="true" />
        {{ refreshing ? 'Обновляем…' : 'Обновить расход за сегодня' }}
      </button>
    </template>
  </PageHeader>

  <OnboardingChecklist @advanced="load" />

  <div v-if="loading" class="panel"><p class="empty">Собираем обзор…</p></div>

  <!-- Простой режим: решения, сигналы, неделя в трёх цифрах и карточки проектов. -->
  <template v-else-if="!expert">
    <section v-if="!rows.length" class="panel">
      <p class="empty">
        Здесь появятся ваши сайты: сколько потрачено, сколько заявок и что требует внимания. Начните с подключения
        Метрики и Директа в <RouterLink to="/cabinet/connections">подключениях</RouterLink>.
      </p>
    </section>
    <template v-else>
      <PendingDecisions />
      <SignalCards :problems="data.problems" show-project />

      <h2 class="section-h">
        За неделю
        <small class="home-link"><RouterLink to="/cabinet/analytics">Все отчёты</RouterLink></small>
      </h2>
      <div class="bigstats">
        <div v-for="t in weekTiles" :key="t.label" class="bigstat">
          <span>{{ t.label }}<TermHint v-if="t.term" :term="t.term" /></span>
          <b>{{ t.value }}</b>
          <small :class="t.tone">{{ t.note }}</small>
        </div>
      </div>

      <h2 class="section-h">Проекты <span class="cgroup__n">{{ projectCards.length }}</span></h2>
      <div class="pstrip">
        <RouterLink v-for="w in projectCards" :key="w.id" class="pcard" :to="`/w/${w.id}/summary`">
          <b>{{ w.name }}</b>
          <span class="st" :class="`st--${w.status.tone}`">
            <component :is="w.status.icon" :size="14" aria-hidden="true" />
            {{ w.status.text }}
          </span>
          <p>{{ w.verdict }}</p>
        </RouterLink>
      </div>
      <ExpertHint>Расход за сегодня, темп бюджета, балансы и таблица всех проектов</ExpertHint>
    </template>
  </template>

  <template v-else>
    <section v-if="!rows.length" class="panel">
      <p class="empty">
        Здесь появятся все сайты клиентов: расход, KPI, бюджет и что горит. Начните с подключения Метрики и Директа в
        <RouterLink to="/cabinet/connections">подключениях</RouterLink>.
      </p>
    </section>

    <template v-else>
      <p v-if="refreshing" class="sync-status overview-updated">{{ refreshText }}</p>

      <div class="ktiles">
        <KpiTile label="Сегодня" :value="rub(totals.spend_today)" :note="todayNote" />
        <KpiTile
          label="Вчера"
          :value="rub(totals.spend_yesterday)"
          :change="yesterdayChange"
          :versus="yesterdayVersus"
          :note="yesterdayVersus"
        />
        <KpiTile
          label="Расход 7 дней"
          :value="rub(totals.spend_7d)"
          :change="weekChange.cost"
          versus="к прошлым 7 дням"
          note="к прошлым 7 дням"
          :points="lastWeek"
          :prev-points="prevWeek"
          value-key="cost_kopecks"
        />
        <KpiTile
          label="Конверсии 7 дней"
          :value="num(totals.conversions_7d || 0)"
          :change="weekChange.conv"
          good="up"
          versus="к прошлым 7 дням"
          note="к прошлым 7 дням"
          :points="lastWeek"
          :prev-points="prevWeek"
          value-key="conversions"
        />
        <KpiTile
          label="CPA 7 дней"
          :value="totals.conversions_7d ? rub(totals.spend_7d / totals.conversions_7d) : '—'"
          :change="weekChange.cpa"
          good="down"
          versus="к прошлым 7 дням"
          note="к прошлым 7 дням"
          :points="lastWeek"
          :prev-points="prevWeek"
          value-key="cpa_kopecks"
        />
      </div>

      <section class="panel overview-spaces">
        <div class="panel__head">
          <h2>Проекты <span class="cgroup__n">{{ projectCount }}</span></h2>
        </div>
        <div class="ltools" role="search">
          <label class="ltools__search">
            <Search aria-hidden="true" />
            <input v-model.trim="search" type="search" placeholder="Сайт или клиент" aria-label="Поиск по проектам" />
          </label>
          <UiSwitch v-model="onlyProblems" class="ltools__switch" :disabled="!problemProjects && !onlyProblems">
            С проблемами<span class="seg__n">{{ problemProjects }}</span>
          </UiSwitch>
        </div>
        <p v-if="!tableItems.length" class="empty">Нет проектов под фильтр.</p>
        <div v-else class="table-scroll">
          <table class="table overview-table">
            <thead>
              <tr>
                <th v-for="col in COLUMNS" :key="col.key" :class="{ num: col.num }" :aria-sort="ariaSort(col.key)">
                  <button class="th-sort" type="button" v-tip="sortTitle(col)" @click="sortBy(col.key)">
                    {{ col.label }}{{ sortMark(col.key) }}
                  </button>
                  <TermHint v-if="col.term" :term="col.term" />
                </th>
              </tr>
            </thead>
            <tbody>
              <template v-for="item in tableItems" :key="item.key">
                <tr v-if="item.folder" class="folder-row">
                  <td>
                    <strong>{{ item.folder.name }}</strong>
                    <small class="cell-note">
                      {{ item.folder.workspace_ids.length }}
                      {{ plural(item.folder.workspace_ids.length, 'сайт', 'сайта', 'сайтов') }}
                    </small>
                  </td>
                  <td class="num">{{ rub(item.folder.spend_today) }}</td>
                  <td class="num">{{ rub(item.folder.spend_yesterday) }}</td>
                  <td class="num">{{ rub(item.folder.spend_7d) }}</td>
                  <td class="num"></td>
                  <td class="num">
                    <template v-if="item.folder.budget">
                      <span :class="BUDGET_TONES[item.folder.budget.status]">
                        {{ pct(item.folder.budget.pace_pct, 0) }}
                      </span>
                      <small class="cell-note">общий бюджет {{ rub(item.folder.budget.budget_kopecks) }}</small>
                    </template>
                  </td>
                  <td class="num"></td>
                  <td class="num"></td>
                </tr>
                <tr
                  v-else
                  class="ws-row"
                  :class="{ 'is-link': item.w.id, 'is-child': item.child }"
                  @click="item.w.id && openWs(item.w.id)"
                >
                  <td>
                    <div class="ovt-name">
                      <span class="sevdot" :class="`is-${item.w.severity || 'ok'}`" v-tip="severityTitle(item.w)"></span>
                      <div>
                        <RouterLink v-if="item.w.id" :to="`/w/${item.w.id}/summary`" @click.stop>
                          <strong>{{ item.w.name }}</strong>
                        </RouterLink>
                        <strong v-else>{{ item.w.name }}</strong>
                        <small class="cell-note">
                          <template v-if="item.w.id">
                            <template v-if="!item.child && item.w.client_name && item.w.client_name !== item.w.name">
                              {{ item.w.client_name }} ·
                            </template>
                            № {{ item.w.counter_id }} · {{ item.w.campaigns }}
                            {{ plural(item.w.campaigns, 'кампания', 'кампании', 'кампаний') }}
                            <template v-if="lockedIds.has(item.w.id)">
                              · <span class="tone-warn"><Lock class="inline-icon" aria-hidden="true" /> на тарифе выше</span>
                            </template>
                            <template v-else-if="!item.w.counter_ok"> · <span class="tone-warn">счётчик недоступен</span></template>
                            <template v-if="item.w.accounts && !item.w.stats_synced_at"> · статистика не загружена</template>
                          </template>
                          <template v-else>Кампании, которые не входят ни в один проект</template>
                        </small>
                      </div>
                    </div>
                  </td>
                  <td class="num">{{ rub(item.w.spend_today) }}</td>
                  <td class="num">
                    {{ rub(item.w.spend_yesterday) }}
                    <small class="cell-note">{{ num(item.w.conversions_yesterday) }} конв.</small>
                  </td>
                  <td class="num">
                    {{ rub(item.w.spend_7d) }}
                    <small class="cell-note">{{ num(item.w.conversions_7d) }} конв.</small>
                  </td>
                  <td class="num">
                    <template v-if="item.w.kpi?.type">
                      <span :class="KPI_TONES[item.w.kpi.status]">{{ kpiValue(item.w.kpi) }}</span>
                      <small class="cell-note">{{ kpiTarget(item.w.kpi) }}</small>
                    </template>
                    <template v-else>
                      {{ item.w.cpa_7d != null ? rub(item.w.cpa_7d) : '—' }}
                      <small class="cell-note">цель не задана</small>
                    </template>
                  </td>
                  <td class="num">
                    <template v-if="item.w.budget">
                      <span :class="BUDGET_TONES[item.w.budget.status]">
                        {{ pct(item.w.budget.pace_pct, 0) }}
                      </span>
                      <small class="cell-note">
                        {{ rub(item.w.budget.spent_kopecks) }} из {{ rub(item.w.budget.budget_kopecks) }}
                      </small>
                    </template>
                    <template v-else>—</template>
                  </td>
                  <td class="num">
                    {{ item.w.balance_kopecks != null ? rub(item.w.balance_kopecks) : '—' }}
                    <small v-if="item.w.days_on_balance != null" class="cell-note" :class="balanceTone(item.w.days_on_balance)">
                      ≈ {{ daysShort(item.w.days_on_balance) }}
                    </small>
                  </td>
                  <td class="num">
                    <button
                      v-if="item.w.severity"
                      class="th-sort problem-counts"
                      type="button"
                      v-tip="'Показать проблемы проекта'"
                      @click.stop="focusWorkspace(item.w.id)"
                    >
                      <span v-if="item.w.problems.critical" class="tone-bad">
                        {{ item.w.problems.critical }}
                        {{ plural(item.w.problems.critical, 'критичная', 'критичные', 'критичных') }}
                      </span>
                      <span v-if="item.w.problems.warning" class="tone-warn">
                        {{ item.w.problems.warning }}
                        {{ plural(item.w.problems.warning, 'предупреждение', 'предупреждения', 'предупреждений') }}
                      </span>
                      <span v-if="item.w.problems.info" class="cell-note">{{ item.w.problems.info }} к сведению</span>
                    </button>
                    <span v-else class="cell-note">нет</span>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </section>

      <section ref="problemsPanel" class="panel overview-problems">
        <div class="panel__head">
          <h2>Где горит <span class="cgroup__n">{{ fireCount }}</span></h2>
          <div class="filters filters--inline">
            <UiSelect
              v-model="severityFilter"
              size="sm"
              aria-label="Серьёзность проблем"
              :options="[
                { value: 'all', label: 'Все' },
                { value: 'fire', label: 'Критично и внимание' },
                { value: 'critical', label: 'Только критично' },
              ]"
            />
            <UiSelect
              v-model="wsFilter"
              size="sm"
              aria-label="Проект"
              :options="[{ value: '', label: 'Все проекты' }, ...rows.map((w) => ({ value: wsKey(w.id), label: w.name }))]"
            />
          </div>
        </div>
        <p v-if="!filteredProblems.length" class="empty">
          {{
            data.problems.length
              ? 'По выбранным фильтрам ничего.'
              : 'Ничего не горит: токены живы, балансы, бюджеты и KPI в норме.'
          }}
        </p>
        <ProblemList v-else :problems="visibleProblems" show-project />
        <div v-if="filteredProblems.length > visibleProblems.length" class="pager">
          <span class="sync-status">Показано {{ visibleProblems.length }} из {{ filteredProblems.length }}</span>
          <button class="btn btn--ghost" type="button" @click="showAllProblems = true">Показать все</button>
        </div>
      </section>
    </template>
  </template>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { CircleCheck, Lock, RefreshCw, Search, TriangleAlert } from '@lucide/vue'
import ExpertHint from '../components/ExpertHint.vue'
import HomeAsk from '../components/HomeAsk.vue'
import KpiTile from '../components/KpiTile.vue'
import OnboardingChecklist from '../components/OnboardingChecklist.vue'
import PendingDecisions from '../components/PendingDecisions.vue'
import ProblemList from '../components/ProblemList.vue'
import SignalCards from '../components/SignalCards.vue'
import TermHint from '../components/TermHint.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import UiSelect from '../components/ui/UiSelect.vue'
import UiSwitch from '../components/ui/UiSwitch.vue'
import { errorText, jobProgressText, webApi } from '../api/web'
import { useExpert } from '../composables/useExpert'
import { pushToast } from '../composables/useToasts'
import { useWorkspaces } from '../composables/useWorkspaces'
import { useHead } from '../utils/useHead'
import { leadWord } from '../utils/campaignHealth'
import {
  asOfNote,
  balanceTone,
  BUDGET_TONES,
  daysShort,
  KPI_TONES,
  KPI_PLAIN,
  kpiTarget,
  kpiValue,
  num,
  pct,
  plural,
  rub,
  rubles,
} from '../utils/format'

useHead('Главная — Толк', true)

const PROBLEMS_LIMIT = 30
const SEVERITY_RANK = { critical: 0, warning: 1, info: 2, '': 3 }
// «Вчера» сравниваем с тем же днём недели неделю назад — полный день с полным; индекс — getUTCDay().
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
const { expert } = useExpert()
const { loadWorkspaces, workspaces } = useWorkspaces()
// Проекты сверх лимита тарифа: в обзоре видны, но открываются экраном «на тарифе выше».
const lockedIds = computed(() => new Set(workspaces.value.filter((w) => w.locked).map((w) => w.id)))
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

watch([severityFilter, wsFilter], () => {
  showAllProblems.value = false
})

const rows = computed(() => data.value.workspaces || [])
const totals = computed(() => data.value.totals || {})
const fireCount = computed(() => data.value.problems.filter((p) => p.severity !== 'info').length)
const projectCount = computed(() => rows.value.filter((w) => w.id != null).length)
const problemProjects = computed(() => rows.value.filter((w) => w.id != null && w.severity).length)

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
    conv: change(conv, prevConv),
    cpa: conv && prevConv ? change(cost / conv, prevCost / prevConv) : null,
  }
})

/** «на 12% больше, чем неделей раньше»; inverse — рост это плохо (расход, цена). */
function versus(value, more, less, inverse = false) {
  if (value == null) return { note: 'неделей раньше не с чем сравнить', tone: '' }
  const v = Math.round(value)
  if (!v) return { note: 'как неделей раньше', tone: '' }
  const bad = inverse ? v > 0 : v < 0
  return { note: `на ${pct(Math.abs(v), 0)} ${v > 0 ? more : less}, чем неделей раньше`, tone: Math.abs(v) < 5 ? '' : bad ? 'is-warn' : 'is-ok' }
}

const weekTiles = computed(() => {
  const t = totals.value
  const c = weekChange.value
  return [
    { label: 'Потрачено', value: rub(t.spend_7d), ...versus(c.cost, 'больше', 'меньше', true) },
    { label: 'Заявки', term: 'leads', value: num(t.conversions_7d || 0), ...versus(c.conv, 'больше', 'меньше') },
    {
      label: 'Цена заявки',
      term: 'cpa',
      value: t.conversions_7d ? rub(t.spend_7d / t.conversions_7d) : '—',
      ...versus(c.cpa, 'дороже', 'дешевле', true),
    },
  ]
})

const STATUS = {
  bad: { tone: 'bad', icon: TriangleAlert, text: 'Есть проблема' },
  warn: { tone: 'warn', icon: TriangleAlert, text: 'Стоит посмотреть' },
  ok: { tone: 'ok', icon: CircleCheck, text: 'Всё в порядке' },
}

/** Карточка проекта простыми словами: статус по сигналам и KPI, одна строка итога недели. */
const projectCards = computed(() =>
  rows.value
    .filter((w) => w.id != null)
    .map((w) => {
      const kpiBad = w.kpi?.type && (w.kpi.status === 'warn' || w.kpi.status === 'bad')
      const status = w.severity === 'critical' ? STATUS.bad : w.severity === 'warning' || kpiBad ? STATUS.warn : STATUS.ok
      let verdict
      if (lockedIds.value.has(w.id)) verdict = 'Доступен на тарифе выше'
      else if (w.kpi?.type && w.kpi.value != null) {
        const k = w.kpi
        const target = k.target == null ? '' : `, цель ${k.type === 'cpa' ? rubles(k.target) : pct(k.target)}`
        verdict = `${KPI_PLAIN[k.type]} за неделю — ${kpiValue(k)}${target}`
      }
      else if (w.spend_7d) verdict = `${rub(w.spend_7d)} за неделю, ${num(w.conversions_7d || 0)} ${leadWord(w.conversions_7d || 0)}`
      else verdict = 'За неделю расхода не было'
      return { ...w, status, verdict }
    })
    .sort(
      (a, b) =>
        SEVERITY_RANK[a.severity || ''] - SEVERITY_RANK[b.severity || ''] || a.name.localeCompare(b.name, 'ru'),
    ),
)

function shiftDay(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

function weekdayOf(iso) {
  return new Date(`${iso}T00:00:00Z`).getUTCDay()
}

// «Сегодня» — неполный день: почасовых данных нет, поэтому ни с чем не сравниваем, только говорим, на какое время цифра.
const todayNote = computed(() => asOfNote(data.value.today_updated_at))
const weekAgoYesterday = computed(() => {
  const iso = data.value.yesterday
  return iso ? byDay.value.find((r) => r.day === shiftDay(iso, -7)) || null : null
})
const yesterdayChange = computed(() => {
  const prev = weekAgoYesterday.value?.cost_kopecks
  return prev ? ((totals.value.spend_yesterday - prev) / prev) * 100 : null
})
const yesterdayVersus = computed(() =>
  weekAgoYesterday.value ? WEEKDAY_TO_LAST[weekdayOf(weekAgoYesterday.value.day)] : '',
)

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

// --- Таблица проектов: по умолчанию по клиентам; клик по колонке — плоский список с сортировкой ---

const COLUMNS = [
  { key: 'name', label: 'Проект' },
  { key: 'spend_today', label: 'Сегодня', num: true, term: 'spend_today' },
  { key: 'spend_yesterday', label: 'Вчера', num: true },
  { key: 'spend_7d', label: '7 дней', num: true },
  { key: 'kpi', label: 'KPI 7 дн.', num: true, term: 'kpi7' },
  { key: 'pace', label: 'Бюджет', num: true, term: 'pace' },
  { key: 'balance', label: 'Баланс', num: true, term: 'balance' },
  { key: 'problems', label: 'Проблемы', num: true },
]
const SORT_VALUE = {
  name: (w) => w.name,
  spend_today: (w) => w.spend_today,
  spend_yesterday: (w) => w.spend_yesterday,
  spend_7d: (w) => w.spend_7d,
  kpi: (w) => w.kpi?.ratio ?? null,
  pace: (w) => w.budget?.pace_pct ?? null,
  balance: (w) => w.days_on_balance ?? null,
  problems: (w) => (w.problems ? w.problems.critical * 10000 + w.problems.warning * 100 + w.problems.info : 0),
}
// Первый клик ставит важное сверху: имя — по алфавиту, дни на балансе — меньше сверху, остальное — больше сверху.
const ASC_FIRST = new Set(['name', 'balance'])
const sort = reactive({ key: '', desc: false })

function sortBy(key) {
  if (sort.key !== key) {
    sort.key = key
    sort.desc = !ASC_FIRST.has(key)
  } else if (sort.desc === !ASC_FIRST.has(key)) {
    sort.desc = !sort.desc
  } else {
    // Третий клик — обратно к группировке по клиентам.
    sort.key = ''
  }
}

function sortMark(key) {
  return sort.key === key ? (sort.desc ? ' ↓' : ' ↑') : ''
}

function ariaSort(key) {
  if (sort.key !== key) return undefined
  return sort.desc ? 'descending' : 'ascending'
}

function sortTitle(col) {
  if (sort.key !== col.key) return `Сортировать по колонке «${col.label}»`
  return sort.desc === !ASC_FIRST.has(col.key) ? 'Обратный порядок' : 'Вернуть группировку по клиентам'
}

function compareBy(key, desc) {
  const value = SORT_VALUE[key]
  return (a, b) => {
    const x = value(a)
    const y = value(b)
    // Пустые значения — всегда внизу, в какую сторону ни сортируй.
    if (x == null || y == null) return x == null && y == null ? 0 : x == null ? 1 : -1
    const c = typeof x === 'string' ? x.localeCompare(y, 'ru') : x - y
    return desc ? -c : c
  }
}

function severityTitle(w) {
  if (w.severity === 'critical') return 'Есть критичные проблемы'
  if (w.severity === 'warning') return 'Есть предупреждения'
  if (w.severity === 'info') return 'Есть замечания к сведению'
  return 'Всё в порядке'
}

const unassignedRow = computed(() => {
  const u = rows.value.find((r) => r.id == null)
  return u && (u.spend_7d || u.accounts) ? u : null
})

/**
 * Строки таблицы. Подзаголовок клиента — только когда у клиента больше одного сайта: иначе он повторял бы строку
 * проекта. Одиночные проекты идут первыми, клиенты с несколькими сайтами — после, чтобы строка без клиента не
 * читалась как часть группы над ней. «Вне проектов» — всегда последней.
 */
const tableItems = computed(() => {
  const visible = rows.value.filter((w) => w.id != null && matches(w))
  const extra = unassignedRow.value && matches(unassignedRow.value) ? [{ key: 'wnone', w: unassignedRow.value }] : []
  if (sort.key) {
    return [...visible.sort(compareBy(sort.key, sort.desc)).map((w) => ({ key: `w${w.id}`, w })), ...extra]
  }
  const byId = new Map(visible.map((w) => [w.id, w]))
  const single = []
  const groups = []
  for (const f of data.value.folders || []) {
    const items = f.workspace_ids.map((id) => byId.get(id)).filter(Boolean).sort(bySeverity)
    if (!items.length) continue
    if (f.id != null && f.workspace_ids.length > 1) {
      groups.push({ key: `f${f.id}`, folder: f })
      groups.push(...items.map((w) => ({ key: `w${w.id}`, w, child: true })))
    } else {
      single.push(...items)
    }
  }
  // Проекты, которых нет ни в одной папке (на всякий случай), — к одиночным.
  const grouped = new Set((data.value.folders || []).flatMap((f) => f.workspace_ids))
  single.push(...visible.filter((w) => !grouped.has(w.id)))
  single.sort(bySeverity)
  return [...single.map((w) => ({ key: `w${w.id}`, w })), ...groups, ...extra]
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
