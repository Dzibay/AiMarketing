<template>
  <div class="ctab">
    <div class="ltools" role="search">
      <label class="ltools__search">
        <Search aria-hidden="true" />
        <input v-model.trim="search" type="search" placeholder="Название или № кампании" aria-label="Поиск кампании" />
      </label>
      <div class="seg" role="group" aria-label="Состояние кампаний">
        <button
          v-for="v in VIEWS"
          :key="v.id"
          type="button"
          :class="{ active: view === v.id }"
          :aria-pressed="view === v.id"
          @click="view = v.id"
        >
          {{ v.label }}<span class="seg__n">{{ counts[v.id] }}</span>
        </button>
      </div>
      <UiSelect
        v-if="scope && scope.options.length > 1"
        v-model="scopeValue"
        size="sm"
        :options="[{ value: '', label: scope.all }, ...scope.options]"
        :aria-label="scope.label"
      />
      <UiSwitch v-model="wasteOnly" class="ltools__switch">Без конверсий</UiSwitch>
    </div>

    <slot name="notices" />

    <div v-if="loading && !items.length" class="table-scroll" aria-busy="true">
      <table class="table ctab__t">
        <tbody>
          <tr v-for="i in 5" :key="i">
            <td><span class="ui-skel" :style="{ width: `${40 + ((i * 17) % 35)}%` }"></span></td>
            <td><span class="ui-skel" style="width: 70px"></span></td>
            <td class="num"><span class="ui-skel" style="width: 60px; margin-left: auto"></span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState v-else-if="!rows.length" :icon="SearchX" text="Под фильтр ничего не подходит." plain>
      <template v-if="filtered" #action>
        <button class="btn btn--ghost btn--sm" type="button" @click="resetFilters">Сбросить фильтры</button>
      </template>
    </EmptyState>
    <div v-else class="table-scroll" :class="{ 'is-loading': loading }">
      <table class="table ctab__t">
        <thead>
          <tr>
            <th class="check-col">
              <UiCheckbox
                :checked="allPicked"
                :indeterminate="!allPicked && rows.some((r) => selected.has(r.campaign_row_id))"
                aria-label="Выбрать все"
                @change="toggleAll($event.target.checked)"
              />
            </th>
            <th v-for="col in columns" :key="col.key" :class="{ num: col.num }" :aria-sort="ariaSort(col.key)">
              <button class="th-sort" type="button" @click="sortBy(col.key)">{{ col.label }}{{ sortMark(col.key) }}</button>
              <TermHint v-if="col.term" :term="col.term" />
            </th>
            <th aria-label="Действия"></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="c in rows"
            :key="c.campaign_row_id"
            class="ctab__row"
            :class="{ 'is-off': c.state !== 'ON', 'is-sel': selected.has(c.campaign_row_id) }"
            @click="openRow($event, c)"
          >
            <td class="check-col">
              <UiCheckbox
                :checked="selected.has(c.campaign_row_id)"
                :aria-label="`Выбрать «${c.name}»`"
                @change="toggle(c.campaign_row_id, $event.target.checked)"
              />
            </td>
            <td class="ctab__name">
              <RouterLink :to="linkOf(c)" v-tip.truncate="c.name">{{ c.name }}</RouterLink>
              <small class="ctab__meta">{{ metaOf(c) }}</small>
            </td>
            <td v-if="showProject" class="ctab__project">
              <RouterLink :to="`/w/${c.project.id}/campaigns`" v-tip.truncate="c.project.name">
                {{ c.project.name }}
              </RouterLink>
            </td>
            <td>
              <UiStatus v-bind="campaignStatus(c)" />
            </td>
            <td class="num">
              <template v-if="c.weekly_budget_rub">{{ rubles(c.weekly_budget_rub) }}/нед.</template>
              <template v-else-if="c.daily_budget_rub">{{ rubles(c.daily_budget_rub) }}/день</template>
              <template v-else>—</template>
              <small v-if="c.strategy_label" class="ctab__meta" v-tip.truncate="strategyText(c)">{{ strategyText(c) }}</small>
            </td>
            <td class="num">{{ rub(c.cost_kopecks) }}</td>
            <td class="num">{{ num(c.clicks) }}</td>
            <td class="num">{{ c.ctr != null ? pct(c.ctr, 2) : '—' }}</td>
            <td class="num" :class="{ 'tone-bad': !c.conversions && c.cost_kopecks > 0 }">{{ num(c.conversions) }}</td>
            <td class="num">{{ c.cpa_kopecks != null ? rub(c.cpa_kopecks) : '—' }}</td>
            <td class="ctab__acts">
              <UiMenu size="sm" :label="`Действия с «${c.name}»`">
                <UiMenuItem
                  v-if="c.state !== 'ARCHIVED'"
                  :icon="c.state === 'ON' ? Pause : Play"
                  :disabled="busy || !canToggle(c)"
                  :hint="canToggle(c) ? '' : 'черновик: сначала модерация'"
                  @click="$emit('toggle', c)"
                >
                  {{ c.state === 'ON' ? 'Остановить' : 'Запустить' }}
                </UiMenuItem>
                <UiMenuItem v-if="duplicable" :icon="Copy" :disabled="busy" @click="$emit('duplicate', c)">
                  Копия
                </UiMenuItem>
                <template v-if="archivable">
                  <UiMenuItem
                    v-if="c.state === 'ARCHIVED'"
                    :icon="ArchiveRestore"
                    :disabled="busy"
                    @click="$emit('unarchive', c)"
                  >
                    Из архива
                  </UiMenuItem>
                  <UiMenuItem
                    v-else
                    :icon="Archive"
                    :disabled="busy || c.state === 'ON'"
                    :hint="c.state === 'ON' ? 'сначала остановите' : ''"
                    @click="$emit('archive', c)"
                  >
                    В архив
                  </UiMenuItem>
                </template>
              </UiMenu>
            </td>
          </tr>
        </tbody>
        <tfoot v-if="rows.length > 1">
          <tr>
            <td></td>
            <td>Итого: {{ rows.length }}</td>
            <td v-if="showProject"></td>
            <td></td>
            <td class="num">
              <template v-if="budgetTotal?.mixed">—<small class="ctab__meta">разные типы бюджета</small></template>
              <span v-else-if="budgetTotal" v-tip="'Сумма бюджетов запущенных кампаний'">
                {{ rubles(budgetTotal.sum) }}{{ budgetTotal.unit === 'week' ? '/нед.' : '/день' }}
              </span>
              <template v-else>—</template>
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

    <div v-if="selected.size" class="bulk-dock" role="region" aria-label="Действия с выбранными">
      <b>Выбрано: {{ selected.size }}</b>
      <slot name="bulk" />
      <button class="btn btn--ghost btn--sm bulk-dock__end" type="button" @click="$emit('update:selected', new Set())">
        Снять выбор
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Archive, ArchiveRestore, Copy, Pause, Play, Search, SearchX } from '@lucide/vue'
import EmptyState from './ui/EmptyState.vue'
import UiMenu from './ui/UiMenu.vue'
import UiMenuItem from './ui/UiMenuItem.vue'
import UiCheckbox from './ui/UiCheckbox.vue'
import UiSelect from './ui/UiSelect.vue'
import UiStatus from './ui/UiStatus.vue'
import UiSwitch from './ui/UiSwitch.vue'
import TermHint from './TermHint.vue'
import { campaignStatus, campaignTypeShort, num, pct, rub, rubles } from '../utils/format'

/**
 * Таблица кампаний экспертного режима — одна для списка проекта и для списка по всем проектам.
 * Строка инструментов (поиск, состояние, кабинет или проект, «Без конверсий»), сортировка по заголовку,
 * действия строки в «⋯», итоги. Выбор — v-model:selected (Set campaign_row_id); массовые действия
 * приходят слотом bulk и стоят в панели, закреплённой снизу.
 */
const props = defineProps({
  items: { type: Array, required: true },
  selected: { type: Set, required: true },
  linkOf: { type: Function, required: true },
  busy: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  // Колонка «Проект» — в списке по всем проектам (у строк есть project {id, name}).
  showProject: { type: Boolean, default: false },
  // Фильтр «Кабинет» или «Проект»: { label, all, options: [{value, label}], of: (c) => value }.
  scope: { type: Object, default: null },
  duplicable: { type: Boolean, default: true },
  archivable: { type: Boolean, default: true },
})
const emit = defineEmits(['update:selected', 'toggle', 'duplicate', 'archive', 'unarchive'])

const router = useRouter()

const VIEWS = [
  { id: 'all', label: 'Все' },
  { id: 'on', label: 'Работают' },
  { id: 'off', label: 'Остановлены' },
  { id: 'archive', label: 'Архив' },
]
const IN_VIEW = {
  all: (c) => c.state !== 'ARCHIVED',
  on: (c) => c.state === 'ON',
  off: (c) => c.state !== 'ON' && c.state !== 'ARCHIVED',
  archive: (c) => c.state === 'ARCHIVED',
}

const search = ref('')
const view = ref('all')
const wasteOnly = ref(false)
const scopeValue = ref('')
const sort = reactive({ key: 'cost_kopecks', desc: true })

const columns = computed(() =>
  [
    { key: 'name', label: 'Кампания' },
    props.showProject && { key: 'project_name', label: 'Проект' },
    { key: 'state', label: 'Состояние' },
    { key: 'budget_week_rub', label: 'Бюджет', num: true },
    { key: 'cost_kopecks', label: 'Расход', num: true },
    { key: 'clicks', label: 'Клики', num: true },
    { key: 'ctr', label: 'CTR', num: true },
    { key: 'conversions', label: 'Конв.', num: true },
    { key: 'cpa_kopecks', label: 'CPA', num: true, term: 'cpa' },
  ].filter(Boolean),
)

const filtered = computed(() => !!(search.value || wasteOnly.value || scopeValue.value || view.value !== 'all'))

// Всё, кроме сегмента состояния: по этому списку считаются цифры у сегментов.
const base = computed(() => {
  const q = search.value.toLowerCase()
  return props.items.filter((c) => {
    if (scopeValue.value && String(props.scope?.of(c)) !== scopeValue.value) return false
    if (wasteOnly.value && (c.conversions || !c.cost_kopecks)) return false
    if (q && !c.name.toLowerCase().includes(q) && !String(c.campaign_id).includes(q)) return false
    return true
  })
})

const counts = computed(() =>
  Object.fromEntries(VIEWS.map((v) => [v.id, base.value.filter(IN_VIEW[v.id]).length])),
)

const rows = computed(() => {
  const dir = sort.desc ? -1 : 1
  return base.value
    .filter(IN_VIEW[view.value])
    .map(withExtras)
    .sort((a, b) => {
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
    }),
    { cost: 0, clicks: 0, impressions: 0, conversions: 0 },
  ),
)

// Итог бюджета — только по запущенным и только в одной мере: недельный с дневным не складываем,
// иначе сумма выглядит как реальный бюджет, а это пересчёт «дневной × 7».
const budgetTotal = computed(() => {
  const on = rows.value.filter((c) => c.state === 'ON' && (c.weekly_budget_rub || c.daily_budget_rub))
  if (!on.length) return null
  const units = new Set(on.map((c) => (c.weekly_budget_rub ? 'week' : 'day')))
  if (units.size > 1) return { mixed: true }
  const unit = units.has('week') ? 'week' : 'day'
  const sum = on.reduce((s, c) => s + (unit === 'week' ? c.weekly_budget_rub : c.daily_budget_rub), 0)
  return { unit, sum }
})

// Бюджет к одной мере — за неделю: так его можно сортировать и складывать.
function withExtras(c) {
  const week = c.weekly_budget_rub || (c.daily_budget_rub ? c.daily_budget_rub * 7 : null)
  return { ...c, budget_week_rub: week, project_name: c.project?.name }
}

function metaOf(c) {
  return [c.account_login, `№ ${c.campaign_id}`, campaignTypeShort(c)].filter(Boolean).join(' · ')
}

function strategyText(c) {
  if (c.target_cpa_rub) return `${c.strategy_label} · CPA ${rubles(c.target_cpa_rub)}`
  if (c.target_cpc_rub) return `${c.strategy_label} · CPC ${rubles(c.target_cpc_rub, 2)}`
  return c.strategy_label
}

// Черновик Директ не запустит, пока он не прошёл модерацию.
function canToggle(c) {
  return c.state === 'ON' || c.status !== 'DRAFT'
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

function ariaSort(key) {
  if (sort.key !== key) return undefined
  return sort.desc ? 'descending' : 'ascending'
}

function toggle(id, on) {
  const next = new Set(props.selected)
  if (on) next.add(id)
  else next.delete(id)
  emit('update:selected', next)
}

const allPicked = computed(() => rows.value.length > 0 && rows.value.every((r) => props.selected.has(r.campaign_row_id)))

function toggleAll(on) {
  const next = new Set(props.selected)
  for (const r of rows.value) {
    if (on) next.add(r.campaign_row_id)
    else next.delete(r.campaign_row_id)
  }
  emit('update:selected', next)
}

// Клик по строке открывает кампанию; флажок, ссылки, меню и выделение текста — нет.
function openRow(event, c) {
  if (event.target.closest('a, button, input, label, [role="menu"]')) return
  if (window.getSelection()?.toString()) return
  router.push(props.linkOf(c))
}

function resetFilters() {
  search.value = ''
  view.value = 'all'
  wasteOnly.value = false
  scopeValue.value = ''
}
</script>

<style scoped>
.ctab__t .num {
  font-variant-numeric: tabular-nums;
}

.ctab__row {
  cursor: pointer;
}

.ctab__row:hover td {
  background: color-mix(in srgb, var(--ink) 3%, var(--surface));
}

.ctab__row.is-sel td {
  background: color-mix(in srgb, var(--ink) 5%, var(--surface));
}

/* Название и строка под ним — по одной строке, длинное обрезается, целиком — в подсказке. */
.ctab__name {
  max-width: 340px;
}

.ctab__name a,
.ctab__project a {
  display: block;
  overflow: hidden;
  color: var(--ink);
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ctab__project {
  max-width: 180px;
}

.ctab__project a {
  font-weight: 500;
}

.ctab__meta {
  display: block;
  overflow: hidden;
  margin-top: 2px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.num .ctab__meta {
  max-width: 200px;
  margin-left: auto;
}

.ctab__acts {
  width: 1%;
  text-align: right;
}

@media (max-width: 640px) {
  .ctab__name {
    max-width: 190px;
  }
}
</style>
