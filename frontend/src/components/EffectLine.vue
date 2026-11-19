<template>
  <span
    v-if="effect"
    class="effect-line"
    :class="`effect-line--${effect.tone}`"
    tabindex="0"
    :aria-label="`${effect.text}. ${effect.hint}`"
    v-tip="effect.hint"
  >
    <component :is="icon" aria-hidden="true" />{{ effect.text }}<template v-if="effect.others"> · были и другие правки</template>
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { CircleCheck, Minus, TriangleAlert } from '@lucide/vue'

/**
 * Итог правки через 7 и 14 дней (backend app/effects.py): «Цена заявки −18 % за 7 дней» или «Мало данных за 7 дней».
 * В подсказке — цифры до и после и оговорка, что итог только совпал по времени с правкой.
 */
const props = defineProps({
  effect: { type: Object, default: null },
})

const icon = computed(() => {
  if (props.effect?.verdict === 'better') return CircleCheck
  if (props.effect?.verdict === 'worse') return TriangleAlert
  return Minus
})
</script>

<style scoped>
.effect-line {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  cursor: help;
}

.effect-line svg {
  width: 14px;
  height: 14px;
  flex: none;
}

.effect-line--ok {
  color: var(--ok);
}

.effect-line--warn {
  color: var(--warn);
}

.effect-line--muted {
  font-weight: 500;
}
</style>
