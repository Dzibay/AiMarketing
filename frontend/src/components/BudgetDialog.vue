<template>
  <Modal :open="open" title="Изменить бюджет" :locked="busy" @close="close">
    <p v-if="loading" class="field-hint">Загружаем бюджет…</p>
    <p v-else-if="loadError" class="form-error">{{ loadError }}</p>
    <template v-else-if="data">
      <p class="bd__lead">
        <b>«{{ data.name || name }}»</b>
        <template v-if="data.strategy_label"> · {{ data.strategy_label }}</template>
      </p>
      <template v-if="data.editable">
        <div v-field class="field bd__field">
          <label>{{ weekly ? 'Сколько тратить в неделю' : 'Сколько тратить в день' }}</label>
          <UiNumber v-model="amount" suffix="₽" :disabled="busy" :aria-label="weekly ? 'Бюджет в неделю' : 'Бюджет в день'" />
          <small class="field-hint">
            {{
              weekly
                ? 'Директ не потратит больше за неделю — с понедельника по воскресенье.'
                : 'Директ не потратит больше за день.'
            }}
            Меньше {{ rubles(data.min_rub) }} он не примет.
          </small>
        </div>
        <p v-if="changed" class="bd__diff">
          Было <b>{{ before }}</b> → станет <b>{{ after }}</b>
        </p>
        <p v-if="tooSmall" class="field-hint tone-bad">Меньше {{ rubles(data.min_rub) }} Директ не примет.</p>
        <p v-if="error" class="form-error">{{ error }}</p>
        <p class="field-hint">Изменение появится в «Истории» — его можно вернуть.</p>
      </template>
      <p v-else class="field-hint">
        Бюджет этой кампании меняется только в Директе: сервис не правит такую стратегию или тип кампании.
      </p>
    </template>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="close">Отмена</button>
      <button
        v-if="data?.editable"
        class="btn btn--accent"
        type="button"
        :disabled="busy || !ready"
        @click="save"
      >
        {{ busy ? 'Сохраняем…' : 'Сохранить' }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import Modal from './ui/Modal.vue'
import UiNumber from './ui/UiNumber.vue'
import { errorText, wsApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { undoAction } from '../composables/useUndo'
import { rubles } from '../utils/format'

/**
 * Бюджет одной кампании — одним числом, в той единице, в которой он живёт в Директе
 * (автостратегии и ЕПК — в неделю, ручные ставки ТГО — в день). После сохранения — тост
 * с «Отменить», как у других ручных правок: откат той же записи в «Истории».
 */
const props = defineProps({
  open: { type: Boolean, default: false },
  workspaceId: { type: [Number, String], default: null },
  campaignId: { type: [Number, String], default: null },
  name: { type: String, default: '' },
  /** Уже загруженный бюджет (GET …/budget) — чтобы не ходить второй раз. */
  info: { type: Object, default: null },
})
const emit = defineEmits(['close', 'saved'])

const data = ref(null)
const amount = ref(null)
const loading = ref(false)
const loadError = ref('')
const busy = ref(false)
const error = ref('')

const weekly = computed(() => data.value?.kind === 'weekly')
const unit = computed(() => (weekly.value ? 'в неделю' : 'в день'))
const current = computed(() => data.value?.amount_rub ?? null)
const value = computed(() => (amount.value === null || amount.value === '' ? null : Number(amount.value)))
const tooSmall = computed(() => value.value !== null && value.value < (data.value?.min_rub || 0))
const changed = computed(() => value.value !== null && value.value !== current.value)
const ready = computed(() => changed.value && !tooSmall.value)
const before = computed(() => (current.value ? `${rubles(current.value)} ${unit.value}` : 'без ограничения'))
const after = computed(() => `${rubles(value.value)} ${unit.value}`)

watch(
  () => [props.open, props.campaignId],
  ([open]) => {
    if (open) start()
  },
  { immediate: true },
)

async function start() {
  error.value = ''
  loadError.value = ''
  if (props.info && Number(props.info.campaign_row_id) === Number(props.campaignId)) {
    fill(props.info)
    return
  }
  data.value = null
  loading.value = true
  try {
    fill(await wsApi.campaignBudget(props.workspaceId, props.campaignId))
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

function fill(info) {
  data.value = info
  amount.value = info?.amount_rub ?? null
}

function close() {
  if (!busy.value) emit('close')
}

async function save() {
  if (!ready.value) return
  busy.value = true
  error.value = ''
  const payload = weekly.value ? { weekly_budget_rub: value.value } : { daily_budget_rub: value.value }
  try {
    const res = await wsApi.setCampaignBudget(props.workspaceId, props.campaignId, payload)
    const item = res.item || {}
    const undo = undoAction(res, () => emit('saved', null))
    pushToast(
      res.changed ? `Бюджет «${item.name || data.value?.name || props.name}»: ${before.value} → ${after.value}` : 'Бюджет не изменился',
      'ok',
      undo,
    )
    emit('saved', res.info || null)
    emit('close')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.bd__lead {
  margin: 0 0 12px;
}
.bd__field {
  margin-bottom: 8px;
}
.bd__diff {
  margin: 4px 0 8px;
}
</style>
