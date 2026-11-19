<template>
  <form class="rate-form redo-form" @submit.prevent="submit">
    <p class="rate-form__title">Что сделать иначе?</p>
    <div class="rate-form__reasons" role="group" aria-label="Причина">
      <button
        v-for="r in REASONS"
        :key="r"
        class="rate-form__reason"
        :class="{ 'is-on': reason === r }"
        type="button"
        :aria-pressed="reason === r"
        :disabled="busy"
        @click="reason = reason === r ? '' : r"
      >
        {{ r }}
      </button>
    </div>
    <textarea
      v-model="comment"
      class="rate-form__comment"
      rows="2"
      maxlength="1000"
      placeholder="Как надо — по желанию. Например: поднимай ставки не больше чем на 10 %"
      aria-label="Как надо"
      :disabled="busy"
    />
    <p v-if="reason === NO_TOUCH" class="hint redo-form__note">ИИ запомнит это для проекта и не будет предлагать изменения в этой кампании.</p>
    <div class="rate-form__acts">
      <button class="btn btn--accent btn--sm" type="submit" :disabled="busy || !ready">
        {{ busy ? 'Отправляем…' : 'Отправить' }}
      </button>
      <button class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="emit('cancel')">Отмена</button>
    </div>
  </form>
</template>

<script setup>
import { computed, ref } from 'vue'

/**
 * «Сделать иначе» у предложения ИИ: быстрая причина и «Как надо». Отправка отклоняет предложение с этой
 * причиной — ИИ сразу переделает. Список причин совпадает с ai_agent.DECLINE_REASONS.
 */
const REASONS = ['Не то', 'Слишком резко', 'Не трогай эту кампанию', 'Сейчас не время']
const NO_TOUCH = 'Не трогай эту кампанию'

defineProps({ busy: { type: Boolean, default: false } })
const emit = defineEmits(['submit', 'cancel'])

const reason = ref('')
const comment = ref('')
const ready = computed(() => !!reason.value || !!comment.value.trim())

function submit() {
  if (!ready.value) return
  emit('submit', { reason: reason.value, comment: comment.value.trim() })
}
</script>

<style scoped>
.redo-form {
  margin-top: 10px;
}
.redo-form__note {
  margin: 0;
}
</style>
