<template>
  <div class="aib-table">
    <div class="aib-table__scroll">
      <table>
        <thead>
          <tr>
            <th scope="col" :aria-sort="ariaSort(-1)">
              <button type="button" class="aib-sort" @click="sortBy(-1)">
                {{ block.first }}
                <component :is="sortIcon(-1)" aria-hidden="true" />
              </button>
            </th>
            <th v-for="(col, i) in block.columns" :key="col.metric" scope="col" :aria-sort="ariaSort(i)">
              <button type="button" class="aib-sort" @click="sortBy(i)">
                {{ col.label }}
                <component :is="sortIcon(i)" aria-hidden="true" />
              </button>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in shown" :key="r.key">
            <th scope="row">
              <RouterLink v-if="campaignLink(r)" :to="campaignLink(r)">{{ rowLabel(r) }}</RouterLink>
              <RouterLink v-else-if="r.workspace_id && block.by === 'projects'" :to="`/w/${r.workspace_id}/summary`">
                {{ rowLabel(r) }}
              </RouterLink>
              <template v-else>{{ rowLabel(r) }}</template>
            </th>
            <td v-for="(col, i) in block.columns" :key="col.metric">{{ blockValue(r.values[i], col.unit) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <button v-if="rows.length > LIMIT" type="button" class="btn btn--ghost btn--sm aib-table__all" @click="all = !all">
      {{ all ? 'Свернуть' : `Показать все · ${rows.length}` }}
    </button>
  </div>
</template>

<script setup>
import { ArrowDown, ArrowUp, ArrowUpDown } from '@lucide/vue'
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { formatDate, niceQuotes } from '../../../utils/format'
import { blockValue, campaignLink } from './blockFormat'

/**
 * Таблица из блока ИИ: сортировка по столбцу (числа — сначала большие, ещё нажатие — наоборот), первые 10 строк
 * и «Показать все». Первый столбец закреплён, остальное листается вбок внутри рамки.
 */
const props = defineProps({
  block: { type: Object, required: true },
})

const LIMIT = 10
const all = ref(false)
// -1 — первый столбец (название или день); null — порядок, в котором прислал сервис.
const sortCol = ref(props.block.sort ? props.block.columns.findIndex((c) => c.metric === props.block.sort) : null)
const desc = ref(true)

const rowLabel = (r) => (props.block.by === 'days' ? formatDate(r.label) : niceQuotes(r.label))

const rows = computed(() => {
  const list = [...props.block.rows]
  const col = sortCol.value
  if (col === null || col === undefined) return list
  const dir = desc.value ? -1 : 1
  if (col === -1) return list.sort((a, b) => dir * String(a.label).localeCompare(String(b.label), 'ru'))
  return list.sort((a, b) => {
    const x = a.values[col]
    const y = b.values[col]
    if (x === null || x === undefined) return 1
    if (y === null || y === undefined) return -1
    return dir * (x - y)
  })
})
const shown = computed(() => (all.value ? rows.value : rows.value.slice(0, LIMIT)))

function sortBy(col) {
  if (sortCol.value === col) desc.value = !desc.value
  else {
    sortCol.value = col
    // Названия и дни — по порядку, числа — сначала большие.
    desc.value = col !== -1
  }
}
const ariaSort = (col) => (sortCol.value === col ? (desc.value ? 'descending' : 'ascending') : 'none')
const sortIcon = (col) => (sortCol.value !== col ? ArrowUpDown : desc.value ? ArrowDown : ArrowUp)
</script>
