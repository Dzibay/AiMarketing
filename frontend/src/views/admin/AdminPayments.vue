<template>
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
      сумма подтянется сама; срок подписки при возврате поправьте вручную на странице «Пользователи».
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
    <div v-else v-scroll-x class="table-scroll">
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
            <td>
              <RouterLink v-if="p.user_id" :to="{ path: '/admin-panel/users', query: { user: p.user_id } }">
                {{ p.email || `id ${p.user_id}` }}
              </RouterLink>
              <template v-else>—</template>
            </td>
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
import { onMounted, ref } from 'vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { reloadAdminSummary } from '../../composables/useAdminSummary'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, formatDay, num, plural, rubles } from '../../utils/format'

const FILTERS = [
  { id: '', label: 'Все' },
  { id: 'paid', label: 'Оплачены' },
  { id: 'no_receipt', label: 'Без чека' },
  { id: 'refunded', label: 'С возвратом' },
  { id: 'pending', label: 'Ждут оплаты' },
  { id: 'canceled', label: 'Отменены' },
]

const items = ref([])
const summary = ref(null)
const filter = ref('')
const listLoading = ref(true)
const rowBusy = ref(null)

function chipFor(p) {
  if (p.status === 'succeeded') return p.refunded_rub ? 'chip--warning' : 'chip--ok'
  if (p.status === 'canceled' || p.status === 'error') return 'chip--bad'
  return 'chip--info'
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
    reloadAdminSummary()
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

onMounted(loadList)
</script>
