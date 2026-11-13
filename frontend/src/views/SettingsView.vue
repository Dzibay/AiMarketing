<template>
  <div v-if="loading" class="panel"><p class="empty">Загружаем настройки…</p></div>

  <template v-else>
    <template v-if="tab === 'profile'">
      <section class="panel">
        <h2>Профиль</h2>
        <dl class="profile-grid">
          <div class="profile-grid__row">
            <dt><label for="profile-name">Имя</label></dt>
            <dd>
              <form class="profile-name" @submit.prevent="saveName">
                <div class="field">
                  <input
                    id="profile-name"
                    v-model.trim="name"
                    type="text"
                    maxlength="120"
                    placeholder="Как к вам обращаться"
                    autocomplete="name"
                  />
                </div>
                <button class="btn btn--accent btn--sm" type="submit" :disabled="nameBusy || name === (user?.name || '')">
                  Сохранить
                </button>
              </form>
            </dd>
          </div>
          <div class="profile-grid__row">
            <dt>Почта</dt>
            <dd>
              <span class="profile-email">{{ user?.email || '—' }}</span>
              <template v-if="user?.email">
                <UiStatus v-if="user.email_verified" tone="ok" label="подтверждена" />
                <template v-else>
                  <UiStatus tone="warn" label="не подтверждена" />
                  <button class="link-btn" type="button" :disabled="verifyBusy" @click="resendVerify">
                    Отправить письмо ещё раз
                  </button>
                </template>
              </template>
            </dd>
          </div>
          <div class="profile-grid__row">
            <dt>Вход через Яндекс</dt>
            <dd>{{ user?.has_yandex ? 'привязан' : 'нет' }}</dd>
          </div>
          <div v-if="user?.created_at" class="profile-grid__row">
            <dt>С нами с</dt>
            <dd>{{ formatDate(user.created_at) }}</dd>
          </div>
        </dl>
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
        служебные письма — подтверждение и сброс пароля, а также напоминание об окончании тарифа за 3 дня и за 1 день.
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
            <UiSwitch
              class="check-row"
              :checked="digest.enabled"
              :disabled="digestBusy"
              @change="saveDigest({ enabled: $event.target.checked })"
            >
              Присылать каждое утро
            </UiSwitch>
            <div v-field class="field">
              <label>Время, МСК</label>
              <UiSelect
                :model-value="digest.hour"
                :options="HOURS"
                :searchable="false"
                :disabled="digestBusy || !digest.enabled"
                @change="saveDigest({ hour: $event })"
              />
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

    <template v-if="tab === 'interface'">
      <section class="panel">
        <h2>Экспертный режим</h2>
        <UiSwitch :checked="expert" @change="setExpert($event.target.checked)">
          {{ expert ? 'Включён' : 'Выключен' }}
        </UiSwitch>
        <p class="hint">
          Без него кабинет показывает главное: главную, кампании, запросы, отчёты и историю, а ручные настройки ИИ
          приносит готовыми предложениями. Переключается и в меню профиля, выбор хранится в этом браузере.
        </p>
        <div v-scroll-x class="table-scroll">
          <table class="table mode-table">
            <caption>Что добавляет экспертный режим</caption>
            <thead>
              <tr>
                <th scope="col">Что появляется</th>
                <th scope="col">Где</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in EXPERT_ADDS" :key="row.what">
                <td>{{ row.what }}</td>
                <td>{{ row.where }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section class="panel">
        <h2>Тема</h2>
        <div class="seg" role="group" aria-label="Тема">
          <button type="button" :class="{ active: !isDark }" @click="isDark && toggleTheme($event)">Светлая</button>
          <button type="button" :class="{ active: isDark }" @click="!isDark && toggleTheme($event)">Тёмная</button>
        </div>
      </section>
    </template>

    <section v-if="tab === 'profile'" class="panel">
      <h2>Смена пароля</h2>
      <form class="pwd-form" @submit.prevent="changePassword">
        <div v-field class="field">
          <label>Текущий пароль</label>
          <input v-model="pwd.current" type="password" autocomplete="current-password" required />
        </div>
        <div v-field class="field">
          <label>Новый пароль</label>
          <input v-model="pwd.next" type="password" autocomplete="new-password" required minlength="8" />
          <small class="field-hint">Не короче 8 символов.</small>
        </div>
        <div class="pwd-form__acts">
          <button class="btn btn--accent" type="submit" :disabled="pwdBusy">Сменить пароль</button>
        </div>
      </form>
      <p class="hint">Если входили через Яндекс и пароля не было — восстановите его через «Забыли пароль» на входе.</p>
    </section>
  </template>
</template>

<script setup>
import { onMounted, reactive, ref, watch, watchEffect } from 'vue'
import { RouterLink } from 'vue-router'
import FeatureLockNotice from '../components/FeatureLockNotice.vue'
import UiSelect from '../components/ui/UiSelect.vue'
import UiStatus from '../components/ui/UiStatus.vue'
import UiSwitch from '../components/ui/UiSwitch.vue'
import { errorText, webApi } from '../api/web'
import { useExpert } from '../composables/useExpert'
import { loadProfile, setUser, useProfile } from '../composables/useProfile'
import { useTheme } from '../composables/useTheme'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'
import { formatDate } from '../utils/format'

const HOURS = Array.from({ length: 24 }, (_, h) => ({ value: h, label: `${String(h).padStart(2, '0')}:00` }))

/** tab: profile — почта, вход, пароль; notifications — Telegram и утренний дайджест; interface — режим и тема. */
const props = defineProps({ tab: { type: String, default: 'profile' } })

const TAB_TITLES = { profile: 'Профиль', notifications: 'Уведомления', interface: 'Интерфейс' }
// Вкладки — одна страница: при переходе между ними меняется только заголовок.
watchEffect(() => useHead(`${TAB_TITLES[props.tab] || 'Настройки'} — Толк`, true))

const { expert, setExpert } = useExpert()

/** «Интерфейс»: что именно включает экспертный режим и где это искать. */
const EXPERT_ADDS = [
  { what: 'Автопилот: правила и эксперименты', where: 'Меню → Автопилот' },
  { what: 'Отчёты по площадкам и объявлениям', where: 'Отчёты проекта → Площадки, Объявления' },
  { what: 'Списки минус-фраз для нескольких кампаний', where: 'Запросы → Минус-фразы' },
  { what: 'Таблица кампаний со всеми цифрами, фильтры, архив и массовые действия', where: 'Кампании' },
  { what: 'Группы, объявления, ключевые фразы и ставки кампании', where: 'Карточка кампании' },
  { what: 'Таблица проектов и папки клиентов', where: 'Главная, список проектов в меню' },
  { what: 'Доступ ИИ, его память, кабинеты Директа, шаблон ссылки, клиент', where: 'Настройки проекта' },
  { what: 'Термины CPA, ДРР, CTR, номера счётчиков и кампаний', where: 'Везде' },
  { what: 'Горячие клавиши (список — по «?»)', where: 'Везде' },
]
const { isDark, toggleTheme } = useTheme()

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

<style scoped>
/* «Что добавляет экспертный режим»: подпись таблицы — как подзаголовок, «Где» — второстепенным цветом. */
.mode-table {
  width: 100%;
  margin-top: 0.75rem;
}

.mode-table caption {
  padding: 0 12px 6px;
  font-weight: 700;
  text-align: left;
}

.mode-table td:last-child {
  color: var(--muted);
}

/* Профиль: подпись слева, значение справа — одна сетка для всех строк, включая имя. */
.profile-grid {
  margin: 0.25rem 0 1rem;
}

.profile-grid__row {
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  gap: 0.3rem 1rem;
  align-items: center;
  min-height: 52px;
  padding: 0.55rem 0;
  border-top: 1px solid var(--line);
}

.profile-grid__row:first-child {
  border-top: 0;
}

.profile-grid dt {
  color: var(--muted);
  font-size: 0.88rem;
  font-weight: 600;
}

.profile-grid dt label {
  cursor: pointer;
}

.profile-grid dd {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem 0.75rem;
  min-width: 0;
  margin: 0;
  font-weight: 560;
}

.profile-email {
  overflow-wrap: anywhere;
}

/* В кабинете акцент — цвет текста, поэтому ссылку-кнопку отличает подчёркивание. */
.profile-grid .link-btn {
  font-size: 0.88rem;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.profile-grid .link-btn:disabled {
  opacity: 0.55;
  cursor: default;
}

.profile-name {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  max-width: 440px;
}

.profile-name .field {
  flex: 1 1 220px;
  margin: 0;
}

.profile-name .field input {
  width: 100%;
  padding-block: 0.55rem;
}

/* Смена пароля: два поля одной ширины, подписи сверху, кнопка под ними. */
.pwd-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: 0 1rem;
  max-width: 560px;
}

.pwd-form .field input {
  width: 100%;
}

.pwd-form__acts {
  grid-column: 1 / -1;
}

@media (max-width: 560px) {
  .profile-grid__row {
    grid-template-columns: minmax(0, 1fr);
  }

  .pwd-form {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
