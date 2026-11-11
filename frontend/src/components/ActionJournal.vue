<template>
  <section v-if="!expert" class="hist">
    <p class="hist__lead">
      Всё, что меняли в Директе: вы, ИИ с вашего согласия и правила. Почти любое изменение можно вернуть одной кнопкой.
    </p>
    <div v-if="!workspaceId && projects.length > 1" class="hist__filter">
      <select v-model="project" aria-label="Проект" @change="reload">
        <option :value="0">Все проекты</option>
        <option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select>
    </div>

    <p v-if="loading && !items.length" class="ccard ccard--note">Загружаем историю…</p>
    <p v-else-if="loadError" class="ccard ccard--note">{{ loadError }}</p>
    <div v-else-if="!items.length" class="ccard ccard--note hist__empty">
      <History :size="22" aria-hidden="true" />
      <span>{{ workspaceId ? 'В этом проекте ещё ничего не меняли.' : 'Ещё ничего не меняли.' }}</span>
    </div>
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
              <template v-if="a.status === 'error' || a.status === 'failed'"> · <span class="tone-bad">Директ не принял</span></template>
              <template v-else-if="a.status === 'partial'"> · <span class="tone-warn">применено частично</span></template>
            </small>
          </div>
          <div class="hrow__acts">
            <span v-if="a.undone_at" class="st st--off"><Undo2 :size="13" aria-hidden="true" />Возвращено</span>
            <button v-else-if="a.undo" class="btn btn--ghost" type="button" :title="a.undo.label" @click="undoId = a.id">
              <Undo2 :size="14" aria-hidden="true" />
              Вернуть как было
            </button>
          </div>
        </div>
      </div>
    </template>
    <div v-if="hasMore" class="pager">
      <button class="btn btn--ghost" type="button" :disabled="loading" @click="loadMore">
        {{ loading ? 'Загружаем…' : 'Показать ещё' }}
      </button>
    </div>
    <ExpertHint>Фильтр по источнику, параметры каждого изменения и ответ Директа</ExpertHint>
    <UndoDialog :log-id="undoId" @close="undoId = null" @done="reload" />
  </section>

  <section v-else class="panel">
    <div class="panel__head">
      <div>
        <h2>Журнал изменений</h2>
        <p class="hint">
          Что меняли в Директе вы, ИИ и правила — с параметрами и результатом. Большинство действий можно откатить.
        </p>
      </div>
      <div class="row-actions">
        <select v-if="!workspaceId && projects.length > 1" v-model="project" aria-label="Проект" @change="reload">
          <option :value="0">Все проекты</option>
          <option v-for="p in projects" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
        <div class="seg" role="group" aria-label="Источник">
          <button
            v-for="s in SOURCES"
            :key="s.id"
            type="button"
            :class="{ active: source === s.id }"
            @click="setSource(s.id)"
          >
            {{ s.label }}
          </button>
        </div>
      </div>
    </div>

    <p v-if="loading && !items.length" class="empty">Загружаем журнал…</p>
    <p v-else-if="loadError" class="empty">{{ loadError }}</p>
    <p v-else-if="!items.length" class="empty">Записей нет.</p>
    <div v-else class="list">
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
            <span v-if="a.undone_at" class="chip">откачено {{ formatDateTime(a.undone_at) }}</span>
            <span v-else :class="ACTION_STATUS_TONES[a.status]">{{ STATUS_LABELS[a.status] || a.status }}</span>
            <button
              v-if="a.undo"
              class="btn btn--ghost"
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
    <UndoDialog :log-id="undoId" @close="undoId = null" @done="reload" />
    <div v-if="hasMore" class="pager">
      <button class="btn btn--ghost" type="button" :disabled="loading" @click="loadMore">
        {{ loading ? 'Загружаем…' : 'Показать ещё' }}
      </button>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { History, Sparkles, Undo2, User, Zap } from '@lucide/vue'
import ExpertHint from './ExpertHint.vue'
import { useExpert } from '../composables/useExpert'
import UndoDialog from './UndoDialog.vue'
import { errorText, webApi, wsApi } from '../api/web'
import { useWorkspaces } from '../composables/useWorkspaces'
import { ACTION_SOURCES, ACTION_STATUS_TONES, formatDateTime } from '../utils/format'

/** Журнал изменений с откатом: по одному проекту (workspaceId) или по всем — с фильтром проекта и источника. */
const props = defineProps({ workspaceId: { type: Number, default: null } })

const PAGE = 50
const SOURCES = [
  { id: '', label: 'Все' },
  { id: 'user', label: 'Вы' },
  { id: 'ai', label: 'ИИ' },
  { id: 'rule', label: 'Правила' },
]
const STATUS_LABELS = { ok: 'выполнено', partial: 'частично', error: 'ошибка', failed: 'ошибка' }

const WHO_ICONS = { ai: Sparkles, user: User, rule: Zap }

const { expert } = useExpert()
const { workspaces } = useWorkspaces()
const projects = computed(() => workspaces.value.filter((w) => !w.locked))

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
.hist__lead {
  margin: 0 0 6px;
  color: var(--muted);
  font-size: 15px;
}
.hist__filter {
  margin: 12px 0 0;
}
.hist__empty {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
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
.hrow__acts .btn {
  gap: 6px;
}
.hrow.is-undone b {
  color: var(--faint);
  font-weight: 500;
  text-decoration: line-through;
}
@media (max-width: 640px) {
  .hrow {
    flex-wrap: wrap;
  }
}
</style>
