<template>
  <footer class="site-footer">
    <div class="wrap site-footer__inner">
      <span>© {{ year }} Толк</span>
      <nav>
        <RouterLink to="/privacy">Конфиденциальность</RouterLink>
        <RouterLink to="/consent">Согласие</RouterLink>
        <RouterLink to="/offer">Оферта</RouterLink>
        <RouterLink to="/cookies">Cookies</RouterLink>
      </nav>
    </div>
  </footer>
</template>

<script setup>
import { RouterLink } from 'vue-router'

const year = new Date().getFullYear()
</script>
