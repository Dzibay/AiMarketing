<template>
  <div class="drange">
    <div class="seg" role="group" aria-label="Быстрый период">
      <button
        v-for="p in presets"
        :key="p"
        type="button"
        :class="{ active: preset === p }"
        :aria-pressed="preset === p"
        :disabled="disabled"
        @click="pick(p)"
      >
        {{ p === 'month' ? 'Месяц' : `${p} дн.` }}
      </button>
    </div>
    <!-- Поле дат и то, что рядом (подсказка «?»), не разрываются при переносе строки. -->
    <div class="drange__end">
      <div class="drange__dates" :class="{ 'is-custom': preset == null }" role="group" aria-label="Свой период">
        <input
          v-model="draftFrom"
          type="date"
          aria-label="С"
          :max="draftTo || undefined"
          :disabled="disabled"
          @change="commit"
        />
        <span aria-hidden="true">—</span>
        <input
          v-model="draftTo"
          type="date"
          aria-label="По"
          :min="draftFrom || undefined"
          :disabled="disabled"
          @change="commit"
        />
      </div>
      <slot />
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { lastDays, monthToDate } from '../../utils/format'

/**
 * Период отчёта: быстрые кнопки (7, 14, 30 дней, «Месяц» — с начала месяца) и свои даты «с — по».
 * Ничего не меняет сам: отдаёт change({ date_from, date_to, preset }), preset = null — свои даты.
 */
const props = defineProps({
  from: { type: String, required: true },
  to: { type: String, required: true },
  preset: { type: [Number, String], default: null },
  presets: { type: Array, default: () => [7, 14, 30, 'month'] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['change'])

const draftFrom = ref(props.from)
const draftTo = ref(props.to)
watch(
  () => [props.from, props.to],
  ([from, to]) => {
    draftFrom.value = from
    draftTo.value = to
  },
)

function pick(p) {
  emit('change', { ...(p === 'month' ? monthToDate() : lastDays(p)), preset: p })
}

function commit() {
  // Пустая или перевёрнутая дата — ждём, пока поправят, отчёт не перезагружаем.
  if (!draftFrom.value || !draftTo.value || draftFrom.value > draftTo.value) return
  if (draftFrom.value === props.from && draftTo.value === props.to) return
  emit('change', { date_from: draftFrom.value, date_to: draftTo.value, preset: null })
}
</script>

<style scoped>
.drange {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.drange__end {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

.drange__dates {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0 0.35rem;
  border: 1px solid var(--line);
  border-radius: var(--pill);
  background: var(--surface);
  color: var(--muted);
}

.drange__dates.is-custom {
  border-color: var(--ink);
}

.drange__dates input {
  height: 34px;
  padding: 0 0.35rem;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 0.86rem;
  font-variant-numeric: tabular-nums;
}

.drange__dates input:focus {
  outline: none;
}

.drange__dates:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.drange__dates input::-webkit-calendar-picker-indicator {
  opacity: 0.55;
  cursor: pointer;
}
</style>
