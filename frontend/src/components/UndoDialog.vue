<template>
  <Modal :open="logId != null" :title="preview?.label || 'Откат действия'" :locked="busy" @close="emit('close')">
    <p v-if="loading" class="empty">Готовим откат…</p>
    <p v-else-if="error" class="form-error">{{ error }}</p>
    <template v-else-if="preview">
      <p class="undo-title"><strong>{{ preview.title }}</strong></p>
      <p v-if="!preview.available" class="notice notice--warn">{{ preview.reason }}</p>
      <template v-else>
        <h4 class="undo-head">Что сделаем</h4>
        <ol class="undo-steps">
          <li v-for="(s, i) in preview.steps" :key="i">{{ s }}</li>
        </ol>
        <div v-if="preview.warnings.length" class="notice notice--warn undo-warnings">
          <p v-for="(w, i) in preview.warnings" :key="i">{{ w }}</p>
          <ul v-if="preview.later.length" class="undo-later">
            <li v-for="l in preview.later" :key="l.id">
              {{ l.title }} · {{ ACTION_SOURCES[l.source] || l.source }} · {{ formatDateTime(l.created_at) }}
            </li>
          </ul>
        </div>
      </template>
      <p v-if="busy" class="sync-status">{{ progress || 'Откатываем…' }}</p>
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
        {{ busy ? 'Откатываем…' : 'Откатить' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { ref, watch } from 'vue'
import Modal from './ui/Modal.vue'
import { errorText, jobProgressText, undoApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { ACTION_SOURCES, formatDateTime } from '../utils/format'

// Окно отката: показывает шаги словами и предупреждения, затем запускает откат задачей.
const props = defineProps({ logId: { type: Number, default: null } })
const emit = defineEmits(['close', 'done'])

const preview = ref(null)
const loading = ref(false)
const error = ref('')
const busy = ref(false)
const progress = ref('')

watch(
  () => props.logId,
  async (id) => {
    preview.value = null
    error.value = ''
    progress.value = ''
    if (id == null) return
    loading.value = true
    try {
      const data = await undoApi.preview(id)
      if (props.logId === id) preview.value = data
    } catch (err) {
      error.value = errorText(err)
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

async function run() {
  const id = props.logId
  busy.value = true
  try {
    const res = await undoApi.run(id, (job) => {
      progress.value = jobProgressText(job)
    })
    const failed = (res.steps || []).filter((s) => s.status !== 'ok')
    pushToast(
      failed.length ? `Откат выполнен частично: не вышло ${failed.length} из ${res.steps.length}` : 'Откачено',
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
