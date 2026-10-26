<template>
  <div v-if="points.length" class="trend-chart">
    <svg :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="label" preserveAspectRatio="none">
      <defs>
        <linearGradient :id="gradId" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.35" />
          <stop offset="100%" stop-color="var(--accent)" stop-opacity="0" />
        </linearGradient>
      </defs>
      <line :x1="padX" :x2="width - padX" :y1="height - padY" :y2="height - padY" stroke="var(--line)" />
      <polyline v-if="areaPath" :points="areaPath" :fill="`url(#${gradId})`" stroke="none" />
      <polyline
        :points="linePath"
        fill="none"
        stroke="var(--accent)"
        stroke-width="2.5"
        stroke-linejoin="round"
        stroke-linecap="round"
        vector-effect="non-scaling-stroke"
      />
      <circle v-for="(p, i) in plot" :key="i" :cx="p.x" :cy="p.y" r="3" fill="var(--ink)">
        <title>{{ p.title }}</title>
      </circle>
    </svg>
    <div class="trend-chart__axis">
      <span>{{ formatDate(points[0]?.day) }}</span>
      <span v-if="maxValue">макс. {{ formatValue(maxValue) }}</span>
      <span>{{ formatDate(points[points.length - 1]?.day) }}</span>
    </div>
  </div>
  <p v-else class="empty">Нет точек для графика.</p>
</template>

<script setup>
import { computed } from 'vue'
import { formatDate } from '../utils/format'

const props = defineProps({
  points: { type: Array, default: () => [] },
  valueKey: { type: String, default: 'cost_kopecks' },
  label: { type: String, default: 'Тренд' },
  formatValue: { type: Function, default: (v) => String(v) },
})

const width = 640
const height = 160
const padX = 8
const padY = 16
const gradId = `tg-${Math.random().toString(36).slice(2, 8)}`

const values = computed(() => (props.points || []).map((r) => Number(r[props.valueKey] || 0)))
const maxValue = computed(() => (values.value.length ? Math.max(...values.value) : 0))

const plot = computed(() => {
  const rows = props.points || []
  if (!rows.length) return []
  const max = Math.max(maxValue.value, 1)
  const n = rows.length
  return rows.map((row, i) => {
    const x = padX + (n === 1 ? (width - padX * 2) / 2 : (i / (n - 1)) * (width - padX * 2))
    const y = height - padY - (values.value[i] / max) * (height - padY * 2)
    return { x, y, title: `${formatDate(row.day)}: ${props.formatValue(values.value[i])}` }
  })
})

const linePath = computed(() => plot.value.map((p) => `${p.x},${p.y}`).join(' '))
const areaPath = computed(() => {
  if (!plot.value.length) return ''
  const first = plot.value[0]
  const last = plot.value[plot.value.length - 1]
  return `${first.x},${height - padY} ${linePath.value} ${last.x},${height - padY}`
})
</script>
