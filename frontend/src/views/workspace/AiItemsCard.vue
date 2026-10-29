<template>
  <div class="action-card ai-ask ai-items" :class="`is-${action.status}`">
    <div class="action-card__head">
      <strong>{{ args.title || action.label }}</strong>
      <span class="ai-items__chips">
        <span v-if="stage" class="chip">этап: {{ stage }}</span>
        <span class="chip" :class="CHIPS[action.status]">{{ STATUS[action.status] || action.status }}</span>
      </span>
    </div>
    <p v-if="args.intro" class="ai-ask__lead">{{ args.intro }}</p>

    <AiCheckTable
      :off="pending ? off : decidedOff"
      :columns="columns"
      :rows="rows"
      :readonly="!pending"
      :disabled="disabled"
      @update:off="off = $event"
    />

    <template v-if="pending">
      <textarea
        v-model="comment"
        class="ai-input ai-input--area"
        rows="2"
        maxlength="2000"
        :disabled="disabled"
        placeholder="Что поправить или добавить — необязательно"
        aria-label="Комментарий к таблице"
      />
      <p class="hint">Снятые строки ИИ не возьмёт в работу. Следующий этап придёт новой таблицей.</p>
      <div class="action-card__buttons">
        <button class="btn btn--accent" type="button" :disabled="disabled || !chosen.length" @click="approve">
          Согласовать выбранные ({{ chosen.length }})
        </button>
        <button class="btn btn--ghost" type="button" :disabled="disabled" @click="reject">Отклонить этап</button>
      </div>
    </template>

    <template v-else>
      <p v-if="action.status === 'rejected' && action.error" class="hint">{{ action.error }}</p>
      <p v-if="result.comment" class="hint">Комментарий: {{ result.comment }}</p>
    </template>
  </div>
</template>

<script setup>
/** Таблица ИИ на согласование (propose_items): строки с галочками, снятое ИИ не использует. */
import { computed, ref } from 'vue'
import AiCheckTable from './AiCheckTable.vue'

const props = defineProps({
  action: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

const STAGES = {
  analysis: 'анализ',
  keywords: 'ключи и группы',
  negatives: 'минус-фразы',
  ads: 'заголовки и тексты',
  extras: 'ссылка и расширения',
  settings: 'настройки',
}
const STATUS = { pending: 'ждёт решения', done: 'согласовано', rejected: 'не согласовано' }
const CHIPS = { pending: 'chip--warning', done: 'chip--ok' }

const args = computed(() => props.action.args || {})
const result = computed(() => props.action.result || {})
const pending = computed(() => props.action.status === 'pending')
const stage = computed(() => STAGES[args.value.stage] || '')
const columns = computed(() => args.value.columns || [])
const rows = computed(() =>
  (args.value.rows || []).map((r) => ({ id: r.n, section: r.section, kind: r.kind, cells: r.cells || [] })),
)

const off = ref((args.value.rows || []).filter((r) => r.checked === false).map((r) => r.n))
const comment = ref('')
const chosen = computed(() => rows.value.filter((r) => !off.value.includes(r.id)).map((r) => r.id))

// Согласованная таблица: снятые строки зачёркнуты. Отклонённую видно по статусу карточки.
const decidedOff = computed(() => {
  if (props.action.status !== 'done') return []
  const approved = result.value.approved_rows || []
  return rows.value.filter((r) => !approved.includes(r.id)).map((r) => r.id)
})

function approve() {
  emit('submit', { approve: true, rows: chosen.value, comment: comment.value.trim() })
}

function reject() {
  emit('submit', { approve: false, rows: [], comment: comment.value.trim() })
}
</script>
