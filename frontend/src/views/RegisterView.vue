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
            <div v-if="promoOpen" class="field">
              <label for="promo">Промокод</label>
              <input id="promo" v-model="promo" type="text" autocomplete="off" maxlength="32" />
              <p class="field-hint register-promo__hint">Регистрируетесь через Яндекс — введите промокод в кабинете на странице «Тариф».</p>
            </div>
            <button v-else class="link-btn register-promo" type="button" @click="promoOpen = true">
              Есть промокод?
            </button>
            <button class="btn btn--wide btn--accent" type="submit" :disabled="busy">
              Создать аккаунт
            </button>
          </form>

          <p class="register-consent">
            Создавая аккаунт, вы принимаете
            <a :href="docs.offer_url" target="_blank" rel="noopener">оферту</a> и соглашаетесь с
            <a :href="docs.privacy_url" target="_blank" rel="noopener">политикой обработки данных</a>.
          </p>

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
import { computed, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { errorText, webApi } from '../api/web'
import { clearRef, currentRef } from '../composables/useReferral'
import { useSession } from '../composables/useSession'
import { useSiteConfig } from '../composables/useSiteConfig'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'

useHead('Регистрация — AiMarketing', true)

const router = useRouter()
const { setSession } = useSession()

const { config, load: loadConfig } = useSiteConfig()
const docs = computed(() => config.value || { offer_url: '/offer', privacy_url: '/privacy' })
loadConfig()

const route = useRoute()
const promo = ref(typeof route.query.promo === 'string' ? route.query.promo : '')
const promoOpen = ref(Boolean(promo.value))
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
      promo: promo.value.trim(),
      ref: currentRef(),
    })
    clearRef()
    setSession(data.token)
    if (data.promo?.message) pushToast(`Промокод активирован: ${data.promo.message}`, 'success')
    else if (data.promo?.error) pushToast(`Промокод не применён: ${data.promo.error}`, 'error')
    router.replace('/cabinet')
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}
</script>
