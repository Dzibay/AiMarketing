<template>
  <PageHeader :description="description" />

  <UiListTools label="Фильтры истории" :active="sheetFilters" @reset="resetFilters">
    <!-- «Вся история» со страницы кампании: только её изменения, фильтр снимается крестиком. -->
    <button
      v-if="campaign"
      class="chip hist-camp"
      type="button"
      v-tip="'Показать изменения всех кампаний'"
      @click="campaign = 0"
    >
      <span class="hist-camp__name">Кампания: {{ campaignLabel }}</span>
      <X :size="14" aria-label="Убрать фильтр по кампании" />
    </button>
    <UiSelect v-if="showProjectFilter" v-model="project" size="sm" :options="projectOptions" aria-label="Проект" />
    <UiSelect v-model="kind" size="sm" :options="KINDS" aria-label="Тип действия" />
    <p v-if="phone" class="ltools__cap">Кто менял</p>
    <div class="seg" role="group" aria-label="Кто менял">
      <button
        v-for="s in SOURCES"
        :key="s.id"
        type="button"
        :class="{ active: source === s.id }"
        :aria-pressed="source === s.id"
        @click="source = s.id"
      >
        {{ s.label }}
      </button>
    </div>
    <button v-if="filtered && !phone" class="btn btn--ghost btn--sm" type="button" @click="resetFilters">
      Сбросить
    </button>
  </UiListTools>

  <UiNotice v-if="loadError" tone="error">
    Не удалось загрузить историю. {{ loadError }}
    <template #action>
      <button class="btn btn--ghost btn--sm" type="button" :disabled="loading" @click="reload">Повторить</button>
    </template>
  </UiNotice>

  <div v-if="loading && !items.length" class="ccard" aria-busy="true" aria-label="Загружаем историю">
    <div v-for="n in 4" :key="n" class="hrow">
      <span class="ui-skel hskel__kind" />
      <div class="hrow__main">
        <span class="ui-skel" :style="{ width: `${62 - n * 7}%` }" />
        <span class="ui-skel hskel__sub" />
      </div>
    </div>
  </div>
  <EmptyState v-else-if="!items.length && !loadError" :art="filtered ? 'search' : 'history'" :text="emptyText">
    <template #action>
      <button v-if="filtered" class="btn btn--ghost" type="button" @click="resetFilters">Показать всё</button>
      <RouterLink v-else class="btn btn--accent" :to="chatLink">Спросить ИИ, что улучшить</RouterLink>
    </template>
  </EmptyState>

  <section v-else-if="items.length" class="hist">
    <template v-for="d in days" :key="d.label">
      <h3 class="section-h">{{ d.label }}</h3>
      <div class="ccard">
        <div
          v-for="a in d.items"
          :key="a.id"
          class="hrow"
          :class="{ 'is-undone': a.undone_at, 'is-open': open.has(a.id) }"
        >
          <i class="hrow__kind">
            <component :is="ACTION_ICONS[a.action] || CircleDot" :size="16" aria-hidden="true" />
          </i>
          <div class="hrow__main">
            <b>{{ keepNumbers(a.title || a.action) }}</b>
            <!-- Что поменялось — словами, «было → стало»; строки собирает сервер. -->
            <ul v-if="a.changes?.length" class="hrow__changes">
              <li v-for="(line, i) in a.changes" :key="i">{{ keepNumbers(line) }}</li>
            </ul>
            <p
              v-if="result(a).problem && !open.has(a.id) && problemsOf(a).length"
              class="hrow__problem"
              :class="`hrow__problem--${result(a).tone}`"
            >
              {{ keepNumbers(firstProblem(a)) }}
              <button
                v-if="problemsOf(a).length > 1"
                class="hrow__morebtn"
                type="button"
                :aria-expanded="false"
                :aria-controls="`hmore-${a.id}`"
                @click="toggleOpen(a.id)"
              >
                Подробнее
              </button>
            </p>
            <small class="hrow__meta">
              <span>{{ timeOf(a.created_at) }}</span>
              <ActionWho :source="a.source" />
              <RouterLink v-if="!workspaceId && a.workspace_id" :to="`/w/${a.workspace_id}/history`">
                {{ niceQuotes(a.workspace_name) || `Проект ${a.workspace_id}` }}
              </RouterLink>
              <template v-for="c in campaignsOf(a)" :key="c.id">
                <RouterLink
                  v-if="c.workspace_id"
                  class="hrow__camp"
                  :to="`/w/${c.workspace_id}/campaigns/${c.id}`"
                  v-tip="directTip(c)"
                >
                  {{ campaignName(c) }}
                </RouterLink>
                <span v-else class="hrow__camp" v-tip="directTip(c)">{{ campaignName(c) }}</span>
              </template>
              <span v-if="moreCampaigns(a)">и ещё {{ moreCampaigns(a) }}</span>
              <span v-if="expert && summaryLine(a)">{{ summaryLine(a) }}</span>
            </small>

            <div v-if="open.has(a.id)" :id="`hmore-${a.id}`" class="hrow__more">
              <p v-if="expert && a.cut" class="hrow__cut">
                Старая запись: ответ Директа обрезался при сохранении — показано то, что уцелело.
              </p>
              <template v-if="problemsOf(a).length">
                <p class="hrow__errh">{{ isFailed(a) ? 'Директ не принял изменение' : 'Применилось не всё' }}</p>
                <ul class="hrow__errs">
                  <li v-for="(e, i) in problemsOf(a)" :key="i">{{ e }}</li>
                </ul>
              </template>
              <div v-if="expert" class="log-row__body">
                <div>
                  <h4>Параметры</h4>
                  <pre>{{ pretty(a.payload) }}</pre>
                </div>
                <div>
                  <h4>Ответ</h4>
                  <pre>{{ pretty(a.result) }}</pre>
                </div>
              </div>
            </div>
          </div>

          <div class="hrow__acts">
            <UiStatus
              v-if="a.undone_at"
              tone="off"
              :icon="Undo2"
              label="Возвращено"
              :hint="`Вернули ${formatDateTime(a.undone_at)}`"
            />
            <button
              v-else-if="a.undo"
              class="btn btn--ghost btn--sm"
              type="button"
              v-tip="a.undo.label"
              v-coach="'undo'"
              @click="undoId = a.id"
            >
              <Undo2 aria-hidden="true" />{{ expert ? 'Откатить' : 'Вернуть как было' }}
            </button>

            <template v-if="!a.undone_at">
              <button
                v-if="result(a).problem"
                class="hres"
                :class="`hres--${result(a).tone}`"
                type="button"
                :aria-label="`${result(a).label} — показать подробности`"
                v-tip="result(a).label"
                :aria-expanded="open.has(a.id)"
                :aria-controls="`hmore-${a.id}`"
                @click="toggleOpen(a.id)"
              >
                <component :is="result(a).icon" aria-hidden="true" />
              </button>
              <span
                v-else
                class="hres"
                :class="`hres--${result(a).tone}`"
                role="img"
                :aria-label="result(a).label"
                v-tip="result(a).label"
              >
                <component :is="result(a).icon" aria-hidden="true" />
              </span>
            </template>

            <button
              v-if="expert"
              class="hres hres--more"
              type="button"
              :aria-label="open.has(a.id) ? 'Скрыть параметры' : 'Параметры и ответ Директа'"
              v-tip="open.has(a.id) ? 'Скрыть параметры' : 'Параметры и ответ Директа'"
              :aria-expanded="open.has(a.id)"
              :aria-controls="`hmore-${a.id}`"
              @click="toggleOpen(a.id)"
            >
              <ChevronDown aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    </template>
    <ExpertHint v-if="!expert">Параметры каждого изменения и ответ Директа</ExpertHint>
  </section>

  <div v-if="hasMore && items.length" class="pager">
    <button class="btn btn--ghost" type="button" :disabled="loading" @click="loadMore">
      {{ loading ? 'Загружаем…' : 'Показать ещё' }}
    </button>
  </div>
  <UndoDialog :log-id="undoId" @close="undoId = null" @done="reload" />
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import {
  Archive,
  ArchiveRestore,
  Ban,
  Check,
  ChevronDown,
  CircleDot,
  Copy,
  FilePlus,
  FolderTree,
  KeyRound,
  Link2,
  ListX,
  Megaphone,
  Pause,
  Pencil,
  Play,
  SearchCheck,
  Send,
  Settings2,
  SlidersHorizontal,
  Trash2,
  TriangleAlert,
  Undo2,
  Wallet,
  X,
} from '@lucide/vue'
import ActionWho from './ActionWho.vue'
import ExpertHint from './ExpertHint.vue'
import EmptyState from './ui/EmptyState.vue'
import PageHeader from './ui/PageHeader.vue'
import UiListTools from './ui/UiListTools.vue'
import UiNotice from './ui/UiNotice.vue'
import UiSelect from './ui/UiSelect.vue'
import UiStatus from './ui/UiStatus.vue'
import { useExpert } from '../composables/useExpert'
import { useViewport } from '../composables/useViewport'
import UndoDialog from './UndoDialog.vue'
import { errorText, webApi, wsApi } from '../api/web'
import { useWorkspaces } from '../composables/useWorkspaces'
import { formatDateTime, keepNumbers, niceQuotes } from '../utils/format'

/** Журнал изменений с откатом: по одному проекту (workspaceId) или по всем — с фильтрами проекта, типа и источника. */
const props = defineProps({ workspaceId: { type: Number, default: null } })

const PAGE = 50
const SOURCES = [
  { id: '', label: 'Все' },
  { id: 'user', label: 'Вы' },
  { id: 'ai', label: 'ИИ' },
  { id: 'rule', label: 'Правила' },
]
// Те же группы, что ACTION_KINDS в backend/app/journal.py.
const KINDS = [
  { value: '', label: 'Все действия' },
  { value: 'budget', label: 'Бюджеты и ставки' },
  { value: 'negatives', label: 'Минус-фразы и площадки' },
  { value: 'state', label: 'Запуск, остановка, архив' },
  { value: 'edits', label: 'Кампании, группы, объявления' },
  { value: 'undo', label: 'Возвраты' },
]
const ACTION_ICONS = {
  set_budget: Wallet,
  budget: Wallet,
  bid_adjust: SlidersHorizontal,
  negatives: ListX,
  negative_list_apply: ListX,
  queries: SearchCheck,
  exclude_sites: Ban,
  pause: Pause,
  resume: Play,
  archive: Archive,
  unarchive: ArchiveRestore,
  delete: Trash2,
  moderate: Send,
  create: FilePlus,
  update: Pencil,
  settings: Settings2,
  duplicate: Copy,
  keywords: KeyRound,
  ads: Megaphone,
  adgroups: FolderTree,
  links: Link2,
  undo: Undo2,
}
const RESULTS = {
  ok: { tone: 'ok', icon: Check, label: 'Выполнено' },
  partial: { tone: 'warn', icon: TriangleAlert, label: 'Выполнено частично', problem: true },
  error: { tone: 'bad', icon: X, label: 'Директ не принял', problem: true },
}

const { expert } = useExpert()
const { workspaces } = useWorkspaces()
const projects = computed(() => workspaces.value.filter((w) => !w.locked))
const showProjectFilter = computed(() => !props.workspaceId && projects.value.length > 1)
const projectOptions = computed(() => [
  { value: 0, label: 'Все проекты' },
  ...projects.value.map((p) => ({ value: p.id, label: niceQuotes(p.name) })),
])

const description = computed(() =>
  expert.value
    ? 'Что меняли в Директе вы, ИИ и правила — с параметрами и ответом Директа. Большинство действий можно откатить.'
    : 'Всё, что меняли в Директе: вы, ИИ с вашего согласия и правила. Почти любое изменение можно вернуть одной кнопкой.',
)

const items = ref([])
const loading = ref(false)
const loadError = ref('')
// Ссылки вида «Что правила изменили» открывают журнал сразу с фильтром: ?source=rule, ?kind=budget.
const route = useRoute()
const source = ref(SOURCES.some((s) => s.id && s.id === route.query.source) ? route.query.source : '')
const kind = ref(KINDS.some((k) => k.value && k.value === route.query.kind) ? route.query.kind : '')
const project = ref(0)
// ?campaign=<id строки кампании> — «Вся история» со страницы кампании.
const campaignOf = (q) => Number(q) || 0
const campaign = ref(campaignOf(route.query.campaign))
const router = useRouter()
const hasMore = ref(false)
const undoId = ref(null)
const open = ref(new Set())

const filtered = computed(() => !!(source.value || kind.value || project.value || campaign.value))
// Телефон: фильтры — в шторке за кнопкой «Фильтры», на ней — сколько включено.
const { phone } = useViewport()
const sheetFilters = computed(
  () => (source.value ? 1 : 0) + (kind.value ? 1 : 0) + (project.value ? 1 : 0) + (campaign.value ? 1 : 0),
)
// Название кампании фильтра — из записей (их кампании приходят с сервера).
const campaignLabel = computed(() => {
  for (const a of items.value) {
    const c = (a.campaigns || []).find((x) => x.id === campaign.value)
    if (c) return campaignName(c)
  }
  return `№ ${campaign.value}`
})

function dayKey(d) {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

function dayLabel(iso) {
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1)
  if (dayKey(d) === dayKey(today)) return 'Сегодня'
  if (dayKey(d) === dayKey(yesterday)) return 'Вчера'
  return d.toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric',
  })
}

const timeOf = (iso) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })

/** Записи по дням: «Сегодня», «Вчера», дальше дата. */
const days = computed(() => {
  const byDay = new Map()
  for (const a of items.value) {
    const label = dayLabel(a.created_at)
    if (!byDay.has(label)) byDay.set(label, { label, items: [] })
    byDay.get(label).items.push(a)
  }
  return [...byDay.values()]
})

const emptyText = computed(() => {
  if (source.value || kind.value) return 'Таких изменений пока нет.'
  if (campaign.value) return 'В этой кампании пока ничего не меняли.'
  const where = props.workspaceId || project.value ? 'В этом проекте ещё ничего не меняли' : 'Ещё ничего не меняли'
  return `${where}. Здесь появятся все правки — ваши, ИИ и правил, — и любую можно будет вернуть.`
})
const chatLink = computed(() => {
  const ws = props.workspaceId || project.value
  return ws ? `/w/${ws}/chat` : '/cabinet/chat'
})

const isFailed = (a) => a.status === 'error' || a.status === 'failed'

function result(a) {
  if (isFailed(a)) return RESULTS.error
  return RESULTS[a.status] || RESULTS.ok
}

/** Что не применилось и почему — словами с сервера («2 из 10 не применились: …»), полностью — по клику на значок. */
function problemsOf(a) {
  if (Array.isArray(a.problems)) return a.problems
  // Запасной путь для ответа без готовых строк.
  const r = a.result && typeof a.result === 'object' ? a.result : {}
  const out = []
  if (r.message || r.error) out.push(String(r.message || r.error))
  if (Array.isArray(r.errors)) {
    for (const e of r.errors.slice(0, 10)) {
      if (typeof e === 'string') out.push(e)
      else if (e) out.push([e.name || e.campaign_name, e.message || e.error].filter(Boolean).join(': ') || '—')
    }
    if (r.errors.length > 10) out.push(`и ещё ${r.errors.length - 10}`)
  } else if (typeof r.errors === 'number' && r.errors) {
    out.push(`Ошибок: ${r.errors}`)
  }
  if (!out.length && result(a).problem) out.push('Директ не прислал причину. Попробуйте ещё раз или откройте кампанию в Директе.')
  return out
}

function pretty(value) {
  if (value == null || (typeof value === 'object' && !Object.keys(value).length)) return '—'
  return JSON.stringify(value, null, 2)
}

/** Первая строка причины — прямо в строке истории; «14 из 60 не применились:» без висящего двоеточия. */
const firstProblem = (a) => String(problemsOf(a)[0] || '').replace(/:$/, '')

/** Кампании записи — ссылками на кампанию в сервисе; две первые, остальные числом. */
const campaignsOf = (a) => (a.campaigns || []).slice(0, 2)
const moreCampaigns = (a) => Math.max((a.campaigns_total || 0) - campaignsOf(a).length, 0)
const campaignName = (c) => niceQuotes(c.name) || `Кампания ${c.id}`
const directTip = (c) => (expert.value && c.direct_id ? `№ в Директе: ${c.direct_id}` : '')

function summaryLine(a) {
  const r = a.result && typeof a.result === 'object' ? a.result : {}
  const parts = []
  if (a.undo_of) parts.push(`откат записи № ${a.undo_of}`)
  // Есть строки «было → стало» — счётчики из ответа их только повторят.
  if (a.changes?.length) return parts.join(', ')
  if (r.applied != null) parts.push(`применено ${r.applied}`)
  if (r.negative != null && r.target != null) parts.push(`минус ${r.negative}, целевых ${r.target}`)
  else if (r.added != null && r.removed != null) parts.push(`+${r.added} / −${r.removed}`)
  return parts.join(', ')
}

function toggleOpen(id) {
  const next = new Set(open.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  open.value = next
}

async function fetchPage(beforeId) {
  loading.value = true
  loadError.value = ''
  try {
    const params = { limit: PAGE }
    if (source.value) params.source = source.value
    if (kind.value) params.kind = kind.value
    if (campaign.value) params.campaign_row_id = campaign.value
    if (beforeId) params.before_id = beforeId
    let data
    if (props.workspaceId) {
      data = await wsApi.actions(props.workspaceId, params)
    } else {
      if (project.value) params.workspace_id = project.value
      data = await webApi.actionLogs(params)
    }
    const page = data.items || []
    items.value = beforeId ? [...items.value, ...page] : page
    hasMore.value = page.length === PAGE
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

function reload() {
  items.value = []
  open.value = new Set()
  return fetchPage()
}

function resetFilters() {
  source.value = ''
  kind.value = ''
  project.value = 0
  campaign.value = 0
}

function loadMore() {
  const last = items.value[items.value.length - 1]
  if (last) fetchPage(last.id)
}

watch([source, kind, project, campaign], reload)
// Фильтр кампании живёт в адресе: снятый крестиком — убираем из адреса, новая ссылка «Вся история» — подхватываем.
watch(campaign, (id) => {
  if (campaignOf(route.query.campaign) === id) return
  const query = { ...route.query }
  if (id) query.campaign = String(id)
  else delete query.campaign
  router.replace({ query })
})
watch(
  () => route.query.campaign,
  (q) => {
    campaign.value = campaignOf(q)
  },
)
onMounted(() => fetchPage())
</script>

<style scoped>
.hist-camp {
  min-height: 32px;
  max-width: 100%;
  border: 0;
  cursor: pointer;
}
.hist-camp__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.hist > .section-h:first-child {
  margin-top: 0;
}
.hskel__kind {
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: 50%;
}
.hskel__sub {
  width: 24%;
  height: 10px;
  margin-top: 9px;
}
.hrow {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 14px 18px;
  border-top: 1px solid var(--line);
}
.hrow:first-child {
  border-top: 0;
}
.hrow__kind {
  display: grid;
  place-items: center;
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--ink) 7%, transparent);
  color: var(--ink-2, var(--ink));
  font-style: normal;
}
.hrow__main {
  flex: 1;
  min-width: 0;
}
.hrow__main b {
  display: block;
  font-size: 15px;
}
.hrow__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 10px;
  margin-top: 2px;
  color: var(--faint);
  font-size: 12px;
}
.hrow__meta a {
  color: var(--muted);
}
/* Кампания в строке — ссылкой; длинное название не раздвигает строку. */
.hrow__camp {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hrow__changes {
  margin: 3px 0 0;
  padding: 0;
  list-style: none;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.45;
}
.hrow__changes li {
  overflow-wrap: anywhere;
}
.hrow__problem {
  margin: 3px 0 0;
  font-size: 13px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.hrow__problem--warn {
  color: var(--warn);
}
.hrow__problem--bad {
  color: var(--danger);
}
.hrow__morebtn {
  margin-left: 6px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.hrow__cut {
  margin: 0 0 8px;
  color: var(--faint);
  font-size: 12px;
}
.hrow.is-undone .hrow__changes {
  color: var(--faint);
}
.hrow__acts {
  display: flex;
  flex: none;
  align-items: center;
  gap: 6px;
}
.hrow__acts .btn {
  gap: 6px;
}
/* Результат — одной иконкой; ошибка раскрывается по клику. */
.hres {
  display: inline-grid;
  place-items: center;
  flex: none;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 50%;
  background: transparent;
  color: var(--faint);
  font: inherit;
}
.hres svg {
  width: 16px;
  height: 16px;
}
button.hres {
  cursor: pointer;
}
button.hres:hover {
  border-color: var(--line);
  background: var(--surface-2);
}
.hres--ok {
  color: var(--ok);
}
.hres--warn {
  color: var(--warn);
}
.hres--bad {
  color: var(--danger);
}
.hres--more svg {
  transition: transform 0.15s ease;
}
.hres--more[aria-expanded='true'] svg {
  transform: rotate(180deg);
}
.hrow__more {
  margin-top: 10px;
}
.hrow__errh {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 700;
}
.hrow__errs {
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 13px;
}
.hrow__more .log-row__body {
  padding: 10px 0 0;
}
.hrow.is-undone b {
  color: var(--faint);
  font-weight: 500;
  text-decoration: line-through;
}
/* На узком экране кнопки с текстом уходят под заголовок, чтобы он не сжимался в столбик;
   одна иконка результата остаётся справа. */
@media (max-width: 640px) {
  .hrow {
    gap: 12px;
    padding: 12px 14px;
  }
  .hrow:has(.hrow__acts .btn, .hrow__acts .ui-status) {
    display: grid;
    grid-template-columns: 32px minmax(0, 1fr);
    column-gap: 12px;
    row-gap: 8px;
  }
  .hrow:has(.hrow__acts .btn, .hrow__acts .ui-status) .hrow__acts {
    grid-column: 2;
    flex-wrap: wrap;
  }
}
</style>
