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
          <div v-field class="field">
            <label>Провайдер</label>
            <select v-model="aiForm.provider">
              <option v-for="p in ai.providers" :key="p.id" :value="p.id">{{ p.label }}</option>
            </select>
          </div>
          <div v-field class="field">
            <label>Модель</label>
            <input v-model.trim="aiForm.model" type="text" :placeholder="defaultModel" autocomplete="off" />
          </div>
          <div v-if="aiForm.provider === 'openai'" v-field class="field field--wide">
            <label>Адрес API</label>
            <input v-model.trim="aiForm.base_url" type="url" :placeholder="ai.default_base_url" autocomplete="off" />
            <small class="field-hint">
              Любой OpenAI-совместимый сервис: OpenAI, OpenRouter, DeepSeek, локальный vLLM за прокси и т.п. Пусто —
              {{ ai.default_base_url }}.
            </small>
          </div>
          <div v-else v-field class="field field--wide">
            <label>Каталог Yandex Cloud (folder_id)</label>
            <input v-model.trim="aiForm.folder_id" type="text" placeholder="b1g…" autocomplete="off" required />
            <small class="field-hint">Ключ — API-ключ сервисного аккаунта с ролью ai.languageModels.user.</small>
          </div>
          <div v-field class="field field--wide">
            <label>API-ключ</label>
            <input
              v-model="aiForm.api_key"
              type="password"
              autocomplete="new-password"
              :placeholder="ai.has_key ? `Сохранён ${ai.key_hint} — оставьте пустым, чтобы не менять` : 'Вставьте ключ'"
            />
          </div>
          <div v-field class="field">
            <label>Быстрая модель</label>
            <input v-model.trim="aiForm.model_fast" type="text" placeholder="как основная" autocomplete="off" />
            <small class="field-hint">Дешевле и быстрее — для массовой работы. Пусто — всё делает основная.</small>
          </div>
          <div v-field class="field">
            <label>Температура: {{ Number(aiForm.temperature).toFixed(1) }}</label>
            <input v-model.number="aiForm.temperature" type="range" min="0" max="1" step="0.1" />
            <small class="field-hint">Ниже — точнее и стабильнее, выше — разнообразнее тексты.</small>
          </div>
          <p v-if="taskModels" class="field-hint">{{ taskModels }}</p>

          <h3>Цены и месячный лимит</h3>
          <div v-field class="field">
            <label>Основная: вход, ₽ за 1 млн токенов</label>
            <input v-model="aiForm.price_in" type="number" min="0" step="any" inputmode="decimal" placeholder="не задано" />
          </div>
          <div v-field class="field">
            <label>Основная: выход, ₽ за 1 млн токенов</label>
            <input v-model="aiForm.price_out" type="number" min="0" step="any" inputmode="decimal" placeholder="не задано" />
          </div>
          <template v-if="aiForm.model_fast">
            <div v-field class="field">
              <label>Быстрая: вход, ₽ за 1 млн токенов</label>
              <input v-model="aiForm.fast_price_in" type="number" min="0" step="any" inputmode="decimal" placeholder="как у основной" />
            </div>
            <div v-field class="field">
              <label>Быстрая: выход, ₽ за 1 млн токенов</label>
              <input v-model="aiForm.fast_price_out" type="number" min="0" step="any" inputmode="decimal" placeholder="как у основной" />
            </div>
          </template>
          <div v-field class="field">
            <label>Месячный лимит, ₽</label>
            <input v-model="aiForm.monthly_limit_rub" type="number" min="0" step="any" inputmode="decimal" placeholder="без лимита" />
            <small class="field-hint">
              Когда расход за календарный месяц дойдёт до лимита, чат, разметка и бриф остановятся до 1-го числа.
              Проверка подключения не ограничивается.
            </small>
          </div>
          <p class="field-hint">
            Цены — из тарифа провайдера, в рублях (долларовые пересчитайте по курсу). Без цен расход считается только в
            токенах, а лимит не работает.
          </p>
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
        <p v-if="aiTest?.fast" class="sync-status" :class="aiTest.fast.error ? 'is-error' : 'is-ok'">
          <template v-if="aiTest.fast.error">Быстрая модель {{ aiTest.fast.model }}: {{ aiTest.fast.error }}</template>
          <template v-else>Быстрая · {{ aiTest.fast.model }} ответил: «{{ aiTest.fast.reply }}»</template>
        </p>
      </section>

      <section class="panel" id="wordstat">
        <div class="panel__head">
          <h2>Вордстат</h2>
          <span class="chip" :class="wordstat.configured ? '' : 'chip--warning'">
            {{ wordstatChip }}
          </span>
        </div>
        <p class="hint">
          Ассистент берёт из Вордстата похожие запросы с частотой, когда подбирает ключевые фразы для вариантов кампаний.
          Необязательно.
        </p>
        <button v-if="!wordstatOpen" class="btn btn--ghost" type="button" @click="wsExpanded = true">Подключить</button>
        <p v-if="wordstatOpen" class="hint">
          Подключение — через Search API Yandex Cloud на ваш ключ: {{ wordstat.price_rub_per_1000 }} ₽ за 1000 запросов
          (один запрос — одна исходная фраза), до {{ wordstat.hourly_quota }} запросов в час. Ключ хранится в
          зашифрованном виде.
        </p>
        <form v-if="wordstatOpen" class="form-grid" @submit.prevent="saveWordstat">
          <div v-field class="field field--wide">
            <label>Каталог Yandex Cloud (folder_id)</label>
            <input v-model.trim="wsForm.folder_id" type="text" placeholder="b1g…" autocomplete="off" />
          </div>
          <div v-field class="field field--wide">
            <label>API-ключ</label>
            <input
              v-model="wsForm.api_key"
              type="password"
              autocomplete="new-password"
              :placeholder="wordstat.has_key ? `Сохранён ${wordstat.key_hint} — оставьте пустым, чтобы не менять` : 'Вставьте ключ'"
            />
            <small class="field-hint">
              В AI Studio: сервисный аккаунт с ролью search-api.webSearch.user, API-ключ с областью
              yc.search-api.execute. У облака должен быть активный платёжный аккаунт.
            </small>
          </div>
          <p v-if="wordstat.source === 'service'" class="field-hint">
            Сейчас работает ключ сервиса. Свой ключ заменит его.
          </p>
          <div class="form-actions">
            <button class="btn btn--accent" type="submit" :disabled="wsBusy">Сохранить</button>
            <button class="btn btn--ghost" type="button" :disabled="wsBusy || !wordstat.configured" @click="testWordstat">
              Проверить
            </button>
            <button
              v-if="wordstat.has_key"
              class="btn btn--ghost btn--danger"
              type="button"
              :disabled="wsBusy"
              @click="deleteWordstatKey"
            >
              Удалить ключ
            </button>
          </div>
        </form>
        <p v-if="wsTest" class="sync-status" :class="wsTest.ok ? 'is-ok' : 'is-error'">
          <template v-if="wsTest.ok">
            Работает · «{{ wsTest.phrase }}» — {{ num(wsTest.total) }} запросов за 30 дней<template v-if="wsTest.sample">,
              например: {{ wsTest.sample }}</template>
          </template>
          <template v-else>{{ wsTest.error }}</template>
        </p>
        <p v-else-if="wordstat.checked_at" class="sync-status" :class="wordstat.check_error ? 'is-error' : 'is-ok'">
          Проверено {{ formatDateTime(wordstat.checked_at) }} ·
          {{ wordstat.check_error || 'работает' }}
        </p>
      </section>

      <section v-if="usage && (ai.configured || usage.months.length > 1 || usage.total.calls)" class="panel" id="ai-usage">
        <div class="panel__head">
          <h2>Расход ИИ</h2>
          <select
            v-if="usage.months.length > 1"
            :value="usage.month"
            :disabled="usageBusy"
            aria-label="Месяц"
            @change="loadUsage($event.target.value)"
          >
            <option v-for="m in usage.months" :key="m" :value="m">{{ monthLabel(m) }}</option>
          </select>
        </div>
        <template v-if="usage.current && usage.limit_rub">
          <div class="meter" :class="limitTone" role="progressbar" :aria-valuenow="limitPct" aria-valuemin="0" aria-valuemax="100">
            <span :style="{ width: `${limitPct}%` }"></span>
          </div>
          <p class="sync-status" :class="usage.remaining_rub <= 0 ? 'is-error' : ''">
            Потрачено {{ rubAi(usage.spent_rub) }} из {{ rubAi(usage.limit_rub) }} ·
            <template v-if="usage.remaining_rub > 0">осталось {{ rubAi(usage.remaining_rub) }}</template>
            <template v-else>лимит исчерпан — ИИ остановлен до 1-го числа</template>
          </p>
        </template>
        <p v-if="!usage.total.calls" class="empty">
          {{ usage.current ? 'В этом месяце ИИ ещё не вызывался.' : 'В этом месяце вызовов ИИ не было.' }}
        </p>
        <template v-else>
          <div class="stats-grid">
            <div class="stat-card">
              <small>Вызовов</small>
              <strong>{{ num(usage.total.calls) }}</strong>
            </div>
            <div class="stat-card">
              <small>Токенов: вход</small>
              <strong>{{ num(usage.total.tokens_in) }}</strong>
            </div>
            <div class="stat-card">
              <small>Токенов: выход</small>
              <strong>{{ num(usage.total.tokens_out) }}</strong>
            </div>
            <div class="stat-card">
              <small>Потрачено</small>
              <strong>{{ usage.total.cost_rub === null ? '—' : rubAi(usage.total.cost_rub) }}</strong>
            </div>
          </div>
          <p v-if="usage.total.unpriced" class="hint">
            <template v-if="usage.total.unpriced === usage.total.calls">Цены моделей не заданы — расход виден только в токенах.</template>
            <template v-else>
              {{ usage.total.unpriced }} {{ plural(usage.total.unpriced, 'вызов', 'вызова', 'вызовов') }} без цены — в рублях не
              учтены.
            </template>
            Цены задаются выше, в настройках ассистента.
          </p>
          <p v-if="usage.total.estimated" class="hint">
            {{ usage.total.estimated }} {{ plural(usage.total.estimated, 'вызов', 'вызова', 'вызовов') }} — провайдер не
            сообщил число токенов, посчитано по длине текста.
          </p>
          <template v-for="group in usageGroups" :key="group.key">
            <h3 class="subhead">{{ group.title }}</h3>
            <div class="table-scroll">
              <table class="table usage-table">
                <thead>
                  <tr>
                    <th>{{ group.column }}</th>
                    <th class="num">Вызовов</th>
                    <th class="num">Вход</th>
                    <th class="num">Выход</th>
                    <th class="num">₽</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in group.rows" :key="row.key">
                    <td>{{ row.name }}</td>
                    <td class="num">{{ num(row.calls) }}</td>
                    <td class="num">{{ num(row.tokens_in) }}</td>
                    <td class="num">{{ num(row.tokens_out) }}</td>
                    <td class="num">{{ rubAi(row.cost_rub) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
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
import { askConfirm } from '../composables/useConfirm'
import { computed, onMounted, reactive, ref } from 'vue'
import AppShell from '../components/AppShell.vue'
import { aiApi, errorText, webApi } from '../api/web'
import { pushToast } from '../composables/useToasts'
import { useHead } from '../utils/useHead'
import { formatDate, formatDateTime, num, plural, rubAi } from '../utils/format'

useHead('Настройки — AiMarketing', true)

const loading = ref(true)
const user = ref(null)
const ai = ref({ providers: [], default_base_url: '', configured: false, has_key: false, key_hint: '' })
const PRICE_FIELDS = ['price_in', 'price_out', 'fast_price_in', 'fast_price_out', 'monthly_limit_rub']
const aiForm = reactive({
  provider: 'openai',
  model: '',
  model_fast: '',
  base_url: '',
  folder_id: '',
  api_key: '',
  temperature: 0.3,
  ...Object.fromEntries(PRICE_FIELDS.map((f) => [f, ''])),
})
const aiBusy = ref(false)
const aiTest = ref(null)
const wordstat = ref({ configured: false, source: '', has_key: false, key_hint: '', price_rub_per_1000: 20, hourly_quota: 100 })
const wsForm = reactive({ folder_id: '', api_key: '' })
const wsBusy = ref(false)
const wsTest = ref(null)
const usage = ref(null)
const usageBusy = ref(false)
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

/** «ИИ-чат — основная; Разметка запросов, Черновик брифа — быстрая». */
const taskModels = computed(() => {
  const tasks = ai.value.tasks || []
  const by = (tier) => tasks.filter((t) => t.tier === tier).map((t) => t.label).join(', ')
  const parts = [[by('smart'), 'основная'], [by('fast'), aiForm.model_fast ? 'быстрая' : 'тоже основная, пока быстрая не задана']]
  return parts.filter(([labels]) => labels).map(([labels, model]) => `${labels} — ${model}`).join('; ')
})

const limitPct = computed(() => {
  const u = usage.value
  if (!u?.limit_rub) return 0
  return Math.min(100, Math.round((u.spent_rub / u.limit_rub) * 100))
})
const limitTone = computed(() => (limitPct.value >= 100 ? 'is-over' : limitPct.value >= 80 ? 'is-near' : ''))

const usageGroups = computed(() => {
  const u = usage.value
  if (!u) return []
  const groups = [
    { key: 'task', title: 'По задачам', column: 'Задача', rows: u.by_task.map((r) => ({ ...r, key: r.task, name: r.label })) },
    {
      key: 'ws',
      title: 'По пространствам',
      column: 'Пространство',
      rows: u.by_workspace.map((r) => ({ ...r, key: r.workspace_id ?? 'none', name: r.name })),
    },
    { key: 'model', title: 'По моделям', column: 'Модель', rows: u.by_model.map((r) => ({ ...r, key: r.model, name: r.model })) },
  ]
  return groups.filter((g) => g.rows.length)
})

function monthLabel(key) {
  const [y, m] = key.split('-').map(Number)
  const text = new Date(y, m - 1, 1).toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' }).replace(/\s*г\.$/, '')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Пустое поле — null (не задано), иначе число. */
function money(value) {
  return value === '' || value === null || value === undefined ? null : Number(value)
}

async function loadUsage(month = '') {
  usageBusy.value = true
  try {
    usage.value = await aiApi.usage(month)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    usageBusy.value = false
  }
}

function fillAiForm(data) {
  ai.value = data
  aiForm.provider = data.provider || 'openai'
  aiForm.model = data.model || ''
  aiForm.model_fast = data.model_fast || ''
  aiForm.base_url = data.base_url || ''
  aiForm.folder_id = data.folder_id || ''
  aiForm.temperature = data.temperature ?? 0.3
  aiForm.api_key = ''
  for (const f of PRICE_FIELDS) aiForm[f] = data[f] ?? ''
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
      model_fast: aiForm.model_fast,
      ...Object.fromEntries(PRICE_FIELDS.map((f) => [f, money(aiForm[f])])),
    }
    fillAiForm(await aiApi.saveSettings(payload))
    // Лимит и цены меняют «осталось» в сводке.
    loadUsage(usage.value?.current ? '' : usage.value?.month || '')
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
    aiTest.value = {
      ok: true,
      model: res.model,
      reply: String(res.reply || '').slice(0, 120),
      fast: res.fast ? { ...res.fast, reply: String(res.fast.reply || '').slice(0, 120) } : null,
    }
    if (usage.value?.current) loadUsage()
  } catch (err) {
    aiTest.value = { ok: false, error: errorText(err) }
  } finally {
    aiBusy.value = false
  }
}

// Пока Вордстат не настроен, форма ключа свёрнута: большинству он не нужен.
const wsExpanded = ref(false)
const wordstatOpen = computed(
  () => wsExpanded.value || wordstat.value.configured || wordstat.value.has_key || !!wordstat.value.folder_id,
)
const wordstatChip = computed(() => {
  const w = wordstat.value
  if (!w.configured) return 'не подключён'
  if (w.source === 'service') return 'ключ сервиса'
  return w.check_error ? 'ошибка проверки' : 'подключён'
})

function fillWordstat(data) {
  wordstat.value = data
  wsForm.folder_id = data.folder_id || ''
  wsForm.api_key = ''
}

async function saveWordstat() {
  wsBusy.value = true
  wsTest.value = null
  try {
    const apiKey = wsForm.api_key.trim()
    fillWordstat(await aiApi.saveWordstat({ folder_id: wsForm.folder_id, api_key: apiKey || null }))
    pushToast(wordstat.value.source === 'own' ? 'Вордстат сохранён' : 'Сохранено — осталось указать ключ и каталог')
    if (wordstat.value.source === 'own' && apiKey) await testWordstat()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    wsBusy.value = false
  }
}

async function deleteWordstatKey() {
  if (!await askConfirm('Удалить ключ Вордстата? Ассистент перестанет подбирать фразы по частоте.')) return
  wsBusy.value = true
  try {
    fillWordstat(await aiApi.saveWordstat({ folder_id: wordstat.value.folder_id, api_key: '' }))
    wsTest.value = null
    pushToast('Ключ Вордстата удалён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    wsBusy.value = false
  }
}

async function testWordstat() {
  wsBusy.value = true
  try {
    const res = await aiApi.testWordstat()
    const sample = (res.sample || []).map((s) => `«${s.phrase}» ${num(s.count)}`).join(', ')
    wsTest.value = { ok: true, phrase: res.phrase, total: res.total, sample }
  } catch (err) {
    wsTest.value = { ok: false, error: errorText(err) }
  } finally {
    wsBusy.value = false
    // Итог проверки сохраняется у ключа — подтягиваем, чтобы чип не врал.
    aiApi.wordstat().then((data) => { wordstat.value = data }).catch(() => {})
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
    const [me, aiSettings, wordstatSettings] = await Promise.all([
      webApi.me(),
      aiApi.settings(),
      aiApi.wordstat(),
      loadTelegram(),
      loadUsage(),
    ])
    user.value = me.user
    fillAiForm(aiSettings)
    fillWordstat(wordstatSettings)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    loading.value = false
  }
})
</script>
