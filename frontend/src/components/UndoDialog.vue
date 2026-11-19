<template>
  <Modal :open="key != null" :title="preview?.label || 'Откат действия'" :locked="busy" @close="emit('close')">
    <p v-if="loading" class="empty">Готовим откат…</p>
    <p v-else-if="error" class="form-error">{{ error }}</p>
    <template v-else-if="preview">
      <p class="undo-title"><strong>{{ preview.title }}</strong></p>
      <p v-if="!preview.available" class="notice notice--warn">{{ preview.reason }}</p>
      <template v-else-if="group">
        <!-- Несколько записей: каждая словами — что поменялось и что вернём. -->
        <h4 class="undo-head">Что вернём</h4>
        <ul class="undo-entries">
          <li v-for="e in shownEntries.slice(0, ENTRY_LIMIT)" :key="e.log_id" class="undo-entry">
            <span class="undo-entry__title">{{ e.title }}</span>
            <!-- «Было → стало» из «Истории»; если его нет — шаги отката этой записи. -->
            <ul v-if="linesOf(e).length" class="undo-entry__changes">
              <li v-for="(line, i) in linesOf(e)" :key="i">{{ line }}</li>
            </ul>
          </li>
          <!-- Вернутся все записи; список — только первые, чтобы окно не было бесконечным. -->
          <li v-if="moreEntries" class="undo-entry undo-entry__changes">
            И ещё {{ moreEntries }} {{ plural(moreEntries, 'изменение', 'изменения', 'изменений') }} — тоже вернутся
          </li>
        </ul>
        <details v-if="preview.steps.length" class="undo-more">
          <summary>Шаги отката</summary>
          <ol class="undo-steps">
            <li v-for="(s, i) in preview.steps" :key="i">{{ s }}</li>
          </ol>
        </details>
      </template>
      <template v-else>
        <h4 class="undo-head">Что сделаем</h4>
        <ol class="undo-steps">
          <li v-for="(s, i) in preview.steps" :key="i">{{ s }}</li>
        </ol>
      </template>
      <div v-if="preview.available && preview.blocked?.length" class="notice undo-blocked">
        <p>Это вернуть нельзя:</p>
        <ul>
          <li v-for="(b, i) in preview.blocked" :key="i">{{ b }}</li>
        </ul>
      </div>
      <p v-if="preview.available && preview.undone?.length" class="undo-note">
        Уже возвращено: {{ preview.undone.join(', ') }}
      </p>
      <div v-if="preview.available && preview.warnings.length" class="notice notice--warn undo-warnings">
        <p v-for="(w, i) in preview.warnings" :key="i">{{ w }}</p>
        <ul v-if="preview.later.length" class="undo-later">
          <li v-for="l in preview.later" :key="l.id">
            {{ l.title }} · {{ ACTION_SOURCES[l.source] || l.source }} · {{ formatDateTime(l.created_at) }}
          </li>
        </ul>
      </div>
      <p v-if="busy" class="sync-status">{{ progress || 'Возвращаем…' }}</p>
    </template>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="emit('close')">Отмена</button>
      <button
        v-if="preview?.available"
        class="btn"
        :class="preview.danger ? 'btn--danger-solid' : 'btn--accent'"
        type="button"
        :disabled="busy"
        @click="run"
      >
        {{ busy ? 'Возвращаем…' : group ? preview.label : 'Откатить' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import Modal from './ui/Modal.vue'
import { errorText, jobProgressText, undoApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { ACTION_SOURCES, formatDateTime, plural } from '../utils/format'

// Окно отката: показывает шаги словами и предупреждения, затем запускает откат задачей.
// logId — одна запись журнала; logIds — несколько записей одной задачи; aiActionId — «Вернуть как было»
// у выполненного предложения ИИ (все записи его решения). Задаётся что-то одно.
const props = defineProps({
  logId: { type: Number, default: null },
  logIds: { type: Array, default: null },
  aiActionId: { type: Number, default: null },
})
const emit = defineEmits(['close', 'done'])

const group = computed(() => props.aiActionId != null || (props.logIds?.length ?? 0) > 0)
const key = computed(() => {
  if (props.aiActionId != null) return `ai:${props.aiActionId}`
  if (props.logIds?.length) return `ids:${props.logIds.join(',')}`
  return props.logId != null ? `log:${props.logId}` : null
})

const preview = ref(null)
const loading = ref(false)
const error = ref('')
const busy = ref(false)
const progress = ref('')

// Записи, которые откатятся: заблокированные и уже возвращённые названы отдельно.
const shownEntries = computed(() => (preview.value?.entries || []).filter((e) => e.available))
const ENTRY_LIMIT = 30
const moreEntries = computed(() => Math.max(0, shownEntries.value.length - ENTRY_LIMIT))
const linesOf = (e) => (e.changes?.length ? e.changes : e.steps || [])

function load() {
  if (props.aiActionId != null) return undoApi.aiPreview(props.aiActionId)
  if (props.logIds?.length) return undoApi.groupPreview(props.logIds)
  return undoApi.preview(props.logId)
}

function start(onProgress) {
  if (props.aiActionId != null) return undoApi.aiRun(props.aiActionId, onProgress)
  if (props.logIds?.length) return undoApi.groupRun(preview.value?.log_ids || props.logIds, onProgress)
  return undoApi.run(props.logId, onProgress)
}

watch(
  key,
  async (k) => {
    preview.value = null
    error.value = ''
    progress.value = ''
    if (k == null) return
    loading.value = true
    try {
      const data = await load()
      if (key.value === k) preview.value = data
    } catch (err) {
      if (key.value === k) error.value = errorText(err)
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

async function run() {
  busy.value = true
  try {
    const res = await start((job) => {
      progress.value = jobProgressText(job)
    })
    const steps = res.steps || []
    const failed = steps.filter((s) => s.status !== 'ok')
    const done = group.value ? 'Вернули как было' : 'Откачено'
    pushToast(
      failed.length ? `Вернули не всё: не вышло ${failed.length} из ${steps.length}` : done,
      failed.length ? 'error' : 'ok',
    )
    emit('done', res)
    emit('close')
  } catch (err) {
    error.value = errorText(err)
    preview.value = null
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.undo-entries {
  margin: 0 0 1rem;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.55rem;
}
.undo-entry__title {
  font-weight: 560;
}
.undo-entry__changes {
  margin: 2px 0 0;
  padding: 0;
  list-style: none;
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 1.45;
}
.undo-entry__changes li {
  overflow-wrap: anywhere;
}
.undo-more {
  margin: 0 0 1rem;
  font-size: 0.88rem;
}
.undo-more summary {
  cursor: pointer;
  color: var(--muted);
  margin-bottom: 0.4rem;
}
.undo-blocked {
  margin: 0 0 1rem;
}
.undo-blocked p {
  margin: 0 0 0.3rem;
  font-weight: 560;
}
.undo-blocked ul {
  margin: 0;
  padding-left: 1.1rem;
  font-size: 0.88rem;
}
.undo-note {
  margin: 0 0 1rem;
  color: var(--muted);
  font-size: 0.88rem;
}
</style>
