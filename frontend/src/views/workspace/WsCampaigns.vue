<template>
  <div class="ws-toolbar">
    <div class="seg" role="group" aria-label="Период статистики">
      <button v-for="d in [7, 14, 30]" :key="d" type="button" :class="{ active: days === d }" @click="setDays(d)">
        {{ d }} дней
      </button>
    </div>
    <p class="sync-status">
      <template v-if="syncing">{{ syncText }}</template>
      <template v-else-if="data">
        {{ formatDate(data.date_from) }} — {{ formatDate(data.date_to) }} ·
        {{ data.conversions_source === 'goal' ? `конверсии по цели «${ws.goal_name}»` : 'конверсии Директа' }}
      </template>
    </p>
    <div class="row-actions">
      <button class="btn btn--ghost" type="button" :disabled="syncing" @click="syncCampaigns">
        {{ syncing ? 'Синхронизируем…' : 'Обновить из Директа' }}
      </button>
      <button class="btn btn--accent" type="button" @click="wizardOpen = true">Новая кампания</button>
    </div>
  </div>

  <section class="panel">
    <div class="filters">
      <div class="field field--wide">
        <label>Поиск</label>
        <input v-model.trim="filters.search" type="search" placeholder="название или ID кампании" />
      </div>
      <div v-if="accounts.length > 1" class="field">
        <label>Кабинет</label>
        <select v-model="filters.account">
          <option value="">Все кабинеты</option>
          <option v-for="a in accounts" :key="a.id" :value="String(a.id)">
            {{ a.client_login }}{{ a.sandbox ? ' (песочница)' : '' }}
          </option>
        </select>
      </div>
      <div class="field">
        <label>Состояние</label>
        <select v-model="filters.state">
          <option value="">Все, кроме архива</option>
          <option value="ON">Идут показы</option>
          <option value="OFF">Остановлены</option>
          <option value="SUSPENDED">Приостановлены</option>
          <option value="ENDED">Завершены</option>
          <option value="ARCHIVED">В архиве</option>
          <option value="*">Все</option>
        </select>
      </div>
      <label class="check-row">
        <input v-model="filters.wasteOnly" type="checkbox" />
        Расход без конверсий
      </label>
    </div>

    <div v-if="selected.size" class="bulk-bar">
      <span>Выбрано: {{ selected.size }}</span>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="bulk('resume')">Запустить</button>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="bulk('pause')">Остановить</button>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="openBudget">Бюджет и стратегия…</button>
      <button
        v-if="selected.size === 1"
        class="btn btn--ghost"
        type="button"
        :disabled="busy"
        @click="openDuplicate(rows.find((r) => selected.has(r.campaign_row_id)))"
      >
        Копировать
      </button>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="bulk('archive')">В архив</button>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="bulk('unarchive')">Из архива</button>
      <button class="btn btn--ghost btn--danger" type="button" :disabled="busy" @click="bulk('delete')">Удалить</button>
      <button class="btn btn--ghost" type="button" @click="selected = new Set()">Снять выбор</button>
    </div>

    <div v-if="bulkErrors.length" class="notice notice--warn">
      <strong>Не выполнено для {{ bulkErrors.length }}:</strong>
      <ul>
        <li v-for="e in bulkErrors" :key="e.campaign_row_id">
          {{ e.name || e.campaign_row_id }}<template v-if="e.client_login"> ({{ e.client_login }})</template>:
          {{ e.message || ERRORS[e.error] || e.error }}
        </li>
      </ul>
    </div>
    <div v-if="budgetDone.length" class="notice">
      <strong>Бюджеты и цена цели:</strong>
      <ul>
        <li v-for="i in budgetDone" :key="i.campaign_row_id">
          {{ i.name }}: {{ i.before }} → {{ i.after }}<template v-if="!i.changed"> (без изменений)</template>
          <template v-if="i.note"> · {{ i.note }}</template>
        </li>
      </ul>
      <button class="btn btn--ghost" type="button" @click="budgetDone = []">Скрыть</button>
    </div>

    <p v-if="loading && !data" class="empty">Загружаем кампании…</p>
    <div v-else-if="!data" class="empty">
      {{ loadError || 'Не удалось загрузить кампании.' }}
      <button class="btn btn--ghost" type="button" @click="load">Повторить</button>
    </div>
    <div v-else-if="!data.items.length" class="empty">
      В пространстве нет кампаний. Добавьте кабинет и кампании в
      <RouterLink :to="`/w/${ws.id}/settings`">настройках</RouterLink> или создайте новую.
    </div>
    <div v-else class="table-scroll" :class="{ 'is-loading': loading }">
      <table class="table">
        <thead>
          <tr>
            <th class="check-col">
              <input
                type="checkbox"
                :checked="rows.length > 0 && rows.every((r) => selected.has(r.campaign_row_id))"
                aria-label="Выбрать все"
                @change="toggleAll($event.target.checked)"
              />
            </th>
            <th v-for="col in COLUMNS" :key="col.key" :class="{ num: col.num }">
              <button class="th-sort" type="button" @click="sortBy(col.key)">{{ col.label }}{{ sortMark(col.key) }}</button>
            </th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in rows" :key="c.campaign_row_id" :class="{ 'is-off': c.state !== 'ON' }">
            <td class="check-col">
              <input
                type="checkbox"
                :checked="selected.has(c.campaign_row_id)"
                @change="toggle(c.campaign_row_id, $event.target.checked)"
              />
            </td>
            <td>
              <RouterLink :to="`/w/${ws.id}/campaigns/${c.campaign_row_id}`">{{ c.name }}</RouterLink>
              <small class="cell-note">
                {{ c.account_login }} · № {{ c.campaign_id }} · {{ CAMPAIGN_TYPES[c.type] || c.type || '—' }}
              </small>
            </td>
            <td>
              <span class="chip" :class="STATE_CHIPS[c.state] || ''">{{ CAMPAIGN_STATES[c.state] || c.state }}</span>
              <small v-if="c.status && c.status !== 'ACCEPTED'" class="cell-note">
                {{ CAMPAIGN_STATUSES[c.status] || c.status }}
              </small>
            </td>
            <td class="num">
              <template v-if="c.weekly_budget_rub">{{ rubles(c.weekly_budget_rub) }}/нед.</template>
              <template v-else-if="c.daily_budget_rub">{{ rubles(c.daily_budget_rub) }}/день</template>
              <template v-else>—</template>
              <small v-if="c.strategy_label" class="cell-note">
                {{ c.strategy_label }}<template v-if="c.target_cpa_rub"> · CPA {{ rubles(c.target_cpa_rub) }}</template
                ><template v-else-if="c.target_cpc_rub"> · CPC {{ rubles(c.target_cpc_rub, 2) }}</template>
              </small>
            </td>
            <td class="num">{{ rub(c.cost_kopecks) }}</td>
            <td class="num">{{ num(c.clicks) }}</td>
            <td class="num">{{ c.ctr != null ? pct(c.ctr, 2) : '—' }}</td>
            <td class="num" :class="{ 'tone-bad': !c.conversions && c.cost_kopecks > 0 }">{{ num(c.conversions) }}</td>
            <td class="num">{{ c.cpa_kopecks != null ? rub(c.cpa_kopecks) : '—' }}</td>
            <td class="row-actions">
              <button class="btn btn--ghost" type="button" :disabled="busy" @click="quickToggle(c)">
                {{ c.state === 'ON' ? 'Стоп' : 'Старт' }}
              </button>
              <button class="btn btn--ghost" type="button" @click="openDuplicate(c)">Копия</button>
            </td>
          </tr>
        </tbody>
        <tfoot v-if="rows.length > 1">
          <tr>
            <td></td>
            <td>Итого: {{ rows.length }}</td>
            <td></td>
            <td class="num" title="Сумма бюджетов запущенных кампаний; дневной × 7">
              {{ totals.budget ? `${rubles(totals.budget)}/нед.` : '—' }}
            </td>
            <td class="num">{{ rub(totals.cost) }}</td>
            <td class="num">{{ num(totals.clicks) }}</td>
            <td class="num">{{ totals.impressions ? pct((totals.clicks / totals.impressions) * 100, 2) : '—' }}</td>
            <td class="num">{{ num(totals.conversions) }}</td>
            <td class="num">{{ totals.conversions ? rub(totals.cost / totals.conversions) : '—' }}</td>
            <td></td>
          </tr>
        </tfoot>
      </table>
    </div>
  </section>

  <CampaignWizard :open="wizardOpen" :workspace="ws" @close="wizardOpen = false" @created="onCreated" />
  <DuplicateDialog
    :open="duplicate.open"
    :workspace="ws"
    :campaign="duplicate.campaign"
    @close="duplicate.open = false"
    @done="onDuplicated"
  />

  <Modal :open="budgetModal.open" title="Бюджет и стратегия" :locked="busy" @close="budgetModal.open = false">
    <form id="budget-form" class="form-grid" @submit.prevent="applyBudget">
      <div class="seg field--wide" role="group">
        <button
          v-for="m in BUDGET_MODES"
          :key="m.id"
          type="button"
          :class="{ active: budgetModal.mode === m.id }"
          @click="budgetModal.mode = m.id"
        >
          {{ m.label }}
        </button>
      </div>
      <div v-if="budgetModal.mode === 'weekly'" class="field field--wide">
        <label>Недельный бюджет, ₽</label>
        <input v-model.number="budgetModal.weekly" type="number" min="300" step="100" required />
      </div>
      <div v-else-if="budgetModal.mode === 'daily'" class="field field--wide">
        <label>Дневной бюджет, ₽</label>
        <input v-model.number="budgetModal.daily" type="number" min="300" step="50" required />
      </div>
      <div v-else-if="budgetModal.mode === 'pct'" class="field field--wide">
        <label>Изменить бюджет на, % (минус — уменьшить)</label>
        <input v-model.number="budgetModal.pct" type="number" min="-90" max="500" step="1" required />
      </div>
      <label class="check-row field--wide">
        <input v-model="budgetModal.target" type="checkbox" />
        Изменить цену цели стратегии (CPA или CPC)
      </label>
      <div v-if="budgetModal.target" class="field field--wide">
        <label>Цена цели, % (минус — снизить)</label>
        <input v-model.number="budgetModal.targetPct" type="number" min="-90" max="500" step="1" required />
      </div>
      <p class="field-hint field--wide">
        {{ selected.size }} {{ plural(selected.size, 'кампания', 'кампании', 'кампаний') }}<template v-if="selectedKinds.weekly">
          · с недельным бюджетом: {{ selectedKinds.weekly }}</template
        ><template v-if="selectedKinds.daily"> · с дневным: {{ selectedKinds.daily }}</template>.
        Автостратегии и ручные ставки ЕПК получат недельный бюджет, ручные ставки ТГО — дневной: недельная сумма
        поделится на 7, дневная умножится на 7. Цена цели меняется только у стратегий «Средняя цена конверсии»,
        «Оплата за конверсии» и «Средняя цена клика». Всё можно откатить в журнале одной кнопкой.
      </p>
      <p v-if="budgetProgress" class="field-hint field--wide">{{ budgetProgress }}</p>
    </form>
    <template #footer>
      <button class="btn btn--ghost" type="button" :disabled="busy" @click="budgetModal.open = false">Отмена</button>
      <button class="btn btn--accent" type="submit" form="budget-form" :disabled="busy || !budgetReady">Применить</button>
    </template>
  </Modal>
</template>

<script setup>
import { askConfirm } from '../../composables/useConfirm'
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import Modal from '../../components/ui/Modal.vue'
import { ERRORS, errorText, jobProgressText, wsApi } from '../../api/web'
import { pushToast } from '../../composables/useToasts'
import {
  CAMPAIGN_STATES,
  CAMPAIGN_STATUSES,
  CAMPAIGN_TYPES,
  formatDate,
  lastDays,
  num,
  pct,
  plural,
  rub,
  rubles,
} from '../../utils/format'
import CampaignWizard from './CampaignWizard.vue'
import DuplicateDialog from './DuplicateDialog.vue'
import { useWs } from './context'

const COLUMNS = [
  { key: 'name', label: 'Кампания' },
  { key: 'state', label: 'Состояние' },
  { key: 'budget_week_rub', label: 'Бюджет', num: true },
  { key: 'cost_kopecks', label: 'Расход', num: true },
  { key: 'clicks', label: 'Клики', num: true },
  { key: 'ctr', label: 'CTR', num: true },
  { key: 'conversions', label: 'Конв.', num: true },
  { key: 'cpa_kopecks', label: 'CPA', num: true },
]
const STATE_CHIPS = { ON: 'chip--ok', SUSPENDED: 'chip--warning', OFF: '', ARCHIVED: '', ENDED: '' }
const ACTION_TEXT = {
  pause: 'Остановлено',
  resume: 'Запущено',
  set_budget: 'Бюджет изменён',
  archive: 'В архиве',
  unarchive: 'Возвращено из архива',
  delete: 'Удалено',
}

const route = useRoute()
const router = useRouter()
const { ws, reload: reloadWs } = useWs()
const data = ref(null)
const loading = ref(false)
const loadError = ref('')
const days = ref(7)
const syncing = ref(false)
const syncText = ref('')
const busy = ref(false)
const selected = ref(new Set())
const bulkErrors = ref([])
const sort = reactive({ key: 'cost_kopecks', desc: true })
const filters = reactive({ search: '', account: '', state: '', wasteOnly: false })
const wizardOpen = ref(false)
const duplicate = reactive({ open: false, campaign: null })
const BUDGET_MODES = [
  { id: 'weekly', label: 'Недельный' },
  { id: 'daily', label: 'Дневной' },
  { id: 'pct', label: 'Изменить на %' },
  { id: 'none', label: 'Не менять' },
]
const budgetModal = reactive({
  open: false,
  mode: 'weekly',
  weekly: null,
  daily: null,
  pct: 10,
  target: false,
  targetPct: -10,
})
const budgetDone = ref([])
const budgetProgress = ref('')

const accounts = computed(() => data.value?.accounts || [])
const rows = computed(() => {
  const q = filters.search.toLowerCase()
  const list = (data.value?.items || []).filter((c) => {
    if (filters.account && String(c.account_id) !== filters.account) return false
    if (filters.state === '' && c.state === 'ARCHIVED') return false
    if (filters.state && filters.state !== '*' && c.state !== filters.state) return false
    if (filters.wasteOnly && (c.conversions || !c.cost_kopecks)) return false
    if (q && !c.name.toLowerCase().includes(q) && !String(c.campaign_id).includes(q)) return false
    return true
  })
  const dir = sort.desc ? -1 : 1
  return list.map(withWeek).sort((a, b) => {
    const x = a[sort.key]
    const y = b[sort.key]
    if (x == null && y == null) return 0
    if (x == null) return 1
    if (y == null) return -1
    if (typeof x === 'string') return x.localeCompare(y, 'ru') * dir
    return (x - y) * dir
  })
})
const totals = computed(() =>
  rows.value.reduce(
    (t, c) => ({
      cost: t.cost + (c.cost_kopecks || 0),
      clicks: t.clicks + (c.clicks || 0),
      impressions: t.impressions + (c.impressions || 0),
      conversions: t.conversions + (c.conversions || 0),
      budget: t.budget + (c.state === 'ON' ? c.budget_week_rub || 0 : 0),
    }),
    { cost: 0, clicks: 0, impressions: 0, conversions: 0, budget: 0 },
  ),
)

// Бюджет к одной мере — за неделю: так их можно сортировать и складывать.
function withWeek(c) {
  const week = c.weekly_budget_rub || (c.daily_budget_rub ? c.daily_budget_rub * 7 : null)
  return { ...c, budget_week_rub: week }
}

const selectedKinds = computed(() => {
  const out = { weekly: 0, daily: 0 }
  for (const c of data.value?.items || []) {
    if (!selected.value.has(c.campaign_row_id)) continue
    if (c.weekly_budget_rub) out.weekly += 1
    else if (c.daily_budget_rub) out.daily += 1
  }
  return out
})
const budgetReady = computed(() => budgetModal.mode !== 'none' || budgetModal.target)

function sortBy(key) {
  if (sort.key === key) sort.desc = !sort.desc
  else {
    sort.key = key
    sort.desc = key !== 'name' && key !== 'state'
  }
}

function sortMark(key) {
  return sort.key === key ? (sort.desc ? ' ↓' : ' ↑') : ''
}

function toggle(id, on) {
  const next = new Set(selected.value)
  if (on) next.add(id)
  else next.delete(id)
  selected.value = next
}

function toggleAll(on) {
  selected.value = on ? new Set(rows.value.map((r) => r.campaign_row_id)) : new Set()
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await wsApi.campaigns(ws.value.id, lastDays(days.value))
    const ids = new Set(data.value.items.map((c) => c.campaign_row_id))
    selected.value = new Set([...selected.value].filter((id) => ids.has(id)))
  } catch (err) {
    loadError.value = errorText(err)
  } finally {
    loading.value = false
  }
}

function setDays(d) {
  days.value = d
  load()
}

async function syncCampaigns() {
  syncing.value = true
  syncText.value = 'Запрашиваем кампании из Директа…'
  try {
    const results = await wsApi.syncCampaigns(ws.value.id, (job) => {
      syncText.value = `Синхронизация: ${jobProgressText(job)}`
    })
    const failed = results.filter((r) => r?.status === 'error').length
    pushToast(failed ? `Обновлено с ошибками: ${failed}` : 'Кампании обновлены', failed ? 'error' : 'ok')
    await Promise.all([load(), reloadWs()])
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    syncing.value = false
  }
}

async function runBulk(action, extra = {}) {
  const ids = [...selected.value]
  if (!ids.length) return
  busy.value = true
  bulkErrors.value = []
  try {
    const res = await wsApi.bulk(ws.value.id, { campaign_ids: ids, action, ...extra })
    bulkErrors.value = res.errors || []
    pushToast(
      `${ACTION_TEXT[action] || 'Готово'}: ${res.applied || 0}${bulkErrors.value.length ? `, ошибок: ${bulkErrors.value.length}` : ''}`,
      bulkErrors.value.length ? 'error' : 'ok',
    )
    if (action === 'delete') selected.value = new Set()
    await load()
    if (action === 'delete') reloadWs()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
  }
}

async function bulk(action) {
  const n = selected.value.size
  if (action === 'delete' && !await askConfirm(`Удалить в Директе кампаний: ${n}? Удалить можно только черновики и кампании без показов.`))
    return
  if (action === 'archive' && !await askConfirm(`Отправить в архив кампаний: ${n}?`)) return
  return runBulk(action)
}

function quickToggle(c) {
  selected.value = new Set([c.campaign_row_id])
  return runBulk(c.state === 'ON' ? 'pause' : 'resume')
}

function openBudget() {
  budgetDone.value = []
  budgetProgress.value = ''
  budgetModal.open = true
}

async function applyBudget() {
  const ids = [...selected.value]
  if (!ids.length || !budgetReady.value) return
  const payload = { campaign_ids: ids }
  if (budgetModal.mode === 'weekly') payload.weekly_budget_rub = Number(budgetModal.weekly)
  else if (budgetModal.mode === 'daily') payload.daily_budget_rub = Number(budgetModal.daily)
  else if (budgetModal.mode === 'pct') payload.budget_change_pct = Number(budgetModal.pct)
  if (budgetModal.target) payload.target_change_pct = Number(budgetModal.targetPct)
  busy.value = true
  bulkErrors.value = []
  budgetProgress.value = 'Читаем стратегии кампаний в Директе…'
  try {
    const res = await wsApi.bulkBudget(ws.value.id, payload, (job) => {
      budgetProgress.value = `Меняем: ${jobProgressText(job)}`
    })
    bulkErrors.value = res.errors || []
    budgetDone.value = res.items || []
    pushToast(
      `Изменено: ${res.applied || 0}${bulkErrors.value.length ? `, ошибок: ${bulkErrors.value.length}` : ''}`,
      bulkErrors.value.length ? 'error' : 'ok',
    )
    budgetModal.open = false
    await load()
  } catch (err) {
    pushToast(errorText(err), 'error')
  } finally {
    busy.value = false
    budgetProgress.value = ''
  }
}

function openDuplicate(c) {
  if (!c) return
  duplicate.campaign = { id: c.campaign_row_id, name: c.name, account_id: c.account_id }
  duplicate.open = true
}

function onCreated(result) {
  wizardOpen.value = false
  const warnings = result.warnings || []
  pushToast(
    warnings.length ? `Кампания создана. Директ предупреждает: ${warnings.join('; ')}` : 'Кампания создана черновиком',
    warnings.length ? 'warn' : 'ok',
  )
  reloadWs()
  router.push(`/w/${ws.value.id}/campaigns/${result.campaign.id}`)
}

function onDuplicated(result) {
  duplicate.open = false
  const warnings = result.warnings || []
  pushToast(
    `Копия готова: групп ${result.groups ?? 0}, фраз ${result.keywords ?? 0}, объявлений ${result.ads ?? 0}` +
      (warnings.length ? `. Предупреждения: ${warnings.slice(0, 3).join('; ')}` : ''),
    warnings.length ? 'warn' : 'ok',
  )
  if (result.target_workspace_id && result.target_workspace_id !== ws.value.id) {
    router.push(`/w/${result.target_workspace_id}/campaigns/${result.campaign.id}`)
    return
  }
  reloadWs()
  load()
}

onMounted(() => {
  load()
  // Ссылка «Создать кампанию» из других вкладок открывает мастер сразу.
  if (route.query.new) {
    wizardOpen.value = true
    router.replace({ query: { ...route.query, new: undefined } })
  }
})
</script>
