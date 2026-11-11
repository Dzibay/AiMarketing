<template>
  <div class="ws-toolbar">
    <div class="seg" role="group" aria-label="Период статистики">
      <button v-for="d in [7, 14, 30]" :key="d" type="button" :class="{ active: days === d }" @click="setDays(d)">
        {{ d }} дней
      </button>
    </div>
    <p class="sync-status">
      <template v-if="data">
        {{ formatDate(data.date_from) }} — {{ formatDate(data.date_to) }} · конверсии каждого проекта — по его цели
      </template>
    </p>
    <div v-if="projects.length" class="row-actions">
      <RouterLink v-if="projects.length === 1" class="btn btn--accent" :to="newCampaignLink(projects[0].id)">
        Новая кампания
      </RouterLink>
      <details v-else ref="newMenu" class="bulk-more">
        <summary class="btn btn--accent">Новая кампания</summary>
        <div class="bulk-more__menu" role="menu">
          <p class="menu-note">В какой проект?</p>
          <RouterLink
            v-for="p in projects"
            :key="p.id"
            class="menu-item"
            role="menuitem"
            :to="newCampaignLink(p.id)"
          >
            {{ p.name }}
          </RouterLink>
        </div>
      </details>
    </div>
  </div>

  <template v-if="!expert">
    <p v-if="loading && !data" class="empty">Загружаем кампании…</p>
    <div v-else-if="!data" class="empty">
      {{ loadError || 'Не удалось загрузить кампании.' }}
      <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
    </div>
    <p v-else-if="!projects.length" class="empty">
      Проектов пока нет. Подключите Директ и Метрику в
      <RouterLink to="/cabinet/connections">подключениях</RouterLink> — проекты появятся по счётчикам.
    </p>
    <p v-else-if="!simpleRows.length" class="empty">
      В проектах нет кампаний. Нажмите «Новая кампания» — ИИ поможет собрать первую.
    </p>
    <CampaignGroups
      v-else
      :class="{ 'is-loading': loading }"
      :rows="simpleRows"
      :kpi-of="(c) => kpiByProject.get(c.project.id)"
      :busy="busy"
      :selected="selected"
      @toggle="quickToggle"
      @pick="toggle"
    />
    <ExpertHint v-if="data && simpleRows.length">Фильтры, архив, массовые действия и таблица со всеми цифрами</ExpertHint>
    <CampaignAsk
      v-if="data && simpleRows.length"
      :rows="simpleRows"
      :selected="selected"
      @unselect="toggle($event, false)"
    />
  </template>

  <section v-else class="panel">
    <div class="filters">
      <div v-field class="field field--wide">
        <label>Поиск</label>
        <input v-model.trim="filters.search" type="search" placeholder="название или ID кампании" />
      </div>
      <div v-if="projects.length > 1" v-field class="field">
        <label>Проект</label>
        <select v-model="filters.project">
          <option value="">Все проекты</option>
          <option v-for="p in projects" :key="p.id" :value="String(p.id)">{{ p.name }}</option>
        </select>
      </div>
      <div v-field class="field">
        <label>Состояние</label>
        <select v-model="filters.state">
          <option value="">Все, кроме архива</option>
          <option value="ON">Идут показы</option>
          <option value="OFF">Остановлены</option>
          <option value="SUSPENDED">Приостановлены</option>
          <option value="ENDED">Завершены</option>
          <option value="ARCHIVED">В архиве</option>
          <option value="*">Все</option>
        </select>
      </div>
      <label class="check-row">
        <input v-model="filters.wasteOnly" type="checkbox" />
        Расход без конверсий
      </label>
    </div>

    <div v-if="selected.size" class="bulk-bar">
      <span>Выбрано: {{ selected.size }}</span>
      <button v-if="picked.canResume" class="btn btn--ghost" type="button" :disabled="busy" @click="runBulk('resume')">
        Запустить
      </button>
      <button v-if="picked.canPause" class="btn btn--ghost" type="button" :disabled="busy" @click="runBulk('pause')">
        Остановить
      </button>
      <button class="btn btn--ghost" type="button" @click="selected = new Set()">Снять выбор</button>
    </div>

    <div v-if="bulkErrors.length" class="notice notice--warn">
      <strong>Не выполнено для {{ bulkErrors.length }}:</strong>
      <ul>
        <li v-for="e in bulkErrors" :key="e.campaign_row_id">
          {{ e.name || e.campaign_row_id }}<template v-if="e.client_login"> ({{ e.client_login }})</template>:
          {{ e.message || ERRORS[e.error] || e.error }}
        </li>
      </ul>
    </div>

    <p v-if="loading && !data" class="empty">Загружаем кампании…</p>
    <div v-else-if="!data" class="empty">
      {{ loadError || 'Не удалось загрузить кампании.' }}
      <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
    </div>
    <p v-else-if="!projects.length" class="empty">
      Проектов пока нет. Подключите Директ и Метрику в
      <RouterLink to="/cabinet/connections">подключениях</RouterLink> — проекты появятся по счётчикам.
    </p>
    <p v-else-if="!data.campaigns.length" class="empty">
      В проектах нет кампаний. Выберите проект вверху меню и добавьте кампании в его настройках.
    </p>
    <p v-else-if="!rows.length" class="empty">Под фильтр ничего не подходит.</p>
    <div v-else class="table-scroll" :class="{ 'is-loading': loading }">
      <table class="table">
        <thead>
          <tr>
            <th class="check-col">
              <input
                type="checkbox"
                :checked="rows.every((r) => selected.has(r.campaign_row_id))"
                aria-label="Выбрать все"
                @change="toggleAll($event.target.checked)"
              />
            </th>
            <th v-for="col in COLUMNS" :key="col.key" :class="{ num: col.num }">
              <button class="th-sort" type="button" @click="sortBy(col.key)">{{ col.label }}{{ sortMark(col.key) }}</button>
              <TermHint v-if="col.term" :term="col.term" />
            </th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in rows" :key="c.campaign_row_id" :class="{ 'is-off': c.state !== 'ON' }">
            <td class="check-col">
              <input
                type="checkbox"
                :checked="selected.has(c.campaign_row_id)"
                :aria-label="`Выбрать «${c.name}»`"
                @change="toggle(c.campaign_row_id, $event.target.checked)"
              />
            </td>
            <td>
              <RouterLink :to="`/w/${c.project.id}/campaigns/${c.campaign_row_id}`">{{ c.name }}</RouterLink>
              <small class="cell-note">
                {{ CAMPAIGN_TYPES[c.type] || c.type || '—' }} · {{ c.account_login }} · № {{ c.campaign_id }}
              </small>
            </td>
            <td>
              <RouterLink class="link-quiet" :to="`/w/${c.project.id}/campaigns`">{{ c.project.name }}</RouterLink>
            </td>
            <td>
              <span class="chip" :class="STATE_CHIPS[c.state] || ''">{{ CAMPAIGN_STATES[c.state] || c.state }}</span>
              <small v-if="c.status && c.status !== 'ACCEPTED'" class="cell-note">
                {{ CAMPAIGN_STATUSES[c.status] || c.status }}
              </small>
            </td>
            <td class="num">
              <template v-if="c.weekly_budget_rub">{{ rubles(c.weekly_budget_rub) }}/нед.</template>
              <template v-else-if="c.daily_budget_rub">{{ rubles(c.daily_budget_rub) }}/день</template>
              <template v-else>—</template>
            </td>
            <td class="num">{{ rub(c.cost_kopecks) }}</td>
            <td class="num">{{ num(c.clicks) }}</td>
            <td class="num">{{ c.ctr != null ? pct(c.ctr, 2) : '—' }}</td>
            <td class="num" :class="{ 'tone-bad': !c.conversions && c.cost_kopecks > 0 }">{{ num(c.conversions) }}</td>
            <td class="num">{{ c.cpa_kopecks != null ? rub(c.cpa_kopecks) : '—' }}</td>
            <td class="row-actions">
              <button
                v-if="c.state !== 'ARCHIVED' && (c.state === 'ON' || c.status !== 'DRAFT')"
                class="btn btn--ghost"
                type="button"
                :disabled="busy"
                @click="quickToggle(c)"
              >
                {{ c.state === 'ON' ? 'Стоп' : 'Старт' }}
              </button>
            </td>
          </tr>
        </tbody>
        <tfoot v-if="rows.length > 1">
          <tr>
            <td></td>
            <td>Итого: {{ rows.length }}</td>
            <td></td>
            <td></td>
            <td class="num" title="Сумма бюджетов запущенных кампаний; дневной × 7">
              {{ totals.budget ? `${rubles(totals.budget)}/нед.` : '—' }}
            </td>
            <td class="num">{{ rub(totals.cost) }}</td>
            <td class="num">{{ num(totals.clicks) }}</td>
            <td class="num">{{ totals.impressions ? pct((totals.clicks / totals.impressions) * 100, 2) : '—' }}</td>
            <td class="num">{{ num(totals.conversions) }}</td>
            <td class="num">{{ totals.conversions ? rub(totals.cost / totals.conversions) : '—' }}</td>
            <td></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ERRORS, errorText, webApi, wsApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { CAMPAIGN_STATES, CAMPAIGN_STATUSES, CAMPAIGN_TYPES, formatDate, lastDays, num, pct, rub, rubles } from '../utils/format'
import { useHead } from '../utils/useHead'
import TermHint from '../components/TermHint.vue'
import CampaignGroups from '../components/CampaignGroups.vue'
import CampaignAsk from '../components/CampaignAsk.vue'
import ExpertHint from '../components/ExpertHint.vue'
import { useExpert } from '../composables/useExpert'

const { expert } = useExpert()

useHead('Кампании — AiMarketing', true)

const COLUMNS = [
  { key: 'name', label: 'Кампания' },
  { key: 'project_name', label: 'Проект' },
  { key: 'state', label: 'Состояние' },
  { key: 'budget_week_rub', label: 'Бюджет', num: true },
  { key: 'cost_kopecks', label: 'Расход', num: true },
  { key: 'clicks', label: 'Клики', num: true },
  { key: 'ctr', label: 'CTR', num: true },
  { key: 'conversions', label: 'Конв.', num: true },
  { key: 'cpa_kopecks', label: 'CPA', num: true, term: 'cpa' },
]
const STATE_CHIPS = { ON: 'chip--ok', SUSPENDED: 'chip--warning' }
const ACTION_TEXT = { pause: 'Остановлено', resume: 'Запущено' }

const data = ref(null)
const loading = ref(false)
const loadError = ref('')
const days = ref(7)
const busy = ref(false)
const selected = ref(new Set())
const bulkErrors = ref([])
const sort = reactive({ key: 'cost_kopecks', desc: true })
const filters = reactive({ search: '', project: '', state: '', wasteOnly: false })
const newMenu = ref(null)
let seq = 0

const projects = computed(() => data.value?.projects || [])
const kpiByProject = computed(() => new Map(projects.value.map((p) => [p.id, p.kpi])))

// Простой режим: фильтры скрыты, поэтому берём всё, кроме архива, без учёта фильтров эксперта.
const simpleRows = computed(() =>
  (data.value?.campaigns || [])
    .filter((c) => c.state !== 'ARCHIVED')
    .map((c) => ({
      ...c,
      link: `/w/${c.project.id}/campaigns/${c.campaign_row_id}`,
      where: [CAMPAIGN_TYPES[c.type] || c.type, c.project.name].filter(Boolean).join(' · '),
    })),
)

const rows = computed(() => {
  const q = filters.search.toLowerCase()
  const list = (data.value?.campaigns || []).filter((c) => {
    if (filters.project && String(c.project.id) !== filters.project) return false
    if (filters.state === '' && c.state === 'ARCHIVED') return false
    if (filters.state && filters.state !== '*' && c.state !== filters.state) return false
    if (filters.wasteOnly && (c.conversions || !c.cost_kopecks)) return false
    if (q && !c.name.toLowerCase().includes(q) && !String(c.campaign_id).includes(q)) return false
    return true
  })
  const dir = sort.desc ? -1 : 1
  return list.map(withExtras).sort((a, b) => {
    const x = a[sort.key]
    const y = b[sort.key]
    if (x == null && y == null) return 0
    if (x == null) return 1
    if (y == null) return -1
    if (typeof x === 'string') return x.localeCompare(y, 'ru') * dir
    return (x - y) * dir
  })
})

const totals = computed(() =>
  rows.value.reduce(
    (t, c) => ({
      cost: t.cost + (c.cost_kopecks || 0),
      clicks: t.clicks + (c.clicks || 0),
      impressions: t.impressions + (c.impressions || 0),
      conversions: t.conversions + (c.conversions || 0),
      budget: t.budget + (c.state === 'ON' ? c.budget_week_rub || 0 : 0),
    }),
    { cost: 0, clicks: 0, impressions: 0, conversions: 0, budget: 0 },
  ),
)

// Что имеет смысл для выбранных: запустить нечего, если всё уже идёт; черновик Директ не запустит.
const picked = computed(() => {
  const items = (data.value?.campaigns || []).filter((c) => selected.value.has(c.campaign_row_id))
  return {
    canPause: items.some((c) => c.state === 'ON'),
    canResume: items.some((c) => c.state !== 'ON' && c.state !== 'ARCHIVED' && c.status !== 'DRAFT'),
  }
})

// Бюджет к одной мере — за неделю: так его можно сортировать и складывать.
function withExtras(c) {
  const week = c.weekly_budget_rub || (c.daily_budget_rub ? c.daily_budget_rub * 7 : null)
  return { ...c, budget_week_rub: week, project_name: c.project.name }
}

function newCampaignLink(projectId) {
  return { path: `/w/${projectId}/campaigns`, query: { new: 1 } }
}

function sortBy(key) {
  if (sort.key === key) sort.desc = !sort.desc
  else {
    sort.key = key
    sort.desc = !['name', 'project_name', 'state'].includes(key)
  }
}

function sortMark(key) {
  return sort.key === key ? (sort.desc ? ' ↓' : ' ↑') : ''
}

function toggle(id, on) {
  const next = new Set(selected.value)
  if (on) next.add(id)
  else next.delete(id)
  selected.value = next
}

function toggleAll(on) {
  selected.value = on ? new Set(rows.value.map((r) => r.campaign_row_id)) : new Set()
}

async function load() {
  const my = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const res = await webApi.portfolio(lastDays(days.value))
    if (my !== seq) return
    data.value = res
    const ids = new Set(res.campaigns.map((c) => c.campaign_row_id))
    selected.value = new Set([...selected.value].filter((id) => ids.has(id)))
  } catch (err) {
    if (my === seq) loadError.value = errorText(err)
  } finally {
    if (my === seq) loading.value = false
  }
}

function setDays(d) {
  days.value = d
  load()
}

// Действия идут через проект кампании — так они попадают в историю этого проекта.
async function runBulk(action, ids = [...selected.value]) {
  if (!ids.length) return
  const byProject = new Map()
  for (const c of data.value?.campaigns || []) {
    if (!ids.includes(c.campaign_row_id)) continue
    if (!byProject.has(c.project.id)) byProject.set(c.project.id, [])
    byProject.get(c.project.id).push(c.campaign_row_id)
  }
  busy.value = true
  bulkErrors.value = []
  let applied = 0
  try {
    for (const [projectId, campaignIds] of byProject) {
      const res = await wsApi.bulk(projectId, { campaign_ids: campaignIds, action })
      applied += res.applied || 0
      bulkErrors.value.push(...(res.errors || []))
    }
    pushToast(
      `${ACTION_TEXT[action]}: ${applied}${bulkErrors.value.length ? `, ошибок: ${bulkErrors.value.length}` : ''}`,
      bulkErrors.value.length ? 'error' : 'ok',
    )
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

function quickToggle(c) {
  return runBulk(c.state === 'ON' ? 'pause' : 'resume', [c.campaign_row_id])
}

onMounted(load)
</script>

<style scoped>
/* Кнопка стоит у правого края — меню раскрываем влево, иначе появляется горизонтальная прокрутка. */
.ws-toolbar > .row-actions {
  margin-left: auto;
}
.row-actions .bulk-more__menu {
  left: auto;
  right: 0;
}
.menu-note {
  margin: 0;
  padding: 0.4rem 0.7rem;
  color: var(--muted);
  font-size: 0.85rem;
}
.link-quiet {
  color: var(--ink);
}
</style>
