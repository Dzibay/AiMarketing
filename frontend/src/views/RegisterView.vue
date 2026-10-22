<template>
  <div class="site">
    <SiteHeader solid />
    <main class="auth-shell">
      <div class="wrap">
        <div class="auth-card">
          <h1>Регистрация</h1>
          <p class="sub">
            Создайте аккаунт по почте. Метрику и Директ подключите одной кнопкой уже в кабинете.
          </p>

          <form @submit.prevent="submit">
            <p v-if="error" class="form-error">{{ error }}</p>
            <div class="field">
              <label for="name">Имя</label>
              <input id="name" v-model="name" type="text" autocomplete="name" />
            </div>
            <div class="field">
              <label for="email">Почта</label>
              <input id="email" v-model="email" type="email" autocomplete="email" required />
            </div>
            <div class="field">
              <label for="password">Пароль</label>
              <input
                id="password"
                v-model="password"
                type="password"
                autocomplete="new-password"
                required
                minlength="8"
              />
            </div>
            <button class="btn btn--wide btn--accent" type="submit" :disabled="busy">
              Создать аккаунт
            </button>
          </form>

          <p class="muted-link">
            Уже есть аккаунт?
            <RouterLink to="/login">Войти</RouterLink>
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

useHead('Регистрация — AiMarketing', true)

const router = useRouter()
const { setSession } = useSession()

const name = ref('')
const email = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    const data = await webApi.register({
      name: name.value,
      email: email.value,
      password: password.value,
    })
    setSession(data.token)
    router.replace('/cabinet')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>
