<template>
  <AppShell>
    <div class="cabinet__head">
      <div>
        <h1>Настройки</h1>
        <p>
          <template v-if="user">{{ user.name || user.email }}<span v-if="user.name && user.email"> · {{ user.email }}</span></template>
          <template v-else>Профиль, ИИ-ассистент, уведомления</template>
        </p>
      </div>
    </div>

    <div v-if="loading" class="panel"><p class="empty">Загружаем настройки…</p></div>

    <template v-else>
      <section class="panel" id="ai">
        <div class="panel__head">
          <h2>ИИ-ассистент</h2>
          <span class="chip" :class="ai.configured ? '' : 'chip--warning'">
            {{ ai.configured ? 'подключён' : 'не подключён' }}
          </span>
        </div>
        <p class="hint">
          Ассистент работает на вашем ключе: запросы идут напрямую к выбранному провайдеру, оплата — по вашему тарифу.
          Ключ хранится в зашифрованном виде. Что ассистенту можно менять без подтверждения, задаётся в настройках
          каждого пространства.
        </p>
        <form class="form-grid" @submit.prevent="saveAi">
          <div class="field">
            <label>Провайдер</label>
            <select v-model="aiForm.provider">
              <option v-for="p in ai.providers" :key="p.id" :value="p.id">{{ p.label }}</option>
            </select>
          </div>
          <div class="field">
            <label>Модель</label>
            <input v-model.trim="aiForm.model" type="text" :placeholder="defaultModel" autocomplete="off" />
          </div>
          <div v-if="aiForm.provider === 'openai'" class="field field--wide">
            <label>Адрес API</label>
            <input v-model.trim="aiForm.base_url" type="url" :placeholder="ai.default_base_url" autocomplete="off" />
            <small class="field-hint">
              Любой OpenAI-совместимый сервис: OpenAI, OpenRouter, DeepSeek, локальный vLLM за прокси и т.п. Пусто —
              {{ ai.default_base_url }}.
            </small>
          </div>
          <div v-else class="field field--wide">
            <label>Каталог Yandex Cloud (folder_id)</label>
            <input v-model.trim="aiForm.folder_id" type="text" placeholder="b1g…" autocomplete="off" required />
            <small class="field-hint">Ключ — API-ключ сервисного аккаунта с ролью ai.languageModels.user.</small>
          </div>
          <div class="field field--wide">
            <label>API-ключ</label>
            <input
              v-model="aiForm.api_key"
              type="password"
              autocomplete="new-password"
              :placeholder="ai.has_key ? `Сохранён ${ai.key_hint} — оставьте пустым, чтобы не менять` : 'Вставьте ключ'"
            />
          </div>
          <div class="field">
            <label>Температура: {{ Number(aiForm.temperature).toFixed(1) }}</label>
            <input v-model.number="aiForm.temperature" type="range" min="0" max="1" step="0.1" />
            <small class="field-hint">Ниже — точнее и стабильнее, выше — разнообразнее тексты.</small>
          </div>
          <div class="form-actions">
            <button class="btn btn--accent" type="submit" :disabled="aiBusy">Сохранить</button>
            <button class="btn btn--ghost" type="button" :disabled="aiBusy || !ai.configured" @click="testAi">
              Проверить
            </button>
            <button v-if="ai.has_key" class="btn btn--ghost btn--danger" type="button" :disabled="aiBusy" @click="deleteKey">
              Удалить ключ
            </button>
          </div>
        </form>
        <p v-if="aiTest" class="sync-status" :class="aiTest.ok ? 'is-ok' : 'is-error'">
          <template v-if="aiTest.ok">Работает · {{ aiTest.model }} ответил: «{{ aiTest.reply }}»</template>
          <template v-else>{{ aiTest.error }}</template>
        </p>
      </section>

      <section class="panel">
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
              <div class="field">
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
          <div class="field">
            <label>Текущий</label>
            <input v-model="pwd.current" type="password" autocomplete="current-password" required />
          </div>
          <div class="field">
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
import { askConfirm } from '../composables/useConfirm'
import { computed, onMounted, reactive, ref } from 'vue'
import AppShell from '../components/AppShell.vue'
import { aiApi, errorText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'
import { formatDate } from '../utils/format'

useHead('Настройки — AiMarketing', true)

const loading = ref(true)
const user = ref(null)
const ai = ref({ providers: [], default_base_url: '', configured: false, has_key: false, key_hint: '' })
const aiForm = reactive({ provider: 'openai', model: '', base_url: '', folder_id: '', api_key: '', temperature: 0.3 })
const aiBusy = ref(false)
const aiTest = ref(null)
const telegram = ref({ configured: false, linked: false })
const tgLink = ref('')
const tgCode = ref('')
const tgBusy = ref(false)
const digest = ref(null)
const digestBusy = ref(false)
const digestText = ref('')
const pwd = reactive({ current: '', next: '' })
const pwdBusy = ref(false)

const defaultModel = computed(
  () => ai.value.providers.find((p) => p.id === aiForm.provider)?.default_model || '',
)

function fillAiForm(data) {
  ai.value = data
  aiForm.provider = data.provider || 'openai'
  aiForm.model = data.model || ''
  aiForm.base_url = data.base_url || ''
  aiForm.folder_id = data.folder_id || ''
  aiForm.temperature = data.temperature ?? 0.3
  aiForm.api_key = ''
}

async function saveAi() {
  aiBusy.value = true
  aiTest.value = null
  try {
    const payload = {
      provider: aiForm.provider,
      model: aiForm.model,
      base_url: aiForm.provider === 'openai' ? aiForm.base_url : '',
      folder_id: aiForm.provider === 'yandexgpt' ? aiForm.folder_id : '',
      temperature: aiForm.temperature,
      api_key: aiForm.api_key ? aiForm.api_key : null,
    }
    fillAiForm(await aiApi.saveSettings(payload))
    pushToast(ai.value.configured ? 'Настройки ИИ сохранены' : 'Сохранено — осталось указать ключ')
    if (ai.value.configured && payload.api_key) await testAi()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    aiBusy.value = false
  }
}

async function deleteKey() {
  if (!await askConfirm('Удалить сохранённый ключ? Ассистент перестанет работать, пока не добавите новый.')) return
  aiBusy.value = true
  try {
    fillAiForm(
      await aiApi.saveSettings({
        provider: ai.value.provider,
        model: ai.value.model,
        base_url: ai.value.base_url,
        folder_id: ai.value.folder_id,
        temperature: ai.value.temperature,
        api_key: '',
      }),
    )
    aiTest.value = null
    pushToast('Ключ удалён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    aiBusy.value = false
  }
}

async function testAi() {
  aiBusy.value = true
  try {
    const res = await aiApi.test()
    aiTest.value = { ok: true, model: res.model, reply: String(res.reply || '').slice(0, 120) }
  } catch (err) {
    aiTest.value = { ok: false, error: errorText(err) }
  } finally {
    aiBusy.value = false
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
    const [me, aiSettings] = await Promise.all([webApi.me(), aiApi.settings(), loadTelegram()])
    user.value = me.user
    fillAiForm(aiSettings)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
})
</script>
