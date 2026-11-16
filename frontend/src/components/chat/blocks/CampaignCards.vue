<template>
  <div class="aib-cards">
    <component
      :is="campaignLink(c) ? RouterLink : 'div'"
      v-for="c in block.items"
      :key="c.campaign_row_id"
      class="aib-card"
      :to="campaignLink(c) || undefined"
    >
      <div class="aib-card__head">
        <strong class="aib-card__name" v-tip.truncate="niceQuotes(c.name)">{{ niceQuotes(c.name) }}</strong>
        <ChevronRight v-if="campaignLink(c)" class="aib-card__go" aria-hidden="true" />
      </div>
      <div class="aib-card__sub">
        <UiStatus v-bind="campaignStatus(c)" />
        <span v-if="c.project" class="aib__muted">{{ niceQuotes(c.project) }}</span>
      </div>
      <dl class="aib-card__nums">
        <div v-for="v in c.values" :key="v.metric">
          <dt>{{ v.label }}</dt>
          <dd>{{ blockValue(v.value, v.unit) }}</dd>
        </div>
      </dl>
    </component>
    <p v-if="block.more" class="aib__muted aib-cards__more">И ещё {{ block.more }} {{ plural(block.more, 'кампания', 'кампании', 'кампаний') }}</p>
  </div>
</template>

<script setup>
import { ChevronRight } from '@lucide/vue'
import { RouterLink } from 'vue-router'
import { campaignStatus, niceQuotes, plural } from '../../../utils/format'
import UiStatus from '../../ui/UiStatus.vue'
import { blockValue, campaignLink } from './blockFormat'

/** Карточки кампаний: статус, три числа за период и переход в кампанию. */
defineProps({
  block: { type: Object, required: true },
})
</script>
