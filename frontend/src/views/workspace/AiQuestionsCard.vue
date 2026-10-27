<template>
  <div class="action-card ai-ask" :class="`is-${action.status}`">
    <div class="action-card__head">
      <strong>{{ action.label }}</strong>
      <span class="chip" :class="CHIPS[action.status]">{{ STATUS[action.status] || action.status }}</span>
    </div>
    <p v-if="args.understanding" class="ai-ask__lead">{{ args.understanding }}</p>

    <details v-if="args.answered?.length" class="action-card__details">
      <summary>Уже знаю из прошлых ответов: {{ args.answered.length }}</summary>
      <ul class="ai-ask__list">
        <li v-for="f in args.answered" :key="f.key">
          <span class="ai-ask__q">{{ f.question }}</span>
          {{ f.answer }}
        </li>
      </ul>
    </details>

    <template v-if="action.status === 'pending'">
      <div v-for="q in questions" :key="q.n" class="ai-question">
        <p class="ai-question__title">{{ q.n }}. {{ q.question }}</p>
        <div v-if="q.options.length" class="ai-question__options" :role="q.multiple ? 'group' : 'radiogroup'">
          <button
            v-for="o in q.options"
            :key="o"
            type="button"
            class="ai-option"
            :class="{ 'is-on': isPicked(q, o) }"
            :role="q.multiple ? 'checkbox' : 'radio'"
            :aria-checked="isPicked(q, o)"
            :disabled="disabled"
            @click="toggle(q, o)"
          >
            {{ o }}
          </button>
        </div>
        <input
          v-model="texts[q.n]"
          class="ai-input"
          type="text"
          maxlength="1000"
          :disabled="disabled"
          :placeholder="q.options.length ? 'Свой вариант или уточнение' : 'Ваш ответ'"
          :aria-label="`Ответ на вопрос ${q.n}`"
        />
        <small v-if="q.multiple || q.remember" class="hint">
          {{ [q.multiple ? 'Можно выбрать несколько' : '', q.remember ? 'Запомню для этого клиента и больше не спрошу' : ''].filter(Boolean).join(' · ') }}
        </small>
      </div>
      <textarea
        v-model="comment"
        class="ai-input ai-input--area"
        rows="2"
        maxlength="2000"
        :disabled="disabled"
        placeholder="Комментарий для ИИ — необязательно"
        aria-label="Комментарий для ИИ"
      />
      <p v-if="hint" class="tone-bad">{{ hint }}</p>
      <div class="action-card__buttons">
        <button class="btn btn--accent" type="button" :disabled="disabled" @click="submit">Ответить</button>
        <button class="btn btn--ghost" type="button" :disabled="disabled" @click="skip">Пропустить</button>
      </div>
    </template>

    <template v-else-if="action.status === 'done'">
      <ul class="ai-ask__list">
        <li v-for="(a, i) in result.answers || []" :key="i">
          <span class="ai-ask__q">{{ a.question }}</span>
          <span v-if="a.skipped" class="hint">без ответа</span>
          <template v-else>
            {{ a.answer }}
            <span v-if="isRemembered(i)" class="chip">запомнено</span>
          </template>
        </li>
      </ul>
      <p v-if="result.comment" class="hint">Комментарий: {{ result.comment }}</p>
    </template>

    <template v-else>
      <p class="hint">{{ action.error || 'Без ответа' }}</p>
      <p v-if="result.comment" class="hint">Комментарий: {{ result.comment }}</p>
    </template>
  </div>
</template>

<script setup>
/** Карточка вопросов ИИ перед работой: варианты ответа и свой текст; ответы с key запоминаются для клиента. */
import { computed, reactive, ref } from 'vue'

const props = defineProps({
  action: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

const STATUS = { pending: 'ждёт ответа', done: 'отвечено', rejected: 'без ответа' }
const CHIPS = { pending: 'chip--warning', done: 'chip--ok' }

const args = computed(() => props.action.args || {})
const questions = computed(() => args.value.questions || [])
const result = computed(() => props.action.result || {})

const picked = reactive({})
const texts = reactive({})
const comment = ref('')
const hint = ref('')

function isPicked(q, option) {
  return (picked[q.n] || []).includes(option)
}

function toggle(q, option) {
  const current = picked[q.n] || []
  if (current.includes(option)) picked[q.n] = current.filter((o) => o !== option)
  else picked[q.n] = q.multiple ? [...current, option] : [option]
  hint.value = ''
}

// Ответы идут в том же порядке, что и вопросы карточки.
function isRemembered(index) {
  const key = questions.value[index]?.key
  return !!key && (result.value.remembered || []).includes(key)
}

function submit() {
  const answers = questions.value.map((q) => ({
    n: q.n,
    selected: picked[q.n] || [],
    text: (texts[q.n] || '').trim(),
  }))
  if (!comment.value.trim() && !answers.some((a) => a.selected.length || a.text)) {
    hint.value = 'Ответьте хотя бы на один вопрос или напишите комментарий'
    return
  }
  emit('submit', { answers, comment: comment.value.trim() })
}

function skip() {
  emit('submit', { skip: true, comment: comment.value.trim() })
}
</script>
