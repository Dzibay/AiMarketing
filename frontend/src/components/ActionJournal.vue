<template>
  <PageHeader :description="description">
    <template v-if="showProjectFilter || expert" #actions>
      <UiSelect
        v-if="showProjectFilter"
        v-model="project"
        :options="projectOptions"
        aria-label="Проект"
        @change="reload"
      />
      <div v-if="expert" class="seg" role="group" aria-label="Кто менял">
        <button
          v-for="s in SOURCES"
          :key="s.id"
          type="button"
          :class="{ active: source === s.id }"
          :aria-pressed="source === s.id"
          @click="setSource(s.id)"
        >
          {{ s.label }}
        </button>
      </div>
    </template>
  </PageHeader>

  <UiNotice v-if="loadError" tone="error">
    Не удалось загрузить историю. {{ loadError }}
    <template #action>
      <button class="btn btn--ghost btn--sm" type="button" :disabled="loading" @click="reload">Повторить</button>
    </template>
  </UiNotice>

  <div v-if="loading && !items.length" class="ccard" aria-busy="true" aria-label="Загружаем историю">
    <div v-for="n in 4" :key="n" class="hrow">
      <span class="ui-skel hskel__who" />
      <div class="hrow__main">
        <span class="ui-skel" :style="{ width: `${62 - n * 7}%` }" />
        <span class="ui-skel hskel__sub" />
      </div>
    </div>
  </div>
  <EmptyState v-else-if="!items.length && !loadError" :icon="History" :text="emptyText" />

  <section v-else-if="items.length && !expert" class="hist">
    <template v-for="d in days" :key="d.label">
      <h3 class="section-h">{{ d.label }}</h3>
      <div class="ccard">
        <div v-for="a in d.items" :key="a.id" class="hrow" :class="{ 'is-undone': a.undone_at }">
          <i class="hrow__who" :title="ACTION_SOURCES[a.source] || a.source">
            <component :is="WHO_ICONS[a.source] || User" :size="16" aria-hidden="true" />
          </i>
          <div class="hrow__main">
            <b>{{ a.title || a.action }}</b>
            <small>
              {{ timeOf(a.created_at) }} · {{ ACTION_SOURCES[a.source] || a.source }}
              <template v-if="!workspaceId && a.workspace_id">
                ·
                <RouterLink :to="`/w/${a.workspace_id}/history`">{{ a.workspace_name || `Проект ${a.workspace_id}` }}</RouterLink>
              </template>
            </small>
          </div>
          <div class="hrow__acts">
            <UiStatus
              v-if="a.undone_at"
              tone="off"
              :icon="Undo2"
              label="Возвращено"
              :hint="`Вернули ${formatDateTime(a.undone_at)}`"
            />
            <UiStatus v-else-if="isFailed(a)" tone="bad" label="Директ не принял" :hint="summaryLine(a)" />
            <UiStatus v-else-if="a.status === 'partial'" tone="warn" label="Частично" :hint="summaryLine(a)" />
            <button
              v-if="!a.undone_at && a.undo"
              class="btn btn--ghost btn--sm"
              type="button"
              :title="a.undo.label"
              @click="undoId = a.id"
            >
              <Undo2 aria-hidden="true" />
              Вернуть как было
            </button>
          </div>
        </div>
      </div>
    </template>
    <ExpertHint>Фильтр по источнику, параметры каждого изменения и ответ Директа</ExpertHint>
  </section>

  <section v-else-if="items.length" class="panel">
    <div class="list">
      <details v-for="a in items" :key="a.id" class="log-row" :class="{ 'is-undone': a.undone_at }">
        <summary class="list-row">
          <div>
            <strong>{{ a.title || a.action }}</strong>
            <small>
              <span class="chip" :class="`chip--src-${a.source}`">{{ ACTION_SOURCES[a.source] || a.source }}</span>
              <RouterLink
                v-if="!workspaceId && a.workspace_id"
                class="log-row__project"
                :to="`/w/${a.workspace_id}/history`"
                @click.stop
              >
                {{ a.workspace_name || `Проект ${a.workspace_id}` }}
              </RouterLink>
              {{ formatDateTime(a.created_at) }}
              <template v-if="summaryLine(a)"> · {{ summaryLine(a) }}</template>
            </small>
          </div>
          <div class="log-row__side">
            <UiStatus v-if="a.undone_at" tone="off" :icon="Undo2" label="Откачено" :hint="formatDateTime(a.undone_at)" />
            <UiStatus v-else v-bind="STATUS[a.status] || { tone: 'info', label: a.status || '—' }" />
            <button
              v-if="!a.undone_at && a.undo"
              class="btn btn--ghost btn--sm"
              :class="{ 'btn--danger': a.undo.danger }"
              type="button"
              :title="a.undo.label"
              @click.prevent.stop="undoId = a.id"
            >
              Откатить
            </button>
          </div>
        </summary>
        <div class="log-row__body">
          <div>
            <h4>Параметры</h4>
            <pre>{{ pretty(a.payload) }}</pre>
          </div>
          <div>
            <h4>Результат</h4>
            <pre>{{ pretty(a.result) }}</pre>
          </div>
        </div>
      </details>
    </div>
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
import { RouterLink, useRoute } from 'vue-router'
import { History, Sparkles, Undo2, User, Zap } from '@lucide/vue'
import ExpertHint from './ExpertHint.vue'
import EmptyState from './ui/EmptyState.vue'
import PageHeader from './ui/PageHeader.vue'
import UiNotice from './ui/UiNotice.vue'
import UiSelect from './ui/UiSelect.vue'
import UiStatus from './ui/UiStatus.vue'
import { useExpert } from '../composables/useExpert'
import UndoDialog from './UndoDialog.vue'
import { errorText, webApi, wsApi } from '../api/web'
import { useWorkspaces } from '../composables/useWorkspaces'
import { ACTION_SOURCES, formatDateTime } from '../utils/format'

/** Журнал изменений с откатом: по одному проекту (workspaceId) или по всем — с фильтром проекта и источника. */
const props = defineProps({ workspaceId: { type: Number, default: null } })

const PAGE = 50
const SOURCES = [
  { id: '', label: 'Все' },
  { id: 'user', label: 'Вы' },
  { id: 'ai', label: 'ИИ' },
  { id: 'rule', label: 'Правила' },
]
const STATUS = {
  ok: { tone: 'ok', label: 'Выполнено' },
  partial: { tone: 'warn', label: 'Частично' },
  error: { tone: 'bad', label: 'Ошибка' },
  failed: { tone: 'bad', label: 'Ошибка' },
}

const WHO_ICONS = { ai: Sparkles, user: User, rule: Zap }

const { expert } = useExpert()
const { workspaces } = useWorkspaces()
const projects = computed(() => workspaces.value.filter((w) => !w.locked))
const showProjectFilter = computed(() => !props.workspaceId && projects.value.length > 1)
const projectOptions = computed(() => [
  { value: 0, label: 'Все проекты' },
  ...projects.value.map((p) => ({ value: p.id, label: p.name })),
])

const description = computed(() =>
  expert.value
    ? 'Что меняли в Директе вы, ИИ и правила — с параметрами и ответом Директа. Большинство действий можно откатить.'
    : 'Всё, что меняли в Директе: вы, ИИ с вашего согласия и правила. Почти любое изменение можно вернуть одной кнопкой.',
)

const items = ref([])
const loading = ref(false)
const loadError = ref('')
// «Что правила изменили» с Автопилота открывает журнал сразу с фильтром: ?source=rule.
const route = useRoute()
const source = ref(expert.value && SOURCES.some((s) => s.id && s.id === route.query.source) ? route.query.source : '')
const project = ref(0)
const hasMore = ref(false)
const undoId = ref(null)

function dayKey(iso) {
  const d = new Date(iso)
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

/** Записи по дням — как в макете: «Сегодня», «Вчера», дальше дата. */
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
  if (source.value) return 'Таких изменений пока нет.'
  return props.workspaceId || project.value ? 'В этом проекте ещё ничего не меняли.' : 'Ещё ничего не меняли.'
})

const isFailed = (a) => a.status === 'error' || a.status === 'failed'

function pretty(value) {
  if (value == null || (typeof value === 'object' && !Object.keys(value).length)) return '—'
  return JSON.stringify(value, null, 2)
}

function summaryLine(a) {
  const r = a.result || {}
  const parts = []
  if (a.undo_of) parts.push(`откат записи № ${a.undo_of}`)
  if (r.applied != null) parts.push(`применено ${r.applied}`)
  if (r.negative != null && r.target != null) parts.push(`минус ${r.negative}, целевых ${r.target}`)
  else if (r.added != null && r.removed != null) parts.push(`+${r.added} / −${r.removed}`)
  if (Array.isArray(r.errors) && r.errors.length) parts.push(`ошибок ${r.errors.length}`)
  else if (typeof r.errors === 'number' && r.errors) parts.push(`ошибок ${r.errors}`)
  if (r.error) parts.push(String(r.message || r.error))
  return parts.join(', ')
}

async function fetchPage(beforeId) {
  loading.value = true
  loadError.value = ''
  try {
    const params = { limit: PAGE }
    if (source.value) params.source = source.value
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
  return fetchPage()
}

function setSource(id) {
  source.value = id
  reload()
}

function loadMore() {
  const last = items.value[items.value.length - 1]
  if (last) fetchPage(last.id)
}

onMounted(() => fetchPage())

// Фильтра по источнику в простом режиме нет — сбрасываем его, чтобы не прятать записи молча.
watch(expert, (on) => {
  if (!on && source.value) setSource('')
})
</script>

<style scoped>
.hist > .section-h:first-child {
  margin-top: 0;
}
.hskel__who {
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
.hrow__who {
  display: grid;
  place-items: center;
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--ink) 7%, transparent);
  color: var(--muted);
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
.hrow__main small {
  color: var(--faint);
  font-size: 12px;
}
.hrow__main small a {
  color: inherit;
}
.hrow__acts {
  display: flex;
  flex: none;
  align-items: center;
  gap: 6px;
}
.hrow.is-undone b {
  color: var(--faint);
  font-weight: 500;
  text-decoration: line-through;
}
/* На узком экране статус и «Вернуть» уходят под текст, чтобы заголовок не сжимался в столбик. */
@media (max-width: 640px) {
  .hrow {
    display: grid;
    grid-template-columns: 32px minmax(0, 1fr);
    column-gap: 14px;
    row-gap: 10px;
  }
  .hrow__acts {
    grid-column: 2;
    flex-wrap: wrap;
  }
  .hrow__acts:empty {
    display: none;
  }
}
</style>
