<template>
  <div>
  <section class="panel fb-summary">
    <div class="panel__head">
      <h2>Ответы «вниз»</h2>
      <UiSelect v-model="summaryPeriod" :options="SUMMARY_PERIODS" size="sm" aria-label="Период" @change="loadSummary()" />
    </div>
    <p v-if="!summary" class="empty">Загружаем…</p>
    <template v-else>
      <div class="fb-summary__main">
        <strong :class="downTone">{{ summaryTotal ? share(downShare) : '—' }}</strong>
        <span class="cell-note">
          {{ summaryTotal ? `«вниз» среди ${num(summaryTotal)} ${plural(summaryTotal, 'оценки', 'оценок', 'оценок')}` : 'оценок за период нет' }}
        </span>
      </div>
      <ul v-if="summary.models.length" class="fb-summary__models">
        <li v-for="m in summary.models" :key="m.model">
          <span>{{ m.model || 'без модели' }}</span>
          <strong :class="toneFor(m.down_share)">{{ share(m.down_share) }}</strong>
          <span class="cell-note">{{ num(m.down) }} из {{ num(m.total) }}</span>
        </li>
      </ul>
      <p class="hint">Больше трети «вниз» — жёлтым, повод разобраться; больше половины — красным.</p>
    </template>
  </section>

  <section class="panel">
    <div class="panel__head">
      <h2>Оценки ответов ИИ</h2>
    </div>
    <p class="hint">
      «Палец вверх / вниз» под ответами в чатах. Причины и комментарий пользователь оставляет у «вниз». Оценки удалённых
      чатов исчезают вместе с ними.
    </p>
    <div class="fb-filters">
      <UiSelect v-model="rating" :options="RATINGS" aria-label="Оценка" @change="load()" />
      <UiSelect v-model="model" :options="modelOptions" aria-label="Модель" @change="load()" />
      <UiSelect v-model="reason" :options="reasonOptions" aria-label="Причина" @change="load()" />
      <span v-if="data" class="fb-filters__total">{{ num(data.total) }} {{ plural(data.total, 'оценка', 'оценки', 'оценок') }}</span>
    </div>

    <p v-if="!data" class="empty">Загружаем…</p>
    <p v-else-if="!data.items.length" class="empty">Оценок с такими условиями нет.</p>
    <ul v-else class="fb-list">
      <li v-for="f in data.items" :key="f.id" class="fb-item">
        <div class="fb-item__head">
          <span class="fb-item__mark" :class="f.rating < 0 ? 'tone-bad' : 'tone-ok'">
            <ThumbsDown v-if="f.rating < 0" aria-hidden="true" />
            <ThumbsUp v-else aria-hidden="true" />
            {{ f.rating < 0 ? 'Плохо' : 'Хорошо' }}
          </span>
          <RouterLink v-if="f.user.id" :to="{ path: '/admin-panel/users', query: { user: f.user.id } }">
            {{ f.user.email || `#${f.user.id}` }}
          </RouterLink>
          <span v-else>{{ f.user.email }}</span>
          <span v-if="f.model" class="cell-note">{{ f.model }}</span>
          <time class="cell-note" :datetime="f.created_at">{{ formatDateTime(f.created_at) }}</time>
        </div>
        <div v-if="f.reasons.length" class="fb-item__reasons">
          <span v-for="r in f.reasons" :key="r.id" class="fb-chip">{{ r.label }}</span>
        </div>
        <p v-if="f.comment" class="fb-item__comment">«{{ f.comment }}»</p>
        <dl class="fb-item__qa">
          <template v-if="f.question">
            <dt>Вопрос</dt>
            <dd>{{ f.question }}</dd>
          </template>
          <dt>Ответ</dt>
          <dd>{{ f.answer }}</dd>
        </dl>
        <button class="btn btn--ghost btn--sm" type="button" @click="openChat(f)">
          <MessagesSquare aria-hidden="true" /> Читать чат
        </button>
      </li>
    </ul>
    <button v-if="data && data.items.length < data.total" class="btn btn--ghost" type="button" :disabled="busy" @click="load(true)">
      Показать ещё
    </button>

    <AdminChatModal :chat="reading" :highlight="readingMessage" @close="reading = null" />
  </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { MessagesSquare, ThumbsDown, ThumbsUp } from '@lucide/vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import AdminChatModal from './AdminChatModal.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, num, plural } from '../../utils/format'

/** Оценки ответов ИИ: фильтр по оценке, модели и причине; чат целиком — на чтение в окне. */
const RATINGS = [
  { value: -1, label: 'Только «вниз»' },
  { value: 1, label: 'Только «вверх»' },
  { value: 0, label: 'Все оценки' },
]
const PAGE = 50

const data = ref(null)
const busy = ref(false)
const rating = ref(-1)
const model = ref('')
const reason = ref('')
const reading = ref(null)
const readingMessage = ref(null)

const modelOptions = computed(() => [
  { value: '', label: 'Все модели' },
  ...(data.value?.models || []).map((m) => ({ value: m, label: m })),
])
const reasonOptions = computed(() => [
  { value: '', label: 'Все причины' },
  ...(data.value?.reasons || []).map((r) => ({ value: r.id, label: r.label })),
])

async function load(more = false) {
  busy.value = true
  try {
    const res = await adminApi.feedback({
      rating: rating.value,
      model: model.value,
      reason: reason.value,
      limit: PAGE,
      offset: more ? data.value.items.length : 0,
    })
    data.value = more ? { ...res, items: [...data.value.items, ...res.items] } : res
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function openChat(f) {
  try {
    reading.value = await adminApi.chat(f.chat_id)
    readingMessage.value = f.message_id
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

// Доля «вниз» за период по моделям (раньше была карточкой на странице расходов).
const SUMMARY_PERIODS = [
  { value: '7d', label: '7 дней' },
  { value: '30d', label: '30 дней' },
  { value: '90d', label: '90 дней' },
  { value: 'all', label: 'Всё время' },
]
const summaryPeriod = ref('30d')
const summary = ref(null)
const summaryTotal = computed(() => (summary.value?.models || []).reduce((s, m) => s + m.total, 0))
const downShare = computed(() =>
  summaryTotal.value ? summary.value.models.reduce((s, m) => s + m.down, 0) / summaryTotal.value : 0,
)
// Больше трети «вниз» — повод разобраться: жёлтым; больше половины — красным.
const toneFor = (v) => (v > 0.5 ? 'tone-bad' : v > 0.33 ? 'tone-warn' : 'tone-ok')
const downTone = computed(() => (summaryTotal.value ? toneFor(downShare.value) : ''))
const share = (v) => `${Math.round(v * 100)}%`

async function loadSummary() {
  try {
    summary.value = await adminApi.feedbackSummary(summaryPeriod.value)
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

onMounted(() => {
  load()
  loadSummary()
})
</script>

<style scoped>
.fb-summary {
  margin-bottom: 16px;
}
.fb-summary__main {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 12px;
}
.fb-summary__main strong {
  font-size: 28px;
  font-weight: 800;
  letter-spacing: -0.03em;
}
.fb-summary__models {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 20px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  font-size: 14px;
}
.fb-summary__models li {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.fb-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  margin-bottom: 16px;
}
.fb-filters__total {
  color: var(--muted);
  font-size: 13px;
}
.fb-list {
  display: grid;
  gap: 12px;
  margin: 0 0 16px;
  padding: 0;
  list-style: none;
}
.fb-item {
  display: grid;
  gap: 8px;
  justify-items: start;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: 14px;
}
.fb-item__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  font-size: 14px;
}
.fb-item__mark {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-weight: 700;
}
.fb-item__mark svg {
  width: 15px;
  height: 15px;
}
.fb-item__reasons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.fb-chip {
  padding: 2px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 12px;
}
.fb-item__comment {
  margin: 0;
  font-size: 14px;
}
.fb-item__qa {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 0;
  font-size: 13px;
}
.fb-item__qa dt {
  color: var(--muted);
}
.fb-item__qa dd {
  margin: 0;
  overflow-wrap: anywhere;
}
@media (max-width: 640px) {
  .fb-item__qa {
    grid-template-columns: 1fr;
  }
}
</style>
