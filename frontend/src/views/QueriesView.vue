<template>
  <template v-if="!expert">
    <PageHeader>
      <template #description>
        <UiMore page="queries" lead="Где есть что разобрать: ИИ делит новые запросы на «наши» и мусор.">
          <p>
            Решаете вы — группами внутри сайта. Новые запросы сервис забирает из Директа сам, а размечает их ИИ только
            когда вы нажмёте кнопку в проекте.
          </p>
        </UiMore>
      </template>
    </PageHeader>

    <p v-if="loading && !data" class="empty">Загружаем запросы…</p>
    <div v-else-if="!data" class="empty">
      {{ loadError || 'Не удалось загрузить запросы.' }}
      <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
    </div>
    <p v-else-if="!data.projects.length" class="empty">
      Проектов пока нет. Подключите Директ и Метрику в
      <RouterLink to="/cabinet/connections">подключениях</RouterLink>.
    </p>
    <template v-else>
      <div class="ccard">
        <div v-for="p in queue" :key="p.id" class="prow">
          <div class="prow__main">
            <small v-if="showClient(p)">{{ niceQuotes(p.client_name) }}</small>
            <b>{{ niceQuotes(p.name) }}</b>
            <p>{{ p.line.text }}</p>
          </div>
          <div class="prow__acts">
            <span class="st" :class="`st--${p.line.st}`">
              <component :is="p.line.st === 'ok' ? CircleCheck : TriangleAlert" :size="14" aria-hidden="true" />
              {{ p.line.st === 'ok' ? 'Разобрано' : 'Есть что разобрать' }}
            </span>
            <RouterLink
              v-if="p.line.btn"
              class="btn"
              :class="p.line.st === 'warn' ? 'btn--accent' : 'btn--ghost'"
              :to="`/w/${p.id}/queries`"
            >
              {{ p.line.btn }}
            </RouterLink>
          </div>
        </div>
      </div>
      <ExpertHint>Общая таблица запросов по всем сайтам, фильтры и пакетное применение</ExpertHint>
    </template>
  </template>

  <template v-else>
    <PageHeader>
      <template #description>
        <UiMore page="queries" lead="Запросы из Директа по всем проектам.">
          <p>Загрузить новые и разметить их ИИ — в проекте: у каждого свой бриф и память.</p>
          <p>
            Разметка раскладывает запросы во вкладки «В минус» и «Целевые» — принять предложения можно здесь сразу по
            всем проектам.
          </p>
        </UiMore>
      </template>
    </PageHeader>

    <div v-if="tiles.length" class="stats-grid qstats">
      <div v-for="t in tiles" :key="t.key" class="stat-card">
        <small>{{ t.label }}<TermHint v-if="t.term" :term="t.term" /></small>
        <strong :class="t.tone">{{ num(t.value) }}</strong>
        <span class="cell-note">{{ t.note }}</span>
      </div>
    </div>

    <section class="panel">
      <UiListTools :active="sheetFilters" @reset="resetSheet">
        <template #search>
          <label class="ltools__search">
            <Search aria-hidden="true" />
            <input v-model.trim="filters.search" type="search" placeholder="Слово из запроса" aria-label="Поиск запроса" />
          </label>
        </template>
        <template #tabs>
          <div class="seg" role="group" aria-label="Очередь запросов">
            <button
              v-for="t in TABS"
              :key="t.key"
              type="button"
              :class="{ active: tab === t.key }"
              :aria-pressed="tab === t.key"
              @click="setTab(t.key)"
            >
              {{ t.label }}<span v-if="data" class="seg__n">{{ num(data.counts[t.key]) }}</span>
            </button>
          </div>
        </template>
        <UiSelect
          v-if="projectOptions.length > 1"
          v-model="filters.project"
          size="sm"
          :options="[{ value: '', label: 'Все проекты' }, ...projectOptions]"
          aria-label="Проект"
        />
        <label class="ltools__field">
          Расход от
          <input v-model="filters.minCost" type="number" min="0" step="10" placeholder="0" aria-label="Расход от, ₽" />
          ₽
        </label>
        <UiSwitch v-model="filters.noConv" class="ltools__switch">Без конверсий</UiSwitch>
      </UiListTools>

      <p v-if="projectInfo" class="sync-status">
        {{ projectInfo.name }}:
        <template v-if="!projectInfo.search_campaigns">нет поисковых кампаний</template>
        <template v-else-if="projectInfo.synced_at">запросы загружены {{ formatDateTime(projectInfo.synced_at) }}</template>
        <template v-else>запросы ещё не загружали</template>
        · <RouterLink :to="`/w/${projectInfo.id}/queries`">загрузить и разметить в проекте</RouterLink>
      </p>

      <UiNotice v-if="problems.length" tone="warn" closable @close="problems = []">
        <strong>Не всё применилось</strong>
        <ul class="notice-list">
          <li v-for="p in problems.slice(0, 20)" :key="`${p.project}-${p.query_norm}`">
            {{ p.project }} · «{{ p.query }}»: {{ p.message || STATUS_TEXT[p.status] || p.status }}
          </li>
        </ul>
      </UiNotice>

      <p v-if="tab === 'new' && data?.counts.new" class="hint">
        Эти запросы ещё не размечены — откройте проект и нажмите «Разметить с ИИ».
      </p>

      <p v-if="loading && !data" class="empty">Загружаем запросы…</p>
      <EmptyState v-else-if="!data" :icon="CloudOff" :text="loadError || 'Не удалось загрузить запросы.'" plain>
        <template #action><button class="btn btn--ghost" type="button" @click="load">Повторить</button></template>
      </EmptyState>
      <EmptyState v-else-if="!data.projects.length && !filters.project" :icon="SearchX" plain>
        Проектов пока нет. Подключите Директ и Метрику в
        <RouterLink to="/cabinet/connections">подключениях</RouterLink>.
      </EmptyState>
      <EmptyState v-else-if="!data.items.length" :icon="SearchX" :text="EMPTY[tab]" plain />
      <template v-else>
        <div v-scroll-x class="table-scroll" :class="{ 'is-loading': loading }">
          <table v-cards class="table queries-table tcards">
            <thead>
              <tr>
                <th v-if="canApply" class="check-col">
                  <UiCheckbox
                    :checked="allPicked"
                    :indeterminate="!allPicked && selectable.some((q) => selected.has(key(q)))"
                    aria-label="Выбрать все"
                    @change="toggleAll($event.target.checked)"
                  />
                </th>
                <th>Запрос</th>
                <th class="num">Клики</th>
                <th class="num">Расход</th>
                <th class="num">Конв.</th>
                <th>{{ tab === 'new' ? 'Где' : 'Решение' }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="q in data.items" :key="key(q)" :class="{ 'is-selected': selected.has(key(q)) }">
                <td v-if="canApply" class="check-col">
                  <UiCheckbox
                    :checked="selected.has(key(q))"
                    :disabled="!isSelectable(q)"
                    :aria-label="`Выбрать «${q.query}»`"
                    @change="toggle(key(q), $event.target.checked)"
                  />
                </td>
                <td class="query-cell tc-name">
                  <strong>{{ q.query }}</strong>
                  <small class="cell-note">
                    <RouterLink :to="`/w/${q.project.id}/queries`">{{ niceQuotes(q.project.name) }}</RouterLink> ·
                    {{ campaignsText(q) || 'кампания' }}
                  </small>
                  <small v-if="q.blocked_by" class="cell-note tone-ok">Уже отсекается: {{ q.blocked_by }}</small>
                </td>
                <td class="num tc-main" data-label="Клики">{{ num(q.clicks) }}</td>
                <td class="num tc-main" data-label="Расход">{{ rubles(q.cost_rub) }}</td>
                <td
                  class="num tc-main"
                  :class="{ 'tone-bad': !q.conversions && q.cost_rub > 0 }"
                  data-label="Конв."
                >
                  {{ num(q.conversions) }}
                </td>
                <td class="decision tc-wide">
                  <template v-if="q.label">
                    <div class="decision__top">
                      <span class="chip" :class="q.label.verdict === 'negative' ? 'chip--bad' : 'chip--ok'">
                        {{ q.label.verdict === 'negative' ? 'в минус' : 'целевой' }}
                      </span>
                      <span class="chip" :class="SOURCE_CHIPS[q.label.source] || ''">
                        {{ SOURCES[q.label.source] || q.label.source }}
                      </span>
                      <span v-if="q.label.confirmed" class="chip chip--ok">
                        {{ q.label.applied_at ? 'применено' : 'решено' }}
                      </span>
                    </div>
                    <p v-if="q.label.verdict === 'negative' && q.label.negative_phrase" class="decision__reason">
                      <strong>{{ q.label.negative_phrase }}</strong> {{ LEVELS[q.label.level] || '' }}
                    </p>
                    <p v-if="q.label.reason" class="decision__reason">{{ q.label.reason }}</p>
                    <p v-if="q.conflicts_total" class="decision__warn tone-warn">
                      Заденет ключи: {{ q.conflicts.map((k) => `«${k.keyword}»`).join(', ') }} — решите в
                      <RouterLink :to="`/w/${q.project.id}/queries`">проекте</RouterLink>
                    </p>
                  </template>
                  <small v-else class="cell-note">
                    {{ q.matched_keywords.length ? `по фразе ${q.matched_keywords.map((k) => `«${k}»`).join(', ')}` : '—' }}
                  </small>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="data.items.length < data.total" class="sync-status">
          Показано {{ data.items.length }} из {{ data.total }} — самые дорогие. Остальные — в проектах или по фильтру.
        </p>
      </template>

      <div v-if="canApply && selected.size" class="bulk-dock" role="region" aria-label="Действия с выбранными">
        <b>Выбрано: {{ selected.size }}</b>
        <div class="bulk-dock__acts">
          <button v-if="tab === 'negative'" class="btn btn--accent btn--sm" type="button" :disabled="busy" @click="openPreview">
            <ListX aria-hidden="true" />В минус-фразы…
          </button>
          <button v-else class="btn btn--accent btn--sm" type="button" :disabled="busy" @click="applySelected">
            {{ busy ? busyText || 'Применяем…' : 'Подтвердить целевые' }}
          </button>
        </div>
        <button class="btn btn--ghost btn--sm bulk-dock__end" type="button" :disabled="busy" @click="selected = new Set()">
          Снять выбор
        </button>
      </div>
    </section>

    <Modal :open="preview.open" title="Что уйдёт в минус-фразы" wide :locked="busy" @close="preview.open = false">
      <p class="hint">
        Фразы добавятся в Директ так, как их предложил ИИ. Поправить фразу или уровень можно в очереди проекта, откатить —
        в истории.
      </p>
      <div v-for="g in preview.groups" :key="g.id" class="mprev">
        <h3 class="mprev__h">
          {{ g.name }}
          <span>{{ g.items.length }} {{ plural(g.items.length, 'фраза', 'фразы', 'фраз') }}</span>
        </h3>
        <ul class="mprev__list">
          <li v-for="q in g.items" :key="q.query_norm">
            <b>−{{ q.label.negative_phrase || q.query }}</b>
            <span>
              {{ LEVELS[q.label.level] || '' }}<template v-if="campaignsText(q)"> «{{ campaignsText(q) }}»</template>
              · из запроса «{{ q.query }}» · {{ rubles(q.cost_rub) }}
            </span>
          </li>
        </ul>
      </div>
      <template #footer>
        <button class="btn btn--ghost" type="button" :disabled="busy" @click="preview.open = false">Отмена</button>
        <button class="btn btn--accent" type="button" :disabled="busy" @click="applySelected">
          {{ busy ? busyText || 'Применяем…' : `Добавить ${previewCount} в Директ` }}
        </button>
      </template>
    </Modal>
  </template>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { errorText, jobProgressText, webApi, wsApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { undoAction } from '../composables/useUndo'
import { formatDateTime, niceQuotes, num, plural, rubles } from '../utils/format'
import { useHead } from '../utils/useHead'
import TermHint from '../components/TermHint.vue'
import ExpertHint from '../components/ExpertHint.vue'
import EmptyState from '../components/ui/EmptyState.vue'
import Modal from '../components/ui/Modal.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import UiMore from '../components/ui/UiMore.vue'
import UiNotice from '../components/ui/UiNotice.vue'
import UiCheckbox from '../components/ui/UiCheckbox.vue'
import UiListTools from '../components/ui/UiListTools.vue'
import UiSelect from '../components/ui/UiSelect.vue'
import UiSwitch from '../components/ui/UiSwitch.vue'
import { CircleCheck, CloudOff, ListX, Search, SearchX, TriangleAlert } from '@lucide/vue'
import { useExpert } from '../composables/useExpert'

useHead('Запросы — Толк', true)

const { expert } = useExpert()
const words = (n) => plural(n, 'запрос', 'запроса', 'запросов')

/** Простой режим: строка на сайт — что с запросами и куда нажать. Сначала те, где есть что разбирать. */
const queue = computed(() =>
  (data.value?.projects || [])
    .map((p) => ({ ...p, line: queueLine(p) }))
    .sort((a, b) => (a.line.st === b.line.st ? 0 : a.line.st === 'warn' ? -1 : 1)),
)

function queueLine(p) {
  const c = p.counts
  if (!p.search_campaigns) return { st: 'ok', text: 'Нет поисковых кампаний — чистить нечего', btn: '' }
  if (c.negative)
    return {
      st: 'warn',
      text: `ИИ предлагает в минус ${c.negative} ${words(c.negative)}${p.negative_cost_rub ? ` · на них ушло ${rubles(p.negative_cost_rub)}` : ''}`,
      btn: 'Разобрать',
    }
  if (c.new)
    return {
      st: 'warn',
      text: `${c.new} ${plural(c.new, 'новый запрос', 'новых запроса', 'новых запросов')}${p.new_cost_rub ? ` на ${rubles(p.new_cost_rub)}` : ''}, ИИ ещё не размечал`,
      btn: 'Разметить',
    }
  if (c.target) return { st: 'warn', text: `${c.target} ${words(c.target)} ИИ считает целевыми — подтвердите`, btn: 'Открыть' }
  if (!p.synced_at) return { st: 'warn', text: 'Запросы из Директа ещё не загружали', btn: 'Загрузить' }
  return { st: 'ok', text: `Новых запросов нет · загружены ${formatDateTime(p.synced_at)}`, btn: 'Посмотреть' }
}

const TABS = [
  { key: 'new', label: 'Новые' },
  { key: 'negative', label: 'В минус' },
  { key: 'target', label: 'Целевые' },
  { key: 'done', label: 'Решённые' },
]
const EMPTY = {
  new: 'Новых запросов нет — всё разобрано или запросы ещё не загружали.',
  negative: 'ИИ пока ничего не предложил в минус.',
  target: 'Нет запросов, предложенных целевыми.',
  done: 'Решённых запросов пока нет.',
}
const SOURCES = { ai: 'ИИ', memory: 'по памяти', brief: 'по брифу', direct: 'уже в Директе', user: 'вы' }
const SOURCE_CHIPS = { ai: 'chip--src-ai', memory: 'chip--src-rule', brief: 'chip--src-rule', direct: 'chip--src-rule' }
const LEVELS = { adgroup: 'на группу', campaign: 'на кампанию' }
const STATUS_TEXT = { partial: 'частично', skipped: 'пропущено', error: 'ошибка' }

const data = ref(null)
const loading = ref(false)
const loadError = ref('')
const tab = ref('new')
const filters = reactive({ search: '', project: '', minCost: '', noConv: false })
const selected = ref(new Set())
const busy = ref(false)
const busyText = ref('')
const problems = ref([])
const allProjects = ref([])
let seq = 0
let timer = null

const preview = reactive({ open: false, groups: [] })

const canApply = computed(() => tab.value === 'negative' || tab.value === 'target')
// Телефон: сколько фильтров включено в шторке (поиск — не в ней) и их сброс.
const sheetFilters = computed(() => (filters.project ? 1 : 0) + (filters.minCost ? 1 : 0) + (filters.noConv ? 1 : 0))

function resetSheet() {
  filters.project = ''
  filters.minCost = ''
  filters.noConv = false
}
const selectable = computed(() => (data.value?.items || []).filter(isSelectable))
const allPicked = computed(() => selectable.value.length > 0 && selectable.value.every((q) => selected.value.has(key(q))))
// Список проектов для фильтра не сужается, когда фильтр по проекту уже выбран.
const projectOptions = computed(() => allProjects.value.map((p) => ({ value: String(p.id), label: niceQuotes(p.name) })))
// Выбран один проект — показываем, когда загружали его запросы и куда идти за новыми.
const projectInfo = computed(() =>
  filters.project ? (data.value?.projects || []).find((p) => String(p.id) === filters.project) || null : null,
)
const previewCount = computed(() => preview.groups.reduce((n, g) => n + g.items.length, 0))

// Плитки только с ненулевыми числами: нули ничего не сообщают.
const tiles = computed(() => {
  const d = data.value
  if (!d) return []
  return [
    { key: 'new', label: 'Новые запросы', value: d.counts.new, note: `расход ${rubles(d.new_cost_rub)}` },
    {
      key: 'negative',
      label: 'Предложено в минус',
      term: 'minus',
      value: d.counts.negative,
      tone: 'tone-bad',
      note: `слили ${rubles(d.negative_cost_rub)} за период`,
    },
    { key: 'target', label: 'Предложено целевыми', value: d.counts.target, note: 'ждут подтверждения' },
    { key: 'done', label: 'Решено', value: d.counts.done, note: 'в памяти проектов' },
  ].filter((t) => t.value > 0)
})

function showClient(p) {
  return !!p.client_name && p.client_name.trim().toLowerCase() !== (p.name || '').trim().toLowerCase()
}

function campaignsText(q) {
  return (q.campaigns || []).map((c) => c.name).filter(Boolean).join(', ')
}

function key(q) {
  return `${q.project.id}:${q.query_norm}`
}

function isSelectable(q) {
  return !!q.label && !q.label.confirmed && !q.conflicts_total
}

function toggle(k, on) {
  const next = new Set(selected.value)
  if (on) next.add(k)
  else next.delete(k)
  selected.value = next
}

function toggleAll(on) {
  selected.value = on ? new Set(selectable.value.map(key)) : new Set()
}

async function load() {
  const my = ++seq
  loading.value = true
  loadError.value = ''
  try {
    const res = await webApi.queries({
      tab: tab.value,
      search: filters.search,
      workspace_id: filters.project || undefined,
      min_cost_rub: filters.minCost || undefined,
      no_conversions: filters.noConv,
    })
    if (my !== seq) return
    data.value = res
    if (!filters.project) allProjects.value = res.projects.map((p) => ({ id: p.id, name: p.name }))
    const keys = new Set(res.items.filter(isSelectable).map(key))
    selected.value = new Set([...selected.value].filter((k) => keys.has(k)))
  } catch (err) {
    if (my === seq) loadError.value = errorText(err)
  } finally {
    if (my === seq) loading.value = false
  }
}

function setTab(t) {
  if (tab.value === t) return
  tab.value = t
  selected.value = new Set()
  load()
}

watch(
  () => [filters.search, filters.project, filters.minCost, filters.noConv],
  () => {
    clearTimeout(timer)
    timer = setTimeout(load, 300)
  },
)

function chosenByProject() {
  const byProject = new Map()
  for (const q of data.value?.items || []) {
    if (!selected.value.has(key(q)) || !isSelectable(q)) continue
    if (!byProject.has(q.project.id)) {
      byProject.set(q.project.id, { id: q.project.id, name: niceQuotes(q.project.name), items: [] })
    }
    byProject.get(q.project.id).items.push(q)
  }
  return byProject
}

// Минус-фразы сначала показываем списком по проектам — в Директ они уходят только из этого окна.
function openPreview() {
  preview.groups = [...chosenByProject().values()]
  if (preview.groups.length) preview.open = true
}

// Предложения ИИ принимаются как есть; править фразу или уровень — в очереди проекта.
async function applySelected() {
  const byProject = chosenByProject()
  if (!byProject.size) return
  busy.value = true
  problems.value = []
  let applied = 0
  const undo = []
  try {
    for (const [projectId, group] of byProject) {
      busyText.value = `${group.name}: применяем…`
      const items = group.items.map((q) => ({
        query_norm: q.query_norm,
        verdict: q.label.verdict,
        negative_phrase: q.label.verdict === 'negative' ? q.label.negative_phrase : '',
        level: q.label.level,
      }))
      const names = Object.fromEntries(group.items.map((q) => [q.query_norm, q.query]))
      try {
        const res = await wsApi.queriesApply(projectId, { items }, (job) => {
          busyText.value = `${group.name}: ${jobProgressText(job)}`
        })
        undo.push(res)
        for (const it of res.items || []) {
          if (it.status === 'ok' || it.status === 'partial') applied += 1
          if (it.status !== 'ok' && (it.status !== 'partial' || it.message))
            problems.value.push({ ...it, project: group.name, query: names[it.query_norm] || it.query_norm })
        }
      } catch (err) {
        problems.value.push({ project: group.name, query_norm: '*', query: 'все выбранные', message: errorText(err) })
      }
    }
    pushToast(
      `Применено: ${applied}${problems.value.length ? `, с проблемами: ${problems.value.length}` : ''}`,
      problems.value.length ? 'warn' : 'ok',
      // Одной кнопкой отменяется только применение в одном проекте — иначе откат по одному в «Истории».
      byProject.size === 1 && undo.length === 1 ? undoAction(undo[0], load) : {},
    )
    selected.value = new Set()
    preview.open = false
    await load()
  } finally {
    busy.value = false
    busyText.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
/* Плиток бывает от одной до четырёх (пустые не показываем): колонка не шире четверти, одна плитка не тянется во всю ширину. */
.qstats {
  grid-template-columns: repeat(auto-fill, minmax(max(190px, (100% - 3 * 0.9rem) / 4), 1fr));
}

.notice-list {
  margin: 4px 0 0;
  padding-left: 18px;
}

.mprev + .mprev {
  margin-top: 16px;
}

.mprev__h {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 0 0 6px;
  font-size: 15px;
}

.mprev__h span,
.mprev__list span {
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
}

.mprev__list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.mprev__list li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 10px;
  padding: 8px 12px;
  border-radius: var(--r-sm);
  background: var(--surface-2);
}
</style>
