<template>
  <PageHeader>
    <template #description>
      <template v-if="syncing">{{ syncText }}</template>
      <template v-else-if="data">
        {{ formatDate(data.date_from) }} — {{ formatDate(data.date_to) }} ·
        {{ data.conversions_source === 'goal' ? `конверсии по цели «${ws.goal_name}»` : 'конверсии Директа' }}
      </template>
    </template>
    <template #actions>
      <div class="seg" role="group" aria-label="Период статистики">
        <button
          v-for="d in [7, 14, 30]"
          :key="d"
          type="button"
          :class="{ active: days === d }"
          :aria-pressed="days === d"
          @click="setDays(d)"
        >
          {{ d }} дней
        </button>
      </div>
      <button
        class="btn btn--ghost"
        type="button"
        :disabled="syncing"
        v-tip="syncedAgo ? `Данные из Директа обновлены ${syncedAgo}` : 'Обновить кампании из Директа'"
        @click="syncCampaigns"
      >
        <RefreshCw :class="{ spin: syncing }" aria-hidden="true" />
        <span class="sync-btn">
          {{ syncing ? 'Обновляем…' : 'Обновить из Директа' }}
          <small v-if="syncedAgo && !syncing">обновлено {{ syncedAgo }}</small>
        </span>
      </button>
      <button class="btn btn--accent" type="button" @click="wizardOpen = true">
        <Plus aria-hidden="true" />Новая кампания
      </button>
    </template>
  </PageHeader>

  <template v-if="!expert">
    <p v-if="loading && !data" class="empty">Загружаем кампании…</p>
    <EmptyState v-else-if="!data" :icon="CloudOff" :text="loadError || 'Не удалось загрузить кампании.'">
      <template #action><button class="btn btn--ghost" type="button" @click="load">Повторить</button></template>
    </EmptyState>
    <EmptyState v-else-if="!simpleRows.length" :icon="Megaphone">
      В проекте нет кампаний. Нажмите «Новая кампания» или добавьте кабинет в
      <RouterLink :to="`/w/${ws.id}/settings`">настройках</RouterLink>.
    </EmptyState>
    <CampaignGroups
      v-else
      :class="{ 'is-loading': loading }"
      :rows="simpleRows"
      :kpi-of="() => data.kpi"
      :busy="busy"
      :selected="selected"
      :days="days"
      @toggle="quickToggle"
      @pick="toggle"
    />
    <ExpertHint v-if="data && simpleRows.length">
      Фильтры, архив, бюджеты, копии и массовые действия
    </ExpertHint>
    <CampaignAsk
      v-if="data && simpleRows.length"
      :rows="simpleRows"
      :selected="selected"
      :ws-id="ws.id"
      @unselect="toggle($event, false)"
    />
  </template>

  <section v-else class="panel">
    <p v-if="loading && !data" class="empty">Загружаем кампании…</p>
    <EmptyState v-else-if="!data" :icon="CloudOff" :text="loadError || 'Не удалось загрузить кампании.'" plain>
      <template #action><button class="btn btn--ghost" type="button" @click="load">Повторить</button></template>
    </EmptyState>
    <EmptyState v-else-if="!data.items.length" :icon="Megaphone" plain>
      В проекте нет кампаний. Добавьте кабинет и кампании в
      <RouterLink :to="`/w/${ws.id}/settings`">настройках</RouterLink> или создайте новую.
    </EmptyState>
    <CampaignTable
      v-else
      v-model:selected="selected"
      :items="data.items"
      :link-of="(c) => `/w/${ws.id}/campaigns/${c.campaign_row_id}`"
      :busy="busy"
      :loading="loading"
      :scope="accountScope"
      @toggle="quickToggle"
      @duplicate="openDuplicate"
      @archive="archiveOne"
      @unarchive="(c) => runBulk('unarchive', {}, [c.campaign_row_id])"
    >
      <template #notices>
        <UiNotice v-if="bulkErrors.length" tone="warn" closable @close="bulkErrors = []">
          <strong>Не выполнено для {{ bulkErrors.length }}:</strong>
          <ul class="notice-list">
            <li v-for="e in bulkErrors" :key="e.campaign_row_id">
              {{ e.name || e.campaign_row_id }}<template v-if="e.client_login"> ({{ e.client_login }})</template>:
              {{ e.message || ERRORS[e.error] || e.error }}
            </li>
          </ul>
        </UiNotice>
        <UiNotice v-if="budgetDone.length" closable @close="budgetDone = []">
          <strong>Бюджеты и цена цели:</strong>
          <ul class="notice-list">
            <li v-for="i in budgetDone" :key="i.campaign_row_id">
              {{ i.name }}: {{ i.before }} → {{ i.after }}<template v-if="!i.changed"> (без изменений)</template>
              <template v-if="i.note"> · {{ i.note }}</template>
            </li>
          </ul>
        </UiNotice>
      </template>
      <template #bulk>
        <button v-if="picked.canResume" class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="bulk('resume')">
          <Play aria-hidden="true" />Запустить
        </button>
        <button v-if="picked.canPause" class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="bulk('pause')">
          <Pause aria-hidden="true" />Остановить
        </button>
        <button v-if="picked.active" class="btn btn--ghost btn--sm" type="button" :disabled="busy" @click="openBudget">
          Бюджет и стратегия…
        </button>
        <button
          v-if="selected.size === 1"
          class="btn btn--ghost btn--sm"
          type="button"
          :disabled="busy"
          @click="openDuplicate(data.items.find((r) => selected.has(r.campaign_row_id)))"
        >
          <Copy aria-hidden="true" />Копия
        </button>
        <UiMenu size="sm" label="Ещё действия с выбранными" :disabled="busy">
          <UiMenuItem v-if="picked.active" :icon="Archive" @click="bulk('archive')">В архив</UiMenuItem>
          <UiMenuItem v-if="picked.archived" :icon="ArchiveRestore" @click="bulk('unarchive')">Из архива</UiMenuItem>
          <UiMenuItem :icon="Trash2" danger @click="bulk('delete')">Удалить</UiMenuItem>
        </UiMenu>
      </template>
    </CampaignTable>
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
      <div v-if="budgetModal.mode === 'weekly'" v-field class="field field--wide">
        <label>Недельный бюджет, ₽</label>
        <input v-model.number="budgetModal.weekly" type="number" min="300" step="100" required />
      </div>
      <div v-else-if="budgetModal.mode === 'daily'" v-field class="field field--wide">
        <label>Дневной бюджет, ₽</label>
        <input v-model.number="budgetModal.daily" type="number" min="300" step="50" required />
      </div>
      <div v-else-if="budgetModal.mode === 'pct'" v-field class="field field--wide">
        <label>Изменить бюджет на, % (минус — уменьшить)</label>
        <input v-model.number="budgetModal.pct" type="number" min="-90" max="500" step="1" required />
      </div>
      <UiCheckbox v-model="budgetModal.target" class="check-row field--wide">Изменить цену цели стратегии (CPA или CPC)</UiCheckbox>
      <div v-if="budgetModal.target" v-field class="field field--wide">
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
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Archive, ArchiveRestore, CloudOff, Copy, Megaphone, Pause, Play, Plus, RefreshCw, Trash2 } from '@lucide/vue'
import Modal from '../../components/ui/Modal.vue'
import EmptyState from '../../components/ui/EmptyState.vue'
import PageHeader from '../../components/ui/PageHeader.vue'
import UiCheckbox from '../../components/ui/UiCheckbox.vue'
import UiMenu from '../../components/ui/UiMenu.vue'
import UiMenuItem from '../../components/ui/UiMenuItem.vue'
import UiNotice from '../../components/ui/UiNotice.vue'
import { ERRORS, errorText, jobProgressText, wsApi } from '../../api/web'
import { askConfirm } from '../../composables/useConfirm'
import { pushToast } from '../../composables/useToasts'
import { undoAction } from '../../composables/useUndo'
import { confirmCampaignArchive, confirmCampaignsBulk, confirmCampaignToggle } from '../../utils/campaignConfirm'
import { campaignTypeShort, formatDate, lastDays, plural, timeAgo } from '../../utils/format'
import CampaignWizard from './CampaignWizard.vue'
import DuplicateDialog from './DuplicateDialog.vue'
import { useWs } from './context'
import CampaignGroups from '../../components/CampaignGroups.vue'
import CampaignTable from '../../components/CampaignTable.vue'
import CampaignAsk from '../../components/CampaignAsk.vue'
import ExpertHint from '../../components/ExpertHint.vue'
import { useExpert } from '../../composables/useExpert'

const { expert } = useExpert()

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
const accountScope = computed(() => ({
  label: 'Кабинет',
  all: 'Все кабинеты',
  options: accounts.value.map((a) => ({ value: String(a.id), label: a.client_login })),
  of: (c) => c.account_id,
}))
// «обновлено 5 мин назад» — по самому давнему обновлению кабинетов проекта; пересчитываем раз в минуту.
const now = ref(Date.now())
const clock = setInterval(() => (now.value = Date.now()), 60_000)
onBeforeUnmount(() => clearInterval(clock))
const syncedAgo = computed(() => {
  const times = accounts.value.map((a) => a.last_sync_at).filter(Boolean).sort()
  return times.length ? timeAgo(times[0], now.value) : ''
})
// Простой режим: фильтры скрыты — всё, кроме архива.
const simpleRows = computed(() =>
  (data.value?.items || [])
    .filter((c) => c.state !== 'ARCHIVED')
    .map((c) => ({
      ...c,
      link: `/w/${ws.value.id}/campaigns/${c.campaign_row_id}`,
      where: [campaignTypeShort(c), accounts.value.length > 1 ? c.account_login : '']
        .filter(Boolean)
        .join(' · '),
    })),
)
// Какие действия имеют смысл для выбранных: запустить нечего, если всё уже идёт, и т. п.
const picked = computed(() => {
  const items = (data.value?.items || []).filter((c) => selected.value.has(c.campaign_row_id))
  const states = items.map((c) => c.state)
  const archived = states.filter((s) => s === 'ARCHIVED').length
  return {
    active: states.length - archived,
    archived,
    canPause: states.includes('ON'),
    // Черновик Директ не запускает, пока он не прошёл модерацию.
    canResume: items.some((c) => c.state !== 'ON' && c.state !== 'ARCHIVED' && c.status !== 'DRAFT'),
  }
})
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

function toggle(id, on) {
  const next = new Set(selected.value)
  if (on) next.add(id)
  else next.delete(id)
  selected.value = next
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

async function runBulk(action, extra = {}, ids = [...selected.value]) {
  if (!ids.length) return
  busy.value = true
  bulkErrors.value = []
  try {
    const res = await wsApi.bulk(ws.value.id, { campaign_ids: ids, action, ...extra })
    bulkErrors.value = res.errors || []
    pushToast(
      `${ACTION_TEXT[action] || 'Готово'}: ${res.applied || 0}${bulkErrors.value.length ? `, ошибок: ${bulkErrors.value.length}` : ''}`,
      bulkErrors.value.length ? 'error' : 'ok',
      undoAction(res, load),
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
  const what = `${n} ${plural(n, 'кампанию', 'кампании', 'кампаний')}`
  if (action === 'delete' && !(await askConfirm(`Удалить в Директе ${what}? Удалить можно только черновики и кампании без показов.`)))
    return
  if (action === 'archive' && !(await askConfirm(`Отправить в архив ${what}?`, { title: 'В архив' }))) return
  if ((action === 'pause' || action === 'resume') && !(await confirmCampaignsBulk(action, n))) return
  return runBulk(action)
}

// Одна кампания из строки (выключатель или «⋯»): выбор не трогаем.
async function quickToggle(c) {
  if (!(await confirmCampaignToggle(c))) return
  return runBulk(c.state === 'ON' ? 'pause' : 'resume', {}, [c.campaign_row_id])
}

async function archiveOne(c) {
  if (!(await confirmCampaignArchive(c))) return
  return runBulk('archive', {}, [c.campaign_row_id])
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
      undoAction(res, load),
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
    undoAction(result, afterUndoCreate),
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
    undoAction(result, afterUndoCreate),
  )
  if (result.target_workspace_id && result.target_workspace_id !== ws.value.id) {
    router.push(`/w/${result.target_workspace_id}/campaigns/${result.campaign.id}`)
    return
  }
  reloadWs()
  load()
}

// Откат создания или копии удаляет кампанию — со страницы удалённой кампании уходим к списку.
function afterUndoCreate() {
  const list = `/w/${ws.value.id}/campaigns`
  reloadWs()
  if (router.currentRoute.value.path === list) load()
  else router.push(list)
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

<style scoped>
.sync-btn {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.15;
}

.sync-btn small {
  color: var(--faint);
  font-size: 11px;
  font-weight: 600;
}

.spin {
  animation: spin 1s linear infinite;
}

.notice-list {
  margin: 4px 0 0;
  padding-left: 18px;
}

@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}
</style>
