<template>
  <ul class="plist">
    <template v-for="(row, i) in rows" :key="row.group ? row.key : `${row.p.key || row.p.kind}-${row.p.workspace_id}-${row.p.account_id}-${row.p.campaign_row_id}-${i}`">
      <!-- Одинаковые проблемы разных клиентов — одной строкой, раскрыть — список. -->
      <li v-if="row.group" class="plist__row plist__row--group" :class="`is-${row.g.severity || 'info'}`">
        <span class="plist__sev" role="img" :aria-label="SEVERITY_LABELS[row.g.severity]" v-tip="SEVERITY_LABELS[row.g.severity]">
          <component :is="ICONS[row.g.severity] || Info" aria-hidden="true" />
        </span>
        <div class="plist__text">
          <span class="plist__title">
            <span class="plist__t" v-tip.truncate="row.g.title">{{ row.g.title }}</span>
            <b v-if="row.g.is_new" class="plist__new">новое</b>
          </span>
          <small v-if="row.g.where" class="plist__where" v-tip.truncate="row.g.where">{{ row.g.where }}</small>
        </div>
        <button
          class="btn btn--ghost btn--sm plist__act"
          type="button"
          :aria-expanded="open.has(row.g.key)"
          @click="toggle(row.g.key)"
        >
          {{ open.has(row.g.key) ? 'Свернуть' : 'Показать все' }}
          <ChevronDown class="plist__chev" :class="{ 'is-open': open.has(row.g.key) }" aria-hidden="true" />
        </button>
      </li>
      <li v-else class="plist__row" :class="[`is-${row.p.severity || 'info'}`, { 'plist__row--child': row.child }]">
        <span class="plist__sev" role="img" :aria-label="SEVERITY_LABELS[row.p.severity]" v-tip="SEVERITY_LABELS[row.p.severity]">
          <component :is="ICONS[row.p.severity] || Info" aria-hidden="true" />
        </span>
        <div class="plist__text">
          <span class="plist__title">
            <span class="plist__t" v-tip.truncate="row.p.title">{{ row.p.title }}</span>
            <b v-if="row.p.is_new" class="plist__new">новое</b>
          </span>
          <small v-if="whereOf(row.p)" class="plist__where" v-tip.truncate="whereOf(row.p)">{{ whereOf(row.p) }}</small>
        </div>
        <a
          v-if="problemLink(row.p)?.external"
          class="btn btn--ghost btn--sm plist__act"
          :href="problemLink(row.p).to"
          target="_blank"
          rel="noopener"
        >
          {{ problemLink(row.p).label }}
        </a>
        <RouterLink v-else-if="problemLink(row.p)" class="btn btn--ghost btn--sm plist__act" :to="problemLink(row.p).to">
          {{ problemLink(row.p).label }}
        </RouterLink>
        <span class="plist__more"><SnoozeMenu :problem="row.p" @snoozed="onSnoozed" /></span>
      </li>
    </template>
  </ul>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { RouterLink } from 'vue-router'
import { ChevronDown, Info, OctagonAlert, TriangleAlert } from '@lucide/vue'
import SnoozeMenu from './SnoozeMenu.vue'
import { errorDetail } from '../api/web'
import { useSnoozed } from '../composables/useSnoozed'
import { niceQuotes, problemLink, SEVERITY_LABELS } from '../utils/format'

/**
 * Список «где горит» экспертного режима: иконка серьёзности, причина одной строкой, под ней — проект,
 * объект и пояснение тоже одной строкой (целиком — в подсказке), справа одно действие и «⋯» — скрыть
 * (на 7 дней, «так задумано», весь тип). @changed — скрыли, пора перечитать сводку; строку «Скрыто»
 * страница ставит сама (SnoozedList), чтобы она была и когда список пуст.
 *
 * В problems могут быть группы из groupProblems() (utils/problemGroups.js): строка «Деньги заканчиваются —
 * 4 клиента» с кнопкой «Показать все». Метка «новое» — у проблем с is_new (утренний разбор на главной).
 */
const props = defineProps({
  problems: { type: Array, default: () => [] },
  /** На общей главной — с названием проекта (или клиента, если проблема на весь его бюджет). */
  showProject: Boolean,
})
const emit = defineEmits(['changed'])

const ICONS = { critical: OctagonAlert, warning: TriangleAlert, info: Info }
const { mark, isGone } = useSnoozed(() => props.problems)
const open = reactive(new Set())

// Плоские строки: проблема, группа и (если раскрыта) её проблемы. Скрытая через «⋯» строка уходит сразу.
const rows = computed(() => {
  const out = []
  for (const item of props.problems) {
    if (!item.group) {
      if (!isGone(item)) out.push({ p: item })
      continue
    }
    const items = item.items.filter((p) => !isGone(p))
    if (items.length === item.items.length) out.push({ group: true, g: item })
    else if (items.length) out.push({ group: true, g: { ...item, items } })
    if (open.has(item.key)) out.push(...items.map((p) => ({ p, child: true })))
  }
  return out
})

function toggle(key) {
  if (open.has(key)) open.delete(key)
  else open.add(key)
}

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
/* Утренний разбор: метка «новое» после причины и раскрытые проблемы группы — с отступом. */
.plist__title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.plist__t {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.plist__new {
  flex: none;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--lime);
  color: #141413;
  font-size: 11px;
  font-weight: 700;
}
.plist__row--child {
  padding-left: 42px;
}
.plist__chev {
  width: 14px;
  height: 14px;
  transition: transform 0.15s;
}
.plist__chev.is-open {
  transform: rotate(180deg);
}
/* Телефон: «⋯» остаётся справа в строке действия. */
@media (max-width: 640px) {
  .plist__more {
    margin-left: auto;
  }
  .plist__title {
    display: flex;
  }
  .plist__t {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    white-space: normal;
  }
  .plist__row--child {
    padding-left: 20px;
  }
}
</style>
