<template>
  <header class="site-header" :class="{ 'is-solid': solid }">
    <div class="wrap site-header__inner">
      <RouterLink class="brand" to="/" aria-label="AiMarketing">
        <img class="brand__mark" src="/logo.png" alt="" width="36" height="36" />
        <span class="brand__name">Ai<span>Marketing</span></span>
      </RouterLink>
      <nav class="nav">
        <RouterLink v-if="!isLoggedIn" class="btn btn--ghost" to="/login">Войти</RouterLink>
        <RouterLink v-if="!isLoggedIn" class="btn btn--accent" to="/register">Регистрация</RouterLink>
        <RouterLink v-else class="btn btn--accent" to="/cabinet">Главная</RouterLink>
      </nav>
    </div>
  </header>
</template>

<script setup>
import { RouterLink } from 'vue-router'
import { useSession } from '../composables/useSession'

defineProps({
  solid: { type: Boolean, default: false },
})

const { isLoggedIn } = useSession()
</script>
