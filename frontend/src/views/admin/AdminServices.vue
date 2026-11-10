<template>
  <section class="panel" id="ai">
    <div class="panel__head">
      <h2>ИИ и модели</h2>
      <span class="chip" :class="ai.configured ? 'chip--ok' : 'chip--warning'">
        {{ ai.configured ? 'подключён' : 'не подключён' }}
      </span>
    </div>
    <p class="hint">
      Один ключ на всех пользователей. В чате пользователь выбирает класс — {{ classNames }} — и видит только шкалу
      месячного лимита в процентах: ни моделей, ни цен, ни токенов. Ключ хранится в зашифрованном виде.
    </p>
    <form class="form-grid" @submit.prevent="saveAi">
      <div v-field class="field">
        <label>Провайдер</label>
        <select v-model="aiForm.provider">
          <option v-for="p in ai.providers" :key="p.id" :value="p.id">{{ p.label }}</option>
        </select>
      </div>
      <div v-field class="field">
        <label>Температура: {{ Number(aiForm.temperature).toFixed(1) }}</label>
        <input v-model.number="aiForm.temperature" type="range" min="0" max="1" step="0.1" />
      </div>
      <p v-if="aiForm.provider === 'openai' && bridgeChat" class="field-hint">
        Запросы идут через <a href="#bridge">мост Cloudflare</a>, адрес API не нужен. Провайдер определяется по имени
        модели: claude-… → Anthropic, gemini-… → Google, остальное → OpenAI. Ключ — того же провайдера.
      </p>
      <div v-else-if="aiForm.provider === 'openai'" v-field class="field field--wide">
        <label>Адрес API</label>
        <input v-model.trim="aiForm.base_url" type="url" :placeholder="ai.default_base_url" autocomplete="off" />
        <small class="field-hint">Любой OpenAI-совместимый сервис. Пусто — {{ ai.default_base_url }}.</small>
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

      <h3>Классы моделей</h3>
      <p class="field-hint">
        Цена — единиц за 1 млн токенов: столько спишется с лимита пользователя. Себестоимость — ₽ за 1 млн токенов по
        тарифу провайдера, для маржи в «Расходе». Выключенный класс в чате не показывается.
      </p>
      <div class="table-scroll">
        <table class="table admin-classes">
          <thead>
            <tr>
              <th>Вкл.</th>
              <th>Название в чате</th>
              <th>Модель</th>
              <th class="num">Ед. · вход</th>
              <th class="num">Ед. · выход</th>
              <th class="num">₽ · вход</th>
              <th class="num">₽ · выход</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in aiForm.classes" :key="c.id">
              <td><input v-model="c.enabled" type="checkbox" :aria-label="`Класс «${c.name}» включён`" /></td>
              <td>
                <input v-model.trim="c.name" type="text" maxlength="40" :aria-label="`Название класса ${c.id}`" />
                <small class="cell-note model-dots">{{ dots(c.dots) }}</small>
              </td>
              <td>
                <input v-model.trim="c.model" type="text" :placeholder="defaultModel" autocomplete="off" aria-label="Модель" />
              </td>
              <td v-for="f in PRICE_FIELDS" :key="f" class="num">
                <input v-model="c[f]" type="number" min="0" step="any" inputmode="decimal" placeholder="—" :aria-label="f" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3>Фоновые задачи</h3>
      <div v-for="t in ai.tasks" :key="t.id" v-field class="field">
        <label>{{ t.label }}</label>
        <select v-model="aiForm.task_classes[t.id]">
          <option v-for="c in aiForm.classes" :key="c.id" :value="c.id">{{ c.name }}</option>
        </select>
      </div>

      <h3>Лимит по умолчанию</h3>
      <div v-field class="field">
        <label>Единиц в месяц на пользователя</label>
        <input v-model="aiForm.default_units_limit" type="number" min="0" step="any" inputmode="decimal" placeholder="без лимита" />
        <small class="field-hint">Свой лимит пользователя задаётся во вкладке «Пользователи» и заменяет этот.</small>
      </div>

      <div class="form-actions">
        <button class="btn btn--accent" type="submit" :disabled="aiBusy">Сохранить</button>
        <button class="btn btn--ghost" type="button" :disabled="aiBusy || !ai.configured" @click="testAi">
          {{ aiBusy && aiTesting ? 'Проверяем…' : 'Проверить подключение' }}
        </button>
        <button v-if="ai.has_key" class="btn btn--ghost btn--danger" type="button" :disabled="aiBusy" @click="deleteAiKey">
          Удалить ключ
        </button>
      </div>
    </form>
    <p v-if="aiTest?.error" class="sync-status is-error">{{ aiTest.error }}</p>
    <template v-else-if="aiTest">
      <p v-for="m in aiTest.models" :key="m.id" class="sync-status" :class="m.error ? 'is-error' : 'is-ok'">
        <b>{{ m.label }}</b> · {{ m.model }}<template v-if="aiTest.bridge"> через мост</template>:
        <template v-if="m.error">{{ m.error }}</template>
        <template v-else>
          ответила «{{ m.reply }}»<template v-if="m.answered_by && m.answered_by !== m.model && m.answered_by !== m.sent">
            (провайдер: {{ m.answered_by }})</template>
        </template>
      </p>
    </template>
  </section>

  <section class="panel" id="wordstat">
    <div class="panel__head">
      <h2>Вордстат</h2>
      <span class="chip" :class="wordstat.configured && !wordstat.check_error ? 'chip--ok' : 'chip--warning'">
        {{ !wordstat.configured ? 'не подключён' : wordstat.check_error ? 'ошибка проверки' : 'подключён' }}
      </span>
    </div>
    <p class="hint">
      Похожие запросы с частотой — для подбора ключевых фраз. Search API Yandex Cloud: у Яндекса
      {{ wordstat.price_rub_per_1000 }} ₽ за 1000 запросов (одна исходная фраза — один запрос), до
      {{ wordstat.hourly_quota }} запросов в час на ключ.
    </p>
    <form class="form-grid" @submit.prevent="saveWordstat">
      <div v-field class="field">
        <label>Каталог Yandex Cloud (folder_id)</label>
        <input v-model.trim="wsForm.folder_id" type="text" placeholder="b1g…" autocomplete="off" />
      </div>
      <div v-field class="field">
        <label>API-ключ</label>
        <input
          v-model="wsForm.api_key"
          type="password"
          autocomplete="new-password"
          :placeholder="wordstat.has_key ? `Сохранён ${wordstat.key_hint} — пусто, чтобы не менять` : 'Вставьте ключ'"
        />
      </div>
      <p class="field-hint">
        Сервисный аккаунт с ролью search-api.webSearch.user, API-ключ с областью yc.search-api.execute. У облака должен
        быть активный платёжный аккаунт.
      </p>
      <div v-field class="field">
        <label>Цена запроса, единиц</label>
        <input v-model="wsForm.units_per_request" type="number" min="0" step="any" inputmode="decimal" placeholder="бесплатно" />
      </div>
      <div v-field class="field">
        <label>Себестоимость запроса, ₽</label>
        <input v-model="wsForm.cost_per_request" type="number" min="0" step="any" inputmode="decimal" :placeholder="String(wordstat.price_rub_per_1000 / 1000)" />
      </div>
      <div class="form-actions">
        <button class="btn btn--accent" type="submit" :disabled="wsBusy">Сохранить</button>
        <button class="btn btn--ghost" type="button" :disabled="wsBusy || !wordstat.configured" @click="testWordstat">Проверить</button>
        <button v-if="wordstat.has_key" class="btn btn--ghost btn--danger" type="button" :disabled="wsBusy" @click="deleteWordstatKey">
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
      Проверено {{ formatDateTime(wordstat.checked_at) }} · {{ wordstat.check_error || 'работает' }}
    </p>
  </section>

  <section class="panel" id="images">
    <div class="panel__head">
      <h2>Генерация картинок</h2>
      <span class="chip" :class="images.configured && !images.check_error ? 'chip--ok' : 'chip--warning'">
        {{ !images.configured ? 'не подключена' : images.check_error ? 'ошибка проверки' : 'подключена' }}
      </span>
    </div>
    <p class="hint">Картинки для объявлений по описанию, квадратные {{ images.size }} — такие Директ берёт в объявления.</p>
    <form class="form-grid" @submit.prevent="saveImages">
      <div v-field class="field">
        <label>Провайдер</label>
        <select v-model="igForm.provider">
          <option v-for="p in images.providers" :key="p.id" :value="p.id">{{ p.label }}</option>
        </select>
      </div>
      <div v-field class="field">
        <label>Модель</label>
        <input v-model.trim="igForm.model" type="text" :placeholder="igDefaultModel" autocomplete="off" />
      </div>
      <p v-if="igForm.provider === 'openai' && bridgeImages" class="field-hint">
        Запросы идут через <a href="#bridge">мост Cloudflare</a> в OpenAI, адрес API не нужен.
      </p>
      <div v-else-if="igForm.provider === 'openai'" v-field class="field field--wide">
        <label>Адрес API</label>
        <input v-model.trim="igForm.base_url" type="url" :placeholder="images.default_base_url" autocomplete="off" />
      </div>
      <div v-else v-field class="field field--wide">
        <label>Каталог Yandex Cloud (folder_id)</label>
        <input v-model.trim="igForm.folder_id" type="text" placeholder="b1g…" autocomplete="off" required />
        <small class="field-hint">Ключ — API-ключ сервисного аккаунта с ролью ai.imageGeneration.user.</small>
      </div>
      <div v-field class="field field--wide">
        <label>API-ключ</label>
        <input
          v-model="igForm.api_key"
          type="password"
          autocomplete="new-password"
          :placeholder="images.has_key ? `Сохранён ${images.key_hint} — оставьте пустым, чтобы не менять` : 'Вставьте ключ'"
        />
      </div>
      <div v-field class="field">
        <label>Цена картинки, единиц</label>
        <input v-model="igForm.units_per_image" type="number" min="0" step="any" inputmode="decimal" placeholder="бесплатно" />
      </div>
      <div v-field class="field">
        <label>Себестоимость картинки, ₽</label>
        <input v-model="igForm.cost_per_image" type="number" min="0" step="any" inputmode="decimal" placeholder="не задана" />
      </div>
      <div class="form-actions">
        <button class="btn btn--accent" type="submit" :disabled="igBusy">Сохранить</button>
        <button class="btn btn--ghost" type="button" :disabled="igBusy || !images.configured" @click="testImages">
          {{ igBusy && igTesting ? 'Рисуем…' : 'Проверить' }}
        </button>
        <button v-if="images.has_key" class="btn btn--ghost btn--danger" type="button" :disabled="igBusy" @click="deleteImagesKey">
          Удалить ключ
        </button>
      </div>
    </form>
    <p v-if="igTest" class="sync-status" :class="igTest.ok ? 'is-ok' : 'is-error'">
      <template v-if="igTest.ok">Работает · {{ igTest.model }} нарисовал пробную картинку</template>
      <template v-else>{{ igTest.error }}</template>
    </p>
    <p v-else-if="images.checked_at" class="sync-status" :class="images.check_error ? 'is-error' : 'is-ok'">
      Проверено {{ formatDateTime(images.checked_at) }} · {{ images.check_error || 'работает' }}
    </p>
    <img v-if="igTest?.image" class="image-gen-sample" :src="igTest.image" alt="Пробная картинка" />
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, num } from '../../utils/format'

const props = defineProps({
  // Настройки моста из AdminBridge: через него ли ходят чат и картинки.
  bridge: { type: Object, default: null },
})
const emit = defineEmits(['changed'])

const PRICE_FIELDS = ['units_in', 'units_out', 'cost_in', 'cost_out']

const ai = ref({ providers: [], tasks: [], classes: [], configured: false, has_key: false, key_hint: '', default_base_url: '' })
const aiForm = reactive({
  provider: 'yandexgpt',
  base_url: '',
  folder_id: '',
  api_key: '',
  temperature: 0.3,
  classes: [],
  task_classes: {},
  default_units_limit: '',
})
const aiBusy = ref(false)
const aiTesting = ref(false)
const aiTest = ref(null)

const wordstat = ref({ configured: false, has_key: false, key_hint: '', price_rub_per_1000: 20, hourly_quota: 100 })
const wsForm = reactive({ folder_id: '', api_key: '', units_per_request: '', cost_per_request: '' })
const wsBusy = ref(false)
const wsTest = ref(null)

const images = ref({ configured: false, providers: [], default_base_url: '', has_key: false, key_hint: '', size: '1024x1024' })
const igForm = reactive({ provider: 'yandexart', model: '', base_url: '', folder_id: '', api_key: '', units_per_image: '', cost_per_image: '' })
const igBusy = ref(false)
const igTesting = ref(false)
const igTest = ref(null)

const bridgeChat = computed(() => !!(props.bridge?.active && props.bridge?.use_chat))
const bridgeImages = computed(() => !!(props.bridge?.active && props.bridge?.use_images))
const defaultModel = computed(() => ai.value.providers.find((p) => p.id === aiForm.provider)?.default_model || '')
const igDefaultModel = computed(() => images.value.providers.find((p) => p.id === igForm.provider)?.default_model || '')
const classNames = computed(() => aiForm.classes.map((c) => `«${c.name}»`).join(', ') || 'быструю, стандартную или думающую')

function dots(n) {
  return '●'.repeat(n) + '○'.repeat(Math.max(0, 3 - n))
}

/** Пустое поле — null (не задано), иначе число. */
function money(value) {
  return value === '' || value === null || value === undefined ? null : Number(value)
}

function blank(value) {
  return value ?? ''
}

function fillAi(data) {
  ai.value = data
  aiForm.provider = data.provider || 'yandexgpt'
  aiForm.base_url = data.base_url || ''
  aiForm.folder_id = data.folder_id || ''
  aiForm.temperature = data.temperature ?? 0.3
  aiForm.api_key = ''
  aiForm.classes = (data.classes || []).map((c) => ({ ...c, ...Object.fromEntries(PRICE_FIELDS.map((f) => [f, blank(c[f])])) }))
  aiForm.task_classes = { ...(data.task_classes || {}) }
  aiForm.default_units_limit = blank(data.default_units_limit)
}

async function saveAi() {
  aiBusy.value = true
  aiTest.value = null
  try {
    const apiKey = aiForm.api_key.trim()
    fillAi(
      await adminApi.saveAi({
        provider: aiForm.provider,
        base_url: aiForm.provider === 'openai' ? aiForm.base_url : '',
        folder_id: aiForm.provider === 'yandexgpt' ? aiForm.folder_id : '',
        api_key: apiKey || null,
        temperature: aiForm.temperature,
        classes: aiForm.classes.map((c) => ({
          id: c.id,
          name: c.name,
          model: c.model,
          enabled: c.enabled,
          ...Object.fromEntries(PRICE_FIELDS.map((f) => [f, money(c[f])])),
        })),
        task_classes: aiForm.task_classes,
        default_units_limit: money(aiForm.default_units_limit),
      }),
    )
    emit('changed')
    pushToast(ai.value.configured ? 'Настройки ИИ сохранены' : 'Сохранено — осталось указать ключ и модель')
    if (ai.value.configured && apiKey) await testAi()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    aiBusy.value = false
  }
}

async function deleteAiKey() {
  if (!await askConfirm('Удалить ключ ИИ? Чат, разметка и бриф перестанут работать у всех пользователей.', { danger: true })) return
  aiBusy.value = true
  try {
    const a = ai.value
    fillAi(
      await adminApi.saveAi({
        provider: a.provider,
        base_url: a.base_url,
        folder_id: a.folder_id,
        temperature: a.temperature,
        api_key: '',
      }),
    )
    aiTest.value = null
    emit('changed')
    pushToast('Ключ ИИ удалён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    aiBusy.value = false
  }
}

async function testAi() {
  aiBusy.value = true
  aiTesting.value = true
  try {
    const res = await adminApi.testAi()
    aiTest.value = {
      bridge: !!res.bridge,
      models: (res.models || []).map((m) => ({ ...m, reply: String(m.reply || '').slice(0, 120) })),
    }
  } catch (err) {
    aiTest.value = { error: errorText(err) }
  } finally {
    aiBusy.value = false
    aiTesting.value = false
  }
}

function fillWordstat(data) {
  wordstat.value = data
  wsForm.folder_id = data.folder_id || ''
  wsForm.api_key = ''
  wsForm.units_per_request = blank(data.units_per_request)
  wsForm.cost_per_request = blank(data.cost_per_request)
}

async function saveWordstat() {
  wsBusy.value = true
  wsTest.value = null
  try {
    const apiKey = wsForm.api_key.trim()
    fillWordstat(
      await adminApi.saveWordstat({
        folder_id: wsForm.folder_id,
        api_key: apiKey || null,
        units_per_request: money(wsForm.units_per_request),
        cost_per_request: money(wsForm.cost_per_request),
      }),
    )
    emit('changed')
    pushToast(wordstat.value.configured ? 'Вордстат сохранён' : 'Сохранено — осталось указать ключ и каталог')
    if (wordstat.value.configured && apiKey) await testWordstat()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    wsBusy.value = false
  }
}

async function deleteWordstatKey() {
  if (!await askConfirm('Удалить ключ Вордстата? ИИ перестанет подбирать фразы по частоте.', { danger: true })) return
  wsBusy.value = true
  try {
    fillWordstat(await adminApi.saveWordstat({ folder_id: wordstat.value.folder_id, api_key: '' }))
    wsTest.value = null
    emit('changed')
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
    const res = await adminApi.testWordstat()
    const sample = (res.sample || []).map((s) => `«${s.phrase}» ${num(s.count)}`).join(', ')
    wsTest.value = { ok: true, phrase: res.phrase, total: res.total, sample }
  } catch (err) {
    wsTest.value = { ok: false, error: errorText(err) }
  } finally {
    wsBusy.value = false
    // Итог проверки сохраняется у ключа — подтягиваем, чтобы чип не врал.
    adminApi.wordstat().then((data) => { wordstat.value = data }).catch(() => {})
  }
}

function fillImages(data) {
  images.value = data
  igForm.provider = data.provider || 'yandexart'
  igForm.model = data.model || ''
  igForm.base_url = data.base_url || ''
  igForm.folder_id = data.folder_id || ''
  igForm.api_key = ''
  igForm.units_per_image = blank(data.units_per_image)
  igForm.cost_per_image = blank(data.cost_per_image)
}

async function saveImages() {
  igBusy.value = true
  igTest.value = null
  try {
    fillImages(
      await adminApi.saveImages({
        provider: igForm.provider,
        model: igForm.model,
        base_url: igForm.provider === 'openai' ? igForm.base_url : '',
        folder_id: igForm.provider === 'yandexart' ? igForm.folder_id : '',
        api_key: igForm.api_key.trim() || null,
        units_per_image: money(igForm.units_per_image),
        cost_per_image: money(igForm.cost_per_image),
      }),
    )
    emit('changed')
    // Проверка рисует настоящую картинку за деньги — сами не запускаем.
    pushToast(images.value.configured ? 'Генерация картинок сохранена — проверьте её' : 'Сохранено — осталось указать ключ')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    igBusy.value = false
  }
}

async function deleteImagesKey() {
  if (!await askConfirm('Удалить ключ генерации картинок? Пользователи не смогут рисовать картинки для объявлений.', { danger: true })) return
  igBusy.value = true
  try {
    const g = images.value
    fillImages(await adminApi.saveImages({ provider: g.provider, model: g.model, base_url: g.base_url, folder_id: g.folder_id, api_key: '' }))
    igTest.value = null
    emit('changed')
    pushToast('Ключ генерации картинок удалён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    igBusy.value = false
  }
}

async function testImages() {
  if (!await askConfirm('Проверка рисует одну настоящую картинку — она стоит как обычная по тарифу провайдера. Проверить?')) return
  igBusy.value = true
  igTesting.value = true
  try {
    const res = await adminApi.testImages()
    igTest.value = { ok: true, model: res.model, image: res.image }
  } catch (err) {
    igTest.value = { ok: false, error: errorText(err) }
  } finally {
    igBusy.value = false
    igTesting.value = false
    adminApi.images().then((data) => { images.value = data }).catch(() => {})
  }
}

onMounted(async () => {
  try {
    const [aiData, wsData, igData] = await Promise.all([adminApi.ai(), adminApi.wordstat(), adminApi.images()])
    fillAi(aiData)
    fillWordstat(wsData)
    fillImages(igData)
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
})
</script>
