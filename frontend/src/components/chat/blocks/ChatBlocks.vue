<template>
  <div class="aib-list">
    <section v-for="(b, i) in blocks" :key="i" class="aib" :class="`aib--${b.type}`">
      <header class="aib__head">
        <strong>{{ niceQuotes(b.title) }}</strong>
        <span class="aib__muted">{{ blockPeriod(b) }}</span>
      </header>
      <component :is="VIEWS[b.type]" v-if="VIEWS[b.type]" :block="b" />
    </section>
  </div>
</template>

<script setup>
import { niceQuotes } from '../../../utils/format'
import CampaignCards from './CampaignCards.vue'
import ChartBlock from './ChartBlock.vue'
import CompareBlock from './CompareBlock.vue'
import DataTable from './DataTable.vue'
import { blockPeriod } from './blockFormat'

/** Наглядные блоки ответа ИИ (meta.blocks): числа в них посчитал сервис, ИИ только выбрал, что показать. */
defineProps({
  blocks: { type: Array, required: true },
})

const VIEWS = { chart: ChartBlock, compare: CompareBlock, campaigns: CampaignCards, table: DataTable }
</script>
