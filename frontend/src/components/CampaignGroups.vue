<template>
  <div class="cgroups">
    <section v-for="g in groups" :key="g.id" class="cgroup">
      <h2 class="cgroup__h">
        <span class="st" :class="`st--${g.id}`">
          <component :is="ICONS[g.id]" :size="14" aria-hidden="true" />{{ g.label }}
        </span>
        <span class="cgroup__n">{{ g.items.length }}</span>
      </h2>
      <div class="ccard">
        <div v-for="c in g.items" :key="c.campaign_row_id" class="crow">
          <div class="crow__name">
            <RouterLink :to="c.link">{{ c.name }}</RouterLink>
            <small>{{ c.where }}</small>
          </div>
          <div class="crow__health">{{ c.health.text }}</div>
          <div class="crow__num num">
            <b>{{ rub(c.cost_kopecks) }}</b>
            <small>{{ num(c.conversions || 0) }} {{ convWord(c.conversions || 0) }}</small>
          </div>
          <label v-if="canToggle(c)" class="switch" :title="c.state === 'ON' ? 'Остановить показы' : 'Запустить показы'">
            <input
              type="checkbox"
              :checked="c.state === 'ON'"
              :disabled="busy"
              :aria-label="`${c.state === 'ON' ? 'Остановить' : 'Запустить'} «${c.name}»`"
              @change="$emit('toggle', c)"
            />
          </label>
          <span v-else></span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { TriangleAlert, CircleCheck, CircleDashed, CirclePause, OctagonAlert } from '@lucide/vue'
import { HEALTH_GROUPS, campaignHealth, convWord } from '../utils/campaignHealth'
import { num, rub } from '../utils/format'

/**
 * Кампании простого режима: группы «что с ними» вместо таблицы. Каждая строка — название, вывод словами,
 * расход с конверсиями и выключатель показов. kpiOf(c) отдаёт цель проекта кампании.
 */
const props = defineProps({
  rows: { type: Array, required: true },
  kpiOf: { type: Function, required: true },
  busy: { type: Boolean, default: false },
})
defineEmits(['toggle'])

const ICONS = { bad: OctagonAlert, warn: TriangleAlert, ok: CircleCheck, idle: CircleDashed, off: CirclePause }

const groups = computed(() => {
  const withHealth = props.rows.map((c) => ({ ...c, health: campaignHealth(c, props.kpiOf(c)) }))
  return HEALTH_GROUPS.map((g) => ({ ...g, items: withHealth.filter((c) => c.health.id === g.id) })).filter(
    (g) => g.items.length,
  )
})

// Черновик Директ не запустит, пока он не прошёл модерацию; архив — только из экспертного режима.
function canToggle(c) {
  return c.state !== 'ARCHIVED' && (c.state === 'ON' || c.status !== 'DRAFT')
}
</script>
