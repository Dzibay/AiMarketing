<template>
  <header class="site-header" :class="{ 'is-solid': solid }">
    <div class="wrap site-header__inner">
      <!-- Главная — статический сайт (site/), а не маршрут SPA: обычная ссылка с перезагрузкой. -->
      <a class="brand" href="/" aria-label="Толк — на сайт">
        <TolkMark :size="30" />
        <span class="brand__name">толк</span>
      </a>
      <nav class="nav">
        <button class="icon-btn" type="button" :aria-label="isDark ? 'Светлая тема' : 'Тёмная тема'" @click="toggleTheme">
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
        </button>
        <RouterLink v-if="!isLoggedIn" class="btn btn--ghost" to="/login">Войти</RouterLink>
        <RouterLink v-if="!isLoggedIn" class="btn btn--accent" to="/register">Регистрация</RouterLink>
        <RouterLink v-else class="btn btn--accent" to="/cabinet">Главная</RouterLink>
      </nav>
    </div>
  </header>
</template>

<script setup>
import { RouterLink } from 'vue-router'
import { Moon, Sun } from '@lucide/vue'
import TolkMark from './TolkMark.vue'
import { useSession } from '../composables/useSession'
import { useTheme } from '../composables/useTheme'

defineProps({
  solid: { type: Boolean, default: false },
})

const { isLoggedIn } = useSession()
const { isDark, toggleTheme } = useTheme()
</script>
