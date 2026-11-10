<template>
  <AppShell>
    <div class="cabinet__head">
      <div>
        <h1>Настройки</h1>
        <p>
          <template v-if="user">{{ user.name || user.email }}<span v-if="user.name && user.email"> · {{ user.email }}</span></template>
          <template v-else>Профиль, лимит ИИ, уведомления</template>
        </p>
      </div>
    </div>

    <div v-if="loading" class="panel"><p class="empty">Загружаем настройки…</p></div>

    <template v-else>
      <section class="panel" id="ai-usage">
        <div class="panel__head">
          <h2>ИИ: лимит месяца</h2>
          <span v-if="usage" class="chip" :class="usageChip.cls">{{ usageChip.text }}</span>
        </div>
        <p v-if="!usage" class="empty">Не удалось загрузить расход.</p>
        <template v-else>
          <template v-if="usage.limited">
            <div
              class="meter"
              :class="usageTone"
              role="progressbar"
              :aria-valuenow="usage.used_percent"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <span :style="{ width: `${usage.used_percent}%` }"></span>
            </div>
            <p class="sync-status" :class="usage.exhausted ? 'is-error' : ''">
              <template v-if="usage.exhausted">
                Лимит исчерпан — ИИ-ассистент, Вордстат и картинки недоступны до {{ formatDate(usage.period_end) }}
              </template>
              <template v-else>
                Использовано {{ pct(usage.used_percent) }} · осталось {{ pct(usage.left_percent) }} · обновится
                {{ formatDate(usage.period_end) }}
              </template>
            </p>
          </template>
          <p v-else class="hint">Лимит не установлен. Ниже — на что уходит расход в этом месяце.</p>
          <p v-if="!usage.by_kind.length" class="empty">В этом месяце ИИ, Вордстат и картинки ещё не использовались.</p>
          <div v-else class="usage-split">
            <div v-for="group in usageGroups" :key="group.key">
              <h3 class="subhead">{{ group.title }}</h3>
              <ul class="usage-shares">
                <li v-for="row in group.rows" :key="row.id">
                  <span>
                    {{ row.label }}
                    <small>{{ num(row.calls) }} {{ plural(row.calls, 'запрос', 'запроса', 'запросов') }}</small>
                  </span>
                  <strong>{{ pct(row.percent) }}</strong>
                </li>
              </ul>
            </div>
          </div>
        </template>
      </section>

      <section class="panel" id="telegram">
        <h2>Telegram</h2>
        <p class="hint">
          Утренний дайджест по всем пространствам и уведомления о срабатывании правил.
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

      <section class="panel">
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
  </AppShell>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import AppShell from '../components/AppShell.vue'
import { aiApi, errorText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'
import { formatDate, num, pct, plural } from '../utils/format'

useHead('Настройки — AiMarketing', true)

const loading = ref(true)
const user = ref(null)
const usage = ref(null)
const telegram = ref({ configured: false, linked: false })
const tgLink = ref('')
const tgCode = ref('')
const tgBusy = ref(false)
const digest = ref(null)
const digestBusy = ref(false)
const digestText = ref('')
const pwd = reactive({ current: '', next: '' })
const pwdBusy = ref(false)

const usageTone = computed(() => {
  const p = usage.value?.used_percent ?? 0
  return p >= 100 ? 'is-over' : p >= 80 ? 'is-near' : ''
})

const usageChip = computed(() => {
  const u = usage.value
  if (!u?.limited) return { text: 'без лимита', cls: '' }
  if (u.exhausted) return { text: 'исчерпан', cls: 'chip--warning' }
  return { text: `осталось ${pct(u.left_percent)}`, cls: u.used_percent >= 80 ? 'chip--warning' : 'chip--ok' }
})

// С лимитом — доли лимита, без лимита — доли всего расхода за месяц.
const usageGroups = computed(() => {
  const u = usage.value
  if (!u) return []
  return [
    { key: 'kind', title: u.limited ? 'Куда ушёл лимит' : 'Куда ушёл расход', rows: u.by_kind },
    { key: 'project', title: 'По проектам', rows: u.by_project },
  ]
})

async function loadUsage() {
  try {
    usage.value = await aiApi.usage()
  } catch (err) {
    usage.value = null
    pushToast(errorText(err), 'error')
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
    const [me] = await Promise.all([webApi.me(), loadTelegram(), loadUsage()])
    user.value = me.user
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
})
</script>
