<template>
  <form class="composer" :class="{ 'composer--big': showIntro, 'is-off': inputOff }" @submit.prevent="send()">
    <div v-if="picker.open" id="mention-list" class="menu-pop mention-pop" role="listbox" aria-label="Проекты">
      <button
        v-for="(w, i) in picker.items"
        :id="`mention-${w.id}`"
        :key="w.id"
        type="button"
        class="menu-item mention-pop__item"
        :class="{ 'is-active': i === picker.index }"
        role="option"
        :aria-selected="i === picker.index"
        @mousedown.prevent="pickProject(w)"
        @mousemove="picker.index = i"
      >
        <strong>{{ niceQuotes(w.name) }}</strong>
        <small v-if="w.site">{{ w.site }}</small>
      </button>
    </div>
    <textarea
      ref="input"
      v-model="draft"
      class="composer__input"
      rows="1"
      maxlength="8000"
      aria-label="Сообщение ИИ"
      :aria-controls="picker.open ? 'mention-list' : null"
      :aria-activedescendant="picker.open && picker.items[picker.index] ? `mention-${picker.items[picker.index].id}` : null"
      :disabled="inputOff"
      :placeholder="placeholder"
      @input="updatePicker"
      @click="updatePicker"
      @keyup.left="updatePicker"
      @keyup.right="updatePicker"
      @blur="closePicker"
      @keydown="onKeydown"
    />
    <div class="composer__row">
      <button
        v-if="otherProjects.length"
        class="composer__icon"
        type="button"
        v-tip="general ? 'Указать проект' : 'Сослаться на другой проект'"
        :aria-label="general ? 'Указать проект' : 'Сослаться на другой проект'"
        :disabled="inputOff"
        @mousedown.prevent
        @click="insertAt"
      >
        <AtSign aria-hidden="true" />
      </button>
      <UiSelect
        v-if="modelItems.length > 1"
        class="composer__model"
        size="sm"
        :model-value="selectedClass"
        :options="modelOptions"
        :disabled="modelBusy || inputOff"
        aria-label="Модель ответа"
        v-tip="'Модель ответа'"
        @change="chooseModel"
      />
      <span class="composer__tip">{{ composerTip }}</span>
      <button
        class="composer__send"
        type="submit"
        :disabled="!canSend"
        :aria-label="busy ? 'ИИ отвечает' : 'Отправить'"
        v-tip="busy ? 'ИИ отвечает' : 'Отправить'"
      >
        <span v-if="busy" class="spinner" aria-hidden="true" />
        <ArrowUp v-else aria-hidden="true" />
      </button>
    </div>
  </form>
</template>

<script setup>
import { ArrowUp, AtSign } from '@lucide/vue'
import { computed, nextTick, reactive, watch } from 'vue'
import { useChat } from '../../composables/useChat'
import { niceQuotes } from '../../utils/format'
import UiSelect from '../ui/UiSelect.vue'

/** Поле ввода чата: текст, @-упоминание другого проекта, модель ответа и кнопка отправки. */

// Подсказка к классу модели в списке «Модель ответа».
const MODEL_HINTS = {
  1: 'Быстрые ответы, расходует меньше лимита',
  2: 'Для большинства задач',
  3: 'Думает дольше, расходует лимит быстрее',
}
// Поле ввода растёт с текстом до ~8 строк, дальше — своя прокрутка.
const INPUT_MAX = 200

const {
  general,
  draft,
  mentions,
  otherProjects,
  input,
  status,
  aiConfigured,
  sending,
  showIntro,
  pendingActions,
  pendingAsks,
  pendingWrites,
  onBoard,
  modelItems,
  selectedClass,
  modelBusy,
  chooseModel,
  send,
} = useChat()

const picker = reactive({ open: false, index: 0, items: [], start: 0, end: 0 })

const modelOptions = computed(() =>
  modelItems.value.map((m) => ({ value: m.id, label: m.name, hint: MODEL_HINTS[m.dots] || '' })),
)

const placeholder = computed(() => {
  if (!aiConfigured.value) return 'ИИ временно недоступен'
  if (status.value === 'running') return 'ИИ отвечает…'
  if (pendingActions.value.length && pendingActions.value.every((a) => onBoard.value.has(a.id))) {
    return 'Решите этап на доске или напишите своими словами'
  }
  if (pendingAsks.value.length) {
    return pendingWrites.value.length
      ? 'Ответьте в карточке; сообщение отклонит предложения'
      : 'Ответьте в карточке или напишите своими словами'
  }
  if (pendingWrites.value.length) return 'Новое сообщение отклонит ждущие предложения'
  return showIntro.value ? 'Спросите или поручите задачу…' : 'Уточните или попросите что-то ещё…'
})
const composerTip = computed(
  () => `Enter — отправить · Shift+Enter — перенос${otherProjects.value.length ? ' · @ — проект' : ''}`,
)
// Пока ИИ отвечает, писать нельзя: остановить ход нечем, новое сообщение ушло бы в середину ответа.
const inputOff = computed(() => status.value === 'running' || !aiConfigured.value)
const busy = computed(() => sending.value || status.value === 'running')
const canSend = computed(
  () => !!draft.value.trim() && !sending.value && status.value !== 'running' && aiConfigured.value,
)

/** Высота поля по тексту. Страница зовёт её после замера своей высоты и при изменении окна. */
function autosize() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  // Пустое поле меряем по подсказке: длинная подсказка на узком экране иначе обрезается на первой строке.
  const empty = !el.value
  if (empty) el.value = el.placeholder
  const height = el.scrollHeight
  if (empty) el.value = ''
  el.style.height = `${Math.min(height, INPUT_MAX)}px`
  el.style.overflowY = height > INPUT_MAX ? 'auto' : 'hidden'
}

// --- @-упоминания проектов --------------------------------------------------------------------

function closePicker() {
  picker.open = false
}

/** Список проектов, пока перед курсором стоит «@запрос» (в начале строки или после пробела). */
function updatePicker() {
  const el = input.value
  if (!el || !otherProjects.value.length) return closePicker()
  const caret = el.selectionStart ?? 0
  const before = el.value.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at === -1 || (at > 0 && !/\s/.test(before[at - 1]))) return closePicker()
  const query = before.slice(at + 1)
  if (query.length > 60 || /^\s|\n/.test(query)) return closePicker()
  const q = query.toLowerCase()
  const items = otherProjects.value.filter((w) => `${w.name} ${w.site || ''}`.toLowerCase().includes(q))
  if (!items.length) return closePicker()
  Object.assign(picker, { open: true, items, start: at, end: caret, index: Math.min(picker.index, items.length - 1) })
}

function pickProject(w) {
  const el = input.value
  const insert = `@${w.name} `
  draft.value = draft.value.slice(0, picker.start) + insert + draft.value.slice(picker.end)
  if (!mentions.value.some((m) => m.id === w.id)) mentions.value.push({ id: w.id, name: w.name })
  const caret = picker.start + insert.length
  picker.index = 0
  closePicker()
  nextTick(() => {
    el?.focus()
    el?.setSelectionRange(caret, caret)
  })
}

/** Кнопка «@»: ставит @ в курсор и открывает список — для тех, кто не знает про клавишу, и для телефона. */
function insertAt() {
  const el = input.value
  if (!el) return
  const pos = el.selectionStart ?? draft.value.length
  const before = draft.value.slice(0, pos)
  const lead = before && !/\s$/.test(before) ? ' ' : ''
  draft.value = `${before}${lead}@${draft.value.slice(el.selectionEnd ?? pos)}`
  const caret = pos + lead.length + 1
  nextTick(() => {
    el.focus()
    el.setSelectionRange(caret, caret)
    updatePicker()
  })
}

function onKeydown(e) {
  if (picker.open) {
    const last = picker.items.length - 1
    if (e.key === 'ArrowDown') picker.index = picker.index >= last ? 0 : picker.index + 1
    else if (e.key === 'ArrowUp') picker.index = picker.index <= 0 ? last : picker.index - 1
    else if (e.key === 'Enter' || e.key === 'Tab') pickProject(picker.items[picker.index])
    else if (e.key === 'Escape') closePicker()
    else return
    e.preventDefault()
    return
  }
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
    e.preventDefault()
    send()
  }
}

watch(
  () => picker.index,
  () => nextTick(() => document.querySelector('.mention-pop__item.is-active')?.scrollIntoView({ block: 'nearest' })),
)

watch([draft, showIntro, placeholder], () => nextTick(autosize))

defineExpose({ autosize })
</script>
