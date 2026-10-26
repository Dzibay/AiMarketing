<template>
  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Быстрые действия</h2>
        <p class="hint">Частые операции с кампаниями пространства. Каждое изменение попадает в журнал ниже.</p>
      </div>
    </div>
    <div class="quick-actions">
      <RouterLink class="quick-action" :to="`/w/${ws.id}/campaigns?new=1`">
        <strong>Создать кампанию</strong>
        <small>Мастер: кабинет, стратегия, расписание, первая группа</small>
      </RouterLink>
      <RouterLink class="quick-action" :to="`/w/${ws.id}/campaigns`">
        <strong>Копировать, остановить, бюджеты</strong>
        <small>Массовые действия в списке кампаний</small>
      </RouterLink>
      <RouterLink class="quick-action" to="/cabinet/negative-lists">
        <strong>Общие минус-фразы</strong>
        <small>Списки с предпросмотром и применением к кампаниям</small>
      </RouterLink>
      <RouterLink class="quick-action" :to="{ path: `/w/${ws.id}/chat`, query: { ask: 'audit' } }">
        <strong>Аудит с ИИ</strong>
        <small>Проверить кампании и получить план правок</small>
      </RouterLink>
    </div>
  </section>

  <RulesManager :workspace="ws" />

  <section class="panel">
    <div class="panel__head">
      <div>
        <h2>Журнал изменений</h2>
        <p class="hint">Что меняли в Директе вы, ИИ и правила — с параметрами и результатом.</p>
      </div>
      <div class="seg" role="group" aria-label="Источник">
        <button v-for="s in SOURCES" :key="s.id" type="button" :class="{ active: source === s.id }" @click="setSource(s.id)">
          {{ s.label }}
        </button>
      </div>
    </div>

    <p v-if="loading && !items.length" class="empty">Загружаем журнал…</p>
    <p v-else-if="loadError" class="empty">{{ loadError }}</p>
    <p v-else-if="!items.length" class="empty">Записей нет.</p>
    <div v-else class="list">
      <details v-for="a in items" :key="a.id" class="log-row">
        <summary class="list-row">
          <div>
            <strong>{{ a.title || a.action }}</strong>
            <small>
              <span class="chip" :class="`chip--src-${a.source}`">{{ ACTION_SOURCES[a.source] || a.source }}</span>
              {{ formatDateTime(a.created_at) }}
              <template v-if="summaryLine(a)"> · {{ summaryLine(a) }}</template>
            </small>
          </div>
          <span :class="ACTION_STATUS_TONES[a.status]">{{ STATUS_LABELS[a.status] || a.status }}</span>
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
    <div v-if="hasMore" class="pager">
      <button class="btn btn--ghost" type="button" :disabled="loading" @click="loadMore">
        {{ loading ? 'Загружаем…' : 'Показать ещё' }}
      </button>
    </div>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import RulesManager from '../../components/RulesManager.vue'
import { errorText, wsApi } from '../../api/web'
import { ACTION_SOURCES, ACTION_STATUS_TONES, formatDateTime } from '../../utils/format'
import { useWs } from './context'

const PAGE = 50
const SOURCES = [
  { id: '', label: 'Все' },
  { id: 'user', label: 'Вы' },
  { id: 'ai', label: 'ИИ' },
  { id: 'rule', label: 'Правила' },
]
const STATUS_LABELS = { ok: 'выполнено', partial: 'частично', error: 'ошибка', failed: 'ошибка' }

const { ws } = useWs()
const items = ref([])
const loading = ref(false)
const loadError = ref('')
const source = ref('')
const hasMore = ref(false)

function pretty(value) {
  if (value == null || (typeof value === 'object' && !Object.keys(value).length)) return '—'
  return JSON.stringify(value, null, 2)
}

function summaryLine(a) {
  const r = a.result || {}
  const parts = []
  if (r.applied != null) parts.push(`применено ${r.applied}`)
  if (Array.isArray(r.errors) && r.errors.length) parts.push(`ошибок ${r.errors.length}`)
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
    const data = await wsApi.actions(ws.value.id, params)
    const page = data.items || []
    items.value = beforeId ? [...items.value, ...page] : page
    hasMore.value = page.length === PAGE
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

function setSource(id) {
  source.value = id
  items.value = []
  fetchPage()
}

function loadMore() {
  const last = items.value[items.value.length - 1]
  if (last) fetchPage(last.id)
}

onMounted(() => fetchPage())
</script>
