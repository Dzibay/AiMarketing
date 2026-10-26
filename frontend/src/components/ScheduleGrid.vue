<template>
  <div class="schedule">
    <div class="seg" role="group" aria-label="Шаблоны расписания">
      <button v-for="p in PRESETS" :key="p.id" type="button" :class="{ active: preset === p.id }" @click="applyPreset(p.id)">
        {{ p.label }}
      </button>
    </div>
    <div class="schedule__grid" role="grid" @mouseleave="dragging = null">
      <span></span>
      <span v-for="h in 24" :key="`h${h}`" class="schedule__hour">{{ h - 1 }}</span>
      <template v-for="(row, d) in grid" :key="`d${d}`">
        <button type="button" class="schedule__day" :title="`Переключить ${DAYS[d]}`" @click="toggleRow(d)">
          {{ DAYS[d] }}
        </button>
        <button
          v-for="(v, h) in row"
          :key="`c${d}-${h}`"
          type="button"
          class="schedule__cell"
          :class="{ 'is-on': v > 0 }"
          :aria-label="`${DAYS[d]} ${h}:00 — ${v > 0 ? 'показы' : 'нет показов'}`"
          @mousedown.prevent="startDrag(d, h)"
          @mouseenter="dragTo(d, h)"
          @mouseup="dragging = null"
        ></button>
      </template>
    </div>
    <p class="field-hint" :class="{ 'tone-bad': weekdayHours < 40 }">
      В будни {{ weekdayHours }} ч показов{{ weekdayHours < 40 ? ' — Директ требует не меньше 40' : '' }}. Клик или
      протягивание мышью переключает часы, клик по дню — весь день.
    </p>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  // null — круглосуточно; иначе 7×24 (100 — показ, 0 — нет).
  modelValue: { type: Array, default: null },
})
const emit = defineEmits(['update:modelValue'])

const DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
const PRESETS = [
  { id: 'always', label: 'Круглосуточно' },
  { id: 'workdays', label: 'Будни' },
  { id: 'workhours', label: 'Будни 9–19' },
  { id: 'daytime', label: 'Каждый день 8–23' },
]

function presetGrid(id) {
  return Array.from({ length: 7 }, (_, d) =>
    Array.from({ length: 24 }, (_, h) => {
      if (id === 'workdays') return d < 5 ? 100 : 0
      if (id === 'workhours') return d < 5 && h >= 9 && h < 19 ? 100 : 0
      if (id === 'daytime') return h >= 8 && h < 23 ? 100 : 0
      return 100
    }),
  )
}

const grid = computed(() => props.modelValue || presetGrid('always'))
const preset = computed(() => {
  const flat = JSON.stringify(grid.value.map((r) => r.map((v) => (v > 0 ? 100 : 0))))
  return PRESETS.find((p) => JSON.stringify(presetGrid(p.id)) === flat)?.id || ''
})
const weekdayHours = computed(() => grid.value.slice(0, 5).reduce((sum, row) => sum + row.filter((v) => v > 0).length, 0))
const dragging = ref(null)

function emitGrid(next) {
  emit('update:modelValue', next.every((row) => row.every((v) => v === 100)) ? null : next)
}

function applyPreset(id) {
  emitGrid(presetGrid(id))
}

function setCell(d, h, value) {
  const next = grid.value.map((row) => [...row])
  next[d][h] = value
  emitGrid(next)
}

function startDrag(d, h) {
  const value = grid.value[d][h] > 0 ? 0 : 100
  dragging.value = value
  setCell(d, h, value)
}

function dragTo(d, h) {
  if (dragging.value == null || grid.value[d][h] === dragging.value) return
  setCell(d, h, dragging.value)
}

function toggleRow(d) {
  const on = grid.value[d].some((v) => v === 0)
  const next = grid.value.map((row) => [...row])
  next[d] = next[d].map(() => (on ? 100 : 0))
  emitGrid(next)
}
</script>
