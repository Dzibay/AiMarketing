<template>
  <div class="site">
    <SiteHeader solid />
    <main class="auth-shell">
      <div class="wrap">
        <div class="auth-card verify-card">
          <template v-if="mode === 'checking'">
            <h1>Подтверждаем почту…</h1>
            <p class="sub">Секунду.</p>
          </template>

          <template v-else-if="mode === 'done'">
            <h1>Почта подтверждена</h1>
            <p class="sub">Спасибо! Кабинет открыт.</p>
            <RouterLink v-if="hasSession" class="btn btn--wide btn--accent" to="/cabinet">В кабинет</RouterLink>
            <RouterLink v-else class="btn btn--wide btn--accent" to="/login">Войти</RouterLink>
          </template>

          <template v-else-if="mode === 'failed'">
            <h1>Ссылка не сработала</h1>
            <p class="sub">{{ failText }}</p>
            <template v-if="hasSession">
              <p v-if="notice" class="form-ok">{{ notice }}</p>
              <p v-if="error" class="form-error">{{ error }}</p>
              <button class="btn btn--wide btn--accent" type="button" :disabled="busy" @click="resend">
                Отправить новое письмо
              </button>
            </template>
            <p v-else class="muted-link">
              <RouterLink to="/login">Войдите</RouterLink> — и отправим новое письмо.
            </p>
          </template>

          <template v-else-if="mode === 'waiting'">
            <h1>Подтвердите почту</h1>
            <p class="sub">
              Мы отправили письмо на <strong>{{ user?.email }}</strong>. Откройте ссылку из него — она действует 3 дня.
              Не видите письма — проверьте «Спам».
            </p>
            <p v-if="notice" class="form-ok">{{ notice }}</p>
            <p v-if="error" class="form-error">{{ error }}</p>
            <div class="verify-card__actions">
              <button class="btn btn--wide btn--accent" type="button" :disabled="busy" @click="check">Я подтвердил</button>
              <button class="btn btn--wide btn--ghost" type="button" :disabled="busy" @click="resend">
                Отправить письмо ещё раз
              </button>
            </div>
            <p class="muted-link">
              Другая почта? <button class="link-btn" type="button" @click="logout">Выйти</button> и зарегистрируйтесь
              заново.
            </p>
          </template>
        </div>
      </div>
    </main>
    <SiteFooter />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import SiteFooter from '../components/SiteFooter.vue'
import SiteHeader from '../components/SiteHeader.vue'
import { ApiError, errorText, webApi } from '../api/web'
import { loadProfile, resetProfile, useProfile } from '../composables/useProfile'
import { clearSession, getSession } from '../composables/useSession'
import { useHead } from '../utils/useHead'

useHead('Подтверждение почты — AiMarketing', true)

const route = useRoute()
const router = useRouter()
const { user, subscription } = useProfile()

const mode = ref('')
const failCode = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const hasSession = ref(!!getSession())

const FAIL_TEXT = {
  state: 'Ссылка недействительна или уже использована. Если почта ещё не подтверждена — отправим новое письмо.',
  'token-expired': 'Срок действия ссылки истёк — отправим новое письмо.',
}
const failText = computed(() => FAIL_TEXT[failCode.value] || 'Не удалось подтвердить почту. Попробуйте позже.')

async function confirm(token) {
  mode.value = 'checking'
  try {
    await webApi.verifyEmail(token)
    mode.value = 'done'
    if (hasSession.value) loadProfile(true).catch(() => null)
  } catch (err) {
    failCode.value = err instanceof ApiError ? err.code : ''
    mode.value = 'failed'
  }
}

async function check() {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await loadProfile(true)
    if (subscription.value?.state === 'unverified') {
      error.value = 'Почта ещё не подтверждена — откройте ссылку из письма.'
    } else {
      router.replace('/cabinet')
    }
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

async function resend() {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await webApi.resendVerify()
    if (res.sent) {
      notice.value = 'Письмо отправлено — проверьте почту.'
    } else {
      // Письмо не ушло: почта уже подтверждена или отправка выключена — проверяем доступ.
      await loadProfile(true)
      if (subscription.value?.state !== 'unverified') router.replace('/cabinet')
      else error.value = 'Не удалось отправить письмо. Попробуйте позже.'
    }
  } catch (err) {
    error.value = errorText(err)
  } finally {
    busy.value = false
  }
}

function logout() {
  clearSession()
  resetProfile()
  router.replace('/register')
}

onMounted(async () => {
  const token = String(route.query.token || '')
  if (token) {
    await confirm(token)
    return
  }
  if (!hasSession.value) {
    router.replace('/login')
    return
  }
  try {
    await loadProfile(true)
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) router.replace('/login')
    else error.value = errorText(err)
    mode.value = 'waiting'
    return
  }
  if (subscription.value?.state !== 'unverified') {
    router.replace('/cabinet')
    return
  }
  mode.value = 'waiting'
})
</script>
