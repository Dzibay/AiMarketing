<template>
  <td v-if="phone" ref="cell" class="tc-toggle">
    <button
      class="tc-more"
      type="button"
      :aria-expanded="open"
      :aria-label="open ? 'Скрыть остальные столбцы' : 'Показать остальные столбцы'"
      @click="open = !open"
    >
      <ChevronDown aria-hidden="true" />
    </button>
  </td>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { ChevronDown } from '@lucide/vue'
import { useViewport } from '../../composables/useViewport'

/**
 * Последняя ячейка строки в таблице-карточках (.tcards): на телефоне — кнопка со стрелкой, которая открывает
 * столбцы, не попавшие в карточку. Открытую строку помечает атрибутом data-open — по нему их показывают стили.
 * На широком экране ячейки нет вовсе: таблица остаётся как была. Нажатие на саму карточку делает то же (v-cards).
 */
const { phone } = useViewport()
const cell = ref(null)
const open = ref(false)
let row = null

watch(
  [cell, open],
  () => {
    const next = cell.value?.parentElement || null
    if (row && row !== next) row.removeAttribute('data-open')
    row = next
    row?.toggleAttribute('data-open', open.value)
  },
  { flush: 'post' },
)

onBeforeUnmount(() => row?.removeAttribute('data-open'))
</script>
