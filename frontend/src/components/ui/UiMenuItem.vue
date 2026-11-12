<template>
  <component
    :is="to ? RouterLink : 'button'"
    class="menu-item"
    :class="{ 'is-danger': danger }"
    :type="to ? undefined : 'button'"
    :to="to || undefined"
    role="menuitem"
    :disabled="to ? undefined : disabled"
    tabindex="-1"
  >
    <component :is="icon" v-if="icon" aria-hidden="true" />
    <span class="menu-item__text"><slot /></span>
    <small v-if="hint" class="menu-item__hint">{{ hint }}</small>
  </component>
</template>

<script setup>
import { RouterLink } from 'vue-router'

/** Пункт меню «⋯». danger — красный текст, такой пункт ставится последним и открывает подтверждение. */
defineProps({
  icon: { type: [Object, Function], default: null },
  danger: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  hint: { type: String, default: '' },
  to: { type: [String, Object], default: null },
})
</script>
