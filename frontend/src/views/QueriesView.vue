<template>
  <div v-if="data" class="stats-grid">
    <div class="stat-card">
      <small>Новые запросы</small>
      <strong>{{ num(data.counts.new) }}</strong>
      <span class="cell-note">расход {{ rubles(data.new_cost_rub) }}</span>
    </div>
    <div class="stat-card">
      <small>Предложено в минус<TermHint term="minus" /></small>
      <strong :class="{ 'tone-bad': data.counts.negative }">{{ num(data.counts.negative) }}</strong>
      <span class="cell-note">слили {{ rubles(data.negative_cost_rub) }} за период</span>
    </div>
    <div class="stat-card">
      <small>Предложено целевыми</small>
      <strong>{{ num(data.counts.target) }}</strong>
      <span class="cell-note">ждут подтверждения</span>
    </div>
    <div class="stat-card">
      <small>Решено</small>
      <strong>{{ num(data.counts.done) }}</strong>
      <span class="cell-note">в памяти проектов</span>
    </div>
  </div>

  <section v-if="data?.projects.length > 1" class="panel">
    <div class="panel__head">
      <h2>По проектам</h2>
      <span class="hint">Загрузить запросы из Директа и разметить их ИИ — в проекте: у каждого свой бриф и память.</span>
    </div>
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>Проект</th>
            <th class="num">Новые</th>
            <th class="num">В минус<TermHint term="minus" /></th>
            <th class="num">Целевые</th>
            <th>Запросы загружены</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in data.projects" :key="p.id">
            <td>{{ p.name }}</td>
            <td class="num">
              {{ num(p.counts.new) }}
              <small v-if="p.new_cost_rub" class="cell-note">{{ rubles(p.new_cost_rub) }}</small>
            </td>
            <td class="num" :class="{ 'tone-bad': p.counts.negative }">
              {{ num(p.counts.negative) }}
              <small v-if="p.negative_cost_rub" class="cell-note">{{ rubles(p.negative_cost_rub) }}</small>
            </td>
            <td class="num">{{ num(p.counts.target) }}</td>
            <td>
              <template v-if="!p.search_campaigns"><span class="cell-note">нет поисковых кампаний</span></template>
              <template v-else-if="p.synced_at">{{ formatDateTime(p.synced_at) }}</template>
              <span v-else class="cell-note">ещё не загружали</span>
            </td>
            <td class="row-actions">
              <RouterLink v-if="p.search_campaigns" class="btn btn--ghost" :to="`/w/${p.id}/queries`">
                {{ p.counts.new ? 'Разметить' : 'Открыть' }}
              </RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <div class="queries-tabs">
    <nav class="seg seg--tabs" aria-label="Очередь запросов">
      <button v-for="t in TABS" :key="t.key" type="button" :class="{ active: tab === t.key }" @click="setTab(t.key)">
        {{ t.label }}<template v-if="data"> · {{ num(data.counts[t.key]) }}</template>
      </button>
    </nav>
  </div>

  <div v-if="problems.length" class="notice notice--warn">
    <div class="queries-result__head">
      <strong>Не всё применилось</strong>
      <button class="btn btn--ghost" type="button" @click="problems = []">Скрыть</button>
    </div>
    <ul>
      <li v-for="p in problems.slice(0, 20)" :key="`${p.project}-${p.query_norm}`">
        {{ p.project }} · «{{ p.query }}»: {{ p.message || STATUS_TEXT[p.status] || p.status }}
      </li>
    </ul>
  </div>

  <section class="panel">
    <div class="filters">
      <div v-field class="field field--wide">
        <label>Поиск</label>
        <input v-model.trim="filters.search" type="search" placeholder="слово из запроса" />
      </div>
      <div v-if="projectOptions.length > 1" v-field class="field">
        <label>Проект</label>
        <select v-model="filters.project">
          <option value="">Все проекты</option>
          <option v-for="p in projectOptions" :key="p.id" :value="String(p.id)">{{ p.name }}</option>
        </select>
      </div>
      <div v-field class="field">
        <label>Расход от, ₽</label>
        <input v-model="filters.minCost" type="number" min="0" step="10" placeholder="0" />
      </div>
      <label class="check-row">
        <input v-model="filters.noConv" type="checkbox" />
        Без конверсий
      </label>
    </div>

    <p v-if="tab === 'new' && data?.counts.new" class="hint">
      Новые запросы ещё никто не разметил. Откройте проект и нажмите «Разметить с ИИ» — предложения появятся во
      вкладках «В минус» и «Целевые», их можно принять здесь сразу по всем проектам.
    </p>

    <p v-if="loading && !data" class="empty">Загружаем запросы…</p>
    <div v-else-if="!data" class="empty">
      {{ loadError || 'Не удалось загрузить запросы.' }}
      <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
    </div>
    <p v-else-if="!data.projects.length && !filters.project" class="empty">
      Проектов пока нет. Подключите Директ и Метрику в
      <RouterLink to="/cabinet/connections">подключениях</RouterLink>.
    </p>
    <p v-else-if="!data.items.length" class="empty">{{ EMPTY[tab] }}</p>
    <template v-else>
      <div class="table-scroll" :class="{ 'is-loading': loading }">
        <table class="table queries-table">
          <thead>
            <tr>
              <th v-if="canApply" class="check-col">
                <input
                  type="checkbox"
                  :checked="selectable.length > 0 && selectable.every((q) => selected.has(key(q)))"
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
                <input
                  type="checkbox"
                  :checked="selected.has(key(q))"
                  :disabled="!isSelectable(q)"
                  :aria-label="`Выбрать «${q.query}»`"
                  @change="toggle(key(q), $event.target.checked)"
                />
              </td>
              <td class="query-cell">
                <strong>{{ q.query }}</strong>
                <small class="cell-note">
                  <RouterLink :to="`/w/${q.project.id}/queries`">{{ q.project.name }}</RouterLink> ·
                  {{ q.campaigns.map((c) => c.name).filter(Boolean).join(', ') || 'кампания' }}
                </small>
                <small v-if="q.blocked_by" class="cell-note tone-ok">Уже отсекается: {{ q.blocked_by }}</small>
              </td>
              <td class="num">{{ num(q.clicks) }}</td>
              <td class="num">{{ rubles(q.cost_rub) }}</td>
              <td class="num" :class="{ 'tone-bad': !q.conversions && q.cost_rub > 0 }">{{ num(q.conversions) }}</td>
              <td class="decision">
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

    <div v-if="canApply && selected.size" class="bulk-bar queries-applybar">
      <span>Выбрано: {{ selected.size }}</span>
      <button class="btn btn--accent" type="button" :disabled="busy" @click="applySelected">
        {{ busy ? busyText || 'Применяем…' : tab === 'negative' ? 'Добавить минус-фразы' : 'Подтвердить целевые' }}
      </button>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="selected = new Set()">Снять выбор</button>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { errorText, jobProgressText, webApi, wsApi } from '../api/web'
import { askConfirm } from '../composables/useConfirm'
import { pushToast } from '../composables/useToasts'
import { formatDateTime, num, plural, rubles } from '../utils/format'
import { useHead } from '../utils/useHead'
import TermHint from '../components/TermHint.vue'

useHead('Чистка запросов — AiMarketing', true)

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

const canApply = computed(() => tab.value === 'negative' || tab.value === 'target')
const selectable = computed(() => (data.value?.items || []).filter(isSelectable))
// Список проектов для фильтра не сужается, когда фильтр по проекту уже выбран.
const projectOptions = computed(() => allProjects.value)

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

// Предложения ИИ принимаются как есть; править фразу или уровень — в очереди проекта.
async function applySelected() {
  const chosen = (data.value?.items || []).filter((q) => selected.value.has(key(q)) && isSelectable(q))
  if (!chosen.length) return
  const byProject = new Map()
  for (const q of chosen) {
    if (!byProject.has(q.project.id)) byProject.set(q.project.id, { name: q.project.name, items: [] })
    byProject.get(q.project.id).items.push(q)
  }
  if (tab.value === 'negative') {
    const n = chosen.length
    const ok = await askConfirm(
      `Добавить в Директ ${n} ${plural(n, 'минус-фразу', 'минус-фразы', 'минус-фраз')} в ${byProject.size} ${plural(byProject.size, 'проекте', 'проектах', 'проектах')}? Откатить можно в истории.`,
      { title: 'Минус-фразы', confirmText: 'Добавить' },
    )
    if (!ok) return
  }
  busy.value = true
  problems.value = []
  let applied = 0
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
    )
    selected.value = new Set()
    await load()
  } finally {
    busy.value = false
    busyText.value = ''
  }
}

onMounted(load)
</script>
