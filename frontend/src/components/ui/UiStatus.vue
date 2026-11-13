<template>
  <span class="ui-status" :class="`ui-status--${tone}`" v-tip="hint || undefined">
    <component :is="icon || ICONS[tone] || Info" aria-hidden="true" />
    <span>{{ label }}</span>
  </span>
</template>

<script setup>
import { CircleCheck, CirclePause, CircleX, Clock, Info, TriangleAlert } from '@lucide/vue'

/**
 * Одна плашка статуса на объект: цвет, иконка и слово — статус не передаётся одним цветом.
 * tone: ok — работает, off — остановлено, wait — ждёт (модерация), warn — внимание, bad — ошибка, info.
 * Причину пишем в hint — она видна в подсказке.
 */
defineProps({
  tone: { type: String, default: 'info' },
  label: { type: String, required: true },
  hint: { type: String, default: '' },
  icon: { type: [Object, Function], default: null },
})

const ICONS = { ok: CircleCheck, off: CirclePause, wait: Clock, warn: TriangleAlert, bad: CircleX, info: Info }
</script>
