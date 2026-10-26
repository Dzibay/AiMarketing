<template>
  <div class="site">
    <SiteHeader solid />
    <main class="auth-shell">
      <div class="wrap">
        <div class="auth-card">
          <h1>Новый пароль</h1>
          <p class="sub">Задайте пароль не короче 8 символов.</p>

          <form v-if="!done" @submit.prevent="submit">
            <p v-if="error" class="form-error">{{ error }}</p>
            <div class="field">
              <label for="password">Пароль</label>
              <input id="password" v-model="password" type="password" autocomplete="new-password" required minlength="8" />
            </div>
            <button class="btn btn--wide btn--accent" type="submit" :disabled="busy || !token">Сохранить</button>
          </form>
          <template v-else>
            <p class="sub">Пароль обновлён.</p>
            <RouterLink class="btn btn--wide btn--accent" to="/login">Войти</RouterLink>
          </template>
        </div>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, webApi } from '../api/web'
import { useHead } from '../utils/useHead'

useHead('Новый пароль — AiMarketing', true)

const route = useRoute()
const token = computed(() => String(route.query.token || ''))
const password = ref('')
const error = ref('')
const busy = ref(false)
const done = ref(false)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await webApi.resetPassword({ token: token.value, password: password.value })
    done.value = true
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>
