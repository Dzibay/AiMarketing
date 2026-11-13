<template>
  <div ref="rootEl" class="ui-cal" :class="{ 'is-picking': picking }" @mouseleave="hoverDay = ''">
    <div v-for="(m, mi) in grids" :key="m.key" class="ui-cal__month">
      <div class="ui-cal__head">
        <button
          v-if="mi === 0"
          class="ui-cal__arrow"
          type="button"
          aria-label="Предыдущий месяц"
          :disabled="!canPrev"
          @click="shiftView(-1)"
        >
          <ChevronLeft aria-hidden="true" />
        </button>
        <span v-else aria-hidden="true"></span>
        <p :id="`${uid}-${m.key}`" class="ui-cal__title" aria-live="polite">{{ m.title }}</p>
        <button
          v-if="mi === grids.length - 1"
          class="ui-cal__arrow"
          type="button"
          aria-label="Следующий месяц"
          :disabled="!canNext"
          @click="shiftView(1)"
        >
          <ChevronRight aria-hidden="true" />
        </button>
        <span v-else aria-hidden="true"></span>
      </div>
      <table class="ui-cal__grid" role="grid" :aria-labelledby="`${uid}-${m.key}`">
        <thead>
          <tr>
            <th v-for="[short, full] in WEEKDAYS" :key="short" scope="col" :aria-label="full">{{ short }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(week, wi) in m.weeks" :key="wi">
            <td
              v-for="(d, di) in week"
              :key="di"
              role="gridcell"
              :aria-selected="d && !d.off ? isEdge(d.iso) : undefined"
            >
              <button
                v-if="d"
                class="ui-cal__day"
                :class="dayClass(d)"
                type="button"
                :data-iso="d.iso"
                :tabindex="d.iso === focusDay ? 0 : -1"
                :aria-label="d.label"
                :aria-disabled="d.off || undefined"
                :aria-current="d.iso === today ? 'date' : undefined"
                @click="pick(d)"
                @mouseenter="hoverDay = d.off ? '' : d.iso"
                @focus="focusDay = d.iso"
                @keydown="onKey"
              >
                {{ d.day }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { ChevronLeft, ChevronRight } from '@lucide/vue'
import {
  MONTHS,
  WEEKDAYS,
  addDays,
  addMonths,
  clampIso,
  monthOf,
  shiftMonth,
  spokenDay,
  todayIso,
  weekday,
} from './calendar'

/**
 * Сетка месяцев для UiDateRange и UiDatePicker: неделя с понедельника, дни вне min…max неактивны.
 * start/end — выбранный день или период. range — выбор периода: пока выбран только первый день,
 * наведение (и стрелки) показывают будущий период.
 * Клавиатура: стрелки — по дням, PageUp/PageDown — по месяцам (с Shift — по годам), Home/End — начало и конец
 * недели, Enter и пробел — выбрать. Отдаёт pick(iso); focus() ставит фокус на текущий день.
 */
const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
  min: { type: String, default: '' },
  max: { type: String, default: '' },
  months: { type: Number, default: 1 },
  range: { type: Boolean, default: false },
})
const emit = defineEmits(['pick'])

const uid = `ui-cal-${Math.random().toString(36).slice(2, 10)}`
const rootEl = ref(null)
const today = todayIso()
const hoverDay = ref('')

// Видимые месяцы: при выборе периода правый месяц — тот, где период кончается.
const anchor = props.end || props.start || clampIso(today, props.min, props.max)
const view = ref(shiftMonth(monthOf(anchor), 1 - props.months))
const visible = (iso) => monthOf(iso) >= view.value && monthOf(iso) <= shiftMonth(view.value, props.months - 1)
const focusDay = ref([props.start, props.end, clampIso(today, props.min, props.max)].find((d) => d && visible(d)) || anchor)

const canPrev = computed(() => !props.min || monthOf(props.min) < view.value)
const canNext = computed(() => !props.max || monthOf(props.max) > shiftMonth(view.value, props.months - 1))

const picking = computed(() => props.range && !!props.start && !props.end)

// Выделение: выбранный период или, пока выбран только первый день, период до дня под курсором.
const span = computed(() => {
  const a = props.start
  if (!a) return null
  const b = props.end || (picking.value && hoverDay.value) || a
  return a <= b ? [a, b] : [b, a]
})

const grids = computed(() =>
  Array.from({ length: props.months }, (_, k) => {
    const key = shiftMonth(view.value, k)
    const [y, m] = key.split('-').map(Number)
    const lead = weekday(`${key}-01`)
    const count = new Date(y, m, 0).getDate()
    const cells = Array(lead).fill(null)
    for (let day = 1; day <= count; day += 1) {
      const iso = `${key}-${String(day).padStart(2, '0')}`
      cells.push({ day, iso, label: spokenDay(iso), off: (props.min && iso < props.min) || (props.max && iso > props.max) })
    }
    while (cells.length % 7) cells.push(null)
    const weeks = []
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7))
    return { key, title: `${MONTHS[m - 1]} ${y}`, weeks }
  }),
)

const isEdge = (iso) => !!span.value && (iso === span.value[0] || iso === span.value[1])

function dayClass(d) {
  const s = span.value
  const inside = s && d.iso >= s[0] && d.iso <= s[1]
  return {
    'is-off': d.off,
    'is-today': d.iso === today,
    'is-start': s && d.iso === s[0],
    'is-end': s && d.iso === s[1],
    'is-in': inside && s[0] !== s[1],
    'is-preview': inside && !props.end && picking.value,
  }
}

function pick(d) {
  if (d.off) return
  hoverDay.value = ''
  emit('pick', d.iso)
}

function shiftView(n) {
  view.value = shiftMonth(view.value, n)
  // Фокус остаётся в видимых месяцах — иначе Tab увёл бы его на скрытый день.
  if (!visible(focusDay.value)) focusDay.value = clampIso(`${view.value}-01`, props.min, props.max)
}

async function moveTo(iso) {
  const next = clampIso(iso, props.min, props.max)
  if (monthOf(next) < view.value) view.value = monthOf(next)
  else if (!visible(next)) view.value = shiftMonth(monthOf(next), 1 - props.months)
  focusDay.value = next
  if (picking.value) hoverDay.value = next
  await nextTick()
  focus()
}

function onKey(event) {
  const d = focusDay.value
  const steps = {
    ArrowLeft: () => addDays(d, -1),
    ArrowRight: () => addDays(d, 1),
    ArrowUp: () => addDays(d, -7),
    ArrowDown: () => addDays(d, 7),
    PageUp: () => addMonths(d, event.shiftKey ? -12 : -1),
    PageDown: () => addMonths(d, event.shiftKey ? 12 : 1),
    Home: () => addDays(d, -weekday(d)),
    End: () => addDays(d, 6 - weekday(d)),
  }
  const step = steps[event.key]
  if (!step) return
  event.preventDefault()
  moveTo(step())
}

function focus() {
  rootEl.value?.querySelector(`[data-iso="${focusDay.value}"]`)?.focus()
}

defineExpose({ focus })
</script>
