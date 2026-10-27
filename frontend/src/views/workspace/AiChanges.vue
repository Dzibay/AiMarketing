<template>
  <table class="ai-changes">
    <thead>
      <tr>
        <th scope="col">Что</th>
        <th scope="col">Было</th>
        <th scope="col">Станет</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(row, i) in changes" :key="i">
        <td v-if="!row.before && !row.after" colspan="3" class="ai-changes__more">{{ row.what }}</td>
        <template v-else>
          <td>{{ row.what }}</td>
          <td class="ai-changes__before">{{ row.before }}</td>
          <td class="ai-changes__after" :class="{ 'is-fail': row.after.startsWith('не выйдет') }">{{ row.after }}</td>
        </template>
      </tr>
    </tbody>
  </table>
</template>

<script setup>
/** «Было → станет» в карточке действия ИИ: строки [{what, before, after}] собирает сервер. */
defineProps({
  changes: { type: Array, required: true },
})
</script>
