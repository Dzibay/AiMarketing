<template>
  <div v-if="visible" class="cookie-banner" role="region" aria-label="Cookie">
    <p>Только необходимые <RouterLink to="/cookies">cookie</RouterLink></p>
    <button class="btn btn--accent" type="button" @click="accept">Понятно</button>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { acceptCookies, cookiesAccepted } from '../utils/cookies'

const visible = ref(false)

onMounted(() => {
  visible.value = !cookiesAccepted()
})

function accept() {
  acceptCookies()
  visible.value = false
}
</script>
