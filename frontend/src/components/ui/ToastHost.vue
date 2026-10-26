<template>
  <div class="toast-host" aria-live="polite">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      class="toast"
      :class="toast.tone ? `is-${toast.tone}` : ''"
    >
      {{ toast.message }}
    </div>
  </div>
</template>

<script setup>
import { useToasts } from '../../composables/useToasts'

const { toasts } = useToasts()
</script>
