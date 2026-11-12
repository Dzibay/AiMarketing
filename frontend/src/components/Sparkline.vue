<template>
  <svg
    v-if="values.some((v) => v !== null)"
    class="sparkline"
    :viewBox="`0 0 ${W} ${height}`"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    <defs>
      <linearGradient :id="gradId" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" :style="{ stopColor: color, stopOpacity: 0.12 }" />
        <stop offset="100%" :style="{ stopColor: color, stopOpacity: 0 }" />
      </linearGradient>
    </defs>
    <path v-if="prevLine" class="is-prev" :d="prevLine" vector-effect="non-scaling-stroke" />
    <path :d="area" :fill="`url(#${gradId})`" />
    <path class="is-line" :d="line" :style="{ stroke: color }" vector-effect="non-scaling-stroke" />
    <!-- Последний день — точкой. SVG растянут по ширине, поэтому круг рисуем круглым концом штриха нулевой
         длины: non-scaling-stroke держит его круглым при любой ширине. -->
    <template v-if="dot">
      <path class="is-dot-ring" :d="dot" vector-effect="non-scaling-stroke" />
      <path class="is-dot" :d="dot" :style="{ stroke: color }" vector-effect="non-scaling-stroke" />
    </template>
  </svg>
</template>

<script setup>
import { computed } from 'vue'
import { areaPath, linePath, seriesValues } from '../utils/chart'

const props = defineProps({
  points: { type: Array, default: () => [] },
  /** Предыдущий период той же длины — бледный пунктир под основной линией. */
  prevPoints: { type: Array, default: () => [] },
  valueKey: { type: String, default: 'cost_kopecks' },
  // Один цвет на все плитки — чернила; цвет значит оценку, а у линии её нет.
  color: { type: String, default: 'var(--ink)' },
  height: { type: Number, default: 46 },
})

// Без подписей, поэтому SVG можно растягивать по ширине; толщину линий держит non-scaling-stroke.
const W = 200
const gradId = `sg-${Math.random().toString(36).slice(2, 8)}`

const values = computed(() => seriesValues(props.points, props.valueKey))
const prevValues = computed(() => seriesValues(props.prevPoints, props.valueKey).slice(0, values.value.length))

const top = computed(() => Math.max(0, ...values.value, ...prevValues.value) || 1)

function toPlot(list) {
  const n = values.value.length
  const pad = 3
  return list.map((v, i) => ({
    x: n === 1 ? W / 2 : (i / (n - 1)) * W,
    y: v === null ? null : props.height - pad - (v / top.value) * (props.height - pad * 2),
  }))
}

const line = computed(() => linePath(toPlot(values.value)))
const area = computed(() => areaPath(toPlot(values.value), props.height))
const dot = computed(() => {
  const pts = toPlot(values.value)
  const last = [...pts].reverse().find((p) => p.y !== null)
  return last ? `M${last.x.toFixed(1)},${last.y.toFixed(1)}h0.01` : ''
})
const prevLine = computed(() => (prevValues.value.some((v) => v !== null) ? linePath(toPlot(prevValues.value)) : ''))
</script>
