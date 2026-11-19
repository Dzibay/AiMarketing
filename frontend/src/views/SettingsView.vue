<template>
  <UiSkeleton v-if="loading" variant="rows" :count="4" label="Загружаем настройки" />

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

    <section v-if="tab === 'notifications'" class="panel">
      <h2>Что и куда присылать</h2>
      <p class="hint">
        «В кабинете» — запись в колокольчике в верхней строке, «В Telegram» — сообщение от бота. Повторы одного события
        склеиваются: один и тот же сигнал приходит не чаще раза в неделю.
      </p>
      <p class="hint">
        «Срочное» проверяем каждый час и пишем об одном и том же не чаще раза в сутки. С 22:00 до 9:00 по Москве
        запись появится в колокольчике сразу, а в Telegram — утром, если проблема ещё не решена. Скрытое через «⋯»
        у сигнала не присылаем.
      </p>
      <p v-if="!notifyPrefs" class="empty">{{ notifyError || 'Загружаем…' }}</p>
      <div v-else v-scroll-x class="table-scroll">
        <table class="table notify-prefs">
          <thead>
            <tr>
              <th>Событие</th>
              <th>В кабинете</th>
              <th>В Telegram</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in notifyPrefs.events" :key="e.kind">
              <td>{{ e.label }}</td>
              <td>
                <UiSwitch
                  :checked="e.cabinet"
                  :disabled="notifyBusy"
                  :aria-label="`${e.label}: в кабинете`"
                  @change="saveNotify(e.kind, 'cabinet', $event.target.checked)"
                />
              </td>
              <td>
                <UiSwitch
                  :checked="e.telegram && tgReady"
                  :disabled="notifyBusy || !tgReady"
                  :aria-label="`${e.label}: в Telegram`"
                  @change="saveNotify(e.kind, 'telegram', $event.target.checked)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="notifyPrefs && !tgReady" class="hint">
        {{ notifyPrefs.telegram_available ? 'Чтобы получать события в Telegram, подключите бота ниже.' : 'Telegram-бот пока не настроен на сервере.' }}
      </p>
    </section>

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
        <template v-if="notifyPrefs">
          <h3 class="subhead">Решать из Telegram</h3>
          <p class="hint">
            У простых предложений ИИ появятся кнопки «Выполнить» и «Отклонить». Перед выполнением бот переспросит,
            а после — предложит «Вернуть как было». Где нужно выбрать строки, будет только «Открыть в кабинете».
          </p>
          <p class="hint">
            Внимание: кто откроет ваш Telegram, сможет менять рекламу. Включайте, только если телефон под паролем.
          </p>
          <div class="filters">
            <UiSwitch
              class="check-row"
              :checked="!!notifyPrefs.telegram_decide"
              :disabled="notifyBusy"
              @change="saveDecide($event.target.checked)"
            >
              Кнопки в Telegram
            </UiSwitch>
          </div>
        </template>
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
        <h2>Кто вы</h2>
        <p class="hint">
          От этого зависят быстрые вопросы на главной, готовые задачи, подсказки и то, как пишет ИИ: владельцу — простыми
          словами, специалисту — с терминами и цифрами. Выбор включает подходящий режим; режим можно переключить и
          отдельно.
        </p>
        <RoleChoice />
      </section>
      <section class="panel">
        <h2>Экспертный режим</h2>
        <UiSwitch v-coach="'mode'" :checked="expert" @change="setExpert($event.target.checked)">
          {{ expert ? 'Включён' : 'Выключен' }}
        </UiSwitch>
        <p class="hint">
          Без него кабинет показывает главное: главную, кампании, запросы, отчёты и историю, а ручные настройки ИИ
          приносит готовыми предложениями. Переключается и в меню профиля. Выбор сохраняется в аккаунте: на телефоне
          и другом компьютере режим будет тот же.
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

    <form v-if="tab === 'ai'" class="panel" @submit.prevent="saveAiPrefs">
      <h2>Мои правила для ИИ</h2>
      <p class="hint">
        ИИ читает это в каждом чате — общем и во всех проектах. Пишите то, что важно всегда: как вы работаете, чего не
        делать, как с вами говорить. Правила клиента и проекта важнее этих: их задают в настройках проекта.
      </p>
      <p v-if="!aiPrefs" class="empty">{{ aiPrefsError || 'Загружаем…' }}</p>
      <template v-else>
        <div v-field class="field">
          <label for="ai-rules">Правила</label>
          <textarea
            id="ai-rules"
            v-model="aiRules"
            class="ai-rules"
            rows="6"
            :maxlength="AI_RULES_MAX"
            placeholder="Например: без обещаний скидок; ставки меняй не больше чем на 20% за раз; отвечай коротко"
          ></textarea>
          <small class="field-hint">{{ aiRules.length }} из {{ AI_RULES_MAX }} символов</small>
        </div>
        <h3 class="subhead">Тон объявлений по умолчанию</h3>
        <p class="hint">Для проектов, где свой тон не выбран, и для текстов в общем чате.</p>
        <div class="seg" role="group" aria-label="Тон объявлений по умолчанию">
          <button
            v-for="t in AI_TONES"
            :key="t.id"
            type="button"
            :class="{ active: aiTone === t.id }"
            :aria-pressed="aiTone === t.id"
            @click="aiTone = t.id"
          >
            {{ t.label }}
          </button>
        </div>
        <div class="row-actions ai-prefs__acts">
          <button class="btn btn--accent" type="submit" :disabled="aiPrefsBusy || !aiPrefsChanged">
            {{ aiPrefsBusy ? 'Сохраняем…' : 'Сохранить' }}
          </button>
        </div>
      </template>
    </form>

    <AiReviewSchedule v-if="tab === 'ai'" />

    <ProjectDefaultsPanel v-if="tab === 'projects'" />

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
import UiSkeleton from '../components/ui/UiSkeleton.vue'
import { computed, onMounted, reactive, ref, watch, watchEffect } from 'vue'
import { RouterLink } from 'vue-router'
import AiReviewSchedule from '../components/AiReviewSchedule.vue'
import FeatureLockNotice from '../components/FeatureLockNotice.vue'
import ProjectDefaultsPanel from '../components/ProjectDefaultsPanel.vue'
import RoleChoice from '../components/RoleChoice.vue'
import UiSelect from '../components/ui/UiSelect.vue'
import UiStatus from '../components/ui/UiStatus.vue'
import UiSwitch from '../components/ui/UiSwitch.vue'
import { errorText, notifyApi, webApi } from '../api/web'
import { useExpert } from '../composables/useExpert'
import { loadProfile, setUser, useProfile } from '../composables/useProfile'
import { useTheme } from '../composables/useTheme'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'
import { formatDate } from '../utils/format'

const HOURS = Array.from({ length: 24 }, (_, h) => ({ value: h, label: `${String(h).padStart(2, '0')}:00` }))

/**
 * tab: profile — почта, вход, пароль; notifications — что и куда присылать, Telegram и утренний дайджест;
 * interface — режим и тема; ai — «Мои правила для ИИ» и тон по умолчанию для всех чатов.
 */
const props = defineProps({ tab: { type: String, default: 'profile' } })

const TAB_TITLES = { profile: 'Профиль', notifications: 'Уведомления', interface: 'Интерфейс', ai: 'ИИ', projects: 'Для новых проектов' }
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
  { what: 'Таблица проектов и папки клиентов', where: 'Главная, список проектов в развёрнутом меню' },
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
const notifyPrefs = ref(null)
const notifyError = ref('')
const notifyBusy = ref(false)
/** Переключатели «В Telegram» доступны, когда бот настроен и привязан. */
const tgReady = computed(() => !!(notifyPrefs.value?.telegram_available && telegram.value.linked))

async function loadNotify() {
  try {
    notifyPrefs.value = await notifyApi.settings()
  } catch (err) {
    notifyError.value = errorText(err)
  }
}

async function saveNotify(kind, where, value) {
  notifyBusy.value = true
  try {
    notifyPrefs.value = await notifyApi.saveSettings({ [kind]: { [where]: value } })
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    notifyBusy.value = false
  }
}
/** «Решать из Telegram»: кнопки «Выполнить / Отклонить» у предложений ИИ, по умолчанию выключено. */
async function saveDecide(value) {
  notifyBusy.value = true
  try {
    notifyPrefs.value = await notifyApi.saveSettings({ telegram_decide: value })
    pushToast(value ? 'Кнопки в Telegram включены' : 'Кнопки в Telegram выключены')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    notifyBusy.value = false
  }
}
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

// --- «ИИ»: правила маркетолога для всех чатов и тон по умолчанию -----------------------------------

const AI_RULES_MAX = 1000
const AI_TONES = [
  { id: '', label: 'Не задан' },
  { id: 'calm', label: 'Спокойный' },
  { id: 'sell', label: 'Продающий' },
  { id: 'expert', label: 'Экспертный' },
]
const aiPrefs = ref(null)
const aiPrefsError = ref('')
const aiPrefsBusy = ref(false)
const aiRules = ref('')
const aiTone = ref('')
const aiPrefsChanged = computed(
  () => !!aiPrefs.value && (aiRules.value !== aiPrefs.value.rules || aiTone.value !== aiPrefs.value.tone),
)

function setAiPrefs(prefs) {
  aiPrefs.value = { rules: prefs.rules || '', tone: prefs.tone || '' }
  aiRules.value = aiPrefs.value.rules
  aiTone.value = aiPrefs.value.tone
}

async function loadAiPrefs() {
  aiPrefsError.value = ''
  try {
    setAiPrefs(await webApi.aiPrefs())
  } catch (err) {
    aiPrefsError.value = errorText(err)
  }
}

async function saveAiPrefs() {
  aiPrefsBusy.value = true
  try {
    setAiPrefs(await webApi.saveAiPrefs({ rules: aiRules.value, tone: aiTone.value }))
    pushToast('Правила для ИИ сохранены')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    aiPrefsBusy.value = false
  }
}

watch(
  () => props.tab,
  (tab) => {
    if (tab === 'ai' && !aiPrefs.value) loadAiPrefs()
  },
  { immediate: true },
)

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
    await Promise.all([loadProfile(true), loadTelegram(), loadNotify()])
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

/* Смена пароля: поля столбиком на всю ширину колонки до 480 px, подписи сверху, кнопка под ними. */
.pwd-form {
  max-width: 480px;
}

.pwd-form .field input {
  width: 100%;
}

/* «ИИ»: поле правил на всю ширину колонки до 720 px. */
.ai-rules {
  width: 100%;
  max-width: 720px;
}

.ai-prefs__acts {
  margin-top: 16px;
}

@media (max-width: 560px) {
  .profile-grid__row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
