<template>
  <div v-if="loading" class="panel"><p class="empty">Загружаем настройки…</p></div>

  <template v-else>
    <template v-if="tab === 'profile'">
      <section class="panel">
        <h2>Профиль</h2>
        <ul class="plan-facts">
          <li>
            <span>Почта</span>
            <strong>
              {{ user?.email || '—' }}
              <span v-if="user?.email" class="chip" :class="user.email_verified ? 'chip--ok' : 'chip--warning'">
                {{ user.email_verified ? 'подтверждена' : 'не подтверждена' }}
              </span>
            </strong>
          </li>
          <li>
            <span>Вход через Яндекс</span>
            <strong>{{ user?.has_yandex ? 'привязан' : 'нет' }}</strong>
          </li>
          <li v-if="user?.created_at">
            <span>С нами с</span>
            <strong>{{ formatDate(user.created_at) }}</strong>
          </li>
        </ul>
        <form class="filters" @submit.prevent="saveName">
          <div v-field class="field field--wide">
            <label>Имя</label>
            <input v-model.trim="name" type="text" maxlength="120" placeholder="Как к вам обращаться" autocomplete="name" />
          </div>
          <button class="btn btn--accent" type="submit" :disabled="nameBusy || name === (user?.name || '')">Сохранить</button>
        </form>
        <div v-if="user?.email && !user.email_verified" class="row-actions">
          <button class="btn btn--ghost" type="button" :disabled="verifyBusy" @click="resendVerify">
            Отправить письмо ещё раз
          </button>
        </div>
        <p class="hint">
          Тариф и лимит — в разделе <RouterLink to="/cabinet/plan">«Тариф»</RouterLink>, Яндекс-аккаунты — в
          <RouterLink to="/cabinet/connections">«Подключениях»</RouterLink>.
        </p>
      </section>

    </template>

    <section v-if="tab === 'notifications'" class="panel" id="telegram">
      <h2>Telegram</h2>
      <p class="hint">
        Утренний дайджест по всем проектам и уведомления о срабатывании правил автопилота. На почту приходят только
        служебные письма — подтверждение и сброс пароля.
      </p>
      <p v-if="!telegram.configured" class="empty">Бот не настроен на сервере (TELEGRAM_BOT_TOKEN).</p>
      <template v-else-if="telegram.linked">
        <p class="sync-status is-ok">Подключено</p>
        <div class="row-actions">
          <button class="btn btn--ghost" type="button" :disabled="tgBusy" @click="testTelegram">Тестовое сообщение</button>
          <button class="btn btn--ghost" type="button" :disabled="tgBusy" @click="unlinkTelegram">Отвязать</button>
        </div>
        <template v-if="digest">
          <h3 class="subhead">Утренний дайджест</h3>
          <FeatureLockNotice feature="digest">Дайджест в Telegram не входит в ваш тариф.</FeatureLockNotice>
          <p class="hint">
            Вчерашний расход по сайтам, KPI, темп бюджета, баланс и «где горит». Перед отправкой статистика за вчера и
            балансы обновляются.
          </p>
          <div class="filters">
            <label class="check-row">
              <input
                type="checkbox"
                :checked="digest.enabled"
                :disabled="digestBusy"
                @change="saveDigest({ enabled: $event.target.checked })"
              />
              Присылать каждое утро
            </label>
            <div v-field class="field">
              <label>Время, МСК</label>
              <select
                :value="digest.hour"
                :disabled="digestBusy || !digest.enabled"
                @change="saveDigest({ hour: Number($event.target.value) })"
              >
                <option v-for="h in 24" :key="h" :value="h - 1">{{ String(h - 1).padStart(2, '0') }}:00</option>
              </select>
            </div>
          </div>
          <p v-if="digest.last_sent_on" class="sync-status">
            Последний утренний — {{ formatDate(digest.last_sent_on) }}
          </p>
          <div class="row-actions">
            <button class="btn btn--ghost" type="button" :disabled="digestBusy" @click="previewDigest">Показать</button>
            <button class="btn btn--ghost" type="button" :disabled="digestBusy" @click="sendDigest">Отправить сейчас</button>
          </div>
          <pre v-if="digestText" class="digest-preview">{{ digestText }}</pre>
        </template>
      </template>
      <template v-else>
        <p class="hint">Нажмите «Получить ссылку», откройте бота и нажмите Start.</p>
        <div class="row-actions">
          <button class="btn btn--accent" type="button" :disabled="tgBusy" @click="linkTelegram">Получить ссылку</button>
          <a v-if="tgLink" class="btn btn--ghost" :href="tgLink" target="_blank" rel="noopener">Открыть бота</a>
          <button v-if="tgLink || tgCode" class="btn btn--ghost" type="button" :disabled="tgBusy" @click="loadTelegram">
            Я нажал Start
          </button>
        </div>
        <p v-if="tgCode && !tgLink" class="hint">Отправьте боту: /start {{ tgCode }}</p>
      </template>
    </section>

    <section v-if="tab === 'profile'" class="panel">
      <h2>Смена пароля</h2>
      <form class="filters" @submit.prevent="changePassword">
        <div v-field class="field">
          <label>Текущий</label>
          <input v-model="pwd.current" type="password" autocomplete="current-password" required />
        </div>
        <div v-field class="field">
          <label>Новый</label>
          <input v-model="pwd.next" type="password" autocomplete="new-password" required minlength="8" />
        </div>
        <button class="btn btn--accent" type="submit" :disabled="pwdBusy">Сохранить</button>
      </form>
      <p class="hint">Если входили через Яндекс и пароля не было — восстановите его через «Забыли пароль» на входе.</p>
    </section>
  </template>
</template>

<script setup>
import { onMounted, reactive, ref, watch, watchEffect } from 'vue'
import { RouterLink } from 'vue-router'
import FeatureLockNotice from '../components/FeatureLockNotice.vue'
import { errorText, webApi } from '../api/web'
import { loadProfile, setUser, useProfile } from '../composables/useProfile'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'
import { formatDate } from '../utils/format'

/** tab: profile — почта, вход, пароль; notifications — Telegram и утренний дайджест. */
const props = defineProps({ tab: { type: String, default: 'profile' } })

// Вкладки — одна страница: при переходе между ними меняется только заголовок.
watchEffect(() => useHead(`${props.tab === 'notifications' ? 'Уведомления' : 'Профиль'} — AiMarketing`, true))

const loading = ref(true)
const { user } = useProfile()
const verifyBusy = ref(false)
const name = ref('')
const nameBusy = ref(false)

watch(() => user.value?.name, (value) => (name.value = value || ''), { immediate: true })

async function saveName() {
  nameBusy.value = true
  try {
    setUser((await webApi.updateProfile({ name: name.value })).user)
    pushToast('Имя сохранено')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    nameBusy.value = false
  }
}
const telegram = ref({ configured: false, linked: false })
const tgLink = ref('')
const tgCode = ref('')
const tgBusy = ref(false)
const digest = ref(null)
const digestBusy = ref(false)
const digestText = ref('')
const pwd = reactive({ current: '', next: '' })
const pwdBusy = ref(false)

async function resendVerify() {
  verifyBusy.value = true
  try {
    const res = await webApi.resendVerify()
    if (res.sent) pushToast(`Письмо отправлено на ${user.value.email}`)
    else {
      // Не ушло: почта уже подтверждена или отправка выключена.
      await loadProfile(true)
      if (!user.value?.email_verified) pushToast('Не удалось отправить письмо. Попробуйте позже.', 'error')
    }
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    verifyBusy.value = false
  }
}

async function loadTelegram() {
  try {
    telegram.value = await webApi.telegramStatus()
    if (telegram.value.linked) {
      tgLink.value = ''
      tgCode.value = ''
      digest.value = await webApi.digestSettings().catch(() => null)
    }
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

async function linkTelegram() {
  tgBusy.value = true
  try {
    const data = await webApi.telegramLink()
    if (!data.configured) {
      pushToast('Бот не настроен на сервере', 'error')
      return
    }
    tgLink.value = data.url || ''
    tgCode.value = data.code || ''
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    tgBusy.value = false
  }
}

async function unlinkTelegram() {
  tgBusy.value = true
  try {
    await webApi.telegramUnlink()
    pushToast('Telegram отвязан')
    await loadTelegram()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    tgBusy.value = false
  }
}

async function testTelegram() {
  tgBusy.value = true
  try {
    await webApi.telegramTest()
    pushToast('Тестовое сообщение отправлено')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    tgBusy.value = false
  }
}

async function saveDigest(patch) {
  digestBusy.value = true
  try {
    digest.value = await webApi.updateDigest(patch)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    digestBusy.value = false
  }
}

async function previewDigest() {
  digestBusy.value = true
  try {
    digestText.value = (await webApi.digestPreview()).text
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    digestBusy.value = false
  }
}

async function sendDigest() {
  digestBusy.value = true
  try {
    await webApi.digestSend()
    pushToast('Дайджест отправлен в Telegram')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    digestBusy.value = false
  }
}

async function changePassword() {
  pwdBusy.value = true
  try {
    await webApi.changePassword({ current_password: pwd.current, new_password: pwd.next })
    pwd.current = ''
    pwd.next = ''
    pushToast('Пароль обновлён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    pwdBusy.value = false
  }
}

onMounted(async () => {
  try {
    await Promise.all([loadProfile(true), loadTelegram()])
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
})
</script>
