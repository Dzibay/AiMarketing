<template>
  <div class="rate">
    <div class="rate__btns" role="group" aria-label="Оценка ответа">
      <button
        class="rate__btn"
        :class="{ 'is-on': rating === 1 }"
        type="button"
        :aria-pressed="rating === 1"
        aria-label="Хороший ответ"
        v-tip="'Хороший ответ'"
        :disabled="busy"
        @click="toggleUp"
      >
        <ThumbsUp aria-hidden="true" />
      </button>
      <button
        class="rate__btn"
        :class="{ 'is-on': rating === -1 }"
        type="button"
        :aria-pressed="rating === -1"
        :aria-expanded="open"
        aria-label="Плохой ответ"
        v-tip="'Плохой ответ'"
        :disabled="busy"
        @click="down"
      >
        <ThumbsDown aria-hidden="true" />
      </button>
      <span v-if="thanks" class="rate__thanks" role="status">Спасибо, учтём</span>
    </div>

    <form v-if="open" class="rate-form" @submit.prevent="submit">
      <p class="rate-form__title">Что не так с ответом?</p>
      <div class="rate-form__reasons" role="group" aria-label="Причины">
        <button
          v-for="r in REASONS"
          :key="r.id"
          class="rate-form__reason"
          :class="{ 'is-on': picked.has(r.id) }"
          type="button"
          :aria-pressed="picked.has(r.id)"
          @click="flip(r.id)"
        >
          {{ r.label }}
        </button>
      </div>
      <textarea
        v-model="comment"
        class="rate-form__comment"
        rows="2"
        maxlength="1000"
        placeholder="Комментарий — по желанию"
        aria-label="Комментарий к оценке"
      />
      <div class="rate-form__acts">
        <button class="btn btn--accent btn--sm" type="submit" :disabled="busy">Отправить</button>
        <button class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="open = false">Закрыть</button>
        <button class="rate-form__clear" type="button" :disabled="busy" @click="clear">Убрать оценку</button>
      </div>
    </form>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { ThumbsDown, ThumbsUp } from '@lucide/vue'
import { errorText } from '../../api/web'
import { useChat } from '../../composables/useChat'
import { pushToast } from '../../composables/useToasts'

/**
 * «Палец вверх / вниз» под ответом ИИ. «Вниз» сохраняется сразу, а форма причин и комментария
 * уточняет оценку — закрыть форму без отправки можно, голос уже учтён. Повторное нажатие «вверх» снимает оценку.
 */
const REASONS = [
  { id: 'wrong_data', label: 'Неверные данные' },
  { id: 'not_asked', label: 'Не то, что просил' },
  { id: 'unclear', label: 'Непонятно' },
  { id: 'too_long', label: 'Слишком длинно' },
  { id: 'other', label: 'Другое' },
]

const props = defineProps({
  // id сообщения ИИ и текущая оценка { rating, reasons, comment } или null.
  messageId: { type: Number, required: true },
  feedback: { type: Object, default: null },
})

const { rateMessage } = useChat()
const busy = ref(false)
const open = ref(false)
const thanks = ref(false)
const picked = ref(new Set())
const comment = ref('')
let thanksTimer = null

const rating = computed(() => props.feedback?.rating || 0)

async function save(payload) {
  busy.value = true
  try {
    await rateMessage(props.messageId, payload)
    return true
  } catch (e) {
    pushToast(errorText(e), 'error')
    return false
  } finally {
    busy.value = false
  }
}

function sayThanks() {
  thanks.value = true
  clearTimeout(thanksTimer)
  thanksTimer = setTimeout(() => (thanks.value = false), 2500)
}

async function toggleUp() {
  open.value = false
  if (await save({ rating: rating.value === 1 ? 0 : 1 }) && rating.value === 1) sayThanks()
}

async function down() {
  if (open.value) {
    open.value = false
    return
  }
  picked.value = new Set(props.feedback?.rating === -1 ? props.feedback.reasons : [])
  comment.value = props.feedback?.rating === -1 ? props.feedback.comment : ''
  if (rating.value !== -1 && !(await save({ rating: -1 }))) return
  open.value = true
}

function flip(id) {
  const next = new Set(picked.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  picked.value = next
}

async function submit() {
  if (await save({ rating: -1, reasons: [...picked.value], comment: comment.value })) {
    open.value = false
    sayThanks()
  }
}

async function clear() {
  if (await save({ rating: 0 })) open.value = false
}

onBeforeUnmount(() => clearTimeout(thanksTimer))
</script>
