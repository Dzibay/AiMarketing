<template>
  <p v-if="!expert" class="xhint">
    <SlidersHorizontal :size="15" aria-hidden="true" />
    <span>
      <slot /> — в <RouterLink to="/cabinet/settings/interface">экспертном режиме</RouterLink>.
    </span>
  </p>
</template>

<script setup>
import { RouterLink } from 'vue-router'
import { SlidersHorizontal } from '@lucide/vue'
import { useExpert } from '../composables/useExpert'

/** Подсказка простого режима: где лежит то, чего на странице не видно. В экспертном режиме не показывается. */
const { expert } = useExpert()
</script>
