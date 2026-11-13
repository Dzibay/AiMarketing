<template>
  <ul class="plist">
    <li
      v-for="(p, i) in problems"
      :key="`${p.kind}-${p.workspace_id}-${p.account_id}-${p.campaign_row_id}-${i}`"
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
      <RouterLink v-if="problemLink(p)" class="btn btn--ghost btn--sm plist__act" :to="problemLink(p).to">
        {{ problemLink(p).label }}
      </RouterLink>
    </li>
  </ul>
</template>

<script setup>
import { RouterLink } from 'vue-router'
import { Info, OctagonAlert, TriangleAlert } from '@lucide/vue'
import { errorDetail } from '../api/web'
import { niceQuotes, problemLink, SEVERITY_LABELS } from '../utils/format'

/**
 * Список «где горит» экспертного режима: иконка серьёзности, причина одной строкой, под ней — проект,
 * объект и пояснение тоже одной строкой (целиком — в подсказке), справа одно действие.
 */
const props = defineProps({
  problems: { type: Array, default: () => [] },
  /** На общей главной — с названием проекта (или клиента, если проблема на весь его бюджет). */
  showProject: Boolean,
})

const ICONS = { critical: OctagonAlert, warning: TriangleAlert, info: Info }

function whereOf(p) {
  const project = props.showProject ? p.workspace_name || p.client_name : ''
  // Кабинет часто назван как проект — второй раз то же имя не пишем.
  const object = p.object_name !== project ? p.object_name : ''
  return [niceQuotes(project), object, p.detail ? errorDetail(p.detail) : ''].filter(Boolean).join(' · ')
}
</script>
