<template>
  <div class="site">
    <SiteHeader solid />
    <main class="admin">
      <div class="wrap">
        <h1>Админка</h1>

        <form v-if="!isLoggedIn" class="auth-card" style="margin: 0" @submit.prevent="login">
          <p class="sub">Вход оператора</p>
          <p v-if="error" class="form-error">{{ error }}</p>
          <div class="field">
            <label for="password">Пароль</label>
            <input id="password" v-model="password" type="password" autocomplete="current-password" required />
          </div>
          <button class="btn btn--wide" type="submit" :disabled="busy">Войти</button>
        </form>

        <template v-else>
          <div class="panel">
            <h2>Сводка</h2>
            <p class="hint">
              Пользователей: {{ summary?.users_total ?? '—' }}.
              OAuth Яндекса: {{ summary?.yandex_oauth ? 'настроен' : 'не настроен' }}.
            </p>
            <button class="btn btn--ghost" type="button" @click="logout">Выйти</button>
          </div>

          <div class="panel">
            <h2>Пользователи</h2>
            <table v-if="users.length" class="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Почта</th>
                  <th>Имя</th>
                  <th>Яндекс</th>
                  <th>Статус</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="user in users" :key="user.id">
                  <td>{{ user.id }}</td>
                  <td>{{ user.email || '—' }}</td>
                  <td>{{ user.name || '—' }}</td>
                  <td>{{ user.has_yandex ? 'да' : 'нет' }}</td>
                  <td>
                    <span v-if="user.blocked">блок{{ user.blocked_reason ? `: ${user.blocked_reason}` : '' }}</span>
                    <span v-else>активен</span>
                  </td>
                  <td>
                    <button
                      v-if="!user.blocked"
                      class="btn btn--ghost"
                      type="button"
                      :disabled="busyId === user.id"
                      @click="blockUser(user)"
                    >
                      Блок
                    </button>
                    <button
                      v-else
                      class="btn btn--ghost"
                      type="button"
                      :disabled="busyId === user.id"
                      @click="unblockUser(user)"
                    >
                      Разблок
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-else class="empty">Пользователей пока нет.</p>
          </div>
        </template>
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import SiteHeader from '../components/SiteHeader.vue'
import { adminApi } from '../api/admin'
import { clearAdminSession, setAdminSession, useAdminSession } from '../composables/useAdminSession'
import { askText } from '../composables/useConfirm'
import { useHead } from '../utils/useHead'

useHead('Админка — AiMarketing', true)

const { isLoggedIn } = useAdminSession()
const password = ref('')
const error = ref('')
const busy = ref(false)
const busyId = ref(null)
const summary = ref(null)
const users = ref([])

async function load() {
  if (!isLoggedIn.value) return
  try {
    summary.value = await adminApi.me()
    const data = await adminApi.users()
    users.value = data.items || []
  } catch {
    clearAdminSession()
  }
}

async function login() {
  busy.value = true
  error.value = ''
  try {
    const data = await adminApi.login(password.value)
    setAdminSession(data.token)
    password.value = ''
    await load()
  } catch {
    error.value = 'Неверный пароль.'
  } finally {
    busy.value = false
  }
}

function logout() {
  clearAdminSession()
  summary.value = null
  users.value = []
}

async function blockUser(user) {
  const reason = await askText('Причина (необязательно)', {
    title: `Заблокировать ${user.email || 'пользователя'}?`,
    confirmText: 'Заблокировать',
    danger: true,
  })
  if (reason == null) return
  busyId.value = user.id
  try {
    const updated = await adminApi.blockUser(user.id, reason)
    users.value = users.value.map((u) => (u.id === user.id ? updated : u))
  } catch {
    error.value = 'Не удалось заблокировать.'
  } finally {
    busyId.value = null
  }
}

async function unblockUser(user) {
  busyId.value = user.id
  try {
    const updated = await adminApi.unblockUser(user.id)
    users.value = users.value.map((u) => (u.id === user.id ? updated : u))
  } catch {
    error.value = 'Не удалось разблокировать.'
  } finally {
    busyId.value = null
  }
}

onMounted(load)
</script>
