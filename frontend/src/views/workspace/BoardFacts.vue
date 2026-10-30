<template>
  <ul class="board-facts" :class="{ 'board-facts--bars': bars }">
    <li
      v-for="(f, i) in items"
      :key="i"
      :class="{ 'is-pending': f.pending, 'is-off': !board.isOn(f), 'is-own': f.own }"
      :style="bars && width(f) != null ? { '--bar': `${width(f)}%` } : null"
    >
      <input
        v-if="f.pending"
        type="checkbox"
        :checked="board.isOn(f)"
        :disabled="board.disabled.value"
        :aria-label="f.text"
        @change="board.toggle(f)"
      />
      <span class="board-facts__key">{{ f.text }}</span>
      <span v-if="f.value" class="board-facts__value">{{ f.value }}</span>
      <small v-if="f.note">{{ f.note }}</small>
    </li>
  </ul>
</template>

<script setup>
/** Строки этапа на доске: цели, спрос (bars — полосы по числу), ссылка, настройки. Ждущие — с галочками. */
import { computed, inject } from 'vue'

const props = defineProps({
  items: { type: Array, required: true },
  bars: { type: Boolean, default: false },
})
const board = inject('board')

const numbers = computed(() => props.items.map((f) => toNumber(f.value)))
const top = computed(() => Math.max(0, ...numbers.value.filter((n) => n != null)))

function toNumber(value) {
  const digits = String(value || '').replace(/[\s ]/g, '').match(/^\d+/)
  return digits ? Number(digits[0]) : null
}

function width(f) {
  const n = toNumber(f.value)
  if (n == null || !top.value) return null
  return Math.max(3, Math.round((n / top.value) * 100))
}
</script>
