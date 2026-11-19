<template>
  <Modal :open="open" title="Взять из проекта" wide :locked="busy" @close="$emit('close')">
    <p class="hint">Настройки другого проекта перенесутся сюда. Отметьте, что взять, и проверьте, что изменится.</p>
    <div v-field class="field cfp-pick">
      <label>Проект</label>
      <UiSelect v-model="sourceId" :options="sourceOptions" placeholder="Выберите проект" aria-label="Проект" />
    </div>

    <UiSkeleton v-if="sourceId && !preview && !error" variant="rows" :count="4" label="Сравниваем настройки" />
    <div v-else-if="preview" class="cfp-list">
      <div v-for="item in visibleItems" :key="item.kind" class="cfp-row" :class="{ 'is-off': !item.available }">
        <UiCheckbox v-model="picked[item.kind]" :disabled="!item.available || item.same" class="check-row">
          <strong>{{ item.label }}</strong>
        </UiCheckbox>
        <p v-if="!item.available" class="cfp-note">{{ item.note }}</p>
        <p v-else-if="item.same" class="cfp-note">Уже так же: {{ item.after }}</p>
        <p v-else class="cfp-diff">
          <span class="cfp-before">{{ item.before }}</span>
          <span class="cfp-arrow" aria-label="станет">→</span>
          <span class="cfp-after">{{ item.after }}</span>
        </p>
        <p v-if="item.available && !item.same && item.note" class="cfp-note">{{ item.note }}</p>
      </div>
    </div>
    <p v-if="error" class="form-error">{{ error }}</p>

    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="$emit('close')">Отмена</button>
      <button class="btn btn--accent" type="button" :disabled="busy || !chosen.length" @click="apply">
        {{ busy ? 'Переносим…' : chosen.length ? `Перенести (${chosen.length})` : 'Перенести' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { defaultsApi, errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { useExpert } from '../../composables/useExpert'
import { pushToast } from '../../composables/useToasts'
import { useWorkspaces } from '../../composables/useWorkspaces'
import { niceQuotes } from '../../utils/format'
import Modal from '../../components/ui/Modal.vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import UiSkeleton from '../../components/ui/UiSkeleton.vue'

/**
 * «Взять из проекта…»: шаблон ссылки, тон и обращение ИИ, модель, доступ ИИ, главный показатель, защита бюджета,
 * заметки и запомненные ответы. По каждому полю — «было → станет»; переносится только отмеченное.
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  wsId: { type: Number, required: true },
})
const emit = defineEmits(['close', 'applied'])

const { expert } = useExpert()
const { workspaces } = useWorkspaces()
const sourceId = ref(null)
const preview = ref(null)
const picked = reactive({})
const error = ref('')
const busy = ref(false)

const sourceOptions = computed(() =>
  workspaces.value
    .filter((w) => w.id !== props.wsId && !w.archived)
    .map((w) => ({ value: w.id, label: niceQuotes(w.name) })),
)
// Доступ ИИ — только в экспертном режиме, как и сама вкладка «Доступ ИИ».
const visibleItems = computed(() => (preview.value?.items || []).filter((i) => expert.value || i.kind !== 'ai_access'))
const chosen = computed(() => visibleItems.value.filter((i) => i.available && !i.same && picked[i.kind]).map((i) => i.kind))

async function load(id) {
  preview.value = null
  error.value = ''
  for (const k of Object.keys(picked)) delete picked[k]
  if (!id) return
  try {
    const res = await defaultsApi.copyPreview(props.wsId, id)
    if (sourceId.value !== id) return
    for (const item of res.items) picked[item.kind] = item.available && !item.same
    preview.value = res
  } catch (err) {
    error.value = errorText(err)
  }
}

watch(sourceId, load)
watch(
  () => props.open,
  (open) => {
    if (open) {
      sourceId.value = null
      load(null)
    }
  },
)

async function apply() {
  const kinds = chosen.value
  const access = preview.value.items.find((i) => i.kind === 'ai_access')
  if (kinds.includes('ai_access') && access?.after === 'Полный доступ') {
    const ok = await askConfirm('Перенести полный доступ ИИ? Он сможет менять бюджеты и ставки без вашего подтверждения.')
    if (!ok) return
  }
  busy.value = true
  error.value = ''
  try {
    const res = await defaultsApi.copyApply(props.wsId, sourceId.value, kinds)
    emit('applied', res.workspace)
    const skipped = res.skipped.map((s) => s.reason || s.label).join('; ')
    pushToast(skipped ? `Перенесено. Не перенесено: ${skipped}` : 'Настройки перенесены', 'ok')
    emit('close')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.cfp-pick {
  max-width: 420px;
}
.cfp-list {
  display: grid;
  margin-top: 8px;
}
.cfp-row {
  display: grid;
  gap: 4px;
  padding: 10px 0;
  border-top: 1px solid var(--line);
}
.cfp-row:first-child {
  border-top: 0;
}
.cfp-row.is-off {
  opacity: 0.7;
}
.cfp-note,
.cfp-diff {
  margin: 0 0 0 30px;
  color: var(--muted);
  font-size: 13px;
}
.cfp-diff {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  overflow-wrap: anywhere;
}
.cfp-before {
  text-decoration: line-through;
  text-decoration-color: var(--faint);
}
.cfp-after {
  color: var(--ink);
}
</style>
