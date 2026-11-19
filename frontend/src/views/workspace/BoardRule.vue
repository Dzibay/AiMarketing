<template>
  <span
    v-if="item.rule && board.isOn(item)"
    class="chip board-rule"
    :class="item.rule.state === 'bad' ? 'chip--bad' : 'chip--warning'"
    tabindex="0"
    v-tip="item.rule.note"
  >
    {{ item.rule.label }}
  </span>
</template>

<script setup>
/**
 * Пометка строки доски по правилам Директа: «Директ не примет» (красная), «модерация» или «пересечение»
 * (жёлтые). Причина — в подсказке; на телефоне её видно в разделе «Готовность».
 */
import { inject } from 'vue'

defineProps({
  item: { type: Object, required: true },
})
const board = inject('board')
</script>

<style scoped>
.board-rule {
  flex: none;
  padding: 0.05rem 0.45rem;
  font-size: 0.7rem;
  cursor: help;
}
</style>
