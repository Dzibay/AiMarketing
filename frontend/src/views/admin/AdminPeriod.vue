<template>
  <div class="adm-period">
    <UiDateRange
      :from="period?.from || today"
      :to="period?.to || today"
      :max="today"
      :preset="preset"
      :presets="PERIOD_PRESETS"
      :labels="PERIOD_LABELS"
      :resolve="resolve"
      :disabled="disabled"
      @change="$emit('period', $event)"
    />
    <div class="seg seg--sm" role="group" aria-label="Шаг графиков">
      <button
        v-for="s in STEPS"
        :key="s.value"
        type="button"
        :class="{ active: step === s.value }"
        :aria-pressed="step === s.value"
        :disabled="disabled"
        @click="$emit('step', s.value)"
      >
        {{ s.label }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import UiDateRange from '../../components/ui/UiDateRange.vue'
import { addDays, todayIso } from '../../components/ui/calendar'
import { PERIOD_LABELS, PERIOD_PRESETS, STEPS } from './useAdminPeriod'

/**
 * Период дашборда и шаг графиков. Даты на кнопке — те, что посчитал сервер (по Москве, включая сегодня);
 * быстрые периоды — скользящие окна по сегодня. Сам ничего не меняет: period(change), step(значение).
 */
const props = defineProps({
  /** period из ответа сервера: { preset, from, to, step }; до ответа — null. */
  period: { type: Object, default: null },
  /** Что сейчас в адресе: { p } или { from, to } — чтобы кнопка не мигала до ответа. */
  query: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})
defineEmits(['period', 'step'])

const today = todayIso()
const DAYS = { day: 1, '7d': 7, '30d': 30, '90d': 90, '180d': 180, '365d': 365 }

const preset = computed(() => (props.query.from ? null : props.query.p))
const step = computed(() => props.query.step || props.period?.step || '')

// Даты быстрого периода для календаря; на сервер уходит только ключ, даты он считает сам.
function resolve(p) {
  if (p === 'all') return { date_from: props.period?.preset === 'all' ? props.period.from : today, date_to: today }
  return { date_from: addDays(today, -((DAYS[p] || 30) - 1)), date_to: today }
}
</script>
