<template>
  <div v-if="points.length" class="trend-chart">
    <div v-if="hasPrev" class="trend-chart__legend">
      <span><i :style="{ borderColor: color }"></i>Текущий период</span>
      <span><i class="is-prev"></i>Предыдущий период</span>
    </div>

    <div ref="plotEl" class="trend-chart__plot">
      <svg
        :viewBox="`0 0 ${width} ${height}`"
        :height="height"
        role="img"
        :aria-label="label"
        @pointermove="onPointer"
        @pointerdown="onPointer"
        @pointerleave="hover = null"
      >
        <defs>
          <linearGradient :id="gradId" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" :style="{ stopColor: color, stopOpacity: 0.26 }" />
            <stop offset="100%" :style="{ stopColor: color, stopOpacity: 0 }" />
          </linearGradient>
        </defs>

        <g v-for="tick in ticks" :key="tick.value">
          <line class="trend-chart__grid" x1="0" :x2="width" :y1="tick.y" :y2="tick.y" />
          <text class="trend-chart__ylabel" x="0" :y="tick.y - 6">{{ formatValue(tick.value) }}</text>
        </g>

        <path v-if="prevLine" class="trend-chart__prev" :d="prevLine" />
        <path :d="area" :fill="`url(#${gradId})`" />
        <path class="trend-chart__line" :d="line" :style="{ stroke: color }" />
        <circle v-for="p in isolated" :key="p.i" :cx="p.x" :cy="p.y" r="3" :style="{ fill: color }" />

        <g v-if="active">
          <line class="trend-chart__cursor" :x1="active.x" :x2="active.x" :y1="padTop" :y2="baseY" />
          <circle
            v-if="active.prevY !== null"
            class="trend-chart__dot"
            :cx="active.x"
            :cy="active.prevY"
            r="4"
            style="fill: var(--faint)"
          />
          <circle v-if="active.y !== null" class="trend-chart__dot" :cx="active.x" :cy="active.y" r="5" :style="{ fill: color }" />
        </g>
      </svg>

      <div v-if="active" class="trend-chart__tip" :style="tipStyle">
        <strong>{{ formatDate(active.day) }}</strong>
        <div>
          <span><i :style="{ background: color }"></i>{{ label }}</span>
          <b>{{ active.value === null ? '—' : formatValue(active.value) }}</b>
        </div>
        <div v-if="hasPrev">
          <span><i style="background: var(--faint)"></i>Было, {{ formatDate(active.prevDay) }}</span>
          <b>{{ active.prevValue === null ? '—' : formatValue(active.prevValue) }}</b>
        </div>
      </div>
    </div>

    <div class="trend-chart__axis">
      <span v-for="(day, i) in axisDays" :key="i">{{ formatDate(day) }}</span>
    </div>
  </div>
  <p v-else class="empty">Нет точек для графика.</p>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { formatDate } from '../utils/format'
import { areaPath, isolatedPoints, linePath, niceTicks, seriesValues } from '../utils/chart'

const props = defineProps({
  points: { type: Array, default: () => [] },
  /** Тот же ряд за предыдущий период той же длины — рисуется пунктиром. */
  prevPoints: { type: Array, default: () => [] },
  valueKey: { type: String, default: 'cost_kopecks' },
  label: { type: String, default: 'Тренд' },
  formatValue: { type: Function, default: (v) => String(v) },
  color: { type: String, default: 'var(--accent)' },
  height: { type: Number, default: 220 },
})

const plotEl = ref(null)
const width = ref(640)
const hover = ref(null)
const gradId = `tg-${Math.random().toString(36).slice(2, 8)}`

// Ширина SVG = ширина блока в пикселях: подписи оси не растягиваются вместе с графиком.
let observer = null
watch(plotEl, (el) => {
  observer?.disconnect()
  if (!el) return
  observer = new ResizeObserver(([entry]) => {
    width.value = Math.max(Math.round(entry.contentRect.width), 240)
  })
  observer.observe(el)
})
onBeforeUnmount(() => observer?.disconnect())

const padX = 6
const padTop = 22
const baseY = computed(() => props.height - 4)

const values = computed(() => seriesValues(props.points, props.valueKey))
const prevValues = computed(() => seriesValues(props.prevPoints, props.valueKey).slice(0, props.points.length))
const hasPrev = computed(() => prevValues.value.some((v) => v !== null))

const ticks = computed(() => {
  const all = [...values.value, ...(hasPrev.value ? prevValues.value : [])].filter((v) => v !== null)
  const list = niceTicks(Math.max(0, ...all))
  const top = list[list.length - 1] || 1
  return list.map((value) => ({ value, y: yFor(value, top) }))
})
const scaleTop = computed(() => ticks.value[ticks.value.length - 1].value || 1)

function yFor(value, top = scaleTop.value) {
  return baseY.value - (value / top) * (baseY.value - padTop)
}

function xFor(i) {
  const n = props.points.length
  if (n === 1) return width.value / 2
  return padX + (i / (n - 1)) * (width.value - padX * 2)
}

const plot = computed(() => values.value.map((v, i) => ({ i, x: xFor(i), y: v === null ? null : yFor(v) })))
const prevPlot = computed(() => prevValues.value.map((v, i) => ({ i, x: xFor(i), y: v === null ? null : yFor(v) })))

const line = computed(() => linePath(plot.value))
const area = computed(() => areaPath(plot.value, baseY.value))
const isolated = computed(() => isolatedPoints(plot.value))
const prevLine = computed(() => (hasPrev.value ? linePath(prevPlot.value) : ''))

const axisDays = computed(() => {
  const rows = props.points
  if (rows.length < 3) return rows.map((r) => r.day)
  return [rows[0].day, rows[Math.floor((rows.length - 1) / 2)].day, rows[rows.length - 1].day]
})

function onPointer(event) {
  const n = props.points.length
  if (!n) return
  const rect = event.currentTarget.getBoundingClientRect()
  const x = ((event.clientX - rect.left) / rect.width) * width.value
  const i = n === 1 ? 0 : Math.round(((x - padX) / (width.value - padX * 2)) * (n - 1))
  hover.value = Math.min(Math.max(i, 0), n - 1)
}

const active = computed(() => {
  const i = hover.value
  if (i === null || i >= props.points.length) return null
  return {
    x: plot.value[i].x,
    y: plot.value[i].y,
    day: props.points[i].day,
    value: values.value[i],
    prevY: hasPrev.value ? (prevPlot.value[i]?.y ?? null) : null,
    prevDay: props.prevPoints[i]?.day,
    prevValue: prevValues.value[i] ?? null,
  }
})

// Подсказка сбоку от курсора, чтобы не закрывать точку: слева от середины — справа, и наоборот.
const tipStyle = computed(() => {
  const x = active.value.x
  const right = x > width.value / 2
  return { left: `${x}px`, transform: right ? 'translateX(calc(-100% - 14px))' : 'translateX(14px)' }
})
</script>
