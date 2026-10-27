<template>
  <div class="action-card ai-ask ai-plan" :class="`is-${action.status}`">
    <div class="action-card__head">
      <strong>{{ action.label }}</strong>
      <span class="chip" :class="CHIPS[action.status]">{{ STATUS[action.status] || action.status }}</span>
    </div>
    <p v-if="args.goal" class="ai-ask__lead">{{ args.goal }}</p>

    <ol class="ai-plan__steps">
      <li v-for="s in steps" :key="s.n" class="ai-plan__step" :class="{ 'is-off': isDeclined(s) }">
        <label v-if="action.status === 'pending'" class="ai-plan__title">
          <input v-model="chosen" type="checkbox" :value="s.n" :disabled="disabled" />
          <strong>{{ s.n }}. {{ s.title }}</strong>
        </label>
        <div v-else class="ai-plan__title">
          <strong>{{ s.n }}. {{ s.title }}</strong>
          <span v-if="action.status === 'done'" class="chip" :class="{ 'chip--ok': !isDeclined(s) }">
            {{ isDeclined(s) ? 'не делаем' : 'согласован' }}
          </span>
        </div>
        <p v-if="s.before || s.after" class="ai-plan__diff">
          <span class="ai-plan__before">Было: {{ s.before || '—' }}</span>
          <span aria-hidden="true">→</span>
          <span class="ai-plan__after">Станет: {{ s.after || '—' }}</span>
        </p>
        <p v-if="s.why" class="hint">Зачем: {{ s.why }}</p>
        <ul v-if="progress[s.n]?.length" class="ai-plan__progress">
          <li v-for="p in progress[s.n]" :key="p.id">
            {{ p.label }} — <span :class="p.tone">{{ p.status }}</span>
          </li>
        </ul>
      </li>
    </ol>

    <template v-if="action.status === 'pending'">
      <textarea
        v-model="comment"
        class="ai-input ai-input--area"
        rows="2"
        maxlength="2000"
        :disabled="disabled"
        placeholder="Комментарий к плану — необязательно"
        aria-label="Комментарий к плану"
      />
      <p class="hint">Изменения по согласованным шагам придут отдельными карточками «было → станет».</p>
      <div class="action-card__buttons">
        <button class="btn btn--accent" type="button" :disabled="disabled || !chosen.length" @click="approve">
          Согласовать выбранные ({{ chosen.length }})
        </button>
        <button class="btn btn--ghost" type="button" :disabled="disabled" @click="reject">Отклонить план</button>
      </div>
    </template>

    <template v-else>
      <p v-if="action.status === 'rejected' && action.error" class="hint">{{ action.error }}</p>
      <p v-if="result.comment" class="hint">Комментарий: {{ result.comment }}</p>
    </template>
  </div>
</template>

<script setup>
/** Карточка плана ИИ: шаги «было → станет → зачем»; маркетолог отмечает, какие делать. */
import { computed, ref } from 'vue'

const props = defineProps({
  action: { type: Object, required: true },
  // {номер шага: [{id, label, status, tone}]} — действия ИИ по этому шагу.
  progress: { type: Object, default: () => ({}) },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

const STATUS = { pending: 'ждёт решения', done: 'согласован', rejected: 'не согласован' }
const CHIPS = { pending: 'chip--warning', done: 'chip--ok' }

const args = computed(() => props.action.args || {})
const steps = computed(() => args.value.steps || [])
const result = computed(() => props.action.result || {})
const chosen = ref(steps.value.map((s) => s.n))
const comment = ref('')

function isDeclined(step) {
  return props.action.status === 'done' && !(result.value.approved_steps || []).includes(step.n)
}

function approve() {
  emit('submit', { approve: true, steps: [...chosen.value].sort((a, b) => a - b), comment: comment.value.trim() })
}

function reject() {
  emit('submit', { approve: false, steps: [], comment: comment.value.trim() })
}
</script>
