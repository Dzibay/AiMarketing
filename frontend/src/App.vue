<template>
  <RouterView />
  <ToastHost />
  <ConfirmHost />
  <CookieBanner />
</template>

<script setup>
import { RouterView } from 'vue-router'
import CookieBanner from './components/CookieBanner.vue'
import ConfirmHost from './components/ui/ConfirmHost.vue'
import ToastHost from './components/ui/ToastHost.vue'
</script>
