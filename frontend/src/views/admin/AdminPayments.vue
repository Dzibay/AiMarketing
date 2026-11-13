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

  <section class="panel">
    <div class="panel__head">
      <h2>Платежи</h2>
      <button class="btn btn--ghost btn--xs" type="button" :disabled="listLoading" @click="loadList">Обновить</button>
    </div>
    <div v-if="summary" class="stats-grid stats-grid--compact">
      <div class="stat-card">
        <small>За этот месяц</small>
        <strong>{{ rubles(summary.month_rub) }}</strong>
        <span class="cell-note">{{ num(summary.month_count) }} {{ plural(summary.month_count, 'оплата', 'оплаты', 'оплат') }}</span>
      </div>
      <div class="stat-card">
        <small>Всего</small>
        <strong>{{ rubles(summary.total_rub) }}</strong>
        <span class="cell-note">{{ num(summary.total_count) }} {{ plural(summary.total_count, 'оплата', 'оплаты', 'оплат') }}</span>
      </div>
      <div class="stat-card">
        <small>Возвраты</small>
        <strong>{{ rubles(summary.refunded_rub) }}</strong>
      </div>
      <div class="stat-card">
        <small>Без чека</small>
        <strong :class="summary.no_receipt ? 'tone-warn' : 'tone-ok'">{{ num(summary.no_receipt) }}</strong>
      </div>
    </div>
    <p class="hint">
      Суммы — без тестовых платежей и за вычетом возвратов. Чек самозанятого ЮKassa с 29.12.2025 в «Мой налог» не
      передаёт: пробейте его в приложении «Мой налог» и поставьте отметку. Возврат делается в личном кабинете ЮKassa —
      сумма подтянется сама; срок подписки при возврате поправьте вручную на вкладке «Пользователи».
    </p>
    <nav class="seg" aria-label="Фильтр платежей">
      <button
        v-for="f in FILTERS"
        :key="f.id"
        type="button"
        :class="{ active: filter === f.id }"
        @click="setFilter(f.id)"
      >
        {{ f.label }}
      </button>
    </nav>
    <p v-if="listLoading && !items.length" class="empty">Загружаем…</p>
    <p v-else-if="!items.length" class="empty">Платежей нет.</p>
    <div v-else class="table-scroll">
      <table class="table admin-payments">
        <thead>
          <tr>
            <th>Когда</th>
            <th>Пользователь</th>
            <th>Тариф</th>
            <th>Статус</th>
            <th class="num">Сумма</th>
            <th>Чек</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in items" :key="p.id">
            <td>
              {{ formatDateTime(p.created_at) }}
              <small class="cell-note">№ {{ p.id }}<template v-if="p.provider_id"> · {{ p.provider_id }}</template></small>
            </td>
            <td>{{ p.email || `id ${p.user_id}` }}</td>
            <td>
              {{ p.plan_name || '—' }}
              <small class="cell-note">
                {{ p.kind === 'renew' ? 'продление' : 'переход' }}, {{ p.days }} дн.<template v-if="p.last_day">
                  · по {{ formatDay(p.last_day) }}</template>
              </small>
            </td>
            <td>
              <span class="chip" :class="chipFor(p)">{{ p.status_label }}</span>
              <span v-if="p.test" class="chip">тест</span>
              <small v-if="p.cancel_reason" class="cell-note">{{ p.cancel_reason }}</small>
              <small v-else-if="p.status === 'succeeded' && !p.applied" class="cell-note tone-warn">тариф не подключён</small>
            </td>
            <td class="num">
              {{ rubles(p.amount_rub, 2) }}
              <small v-if="p.refunded_rub" class="cell-note">возврат {{ rubles(p.refunded_rub, 2) }}</small>
            </td>
            <td>
              <UiCheckbox
                v-if="p.status === 'succeeded' && !p.test"
                class="check-row--inline"
                :checked="p.receipt_done"
                :disabled="rowBusy === p.id"
                @change="setReceipt(p, $event.target.checked)"
              >
                пробит
              </UiCheckbox>
              <span v-else class="cell-note">—</span>
            </td>
            <td>
              <button
                v-if="p.provider_id"
                class="btn btn--ghost btn--xs"
                type="button"
                :disabled="rowBusy === p.id"
                v-tip="'Перечитать статус и возврат из ЮKassa'"
                @click="sync(p)"
              >
                Сверить
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, formatDay, num, plural, rubles } from '../../utils/format'

const emit = defineEmits(['changed'])

const FILTERS = [
  { id: '', label: 'Все' },
  { id: 'paid', label: 'Оплачены' },
  { id: 'no_receipt', label: 'Без чека' },
  { id: 'refunded', label: 'С возвратом' },
  { id: 'pending', label: 'Ждут оплаты' },
  { id: 'canceled', label: 'Отменены' },
]

const settings = ref({ configured: false, has_key: false, test_mode: true, enabled: false, webhook_events: [] })
const form = reactive({ shop_id: '', secret_key: '', test_mode: true, enabled: false })
const busy = ref(false)
const testing = ref(false)
const check = ref(null)

const items = ref([])
const summary = ref(null)
const filter = ref('')
const listLoading = ref(true)
const rowBusy = ref(null)

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

function chipFor(p) {
  if (p.status === 'succeeded') return p.refunded_rub ? 'chip--warning' : 'chip--ok'
  if (p.status === 'canceled' || p.status === 'error') return 'chip--bad'
  return 'chip--info'
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
    emit('changed')
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
    emit('changed')
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

async function loadList() {
  listLoading.value = true
  try {
    const data = await adminApi.payments({ status: filter.value })
    items.value = data.items || []
    summary.value = data.summary
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    listLoading.value = false
  }
}

function setFilter(id) {
  filter.value = id
  loadList()
}

function replace(row) {
  items.value = items.value.map((p) => (p.id === row.id ? row : p))
}

async function setReceipt(p, done) {
  rowBusy.value = p.id
  try {
    replace(await adminApi.setReceipt(p.id, done))
    loadList()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    rowBusy.value = null
  }
}

async function sync(p) {
  rowBusy.value = p.id
  try {
    const row = await adminApi.syncPayment(p.id)
    replace(row)
    pushToast(`Платёж № ${p.id}: ${row.status_label}${row.refunded_rub ? `, возврат ${rubles(row.refunded_rub, 2)}` : ''}`)
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    rowBusy.value = null
  }
}

onMounted(async () => {
  try {
    fill(await adminApi.paymentsSettings())
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
  loadList()
})
</script>
