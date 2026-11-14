<template>
  <table v-bind="$attrs" class="ai-changes">
    <thead>
      <tr>
        <th scope="col">Что</th>
        <th scope="col">Было</th>
        <th scope="col">Станет</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(row, i) in shown" :key="i">
        <td v-if="!row.before && !row.after" colspan="3" class="ai-changes__more">{{ row.what }}</td>
        <template v-else>
          <td>{{ row.what }}</td>
          <td class="ai-changes__before">{{ row.before }}</td>
          <td class="ai-changes__after" :class="{ 'is-fail': row.after.startsWith('не выйдет') }">{{ row.after }}</td>
        </template>
      </tr>
    </tbody>
  </table>
  <button v-if="long" class="ai-fold" type="button" :aria-expanded="open" @click="open = !open">
    {{ open ? 'Свернуть' : `Показать все ${changes.length}` }}
  </button>
</template>

<script setup>
/**
 * «Было → станет» в карточке действия ИИ: строки [{what, before, after}] собирает сервер.
 * На телефоне строка — блок (название, под ним «Было» и «Станет»), а длинный список свёрнут до первых пяти строк.
 */
import { computed, ref } from 'vue'
import { useViewport } from '../../composables/useViewport'
import { FOLD_ROWS, isLongList } from './foldRows'

// Класс и атрибуты снаружи — таблице: кнопка «Показать все» стоит рядом с ней, а не внутри.
defineOptions({ inheritAttrs: false })

const props = defineProps({
  changes: { type: Array, required: true },
})

const { phone } = useViewport()
const open = ref(false)
const long = computed(() => phone.value && isLongList(props.changes.length))
const shown = computed(() => (long.value && !open.value ? props.changes.slice(0, FOLD_ROWS) : props.changes))
</script>
