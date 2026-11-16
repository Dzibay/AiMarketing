<template>
  <section class="panel">
    <div class="au-tools">
      <input
        v-model.trim="search"
        type="search"
        class="search-input au-tools__search"
        placeholder="Почта, имя, № или код ссылки"
        aria-label="Поиск пользователя"
      />
      <UiSelect :model-value="f.plan" :options="planFilter" size="sm" aria-label="Тариф" @update:model-value="setF('plan', $event)" />
      <UiSelect :model-value="f.sub" :options="SUB_OPTIONS" size="sm" aria-label="Подписка" @update:model-value="setF('sub', $event)" />
      <UiSelect
        :model-value="f.activity"
        :options="ACTIVITY_OPTIONS"
        size="sm"
        aria-label="Активность"
        @update:model-value="setF('activity', $event)"
      />
      <UiSelect :model-value="f.source" :options="sourceFilter" size="sm" aria-label="Источник" @update:model-value="setF('source', $event)" />
      <UiSelect :model-value="f.paid" :options="PAID_OPTIONS" size="sm" aria-label="Платежи" @update:model-value="setF('paid', $event)" />
      <UiCheckbox :model-value="f.blocked" @update:model-value="setF('blocked', $event)">заблокированы</UiCheckbox>
      <UiCheckbox :model-value="f.unverified" @update:model-value="setF('unverified', $event)">почта не подтверждена</UiCheckbox>
      <span class="au-tools__sort">
        <UiSelect :model-value="f.sort" :options="SORT_OPTIONS" size="sm" aria-label="Сортировка" @update:model-value="setF('sort', $event)" />
        <button
          class="btn btn--ghost btn--sm"
          type="button"
          :aria-label="f.dir === 'asc' ? 'По возрастанию' : 'По убыванию'"
          v-tip="f.dir === 'asc' ? 'По возрастанию' : 'По убыванию'"
          @click="setF('dir', f.dir === 'asc' ? 'desc' : 'asc')"
        >
          {{ f.dir === 'asc' ? '↑' : '↓' }}
        </button>
      </span>
      <button v-if="filtered" class="btn btn--ghost btn--sm" type="button" @click="resetFilters">Сбросить</button>
    </div>

    <p v-if="data" class="au-summary">
      Найдено <b>{{ num(data.summary.found) }}</b> · платят <b>{{ num(data.summary.paying) }}</b> · заходили сегодня
      <b>{{ num(data.summary.today) }}</b>
    </p>

    <p v-if="!data" class="empty">{{ failed ? 'Не удалось загрузить список.' : 'Загружаем…' }}</p>
    <p v-else-if="!data.items.length" class="empty">{{ filtered ? 'Никого не нашлось — измените фильтры.' : 'Пользователей пока нет.' }}</p>
    <div v-else v-scroll-x class="table-scroll" :class="{ 'is-loading': loading }">
      <table class="table au-table">
        <thead>
          <tr>
            <th class="au-mark-col" aria-label="Активность"></th>
            <th>Пользователь</th>
            <th>Источник</th>
            <th>Тариф</th>
            <th>Оплачено всего</th>
            <th class="num">Платежи</th>
            <th>Лимит</th>
            <th class="num">ИИ и маржа</th>
            <th>Проекты</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in data.items" :key="u.id" :class="{ 'is-muted': u.blocked }" @click="openUser(u.id, $event)">
            <td class="au-mark-col">
              <span class="act-mark" :class="`act-${activityLevel(u.last_seen_at)}`" v-tip="activityText(u.last_seen_at)"></span>
            </td>
            <td>
              <RouterLink class="au-user" :to="userLink(u.id)">{{ u.email || `#${u.id}` }}</RouterLink>
              <small class="cell-note">
                #{{ u.id }}<template v-if="u.name"> · {{ u.name }}</template> · с {{ formatDay(u.created_at) }}
              </small>
              <span class="au-badges">
                <span v-if="u.has_yandex" class="chip">Яндекс</span>
                <span v-if="!u.email_verified" class="chip chip--warning">почта не подтверждена</span>
                <span v-if="u.blocked" class="chip chip--bad" v-tip="u.blocked_reason">заблокирован</span>
                <span v-if="u.support_unread" class="chip chip--info">обращение · {{ u.support_unread }}</span>
              </span>
            </td>
            <td>
              <template v-if="sourceView(u.source)">
                <span class="au-src">
                  <i v-if="sourceView(u.source).color" :style="{ background: sourceView(u.source).color }"></i>{{ sourceView(u.source).text }}
                </span>
                <small v-if="sourceView(u.source).note" class="cell-note">{{ sourceView(u.source).note }}</small>
              </template>
              <span v-else class="cell-note">—</span>
            </td>
            <td class="au-tariff">
              {{ tariffView(u).title }}
              <small v-if="tariffView(u).note" class="cell-note" :class="tariffView(u).tone">{{ tariffView(u).note }}</small>
              <span v-if="hasOverrides(u.subscription)" class="chip chip--info">свои условия</span>
            </td>
            <td>{{ paidTotalText(u.paid) }}</td>
            <td class="num">{{ u.paid.rub ? rubles(u.paid.rub) : '—' }}</td>
            <td class="au-limit">
              <template v-if="usedPct(u.usage) !== null">
                <span>{{ usedPct(u.usage) }}%</span>
                <div
                  class="meter"
                  :class="usedPct(u.usage) >= 100 ? 'is-over' : usedPct(u.usage) >= 80 ? 'is-near' : ''"
                  role="progressbar"
                  :aria-valuenow="Math.min(100, usedPct(u.usage))"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  v-tip="`${num(u.usage.spent, 2)} из ${num(u.usage.limit, 2)} ед. за период`"
                >
                  <span :style="{ width: `${Math.min(100, usedPct(u.usage))}%` }"></span>
                </div>
              </template>
              <span v-else class="cell-note">без лимита · {{ num(u.usage.spent, 2) }} ед.</span>
            </td>
            <td class="num">
              {{ rubAi(u.ai_cost_rub) }}
              <small class="cell-note" :class="{ 'tone-bad': u.margin_rub < 0 }">маржа {{ rubles(u.margin_rub) }}</small>
            </td>
            <td>
              <template v-if="u.projects.count">
                {{ u.projects.count }} {{ plural(u.projects.count, 'проект', 'проекта', 'проектов') }}
                <small class="cell-note">
                  <template v-if="u.projects.direct">Директ · {{ u.projects.campaigns }} {{ plural(u.projects.campaigns, 'кампания', 'кампании', 'кампаний') }}</template>
                  <template v-else>без Директа</template>
                </small>
              </template>
              <span v-else class="cell-note">нет</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <nav v-if="data && data.pages > 1" class="au-pages" aria-label="Страницы">
      <button class="btn btn--ghost btn--sm" type="button" :disabled="data.page <= 1" @click="setPage(data.page - 1)">← Назад</button>
      <span>Страница {{ data.page }} из {{ data.pages }}</span>
      <button class="btn btn--ghost btn--sm" type="button" :disabled="data.page >= data.pages" @click="setPage(data.page + 1)">Дальше →</button>
    </nav>

    <AdminUserPanel :user-id="openId" :plans="plans" :features="features" @close="closeUser" @changed="onChanged" />
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import UiSelect from '../../components/ui/UiSelect.vue'
import AdminUserPanel from './AdminUserPanel.vue'
import { adminApi } from '../../api/admin'
import { errorText } from '../../api/web'
import { reloadAdminSummary } from '../../composables/useAdminSummary'
import { pushToast } from '../../composables/useToasts'
import { formatDay, num, plural, rubAi, rubles } from '../../utils/format'
import { activityLevel, activityText, hasOverrides, paidTotalText, sourceView, tariffView, usedPct } from './adminUsers'

/**
 * Список пользователей: поиск, фильтры, сортировка и страница — в адресе (?q=…&sub=…&page=…),
 * по одному запросу к серверу на страницу. ?user=ID открывает панель пользователя поверх списка.
 */
const SUB_OPTIONS = [
  { value: '', label: 'Любая подписка' },
  { value: 'paying', label: 'Платит' },
  { value: 'trial', label: 'Пробный' },
  { value: 'ending', label: 'Истекает за 7 дней' },
  { value: 'ending_paid', label: 'Истекает за 7 дней, платный' },
  { value: 'expired', label: 'Истекла' },
  { value: 'none', label: 'Без тарифа' },
]
const ACTIVITY_OPTIONS = [
  { value: '', label: 'Любая активность' },
  { value: 'today', label: 'Заходили сегодня' },
  { value: 'week', label: 'За 7 дней' },
  { value: 'lost', label: 'Пропали 14+ дней' },
  { value: 'never', label: 'Ни разу не заходили' },
]
const PAID_OPTIONS = [
  { value: '', label: 'Платили и нет' },
  { value: 'yes', label: 'Платили' },
  { value: 'no', label: 'Не платили' },
]
const SORT_OPTIONS = [
  { value: 'created', label: 'По регистрации' },
  { value: 'seen', label: 'По последнему заходу' },
  { value: 'paid', label: 'По сумме платежей' },
  { value: 'ai', label: 'По расходу ИИ' },
  { value: 'ends', label: 'По концу подписки' },
]
const DEFAULTS = { q: '', plan: '', sub: '', activity: '', source: '', paid: '', blocked: false, unverified: false, sort: 'created', dir: 'desc', page: 1 }
const FILTER_KEYS = ['q', 'plan', 'sub', 'activity', 'source', 'paid', 'blocked', 'unverified']

const route = useRoute()
const router = useRouter()
const data = ref(null)
const loading = ref(false)
const failed = ref(false)
const plans = ref([])
const features = ref({})
const options = ref({ plans: [], groups: [], links: [] })

/** Состояние таблицы из адреса. */
const f = computed(() => {
  const q = route.query
  const one = (k) => (typeof q[k] === 'string' ? q[k] : '')
  return {
    q: one('q'),
    plan: one('plan'),
    sub: one('sub'),
    activity: one('activity'),
    source: one('source'),
    paid: one('paid'),
    blocked: one('blocked') === '1',
    unverified: one('unverified') === '1',
    sort: SORT_OPTIONS.some((o) => o.value === one('sort')) ? one('sort') : DEFAULTS.sort,
    dir: one('dir') === 'asc' ? 'asc' : 'desc',
    page: Math.max(1, parseInt(one('page'), 10) || 1),
  }
})
const filtered = computed(() => FILTER_KEYS.some((k) => f.value[k] !== DEFAULTS[k]))
const openId = computed(() => {
  const id = parseInt(route.query.user, 10)
  return id > 0 ? id : null
})

const planFilter = computed(() => [
  { value: '', label: 'Любой тариф' },
  ...options.value.plans.map((p) => ({
    value: String(p.id),
    label: `${p.name}${p.is_trial ? ' (пробный)' : ''}${p.active ? '' : ' — выключен'}`,
  })),
])
const sourceFilter = computed(() => [
  { value: '', label: 'Любой источник' },
  { value: 'ref', label: 'Любая ссылка' },
  { value: 'personal', label: 'Личные ссылки' },
  { value: 'none', label: 'Без ссылки' },
  ...options.value.groups.map((g) => ({ value: `group:${g.id}`, label: g.name, group: 'Группы' })),
  ...options.value.links.map((l) => ({ value: `link:${l.id}`, label: l.name || l.code, hint: l.code, group: 'Ссылки' })),
])

// Пишем в адрес только отличное от умолчаний — ссылку на выборку можно скопировать.
function writeQuery(next) {
  const query = {}
  for (const [k, v] of Object.entries(next)) {
    if (v === DEFAULTS[k]) continue
    query[k] = typeof v === 'boolean' ? '1' : String(v)
  }
  if (route.query.user) query.user = route.query.user
  if (route.query.tab) query.tab = route.query.tab
  router.replace({ query })
}

function setF(key, value) {
  writeQuery({ ...f.value, [key]: value ?? DEFAULTS[key], page: key === 'page' ? value : 1 })
}

function setPage(page) {
  setF('page', page)
}

function resetFilters() {
  search.value = ''
  writeQuery({ ...DEFAULTS, sort: f.value.sort, dir: f.value.dir })
}

// Поиск — с паузой, чтобы не дёргать сервер на каждую букву.
const search = ref(f.value.q)
let searchTimer = null
watch(search, (v) => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    if (v !== f.value.q) setF('q', v)
  }, 350)
})
watch(
  () => f.value.q,
  (v) => {
    if (v !== search.value) search.value = v
  },
)

let seq = 0
async function load() {
  const my = ++seq
  const { page, ...rest } = f.value
  loading.value = true
  try {
    const res = await adminApi.users({ ...rest, blocked: rest.blocked ? 1 : '', unverified: rest.unverified ? 1 : '', page })
    if (my !== seq) return
    data.value = res
    options.value = res.options || options.value
    failed.value = false
  } catch (err) {
    if (my !== seq) return
    failed.value = true
    pushToast(errorText(err), 'error')
  } finally {
    if (my === seq) loading.value = false
  }
}

// Список перечитываем только при смене фильтров и страницы, не при открытии панели.
const listKey = computed(() => JSON.stringify(f.value))
watch(listKey, load)

async function loadPlans() {
  try {
    const res = await adminApi.plans()
    plans.value = res.items || []
    features.value = res.features || {}
  } catch (err) {
    pushToast(errorText(err), 'error')
  }
}

function userLink(id) {
  return { query: { ...route.query, user: String(id), tab: undefined } }
}

// Клик по строке открывает панель; ссылки и кнопки внутри работают сами.
function openUser(id, event) {
  if (event?.target.closest('a, button, input, label')) return
  if (window.getSelection()?.toString()) return
  router.replace(userLink(id))
}

function closeUser() {
  const { user, tab, ...rest } = route.query
  router.replace({ query: rest })
}

function onChanged() {
  load()
  reloadAdminSummary()
}

onMounted(() => {
  load()
  loadPlans()
})
onBeforeUnmount(() => clearTimeout(searchTimer))
</script>

<style scoped>
.au-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.au-tools__search {
  flex: 1 1 240px;
  max-width: 320px;
}
.au-tools__sort {
  display: inline-flex;
  gap: 4px;
  margin-left: auto;
}
.au-summary {
  margin: 0 0 10px;
  color: var(--muted);
  font-size: 14px;
}
.au-summary b {
  color: var(--ink);
}
.table-scroll.is-loading {
  opacity: 0.6;
  transition: opacity 0.15s;
}
.au-table tbody tr {
  cursor: pointer;
}
.au-table tbody tr:hover td {
  background: var(--accent-soft);
}
.au-table td {
  vertical-align: top;
}
.au-mark-col {
  width: 10px;
  padding: 0 0 0 4px !important;
}
td.au-mark-col {
  position: relative;
}
td.au-mark-col .act-mark {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 2px;
  width: 6px;
}
.au-user {
  color: var(--ink);
  font-weight: 700;
  text-decoration: none;
  overflow-wrap: anywhere;
}
.au-user:hover {
  text-decoration: underline;
}
.au-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.au-badges:empty {
  display: none;
}
.au-src {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
}
.au-src i {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.au-tariff {
  min-width: 200px;
}
.au-tariff .chip {
  margin-top: 4px;
}
.au-limit {
  min-width: 110px;
}
.au-limit .meter {
  margin-top: 4px;
}
.au-pages {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 14px;
  color: var(--muted);
  font-size: 14px;
}
.is-muted {
  opacity: 0.65;
}
</style>
