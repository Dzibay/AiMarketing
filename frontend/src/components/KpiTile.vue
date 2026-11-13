<template>
  <div class="ktile">
    <span class="ktile__label" v-tip.truncate="label">{{ label }}</span>
    <div class="ktile__value">
      <strong>{{ value }}</strong>
      <DeltaChip v-if="change != null" :value="change" :good="good" :versus="versus" />
    </div>
    <!-- Строка пояснения есть всегда (пустая — неразрывный пробел), чтобы плитки в ряду совпадали построчно. -->
    <span class="ktile__note" :class="noteTone" v-tip.truncate="note || undefined">
      <slot name="note">{{ note || NBSP }}</slot>
    </span>
    <!-- Место под спарклайн — всегда; без данных в нём тонкий пунктир (:empty в ui.css). -->
    <div class="ktile__spark"><Sparkline v-if="points.length" :points="points" :prev-points="prevPoints" :value-key="valueKey" :height="40" /></div>
  </div>
</template>

<script setup>
import DeltaChip from './DeltaChip.vue'
import Sparkline from './Sparkline.vue'

const NBSP = String.fromCharCode(160) // неразрывный пробел: держит высоту пустой строки

/**
 * Плитка экспертной главной. Структура одна на все плитки: подпись, цифра с фишкой изменения в одну строку,
 * одна строка пояснения и место под спарклайн недели (пунктир — неделя до неё). Нет ряда по дням — пунктир-заглушка.
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
