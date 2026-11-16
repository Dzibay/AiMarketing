<template>
  <div class="ui-empty" :class="{ 'ui-empty--plain': plain, 'ui-empty--art': art }">
    <EmptyArt v-if="art" class="ui-empty__art" :name="art" />
    <span v-else-if="icon" class="ui-empty__icon"><component :is="icon" aria-hidden="true" /></span>
    <p class="ui-empty__text">
      <slot>{{ text }}</slot>
    </p>
    <div v-if="$slots.action" class="ui-empty__act"><slot name="action" /></div>
  </div>
</template>

<script setup>
import EmptyArt from './EmptyArt.vue'

/**
 * Пустое состояние: рисунок (art — имя из EmptyArt) или иконка, одна фраза и следующий шаг кнопкой.
 * Рисунок — для «здесь пока ничего нет», иконка — для ошибок загрузки. plain — без карточки (внутри другой карточки).
 */
defineProps({
  art: { type: String, default: '' },
  icon: { type: [Object, Function], default: null },
  text: { type: String, default: '' },
  plain: { type: Boolean, default: false },
})
</script>
