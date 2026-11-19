<template>
  <ul class="plist">
    <li
      v-for="(p, i) in shown"
      :key="`${p.key || p.kind}-${p.workspace_id}-${p.account_id}-${p.campaign_row_id}-${i}`"
      class="plist__row"
      :class="`is-${p.severity || 'info'}`"
    >
      <span class="plist__sev" role="img" :aria-label="SEVERITY_LABELS[p.severity]" v-tip="SEVERITY_LABELS[p.severity]">
        <component :is="ICONS[p.severity] || Info" aria-hidden="true" />
      </span>
      <div class="plist__text">
        <span class="plist__title" v-tip.truncate="p.title">{{ p.title }}</span>
        <small v-if="whereOf(p)" class="plist__where" v-tip.truncate="whereOf(p)">{{ whereOf(p) }}</small>
      </div>
      <a
        v-if="problemLink(p)?.external"
        class="btn btn--ghost btn--sm plist__act"
        :href="problemLink(p).to"
        target="_blank"
        rel="noopener"
      >
        {{ problemLink(p).label }}
      </a>
      <RouterLink v-else-if="problemLink(p)" class="btn btn--ghost btn--sm plist__act" :to="problemLink(p).to">
        {{ problemLink(p).label }}
      </RouterLink>
      <span class="plist__more"><SnoozeMenu :problem="p" @snoozed="onSnoozed" /></span>
    </li>
  </ul>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { Info, OctagonAlert, TriangleAlert } from '@lucide/vue'
import SnoozeMenu from './SnoozeMenu.vue'
import { errorDetail } from '../api/web'
import { useSnoozed } from '../composables/useSnoozed'
import { niceQuotes, problemLink, SEVERITY_LABELS } from '../utils/format'

/**
 * Список «где горит» экспертного режима: иконка серьёзности, причина одной строкой, под ней — проект,
 * объект и пояснение тоже одной строкой (целиком — в подсказке), справа одно действие и «⋯» — скрыть
 * (на 7 дней, «так задумано», весь тип). @changed — скрыли, пора перечитать сводку; строку «Скрыто»
 * страница ставит сама (SnoozedList), чтобы она была и когда список пуст.
 */
const props = defineProps({
  problems: { type: Array, default: () => [] },
  /** На общей главной — с названием проекта (или клиента, если проблема на весь его бюджет). */
  showProject: Boolean,
})
const emit = defineEmits(['changed'])

const ICONS = { critical: OctagonAlert, warning: TriangleAlert, info: Info }
const { mark, isGone } = useSnoozed(() => props.problems)
const shown = computed(() => props.problems.filter((p) => !isGone(p)))

function onSnoozed(g) {
  mark(g)
  emit('changed')
}

function whereOf(p) {
  const project = props.showProject ? p.workspace_name || p.client_name : ''
  // Кабинет часто назван как проект — второй раз то же имя не пишем.
  const object = p.object_name !== project ? p.object_name : ''
  return [niceQuotes(project), object, p.detail ? errorDetail(p.detail) : ''].filter(Boolean).join(' · ')
}
</script>

<style scoped>
.plist__more {
  display: flex;
  flex: none;
}
.plist__more :deep(.ui-menu__btn:not(.is-open):not(:hover)) {
  border-color: transparent;
  background: none;
  color: var(--faint);
}
/* Телефон: «⋯» остаётся справа в строке действия. */
@media (max-width: 640px) {
  .plist__more {
    margin-left: auto;
  }
}
</style>
