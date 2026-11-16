<template>
  <span class="term-hint" :class="{ 'is-open': shown }">
    <!-- span, а не button: подсказка стоит и внутри <label>, где кнопка перехватила бы связь подписи с полем. -->
    <span
      ref="btn"
      class="term-hint__btn"
      role="button"
      tabindex="0"
      :aria-label="`Что значит «${entry.title}»`"
      :aria-expanded="shown"
      @click.prevent="toggle"
      @keydown.enter.prevent="toggle"
      @keydown.space.prevent="toggle"
      @pointerenter="onEnter"
      @pointerleave="onLeave"
      @focus="show(false)"
      @blur="hideTip(btn)"
    >?</span>
  </span>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { hideTip, showTip, tip } from '../composables/useTooltip'
import { TERMS } from '../utils/glossary'

/**
 * Подсказка «?» к термину рекламы: наведение, фокус или нажатие (на телефоне) открывают пояснение.
 * Пояснение рисует общий TooltipHost; нажатие закрепляет его до нажатия мимо.
 * term — из словаря ниже; title и text — своё пояснение к месту (например, как считается период).
 */
const props = defineProps({
  term: { type: String, default: '' },
  title: { type: String, default: '' },
  text: { type: String, default: '' },
})

const entry = computed(() =>
  props.text ? { title: props.title, text: props.text } : TERMS[props.term] || { title: props.title || props.term, text: '' },
)
const btn = ref(null)
const shown = computed(() => !!btn.value && tip.el === btn.value)

function show(pinned) {
  if (!btn.value || !entry.value.text) return
  showTip(btn.value, { title: entry.value.title, text: entry.value.text, pinned })
}

function toggle() {
  if (shown.value && tip.pinned) hideTip(btn.value)
  else show(true)
}

function onEnter(event) {
  if (event.pointerType === 'mouse') show(false)
}

function onLeave() {
  if (shown.value && !tip.pinned) hideTip(btn.value)
}

onBeforeUnmount(() => {
  if (btn.value) hideTip(btn.value)
})
</script>

<style scoped>
.term-hint {
  display: inline-flex;
  vertical-align: middle;
  margin-left: 0.3rem;
}

.term-hint__btn {
  display: inline-grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border: 1px solid var(--line);
  border-radius: 50%;
  background: var(--surface);
  color: var(--muted);
  font-size: 0.68rem;
  font-weight: 700;
  line-height: 1;
  cursor: help;
  user-select: none;
}

.term-hint__btn:hover,
.term-hint.is-open .term-hint__btn {
  border-color: var(--accent);
  color: var(--accent);
}

.term-hint__btn:focus-visible {
  outline: 2px solid var(--accent-ring, var(--accent));
  outline-offset: 2px;
}
</style>
