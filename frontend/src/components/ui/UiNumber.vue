<template>
  <span class="ui-num" :class="{ 'has-suffix': suffix, 'is-disabled': disabled }">
    <input
      ref="input"
      class="ui-num__input"
      type="text"
      :inputmode="digits ? 'decimal' : 'numeric'"
      autocomplete="off"
      :value="text"
      :placeholder="placeholder"
      :disabled="disabled"
      :aria-label="ariaLabel || undefined"
      @input="onInput"
      @blur="onBlur"
    />
    <span v-if="suffix" class="ui-num__suffix" aria-hidden="true">{{ suffix }}</span>
  </span>
</template>

<script setup>
import { ref, watch } from 'vue'

/**
 * Поле для денег и чисел: разряды прямо при вводе («150 000»), суффикс «₽» или «%», цифры по правому краю.
 * Значение наружу — число или null (пустое поле). digits — сколько знаков после запятой можно ввести;
 * точку и запятую принимает одинаково.
 */
const props = defineProps({
  modelValue: { type: [Number, String, null], default: null },
  digits: { type: Number, default: 0 },
  suffix: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const NBSP = '\u00a0'
const input = ref(null)

function group(intPart) {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP)
}

/** Число → текст поля: «150 000», «4,5». */
function format(value) {
  if (value === null || value === undefined || value === '') return ''
  const n = Number(value)
  if (!Number.isFinite(n)) return ''
  const [i, f] = String(Math.abs(n)).split('.')
  const frac = props.digits && f ? `,${f.slice(0, props.digits)}` : ''
  return `${n < 0 ? '−' : ''}${group(i)}${frac}`
}

/** Текст из поля → { text, value }: оставляем цифры и одну запятую, переставляем пробелы-разряды. */
function normalize(raw) {
  let s = raw.replace(/\./g, ',').replace(/[^\d,]/g, '')
  const comma = s.indexOf(',')
  let intPart = comma >= 0 ? s.slice(0, comma) : s
  let frac = comma >= 0 ? s.slice(comma + 1).replace(/,/g, '') : null
  if (!props.digits) frac = null
  else if (frac !== null) frac = frac.slice(0, props.digits)
  intPart = intPart.replace(/^0+(?=\d)/, '')
  if (!intPart && frac !== null) intPart = '0'
  const text = intPart || frac !== null ? `${group(intPart)}${frac !== null ? `,${frac}` : ''}` : ''
  const value = intPart || frac ? Number(`${intPart || '0'}.${frac || '0'}`) : null
  return { text, value }
}

const text = ref(format(props.modelValue))

// Значение поменяли снаружи (сброс формы) — показываем его, если оно не совпадает с тем, что уже набрано.
watch(
  () => props.modelValue,
  (value) => {
    const typed = normalize(text.value).value
    const next = value === '' || value == null ? null : Number(value)
    if (typed !== next) text.value = format(value)
  },
)

function onInput(event) {
  const el = event.target
  const raw = el.value
  const caret = el.selectionStart ?? raw.length
  // Сколько значимых символов (цифр и запятой) стояло до курсора — после перестановки пробелов ставим курсор туда же.
  const before = raw.slice(0, caret).replace(/\./g, ',').replace(/[^\d,]/g, '').length
  const { text: next, value } = normalize(raw)
  text.value = next
  el.value = next
  let pos = 0
  for (let seen = 0; pos < next.length && seen < before; pos += 1) {
    if (/[\d,]/.test(next[pos])) seen += 1
  }
  el.setSelectionRange(pos, pos)
  emit('update:modelValue', value)
}

// Запятая без дробной части («150,») при уходе из поля убирается.
function onBlur() {
  text.value = format(normalize(text.value).value)
}

defineExpose({ focus: () => input.value?.focus() })
</script>
