<template>
  <div class="site app">
    <header class="app-header">
      <div class="wrap app-header__inner">
        <RouterLink class="brand" to="/cabinet" aria-label="AiMarketing">
          <img class="brand__mark" src="/logo.png" alt="" width="32" height="32" />
          <span class="brand__name">Ai<span>Marketing</span></span>
        </RouterLink>
        <WorkspacePicker />
        <LoadsIndicator />
        <nav class="app-nav" aria-label="Кабинет">
          <RouterLink to="/cabinet" :class="{ active: route.path === '/cabinet' }">Обзор</RouterLink>
          <RouterLink to="/cabinet/accounts" :class="{ active: isActive('/cabinet/accounts') }">Аккаунты</RouterLink>
          <RouterLink to="/cabinet/clients" :class="{ active: isActive('/cabinet/clients') }">Клиенты</RouterLink>
          <RouterLink to="/cabinet/negative-lists" :class="{ active: isActive('/cabinet/negative-lists') }">
            Минус-фразы
          </RouterLink>
          <RouterLink to="/cabinet/rules" :class="{ active: isActive('/cabinet/rules') }">Правила</RouterLink>
          <RouterLink to="/cabinet/settings" :class="{ active: isActive('/cabinet/settings') }">Настройки</RouterLink>
          <button class="app-nav__logout" type="button" @click="logout">Выйти</button>
        </nav>
      </div>
    </header>
    <main class="cabinet">
      <div class="wrap">
        <slot />
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import LoadsIndicator from './LoadsIndicator.vue'
import WorkspacePicker from './WorkspacePicker.vue'
import { resetLoads } from '../composables/useLoads'
import { clearSession, getSession } from '../composables/useSession'
import { loadWorkspaces, resetWorkspaces } from '../composables/useWorkspaces'
import { ApiError, errorText } from '../api/web'
import { pushToast } from '../composables/useToasts'

const route = useRoute()
const router = useRouter()

function isActive(path) {
  return route.path === path || route.path.startsWith(`${path}/`)
}

function logout() {
  clearSession()
  resetWorkspaces()
  resetLoads()
  router.replace('/login')
}

onMounted(async () => {
  if (!getSession()) {
    router.replace('/login')
    return
  }
  try {
    await loadWorkspaces()
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      resetWorkspaces()
      router.replace('/login')
      return
    }
    pushToast(errorText(err), 'error')
  }
})
</script>
