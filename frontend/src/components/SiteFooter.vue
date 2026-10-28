<template>
  <footer class="site-footer">
    <div class="wrap site-footer__inner">
      <div class="brand" style="font-size: 0.95rem">
        <BrandMark class="brand__mark" :size="28" />
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
import BrandMark from './BrandMark.vue'

const year = new Date().getFullYear()
</script>
