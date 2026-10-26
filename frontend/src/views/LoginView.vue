<template>
  <div class="site">
    <SiteHeader solid />
    <main class="auth-shell">
      <div class="wrap">
        <div class="auth-card">
          <h1>Вход</h1>
          <p class="sub">Войдите в аккаунт, затем подключите Яндекс в кабинете одной кнопкой.</p>

          <form @submit.prevent="submit">
            <p v-if="error" class="form-error">{{ error }}</p>
            <div class="field">
              <label for="email">Почта</label>
              <input id="email" v-model="email" type="email" autocomplete="email" required />
            </div>
            <div class="field">
              <label for="password">Пароль</label>
              <input id="password" v-model="password" type="password" autocomplete="current-password" required />
            </div>
            <button class="btn btn--wide btn--accent" type="submit" :disabled="busy">Войти</button>
          </form>

          <p class="muted-link">
            <RouterLink to="/forgot-password">Забыли пароль?</RouterLink>
            · Нет аккаунта?
            <RouterLink to="/register">Регистрация</RouterLink>
          </p>
        </div>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, webApi } from '../api/web'
import { useSession } from '../composables/useSession'
import { useHead } from '../utils/useHead'

useHead('Вход — AiMarketing', true)

const router = useRouter()
const { setSession } = useSession()

const email = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    const data = await webApi.login({ email: email.value, password: password.value })
    setSession(data.token)
    router.replace('/cabinet')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>
