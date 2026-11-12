<template>
  <div class="mgrid">
    <div v-for="row in ROWS" :key="row.label" class="mgrid__row" role="group" :aria-label="row.label">
      <h3 class="mgrid__cap">{{ row.label }}</h3>
      <div class="mgrid__tiles">
        <template v-for="m in row.metrics" :key="m.key">
          <!-- Без Метрики визиты всегда 0 — вместо нуля одна строка, что сделать. -->
          <p v-if="m.key === 'visits' && !metrika" class="mgrid__na">
            <slot name="no-metrika">Подключите Метрику, чтобы видеть визиты</slot>
          </p>
          <component
            :is="CHART_METRICS.includes(m.key) ? 'button' : 'div'"
            v-else
            :type="CHART_METRICS.includes(m.key) ? 'button' : undefined"
            class="metric-grid__item"
            :class="{ active: modelValue === m.key, 'is-static': !CHART_METRICS.includes(m.key) }"
            :aria-pressed="CHART_METRICS.includes(m.key) ? modelValue === m.key : undefined"
            @click="CHART_METRICS.includes(m.key) && $emit('update:modelValue', m.key)"
          >
            <small>{{ m.label }}</small>
            <strong>{{ m.fmt(totals[m.key]) }}</strong>
            <span class="metric-grid__cmp">
              <DeltaChip v-if="compare?.[m.key] != null" :value="compare[m.key]" :good="m.good || ''" :versus="versus" />
              <small v-if="previous?.[m.key] != null">было {{ m.fmt(previous[m.key]) }}</small>
            </span>
            <small v-if="m.key === 'visits' && totals.bounce_rate != null" class="mgrid__extra">
              отказы {{ pct(totals.bounce_rate) }}
            </small>
          </component>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import DeltaChip from './DeltaChip.vue'
import { pct } from '../utils/format'
import { CHART_METRICS, METRIC_BY_KEY } from '../views/workspace/metrics'

/**
 * Итоги периода двумя рядами по пять: «Реклама» — что купили, «Результат» — что получили. Плитка с графиком
 * по дням — кнопка: выбирает метрику графика (v-model). metrika=false — визитов нет, вместо плитки подсказка.
 */
defineProps({
  totals: { type: Object, required: true },
  previous: { type: Object, default: null },
  compare: { type: Object, default: null },
  modelValue: { type: String, default: '' },
  /** С чем сравнили — для подсказки к изменению: «к прошлым 7 дням». */
  versus: { type: String, default: 'к прошлому периоду' },
  metrika: { type: Boolean, default: true },
})
defineEmits(['update:modelValue'])

const ROWS = [
  { label: 'Реклама', keys: ['cost_kopecks', 'impressions', 'clicks', 'ctr', 'cpc_kopecks'] },
  { label: 'Результат', keys: ['conversions', 'cpa_kopecks', 'revenue_kopecks', 'drr', 'visits'] },
].map((row) => ({ label: row.label, metrics: row.keys.map((key) => METRIC_BY_KEY[key]) }))
</script>

<style scoped>
.mgrid {
  container-type: inline-size;
  display: grid;
  gap: 0.9rem;
  margin-bottom: 1rem;
}

.mgrid__cap {
  margin: 0 0 0.4rem;
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.mgrid__tiles {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.6rem;
}

.mgrid__tiles .metric-grid__item {
  min-width: 0;
  padding: 0.8rem 0.9rem;
}

.mgrid__tiles .metric-grid__item strong {
  font-size: clamp(1.05rem, 2.3cqi, 1.4rem);
  white-space: nowrap;
}

.mgrid__extra {
  font-size: 0.76rem;
}

.mgrid__na {
  display: flex;
  align-items: center;
  margin: 0;
  padding: 0.8rem 0.9rem;
  border: 1px dashed var(--line-2);
  border-radius: 18px;
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1.35;
}

.mgrid__na :slotted(a) {
  color: var(--ink);
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 2px;
}

@container (max-width: 760px) {
  .mgrid__tiles {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@container (max-width: 460px) {
  .mgrid__tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
