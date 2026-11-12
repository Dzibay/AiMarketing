<template>
  <div class="ui-empty" :class="{ 'ui-empty--plain': plain }">
    <span v-if="icon" class="ui-empty__icon"><component :is="icon" aria-hidden="true" /></span>
    <p class="ui-empty__text">
      <slot>{{ text }}</slot>
    </p>
    <div v-if="$slots.action" class="ui-empty__act"><slot name="action" /></div>
  </div>
</template>

<script setup>
/** Пустое состояние: иконка, одна фраза и одно действие. plain — без карточки (внутри другой карточки). */
defineProps({
  icon: { type: [Object, Function], default: null },
  text: { type: String, default: '' },
  plain: { type: Boolean, default: false },
})
</script>
