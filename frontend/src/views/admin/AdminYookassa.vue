<template>
  <section class="panel" id="yookassa">
    <div class="panel__head">
      <h2>ЮKassa</h2>
      <span class="chip" :class="statusChip.cls">{{ statusChip.text }}</span>
    </div>
    <p class="hint">
      Оплата тарифов картой и СБП. Ключи — в личном кабинете ЮKassa: «Интеграция → Ключи API». Ключ хранится в
      зашифрованном виде. Пока оплата выключена, на странице «Тариф» пользователь видит «напишите в поддержку».
    </p>
    <form class="form-grid" @submit.prevent="save">
      <div v-field class="field">
        <label>shopId</label>
        <input v-model.trim="form.shop_id" type="text" inputmode="numeric" placeholder="123456" autocomplete="off" />
      </div>
      <div v-field class="field">
        <label>Секретный ключ</label>
        <input
          v-model="form.secret_key"
          type="password"
          autocomplete="new-password"
          :placeholder="settings.has_key ? `Сохранён ${settings.key_hint} — пусто, чтобы не менять` : 'test_… или live_…'"
        />
      </div>
      <UiCheckbox v-model="form.test_mode" class="check-row">
        Тестовый режим — ключ тестового магазина, деньги не списываются; платежи не идут в выручку
      </UiCheckbox>
      <UiCheckbox v-model="form.enabled" class="check-row">
        Оплата включена — пользователи платят на странице «Тариф»
      </UiCheckbox>

      <div class="field field--wide">
        <label>HTTP-уведомления</label>
        <div class="copy-row">
          <code>{{ settings.webhook_url || 'задайте адрес сайта (PUBLIC_BASE_URL)' }}</code>
          <button v-if="settings.webhook_url" class="btn btn--ghost btn--xs" type="button" @click="copy(settings.webhook_url)">
            Копировать
          </button>
        </div>
        <small class="field-hint">
          В ЮKassa: «Интеграция → HTTP-уведомления», этот адрес и события {{ (settings.webhook_events || []).join(', ') }}.
          Уведомлению сервис не верит на слово — статус платежа перечитывает через API, повтор ничего не продлит дважды.
          Без уведомлений платежи всё равно сверяются раз в 10 минут.
        </small>
      </div>

      <div class="form-actions">
        <button class="btn btn--accent" type="submit" :disabled="busy">Сохранить</button>
        <button class="btn btn--ghost" type="button" :disabled="busy || !settings.configured" @click="test">
          {{ testing ? 'Проверяем…' : 'Проверить подключение' }}
        </button>
        <button v-if="settings.has_key" class="btn btn--ghost" type="button" :disabled="busy" @click="deleteKey">
          Удалить ключ
        </button>
      </div>
    </form>
    <p v-if="check" class="sync-status" :class="check.ok ? 'is-ok' : 'is-error'">
      <template v-if="check.ok">
        Работает · магазин {{ check.account_id }} · {{ check.test ? 'тестовый' : 'боевой' }}
        <span v-if="check.warning" class="is-warn"> · {{ check.warning }}</span>
      </template>
      <template v-else>{{ check.error }}</template>
    </p>
    <p v-else-if="settings.checked_at" class="sync-status" :class="settings.check_error ? 'is-error' : 'is-ok'">
      Проверено {{ formatDateTime(settings.checked_at) }} ·
      {{ settings.check_error || (settings.check_test ? 'работает, тестовый магазин' : 'работает, боевой магазин') }}
    </p>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { reloadAdminSummary } from '../../composables/useAdminSummary'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime } from '../../utils/format'

const settings = ref({ configured: false, has_key: false, test_mode: true, enabled: false, webhook_events: [] })
const form = reactive({ shop_id: '', secret_key: '', test_mode: true, enabled: false })
const busy = ref(false)
const testing = ref(false)
const check = ref(null)

const statusChip = computed(() => {
  const s = settings.value
  if (!s.configured) return { text: 'не подключена', cls: 'chip--warning' }
  if (!s.enabled) return { text: 'выключена', cls: 'chip--warning' }
  if (s.check_error) return { text: 'ошибка проверки', cls: 'chip--bad' }
  return s.test_mode ? { text: 'тестовый режим', cls: 'chip--info' } : { text: 'принимает оплату', cls: 'chip--ok' }
})

function fill(data) {
  settings.value = data
  form.shop_id = data.shop_id || ''
  form.secret_key = ''
  form.test_mode = data.test_mode
  form.enabled = data.enabled
}

async function save() {
  busy.value = true
  check.value = null
  try {
    const key = form.secret_key.trim()
    fill(
      await adminApi.savePaymentsSettings({
        shop_id: form.shop_id,
        secret_key: key || null,
        test_mode: form.test_mode,
        enabled: form.enabled,
      }),
    )
    reloadAdminSummary()
    pushToast(settings.value.ready ? 'ЮKassa сохранена' : 'Сохранено — оплата пока выключена')
    if (key) await test()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function deleteKey() {
  if (!(await askConfirm('Удалить ключ ЮKassa? Оплата на сайте выключится.', { danger: true }))) return
  busy.value = true
  try {
    fill(await adminApi.savePaymentsSettings({ shop_id: settings.value.shop_id, secret_key: '', enabled: false, test_mode: form.test_mode }))
    check.value = null
    reloadAdminSummary()
    pushToast('Ключ ЮKassa удалён')
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function test() {
  testing.value = true
  try {
    check.value = await adminApi.testPayments()
  } catch (err) {
    check.value = { ok: false, error: errorText(err) }
  } finally {
    testing.value = false
    adminApi.paymentsSettings().then((data) => { settings.value = data }).catch(() => {})
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    pushToast('Скопировано')
  } catch {
    pushToast('Не удалось скопировать — выделите адрес вручную', 'error')
  }
}

onMounted(async () => {
  try {
    fill(await adminApi.paymentsSettings())
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
})
</script>
