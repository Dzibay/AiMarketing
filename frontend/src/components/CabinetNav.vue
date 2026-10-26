<template>
  <nav class="cabinet-nav">
    <RouterLink to="/cabinet" :class="{ active: isActive('/cabinet') && !$route.path.startsWith('/cabinet/') }">
      Главная
    </RouterLink>
    <RouterLink to="/cabinet/clients" :class="{ active: isActive('/cabinet/clients') }">Клиенты</RouterLink>
    <RouterLink to="/cabinet/campaigns" :class="{ active: isActive('/cabinet/campaigns') }">Кампании</RouterLink>
    <RouterLink to="/cabinet/analytics" :class="{ active: isActive('/cabinet/analytics') }">Аналитика</RouterLink>
    <RouterLink to="/cabinet/rules" :class="{ active: isActive('/cabinet/rules') }">Правила</RouterLink>
  </nav>
</template>

<script setup>
import { RouterLink, useRoute } from 'vue-router'

const route = useRoute()

function isActive(path) {
  return route.path === path || route.path.startsWith(`${path}/`)
}
</script>
