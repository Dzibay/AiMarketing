<template>
  <footer class="site-footer">
    <div class="wrap site-footer__inner">
      <div class="brand" style="font-size: 0.95rem">
        <img class="brand__mark" src="/logo.png" alt="" width="28" height="28" />
        <span>© {{ year }} Ai<span>Marketing</span></span>
      </div>
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
