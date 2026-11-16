<template>
  <div class="trend-chart adm-chart">
    <div class="trend-chart__legend">
      <span v-for="s in series" :key="s.key">
        <i :class="{ 'is-bar': s.type === 'bar' }" :style="legendStyle(s)"></i>{{ s.label }}
      </span>
      <span v-if="prevSeries"><i class="is-prev"></i>{{ prevSeries.label }}, прошлый период</span>
    </div>

    <div ref="plotEl" class="trend-chart__plot">
      <svg
        :viewBox="`0 0 ${width} ${height}`"
        :height="height"
        role="img"
        :aria-label="title"
        @pointermove="onPointer"
        @pointerdown="onPointer"
        @pointerleave="hover = null"
      >
        <g v-for="tick in ticks" :key="tick.value">
          <line class="trend-chart__grid" :x1="padLeft" :x2="width" :y1="tick.y" :y2="tick.y" />
          <text class="trend-chart__ylabel" :x="padLeft - 8" :y="tick.y" text-anchor="end" dominant-baseline="middle">{{ tick.label }}</text>
        </g>

        <rect v-if="active" class="adm-chart__band" :x="xAt(active.i) - band / 2" :y="padTop" :width="band" :height="baseY - padTop" />

        <template v-for="s in bars" :key="s.key">
          <rect
            v-for="b in s.rects"
            :key="b.i"
            class="adm-chart__bar"
            :x="b.x"
            :y="b.y"
            :width="b.w"
            :height="b.h"
            :style="{ fill: s.color }"
          />
        </template>

        <path v-if="prevLine" class="trend-chart__prev" :d="prevLine" />
        <template v-for="s in lines" :key="s.key">
          <path class="trend-chart__line" :d="s.d" :style="{ stroke: s.color }" />
          <circle v-for="p in s.dots" :key="p.i" :cx="p.x" :cy="p.y" r="3" :style="{ fill: s.color }" />
        </template>

        <g v-if="active">
          <circle
            v-for="d in active.dots"
            :key="d.key"
            class="trend-chart__dot"
            :cx="xAt(active.i)"
            :cy="d.y"
            :r="d.prev ? 4 : 5"
            :style="{ fill: d.color }"
          />
        </g>
      </svg>

      <div v-if="active" class="trend-chart__tip" :style="tipStyle">
        <strong>{{ bucketText(points[active.i].day) }}</strong>
        <div v-for="s in tipSeries" :key="s.key">
          <span><i :style="{ background: s.color }"></i>{{ s.label }}</span>
          <b>{{ fmt(s, points[active.i][s.key]) }}</b>
        </div>
        <div v-if="stacked">
          <span>Всего</span>
          <b>{{ formatValue(stackTotals[active.i]) }}</b>
        </div>
        <div v-if="prevSeries && prevPoints[active.i]">
          <span><i style="background: var(--faint)"></i>Было, {{ bucketText(prevPoints[active.i].day) }}</span>
          <b>{{ fmt(prevSeries, prevPoints[active.i][prevSeries.key]) }}</b>
        </div>
      </div>
    </div>

    <div class="trend-chart__axis" :style="{ paddingLeft: `${padLeft}px` }">
      <span v-for="(day, i) in axisDays" :key="i">{{ axisText(day) }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { formatDate, formatRange } from '../../utils/format'
import { addDays } from '../../components/ui/calendar'
import { isolatedPoints, linePath, niceTicks, seriesValues } from '../../utils/chart'

/**
 * График дашборда: несколько рядов по корзинам шага (день, неделя, месяц) — линии и столбики на одной шкале,
 * пунктиром тот же ряд за прошлый период. Подсказка по корзине — все ряды и «было».
 */
const props = defineProps({
  title: { type: String, required: true },
  /** [{ day, …значения }] — day — первый день корзины. */
  points: { type: Array, default: () => [] },
  prevPoints: { type: Array, default: () => [] },
  /** [{ key, label, color, type: 'line' | 'bar', prev: true — показать пунктир прошлого периода, format? }] */
  series: { type: Array, required: true },
  step: { type: String, default: 'day' },
  /** Последний день периода — чтобы последняя неделя/месяц в подсказке не выходили за него. */
  lastDay: { type: String, default: '' },
  formatValue: { type: Function, default: (v) => String(v) },
  height: { type: Number, default: 200 },
  /** Столбики рядов — друг на друге (части одного целого), шкала — по сумме; в подсказке — «Всего». */
  stacked: { type: Boolean, default: false },
})

const plotEl = ref(null)
const width = ref(640)
const hover = ref(null)

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

const padTop = 14
const padRight = 4
const baseY = computed(() => props.height - 4)
const fmt = (s, v) => (v === null || v === undefined ? '—' : (s.format || props.formatValue)(Number(v)))

const prevSeries = computed(() => {
  const s = props.series.find((x) => x.prev)
  return s && props.prevPoints.length ? s : null
})
const prevValues = computed(() =>
  prevSeries.value ? seriesValues(props.prevPoints, prevSeries.value.key).slice(0, props.points.length) : [],
)

const stackTotals = computed(() =>
  props.points.map((p) => props.series.filter((s) => s.type === 'bar').reduce((a, s) => a + Number(p[s.key] || 0), 0)),
)

const ticks = computed(() => {
  const all = props.stacked
    ? [...stackTotals.value, ...props.series.filter((s) => s.type !== 'bar').flatMap((s) => seriesValues(props.points, s.key))]
    : props.series.flatMap((s) => seriesValues(props.points, s.key))
  const list = niceTicks(Math.max(0, ...all.filter((v) => v !== null), ...prevValues.value.filter((v) => v !== null)))
  const top = list[list.length - 1] || 1
  return list.map((value) => ({ value, label: props.formatValue(value), y: yFor(value, top) }))
})
const scaleTop = computed(() => ticks.value[ticks.value.length - 1].value || 1)

function yFor(value, top = scaleTop.value) {
  return baseY.value - (value / top) * (baseY.value - padTop)
}

// Колонка подписей шкалы — по самой длинной подписи (как в TrendChart).
const fontsReady = ref(0)
document.fonts?.ready.then(() => fontsReady.value++)
let measureCtx = null
function textWidth(text) {
  measureCtx ??= document.createElement('canvas').getContext('2d')
  if (!measureCtx) return text.length * 7
  measureCtx.font = `11px ${getComputedStyle(plotEl.value || document.body).fontFamily}`
  return measureCtx.measureText(text).width
}
const padLeft = computed(() => {
  void fontsReady.value
  return Math.ceil(Math.max(0, ...ticks.value.map((t) => textWidth(t.label)))) + 14
})

// Корзины — полосы одинаковой ширины; точки линий и столбики — по центру полосы.
const n = computed(() => Math.max(props.points.length, 1))
const band = computed(() => Math.max((width.value - padLeft.value - padRight) / n.value, 1))
const xAt = (i) => padLeft.value + (i + 0.5) * band.value

const barSeries = computed(() => props.series.filter((s) => s.type === 'bar'))
const bars = computed(() => {
  const k = barSeries.value.length
  if (!k) return []
  const inner = Math.min(band.value * 0.7, 48)
  if (props.stacked) {
    const acc = props.points.map(() => 0)
    return barSeries.value.map((s) => ({
      key: s.key,
      color: s.color,
      rects: seriesValues(props.points, s.key)
        .map((v, i) => {
          if (!v) return null
          const y0 = yFor(acc[i])
          acc[i] += v
          const y = yFor(acc[i])
          return { i, x: xAt(i) - inner / 2, y, w: Math.max(inner, 1), h: Math.max(y0 - y, 1) }
        })
        .filter(Boolean),
    }))
  }
  const w = Math.max(inner / k - (k > 1 ? 2 : 0), 1)
  return barSeries.value.map((s, j) => ({
    key: s.key,
    color: s.color,
    rects: seriesValues(props.points, s.key)
      .map((v, i) => {
        if (!v) return null
        const y = yFor(v)
        return { i, x: xAt(i) - inner / 2 + j * (inner / k), y, w, h: Math.max(baseY.value - y, 1) }
      })
      .filter(Boolean),
  }))
})

const lines = computed(() =>
  props.series
    .filter((s) => s.type !== 'bar')
    .map((s) => {
      const pts = seriesValues(props.points, s.key).map((v, i) => ({ i, x: xAt(i), y: v === null ? null : yFor(v) }))
      return { key: s.key, color: s.color, d: linePath(pts), dots: isolatedPoints(pts) }
    }),
)

const prevLine = computed(() =>
  prevSeries.value ? linePath(prevValues.value.map((v, i) => ({ i, x: xAt(i), y: v === null ? null : yFor(v) }))) : '',
)

function legendStyle(s) {
  return s.type === 'bar' ? { background: s.color } : { borderColor: s.color }
}

const MONTH = { month: 'short', year: 'numeric' }
function monthText(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString('ru-RU', MONTH).replace(/\s*г\.$/, '')
}

function bucketEnd(iso) {
  let end = iso
  if (props.step === 'week') end = addDays(iso, 6)
  if (props.step === 'month') {
    const d = new Date(`${iso}T00:00:00`)
    end = addDays(iso, new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate() - 1)
  }
  return end
}

/** Подпись корзины в подсказке: «16 нояб.», «9–15 нояб.», «нояб. 2026». */
function bucketText(iso) {
  if (props.step === 'month') return monthText(iso)
  if (props.step === 'week') {
    const end = bucketEnd(iso)
    return formatRange(iso, props.lastDay && end > props.lastDay && iso <= props.lastDay ? props.lastDay : end)
  }
  return formatDate(iso)
}

const axisText = (iso) => (props.step === 'month' ? monthText(iso) : formatDate(iso))

const axisDays = computed(() => {
  const rows = props.points
  if (rows.length < 3) return rows.map((r) => r.day)
  return [rows[0].day, rows[Math.floor((rows.length - 1) / 2)].day, rows[rows.length - 1].day]
})

function onPointer(event) {
  if (!props.points.length) return
  const rect = event.currentTarget.getBoundingClientRect()
  const x = ((event.clientX - rect.left) / rect.width) * width.value
  const i = Math.floor((x - padLeft.value) / band.value)
  hover.value = Math.min(Math.max(i, 0), props.points.length - 1)
}

const active = computed(() => {
  const i = hover.value
  if (i === null || i >= props.points.length) return null
  const dots = props.series
    .filter((s) => s.type !== 'bar')
    .map((s) => ({ key: s.key, color: s.color, v: props.points[i][s.key] }))
    .filter((d) => d.v !== null && d.v !== undefined)
    .map((d) => ({ ...d, y: yFor(Number(d.v)) }))
  const pv = prevValues.value[i]
  if (prevSeries.value && pv !== null && pv !== undefined) dots.unshift({ key: '_prev', color: 'var(--faint)', y: yFor(pv), prev: true })
  return { i, dots }
})

/** В подсказке сложенного графика — только части с ненулевым значением в этой корзине, сверху вниз. */
const tipSeries = computed(() => {
  if (!props.stacked || !active.value) return props.series
  const p = props.points[active.value.i]
  return props.series.filter((s) => Number(p[s.key] || 0) !== 0).reverse()
})

const tipStyle = computed(() => {
  const x = xAt(active.value.i)
  const right = x > width.value / 2
  return { left: `${x}px`, transform: right ? 'translateX(calc(-100% - 14px))' : 'translateX(14px)' }
})
</script>
