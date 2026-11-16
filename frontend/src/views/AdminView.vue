<template>
  <!-- Вход: без меню, одна форма по центру. -->
  <div v-if="!isLoggedIn" class="admin-login">
    <header class="admin-login__top">
      <span class="admin-brand">
        <TolkMark :size="26" />
        <span>толк</span>
        <em>админка</em>
      </span>
      <button class="icon-btn" type="button" :aria-label="isDark ? 'Светлая тема' : 'Тёмная тема'" @click="toggleTheme">
        <Sun v-if="isDark" aria-hidden="true" />
        <Moon v-else aria-hidden="true" />
      </button>
    </header>
    <form class="auth-card" @submit.prevent="login">
      <h1>Вход в админку</h1>
      <p class="sub">Пароль владельца сервиса — ADMIN_PASSWORD в настройках сервера.</p>
      <p v-if="error" class="form-error">{{ error }}</p>
      <div class="field">
        <label for="admin-password">Пароль</label>
        <input id="admin-password" v-model="password" type="password" autocomplete="current-password" required autofocus />
      </div>
      <button class="btn btn--wide btn--accent" type="submit" :disabled="busy">Войти</button>
    </form>
  </div>

  <!-- Админка: меню слева на всю высоту, справа страница. -->
  <div v-else class="adm">
    <aside class="adm-side" aria-label="Меню админки">
      <RouterLink class="admin-brand adm-side__brand" :to="ADMIN_BASE">
        <TolkMark :size="26" />
        <span>толк</span>
        <em>админка</em>
      </RouterLink>

      <nav class="adm-side__nav" aria-label="Разделы">
        <template v-for="group in NAV" :key="group.title">
          <h4 v-if="group.title" class="adm-side__h">{{ group.title }}</h4>
          <RouterLink
            v-for="item in group.items"
            :key="item.key"
            class="srow"
            :class="{ active: navActive(item, route.path) }"
            :to="item.to"
          >
            <component :is="item.icon" aria-hidden="true" />
            <span class="srow__t">{{ item.label }}</span>
            <span v-if="item.badge && item.badge(summary)" class="srow__n" v-tip.right="item.badgeTip">
              {{ num(item.badge(summary)) }}
            </span>
          </RouterLink>
        </template>
      </nav>

      <div class="adm-side__foot">
        <RouterLink
          class="srow"
          :class="{ active: navActive(SETTINGS_ITEM, route.path) }"
          :to="SETTINGS_ITEM.to"
        >
          <component :is="SETTINGS_ITEM.icon" aria-hidden="true" />
          <span class="srow__t">{{ SETTINGS_ITEM.label }}</span>
          <span
            v-if="SETTINGS_ITEM.warn(summary)"
            class="sdot sdot--warn"
            v-tip.right="`Не настроено: ${missingSettings(summary).join(', ')}`"
          ></span>
        </RouterLink>
        <div class="adm-side__tools">
          <button class="srow" type="button" @click="toggleTheme">
            <Sun v-if="isDark" aria-hidden="true" />
            <Moon v-else aria-hidden="true" />
            <span class="srow__t">{{ isDark ? 'Светлая тема' : 'Тёмная тема' }}</span>
          </button>
          <button class="srow" type="button" @click="logout">
            <LogOut aria-hidden="true" />
            <span class="srow__t">Выйти</span>
          </button>
        </div>
      </div>
    </aside>

    <main class="adm-main">
      <header v-if="route.meta.title" class="admin-head">
        <h1>{{ route.meta.title }}</h1>
        <p v-if="route.meta.hint">{{ route.meta.hint }}</p>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { LogOut, Moon, Sun } from '@lucide/vue'
import TolkMark from '../components/TolkMark.vue'
import { ADMIN_BASE, NAV, SETTINGS_ITEM, missingSettings, navActive } from './admin/adminNav'
import { adminApi } from '../api/admin'
import { errorText } from '../api/web'
import { clearAdminSession, setAdminSession, useAdminSession } from '../composables/useAdminSession'
import { reloadAdminSummary, resetAdminSummary, useAdminSummary } from '../composables/useAdminSummary'
import { useTheme } from '../composables/useTheme'
import { useHead } from '../utils/useHead'
import { num } from '../utils/format'
import '../styles/admin.css'

const route = useRoute()
const { isLoggedIn } = useAdminSession()
const { summary } = useAdminSummary()
const { isDark, toggleTheme } = useTheme()
const password = ref('')
const error = ref('')
const busy = ref(false)

watch(
  () => route.meta.title,
  (title) => useHead(title ? `${title} — админка Толк` : 'Админка — Толк', true),
  { immediate: true },
)

async function login() {
  busy.value = true
  error.value = ''
  try {
    const data = await adminApi.login(password.value)
    password.value = ''
    setAdminSession(data.token)
  } catch (err) {
    if (err.status === 429) error.value = 'Слишком много неверных попыток. Попробуйте через 15 минут.'
    else if (err.status === 401) error.value = 'Неверный пароль.'
    else error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

function logout() {
  clearAdminSession()
}

watch(
  isLoggedIn,
  (on) => {
    resetAdminSummary()
    if (on) reloadAdminSummary()
  },
  { immediate: true },
)
</script>
