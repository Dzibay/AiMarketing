<template>
  <!-- Кнопка периода и то, что рядом (подсказка «?»), не разрываются при переносе строки. -->
  <div class="drange">
    <span class="ui-select drange__pick" :class="{ 'is-open': open }">
      <button
        ref="trigger"
        class="ui-select__btn drange__btn"
        type="button"
        aria-haspopup="dialog"
        :aria-expanded="open"
        :aria-label="`Период: ${presetLabel ? `${presetLabel}, ` : ''}${spokenRange}`"
        :disabled="disabled"
        @click="toggle"
        @keydown.down.prevent="openPanel"
      >
        <CalendarDays class="drange__icon" aria-hidden="true" />
        <span class="ui-select__value">
          <template v-if="presetLabel">
            {{ presetLabel }}<span class="drange__dates"> · {{ rangeText }}</span>
          </template>
          <template v-else>{{ rangeText }}</template>
        </span>
        <ChevronDown class="ui-select__chev" aria-hidden="true" />
      </button>
    </span>
    <slot />

    <Teleport to="body">
      <div v-if="open && sheet" class="ui-scrim" aria-hidden="true"></div>
      <div
        v-if="open"
        ref="panel"
        class="ui-pop drp"
        :class="{ 'is-sheet': sheet }"
        role="dialog"
        aria-label="Период отчёта"
        :style="sheet ? null : style"
        @keydown.esc.stop.prevent="close({ focus: true })"
        @focusout="onFocusOut"
      >
        <div class="drp__presets" role="group" aria-label="Быстрый период">
          <button
            v-for="p in presets"
            :key="p"
            class="drp__preset"
            :class="{ 'is-current': preset === p }"
            type="button"
            :aria-pressed="preset === p"
            @click="pickPreset(p)"
          >
            {{ labelOf(p) }}
          </button>
          <button
            class="drp__preset"
            :class="{ 'is-current': preset == null }"
            type="button"
            :aria-pressed="preset == null"
            @click="customRange"
          >
            Свой период
          </button>
        </div>
        <div class="drp__main">
          <UiCalendar
            ref="cal"
            range
            :start="draft.start"
            :end="draft.end"
            :min="min"
            :max="max"
            :months="sheet ? 1 : 2"
            @pick="pickDay"
          />
          <div class="drp__foot">
            <p class="drp__hint" aria-live="polite">{{ hint }}</p>
            <button class="btn btn--ghost btn--sm" type="button" @click="close({ focus: true })">Отмена</button>
            <button class="btn btn--sm" type="button" :disabled="!draft.start" @click="applyDraft">Применить</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, nextTick, reactive, ref } from 'vue'
import { CalendarDays, ChevronDown } from '@lucide/vue'
import { usePopover } from '../../composables/usePopover'
import { formatRange, lastDays, monthToDate } from '../../utils/format'
import UiCalendar from './UiCalendar.vue'
import { addDays, previousMonth, spokenDay, todayIso } from './calendar'

/**
 * Период отчёта: кнопка «7 дней · 6–12 нояб.», по нажатию — быстрые периоды и календарь на два месяца
 * (на телефоне — панель снизу и один месяц). Быстрый период применяется сразу; свой — вторым днём
 * (или «Применить», если нужен один день). Ничего не меняет сам: отдаёт change({ date_from, date_to, preset }),
 * preset = null — свои даты. Будущих дней в отчёте нет, поэтому позже max выбрать нельзя.
 */
const props = defineProps({
  from: { type: String, required: true },
  to: { type: String, required: true },
  preset: { type: [Number, String], default: null },
  presets: { type: Array, default: () => ['yesterday', 7, 14, 30, 'month', 'prev_month'] },
  min: { type: String, default: '' },
  max: { type: String, default: () => todayIso() },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['change'])

const LABELS = { yesterday: 'Вчера', month: 'Этот месяц', prev_month: 'Прошлый месяц' }
const labelOf = (p) => LABELS[p] ?? `${p} дней`

const trigger = ref(null)
const panel = ref(null)
const cal = ref(null)
const sheet = ref(false)
const draft = reactive({ start: '', end: '' })
const { open, style, show, close } = usePopover(trigger, panel, { align: 'start' })

const presetLabel = computed(() => (props.preset == null ? '' : labelOf(props.preset)))
const rangeText = computed(() => formatRange(props.from, props.to))
const spokenRange = computed(() => {
  if (props.from === props.to) return spokenDay(props.from)
  const day = (iso) => spokenDay(iso, { weekday: false })
  return `с ${day(props.from)} по ${day(props.to)}`
})

const hint = computed(() => {
  if (!draft.start) return 'Выберите первый день'
  if (!draft.end) return `С ${formatRange(draft.start)} — теперь последний день`
  return formatRange(draft.start, draft.end)
})

function rangeOf(p) {
  if (p === 'yesterday') {
    const day = addDays(todayIso(), -1)
    return { date_from: day, date_to: day }
  }
  if (p === 'month') return monthToDate()
  if (p === 'prev_month') return previousMonth()
  return lastDays(p)
}

async function openPanel() {
  draft.start = props.from
  draft.end = props.to
  sheet.value = window.matchMedia('(max-width: 640px)').matches
  await show()
  await nextTick()
  cal.value?.focus()
}

function toggle() {
  if (open.value) close()
  else openPanel()
}

function pickPreset(p) {
  close({ focus: true })
  emit('change', { ...rangeOf(p), preset: p })
}

function customRange() {
  draft.start = ''
  draft.end = ''
  cal.value?.focus()
}

function apply(from, to) {
  const [a, b] = from <= to ? [from, to] : [to, from]
  close({ focus: true })
  if (a === props.from && b === props.to) return
  emit('change', { date_from: a, date_to: b, preset: null })
}

// Первый клик начинает период, второй — заканчивает и применяет.
function pickDay(iso) {
  if (!draft.start || draft.end) {
    draft.start = iso
    draft.end = ''
  } else {
    apply(draft.start, iso)
  }
}

function applyDraft() {
  if (draft.start) apply(draft.start, draft.end || draft.start)
}

// Фокус ушёл из панели (Tab за последнюю кнопку) — панель закрывается, как меню.
function onFocusOut(event) {
  const to = event.relatedTarget
  if (to && !panel.value?.contains(to) && to !== trigger.value) close()
}
</script>
