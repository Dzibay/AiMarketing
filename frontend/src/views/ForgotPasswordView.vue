<template>
  <div class="site">
    <SiteHeader solid />
    <main class="auth-shell">
      <div class="wrap">
        <div class="auth-card">
          <h1>Сброс пароля</h1>
          <p class="sub">Укажите почту — если аккаунт есть, отправим ссылку (или запишем её в лог сервера).</p>

          <form v-if="!done" @submit.prevent="submit">
            <p v-if="error" class="form-error">{{ error }}</p>
            <div class="field">
              <label for="email">Почта</label>
              <input id="email" v-model="email" type="email" autocomplete="email" required />
            </div>
            <button class="btn btn--wide btn--accent" type="submit" :disabled="busy">Отправить</button>
          </form>
          <p v-else class="sub">Если аккаунт найден, проверьте почту или логи backend.</p>

          <p class="muted-link">
            <RouterLink to="/login">Ко входу</RouterLink>
          </p>
        </div>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { RouterLink } from 'vue-router'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, webApi } from '../api/web'
import { useHead } from '../utils/useHead'

useHead('Сброс пароля — AiMarketing', true)

const email = ref('')
const error = ref('')
const busy = ref(false)
const done = ref(false)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await webApi.forgotPassword(email.value)
    done.value = true
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>
