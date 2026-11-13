<template>
  <div class="cgroups">
    <section v-for="g in groups" :key="g.id" class="cgroup">
      <h2 class="cgroup__h">
        <span class="st" :class="`st--${g.id}`">
          <component :is="ICONS[g.id]" :size="14" aria-hidden="true" />{{ groupLabel(g) }}
        </span>
        <span class="cgroup__n">{{ g.items.length }}</span>
      </h2>
      <div class="ccard">
        <div
          v-for="c in g.items"
          :key="c.campaign_row_id"
          class="crow"
          :class="{ 'crow--pick': selected, 'is-sel': selected?.has(c.campaign_row_id) }"
        >
          <UiCheckbox
            v-if="selected"
            class="crow__pick"
            :checked="selected.has(c.campaign_row_id)"
            :aria-label="`Выбрать «${c.name}»`"
            @change="$emit('pick', c.campaign_row_id, $event.target.checked)"
          />
          <div class="crow__name">
            <RouterLink :to="c.link">{{ c.name }}</RouterLink>
            <small>{{ c.where }}</small>
          </div>
          <div class="crow__health">{{ c.health.text }}</div>
          <div class="crow__num num">
            <b>{{ rub(c.cost_kopecks) }}</b>
            <small>{{ num(c.conversions || 0) }} {{ leadWord(c.conversions || 0) }}</small>
          </div>
          <UiSwitch
            v-if="canToggle(c)"
            v-tip="c.state === 'ON' ? 'Остановить показы' : 'Запустить показы'"
            :checked="c.state === 'ON'"
            :disabled="busy"
            :aria-label="`${c.state === 'ON' ? 'Остановить' : 'Запустить'} «${c.name}»`"
            @click.prevent="$emit('toggle', c)"
          />
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
import UiCheckbox from './ui/UiCheckbox.vue'
import UiSwitch from './ui/UiSwitch.vue'
import { HEALTH_GROUPS, campaignHealth, leadWord } from '../utils/campaignHealth'
import { num, plural, rub } from '../utils/format'

/**
 * Кампании простого режима: группы «что с ними» вместо таблицы. Каждая строка — название, вывод словами,
 * расход с заявками и выключатель показов. kpiOf(c) отдаёт цель проекта кампании. Выключатель только
 * сообщает toggle: сам он не переключается — страница спрашивает подтверждение и перечитывает список.
 */
const props = defineProps({
  rows: { type: Array, required: true },
  kpiOf: { type: Function, required: true },
  busy: { type: Boolean, default: false },
  // Set выбранных campaign_row_id — тогда у строк галочки (вопрос ИИ про выбранные).
  selected: { type: Set, default: null },
  // Период статистики в днях — для подписи «Расход без заявок за 7 дней».
  days: { type: Number, default: 7 },
})
defineEmits(['toggle', 'pick'])

const ICONS = { bad: OctagonAlert, warn: TriangleAlert, ok: CircleCheck, idle: CircleDashed, off: CirclePause }

function groupLabel(g) {
  return g.id === 'bad' ? `${g.label} за ${props.days} ${plural(props.days, 'день', 'дня', 'дней')}` : g.label
}

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
