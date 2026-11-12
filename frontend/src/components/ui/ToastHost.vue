<template>
  <div class="toast-host" aria-live="polite">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      class="toast"
      :class="[toast.tone ? `is-${toast.tone}` : '', { 'has-action': toast.action }]"
    >
      <span>{{ toast.message }}</span>
      <button v-if="toast.action" class="toast__action" type="button" @click="act(toast)">
        {{ toast.action.label }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { useToasts } from '../../composables/useToasts'

const { toasts, dismissToast } = useToasts()

function act(toast) {
  dismissToast(toast.id)
  toast.action.run()
}
</script>

<style scoped>
.toast.has-action {
  display: flex;
  align-items: center;
  gap: 12px;
}
.toast.has-action > span {
  flex: 1;
}
.toast__action {
  flex: none;
  padding: 6px 12px;
  border: 1px solid currentColor;
  border-radius: 999px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}
.toast__action:hover {
  background: color-mix(in srgb, currentColor 14%, transparent);
}
</style>
