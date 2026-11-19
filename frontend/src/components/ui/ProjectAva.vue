<template>
  <span
    class="pava"
    :class="[`pava--${tone}`, { 'pava--sm': size < 26, 'pava--urgent': hot }]"
    :style="{ '--ava': projectColor(project.id), '--pava-size': `${size}px` }"
    v-bind="a11y"
  >
    {{ projectInitial(project.name) }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { projectColor, projectDotA11y, projectInitial, projectTone, projectUrgent } from '../../composables/useWorkspaces'

/**
 * Кружок проекта: постоянный цвет (по id) и первая буква названия. Вне тарифа — бледный, проблема со счётчиком
 * Метрики — кольцо в углу и текст для экранного диктора, срочный сигнал (urgent) — красная точка сверху.
 * Без tone — только цвет и буква (строки в таблицах).
 */
const props = defineProps({
  /** { id, name, locked?, counter_ok? } */
  project: { type: Object, required: true },
  size: { type: Number, default: 20 },
  /** Показывать состояние проекта (тариф, счётчик). */
  status: { type: Boolean, default: true },
  /** Красная точка, если у проекта горит срочный сигнал (меню и переключатель проектов). */
  urgent: { type: Boolean, default: false },
})

const tone = computed(() => (props.status ? projectTone(props.project) : 'ok'))
const a11y = computed(() => (props.status ? projectDotA11y(props.project) : { 'aria-hidden': 'true' }))
const hot = computed(() => props.urgent && projectUrgent(props.project))
</script>
