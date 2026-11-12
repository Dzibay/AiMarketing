<template>
  <div class="ktile">
    <span class="ktile__label" :title="label">{{ label }}</span>
    <div class="ktile__value">
      <strong>{{ value }}</strong>
      <DeltaChip v-if="change != null" :value="change" :good="good" :versus="versus" />
    </div>
    <span v-if="note || $slots.note" class="ktile__note" :class="noteTone" :title="note || undefined">
      <slot name="note">{{ note }}</slot>
    </span>
    <Sparkline v-if="points.length" :points="points" :prev-points="prevPoints" :value-key="valueKey" :height="40" />
  </div>
</template>

<script setup>
import DeltaChip from './DeltaChip.vue'
import Sparkline from './Sparkline.vue'

/**
 * Плитка экспертной главной: подпись в одну строку, цифра одного размера, изменение фишкой со стрелкой,
 * под ней одна строка пояснения и спарклайн недели (пунктир — неделя до неё).
 */
defineProps({
  label: { type: String, required: true },
  value: { type: String, required: true },
  /** Изменение в процентах к прошлому периоду; null — сравнивать не с чем. */
  change: { type: Number, default: null },
  /** 'up' — рост хорош, 'down' — рост плох, '' — нейтрально (расход). */
  good: { type: String, default: '' },
  versus: { type: String, default: '' },
  note: { type: String, default: '' },
  /** tone-ok / tone-warn / tone-bad — когда пояснение само оценка (KPI против цели, дней на балансе). */
  noteTone: { type: String, default: '' },
  points: { type: Array, default: () => [] },
  prevPoints: { type: Array, default: () => [] },
  valueKey: { type: String, default: 'cost_kopecks' },
})
</script>
