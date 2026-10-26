<template>
  <div class="trend-chart" v-if="points.length">
    <svg :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="label">
      <defs>
        <linearGradient :id="gradId" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.35" />
          <stop offset="100%" stop-color="var(--accent)" stop-opacity="0" />
        </linearGradient>
      </defs>
      <polyline
        v-if="areaPath"
        :points="areaPath"
        fill="url(#gradId)"
        stroke="none"
      />
      <polyline
        :points="linePath"
        fill="none"
        stroke="var(--accent)"
        stroke-width="2.5"
        stroke-linejoin="round"
        stroke-linecap="round"
      />
      <circle
        v-for="(p, i) in plot"
        :key="i"
        :cx="p.x"
        :cy="p.y"
        r="3"
        fill="var(--ink)"
      >
        <title>{{ p.title }}</title>
      </circle>
    </svg>
    <div class="trend-chart__axis">
      <span>{{ points[0]?.day }}</span>
      <span>{{ points[points.length - 1]?.day }}</span>
    </div>
  </div>
  <p v-else class="empty">Нет точек для графика.</p>
</template>

<script setup>
import { computed } from 'vue'

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

const plot = computed(() => {
  const rows = props.points || []
  if (!rows.length) return []
  const values = rows.map((r) => Number(r[props.valueKey] || 0))
  const max = Math.max(...values, 1)
  const n = rows.length
  return rows.map((row, i) => {
    const x = padX + (n === 1 ? (width - padX * 2) / 2 : (i / (n - 1)) * (width - padX * 2))
    const y = height - padY - (values[i] / max) * (height - padY * 2)
    return {
      x,
      y,
      title: `${row.day}: ${props.formatValue(values[i])}`,
    }
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
