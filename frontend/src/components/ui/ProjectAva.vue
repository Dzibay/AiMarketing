<template>
  <span
    class="pava"
    :class="[`pava--${tone}`, { 'pava--sm': size < 26 }]"
    :style="{ '--ava': projectColor(project.id), '--pava-size': `${size}px` }"
    v-bind="a11y"
  >
    {{ projectInitial(project.name) }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { projectColor, projectDotA11y, projectInitial, projectTone } from '../../composables/useWorkspaces'

/**
 * Кружок проекта: постоянный цвет (по id) и первая буква названия. Вне тарифа — бледный, проблема со счётчиком
 * Метрики — кольцо в углу и текст для экранного диктора. Без tone — только цвет и буква (строки в таблицах).
 */
const props = defineProps({
  /** { id, name, locked?, counter_ok? } */
  project: { type: Object, required: true },
  size: { type: Number, default: 20 },
  /** Показывать состояние проекта (тариф, счётчик). */
  status: { type: Boolean, default: true },
})

const tone = computed(() => (props.status ? projectTone(props.project) : 'ok'))
const a11y = computed(() => (props.status ? projectDotA11y(props.project) : { 'aria-hidden': 'true' }))
</script>
