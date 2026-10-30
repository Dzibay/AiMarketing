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

    <div v-if="images.length" class="ai-items__images">
      <figure v-for="r in images" :key="r.id" class="ai-items__image" :class="{ 'is-off': isOff(r) }">
        <button
          type="button"
          class="ai-items__image-pick"
          :aria-pressed="!isOff(r)"
          :aria-label="r.cells[0] || 'Картинка'"
          :disabled="!pending || disabled"
          :title="[r.cells[1], r.cells[2]].filter(Boolean).join(' · ')"
          @click="flip(r)"
        >
          <img v-if="r.image?.preview_url" :src="r.image.preview_url" :alt="r.cells[0] || 'Картинка'" loading="lazy" />
          <span v-else class="ai-items__image-none">нет превью</span>
        </button>
        <figcaption>
          <small v-if="r.section">{{ r.section }}</small>
          {{ r.cells[0] }}
        </figcaption>
      </figure>
    </div>

    <AiCheckTable
      :off="pending ? off : decidedOff"
      :columns="columns"
      :rows="rows"
      :readonly="!pending"
      :disabled="disabled"
      @update:off="off = $event"
    />

    <div v-if="pending && previews.length" class="ai-items__previews">
      <p class="ai-items__previews-head">Так объявления увидят в поиске — меняются вместе с галочками</p>
      <div class="ai-items__preview-grid">
        <div v-for="p in previews" :key="p.section" class="ai-items__preview">
          <small v-if="p.section">{{ p.section }}</small>
          <AdPreview :titles="p.titles" :texts="p.texts" :site="site" />
        </div>
      </div>
    </div>

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
import AdPreview from './AdPreview.vue'
import AiCheckTable from './AiCheckTable.vue'

const props = defineProps({
  action: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
  // Сайт пространства — домен в предпросмотре объявлений.
  site: { type: String, default: '' },
})
const emit = defineEmits(['submit'])

const STAGES = {
  goals: 'цели кампании',
  analysis: 'анализ',
  keywords: 'ключи и группы',
  negatives: 'минус-фразы',
  audiences: 'аудитории',
  ads: 'заголовки и тексты',
  images: 'картинки',
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
  (args.value.rows || []).map((r) => ({ id: r.n, section: r.section, kind: r.kind, cells: r.cells || [], image: r.image })),
)
// Этап картинок: превью над таблицей, щелчок — снять или вернуть галочку.
const images = computed(() => (args.value.stage === 'images' ? rows.value.filter((r) => r.image) : []))

const off = ref((args.value.rows || []).filter((r) => r.checked === false).map((r) => r.n))
const comment = ref('')
const chosen = computed(() => rows.value.filter((r) => !off.value.includes(r.id)).map((r) => r.id))

// Этап объявлений: по группе — отмеченные заголовки и тексты в виде объявления.
const previews = computed(() => {
  if (args.value.stage !== 'ads') return []
  const bySection = new Map()
  for (const r of rows.value) {
    if ((r.kind !== 'title' && r.kind !== 'text') || off.value.includes(r.id)) continue
    const name = r.section || ''
    if (!bySection.has(name)) bySection.set(name, { section: name, titles: [], texts: [] })
    bySection.get(name)[`${r.kind}s`].push(r.cells[0] || '')
  }
  return [...bySection.values()]
})

// Согласованная таблица: снятые строки зачёркнуты. Отклонённую видно по статусу карточки.
const decidedOff = computed(() => {
  if (props.action.status !== 'done') return []
  const approved = result.value.approved_rows || []
  return rows.value.filter((r) => !approved.includes(r.id)).map((r) => r.id)
})

function isOff(row) {
  return (pending.value ? off.value : decidedOff.value).includes(row.id)
}

function flip(row) {
  off.value = off.value.includes(row.id) ? off.value.filter((n) => n !== row.id) : [...off.value, row.id]
}

function approve() {
  emit('submit', { approve: true, rows: chosen.value, comment: comment.value.trim() })
}

function reject() {
  emit('submit', { approve: false, rows: [], comment: comment.value.trim() })
}
</script>
