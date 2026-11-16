<template>
  <div class="aib-chart">
    <div class="aib-chart__total">
      <strong>{{ blockValue(block.total, block.unit) }}</strong>
      <DeltaChip v-if="block.delta != null" :value="block.delta" :good="block.good" :versus="blockVersus(block)" />
      <span v-if="block.prev_points.length" class="aib__muted">было {{ blockValue(block.prev_total, block.unit) }}</span>
    </div>
    <TrendChart
      :points="block.points"
      :prev-points="block.prev_points"
      value-key="value"
      :label="block.label"
      :format-value="(v) => blockValue(v, block.unit)"
      :height="180"
    />
  </div>
</template>

<script setup>
import DeltaChip from '../../DeltaChip.vue'
import TrendChart from '../../TrendChart.vue'
import { blockValue, blockVersus } from './blockFormat'

/** График метрики по дням; пунктир — прошлый период той же длины. Итог за период — сверху, как плитка отчёта. */
defineProps({
  block: { type: Object, required: true },
})
</script>
