<template>
  <!-- Две версии знака: тёмно-зелёная для светлой темы и светлая для тёмной; лишнюю прячет CSS. -->
  <img v-bind="$attrs" class="brand-mark is-light" src="/logo.png" :alt="alt" :width="size" :height="size" />
  <img v-bind="$attrs" class="brand-mark is-dark" src="/logo-dark.png" :alt="alt" :width="size" :height="size" />
</template>

<script setup>
defineOptions({ inheritAttrs: false })

defineProps({
  size: { type: [Number, String], default: 36 },
  alt: { type: String, default: '' },
})
</script>
