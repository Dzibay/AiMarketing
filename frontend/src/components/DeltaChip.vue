<template>
  <span class="delta" :class="tone" role="img" :aria-label="spoken" :title="spoken">
    <ArrowUp v-if="rounded > 0" aria-hidden="true" />
    <ArrowDown v-else-if="rounded < 0" aria-hidden="true" />
    {{ pct(Math.abs(rounded), 0) }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { ArrowDown, ArrowUp } from '@lucide/vue'
import { pct } from '../utils/format'

/**
 * Изменение к прошлому периоду фишкой со стрелкой. Цвет — только там, где направление однозначно:
 * good="up" — рост хорош (конверсии), good="down" — рост плох (CPA, ДРР), без good — нейтрально (расход).
 */
const props = defineProps({
  value: { type: Number, required: true },
  good: { type: String, default: '' },
  /** С чем сравнили — для подсказки и экранного диктора: «к прошлым 7 дням». */
  versus: { type: String, default: '' },
})

// Меньше 5 % — шум, не красим.
const NOISE_PCT = 5

const rounded = computed(() => Math.round(props.value))
const tone = computed(() => {
  if (!props.good || Math.abs(rounded.value) < NOISE_PCT) return ''
  const better = props.good === 'up' ? rounded.value > 0 : rounded.value < 0
  return better ? 'tone-ok' : 'tone-bad'
})
const spoken = computed(() => {
  const v = rounded.value
  const what = v > 0 ? `рост на ${pct(v, 0)}` : v < 0 ? `снижение на ${pct(-v, 0)}` : 'без изменений'
  return props.versus ? `${what} ${props.versus}` : what
})
</script>
