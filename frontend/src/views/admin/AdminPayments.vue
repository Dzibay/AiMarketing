<template>
  <div class="adm-costs" :class="{ 'is-loading': loading && data }">
    <div class="adm-dash__bar">
      <AdminPeriod :period="data?.period" :query="query" :steps="false" @period="setPeriod" />
      <button class="btn btn--ghost btn--sm" type="button" :disabled="loading" @click="reload">Обновить</button>
    </div>

    <div v-if="summary" class="adm-kpis">
      <article class="adm-kpi">
        <header class="adm-kpi__label"><span>За этот месяц</span></header>
        <div class="adm-kpi__value"><strong>{{ rubles(summary.month_rub) }}</strong></div>
        <p class="adm-kpi__note">{{ n(summary.month_count, 'оплата', 'оплаты', 'оплат') }}</p>
      </article>
      <article class="adm-kpi">
        <header class="adm-kpi__label"><span>Всего</span></header>
        <div class="adm-kpi__value"><strong>{{ rubles(summary.total_rub) }}</strong></div>
        <p class="adm-kpi__note">{{ n(summary.total_count, 'оплата', 'оплаты', 'оплат') }}</p>
      </article>
      <article class="adm-kpi">
        <header class="adm-kpi__label"><span>Возвраты за всё время</span></header>
        <div class="adm-kpi__value"><strong>{{ rubles(summary.refunded_rub) }}</strong></div>
      </article>
      <article class="adm-kpi">
        <header class="adm-kpi__label">
          <RouterLink :to="{ query: { no_receipt: '1', p: 'all' } }">Без чека</RouterLink>
        </header>
        <div class="adm-kpi__value">
          <strong :class="summary.no_receipt ? 'tone-warn' : 'tone-ok'">{{ num(summary.no_receipt) }}</strong>
        </div>
        <p class="adm-kpi__note">пробейте в «Мой налог» и отметьте</p>
      </article>
    </div>

    <section class="panel">
      <div class="adm-costs__filters">
        <input
          v-model.trim="search"
          class="search-input adm-costs__search"
          type="search"
          placeholder="Почта, № платежа, id в ЮKassa"
          aria-label="Поиск платежа"
          @keydown.enter="setKey('q', search)"
          @search="setKey('q', search)"
        />
        <UiSelect :model-value="f.status" :options="STATUS_OPTIONS" size="sm" aria-label="Статус" @update:model-value="setKey('status', $event)" />
        <UiSelect :model-value="f.plan" :options="planOptions" size="sm" aria-label="Тариф" @update:model-value="setKey('plan', $event)" />
        <UiSelect :model-value="f.months" :options="TERM_OPTIONS" size="sm" aria-label="Срок" @update:model-value="setKey('months', $event)" />
        <UiSelect :model-value="f.test" :options="TEST_OPTIONS" size="sm" aria-label="Тестовые" @update:model-value="setKey('test', $event === 'hide' ? '' : $event)" />
        <UiCheckbox class="adm-costs__check" :checked="f.no_receipt" @change="setKey('no_receipt', $event.target.checked ? '1' : '')">
          Без чека
        </UiCheckbox>
        <button v-if="hasFilters" class="btn btn--ghost btn--xs" type="button" @click="resetFilters">Сбросить</button>
      </div>

      <div v-if="data" class="adm-costs__facts">
        <div><small>Платежей</small><strong>{{ num(t.count) }}</strong></div>
        <div><small>Пришло денег</small><strong>{{ rubles(t.sum_rub) }}</strong></div>
        <div><small>Средний чек</small><strong>{{ t.avg_rub != null ? rubles(t.avg_rub) : '—' }}</strong></div>
        <div>
          <small>Возвраты</small>
          <strong :class="{ 'tone-warn': t.refunds }">{{ t.refunds ? `${rubles(t.refunded_rub)} · ${num(t.refunds)}` : '—' }}</strong>
        </div>
      </div>
      <p class="hint">
        Итоги — по всей выборке. «Пришло денег» — оплаченные минус возвраты. Чек самозанятого ЮKassa в «Мой налог» не
        передаёт: пробейте его в приложении и поставьте отметку. Возврат делается в кабинете ЮKassa — сумма подтянется
        сама, срок подписки поправьте в карточке пользователя.
      </p>

      <p v-if="error && !data" class="empty">{{ error }}</p>
      <p v-else-if="!data" class="empty">Загружаем…</p>
      <p v-else-if="!data.items.length" class="empty">Платежей с такими условиями нет.</p>
      <div v-else v-scroll-x class="table-scroll">
        <table class="table adm-costs__table">
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
            <tr v-for="p in data.items" :key="p.id">
              <td>
                {{ formatDateTime(p.paid_at || p.created_at) }}
                <small class="cell-note"> · № {{ p.id }}</small>
                <small v-if="p.provider_id" class="cell-note" v-tip="'id в ЮKassa'"> · {{ p.provider_id.slice(0, 8) }}…</small>
              </td>
              <td>
                <RouterLink v-if="p.user_id" :to="{ path: `${ADMIN_BASE}/users`, query: { user: p.user_id } }">
                  {{ p.email || `#${p.user_id}` }}
                </RouterLink>
                <span v-else class="cell-note">—</span>
              </td>
              <td>
                {{ p.plan_name || '—' }}
                <small class="cell-note">
                  · {{ TERMS[p.months] || `${p.months} мес.` }}{{ p.kind === 'renew' ? '' : ', переход' }}<template v-if="p.last_day">
                    · по {{ formatDay(p.last_day) }}</template>
                </small>
              </td>
              <td>
                <span class="chip" :class="chipFor(p)">{{ p.status_label }}</span>
                <span v-if="p.test" class="chip adm-costs__chip">тест</span>
                <small v-if="p.cancel_reason" class="cell-note"> {{ p.cancel_reason }}</small>
                <small v-else-if="p.status === 'succeeded' && !p.applied" class="cell-note tone-warn"> тариф не подключён</small>
              </td>
              <td class="num">
                {{ rubles(p.amount_rub, 2) }}
                <small v-if="p.refunded_rub" class="cell-note tone-warn"> − {{ rubles(p.refunded_rub, 2) }}</small>
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
      <div v-if="data && data.pages > 1" class="adm-costs__pages">
        <button class="btn btn--ghost btn--xs" type="button" :disabled="data.page <= 1" @click="setKey('page', data.page - 1)">Назад</button>
        <span>{{ data.page }} из {{ data.pages }}</span>
        <button class="btn btn--ghost btn--xs" type="button" :disabled="data.page >= data.pages" @click="setKey('page', data.page + 1)">Дальше</button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import AdminPeriod from './AdminPeriod.vue'
import { ADMIN_BASE } from './adminNav'
import { queryOne, useAdminLoad } from './useAdminLoad'
import { useAdminPeriod } from './useAdminPeriod'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { reloadAdminSummary } from '../../composables/useAdminSummary'
import { pushToast } from '../../composables/useToasts'
import { formatDateTime, formatDay, num, plural, rubles } from '../../utils/format'

/**
 * Платежи (docs/admin-plan.md, этап 7): поиск по человеку и номеру, фильтры, итоги выборки, страницы по 50.
 * Фильтры — в адресе, период — общий для админки (по умолчанию всё время).
 */
const STATUS_OPTIONS = [
  { value: '', label: 'Все статусы' },
  { value: 'paid', label: 'Оплачены' },
  { value: 'refunded', label: 'С возвратом' },
  { value: 'pending', label: 'Ждут оплаты' },
  { value: 'canceled', label: 'Отменены' },
  { value: 'error', label: 'Ошибка' },
]
const TERMS = { 1: '1 месяц', 3: '3 месяца', 12: '12 месяцев' }
const TERM_OPTIONS = [{ value: '', label: 'Любой срок' }, ...Object.entries(TERMS).map(([value, label]) => ({ value, label }))]
const TEST_OPTIONS = [
  { value: 'hide', label: 'Без тестовых' },
  { value: 'all', label: 'С тестовыми' },
  { value: 'only', label: 'Только тестовые' },
]
const KEYS = ['q', 'status', 'plan', 'months', 'test', 'no_receipt']

const route = useRoute()
const router = useRouter()
const { query, setPeriod } = useAdminPeriod({ fallback: 'all' })

const f = computed(() => ({
  q: queryOne(route, 'q'),
  status: queryOne(route, 'status'),
  plan: queryOne(route, 'plan'),
  months: queryOne(route, 'months'),
  test: queryOne(route, 'test') || 'hide',
  no_receipt: queryOne(route, 'no_receipt') === '1',
}))
const page = computed(() => Number(queryOne(route, 'page')) || 1)
const params = computed(() => ({ ...query.value, ...f.value, page: page.value }))
const { data, loading, error, reload } = useAdminLoad(adminApi.paymentsList, params)

const search = ref(f.value.q)
watch(() => f.value.q, (v) => (search.value = v))

const t = computed(() => data.value?.total || {})
const summary = computed(() => data.value?.summary)
const planOptions = computed(() => [{ value: '', label: 'Все тарифы' }, ...(data.value?.options.plans || []).map((p) => ({ value: p, label: p }))])
const hasFilters = computed(() => KEYS.some((k) => route.query[k]))
const n = (v, one, few, many) => `${num(v)} ${plural(v, one, few, many)}`

function setKey(key, value) {
  const next = { ...route.query, [key]: value === '' || value == null ? undefined : String(value) }
  if (key !== 'page') delete next.page
  router.push({ query: next })
}

function resetFilters() {
  const next = { ...route.query }
  for (const k of [...KEYS, 'page']) delete next[k]
  router.push({ query: next })
}

function chipFor(p) {
  if (p.status === 'succeeded') return p.refunded_rub ? 'chip--warning' : 'chip--ok'
  if (p.status === 'canceled' || p.status === 'error') return 'chip--bad'
  return 'chip--info'
}

const rowBusy = ref(null)

function replace(row) {
  data.value = { ...data.value, items: data.value.items.map((p) => (p.id === row.id ? { ...p, ...row } : p)) }
}

async function setReceipt(p, done) {
  rowBusy.value = p.id
  try {
    replace(await adminApi.setReceipt(p.id, done))
    reload()
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
</script>
