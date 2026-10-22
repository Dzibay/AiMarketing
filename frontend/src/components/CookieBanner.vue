<template>
  <div v-if="visible" class="cookie-banner">
    <p>
      Мы храним сессию входа в кабинете и используем необходимые cookie.
      <RouterLink to="/cookies">Подробнее</RouterLink>
    </p>
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
