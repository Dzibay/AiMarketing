<template>
  <RouterView />
  <ToastHost />
  <CookieBanner />
</template>

<script setup>
import { RouterView } from 'vue-router'
import CookieBanner from './components/CookieBanner.vue'
import ToastHost from './components/ui/ToastHost.vue'
</script>
