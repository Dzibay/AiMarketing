<template>
  <span class="ui-select" :class="[`ui-select--${size}`, { 'is-open': open, 'is-block': block }]">
    <button
      ref="trigger"
      class="ui-select__btn"
      type="button"
      data-field-control
      :aria-label="ariaLabel || undefined"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :disabled="disabled"
      @click="toggleList"
      @keydown="onTriggerKey"
    >
      <span class="ui-select__value" :class="{ 'is-placeholder': !current }">{{ current?.label ?? placeholder }}</span>
      <ChevronDown class="ui-select__chev" aria-hidden="true" />
    </button>
    <Teleport to="body">
      <ul
        v-if="open"
        :id="listId"
        ref="panel"
        class="ui-pop ui-select__list"
        role="listbox"
        tabindex="-1"
        :aria-label="ariaLabel || undefined"
        :aria-activedescendant="active >= 0 ? `${listId}-${active}` : undefined"
        :style="style"
        @keydown="onListKey"
      >
        <li
          v-for="(o, i) in options"
          :id="`${listId}-${i}`"
          :key="i"
          class="ui-select__opt"
          :class="{ 'is-active': i === active, 'is-selected': same(o.value, modelValue), 'is-disabled': o.disabled }"
          role="option"
          :aria-selected="same(o.value, modelValue)"
          :aria-disabled="o.disabled || undefined"
          @mousemove="o.disabled || (active = i)"
          @click="pick(o)"
        >
          <span class="ui-select__opt-text">
            {{ o.label }}
            <small v-if="o.hint">{{ o.hint }}</small>
          </span>
          <Check v-if="same(o.value, modelValue)" class="ui-select__tick" aria-hidden="true" />
        </li>
      </ul>
    </Teleport>
  </span>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { Check, ChevronDown } from '@lucide/vue'
import { usePopover } from '../../composables/usePopover'

/**
 * Выпадающий список вместо нативного select: тот же вид в любой теме и браузере, подсказка у пункта.
 * options — [{ value, label, hint?, disabled? }]. Внутри .field подпись связывается с кнопкой через v-field.
 * Клавиатура: ↓/↑ открывают и выбирают, Enter/пробел — выбрать, Esc — закрыть, первая буква — переход к пункту.
 */
const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: null },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'Выберите' },
  ariaLabel: { type: String, default: '' },
  size: { type: String, default: 'md' },
  block: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'change'])

const listId = `ui-select-${Math.random().toString(36).slice(2, 10)}`
const trigger = ref(null)
const panel = ref(null)
const active = ref(-1)
const { open, style, show, close } = usePopover(trigger, panel, { align: 'start', matchWidth: true })

const same = (a, b) => a === b || (a == null && b == null)
const current = computed(() => props.options.find((o) => same(o.value, props.modelValue)) || null)

function enabledFrom(start, step) {
  const n = props.options.length
  for (let k = 0, i = start; k < n; k += 1, i = (i + step + n) % n) {
    if (!props.options[i]?.disabled) return i
  }
  return -1
}

function scrollToActive() {
  panel.value?.querySelector(`#${listId}-${active.value}`)?.scrollIntoView({ block: 'nearest' })
}

async function openList() {
  const at = props.options.findIndex((o) => same(o.value, props.modelValue))
  active.value = at >= 0 ? at : enabledFrom(0, 1)
  await show()
  await nextTick()
  panel.value?.focus()
  scrollToActive()
}

function toggleList() {
  if (open.value) close()
  else openList()
}

function pick(o) {
  if (!o || o.disabled) return
  close({ focus: true })
  if (same(o.value, props.modelValue)) return
  emit('update:modelValue', o.value)
  emit('change', o.value)
}

function move(step) {
  const n = props.options.length
  if (!n) return
  active.value = enabledFrom((active.value + step + n) % n, step)
  scrollToActive()
}

// Переход к пункту по первой букве.
function jump(letter) {
  const n = props.options.length
  const l = letter.toLowerCase()
  for (let k = 1; k <= n; k += 1) {
    const i = (active.value + k) % n
    const o = props.options[i]
    if (!o.disabled && String(o.label).toLowerCase().startsWith(l)) {
      active.value = i
      scrollToActive()
      return
    }
  }
}

function onTriggerKey(event) {
  if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
    event.preventDefault()
    openList()
  }
}

function onListKey(event) {
  const key = event.key
  if (key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    close({ focus: true })
  } else if (key === 'ArrowDown' || key === 'ArrowUp') {
    event.preventDefault()
    move(key === 'ArrowDown' ? 1 : -1)
  } else if (key === 'Home' || key === 'End') {
    event.preventDefault()
    active.value = key === 'Home' ? enabledFrom(0, 1) : enabledFrom(props.options.length - 1, -1)
    scrollToActive()
  } else if (key === 'Enter' || key === ' ') {
    event.preventDefault()
    pick(props.options[active.value])
  } else if (key === 'Tab') {
    close()
  } else if (key.length === 1 && /\S/.test(key)) {
    jump(key)
  }
}
</script>
