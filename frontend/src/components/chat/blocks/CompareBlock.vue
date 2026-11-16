<template>
  <table class="aib-compare">
    <thead>
      <tr>
        <th scope="col"><span class="sr-only">Метрика</span></th>
        <th scope="col">{{ formatRange(block.prev_from, block.prev_to) }}</th>
        <th scope="col">{{ formatRange(block.date_from, block.date_to) }}</th>
        <th scope="col"><span class="sr-only">Изменение</span></th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="r in block.rows" :key="r.metric">
        <th scope="row">{{ r.label }}</th>
        <td class="aib__muted">{{ blockValue(r.prev, r.unit) }}</td>
        <td>
          <strong>{{ blockValue(r.cur, r.unit) }}</strong>
        </td>
        <td>
          <DeltaChip v-if="r.delta != null" :value="r.delta" :good="r.good" :versus="blockVersus(block)" />
        </td>
      </tr>
    </tbody>
  </table>
</template>

<script setup>
import { formatRange } from '../../../utils/format'
import DeltaChip from '../../DeltaChip.vue'
import { blockValue, blockVersus } from './blockFormat'

/** Сравнение периодов: было, стало и изменение — строкой на метрику. */
defineProps({
  block: { type: Object, required: true },
})
</script>
