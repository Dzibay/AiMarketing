<template>
  <div class="admin-shell">
    <header class="topbar admin-shell__top">
      <span class="topbar__brand">
        <span class="topbar__logo"><BrandMark :size="34" /></span>
        <span class="topbar__name">
          <strong>Ai<span>Marketing</span></strong>
          <small>Админка</small>
        </span>
      </span>
      <span class="topbar__spacer"></span>
      <div class="topbar__tools">
        <button class="icon-btn" type="button" :aria-label="isDark ? 'Светлая тема' : 'Тёмная тема'" @click="toggleTheme">
          <Sun v-if="isDark" aria-hidden="true" />
          <Moon v-else aria-hidden="true" />
        </button>
        <button v-if="isLoggedIn" class="btn btn--ghost" type="button" @click="logout">
          <LogOut aria-hidden="true" />
          Выйти
        </button>
      </div>
    </header>

    <main class="admin-shell__main">
      <form v-if="!isLoggedIn" class="auth-card" @submit.prevent="login">
        <h1>Вход в админку</h1>
        <p class="sub">Пароль владельца сервиса — ADMIN_PASSWORD в настройках сервера.</p>
        <p v-if="error" class="form-error">{{ error }}</p>
        <div class="field">
          <label for="admin-password">Пароль</label>
          <input
            id="admin-password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
            autofocus
          />
        </div>
        <button class="btn btn--wide" type="submit" :disabled="busy">Войти</button>
      </form>

      <template v-else>
        <div class="cabinet__head">
          <div>
            <h1>Админка</h1>
            <p>Пользователи и подписки, тарифы, общие ИИ, Вордстат и картинки, расход сервиса.</p>
          </div>
        </div>

        <div v-if="!summary" class="panel"><p class="empty">Загружаем…</p></div>

        <template v-else>
          <div class="stats-grid">
            <div class="stat-card">
              <small>Пользователей</small>
              <strong>{{ num(summary.users_total) }}</strong>
            </div>
            <div v-for="s in services" :key="s.key" class="stat-card">
              <small>{{ s.label }}</small>
              <strong :class="s.ok ? 'tone-ok' : 'tone-warn'">{{ s.ok ? 'работает' : 'не подключено' }}</strong>
            </div>
            <div class="stat-card" :title="summary.email_check ? '' : 'Без SMTP письма не уходят — кабинет открыт сразу после регистрации'">
              <small>Подтверждение почты</small>
              <strong :class="summary.email_check ? 'tone-ok' : 'tone-warn'">
                {{ summary.email_check ? 'включено' : 'выкл.: нет SMTP' }}
              </strong>
            </div>
            <div class="stat-card">
              <small>Пробный тариф</small>
              <strong :class="summary.trial_plan ? 'tone-ok' : 'tone-warn'">{{ summary.trial_plan || 'нет — без ограничений' }}</strong>
            </div>
          </div>

          <nav class="seg seg--tabs" aria-label="Разделы админки">
            <button
              v-for="t in TABS"
              :key="t.id"
              type="button"
              :class="{ active: tab === t.id }"
              @click="setTab(t.id)"
            >
              {{ t.label }}
            </button>
          </nav>

          <AdminUsers v-if="tab === 'users'" />
          <AdminPlans v-else-if="tab === 'plans'" @changed="loadSummary" />
          <template v-else-if="tab === 'services'">
            <AdminServices :bridge="bridge" @changed="loadSummary" />
            <AdminBridge @update="bridge = $event" />
          </template>
          <AdminUsage v-else-if="tab === 'usage'" />
        </template>
      </template>
    </main>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { LogOut, Moon, Sun } from '@lucide/vue'
import BrandMark from '../components/BrandMark.vue'
import AdminBridge from './admin/AdminBridge.vue'
import AdminPlans from './admin/AdminPlans.vue'
import AdminServices from './admin/AdminServices.vue'
import AdminUsage from './admin/AdminUsage.vue'
import AdminUsers from './admin/AdminUsers.vue'
import { adminApi } from '../api/admin'
import { errorText } from '../api/web'
import { clearAdminSession, setAdminSession, useAdminSession } from '../composables/useAdminSession'
import { useTheme } from '../composables/useTheme'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'
import { num } from '../utils/format'

useHead('Админка — AiMarketing', true)

const TABS = [
  { id: 'users', label: 'Пользователи' },
  { id: 'plans', label: 'Тарифы' },
  { id: 'services', label: 'ИИ и сервисы' },
  { id: 'usage', label: 'Расход' },
]

const route = useRoute()
const router = useRouter()
const { isLoggedIn } = useAdminSession()
const { isDark, toggleTheme } = useTheme()
const password = ref('')
const error = ref('')
const busy = ref(false)
const summary = ref(null)
const bridge = ref(null)
const tab = ref(TABS.some((t) => t.id === route.query.tab) ? route.query.tab : 'users')

const services = computed(() => [
  { key: 'ai', label: 'ИИ', ok: summary.value?.ai_configured },
  { key: 'wordstat', label: 'Вордстат', ok: summary.value?.wordstat_configured },
  { key: 'images', label: 'Картинки', ok: summary.value?.images_configured },
  { key: 'oauth', label: 'Вход через Яндекс', ok: summary.value?.yandex_oauth },
])

function setTab(id) {
  tab.value = id
  router.replace({ query: id === 'users' ? {} : { tab: id } })
}

async function loadSummary() {
  try {
    summary.value = await adminApi.me()
  } catch (err) {
    // 401 уже сбросил токен — показываем вход.
    if (err.status !== 401) pushToast(errorText(err), 'error')
  }
}

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
    summary.value = null
    if (on) loadSummary()
  },
  { immediate: true },
)
</script>
