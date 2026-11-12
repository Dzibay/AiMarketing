<template>
  <div class="ui-notice" :class="`ui-notice--${tone}`" :role="tone === 'error' ? 'alert' : 'status'">
    <component :is="ICONS[tone] || Info" class="ui-notice__icon" aria-hidden="true" />
    <div class="ui-notice__text"><slot /></div>
    <div v-if="$slots.action" class="ui-notice__act"><slot name="action" /></div>
    <button v-if="closable" class="ui-notice__x" type="button" aria-label="Скрыть" @click="$emit('close')">
      <X aria-hidden="true" />
    </button>
  </div>
</template>

<script setup>
import { CircleAlert, Info, TriangleAlert, X } from '@lucide/vue'

/** Уведомление в одну строку: иконка, текст, действие справа и крестик. tone: info, warn, error. */
defineProps({
  tone: { type: String, default: 'info' },
  closable: { type: Boolean, default: false },
})
defineEmits(['close'])

const ICONS = { info: Info, warn: TriangleAlert, error: CircleAlert }
</script>
