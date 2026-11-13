<template>
  <section class="panel" id="bridge">
    <div class="panel__head">
      <h2>Мост Cloudflare</h2>
      <span class="chip" :class="bridge.active ? '' : 'chip--warning'">{{ bridgeChip }}</span>
    </div>
    <p class="hint">
      OpenAI, Anthropic (Claude) и Google (Gemini) не принимают запросы с российских серверов. Мост — посредник в
      бесплатном аккаунте Cloudflare сервиса: он получает запрос и передаёт его провайдеру из США. Ключ провайдера
      указывается в блоках «ИИ и модели» и «Генерация картинок». Работает только с провайдером
      «OpenAI-совместимый», YandexGPT и YandexART ходят напрямую.
    </p>
    <button v-if="!bridgeOpen" class="btn btn--ghost" type="button" @click="brExpanded = true">Подключить</button>
    <template v-if="bridgeOpen">
      <div class="filters">
        <label class="check-row">
          <input v-model="brForm.kind" type="radio" value="worker" />
          Свой Worker в США (рекомендуется)
        </label>
        <label class="check-row">
          <input v-model="brForm.kind" type="radio" value="gateway" />
          AI Gateway
        </label>
      </div>

      <template v-if="brForm.kind === 'worker'">
        <h3 class="subhead">Развернуть Worker автоматически</h3>
        <p class="hint">
          Сервис сам загрузит Worker в ваш аккаунт Cloudflare, закрепит его в регионе США ({{ bridge.worker_region }})
          и включит мост. API-токен нужен только для этого и не сохраняется.
        </p>
        <form class="form-grid" @submit.prevent="deployBridge">
          <div v-field class="field">
            <label>Account ID</label>
            <input v-model.trim="brForm.account_id" type="text" placeholder="32 символа: 0–9, a–f" autocomplete="off" required />
          </div>
          <div v-field class="field">
            <label>API-токен Cloudflare</label>
            <input v-model="brForm.api_token" type="password" autocomplete="new-password" placeholder="Edit Cloudflare Workers" required />
          </div>
          <div class="form-actions">
            <button class="btn btn--accent" type="submit" :disabled="brBusy">
              {{ brBusy && brDeploying ? 'Разворачиваем…' : bridge.worker_url ? 'Развернуть заново' : 'Развернуть Worker' }}
            </button>
          </div>
        </form>
      </template>

      <form class="form-grid" @submit.prevent="saveBridge()">
        <UiCheckbox v-model="brForm.enabled" class="check-row field--wide">
          Мост включён
        </UiCheckbox>
        <template v-if="brForm.kind === 'worker'">
          <div v-field class="field field--wide">
            <label>Адрес Worker'а</label>
            <input v-model.trim="brForm.worker_url" type="url" placeholder="https://aimarketing-bridge.<ваш-поддомен>.workers.dev" autocomplete="off" />
            <small class="field-hint">Заполняется сам после развёртывания. Вручную — если создавали Worker сами.</small>
          </div>
          <div v-field class="field field--wide">
            <label>Секрет Worker'а (BRIDGE_TOKEN)</label>
            <input
              v-model="brForm.worker_token"
              type="password"
              autocomplete="new-password"
              :placeholder="bridge.has_worker_token ? `Сохранён ${bridge.worker_token_hint} — оставьте пустым, чтобы не менять` : 'Тот же, что в секрете BRIDGE_TOKEN'"
            />
          </div>
        </template>
        <template v-else>
          <div v-field class="field">
            <label>Account ID</label>
            <input v-model.trim="brForm.account_id" type="text" placeholder="32 символа: 0–9, a–f" autocomplete="off" />
          </div>
          <div v-field class="field">
            <label>Имя шлюза (Gateway ID)</label>
            <input v-model.trim="brForm.gateway_id" type="text" placeholder="aimarketing" autocomplete="off" />
          </div>
          <div v-field class="field field--wide">
            <label>Токен шлюза</label>
            <input
              v-model="brForm.token"
              type="password"
              autocomplete="new-password"
              :placeholder="bridge.has_token ? `Сохранён ${bridge.token_hint} — оставьте пустым, чтобы не менять` : 'Если включён Authenticated Gateway'"
            />
            <small class="field-hint">
              Anthropic часто отвечает «Request not allowed»: AI Gateway выходит из дата-центра рядом с вами, а
              отсюда Claude недоступен. Для Claude выбирайте «Свой Worker».
            </small>
          </div>
        </template>
        <div class="field field--wide">
          <label>Что пускать через мост</label>
          <UiCheckbox v-model="brForm.use_chat" class="check-row">ИИ (чат и тексты)</UiCheckbox>
          <UiCheckbox v-model="brForm.use_images" class="check-row">Генерация картинок (OpenAI)</UiCheckbox>
        </div>
        <div class="form-actions">
          <button class="btn btn--accent" type="submit" :disabled="brBusy">Сохранить</button>
          <button
            v-if="brForm.kind === 'worker'"
            class="btn btn--ghost"
            type="button"
            :disabled="brBusy || !bridge.worker_url || !bridge.has_worker_token"
            @click="testBridge"
          >
            Проверить мост
          </button>
        </div>
      </form>
      <p v-if="brTest" class="sync-status" :class="brTest.ok && !brTest.blocked ? 'is-ok' : 'is-error'">
        <template v-if="brTest.ok">
          Worker работает и выходит в интернет из {{ brTest.country || '?' }} (дата-центр {{ brTest.colo || '?' }})<template
            v-if="brTest.blocked"
          >: из этой страны провайдеры не пускают — разверните Worker заново</template>. Теперь нажмите «Проверить»
          в блоке «ИИ и модели».
        </template>
        <template v-else>{{ brTest.error }}</template>
      </p>
    </template>

    <details v-if="brForm.kind === 'worker'" class="bridge-guide" :open="!bridge.worker_url">
      <summary>Как подключить мост через Worker</summary>
      <ol>
        <li>
          Зарегистрируйтесь на <a href="https://dash.cloudflare.com/sign-up" target="_blank" rel="noopener">dash.cloudflare.com</a>.
          Бесплатного плана хватает, карта не нужна. Если сайт не открывается из РФ, зайдите через VPN: он нужен
          только для настройки.
        </li>
        <li>
          Откройте <b>Compute → Workers &amp; Pages</b> один раз: так у аккаунта появится адрес вида
          <i>имя.workers.dev</i>. Если Cloudflare предложит выбрать поддомен, выберите любой.
        </li>
        <li>
          <b>Account ID.</b> Он есть в адресной строке дашборда: dash.cloudflare.com/<b>&lt;account id&gt;</b>/… —
          32 символа.
        </li>
        <li>
          <b>API-токен.</b> Справа вверху иконка профиля → <b>Profile → API Tokens → Create Token</b> → шаблон
          <b>Edit Cloudflare Workers</b> → Use template. В Account Resources выберите свой аккаунт, в Zone Resources
          оставьте All zones → Continue to summary → Create Token. Скопируйте токен.
        </li>
        <li>Вставьте Account ID и токен выше и нажмите <b>«Развернуть Worker»</b>. Мост включится сам.</li>
        <li>
          Нажмите <b>«Проверить мост»</b>: должна быть страна US. Затем в «ИИ и моделях» выберите
          «OpenAI-совместимый», вставьте ключ провайдера (для Claude — sk-ant-… из console.anthropic.com), модель,
          например claude-sonnet-5-5, и нажмите «Проверить».
        </li>
        <li>API-токен Cloudflare после развёртывания можно удалить в Profile → API Tokens: мосту он больше не нужен.</li>
      </ol>
      <details class="bridge-guide__manual">
        <summary>Создать Worker вручную, без API-токена</summary>
        <ol>
          <li>Workers &amp; Pages → Create → Start with Hello World → Deploy.</li>
          <li>Edit code → замените весь код на скрипт ниже → Deploy.</li>
          <li>
            Settings → Variables and Secrets → Add → тип Secret, имя <b>BRIDGE_TOKEN</b>, значение — любая длинная
            случайная строка. Ту же строку вставьте в поле «Секрет Worker'а».
          </li>
          <li>
            Settings → Placement (Runtime): если доступен выбор региона, выберите восток США. Без этого Worker может
            выходить из дата-центра рядом с вами, и Claude ответит «Request not allowed» — «Проверить мост» это
            покажет.
          </li>
          <li>Скопируйте адрес Worker'а (…workers.dev) в поле «Адрес Worker'а», включите мост и сохраните.</li>
        </ol>
        <div class="row-actions">
          <button class="btn btn--ghost" type="button" @click="copyWorkerScript">Скопировать скрипт Worker'а</button>
        </div>
        <pre class="bridge-guide__code">{{ bridge.worker_script }}</pre>
      </details>
      <p class="field-hint">
        Если проверка зависает или ответ обрывается, значит, хостинг сервера замедляет соединения с Cloudflare
        (в РФ так бывает с 2025 года). Помогает только сервер у другого хостинга.
      </p>
    </details>

    <details v-else class="bridge-guide" :open="!bridge.account_id">
      <summary>Как создать мост в AI Gateway</summary>
      <ol>
        <li>Зарегистрируйтесь на dash.cloudflare.com (бесплатный план, при необходимости через VPN).</li>
        <li><b>Account ID</b> — 32 символа из адресной строки дашборда: dash.cloudflare.com/&lt;account id&gt;/….</li>
        <li>
          <b>AI → AI Gateway → Create Gateway</b>, имя, например aimarketing. Кэш и повторы запросов не включайте. Это
          имя впишите в «Имя шлюза».
        </li>
        <li>
          <b>Токен:</b> в разделе AI Gateway → Create authentication token, право <b>AI Gateway → Run</b> (строки
          Workers AI не нужны). Скопируйте токен в «Токен шлюза».
        </li>
        <li>Включите мост, сохраните и нажмите «Проверить» в блоке «ИИ и модели». Запросы видны в шлюзе → Logs.</li>
      </ol>
      <p class="field-hint">Для Claude этот вариант часто не работает (Anthropic: «Request not allowed») — берите Worker.</p>
    </details>
  </section>
</template>

<script setup>
// Мост Cloudflare — только у администратора: им ходят общие ключи ИИ и картинок к зарубежным провайдерам.
import { computed, onMounted, reactive, ref } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { pushToast } from '../../composables/useToasts'

const emit = defineEmits(['update'])

const bridge = ref({
  enabled: false,
  active: false,
  kind: 'worker',
  account_id: '',
  gateway_id: '',
  has_token: false,
  token_hint: '',
  worker_url: '',
  has_worker_token: false,
  worker_token_hint: '',
  worker_region: '',
  worker_script: '',
  use_chat: true,
  use_images: true,
})
const brForm = reactive({
  enabled: false,
  kind: 'worker',
  account_id: '',
  gateway_id: '',
  token: '',
  worker_url: '',
  worker_token: '',
  api_token: '',
  use_chat: true,
  use_images: true,
})
const brBusy = ref(false)
const brDeploying = ref(false)
const brTest = ref(null)

// Мост нужен только серверу в РФ с зарубежной моделью — свёрнут, пока не заполнен.
const brExpanded = ref(false)
const bridgeOpen = computed(
  () => brExpanded.value || bridge.value.enabled || !!bridge.value.account_id || !!bridge.value.worker_url,
)
const bridgeChip = computed(() => {
  const b = bridge.value
  if (b.active) return b.kind === 'worker' ? 'включён · Worker' : 'включён · AI Gateway'
  return b.account_id || b.worker_url ? 'выключен' : 'не подключён'
})

function fillBridge(data) {
  bridge.value = data
  brForm.enabled = !!data.enabled
  brForm.kind = data.kind || 'worker'
  brForm.account_id = data.account_id || ''
  brForm.gateway_id = data.gateway_id || ''
  brForm.worker_url = data.worker_url || ''
  brForm.use_chat = data.use_chat !== false
  brForm.use_images = data.use_images !== false
  brForm.token = ''
  brForm.worker_token = ''
  brForm.api_token = ''
  emit('update', data)
}

async function saveBridge() {
  brBusy.value = true
  try {
    fillBridge(
      await adminApi.saveBridge({
        enabled: brForm.enabled,
        kind: brForm.kind,
        account_id: brForm.account_id,
        gateway_id: brForm.gateway_id,
        token: brForm.token.trim() || null,
        worker_url: brForm.worker_url,
        worker_token: brForm.worker_token.trim() || null,
        use_chat: brForm.use_chat,
        use_images: brForm.use_images,
      }),
    )
    pushToast(bridge.value.active ? 'Мост включён — проверьте ИИ кнопкой «Проверить подключение»' : 'Мост сохранён, запросы идут напрямую')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    brBusy.value = false
  }
}

async function deployBridge() {
  brBusy.value = true
  brDeploying.value = true
  brTest.value = null
  try {
    fillBridge(await adminApi.deployBridge({ account_id: brForm.account_id, api_token: brForm.api_token.trim() }))
    pushToast('Worker развёрнут, мост включён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    brBusy.value = false
    brDeploying.value = false
  }
  // Адрес workers.dev начинает отвечать не мгновенно — проверяем после короткой паузы.
  if (bridge.value.active && bridge.value.kind === 'worker') {
    await new Promise((resolve) => setTimeout(resolve, 3000))
    await testBridge()
  }
}

async function testBridge() {
  brBusy.value = true
  try {
    brTest.value = { ok: true, ...(await adminApi.testBridge()) }
  } catch (err) {
    brTest.value = { ok: false, error: errorText(err) }
  } finally {
    brBusy.value = false
  }
}

async function copyWorkerScript() {
  try {
    await navigator.clipboard.writeText(bridge.value.worker_script)
    pushToast('Скрипт скопирован')
  } catch {
    pushToast('Не удалось скопировать — выделите текст скрипта вручную', 'error')
  }
}

onMounted(async () => {
  try {
    fillBridge(await adminApi.bridge())
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
})
</script>
